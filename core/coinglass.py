"""Coinglass 全网爆仓数据。

Coinglass 的清算页把首屏数据直接渲染进 `__NEXT_DATA__`，解析这份 JSON 即可，
不需要 API key，也不会触发他们的图表接口。

口径说明：这是 Coinglass 汇总的各交易所强平金额，和本站「多空损」模型
（由 OI × 杠杆分布估算）是两回事，不能混着看。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

from . import config as C

URL = "https://www.coinglass.com/LiquidationData"
TTL = 300
_cache: dict[str, object] = {"at": 0.0, "data": None}
_lock = threading.Lock()

_NEXT_RE = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _get(url: str, timeout: float = 25) -> str:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": C.PROXY, "https": C.PROXY}))
    req = urllib.request.Request(url, headers={
        "User-Agent": C.UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _parse(html: str) -> dict:
    match = _NEXT_RE.search(html)
    if not match:
        raise ValueError("页面结构变了：找不到 __NEXT_DATA__")
    payload = json.loads(match.group(1))
    snap = (payload.get("props", {}).get("pageProps") or {}).get("tableSnapshot") or {}
    rows = snap.get("list") or []
    if not rows:
        raise ValueError("Coinglass 没有返回爆仓列表")
    return {"list": rows, "total": snap.get("total") or {}, "at": time.time()}


def snapshot(force: bool = False) -> dict:
    """全市场快照（带缓存）。失败时返回上一次成功的结果。"""
    now = time.time()
    with _lock:
        fresh = _cache["data"] and now - float(_cache["at"]) < TTL
        if fresh and not force:
            return _cache["data"]  # type: ignore[return-value]
    try:
        data = _parse(_get(URL))
        with _lock:
            _cache["data"] = data
            _cache["at"] = data["at"]
        return data
    except Exception as exc:  # noqa: BLE001
        with _lock:
            cached = _cache["data"]
        if cached:
            return cached  # type: ignore[return-value]
        raise


def _window(row: dict, hours: int) -> dict:
    long_usd = float(row.get(f"h{hours}LongLiquidationUsd") or 0)
    short_usd = float(row.get(f"h{hours}ShortLiquidationUsd") or 0)
    long_n = int(row.get(f"longLiquidationNumber{hours}h") or 0)
    short_n = int(row.get(f"shortLiquidationNumber{hours}h") or 0)
    total = long_usd + short_usd
    return {
        "long_usd": round(long_usd, 2),
        "short_usd": round(short_usd, 2),
        "long_n": long_n,
        "short_n": short_n,
        "long_ratio": round(long_usd / total * 100, 1) if total else None,
    }


def for_symbol(symbol: str) -> dict:
    """单个标的的爆仓数据；不在榜单里就返回 ok=False。"""
    base = symbol.upper().split(":")[-1]
    try:
        snap = snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:120], "source": "Coinglass"}

    row = next((r for r in snap["list"] if str(r.get("symbol")).upper() == base), None)
    if not row:
        return {"ok": False, "error": f"Coinglass 榜单未收录 {base}", "source": "Coinglass",
                "covered": [r.get("symbol") for r in snap["list"]]}

    windows = {f"{h}h": _window(row, h) for h in (1, 4, 12, 24)}
    return {
        "ok": True,
        "symbol": base,
        "price": row.get("price"),
        "change_24h": row.get("h24PriceChangePercent"),
        "windows": windows,
        "ratio_24h": windows["24h"]["long_ratio"],
        "total_24h_usd": round(windows["24h"]["long_usd"] + windows["24h"]["short_usd"], 2),
        "market_total": snap.get("total") or {},
        "at": int(float(snap.get("at") or time.time())),
        "source": "Coinglass（全网合计）",
    }
