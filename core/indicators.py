"""技术指标库：纯 numpy 实现，输出与主流图表软件口径一致。

约定：所有函数接收等长 numpy 一维数组，返回等长数组，前置不足部分填 NaN。
"""
from __future__ import annotations

import numpy as np

from . import config as C

NAN = np.nan


# ================================================================ 基础
def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, NAN)
    if len(x) < n or n <= 0:
        return out
    cs = np.cumsum(np.nan_to_num(x, nan=0.0), dtype=float)
    out[n - 1:] = (cs[n - 1:] - np.concatenate(([0.0], cs[:-n]))) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """以首 n 个值的均值为种子，与主流平台一致。"""
    out = np.full(x.shape, NAN)
    if len(x) < n or n <= 0:
        return out
    a = 2.0 / (n + 1.0)
    prev = float(np.mean(x[:n]))
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = a * x[i] + (1 - a) * prev
        out[i] = prev
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder 平滑（RSI/ATR/ADX 用）。"""
    out = np.full(x.shape, NAN)
    if len(x) < n or n <= 0:
        return out
    a = 1.0 / n
    prev = float(np.mean(x[:n]))
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = a * x[i] + (1 - a) * prev
        out[i] = prev
    return out


def _roll_max(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, NAN)
    if len(x) < n:
        return out
    for i in range(n - 1, len(x)):
        out[i] = np.max(x[i - n + 1:i + 1])
    return out


def _roll_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, NAN)
    if len(x) < n:
        return out
    for i in range(n - 1, len(x)):
        out[i] = np.min(x[i - n + 1:i + 1])
    return out


def _roll_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, NAN)
    if len(x) < n:
        return out
    for i in range(n - 1, len(x)):
        out[i] = np.std(x[i - n + 1:i + 1])
    return out


def _true_range(h, l, c) -> np.ndarray:
    tr = np.empty(h.shape)
    tr[0] = h[0] - l[0]
    pc = c[:-1]
    tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - pc), np.abs(l[1:] - pc)])
    return tr


# ================================================================ 振荡类
def macd(c: np.ndarray, fast=12, slow=26, sig=9) -> dict:
    ef, es = ema(c, fast), ema(c, slow)
    dif = ef - es
    dea = ema(np.nan_to_num(dif, nan=0.0), sig)
    dea[:sig - 1] = NAN
    hist = (dif - dea) * 2.0          # 国内软件惯例：柱 = 2*(DIF-DEA)
    return {"dif": dif, "dea": dea, "hist": hist}


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(c.shape, NAN)
    if len(c) < n + 1:
        return out
    d = np.diff(c)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag = rma(gain, n)
    al = rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(al > 0, ag / al, np.inf)
        r = np.where(al > 0, 100.0 - 100.0 / (1.0 + rs), 100.0)
    out[1:] = r
    return out


def stoch_rsi(c: np.ndarray, n=14, k=14, kn=3, dn=3) -> dict:
    r = rsi(c, n)
    rv = np.nan_to_num(r, nan=50.0)
    hh = _roll_max(rv, k)
    ll = _roll_min(rv, k)
    rng = hh - ll
    with np.errstate(divide="ignore", invalid="ignore"):
        st = np.where(rng > 0, (rv - ll) / rng * 100.0, 50.0)
    st = np.where(np.isnan(hh), NAN, st)
    kk = sma(np.nan_to_num(st, nan=50.0), kn)
    dd = sma(np.nan_to_num(kk, nan=50.0), dn)
    return {"stoch_rsi": st, "k": kk, "d": dd}


def boll(c: np.ndarray, n=20, mult=2.0) -> dict:
    mid = sma(c, n)
    sd = _roll_std(c, n)
    return {"mid": mid, "upper": mid + mult * sd, "lower": mid - mult * sd}


def kdj(h, l, c, n=9, k1=3, d1=3) -> dict:
    hh = _roll_max(h, n)
    ll = _roll_min(l, n)
    rng = hh - ll
    with np.errstate(divide="ignore", invalid="ignore"):
        rsv = np.where(rng > 0, (c - ll) / rng * 100.0, 50.0)
    rsv = np.where(np.isnan(hh), NAN, rsv)
    K = np.full(c.shape, NAN)
    D = np.full(c.shape, NAN)
    ks = ds = 50.0
    for i in range(len(c)):
        if np.isnan(rsv[i]):
            continue
        ks = (2.0 / k1) * ks + (1.0 / k1) * rsv[i] if k1 else rsv[i]
        ds = (2.0 / d1) * ds + (1.0 / d1) * ks if d1 else ks
        K[i] = ks
        D[i] = ds
    return {"k": K, "d": D, "j": 3.0 * K - 2.0 * D, "rsv": rsv}


def mfi(h, l, c, v, n=14) -> np.ndarray:
    out = np.full(c.shape, NAN)
    if len(c) < n + 1:
        return out
    tp = (h + l + c) / 3.0
    mf = tp * v
    d = np.diff(tp)
    pos = np.where(d > 0, mf[1:], 0.0)
    neg = np.where(d < 0, mf[1:], 0.0)
    ps = sma(pos, n)
    ns = sma(neg, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        mr = np.where(ns > 0, ps / ns, np.inf)
        r = np.where(ns > 0, 100.0 - 100.0 / (1.0 + mr), 100.0)
    out[1:] = r
    return out


def cci(h, l, c, n=20) -> np.ndarray:
    out = np.full(c.shape, NAN)
    tp = (h + l + c) / 3.0
    if len(c) < n:
        return out
    for i in range(n - 1, len(c)):
        w = tp[i - n + 1:i + 1]
        m = w.mean()
        mad = np.abs(w - m).mean()
        out[i] = (tp[i] - m) / (0.015 * mad) if mad > 0 else 0.0
    return out


def williams_r(h, l, c, n=14) -> np.ndarray:
    hh = _roll_max(h, n)
    ll = _roll_min(l, n)
    rng = hh - ll
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(rng > 0, (hh - c) / rng * -100.0, -50.0)
    return np.where(np.isnan(hh), NAN, out)


def cmf(h, l, c, v, n=20) -> np.ndarray:
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        mfv = np.where(rng > 0, ((c - l) - (h - c)) / rng * v, 0.0)
    vs = sma(v, n)
    ms = sma(mfv, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(vs > 0, ms / vs, 0.0)


def obv(c, v) -> np.ndarray:
    out = np.zeros(c.shape)
    if len(c) < 2:
        return out
    d = np.diff(c)
    step = np.where(d > 0, v[1:], np.where(d < 0, -v[1:], 0.0))
    out[1:] = np.cumsum(step)
    return out


def elder_ray(h, l, c, n=13) -> dict:
    bull = h - ema(l, n)
    bear = l - ema(h, n)
    return {"bull": bull, "bear": bear}


# ================================================================ 趋势类
def atr(h, l, c, n=14) -> np.ndarray:
    return rma(_true_range(h, l, c), n)


def adx(h, l, c, n=14) -> dict:
    tr = _true_range(h, l, c)
    up = np.zeros(h.shape)
    dn = np.zeros(h.shape)
    up[1:] = h[1:] - h[:-1]
    dn[1:] = l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_s = rma(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr_s > 0, 100.0 * rma(plus_dm, n) / atr_s, 0.0)
        mdi = np.where(atr_s > 0, 100.0 * rma(minus_dm, n) / atr_s, 0.0)
        s = pdi + mdi
        dx = np.where(s > 0, 100.0 * np.abs(pdi - mdi) / s, 0.0)
    adx_s = rma(dx, n)
    adx_s[:n] = NAN
    return {"adx": adx_s, "plus_di": pdi, "minus_di": mdi}


def aroon(h, l, n=25) -> dict:
    up = np.full(h.shape, NAN)
    dn = np.full(h.shape, NAN)
    if len(h) < n:
        return {"up": up, "down": dn}
    for i in range(n - 1, len(h)):
        wh = h[i - n + 1:i + 1]
        wl = l[i - n + 1:i + 1]
        since_h = n - 1 - int(np.argmax(wh))
        since_l = n - 1 - int(np.argmin(wl))
        up[i] = 100.0 * (n - since_h) / n
        dn[i] = 100.0 * (n - since_l) / n
    return {"up": up, "down": dn}


def supertrend(h, l, c, n=10, mult=3.0) -> dict:
    a = atr(h, l, c, n)
    hl2 = (h + l) / 2.0
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    direction = np.full(c.shape, 1, dtype=int)
    value = np.full(c.shape, NAN)
    fu = fl = None
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        u, lo = upper[i], lower[i]
        if fu is None:
            fu, fl = u, lo
            value[i] = fl
            direction[i] = 1
            continue
        fu = u if (u < fu or c[i - 1] > fu) else fu
        fl = lo if (lo > fl or c[i - 1] < fl) else fl
        if c[i] > fu:
            direction[i] = 1
        elif c[i] < fl:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        value[i] = fl if direction[i] == 1 else fu
    return {"value": value, "direction": direction}


def vwap_rolling(h, l, c, v, n=50) -> np.ndarray:
    tp = (h + l + c) / 3.0
    pv = np.nan_to_num(tp * v)
    sv = sma(v, n)
    spv = sma(pv, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sv > 0, spv / sv, NAN)


# ================================================================ 结构类
def swing_points(h, l, left=3, right=3) -> tuple[np.ndarray, np.ndarray]:
    """分形法找摆动高低点，返回 (高点索引, 低点索引)。"""
    hi, lo = [], []
    n = len(h)
    for i in range(left, n - right):
        w_h = h[i - left:i + right + 1]
        w_l = l[i - left:i + right + 1]
        if h[i] == w_h.max() and (w_h == h[i]).sum() == 1:
            hi.append(i)
        if l[i] == w_l.min() and (w_l == l[i]).sum() == 1:
            lo.append(i)
    return np.array(hi, dtype=int), np.array(lo, dtype=int)


def trend_structure(h, l, lookback=120) -> dict:
    """用摆动点判断 HH/HL（上升结构）还是 LH/LL（下降结构）。"""
    n = len(h)
    start = max(0, n - lookback)
    hi, lo = swing_points(h[start:], l[start:])
    hi = hi + start
    lo = lo + start
    out = {"higher_high": False, "higher_low": False,
           "lower_high": False, "lower_low": False,
           "label": "盘整", "score": 0, "swings": {"highs": hi[-4:].tolist(), "lows": lo[-4:].tolist()}}
    if len(hi) >= 2:
        out["higher_high"] = bool(h[hi[-1]] > h[hi[-2]])
        out["lower_high"] = bool(h[hi[-1]] < h[hi[-2]])
    if len(lo) >= 2:
        out["higher_low"] = bool(l[lo[-1]] > l[lo[-2]])
        out["lower_low"] = bool(l[lo[-1]] < l[lo[-2]])
    s = 0
    s += 1 if out["higher_high"] else 0
    s += 1 if out["higher_low"] else 0
    s -= 1 if out["lower_high"] else 0
    s -= 1 if out["lower_low"] else 0
    out["score"] = s
    out["label"] = ("上升结构" if s >= 2 else "下降结构" if s <= -2
                    else "偏多震荡" if s == 1 else "偏空震荡" if s == -1 else "盘整")
    return out


def support_resistance(h, l, c, lookback=120) -> dict:
    """摆动点 + 成交量加权的支撑阻力聚类。"""
    n = len(c)
    start = max(0, n - lookback)
    hi, lo = swing_points(h[start:], l[start:])
    hi, lo = hi + start, lo + start
    price = float(c[-1])
    tol = price * C.SR_CLUSTER_PCT / 100.0

    def cluster(idxs, arr):
        pts = sorted((float(arr[i]), i) for i in idxs)
        groups = []
        for p, i in pts:
            if groups and abs(p - groups[-1]["price"]) <= tol:
                g = groups[-1]
                g["price"] = (g["price"] * g["n"] + p) / (g["n"] + 1)
                g["n"] += 1
                g["idx"].append(int(i))
            else:
                groups.append({"price": p, "n": 1, "idx": [int(i)]})
        return groups

    res = [g for g in cluster(hi, h) if g["price"] > price]
    sup = [g for g in cluster(lo, l) if g["price"] < price]
    res.sort(key=lambda g: g["price"])
    sup.sort(key=lambda g: -g["price"])
    return {
        "resistance": [{"price": round(g["price"], 6), "touches": g["n"]} for g in res[:5]],
        "support": [{"price": round(g["price"], 6), "touches": g["n"]} for g in sup[:5]],
    }


def fib_levels(h, l, lookback=120) -> dict:
    n = len(h)
    start = max(0, n - lookback)
    hh = float(np.max(h[start:]))
    ll = float(np.min(l[start:]))
    hi_i = int(np.argmax(h[start:])) + start
    lo_i = int(np.argmin(l[start:])) + start
    up = hi_i > lo_i          # 近期从低到高 -> 上涨段
    diff = hh - ll
    ratios = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
    lv = {}
    for r in ratios:
        lv[str(r)] = round(hh - diff * r if up else ll + diff * r, 6)
    return {"levels": lv, "high": hh, "low": ll, "direction": "up" if up else "down"}


def volume_profile(h, l, c, v, bins=40, lookback=240) -> dict:
    n = len(c)
    start = max(0, n - lookback)
    hh = float(np.max(h[start:]))
    ll = float(np.min(l[start:]))
    if hh <= ll:
        return {}
    edges = np.linspace(ll, hh, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    vol = np.zeros(bins)
    for i in range(start, n):
        lo_b = int(np.searchsorted(edges, l[i], side="right") - 1)
        hi_b = int(np.searchsorted(edges, h[i], side="right") - 1)
        lo_b = max(0, min(bins - 1, lo_b))
        hi_b = max(0, min(bins - 1, hi_b))
        span = hi_b - lo_b + 1
        vol[lo_b:hi_b + 1] += v[i] / span
    poc_i = int(np.argmax(vol))
    total = vol.sum()
    # 价值区：围绕 POC 扩展到 70%
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < total * 0.7 and (lo_i > 0 or hi_i < bins - 1):
        left = vol[lo_i - 1] if lo_i > 0 else -1
        right = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if right >= left:
            hi_i += 1
            acc += vol[hi_i]
        else:
            lo_i -= 1
            acc += vol[lo_i]
    return {
        "poc": round(float(centers[poc_i]), 6),
        "vah": round(float(centers[hi_i]), 6),
        "val": round(float(centers[lo_i]), 6),
        "bins": [{"price": round(float(centers[i]), 6), "volume": float(vol[i])}
                 for i in range(bins) if vol[i] > 0],
    }


# ================================================================ TradingView 常用指标
def wma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, NAN)
    if len(x) < n or n <= 0:
        return out
    w = np.arange(1, n + 1, dtype=float)
    wsum = w.sum()
    for i in range(n - 1, len(x)):
        out[i] = float(np.dot(x[i - n + 1:i + 1], w)) / wsum
    return out


def hma(x: np.ndarray, n: int) -> np.ndarray:
    """Hull MA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))"""
    half = max(1, int(n / 2))
    root = max(1, int(round(np.sqrt(n))))
    raw = 2.0 * wma(x, half) - wma(x, n)
    return wma(np.nan_to_num(raw, nan=0.0), root)


def vwma(c, v, n=20) -> np.ndarray:
    pv = np.nan_to_num(c * v)
    sv = sma(v, n)
    spv = sma(pv, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sv > 0, spv / sv, NAN)


def _shift_fwd(a: np.ndarray, n: int) -> np.ndarray:
    """整体前移 n 根（Ichimoku 的先行span用）。"""
    out = np.full(a.shape, NAN)
    if len(a) > n:
        out[n:] = a[:-n]
    return out


def ichimoku(h, l, c, conv=9, base=26, spanb=52) -> dict:
    """一目均衡表。先行 A/B 已按标准前移 base 根，绘制时直接对齐当前时间轴。"""
    tenkan = (_roll_max(h, conv) + _roll_min(l, conv)) / 2.0
    kijun = (_roll_max(h, base) + _roll_min(l, base)) / 2.0
    senkou_a = _shift_fwd((tenkan + kijun) / 2.0, base)
    raw_b = (_roll_max(h, spanb) + _roll_min(l, spanb)) / 2.0
    senkou_b = _shift_fwd(raw_b, base)
    cloud_top = np.fmax(senkou_a, senkou_b)
    cloud_bot = np.fmin(senkou_a, senkou_b)
    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a,
            "senkou_b": senkou_b, "cloud_top": cloud_top, "cloud_bot": cloud_bot}


def psar(h, l, c, af0=0.02, af_step=0.02, af_max=0.2) -> np.ndarray:
    """抛物线 SAR（Wilder 标准算法）。"""
    n = len(c)
    out = np.full(n, NAN)
    if n < 3:
        return out
    bull = c[1] >= c[0]
    af = af0
    ep = h[0] if bull else l[0]
    sar = l[0] if bull else h[0]
    out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i - 1], l[max(0, i - 2)])
            if l[i] < sar:                 # 反转
                bull = False
                sar = ep
                ep = l[i]
                af = af0
            else:
                if h[i] > ep:
                    ep = h[i]
                    af = min(af_max, af + af_step)
        else:
            sar = max(sar, h[i - 1], h[max(0, i - 2)])
            if h[i] > sar:
                bull = True
                sar = ep
                ep = h[i]
                af = af0
            else:
                if l[i] < ep:
                    ep = l[i]
                    af = min(af_max, af + af_step)
        out[i] = sar
    return out


def keltner(h, l, c, n=20, mult=2.0, atr_n=10) -> dict:
    mid = ema(c, n)
    a = atr(h, l, c, atr_n)
    return {"mid": mid, "upper": mid + mult * a, "lower": mid - mult * a}


def donchian(h, l, n=20) -> dict:
    up, dn = _roll_max(h, n), _roll_min(l, n)
    return {"upper": up, "lower": dn, "mid": (up + dn) / 2.0}


def chandelier_exit(h, l, c, n=22, mult=3.0) -> dict:
    a = atr(h, l, c, n)
    long_stop = _roll_max(h, n) - mult * a
    short_stop = _roll_min(l, n) + mult * a
    direction = np.zeros(len(c), dtype=int)
    value = np.full(len(c), NAN)
    d = 1
    for i in range(len(c)):
        if np.isnan(long_stop[i]):
            continue
        if d > 0 and c[i] < long_stop[i]:
            d = -1
        elif d < 0 and c[i] > short_stop[i]:
            d = 1
        direction[i] = d
        value[i] = long_stop[i] if d > 0 else short_stop[i]
    return {"value": value, "direction": direction}


def pivot_points(ts, h, l, c) -> dict:
    """标准枢轴点：用「上一个完整 UTC 日」的高/低/收。"""
    if len(ts) < 2:
        return {}
    day = ts // 86_400_000
    last = day[-1]
    prev_mask = day != last
    if not prev_mask.any():
        return {}
    prev_day = day[prev_mask][-1]
    m = day == prev_day
    H, L, C = float(np.max(h[m])), float(np.min(l[m])), float(c[m][-1])
    P = (H + L + C) / 3.0
    return {
        "P": round(P, 8), "R1": round(2 * P - L, 8), "S1": round(2 * P - H, 8),
        "R2": round(P + (H - L), 8), "S2": round(P - (H - L), 8),
        "R3": round(H + 2 * (P - L), 8), "S3": round(L - 2 * (H - P), 8),
    }


def stochastic(h, l, c, n=14, k_n=3, d_n=3) -> dict:
    """经典随机指标（不同于 KDJ 的平滑方式）。"""
    hh, ll = _roll_max(h, n), _roll_min(l, n)
    rng = hh - ll
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(rng > 0, (c - ll) / rng * 100.0, 50.0)
    raw = np.where(np.isnan(hh), NAN, raw)
    k = sma(np.nan_to_num(raw, nan=50.0), k_n)
    d = sma(np.nan_to_num(k, nan=50.0), d_n)
    return {"k": k, "d": d}


def awesome_oscillator(h, l, fast=5, slow=34) -> np.ndarray:
    med = (h + l) / 2.0
    return sma(med, fast) - sma(med, slow)


def trix(c, n=15, sig=9) -> dict:
    e1 = ema(c, n)
    e2 = ema(np.nan_to_num(e1, nan=c[0]), n)
    e3 = ema(np.nan_to_num(e2, nan=c[0]), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(e3[:-1] > 0, (e3[1:] - e3[:-1]) / e3[:-1] * 100.0, 0.0)
    out = np.full(c.shape, NAN)
    out[1:] = t
    out[:n * 3] = NAN
    return {"trix": out, "signal": ema(np.nan_to_num(out, nan=0.0), sig)}


def ultimate_oscillator(h, l, c, n1=7, n2=14, n3=28) -> np.ndarray:
    n = len(c)
    out = np.full(n, NAN)
    if n < n3 + 1:
        return out
    pc = c[:-1]
    bp = c[1:] - np.minimum(l[1:], pc)
    tr = np.maximum(h[1:], pc) - np.minimum(l[1:], pc)
    for i in range(n3, n):
        b1 = bp[i - n1:i].sum(); t1 = tr[i - n1:i].sum()
        b2 = bp[i - n2:i].sum(); t2 = tr[i - n2:i].sum()
        b3 = bp[i - n3:i].sum(); t3 = tr[i - n3:i].sum()
        if t1 <= 0 or t2 <= 0 or t3 <= 0:
            continue
        out[i] = 100.0 * (4 * b1 / t1 + 2 * b2 / t2 + b3 / t3) / 7.0
    return out


def vortex(h, l, c, n=14) -> dict:
    tr = _true_range(h, l, c)
    vm_plus = np.abs(h[1:] - l[:-1])
    vm_minus = np.abs(l[1:] - h[:-1])
    st = sma(tr, n)[1:]
    sp = sma(np.concatenate(([0.0], vm_plus)), n)[1:]
    sm = sma(np.concatenate(([0.0], vm_minus)), n)[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        vip = np.where(st > 0, sp / st, NAN)
        vim = np.where(st > 0, sm / st, NAN)
    n_ = len(c)
    out_p = np.full(n_, NAN); out_m = np.full(n_, NAN)
    out_p[1:] = vip; out_m[1:] = vim
    return {"vi_plus": out_p, "vi_minus": out_m}


def chaikin_osc(h, l, c, v, fast=3, slow=10) -> dict:
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        mfv = np.where(rng > 0, ((c - l) - (h - c)) / rng * v, 0.0)
    adl = np.cumsum(mfv)
    return {"osc": ema(adl, fast) - ema(adl, slow), "adl": adl}


def squeeze_momentum(h, l, c, n=20, bb_mult=2.0, kc_mult=1.5) -> dict:
    """TTM Squeeze：布林带被肯特纳通道包住时=挤压中，动能是线性回归值。"""
    bb = boll(c, n, bb_mult)
    kc = keltner(h, l, c, n, kc_mult, n)
    mid = (bb["mid"] + kc["mid"]) / 2.0
    base = np.nan_to_num(c - mid, nan=0.0)
    mom = np.full(len(c), NAN)
    for i in range(n - 1, len(c)):
        y = base[i - n + 1:i + 1]
        x = np.arange(n, dtype=float)
        xm, ym = x.mean(), y.mean()
        den = ((x - xm) ** 2).sum()
        if den > 0:
            slope = ((x - xm) * (y - ym)).sum() / den
            mom[i] = ym + slope * (x[-1] - xm)
    squeeze_on = (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])
    return {"momentum": mom, "on": squeeze_on.astype(int)}


def dpo(c, n=20) -> np.ndarray:
    """Detrended Price Oscillator。

    标准画法要把均线前移 n/2+1 根，导致最后 n/2+1 个点是空的。
    这里改用等价形式：DPO[i] = close[i] − SMA[i−shift]，
    这样最后一根有值，画图和读信号都不会断尾。
    """
    shift = n // 2 + 1
    ma = sma(c, n)
    out = np.full(c.shape, NAN)
    for i in range(shift, len(c)):
        j = i - shift
        if j >= 0 and not np.isnan(ma[j]):
            out[i] = c[i] - ma[j]
    return out


def balance_of_power(o, c, h, l) -> np.ndarray:
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(rng > 0, (c - o) / rng, 0.0)


def divergence_rsi(h, l, c, rsi_arr, lookback=80) -> list[dict]:
    """RSI 背离（供形态/信号复用的轻量版）。"""
    n = len(c)
    if n < lookback:
        return []
    hi, lo = swing_points(h[-lookback:], l[-lookback:])
    hi, lo = hi + n - lookback, lo + n - lookback
    out = []
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        if h[b] > h[a] and rsi_arr[b] < rsi_arr[a] - 1:
            out.append({"dir": -1, "label": "顶背离", "at": int(b)})
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        if l[b] < l[a] and rsi_arr[b] > rsi_arr[a] + 1:
            out.append({"dir": 1, "label": "底背离", "at": int(b)})
    return out


# ================================================================ 汇总
def compute_all(bars: dict) -> dict:
    """一次算全部指标，返回 {name: array | dict of arrays}。"""
    o, h, l, c, v = bars["open"], bars["high"], bars["low"], bars["close"], bars["volume"]
    ind: dict = {}
    ind["ma"] = {str(p): sma(c, p) for p in C.MA_PERIODS}
    ind["ema"] = {str(p): ema(c, p) for p in C.EMA_PERIODS}
    ind["macd"] = macd(c, *C.MACD)
    ind["rsi"] = rsi(c, C.RSI_PERIOD)
    ind["boll"] = boll(c, *C.BOLL)
    ind["kdj"] = kdj(h, l, c, *C.KDJ)
    ind["obv"] = {"obv": obv(c, v)}
    ind["adx"] = adx(h, l, c, C.ADX_PERIOD)
    ind["atr"] = atr(h, l, c, C.ATR_PERIOD)
    ind["mfi"] = mfi(h, l, c, v, C.MFI_PERIOD)
    ind["cci"] = cci(h, l, c, C.CCI_PERIOD)
    ind["wr"] = williams_r(h, l, c, C.WR_PERIOD)
    ind["cmf"] = cmf(h, l, c, v, C.CMF_PERIOD)
    ind["aroon"] = aroon(h, l, C.AROON_PERIOD)
    ind["stoch_rsi"] = stoch_rsi(c, *C.STOCHRSI)
    ind["supertrend"] = supertrend(h, l, c, *C.SUPERTREND)
    ind["elder"] = elder_ray(h, l, c)
    ind["vwap"] = vwap_rolling(h, l, c, v, C.VWAP_PERIOD)
    ind["structure"] = trend_structure(h, l)
    ind["sr"] = support_resistance(h, l, c, C.SR_LOOKBACK)
    ind["fib"] = fib_levels(h, l)
    ind["volprofile"] = volume_profile(h, l, c, v, C.VOLPROFILE_BINS)

    # ---- TradingView 常用指标 ----
    ind["ichimoku"] = ichimoku(h, l, c)
    ind["psar"] = psar(h, l, c)
    ind["keltner"] = keltner(h, l, c)
    ind["donchian"] = donchian(h, l)
    ind["hma"] = hma(c, 55)
    ind["vwma"] = vwma(c, v, 20)
    ind["chandelier"] = chandelier_exit(h, l, c)
    ind["pivot"] = pivot_points(bars["ts"], h, l, c)
    ind["stoch"] = stochastic(h, l, c)
    ind["ao"] = awesome_oscillator(h, l)
    ind["trix"] = trix(c)
    ind["uo"] = ultimate_oscillator(h, l, c)
    ind["vortex"] = vortex(h, l, c)
    ind["chaikin"] = chaikin_osc(h, l, c, v)
    ind["squeeze"] = squeeze_momentum(h, l, c)
    ind["dpo"] = dpo(c)
    ind["bop"] = balance_of_power(o, c, h, l)
    return ind


def _f(x, nd=8):
    """numpy 标量 -> 可 JSON 化的 float。"""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or np.isinf(f)) else round(f, nd)


def snapshot(ind: dict, bars: dict, i: int = -1) -> dict:
    """把指标序列在某一根上的值压成扁平标量，供 AI/表格直接消费。"""
    def at(d, key):
        try:
            return _f(d[key][i])
        except Exception:  # noqa: BLE001
            return None

    s = {"close": _f(bars["close"][i]), "ts": int(bars["ts"][i])}
    for p in C.MA_PERIODS:
        s[f"ma{p}"] = at(ind["ma"], str(p))
    for p in C.EMA_PERIODS:
        s[f"ema{p}"] = at(ind["ema"], str(p))
    for k in ("dif", "dea", "hist"):
        s[f"macd_{k}"] = at(ind["macd"], k)
    s["rsi"] = at({"r": ind["rsi"]}, "r")
    for k in ("mid", "upper", "lower"):
        s[f"boll_{k}"] = at(ind["boll"], k)
    for k in ("k", "d", "j"):
        s[f"kdj_{k}"] = at(ind["kdj"], k)
    s["adx"] = at(ind["adx"], "adx")
    s["di_plus"] = at(ind["adx"], "plus_di")
    s["di_minus"] = at(ind["adx"], "minus_di")
    s["atr"] = at({"a": ind["atr"]}, "a")
    s["mfi"] = at({"a": ind["mfi"]}, "a")
    s["cci"] = at({"a": ind["cci"]}, "a")
    s["wr"] = at({"a": ind["wr"]}, "a")
    s["cmf"] = at({"a": ind["cmf"]}, "a")
    s["aroon_up"] = at(ind["aroon"], "up")
    s["aroon_down"] = at(ind["aroon"], "down")
    s["st_dir"] = int(ind["supertrend"]["direction"][i])
    s["st_value"] = at(ind["supertrend"], "value")
    s["vwap"] = at({"a": ind["vwap"]}, "a")
    s["obv"] = at(ind["obv"], "obv")
    # TradingView 系列
    s["ichi_tenkan"] = at(ind["ichimoku"], "tenkan")
    s["ichi_kijun"] = at(ind["ichimoku"], "kijun")
    s["ichi_cloud_top"] = at(ind["ichimoku"], "cloud_top")
    s["ichi_cloud_bot"] = at(ind["ichimoku"], "cloud_bot")
    s["psar"] = at({"a": ind["psar"]}, "a")
    s["keltner_up"] = at(ind["keltner"], "upper")
    s["keltner_low"] = at(ind["keltner"], "lower")
    s["donchian_up"] = at(ind["donchian"], "upper")
    s["donchian_low"] = at(ind["donchian"], "lower")
    s["hma"] = at({"a": ind["hma"]}, "a")
    s["vwma"] = at({"a": ind["vwma"]}, "a")
    s["stoch_k"] = at(ind["stoch"], "k")
    s["stoch_d"] = at(ind["stoch"], "d")
    s["ao"] = at({"a": ind["ao"]}, "a")
    s["trix"] = at(ind["trix"], "trix")
    s["uo"] = at({"a": ind["uo"]}, "a")
    s["vi_plus"] = at(ind["vortex"], "vi_plus")
    s["vi_minus"] = at(ind["vortex"], "vi_minus")
    s["chaikin"] = at(ind["chaikin"], "osc")
    s["squeeze_mom"] = at(ind["squeeze"], "momentum")
    s["squeeze_on"] = int(ind["squeeze"]["on"][i])
    s["dpo"] = at({"a": ind["dpo"]}, "a")
    s["bop"] = at({"a": ind["bop"]}, "a")
    s["pivot"] = ind.get("pivot") or {}
    return s
