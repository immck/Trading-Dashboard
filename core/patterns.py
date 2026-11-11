"""形态识别：K 线形态 + 图表结构形态。

输出统一结构：
{"key","label","dir"(1多/-1空/0中性),"idx","conf"(0~1),"note"}
"""
from __future__ import annotations

import numpy as np

from .indicators import swing_points


def _rng(h, l, c, o, i):
    return max(h[i] - l[i], 1e-12)


def _body(o, c, i):
    return abs(c[i] - o[i])


def _upper(h, o, c, i):
    return h[i] - max(o[i], c[i])


def _lower(l, o, c, i):
    return min(o[i], c[i]) - l[i]


# ================================================================ 单根 / 双根 / 三根
def _candles(o, h, l, c, lookback=6) -> list[dict]:
    out = []
    n = len(c)
    start = max(2, n - lookback)
    for i in range(start, n):
        rng = _rng(h, l, c, o, i)
        body = _body(o, c, i)
        up_w = _upper(h, o, c, i)
        lo_w = _lower(l, o, c, i)
        bull = c[i] > o[i]

        # 十字星
        if body <= rng * 0.1:
            out.append({"key": "doji", "label": "十字星", "dir": 0, "idx": i, "conf": 0.5,
                        "note": "多空胶着，等待方向选择"})
        # 锤子 / 上吊
        if lo_w >= body * 2 and up_w <= body * 0.6 and body > 0:
            key = "hammer" if not bull or i == start else "hanging_man"
            if bull:
                out.append({"key": "hammer", "label": "锤子线", "dir": 1, "idx": i, "conf": 0.62,
                            "note": "下影线长，下方承接明显"})
            else:
                out.append({"key": "hanging_man", "label": "上吊线", "dir": -1, "idx": i, "conf": 0.58,
                            "note": "高位长下影，警惕多头衰竭"})
        # 射击之星 / 倒锤头
        if up_w >= body * 2 and lo_w <= body * 0.6 and body > 0:
            if not bull:
                out.append({"key": "shooting_star", "label": "射击之星", "dir": -1, "idx": i, "conf": 0.62,
                            "note": "上影线长，上方抛压重"})
            else:
                out.append({"key": "inv_hammer", "label": "倒锤头", "dir": 1, "idx": i, "conf": 0.55,
                            "note": "低位长上影，试盘嫌疑"})

        if i < 1:
            continue
        # 吞没
        p_bull = c[i - 1] > o[i - 1]
        if bull and not p_bull and c[i] >= o[i - 1] and o[i] <= c[i - 1] and body > _body(o, c, i - 1):
            out.append({"key": "bull_engulf", "label": "看涨吞没", "dir": 1, "idx": i, "conf": 0.72,
                        "note": "阳线实体完全包住前一根阴线"})
        if (not bull) and p_bull and c[i] <= o[i - 1] and o[i] >= c[i - 1] and body > _body(o, c, i - 1):
            out.append({"key": "bear_engulf", "label": "看跌吞没", "dir": -1, "idx": i, "conf": 0.72,
                        "note": "阴线实体完全包住前一根阳线"})
        # 孕线
        if body < _body(o, c, i - 1) * 0.6 and max(o[i], c[i]) <= max(o[i - 1], c[i - 1]) \
                and min(o[i], c[i]) >= min(o[i - 1], c[i - 1]):
            d = 1 if p_bull else -1
            out.append({"key": "bull_harami" if p_bull else "bear_harami",
                        "label": "看涨孕线" if p_bull else "看跌孕线",
                        "dir": d, "idx": i, "conf": 0.45,
                        "note": "小实体被前一根包裹，动能减弱"})

        if i < 2:
            continue
        b0, b1, b2 = o[i - 2], o[i - 1], o[i]
        c0, c1, c2 = c[i - 2], c[i - 1], c[i]
        # 早晨之星 / 黄昏之星
        if c0 < b0 and _body(o, c, i - 1) <= _rng(h, l, c, o, i - 1) * 0.3 and c2 > b2 and c2 > (b0 + c0) / 2:
            out.append({"key": "morning_star", "label": "早晨之星", "dir": 1, "idx": i, "conf": 0.75,
                        "note": "底部反转三根组合"})
        if c0 > b0 and _body(o, c, i - 1) <= _rng(h, l, c, o, i - 1) * 0.3 and c2 < b2 and c2 < (b0 + c0) / 2:
            out.append({"key": "evening_star", "label": "黄昏之星", "dir": -1, "idx": i, "conf": 0.75,
                        "note": "顶部反转三根组合"})
        # 红三兵 / 三只乌鸦
        if all(c[k] > o[k] for k in (i - 2, i - 1, i)) and c2 > c1 > c0:
            out.append({"key": "three_soldiers", "label": "红三兵", "dir": 1, "idx": i, "conf": 0.7,
                        "note": "连续三根阳线，多头持续推进"})
        if all(c[k] < o[k] for k in (i - 2, i - 1, i)) and c2 < c1 < c0:
            out.append({"key": "three_crows", "label": "三只乌鸦", "dir": -1, "idx": i, "conf": 0.7,
                        "note": "连续三根阴线，空头持续压制"})
    return out


