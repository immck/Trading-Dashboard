"""多空损（预估清算地图）。

原理：拿不到每个人的真实开仓价和杠杆，但可以用公开数据反推分布——
  1. OI（持仓量 USD）× 多空比例 → 多头/空头各自的名义持仓
  2. 用最近 N 根的成交量加权收盘价，当作「开仓价分布」
  3. 假设杠杆呈固定分布（5/10/20/25/50/100 倍）
  4. 由 开仓价 + 杠杆 + 维持保证金率 → 该笔仓位在什么价位爆仓
  5. 把爆仓价位分箱累加 → 每个价位上「多损 / 空损」多少

这是估算模型（原站同类功能也标注为 oi_leverage 模型），不是交易所的真实强平数据。
"""
from __future__ import annotations

import numpy as np

from . import feed

# (杠杆, 权重)：权重之和为 1。加密市场实际分布偏中高杠杆
LEVERAGES = [(5, 0.07), (10, 0.17), (20, 0.27), (25, 0.15), (50, 0.22), (100, 0.12)]
MMR = 0.004           # 维持保证金率近似（U 本位永续常见 0.4%~1%）
MODEL = "oi_leverage_v3"

_cache: dict[tuple, tuple[float, dict]] = {}
TTL = 20


def _r(x, nd=2):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, nd)


def _px(x):
    """价格按量级自适应精度：BTC 两位小数，0.00xxxx 保留 10 位。"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    a = abs(f)
    if a >= 100:
        return round(f, 2)
    if a >= 1:
        return round(f, 5)
    if a >= 0.01:
        return round(f, 7)
    return round(f, 10)


def estimate(symbol: str, price: float, oi_usd: float, long_ratio: float,
             closes: np.ndarray, vols: np.ndarray,
             range_pct: float = 12.0, bins: int = 120) -> dict:
    if not price or not oi_usd or len(closes) < 20:
        return {}

    # ---- 1. 开仓价分布（成交量加权）----
    use = min(len(closes), 300)
    c = np.asarray(closes[-use:], dtype=float)
    v = np.asarray(vols[-use:], dtype=float)
    v = np.nan_to_num(v, nan=0.0)
    if v.sum() <= 0:
        v = np.ones_like(v)
    lo, hi = float(c.min()), float(c.max())
    if hi <= lo:
        lo, hi = price * 0.98, price * 1.02
    nb = 60
    edges = np.linspace(lo, hi, nb + 1)
    idx = np.clip(np.searchsorted(edges, c, side="right") - 1, 0, nb - 1)
    w = np.zeros(nb)
    np.add.at(w, idx, v)
    w = w / w.sum()
    entry = (edges[:-1] + edges[1:]) / 2.0

    # ---- 2. 多空持仓拆分 ----
    lr = long_ratio if (long_ratio and long_ratio > 0) else 1.0
    long_share = lr / (1.0 + lr)
    long_oi = oi_usd * long_share
    short_oi = oi_usd - long_oi

    # ---- 3. 清算价网格 ----
    g_lo = price * (1 - range_pct / 100.0)
    g_hi = price * (1 + range_pct / 100.0)
    grid = np.linspace(g_lo, g_hi, bins + 1)
    centers = (grid[:-1] + grid[1:]) / 2.0
    span = g_hi - g_lo

    long_usd = np.zeros(bins)
    short_usd = np.zeros(bins)

    def add(arr, liq_prices, notional_by_entry, alive_mask):
        """只统计「还没被扫掉」的仓位：多头清算价必须低于现价，空头必须高于现价。"""
        pos = ((liq_prices - g_lo) / span * bins).astype(int)
        ok = (pos >= 0) & (pos < bins) & alive_mask
        if ok.any():
            np.add.at(arr, pos[ok], notional_by_entry[ok])

    for lev, weight in LEVERAGES:
        # 多头：价格跌到 entry*(1 - 1/lev + mmr) 爆仓
        liq_l = entry * (1.0 - 1.0 / lev + MMR)
        add(long_usd, liq_l, long_oi * weight * w, liq_l < price)
        # 空头：价格涨到 entry*(1 + 1/lev - mmr) 爆仓
        liq_s = entry * (1.0 + 1.0 / lev - MMR)
        add(short_usd, liq_s, short_oi * weight * w, liq_s > price)

    total = long_usd + short_usd
    if total.max() <= 0:
        return {}

    # ---- 4. 输出：只保留有意义的档位 ----
    thresh = total.max() * 0.02
    levels = []
    for i in range(bins):
        if total[i] < thresh:
            continue
        levels.append({
            "price": _px(centers[i]),
            "long_usd": _r(long_usd[i]),
            "short_usd": _r(short_usd[i]),
            "total": _r(total[i]),
            "dist_pct": _r((centers[i] / price - 1) * 100, 3),
            "side": "long" if long_usd[i] >= short_usd[i] else "short",
        })

    below = [lv for lv in levels if lv["price"] < price]
    above = [lv for lv in levels if lv["price"] > price]

    def nearest(zones, key):
        """最近的「有份量」的档位：要求金额 ≥ 该侧峰值的 15%，再取距离最近。"""
        if not zones:
            return None
        peak = max(z[key] for z in zones)
        cand = [z for z in zones if z[key] >= peak * 0.15]
        cand.sort(key=lambda z: abs(z["price"] - price))
        return cand[0] if cand else None

    biggest_below = sorted(below, key=lambda x: -x["long_usd"])
    biggest_above = sorted(above, key=lambda x: -x["short_usd"])

    # 最近 3% 内的累计风险
    near_lo = price * 0.97
    near_hi = price * 1.03
    m_lo = (centers >= near_lo) & (centers < price)
    m_hi = (centers > price) & (centers <= near_hi)

    return {
        "model": MODEL,
        "price": _px(price),
        "range_pct": range_pct,
        "levels": levels,
        "long_total": _r(long_usd.sum()),
        "short_total": _r(short_usd.sum()),
        "nearest_long": nearest(below, "long_usd"),
        "nearest_short": nearest(above, "short_usd"),
        "biggest_long": biggest_below[0] if biggest_below else None,
        "biggest_short": biggest_above[0] if biggest_above else None,
        "top_long": biggest_below[:5],
        "top_short": biggest_above[:5],
        "near3_long_usd": _r(long_usd[m_lo].sum()),
        "near3_short_usd": _r(short_usd[m_hi].sum()),
        "long_share": _r(long_share * 100, 1),
        "leverages": [lv for lv, _ in LEVERAGES],
        "mmr": MMR,
    }


def build(symbol: str, range_pct: float = 12.0) -> dict:
    import time
    base = feed.base_symbol(feed.norm_symbol(symbol))
    now = time.time()
    key = (base, round(range_pct, 2))
    hit = _cache.get(key)
    if hit and now - hit[0] < TTL:
        return hit[1]

    from . import market
    snap = market.snapshot(base)
    price = snap.get("price")
    oi_usd = snap.get("oi_usd")
    lr = (snap.get("positioning") or {}).get("global_ratio")

    bars = feed.klines(base, "1h", want=320)
    out = estimate(base, price, oi_usd, lr, bars["close"], bars["volume"], range_pct)
    out["symbol"] = base
    out["ok"] = bool(out.get("levels"))
    _cache[key] = (now, out)
    return out
