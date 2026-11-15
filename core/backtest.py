"""回测/校准：验证「多周期共振」到底有没有预测力。

做法（严格无未来函数）：
  1. 每个周期取较长历史，预计算指标；
  2. 逐根 K 线，只用「该根为止」的数据算打分（形态检测也切片到该根）；
  3. 把 6 个周期的打分按时间对齐（高周期取「最近一根已收/正在走的」），加权成共振分；
  4. 统计未来 N 根的实际收益，按分数分桶算胜率与期望。

输出可以直接回答三个问题：
  · 这套打分对涨跌有没有区分度？（IC）
  · 哪个分数段真的能赚钱？（分桶）
  · 多周期共振比单周期强吗？（IC 对比）
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import config as C, feed, indicators as I, patterns as P
from .analysis import WARMUP, eval_at

PATTERN_WINDOW = 130      # 形态检测的滑动窗口
MIN_BARS = 400            # 少于此不做回测
CACHE_TTL = 900           # 结果缓存 15 分钟

_cache: dict[tuple, tuple[float, dict]] = {}


def _score_series(symbol: str, tf: str, want: int, t_min: int) -> dict:
    """返回该周期每个下标的打分（只算 t_min 之后的部分，省时间）。"""
    bars = feed.klines(symbol, tf, want=want)
    n = len(bars["close"])
    ind = I.compute_all(bars)
    ts = bars["ts"]
    scores = np.full(n, np.nan)

    start = max(WARMUP, int(np.searchsorted(ts, t_min, side="left")) - 1, 0)
    for i in range(start, n):
        s = max(0, i - PATTERN_WINDOW + 1)
        win = {k: bars[k][s:i + 1] for k in ("open", "high", "low", "close", "volume")}
        pat = P.detect(win)
        _, sc = eval_at(bars, ind, pat, i)
        scores[i] = sc
    return {"ts": ts, "close": bars["close"], "scores": scores, "n": n}


def _align(tf_data: dict, base_ts: np.ndarray) -> np.ndarray:
    """把某周期的打分序列按时间对齐到底周期（取 ts <= t 的最后一根，无未来函数）。"""
    ts = tf_data["ts"]
    sc = tf_data["scores"]
    idx = np.searchsorted(ts, base_ts, side="right") - 1
    out = np.full(len(base_ts), np.nan)
    valid = idx >= 0
    out[valid] = sc[idx[valid]]
    return out


def _bucket_stats(scores, fwd, edges) -> list[dict]:
    out = []
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        m = (scores > lo) & (scores <= hi) & np.isfinite(fwd) & np.isfinite(scores)
        n = int(m.sum())
        if n == 0:
            out.append({"label": _blabel(lo, hi), "n": 0, "avg_ret": None,
                        "hit_rate": None, "edge": None})
            continue
        r = fwd[m]
        s = scores[m]
        # 方向命中：分数方向与收益同号
        dirs = np.sign(s)
        hit = float((np.sign(r) == dirs).mean()) if np.any(dirs != 0) else None
        avg = float(r.mean())
        # 期望收益：按分数方向持仓
        edge = float((r * dirs).mean())
        out.append({
            "label": _blabel(lo, hi),
            "n": n,
            "avg_ret": round(avg * 100, 3),
            "hit_rate": round(hit * 100, 1) if hit is not None else None,
            "edge": round(edge * 100, 3),
        })
    return out


def _blabel(lo, hi):
    def f(v):
        return "−∞" if v <= -1e9 else ("+∞" if v >= 1e9 else f"{v:+.0f}")
    return f"{f(lo)} ~ {f(hi)}"


def _ic(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 30:
        return None
    aa, bb = a[m], b[m]
    if aa.std() == 0 or bb.std() == 0:
        return None
    return float(np.corrcoef(aa, bb)[0, 1])


EDGES = [-1e9, -50, -30, -10, 10, 30, 50, 1e9]


def run(symbol: str, base_tf: str = "1h", horizon: int = 24, bars: int = 3000) -> dict:
    sym = feed.base_symbol(feed.norm_symbol(symbol))
    if base_tf not in C.TIMEFRAMES:
        raise ValueError(f"不支持的周期: {base_tf}")

    key = (sym, base_tf, horizon, bars)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    base = feed.klines(sym, base_tf, want=bars)
    base_ts = base["ts"]
    base_close = base["close"]
    n = len(base_close)
    if n < MIN_BARS:
        raise ValueError(f"历史太短（{n} < {MIN_BARS}），无法回测")

    t_min = int(base_ts[0])
    base_sec = C.TIMEFRAMES[base_tf]
    cover_sec = n * base_sec

    # 长时间窗口下 15m 历史覆盖不到，剔除，避免样本口径不一致
    tfs_used = [tf for tf in C.MTF_TIMEFRAMES if not (bars > 2000 and tf == "15m")]

    # 各周期需要多少根：按时间跨度换算，不是一律拉满
    wants = {}
    for tf in tfs_used:
        need = int(cover_sec / C.TIMEFRAMES[tf]) + WARMUP + 5
        wants[tf] = max(300, min(need, feed.PAGE_CAP))

    data: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(tfs_used)) as pool:
        futs = {tf: pool.submit(_score_series, sym, tf, wants[tf], t_min) for tf in tfs_used}
        for tf, f in futs.items():
            try:
                data[tf] = f.result()
            except Exception as e:  # noqa: BLE001
                data[tf] = {"error": str(e)}

    # 对齐 + 加权
    aligned = {tf: _align(d, base_ts) for tf, d in data.items() if "error" not in d}
    if base_tf not in aligned:
        raise RuntimeError(f"底周期 {base_tf} 打分失败")

    tfs = [tf for tf in tfs_used if tf in aligned]
    num = np.zeros(n)
    den = np.zeros(n)
    for tf in tfs:
        w = C.TF_WEIGHT[tf]
        a = aligned[tf]
        ok = np.isfinite(a)
        num[ok] += a[ok] * w
        den[ok] += w
    mtf_score = np.where(den > 0, num / np.maximum(den, 1e-9), np.nan)

    # 未来收益
    fwd = np.full(n, np.nan)
    if horizon < n:
        fwd[:n - horizon] = base_close[horizon:] / base_close[:n - horizon] - 1.0

    # 只保留指标已稳定 + 有未来收益的样本
    valid = np.isfinite(mtf_score) & np.isfinite(fwd)
    valid[:WARMUP] = False

    # 只测「所有周期都有数据」的区间（15m 历史最短，决定了下限），
    # 否则早期的共振分会少掉短期周期，口径不统一
    starts = []
    for tf, d in data.items():
        if "error" in d:
            continue
        k = min(WARMUP, d["n"] - 1)
        starts.append(int(d["ts"][k]))
    common_start = max(starts) if starts else int(base_ts[0])
    valid &= base_ts >= common_start

    if valid.sum() < 50:
        raise ValueError(f"有效样本只有 {int(valid.sum())} 个，太少")

    ms, fs = mtf_score, fwd

    # 策略对照：每 horizon 根按分数方向换仓（不重叠），与买入持有比
    step = max(1, horizon)
    idxs = np.arange(WARMUP, n - horizon, step)
    idxs = idxs[valid[idxs]]
    strat = None
    if len(idxs) >= 5:
        pos = np.sign(ms[idxs])
        r = pos * fs[idxs]
        strat = {
            "step": step,
            "trades": int(len(idxs)),
            "ret_pct": round(float(np.prod(1 + r) - 1) * 100, 2),
            "buyhold_pct": round(float(base_close[idxs[-1] + horizon] / base_close[idxs[0]] - 1) * 100, 2),
            "win_pct": round(float((r > 0).mean()) * 100, 1),
        }

    # 分桶
    buckets = _bucket_stats(ms[valid], fs[valid], EDGES)
    single = _bucket_stats(aligned[base_tf][valid], fs[valid], EDGES)

    # 各周期单独 IC，看谁最有用
    per_tf = {}
    for tf in tfs:
        ic = _ic(aligned[tf][valid], fs[valid])
        per_tf[tf] = round(ic, 4) if ic is not None else None

    ic_mtf = _ic(ms[valid], fs[valid])
    ic_base = _ic(aligned[base_tf][valid], fs[valid])

    # 极端分桶的期望收益（最有用的两个）
    strong_up = (ms > 50) & valid
    strong_dn = (ms < -50) & valid
    e_up = float((fs[strong_up] * 1).mean()) if strong_up.sum() else None
    e_dn = float((fs[strong_dn] * -1).mean()) if strong_dn.sum() else None

    best_tf = max(((tf, ic) for tf, ic in per_tf.items() if ic is not None),
                  key=lambda x: abs(x[1]), default=(None, None))

    out = {
        "symbol": sym,
        "base_tf": base_tf,
        "horizon": horizon,
        "bars": n,
        "tfs_used": tfs,
        "samples": int(valid.sum()),
        "span": {
            "from": int(base_ts[0]),
            "to": int(base_ts[-1]),
        },
        "ic": {"mtf": round(ic_mtf, 4) if ic_mtf is not None else None,
               "single": round(ic_base, 4) if ic_base is not None else None,
               "per_tf": per_tf},
        "buckets": buckets,
        "single_buckets": single,
        "strong": {
            "up_n": int(strong_up.sum()),
            "up_ret": round(e_up * 100, 3) if e_up is not None else None,
            "dn_n": int(strong_dn.sum()),
            "dn_ret": round(e_dn * 100, 3) if e_dn is not None else None,
        },
        "best_tf": {"tf": best_tf[0], "ic": round(best_tf[1], 4) if best_tf[1] is not None else None},
        "strategy": strat,
        "verdict": _verdict_text(ic_mtf, ic_base, buckets, strong_up.sum(), strong_dn.sum(),
                                 e_up, e_dn, strat),
    }
    _cache[key] = (time.time(), out)
    return out


def _verdict_text(ic_mtf, ic_base, buckets, n_up, n_dn, e_up, e_dn, strat=None) -> str:
    if ic_mtf is None:
        return "样本不足以判断。"
    parts = []
    a = abs(ic_mtf)
    sign = "正相关（顺势有效）" if ic_mtf > 0 else "负相关（反向更有效）"
    if a < 0.03:
        parts.append(f"共振分与未来收益几乎不相关（IC {ic_mtf:+.3f}），当前权重基本没有预测力")
    elif a < 0.08:
        parts.append(f"共振分有微弱预测力（IC {ic_mtf:+.3f}，{sign}）")
    elif a < 0.15:
        parts.append(f"共振分有中等预测力（IC {ic_mtf:+.3f}，{sign}）")
    else:
        parts.append(f"共振分预测力较强（IC {ic_mtf:+.3f}，{sign}）")

    if ic_base is not None:
        if abs(ic_mtf) > abs(ic_base) + 0.01:
            parts.append(f"多周期共振优于单周期（|{abs(ic_mtf):.3f}| vs |{abs(ic_base):.3f}|），聚合带来了信息")
        elif abs(ic_base) > abs(ic_mtf) + 0.01:
            parts.append(f"多周期共振不如单周期（|{abs(ic_mtf):.3f}| vs |{abs(ic_base):.3f}|），高周期权重可能过大")
        else:
            parts.append("多周期与单周期预测力接近，聚合没带来额外信息")

    if strat:
        parts.append(f"按分数方向持仓（每 {strat['step']} 根换仓）累计 {strat['ret_pct']:+.2f}%，"
                     f"同期买入持有 {strat['buyhold_pct']:+.2f}%")

    if n_up + n_dn > 0:
        eu = f"{e_up*100:+.2f}%" if e_up is not None else "--"
        ed = f"{e_dn*100:+.2f}%" if e_dn is not None else "--"
        parts.append(f"强信号（|分|>50）{n_up + n_dn} 次，按方向平均收益 多 {eu} / 空 {ed}")
    return "；".join(parts) + "。"
