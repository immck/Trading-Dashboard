"""市场聚合层：为一屏看板一次性汇总行情、盘口、量能、持仓、订单流。

全部基于 Binance 公共接口 + 若干交易所的公开行情；拿不到的字段留空，
前端按空值降级显示，不编造数据。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import config as C, feed

# 参与「全网量能」的交易所：key -> 显示名
VENUES = {
    "binance": "Binance",
    "bybit": "Bybit",
    "okx": "OKX",
    "bitget": "Bitget",
    "gate": "Gate",
}

_cache: dict[tuple, tuple[float, dict]] = {}
TTL = 8


def _r(x, nd=8):
    """数值清洗。价格类字段默认保留 8 位小数，避免小市值币（如 0.004994）被抹成 0。"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, nd)


def _get(url, timeout=6):
    try:
        return feed.get_json(url, retries=1, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 分所行情
def _binance(pair: str) -> dict:
    out = {"key": "binance", "name": "Binance", "market": "futures"}
    t = _get(f"{C.BINANCE_FUT}/fapi/v1/ticker/24hr?symbol={pair}")
    if t:
        out.update(last=_r(t.get("lastPrice")), change=_r(t.get("priceChangePercent"), 3),
                   high=_r(t.get("highPrice")), low=_r(t.get("lowPrice")),
                   vol_usd=_r(t.get("quoteVolume"), 2))
    pi = _get(f"{C.BINANCE_FUT}/fapi/v1/premiumIndex?symbol={pair}")
    if pi:
        out.update(funding=_r(pi.get("lastFundingRate"), 8), mark=_r(pi.get("markPrice")),
                   next_funding=int(pi.get("nextFundingTime") or 0))
    oi = _get(f"{C.BINANCE_FUT}/fapi/v1/openInterest?symbol={pair}")
    if oi and out.get("last"):
        out["oi"] = _r(float(oi.get("openInterest", 0)), 2)
        out["oi_usd"] = _r(out["oi"] * out["last"], 2)
    return out


def _bybit(base: str) -> dict:
    out = {"key": "bybit", "name": "Bybit", "market": "futures"}
    d = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={base}USDT")
    if d and d.get("result", {}).get("list"):
        r = d["result"]["list"][0]
        out.update(last=_r(r.get("lastPrice")), change=_r(r.get("price24hPcnt"), 6),
                   high=_r(r.get("highPrice24h")), low=_r(r.get("lowPrice24h")),
                   vol_usd=_r(r.get("turnover24h"), 2), funding=_r(r.get("fundingRate"), 8),
                   oi=_r(r.get("openInterest"), 2))
        if out.get("last") and out.get("oi"):
            out["oi_usd"] = _r(out["oi"] * out["last"], 2)
        out["change"] = _r((out.get("change") or 0) * 100, 3)
    return out


def _okx(base: str) -> dict:
    out = {"key": "okx", "name": "OKX", "market": "futures"}
    d = _get(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT-SWAP")
    if d and d.get("data"):
        r = d["data"][0]
        last = _r(r.get("last"))
        # OKX 的 volCcy24h 对永续是「币本位数量」，要乘价格才是 USD
        vol_ccy = _r(r.get("volCcy24h"), 4)
        out.update(last=last, high=_r(r.get("high24h")), low=_r(r.get("low24h")),
                   vol_usd=_r(vol_ccy * last, 2) if (vol_ccy and last) else None)
        if r.get("open24h"):
            try:
                out["change"] = _r((float(r["last"]) / float(r["open24h"]) - 1) * 100, 3)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return out


def _bitget(base: str) -> dict:
    out = {"key": "bitget", "name": "Bitget", "market": "futures"}
    d = _get(f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={base}USDT&productType=usdt-futures")
    if d and d.get("data"):
        r = d["data"][0] if isinstance(d["data"], list) else d["data"]
        out.update(last=_r(r.get("lastPr")), high=_r(r.get("high24h")), low=_r(r.get("low24h")),
                   vol_usd=_r(r.get("usdtVolume") or r.get("quoteVolume"), 2))
        if r.get("open24h"):
            try:
                out["change"] = _r((float(r["lastPr"]) / float(r["open24h"]) - 1) * 100, 3)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return out


def _gate(base: str) -> dict:
    out = {"key": "gate", "name": "Gate", "market": "futures"}
    d = _get(f"https://api.gateio.ws/api/v4/futures/usdt/tickers?contract={base}_USDT")
    if isinstance(d, list) and d:
        r = d[0]
        out.update(last=_r(r.get("last")), change=_r(r.get("change_percentage"), 3),
                   high=_r(r.get("high_24h")), low=_r(r.get("low_24h")),
                   vol_usd=_r(r.get("volume_24h_quote") or r.get("volume_24h_settle"), 2),
                   funding=_r(r.get("funding_rate"), 8))
    return out


def venues(base: str) -> list[dict]:
    pair = f"{base}{C.QUOTE}"
    jobs = {
        "binance": lambda: _binance(pair),
        "bybit": lambda: _bybit(base),
        "okx": lambda: _okx(base),
        "bitget": lambda: _bitget(base),
        "gate": lambda: _gate(base),
    }
    out = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futs = {k: pool.submit(f) for k, f in jobs.items()}
        for k, f in futs.items():
            try:
                d = f.result()
                if d.get("last"):
                    out.append(d)
            except Exception:  # noqa: BLE001
                pass
    out.sort(key=lambda v: -(v.get("vol_usd") or 0))
    return out


# ---------------------------------------------------------------- 持仓/情绪
def _binance_data(path: str, pair: str) -> dict | None:
    d = _get(f"{C.BINANCE_FUT}/futures/data/{path}?symbol={pair}&period=1h&limit=2")
    if isinstance(d, list) and d:
        return d[-1]
    return None


def positioning(base: str) -> dict:
    pair = f"{base}{C.QUOTE}"
    out: dict = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {
            "top_pos": pool.submit(_binance_data, "topLongShortPositionRatio", pair),
            "top_acct": pool.submit(_binance_data, "topLongShortAccountRatio", pair),
            "global": pool.submit(_binance_data, "globalLongShortAccountRatio", pair),
            "taker": pool.submit(_binance_data, "takerlongshortRatio", pair),
        }
        res = {}
        for k, f in futs.items():
            try:
                res[k] = f.result()
            except Exception:  # noqa: BLE001
                res[k] = None
    if res.get("top_pos"):
        out["top_position_ratio"] = _r(res["top_pos"].get("longShortRatio"), 4)
        out["top_position_long_pct"] = _r(float(res["top_pos"].get("longAccount", 0)) * 100, 2)
    if res.get("top_acct"):
        out["top_account_ratio"] = _r(res["top_acct"].get("longShortRatio"), 4)
        out["top_account_long_pct"] = _r(float(res["top_acct"].get("longAccount", 0)) * 100, 2)
    if res.get("global"):
        out["global_ratio"] = _r(res["global"].get("longShortRatio"), 4)
        out["global_long_pct"] = _r(float(res["global"].get("longAccount", 0)) * 100, 2)
    if res.get("taker"):
        out["taker_ratio"] = _r(res["taker"].get("buySellRatio"), 4)
    return out


# ---------------------------------------------------------------- 盘口
def _depth(pair: str, limit: int = 1000) -> dict:
    d = _get(f"{C.BINANCE_FUT}/fapi/v1/depth?symbol={pair}&limit={limit}")
    if not d:
        return {"bids": [], "asks": []}
    bids = [(float(p), float(q)) for p, q in d.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in d.get("asks", [])]
    return {"bids": bids, "asks": asks}


def _walls(depth: dict, price: float, step: float, top: int = 6) -> list[dict]:
    """按价格分档聚合挂单额，取最大的几档。"""
    step = max(step, 1e-9)
    buckets: dict[float, dict] = {}
    for side, rows in (("bid", depth["bids"]), ("ask", depth["asks"])):
        for p, q in rows:
            k = round(p / step) * step
            b = buckets.setdefault(k, {"price": k, "bid": 0.0, "ask": 0.0})
            b[side] += p * q
    out = []
    for b in buckets.values():
        total = b["bid"] + b["ask"]
        if total <= 0:
            continue
        out.append({
            "price": _r(b["price"]),
            "total": _r(total, 2),
            "side": "bid" if b["bid"] >= b["ask"] else "ask",
            "dist_pct": _r((b["price"] / price - 1) * 100, 3) if price else None,
        })
    out.sort(key=lambda x: -x["total"])
    return out[:top]


def walls(base: str, price: float) -> dict:
    """挂单墙分档。

    深度接口最多 1000 档，价格跨度因币种/时段而变（BTC 常见 ±0.05%），
    所以分档步长按「实际深度跨度」自适应，否则远档永远落空。
    """
    pair = f"{base}{C.QUOTE}"
    d = _depth(pair, 1000)
    empty = {"near": [], "mid": [], "far": [], "bid_total": 0.0, "ask_total": 0.0,
             "span_pct": 0.0, "tiers": {}}
    if not d["bids"] or not d["asks"]:
        return empty

    bid_total = sum(p * q for p, q in d["bids"])
    ask_total = sum(p * q for p, q in d["asks"])
    lo = d["bids"][-1][0]
    hi = d["asks"][-1][0]
    span = max(hi - lo, price * 1e-6)

    tiers = {
        "near": span / 60.0,
        "mid": span / 25.0,
        "far": span / 10.0,
    }
    span_pct = (span / price) * 100 if price else 0
    return {
        "near": _walls(d, price, tiers["near"], 6),
        "mid": _walls(d, price, tiers["mid"], 6),
        "far": _walls(d, price, tiers["far"], 6),
        "bid_total": _r(bid_total, 2),
        "ask_total": _r(ask_total, 2),
        "span_pct": _r(span_pct, 3),
        "tiers": {k: _r((v / price) * 100, 3) if price else None for k, v in tiers.items()},
    }


def oi_history(pair: str, limit: int = 25) -> dict:
    """Binance 持仓量历史（USD 计），用于 OI 变化与迷你走势图。"""
    d = _get(f"{C.BINANCE_FUT}/futures/data/openInterestHist?symbol={pair}&period=1h&limit={limit}")
    if not isinstance(d, list) or not d:
        return {}
    series = [[int(r["timestamp"]) // 1000, _r(r.get("sumOpenInterestValue"), 2)] for r in d]
    vals = [s[1] for s in series if s[1]]
    out = {"series": series}
    if len(vals) >= 2:
        out["change_1h_pct"] = _r((vals[-1] / vals[-2] - 1) * 100, 3) if vals[-2] else None
        out["change_24h_pct"] = _r((vals[-1] / vals[0] - 1) * 100, 3) if vals[0] else None
        out["change_24h_usd"] = _r(vals[-1] - vals[0], 2)
    return out


def imbalance(depth: dict, price: float, band_pct: float = 0.5) -> dict:
    lo, hi = price * (1 - band_pct / 100), price * (1 + band_pct / 100)
    bid = sum(p * q for p, q in depth["bids"] if p >= lo)
    ask = sum(p * q for p, q in depth["asks"] if p <= hi)
    tot = bid + ask
    return {"bid_usd": _r(bid, 2), "ask_usd": _r(ask, 2),
            "ratio": _r(bid / tot, 4) if tot else None, "band_pct": band_pct}


# ---------------------------------------------------------------- 订单流
def orderflow(pair: str, limit: int = 48) -> dict:
    """用 K 线的 taker 买入量还原主动买卖与 CVD（无需逐笔）。"""
    rows = _get(f"{C.BINANCE_FUT}/fapi/v1/klines?symbol={pair}&interval=1h&limit={limit}")
    if not isinstance(rows, list) or not rows:
        return {}
    ts, cvd, series = [], [], []
    cum = 0.0
    buy_1h = sell_1h = 0.0
    for r in rows:
        vol = float(r[5])
        taker_buy = float(r[9]) if len(r) > 9 else vol / 2.0
        taker_sell = vol - taker_buy
        delta = (taker_buy - taker_sell) * float(r[4])
        cum += delta
        series.append([int(r[0]) // 1000, _r(cum, 2)])
        ts.append(r[0])
    last = rows[-1]
    vol = float(last[5])
    taker_buy = float(last[9]) if len(last) > 9 else vol / 2.0
    buy_1h = taker_buy * float(last[4])
    sell_1h = (vol - taker_buy) * float(last[4])
    return {"cvd": _r(cum, 2), "buy_1h": _r(buy_1h, 2), "sell_1h": _r(sell_1h, 2),
            "series": series}


# ---------------------------------------------------------------- 恐惧贪婪
def fear_greed() -> dict:
    d = _get("https://api.alternative.me/fng/?limit=1", timeout=6)
    if d and isinstance(d.get("data"), list) and d["data"]:
        r = d["data"][0]
        return {"value": int(r.get("value", 0)), "label": r.get("value_classification", "")}
    return {}


# ---------------------------------------------------------------- 汇总
def snapshot(symbol: str) -> dict:
    base = feed.base_symbol(feed.norm_symbol(symbol))
    key = (base,)
    import time
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < TTL:
        return hit[1]

    pair = f"{base}{C.QUOTE}"
    # 并行取各块
    with ThreadPoolExecutor(max_workers=7) as pool:
        f_venues = pool.submit(venues, base)
        f_pos = pool.submit(positioning, base)
        f_depth = pool.submit(_depth, pair)
        f_flow = pool.submit(orderflow, pair)
        f_fg = pool.submit(fear_greed)
        f_kl = pool.submit(_get, f"{C.BINANCE_FUT}/fapi/v1/klines?symbol={pair}&interval=1h&limit=30")
        f_oi = pool.submit(oi_history, pair)

        try:
            vs = f_venues.result()
        except Exception:  # noqa: BLE001
            vs = []
        try:
            pos = f_pos.result()
        except Exception:  # noqa: BLE001
            pos = {}
        try:
            depth = f_depth.result()
        except Exception:  # noqa: BLE001
            depth = {"bids": [], "asks": []}
        try:
            flow = f_flow.result()
        except Exception:  # noqa: BLE001
            flow = {}
        try:
            fg = f_fg.result()
        except Exception:  # noqa: BLE001
            fg = {}
        try:
            kl = f_kl.result() or []
        except Exception:  # noqa: BLE001
            kl = []
        try:
            oi_h = f_oi.result()
        except Exception:  # noqa: BLE001
            oi_h = {}

    bnc = next((v for v in vs if v["key"] == "binance"), {})
    price = bnc.get("last") or (float(kl[-1][4]) if kl else None)
    if not price:
        raise RuntimeError("拿不到价格")

    best_bid = depth["bids"][0][0] if depth["bids"] else None
    best_ask = depth["asks"][0][0] if depth["asks"] else None
    spread_pct = None
    if best_bid and best_ask:
        spread_pct = (best_ask - best_bid) / ((best_ask + best_bid) / 2) * 100

    prices = [v["last"] for v in vs if v.get("last")]
    xex = None
    if len(prices) >= 2:
        xex = {"pct": _r((max(prices) - min(prices)) / (sum(prices) / len(prices)) * 100, 4),
               "max": _r(max(prices)), "min": _r(min(prices)), "count": len(prices)}

    # 量比：当前 1h 成交量 / 过去 24 根均量
    vol_ratio = None
    if len(kl) >= 25:
        vols = [float(r[5]) for r in kl[-25:-1]]
        avg = sum(vols) / len(vols)
        if avg > 0:
            vol_ratio = _r(float(kl[-1][5]) / avg, 3)

    tot_bid = sum(p * q for p, q in depth["bids"])
    tot_ask = sum(p * q for p, q in depth["asks"])
    vol_summary = {
        "bid": _r(tot_bid, 2), "ask": _r(tot_ask, 2),
        "total": _r(tot_bid + tot_ask, 2),
        "venue_rows": [{
            "key": v["key"], "name": v["name"],
            "last": v.get("last"),
            "change": v.get("change"),
            "vol_usd": v.get("vol_usd"),
            "oi_usd": v.get("oi_usd"),
            "funding": v.get("funding"),
        } for v in vs],
    }

    out = {
        "symbol": base,
        "price": _r(price),
        "change_pct": bnc.get("change"),
        "high": bnc.get("high"),
        "low": bnc.get("low"),
        "mark": bnc.get("mark"),
        "oi": bnc.get("oi"),
        "oi_usd": bnc.get("oi_usd"),
        "oi_change_pct": oi_h.get("change_24h_pct"),
        "oi_change_1h_pct": oi_h.get("change_1h_pct"),
        "oi_change_usd": oi_h.get("change_24h_usd"),
        "oi_series": oi_h.get("series", []),
        "funding": bnc.get("funding"),
        "next_funding": bnc.get("next_funding"),
        "vol_usd": bnc.get("vol_usd"),
        "vol_ratio": vol_ratio,
        "spread_pct": _r(spread_pct, 4),
        "xex": xex,
        "imbalance": imbalance(depth, price),
        "walls": walls(base, price),
        "orderflow": flow,
        "positioning": pos,
        "fear_greed": fg,
        "venues": vs,
        "volume_summary": vol_summary,
        "ts": int(now * 1000),
    }
    _cache[key] = (now, out)
    return out
