"""事件时间轴：把历史事件映射到 K 线上。

数据源是一个可编辑的 JSON（data/events.json），因为历史事件没有可靠的免费 API，
与其编造不如让人工维护。文件不存在时会自动写一份默认种子。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

from . import config as C, feed

EVENTS_FILE = os.path.join(C.DATA_DIR, "events.json")
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}
TTL = 30

DEFAULT_TYPES = {
    "halving": {"label": "减半", "color": "#f0b90b"},
    "etf": {"label": "ETF/上市", "color": "#3fb950"},
    "upgrade": {"label": "技术升级", "color": "#c480ff"},
    "incident": {"label": "风险事件", "color": "#ff4976"},
    "macro": {"label": "宏观", "color": "#4da6ff"},
    "regulation": {"label": "监管", "color": "#ff9500"},
    "peak": {"label": "极值", "color": "#e6edf3"},
    "other": {"label": "其它", "color": "#8b949e"},
}

EVENT_CATEGORIES = {
    "liquidation": {"label": "爆仓事件", "color": "#ff4976"},
    "payroll": {"label": "非农", "color": "#4da6ff"},
    "cpi": {"label": "CPI", "color": "#c480ff"},
    "ppi": {"label": "PPI", "color": "#38c6b0"},
    "fed": {"label": "美联储利率", "color": "#f0883e"},
    "policy": {"label": "政策 / 宏观", "color": "#4da6ff"},
    "macro": {"label": "政策 / 宏观", "color": "#4da6ff"},
    "halving": {"label": "比特币减半", "color": "#c480ff"},
    "industry": {"label": "行业", "color": "#38c6b0"},
    "security": {"label": "安全事件", "color": "#ff4976"},
    "incident": {"label": "其他事件", "color": "#d5a33c"},
    "etf": {"label": "行业", "color": "#38c6b0"},
    "upgrade": {"label": "行业", "color": "#38c6b0"},
    "regulation": {"label": "政策 / 宏观", "color": "#4da6ff"},
    "peak": {"label": "其他事件", "color": "#d5a33c"},
    "other": {"label": "其他事件", "color": "#d5a33c"},
}

CATEGORY_BY_TYPE = {
    "macro": "policy", "regulation": "policy",
    "etf": "industry", "upgrade": "industry",
    "incident": "other", "peak": "other",
}


def _read() -> dict:
    if not os.path.exists(EVENTS_FILE):
        return {"types": dict(DEFAULT_TYPES), "events": []}
    with open(EVENTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("types", dict(DEFAULT_TYPES))
    # 补齐缺失的类型颜色
    for k, v in DEFAULT_TYPES.items():
        data["types"].setdefault(k, v)
    data.setdefault("events", [])
    return data


def _ts(date_str: str) -> int | None:
    """'YYYY-MM-DD' -> UTC 当天 00:00 的秒级时间戳。"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def for_symbol(symbol: str) -> dict:
    """返回该标的可见的事件（scope 匹配 all 或该标的）。"""
    sym = feed.base_symbol(feed.norm_symbol(symbol)).upper()
    now = time.time()
    hit = _cache.get(sym)
    if hit and now - hit[0] < TTL:
        return hit[1]

    with _lock:
        data = _read()

    out = []
    for ev in data.get("events", []):
        scope = (ev.get("scope") or "all").upper()
        if scope not in ("ALL", sym):
            continue
        ts = _ts(ev.get("date", ""))
        if ts is None:
            continue
        etype = ev.get("type") or "other"
        meta = data["types"].get(etype, DEFAULT_TYPES["other"])
        category = CATEGORY_BY_TYPE.get(etype, etype)
        if category not in EVENT_CATEGORIES:
            category = "other"
        out.append({
            "time": ts,
            "date": ev.get("date"),
            "title": ev.get("title") or "",
            "brief": ev.get("brief") or "",
            "type": etype,
            "category": category,
            "color": ev.get("color") or meta.get("color") or "#8b949e",
            "label": EVENT_CATEGORIES[category]["label"],
        })
    out.sort(key=lambda x: x["time"])

    result = {"symbol": sym, "events": out, "types": data["types"],
              "file": EVENTS_FILE, "count": len(out)}
    _cache[sym] = (now, result)
    return result


def add_event(date: str, title: str, brief: str = "", type_: str = "other",
              scope: str = "all", color: str | None = None) -> dict:
    """追加一条事件并写回文件（给接口/脚本用）。"""
    if _ts(date) is None:
        raise ValueError(f"日期格式应为 YYYY-MM-DD: {date}")
    with _lock:
        data = _read()
        ev = {"date": date, "title": title, "brief": brief, "type": type_,
              "scope": scope.upper()}
        if color:
            ev["color"] = color
        data["events"].append(ev)
        data["events"].sort(key=lambda e: e.get("date") or "")
        os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
        tmp = EVENTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, EVENTS_FILE)
    _cache.clear()
    return ev
