"""事件影响回测：统计宏观数据公布当天 / 之后，标的实际怎么走。

只描述历史基准 —— 过去 N 次公布，当天平均涨跌多少、上涨概率多大、
振幅是平常日的几倍。**不预测下一次**，样本量也一并返回，方便判断可信度。
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

from . import feed

# 会明显影响风险资产定价、且有足够历史样本的事件
TYPES = {
    "cpi": "CPI",
    "payroll": "非农",
    "ppi": "PPI",
    "fed": "FOMC",
}
DAY_MS = 86400 * 1000

_cache: dict[str, tuple[float, dict]] = {}
TTL = 3600


def _day_key(ms: int) -> int:
    return int(ms) // DAY_MS


def _pct(x: float) -> float:
    return round(x * 100, 2)


def compute(symbol: str, force: bool = False) -> dict:
    sym = feed.base_symbol(feed.norm_symbol(symbol))
    now = time.time()
    hit = _cache.get(sym)
    if hit and not force and now - hit[0] < TTL:
        return hit[1]

    result = {
        "symbol": sym,
        "ok": False,
        "baseline": {},
        "types": [],
        "window": {},
        "note": "统计口径：日线，公布日按 UTC 日期对齐；只描述历史分布，不代表预测。",
    }

    try:
        bars = feed.klines(sym, "1d", want=3200)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"日线不可用: {exc}"
        return result

    ts, close, high, low = bars["ts"], bars["close"], bars["high"], bars["low"]
    n = len(ts)
    if n < 120:
        result["error"] = "日线样本不足（需要至少 120 根）"
        return result

    index = {_day_key(t): i for i, t in enumerate(ts)}

    # 基准：全样本的日均绝对涨跌与日均振幅
    day_rets, day_ranges = [], []
    for i in range(1, n):
        if not close[i - 1]:
            continue
        day_rets.append(abs(close[i] / close[i - 1] - 1))
        day_ranges.append((high[i] - low[i]) / close[i - 1])
    base_abs = statistics.fmean(day_rets) if day_rets else 0.0
    base_range = statistics.fmean(day_ranges) if day_ranges else 0.0

    result["baseline"] = {
        "days": len(day_rets),
        "abs_ret_pct": _pct(base_abs),
        "range_pct": _pct(base_range),
    }
    result["window"] = {
        "from": datetime.fromtimestamp(ts[0] / 1000, timezone.utc).strftime("%Y-%m-%d"),
        "to": datetime.fromtimestamp(ts[-1] / 1000, timezone.utc).strftime("%Y-%m-%d"),
        "bars": n,
    }

    try:
        from . import events as _events
        evs = (_events.for_symbol(sym).get("events") or [])
    except Exception:  # noqa: BLE001
        evs = []

    by_type: dict[str, list[dict]] = {k: [] for k in TYPES}
    for ev in evs:
        etype = ev.get("type")
        if etype not in by_type:
            continue
        date = (ev.get("date") or "")[:10]
        if len(date) != 10:
            continue
        try:
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        i = index.get(_day_key(int(day.timestamp()) * 1000))
        if not i or i < 1 or i + 1 >= n or not close[i - 1]:
            continue
        by_type[etype].append({
            "date": date,
            "ret": close[i] / close[i - 1] - 1,                  # 公布日当天涨跌
            "range": (high[i] - low[i]) / close[i - 1],          # 公布日振幅
            "next": close[i + 1] / close[i] - 1 if close[i] else 0.0,   # 公布后次日
        })

    rows = []
    for etype, label in TYPES.items():
        sample = by_type[etype]
        if len(sample) < 8:
            continue
        rets = [s["ret"] for s in sample]
        ranges = [s["range"] for s in sample]
        nxt = [s["next"] for s in sample]
        up = sum(1 for r in rets if r > 0) / len(rets)
        rows.append({
            "type": etype,
            "label": label,
            "n": len(sample),
            "mean_pct": _pct(statistics.fmean(rets)),
            "median_pct": _pct(statistics.median(rets)),
            "up_rate": round(up * 100, 1),
            "abs_pct": _pct(statistics.fmean(abs(r) for r in rets)),
            "range_pct": _pct(statistics.fmean(ranges)),
            "range_ratio": round(statistics.fmean(ranges) / base_range, 2) if base_range else None,
            "next_mean_pct": _pct(statistics.fmean(nxt)),
            "last_date": sample[-1]["date"],
        })

    result["types"] = rows
    result["ok"] = bool(rows)
    if not rows:
        result["error"] = "当前标的与日历事件没有足够重叠样本"
    _cache[sym] = (now, result)
    return result
