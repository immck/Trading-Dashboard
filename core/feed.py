"""行情抓取层：代理自适应、重试、内存缓存、落盘快照。

对外只暴露 klines(symbol, tf) / ticker(symbol)，返回 numpy 数组。
"""
from __future__ import annotations

import gzip
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request

import numpy as np

from . import config as C

_lock = threading.Lock()
_cache: dict[tuple[str, str], dict] = {}
_disk_written: dict[tuple[str, str], float] = {}

# 传输通道在首次使用时决定，之后固定
_transport: dict | None = None
_transport_lock = threading.Lock()


# ---------------------------------------------------------------- 传输
def _build_opener(proxy: str | None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _fetch_raw(opener, url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": C.UA,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return raw


def _route():
    """决定走直连还是代理。探测一次后固定。"""
    global _transport
    if _transport is not None:
        return _transport
    with _transport_lock:
        if _transport is not None:
            return _transport
        probe = f"{C.BINANCE_FUT}/fapi/v1/ping"
        order = [None] + ([C.PROXY] if C.PROXY else [])
        for proxy in order:
            name = proxy or "direct"
            try:
                _fetch_raw(_build_opener(proxy), probe, C.DIRECT_PROBE_TIMEOUT)
                _transport = {"proxy": proxy, "opener": _build_opener(proxy),
                              "name": name, "ok_at": time.time()}
                print(f"[feed] 通道已选定: {name}", flush=True)
                return _transport
            except Exception as e:  # noqa: BLE001
                print(f"[feed] 通道 {name} 不可用: {type(e).__name__}", flush=True)
        _transport = {"proxy": None, "opener": _build_opener(None),
                      "name": "direct", "ok_at": time.time()}
        return _transport


_last_reset = 0.0


def reset_transport():
    """重置通道。每秒最多重探一次 —— 断网时多个并发请求会同时失败，
    每个都触发一次探测的话，一次快照要多等十几秒。"""
    global _transport, _last_reset
    now = time.time()
    with _transport_lock:
        if now - _last_reset < 1.0:
            return
        _last_reset = now
        _transport = None


def opener():
    """对外暴露当前通道的 opener，供外部 API（如 LLM）复用同一代理。"""
    return _route()["opener"]


_direct_opener = None


def direct_opener():
    """不走代理的 opener。国内站点（金十、新浪）走直连更快更稳。"""
    global _direct_opener
    if _direct_opener is None:
        _direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _direct_opener


def transport_name() -> str:
    return (_route() or {}).get("name", "未探测")


_fail_count = 0
_down_until = 0.0


def transport_down() -> bool:
    return time.time() < _down_until


def get_json(url: str, retries: int = 2, timeout: float | None = None):
    """取 JSON。连续失败后进入短暂熔断，避免代理挂掉时每个请求都干等十几秒。"""
    global _fail_count, _down_until
    if transport_down():
        raise RuntimeError("数据通道不可用（代理无响应），稍后自动重试")
    timeout = timeout or C.REQUEST_TIMEOUT
    last = None
    for attempt in range(retries + 1):
        t = _route()
        try:
            data = json.loads(_fetch_raw(t["opener"], url, timeout).decode("utf-8"))
            _fail_count = 0
            _down_until = 0.0
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            # 通道可能失效了，重置后重试一次
            if attempt == 0:
                reset_transport()
            time.sleep(0.35 * (attempt + 1))
    _fail_count += 1
    if _fail_count >= 2:
        # 连续 2 次全失败就熔断 30 秒：断网时页面秒回错误，网络恢复后最多 30 秒自动接上
        _down_until = time.time() + 30
        _fail_count = 0
    raise RuntimeError(f"请求失败 {url}: {last}")


# ---------------------------------------------------------------- 符号
_US_SET = {s.upper() for s in C.US_SYMBOLS}


def market_of(symbol: str) -> str:
    """判断标的属于哪个市场：'us' 或 'crypto'。

    带 US: 前缀强制按美股；否则命中美股列表/指数符号的按美股。
    """
    s = (symbol or "").strip().upper()
    if s.startswith(C.US_PREFIX):
        return "us"
    if s.startswith("^"):
        return "us"
    if s in _US_SET:
        return "us"
    return "crypto"


def strip_us(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s.startswith(C.US_PREFIX):
        s = s[len(C.US_PREFIX):]
    return s


def norm_symbol(sym: str) -> str:
    if market_of(sym) == "us":
        return strip_us(sym)
    s = (sym or "").strip().upper()
    if not s:
        raise ValueError("symbol 为空")
    if s.endswith(C.QUOTE):
        return s
    if "/" in s:
        s = s.replace("/", "")
    return s + C.QUOTE


def base_symbol(pair: str) -> str:
    if market_of(pair) == "us":
        return strip_us(pair)
    return pair[:-len(C.QUOTE)] if pair.endswith(C.QUOTE) else pair


def _kv(symbol: str) -> dict:
    # Binance 合约最多 1500 根，现货最多 1000 根
    limit = C.MAX_BARS if C.SOURCE == "futures" else min(C.MAX_BARS, 1000)
    url = (f"{C.BINANCE_FUT}/fapi/v1/klines?symbol={symbol}&interval={{tf}}&limit={limit}"
           if C.SOURCE == "futures"
           else f"{C.BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval={{tf}}&limit={limit}")
    return {"url": url, "limit": limit}


# ---------------------------------------------------------------- K 线
def _parse_klines(rows) -> dict:
    n = len(rows)
    ts = np.empty(n, dtype=np.int64)
    o = np.empty(n); h = np.empty(n); l = np.empty(n); c = np.empty(n); v = np.empty(n)
    for i, r in enumerate(rows):
        ts[i] = int(r[0])
        o[i] = float(r[1]); h[i] = float(r[2]); l[i] = float(r[3])
        c[i] = float(r[4]); v[i] = float(r[5])
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _disk_path(symbol: str, tf: str) -> str:
    return os.path.join(C.DATA_DIR, f"{symbol}_{tf}.json")


def _save_disk(symbol: str, tf: str, bars: dict):
    if not C.DISK_CACHE:
        return
    key = (symbol, tf)
    now = time.time()
    if now - _disk_written.get(key, 0) < 60:
        return
    _disk_written[key] = now
    try:
        os.makedirs(C.DATA_DIR, exist_ok=True)
        payload = {
            "ts": bars["ts"].tolist(),
            "open": bars["open"].tolist(),
            "high": bars["high"].tolist(),
            "low": bars["low"].tolist(),
            "close": bars["close"].tolist(),
            "volume": bars["volume"].tolist(),
            "saved_at": int(now * 1000),
        }
        tmp = _disk_path(symbol, tf) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, _disk_path(symbol, tf))
    except Exception:  # noqa: BLE001
        pass


def _load_disk(symbol: str, tf: str):
    p = _disk_path(symbol, tf)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return {
            "ts": np.array(d["ts"], dtype=np.int64),
            "open": np.array(d["open"]), "high": np.array(d["high"]),
            "low": np.array(d["low"]), "close": np.array(d["close"]),
            "volume": np.array(d["volume"]),
            "stale": True,
        }
    except Exception:  # noqa: BLE001
        return None


def _slice_bars(bars: dict, n: int) -> dict:
    """取最近 n 根；不足则原样返回。"""
    if n is None or n >= len(bars["ts"]):
        return bars
    out = {k: bars[k][-n:] for k in ("ts", "open", "high", "low", "close", "volume")}
    out["stale"] = bars.get("stale", False)
    out["source"] = bars.get("source")
    return out


PAGE_CAP = 6000     # 单周期最多回溯多少根（约 4 次请求）


def _fetch_paged(url: str, need: int, limit: int):
    """向前翻页，凑够 need 根。用于回测需要长历史时。"""
    need = min(need, PAGE_CAP)
    rows: list = []
    end = None
    while len(rows) < need:
        u = url + (f"&endTime={end}" if end else "")
        batch = get_json(u)
        if not isinstance(batch, list) or not batch:
            break
        rows = batch + rows
        end = int(batch[0][0]) - 1
        if len(batch) < limit or end <= 0:
            break
    return rows[-need:]


def klines(symbol: str, tf: str, force: bool = False, want: int | None = None) -> dict:
    """取 K 线。带内存缓存 + 落盘兜底。

    want: 需要最近多少根（默认 config.BARS）。超过单次上限时自动向前翻页，
    缓存里永远保留拿到的最长序列，因此回测要几千根、看板要几百根可共用缓存。
    """
    if tf not in C.TIMEFRAMES:
        raise ValueError(f"不支持的周期: {tf}")
    if market_of(symbol) == "us":
        from . import usstock
        return usstock.klines(strip_us(symbol), tf, force=force, want=want)
    pair = norm_symbol(symbol)
    key = (pair, tf)
    need = min(int(want or C.BARS), PAGE_CAP)
    ttl = max(C.MIN_TTL, C.TIMEFRAMES[tf] // C.TTL_DIVISOR)
    now = time.time()

    with _lock:
        hit = _cache.get(key)

    # 缓存里已有足够长的数据就直接用
    if hit and not force and now - hit["at"] < ttl and len(hit["data"]["ts"]) >= need:
        return _slice_bars(hit["data"], need)

    kv = _kv(pair)
    url = kv["url"].format(tf=tf)
    try:
        rows = _fetch_paged(url, need, kv["limit"]) if need > kv["limit"] else get_json(url)
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("空 K 线")
        bars = _parse_klines(rows[-PAGE_CAP:])
        bars["stale"] = False
        bars["source"] = C.SOURCE
        with _lock:
            _cache[key] = {"at": now, "data": bars}
        _save_disk(pair, tf, bars)
        return _slice_bars(bars, need)
    except Exception as e:  # noqa: BLE001
        if hit:
            return _slice_bars(hit["data"], need)
        disk = _load_disk(pair, tf)
        if disk is not None:
            print(f"[feed] {pair} {tf} 用落盘快照: {e}", flush=True)
            return _slice_bars(disk, need)
        raise


# ---------------------------------------------------------------- 快照信息
def ticker(symbol: str, light: bool = False) -> dict:
    """24h 行情。light=True 时只拉 ticker/24hr（列表页用，省 2/3 请求）。"""
    if market_of(symbol) == "us":
        from . import usstock
        return usstock.ticker(strip_us(symbol), light=light)
    pair = norm_symbol(symbol)
    key = ("ticker_light" if light else "ticker", pair)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit["at"] < 15:
        return hit["data"]
    out = {}
    try:
        if C.SOURCE == "futures":
            d = get_json(f"{C.BINANCE_FUT}/fapi/v1/ticker/24hr?symbol={pair}")
            out = {
                "last": float(d["lastPrice"]),
                "change_pct": float(d["priceChangePercent"]),
                "high": float(d["highPrice"]),
                "low": float(d["lowPrice"]),
                "volume": float(d["quoteVolume"]),
            }
            if not light:
                try:
                    pi = get_json(f"{C.BINANCE_FUT}/fapi/v1/premiumIndex?symbol={pair}")
                    out["funding"] = float(pi.get("lastFundingRate") or 0)
                    out["mark"] = float(pi.get("markPrice") or 0)
                    out["next_funding"] = int(pi.get("nextFundingTime") or 0)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    oi = get_json(f"{C.BINANCE_FUT}/fapi/v1/openInterest?symbol={pair}")
                    out["oi"] = float(oi.get("openInterest") or 0)
                except Exception:  # noqa: BLE001
                    pass
        else:
            d = get_json(f"{C.BINANCE_SPOT}/api/v3/ticker/24hr?symbol={pair}")
            out = {
                "last": float(d["lastPrice"]),
                "change_pct": float(d["priceChangePercent"]),
                "high": float(d["highPrice"]),
                "low": float(d["lowPrice"]),
                "volume": float(d["quoteVolume"]),
            }
    except Exception:  # noqa: BLE001
        out = hit["data"] if hit else {}
    if out:
        with _lock:
            _cache[key] = {"at": now, "data": out}
    return out


def warmup(symbol: str = "BTC", tf: str = "1h"):
    """服务启动时预先定通道 + 预热默认标的，避免首个用户请求等 3~7 秒。"""
    t0 = time.time()
    try:
        _route()
        klines(symbol, tf)
        ticker(symbol, light=True)
        print(f"[feed] 预热完成 {time.time()-t0:.1f}s 通道={_route()['name']}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[feed] 预热失败（不影响使用）: {e}", flush=True)


# ---------------------------------------------------------------- 全量标的
_sym_cache = {"at": 0.0, "list": []}


def all_symbols() -> list[dict]:
    """Binance U 本位永续全部可交易标的，按 24h 成交额降序（最活跃的在前）。

    (缓存 30 分钟)
    """
    now = time.time()
    if _sym_cache["list"] and now - _sym_cache["at"] < 1800:
        return _sym_cache["list"]

    info = get_json(f"{C.BINANCE_FUT}/fapi/v1/exchangeInfo", retries=2, timeout=25)
    bases = {}
    for s in info.get("symbols", []):
        if s.get("quoteAsset") != C.QUOTE or s.get("status") != "TRADING":
            continue
        ctype = s.get("contractType")
        if ctype and ctype != "PERPETUAL":
            continue
        base = s.get("baseAsset")
        pair = s.get("symbol")
        if base and pair:
            bases[pair] = base

    # 用 24h 成交额排序，让「最有交易价值」的排前面
    vol = {}
    try:
        tk = get_json(f"{C.BINANCE_FUT}/fapi/v1/ticker/24hr", retries=1, timeout=25)
        for t in tk:
            vol[t.get("symbol")] = float(t.get("quoteVolume") or 0)
    except Exception:  # noqa: BLE001
        pass

    out = [{"symbol": b, "pair": p, "vol": round(vol.get(p, 0.0), 2)} for p, b in bases.items()]
    out.sort(key=lambda x: -x["vol"])
    _sym_cache.update(at=now, list=out)
    return out


def cache_info() -> dict:
    with _lock:
        return {
            "entries": len(_cache),
            "keys": [f"{k[0]}:{k[1]}" for k in list(_cache)[:40]],
            "transport": (_transport or {}).get("name", "未探测"),
        }
