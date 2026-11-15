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

from . import config as C, feed, calendar_feed

EVENTS_FILE = os.path.join(C.DATA_DIR, "events.json")
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}
TTL = 30
SEED_FILE = os.path.join(os.path.dirname(__file__), "event_seed.json")

DEFAULT_TYPES = {
    "halving": {"label": "减半", "color": "#f0b90b"},
    "etf": {"label": "ETF/上市", "color": "#3fb950"},
    "upgrade": {"label": "技术升级", "color": "#c480ff"},
    "incident": {"label": "风险事件", "color": "#ff4976"},
    "macro": {"label": "宏观", "color": "#4da6ff"},
    "regulation": {"label": "监管", "color": "#ff9500"},
    "peak": {"label": "极值", "color": "#e6edf3"},
    "other": {"label": "其它", "color": "#8b949e"},
    "exchange": {"label": "交易所暴雷", "color": "#ff4976"},
    "security": {"label": "安全 / 被盗", "color": "#ff4976"},
    "executive": {"label": "高管 / CEO", "color": "#f0883e"},
    "election": {"label": "选举 / 政治", "color": "#4da6ff"},
}

EVENT_CATEGORIES = {
    "liquidation": {"label": "爆仓事件", "color": "#ff4976"},
    "payroll": {"label": "非农", "color": "#4da6ff"},
    "cpi": {"label": "CPI", "color": "#ff9f45"},
    "ppi": {"label": "PPI", "color": "#38c6b0"},
    "fed": {"label": "美联储利率", "color": "#c480ff"},
    "policy": {"label": "政策 / 宏观", "color": "#8b9dff"},
    "macro": {"label": "政策 / 宏观", "color": "#8b9dff"},
    "halving": {"label": "比特币减半", "color": "#f0b90b"},
    "industry": {"label": "行业", "color": "#6e7681"},
    "security": {"label": "安全事件", "color": "#ffa198"},
    "exchange": {"label": "交易所暴雷", "color": "#f85149"},
    "executive": {"label": "高管 / CEO", "color": "#d2a8ff"},
    "election": {"label": "选举 / 政治", "color": "#3fb950"},
    "incident": {"label": "其他事件", "color": "#d5a33c"},
    "etf": {"label": "行业", "color": "#6e7681"},
    "upgrade": {"label": "行业", "color": "#6e7681"},
    "regulation": {"label": "政策 / 宏观", "color": "#8b9dff"},
    "peak": {"label": "其他事件", "color": "#d5a33c"},
    "other": {"label": "其他事件", "color": "#d5a33c"},
}

CATEGORY_BY_TYPE = {
    "macro": "policy", "regulation": "policy",
    "etf": "industry", "upgrade": "industry",
    "incident": "other", "peak": "other",
}


def _read() -> dict:
    data = {"types": dict(DEFAULT_TYPES), "events": []}
    for path in (SEED_FILE, os.path.join(os.path.dirname(__file__), "macro_seed.json"), EVENTS_FILE):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            incoming = json.load(f)
        data["types"].update(incoming.get("types", {}))
        merged = {(ev.get("date"), ev.get("title")): ev for ev in data["events"]}
        merged.update({(ev.get("date"), ev.get("title")): ev for ev in incoming.get("events", [])})
        data["events"] = list(merged.values())
    archived = calendar_feed.read()
    from .news import _translation_cache, _clean
    for item in archived:
        for field in ("title", "brief"):
            item[field] = _translation_cache.get(_clean(item.get(field) or ""), item.get(field) or "")
    merged_ids = {item.get("id"): item for item in data["events"] if item.get("id")}
    merged_ids.update({item["id"]: item for item in archived if item.get("id")})
    data["events"] = [item for item in data["events"] if not item.get("id")] + list(merged_ids.values())
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


def for_symbol(symbol: str, exclude: list[str] | None = None) -> dict:
    """返回该标的可见的事件（scope 匹配 all 或该标的）。

    exclude: 要排除的事件分类（category），用于把「行业快讯」这类大量条目挡在传输之前。
    """
    sym = feed.base_symbol(feed.norm_symbol(symbol)).upper()
    calendar_feed.refresh()
    now = time.time()
    hit = _cache.get(sym)
    if hit and now - hit[0] < TTL:
        base, types_meta, cal = hit[1]
    else:
        with _lock:
            data = _read()

        base = []
        for ev in data.get("events", []):
            scope = (ev.get("scope") or "all").upper()
            if scope not in ("ALL", sym):
                continue
            ts = ev.get("time") or _ts(ev.get("date", ""))
            if ts is None:
                continue
            etype = ev.get("type") or "other"
            meta = data["types"].get(etype, DEFAULT_TYPES["other"])
            category = CATEGORY_BY_TYPE.get(etype, etype)
            if ev.get("title") == "黑色星期四":
                category = "liquidation"
            elif ev.get("title") in {"FTX 破产", "FTX 申请破产"}:
                category = "exchange"
            if category not in EVENT_CATEGORIES:
                category = "other"
            base.append({
                "time": ts,
                "date": ev.get("date"),
                "title": ev.get("title") or "",
                "brief": ev.get("brief") or "",
                "type": etype,
                "category": category,
                "color": (ev.get("color") or EVENT_CATEGORIES[category]["color"]
                          or meta.get("color") or "#8b949e"),
                "label": EVENT_CATEGORIES[category]["label"],
                "source": ev.get("source") or "历史事件档案",
                "url": ev.get("url") or "",
                "value": ev.get("value") or "",
                "scheduled": bool(ev.get("scheduled")),
            })
        base.sort(key=lambda x: x["time"])
        # 同一天同一指标可能既有官方日程又有新闻快讯，保留官方那条
        CAL_TYPES = {"cpi", "payroll", "ppi", "fed"}
        keep: dict[tuple, dict] = {}
        deduped = []
        for item in base:
            if item["type"] not in CAL_TYPES:
                deduped.append(item)
                continue
            key = (item["type"], (item.get("date") or "")[:10])
            cur = keep.get(key)
            if cur is None:
                keep[key] = item
                deduped.append(item)
            elif (cur.get("source") != "BLS 官方发布日程"
                  and item.get("source") == "BLS 官方发布日程"):
                deduped[deduped.index(cur)] = item
                keep[key] = item
        base = deduped
        types_meta = data["types"]
        cal = calendar_feed.status()
        _cache[sym] = (now, (base, types_meta, cal))

    skip = {c for c in (exclude or []) if c}
    evs = [e for e in base if e["category"] not in skip] if skip else base

    return {"symbol": sym, "events": evs, "types": types_meta,
            "file": EVENTS_FILE, "count": len(evs), "total": len(base),
            "excluded": sorted(skip), "calendar": cal}


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
