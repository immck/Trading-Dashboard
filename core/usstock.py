"""美股数据源：K 线走 Yahoo Finance，实时报价走新浪（快）并回退 Yahoo。

对外暴露与 feed.klines 相同的返回结构，因此指标、形态、多周期研判、回测
可以原样复用，不需要为美股单独写一套分析逻辑。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import numpy as np

from . import config as C, feed

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
SINA = "https://hq.sinajs.cn/list="
FACEBOOK_UA = C.UA

# 周期 -> (Yahoo interval, range, 合成步长秒数；0 表示直接用原始周期)
# Yahoo 的分钟级历史有限：1m/5m 最多 7 天，15m/30m 60 天，60m 730 天
TF_MAP = {
    "1m": ("1m", "5d", 0),
    "3m": ("1m", "7d", 180),        # Yahoo 无 3m，用 1m 合成
    "5m": ("5m", "1mo", 0),
    "15m": ("15m", "1mo", 0),
    "30m": ("30m", "1mo", 0),
    "1h": ("60m", "3mo", 0),
    "2h": ("60m", "6mo", 7200),
    "4h": ("60m", "6mo", 14400),
    "6h": ("60m", "1y", 21600),
    "8h": ("60m", "1y", 28800),
    "12h": ("60m", "730d", 43200),
    "1d": ("1d", "5y", 0),
    "3d": ("1d", "10y", 259200),
    "1w": ("1wk", "10y", 0),
    "1M": ("1mo", "max", 0),
}

_cache: dict[tuple, tuple[float, dict]] = {}
_quote_cache: dict[str, tuple[float, dict]] = {}


def _r(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, 6)


def _get(url: str, headers: dict | None = None, timeout: float = 15):
    req = urllib.request.Request(url, headers=dict({"User-Agent": FACEBOOK_UA}, **(headers or {})))
    with feed.opener().open(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- K 线
def _yahoo_bars(symbol: str, interval: str, rng: str) -> dict:
    url = (f"{YAHOO}/{urllib.parse.quote(symbol)}"
           f"?interval={interval}&range={rng}&includePrePost=false&events=div%2Csplit")
    raw = _get(url)
    data = json.loads(raw)
    res = (data.get("chart") or {}).get("result")
    if not res:
        err = (data.get("chart") or {}).get("error")
        raise RuntimeError(f"Yahoo 无数据: {err or symbol}")
    r0 = res[0]
    ts = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []

    t_out, o_out, h_out, l_out, c_out, v_out = [], [], [], [], [], []
    for i, t in enumerate(ts):
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        cl = closes[i] if i < len(closes) else None
        if None in (o, h, lo, cl):
            continue                      # Yahoo 会给停牌/无成交的 null
        # Yahoo 的 timestamp 是「秒」，这里统一成毫秒，与加密数据源保持一致
        t_out.append(int(t) * 1000)
        o_out.append(float(o)); h_out.append(float(h))
        l_out.append(float(lo)); c_out.append(float(cl))
        v_out.append(float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0)

    if not t_out:
        raise RuntimeError(f"{symbol} 返回空 K 线")
    return {
        "ts": np.array(t_out, dtype=np.int64),
        "open": np.array(o_out), "high": np.array(h_out),
        "low": np.array(l_out), "close": np.array(c_out),
        "volume": np.array(v_out),
        "meta": r0.get("meta") or {},
    }


def _resample(bars: dict, step_sec: int) -> dict:
    """按固定步长分桶合成（4h/3d 等都靠它），时间戳对齐到步长整数倍。"""
    ts = bars["ts"]
    if len(ts) == 0:
        return bars
    step = step_sec * 1000
    buckets = (ts // step)
    out_ts, o, h, l, c, v = [], [], [], [], [], []
    cur = buckets[0]
    idx = []
    for i, b in enumerate(buckets):
        if b != cur:
            _flush(out_ts, o, h, l, c, v, bars, idx, ts)
            idx = []
            cur = b
        idx.append(i)
    _flush(out_ts, o, h, l, c, v, bars, idx, ts)
    return {"ts": np.array(out_ts, dtype=np.int64),
            "open": np.array(o), "high": np.array(h), "low": np.array(l),
            "close": np.array(c), "volume": np.array(v), "meta": bars.get("meta", {})}


def _flush(out_ts, o, h, l, c, v, bars, idx, ts):
    if not idx:
        return
    out_ts.append(int(ts[idx[0]]))
    o.append(float(bars["open"][idx[0]]))
    h.append(float(np.max(bars["high"][idx])))
    l.append(float(np.min(bars["low"][idx])))
    c.append(float(bars["close"][idx[-1]]))
    v.append(float(np.sum(bars["volume"][idx])))


def klines(symbol: str, tf: str, force: bool = False, want: int | None = None) -> dict:
    sym = symbol.upper().strip()
    if tf not in TF_MAP:
        raise ValueError(f"美股不支持周期: {tf}")
    key = (sym, tf)
    now = time.time()
    ttl = 60 if C.TIMEFRAMES.get(tf, 86400) <= 14400 else 300
    hit = _cache.get(key)
    if hit and not force and now - hit[0] < ttl:
        return _slice(hit[1], want or C.BARS)

    interval, rng, step = TF_MAP[tf]
    bars = _yahoo_bars(sym, interval, rng)
    if step:
        bars = _resample(bars, step)
    bars["stale"] = False
    bars["source"] = "yahoo"
    _cache[key] = (now, bars)
    return _slice(bars, want or C.BARS)


def _slice(bars: dict, n: int) -> dict:
    if n >= len(bars["ts"]):
        return bars
    out = {k: bars[k][-n:] for k in ("ts", "open", "high", "low", "close", "volume")}
    out["stale"] = bars.get("stale", False)
    out["source"] = bars.get("source")
    out["meta"] = bars.get("meta", {})
    return out


# ---------------------------------------------------------------- 实时报价
def _sina_quote(sym: str) -> dict | None:
    """新浪实时报价：gb_aapl / int_dji 等。直连即可，速度最快。"""
    code = sym.lower()
    if code.startswith("^"):
        mapping = {"^gspc": "int_sp500", "^ixic": "int_nasdaq", "^dji": "int_dji"}
        code = mapping.get(code, "")
        if not code:
            return None
    else:
        code = "gb_" + code.replace(".", "$")
    url = SINA + code
    try:
        raw = _get(url, {"Referer": "https://finance.sina.com.cn"}, timeout=6)
    except Exception:  # noqa: BLE001
        return None
    txt = raw.decode("gbk", "replace")
    if '"' not in txt:
        return None
    body = txt.split('"', 2)[1] if txt.count('"') >= 2 else ""
    f = body.split(",")
    if len(f) < 8:
        return None
    try:
        return {
            "name": f[0],
            "last": _r(f[1]),
            "change_pct": _r(f[2]),
            "time": f[3],
            "open": _r(f[5]),
            "high": _r(f[6]),
            "low": _r(f[7]),
            "source": "sina",
        }
    except (IndexError, ValueError):
        return None


def quote(sym: str) -> dict:
    sym = sym.upper().strip()
    now = time.time()
    hit = _quote_cache.get(sym)
    if hit and now - hit[0] < 10:
        return hit[1]

    out = _sina_quote(sym) or {}
    if not out.get("last"):
        # 回退：用 Yahoo 日线自己算涨跌（比 meta 的 chartPreviousClose 可靠）
        try:
            b = _yahoo_bars(sym, "1d", "1mo")
            c = [float(x) for x in b["close"] if x is not None]
            h = [float(x) for x in b["high"] if x is not None]
            lo = [float(x) for x in b["low"] if x is not None]
            m = b.get("meta") or {}
            last = c[-1] if c else None
            prev = c[-2] if len(c) >= 2 else (m.get("previousClose") or m.get("chartPreviousClose"))
            out = {
                "name": m.get("shortName") or sym,
                "last": _r(last),
                "change_pct": _r((last / prev - 1) * 100) if (last and prev) else None,
                "high": _r(h[-1] if h else m.get("regularMarketDayHigh")),
                "low": _r(lo[-1] if lo else m.get("regularMarketDayLow")),
                "source": "yahoo",
            }
        except Exception:  # noqa: BLE001
            out = {"name": sym, "last": None, "source": "none"}

    if out.get("last"):
        _quote_cache[sym] = (now, out)
    return out


_INDEX_CODE = {"^GSPC": "int_sp500", "^IXIC": "int_nasdaq", "^DJI": "int_dji"}


def _sina_code(sym: str) -> str | None:
    s = sym.upper()
    if s.startswith("^"):
        return _INDEX_CODE.get(s)
    return "gb_" + s.lower().replace(".", "$")


def quotes_batch(syms: list[str]) -> dict[str, dict]:
    """一次请求拿多只报价（新浪支持批量），比逐个拉快一个数量级。"""
    pairs = []
    for s in syms:
        c = _sina_code(s)
        if c:
            pairs.append((s.upper(), c))
    if not pairs:
        return {}
    url = SINA + ",".join(c for _, c in pairs)
    try:
        try:
            raw = feed.direct_opener().open(
                urllib.request.Request(url, headers={
                    "User-Agent": FACEBOOK_UA, "Referer": "https://finance.sina.com.cn"}),
                timeout=8).read()
        except Exception:  # noqa: BLE001
            raw = _get(url, {"Referer": "https://finance.sina.com.cn"}, timeout=10)
    except Exception:  # noqa: BLE001
        return {}

    txt = raw.decode("gbk", "replace")
    by_code = {c: s for s, c in pairs}
    out: dict[str, dict] = {}
    for line in txt.splitlines():
        line = line.strip()
        if "hq_str_" not in line or '"' not in line:
            continue
        try:
            head, body = line.split("=", 1)
            code = head.split("hq_str_")[1].strip()
            vals = body.strip().strip(";").strip('"').split(",")
            sym = by_code.get(code)
            if not sym or len(vals) < 8:
                continue
            out[sym] = {
                "name": vals[0],
                "last": _r(vals[1]),
                "change_pct": _r(vals[2]),
                "time": vals[3],
                "open": _r(vals[5]),
                "high": _r(vals[6]),
                "low": _r(vals[7]),
                "source": "sina",
            }
        except (ValueError, IndexError):
            continue
    return out


def ticker(symbol: str, light: bool = False) -> dict:
    q = quote(symbol)
    return {
        "last": q.get("last"),
        "change_pct": q.get("change_pct"),
        "high": q.get("high"),
        "low": q.get("low"),
        "volume": None,
        "source": q.get("source"),
        "name": q.get("name"),
    }