# ================================================================ 图表形态
def _chart(o, h, l, c, lookback=120) -> list[dict]:
    out = []
    n = len(c)
    if n < 30:
        return out
    start = max(0, n - lookback)
    hi, lo = swing_points(h[start:], l[start:])
    hi, lo = hi + start, lo + start
    price = float(c[-1])

    # 双底 / 双顶
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        if abs(l[b] - l[a]) / max(l[a], 1e-9) < 0.02 and b - a >= 5:
            neck = float(np.max(h[a:b + 1]))
            out.append({"key": "double_bottom", "label": "双底", "dir": 1, "idx": int(b),
                        "conf": 0.68 if price > neck else 0.55,
                        "note": f"低点 {l[a]:.2f}/{l[b]:.2f}，颈线 {neck:.2f}"})
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        if abs(h[b] - h[a]) / max(h[a], 1e-9) < 0.02 and b - a >= 5:
            neck = float(np.min(l[a:b + 1]))
            out.append({"key": "double_top", "label": "双顶", "dir": -1, "idx": int(b),
                        "conf": 0.68 if price < neck else 0.55,
                        "note": f"高点 {h[a]:.2f}/{h[b]:.2f}，颈线 {neck:.2f}"})

    # 20 周期突破
    win = min(20, n - 1)
    if n > win:
        hh = float(np.max(h[-win - 1:-1]))
        ll = float(np.min(l[-win - 1:-1]))
        if price > hh:
            out.append({"key": "breakout_up", "label": f"突破{win}周期高点", "dir": 1, "idx": n - 1,
                        "conf": 0.7, "note": f"突破 {hh:.2f}"})
        if price < ll:
            out.append({"key": "breakout_dn", "label": f"跌破{win}周期低点", "dir": -1, "idx": n - 1,
                        "conf": 0.7, "note": f"跌破 {ll:.2f}"})

    # 三角收敛 / 通道
    if len(hi) >= 2 and len(lo) >= 2:
        hi_slope = (h[hi[-1]] - h[hi[-2]]) / max(hi[-1] - hi[-2], 1)
        lo_slope = (l[lo[-1]] - l[lo[-2]]) / max(lo[-1] - lo[-2], 1)
        if hi_slope < 0 and lo_slope > 0:
            out.append({"key": "triangle", "label": "对称三角收敛", "dir": 0, "idx": n - 1,
                        "conf": 0.5, "note": "高点下移、低点上移，等待突破"})
        elif hi_slope > 0 and lo_slope > 0:
            out.append({"key": "channel_up", "label": "上升通道", "dir": 1, "idx": n - 1,
                        "conf": 0.55, "note": "高低点同步上移"})
        elif hi_slope < 0 and lo_slope < 0:
            out.append({"key": "channel_dn", "label": "下降通道", "dir": -1, "idx": n - 1,
                        "conf": 0.55, "note": "高低点同步下移"})

    # 缺口
    if n >= 2 and l[-1] > h[-2]:
        out.append({"key": "gap_up", "label": "向上跳空", "dir": 1, "idx": n - 1, "conf": 0.55,
                    "note": f"缺口 {h[-2]:.2f}~{l[-1]:.2f}"})
    if n >= 2 and h[-1] < l[-2]:
        out.append({"key": "gap_dn", "label": "向下跳空", "dir": -1, "idx": n - 1, "conf": 0.55,
                    "note": f"缺口 {h[-1]:.2f}~{l[-2]:.2f}"})
    return _dedupe(out)


# 互斥形态组：同一组内只能保留一个（取最新、最可信的）
_EXCLUSIVE = (
    {"double_bottom", "double_top"},
    {"channel_up", "channel_dn", "triangle"},
    {"gap_up", "gap_dn"},
    {"breakout_up", "breakout_dn"},
)


def _dedupe(pats: list[dict]) -> list[dict]:
    """同一互斥组里只留 idx 最新、conf 最高的那个，避免自相矛盾。"""
    kept = []
    used: set[str] = set()
    for group in _EXCLUSIVE:
        members = [p for p in pats if p["key"] in group]
        if not members:
            continue
        best = max(members, key=lambda p: (p["idx"], p["conf"]))
        kept.append(best)
        used |= {p["key"] for p in members}
    kept += [p for p in pats if p["key"] not in used]
    return kept


def detect(bars: dict, candle_lookback: int = 6) -> dict:
    o, h, l, c = bars["open"], bars["high"], bars["low"], bars["close"]
    candles = _candles(o, h, l, c, candle_lookback)
    chart = _chart(o, h, l, c)
    # 近 3 根内才算「当前有效」
    n = len(c)
    recent = [p for p in candles if p["idx"] >= n - 3]
    bull = sum(p["conf"] for p in recent if p["dir"] > 0)
    bear = sum(p["conf"] for p in recent if p["dir"] < 0)
    latest = max(recent, key=lambda p: (p["idx"], p["conf"])) if recent else None
    return {
        "candles": candles,
        "chart": chart,
        "recent": recent,
        "bias": 1 if bull > bear + 0.15 else -1 if bear > bull + 0.15 else 0,
        "bull_score": round(bull, 3),
        "bear_score": round(bear, 3),
        "latest": latest,
    }
