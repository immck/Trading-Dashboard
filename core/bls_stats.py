"""取 BLS 三个核心指标的月度实际值，用于在事件标签上显示「公布的数据」。

BLS 官网/API 对服务器直连返回 403，这里经文本代理 r.jina.ai 取官方 JSON。
序列：
  CUUR0000SA0   CPI-U 全项目指数（NSA）
  WPUFD4        PPI 最终需求指数（NSA）
  CES0000000001 非农就业总人数（SA，千人）
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

from . import config as C

SERIES = {
    "cpi": ("CUUR0000SA0", "index"),
    "ppi": ("WPUFD4", "index"),
    "payroll": ("CES0000000001", "level"),
}

_cache: dict[str, object] = {"at": 0.0, "map": {}}
_lock = threading.Lock()
_building = False
TTL = 12 * 3600


def _get(url: str, timeout: float = 30) -> str:
    proxied = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": C.PROXY, "https": C.PROXY}))
    req = urllib.request.Request(url, headers={
        "User-Agent": C.UA, "Accept": "text/plain, application/json, */*"})
    with proxied.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _observations(series_id: str, start: int, end: int) -> list[dict]:
    url = (f"https://r.jina.ai/https://api.bls.gov/publicAPI/v2/timeseries/data/"
           f"{series_id}?startyear={start}&endyear={end}")
    raw = _get(url)
    start_at = raw.find("{")
    if start_at < 0:
        raise ValueError("no json in response")
    data = json.loads(raw[start_at:])
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS status {data.get('status')}")
    series = (data.get("Results") or {}).get("series") or []
    if not series:
        raise ValueError("empty series")
    return series[0].get("data") or []


def _build() -> dict:
    """返回 {(type, 'YYYY-MM'): 显示用数值字符串}"""
    out: dict[tuple[str, str], str] = {}
    now_year = time.gmtime().tm_year
    # BLS 单次查询最多 10 年；再单独补一段最近两年，保证最新月份一定拿到
    spans = [(2008, 2016), (2017, 2024), (max(2025, now_year - 1), now_year + 1)]
    for etype, (series_id, kind) in SERIES.items():
        points: dict[str, float] = {}
        for start, end in spans:
            try:
                for row in _observations(series_id, start, end):
                    period = row.get("period") or ""
                    if not period.startswith("M") or period == "M13":
                        continue
                    try:
                        key = f"{int(row['year']):04d}-{int(period[1:]):02d}"
                        points[key] = float(row["value"])
                    except (KeyError, TypeError, ValueError):
                        continue
            except Exception:  # noqa: BLE001
                continue
        keys = sorted(points)
        for i, key in enumerate(keys):
            value = points[key]
            if kind == "index":
                prev = f"{int(key[:4]) - 1:04d}-{key[5:7]}"
                if prev not in points or not points[prev]:
                    continue
                out[(etype, key)] = f"同比 {(value / points[prev] - 1) * 100:+.1f}%"
            else:
                if i == 0:
                    continue
                delta = value - points[keys[i - 1]]     # 单位：千人
                out[(etype, key)] = f"{delta / 10:+.1f} 万人"
    return out


def values() -> dict:
    """永不阻塞：缓存过期就起后台线程重建，本次先返回旧值。"""
    with _lock:
        fresh = _cache["map"] and time.time() - float(_cache["at"]) < TTL
        if fresh:
            return _cache["map"]  # type: ignore[return-value]
        if _building:
            return _cache["map"] or {}  # type: ignore[return-value]
    _warm()
    with _lock:
        return _cache["map"] or {}  # type: ignore[return-value]


def _warm() -> None:
    global _building
    with _lock:
        if _building:
            return
        _building = True

    def run():
        global _building
        try:
            built = _build()
            if built:
                with _lock:
                    _cache["map"] = built
                    _cache["at"] = time.time()
        finally:
            with _lock:
                _building = False

    threading.Thread(target=run, name="bls-stats", daemon=True).start()


def ref_month(date: str) -> str:
    """发布日 -> 所公布数据的参考月份（前一个月）。"""
    year, month = int(date[:4]), int(date[5:7])
    month -= 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def decorate(records: list[dict]) -> list[dict]:
    """给日历记录补上 value 字段（没有数据就留空）。"""
    table = values()
    if not table:
        return records
    for row in records:
        etype = row.get("type")
        date = row.get("date") or ""
        if etype in SERIES and len(date) >= 7:
            row["value"] = table.get((etype, ref_month(date)), "")
    return records
