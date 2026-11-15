"""Public PANews calendar ingestion with persistent, source-attributed records."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from . import config as C

FILE = os.path.join(C.DATA_DIR, "calendar_archive.json")
BACKFILL_MARK = os.path.join(C.DATA_DIR, ".bls_backfilled")
BACKFILL_TTL = 7 * 86400          # 归档页一周回补一次就够
BLS_ARCHIVE_PAGES = (
    ("cpi", "https://www.bls.gov/bls/news-release/cpi.htm", "美国 CPI 公布"),
    ("payroll", "https://www.bls.gov/bls/news-release/empsit.htm", "美国非农就业公布"),
    ("ppi", "https://www.bls.gov/bls/news-release/ppi.htm", "美国 PPI 公布"),
)
_lock = threading.Lock()
_running = False
_attempt = 0.0
_error = ""


def category(title: str) -> str:
    text = title.lower()
    rules = (
        ("liquidation", ("爆仓", "清算金额", "liquidation")),
        ("security", ("被盗", "黑客", "攻击", "漏洞", "hack", "exploit")),
        ("exchange", ("破产", "暴雷", "停止提现", "暂停提现", "bankrupt")),
        ("executive", ("ceo", "首席", "辞任", "辞职", "卸任", "创始人", "创办人")),
        ("election", ("选举", "大选", "竞选", "election")),
        ("payroll", ("非农", "nonfarm")),
        ("cpi", ("cpi", "消费者价格指数")),
        ("ppi", ("ppi", "生产者价格指数")),
        ("fed", ("美联储", "fomc", "federal reserve")),
        ("policy", ("监管", "财政", "关税", "sec", "央行", "gdp")),
        ("halving", ("比特币减半", "halving")),
    )
    return next((kind for kind, words in rules if any(word in text for word in words)), "industry")


def parse_panews(html: str) -> list[dict]:
    match = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("PANews calendar structured data missing (possibly blocked)")
    table = json.loads(match.group(1))

    def ref(index):
        return table[index] if isinstance(index, int) and 0 <= index < len(table) else None

    records = []
    for row in table:
        if not isinstance(row, dict) or not {"startAt", "translations", "articleId"} <= row.keys():
            continue
        translations = ref(row["translations"]) or []
        translated = [ref(index) for index in translations]
        title = next((ref(t["title"]) for t in translated if isinstance(t, dict)
                      and ref(t.get("lang")) == "zh" and "title" in t), None)
        start = ref(row["startAt"])
        if not title or not start:
            continue
        instant = datetime.fromisoformat(start.replace("Z", "+00:00"))
        article = ref(row["articleId"])
        url = ref(row.get("url")) or (f"https://www.panewslab.com/zh/articles/{article}" if article else "https://www.panewslab.com/zh/calendar")
        records.append({"id": "panews-" + str(ref(row["id"])),
                        "date": instant.strftime("%Y-%m-%d"), "time": int(instant.timestamp()),
                        "title": title, "brief": "PANews 日历收录事项；计划日程不代表事件已实际发生。",
                        "type": category(title), "scope": "all", "source": "PANews 日历",
                        "url": url, "scheduled": True, "time_precision": "day" if ref(row.get("ignoreTime")) else "minute"})
    if not records:
        raise ValueError("PANews calendar returned no events")
    return records


def parse_fed_calendar(html: str) -> list[dict]:
    """Parse the Federal Reserve's published FOMC meeting dates."""
    import calendar as _calendar
    heading = re.compile(r'<a id="\d+">(20\d{2}) FOMC Meetings</a>')
    row = re.compile(r'<div class="[^\"]*fomc-meeting__month[^>]*><strong>([^<]+)</strong>.*?'
                     r'<div class="fomc-meeting__date[^>]*>([^<]+)</div>', re.S)
    records = []
    for match in heading.finditer(html):
        year = int(match.group(1))
        section = html[match.end():]
        next_heading = heading.search(section)
        if next_heading:
            section = section[:next_heading.start()]
        for month_name, day_range in row.findall(section):
            month_name = month_name.strip().split("/")[-1]
            months = {name: index for index, name in enumerate(_calendar.month_name)}
            months.update({name: index for index, name in enumerate(_calendar.month_abbr)})
            if month_name not in months:
                continue
            if "notation" in day_range:
                continue
            day = re.search(r'(\d{1,2})\*?\s*$', day_range.strip())
            if not day:
                continue
            date = f"{year:04d}-{months[month_name]:02d}-{int(day.group(1)):02d}"
            records.append({"id": f"fed-fomc-{date}", "date": date,
                            "time": int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()),
                            "title": "美联储 FOMC 会议结束 / 利率决议日",
                            "brief": "美联储官方日程中的会议结束日期（美国当地日期，日级精度）；未填入未经核实的利率结果。",
                            "type": "fed", "scope": "all", "source": "美联储官方日程",
                            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                            "scheduled": date > datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            "time_precision": "day"})
    return records


def archive(records: list[dict]) -> None:
    if not records:
        return
    with _lock:
        current = read()
        merged = {item["id"]: item for item in current if item.get("id")}
        merged.update({item["id"]: item for item in records})
        os.makedirs(C.DATA_DIR, exist_ok=True)
        temp = FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(sorted(merged.values(), key=lambda item: item["time"])[-20000:], handle, ensure_ascii=False)
        os.replace(temp, FILE)


def read() -> list[dict]:
    try:
        with open(FILE, encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, ValueError):
        return []
    from . import bls_stats
    return bls_stats.decorate(rows)


def parse_bls_archive(text: str, etype: str, label: str) -> list[dict]:
    """解析 BLS 新闻稿归档页：链接里就带发布日（cpi_01152025.htm = 2025-01-15）。

    使用归档页而不是 ICS，是因为 ICS 只滚动两年；归档页能一路回到 2008 年。
    """
    slug = {"cpi": "cpi", "payroll": "empsit", "ppi": "ppi"}[etype]
    found: dict[str, dict] = {}
    for match in re.finditer(r"archives/" + slug + r"_(\d{2})(\d{2})(\d{4})\.htm", text):
        month, day, year = match.groups()
        date = f"{year}-{month}-{day}"
        # 发布日 N 号公布的是「上个月」的数据
        ref_year, ref_month = int(year), int(month) - 1
        if ref_month == 0:
            ref_year, ref_month = ref_year - 1, 12
        found[f"bls-{etype}-{date}"] = {
            "id": f"bls-{etype}-{date}",
            "date": date,
            "time": int(datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp()) + 8 * 3600,
            "title": f"{label}（{ref_year}年{ref_month}月数据）",
            "brief": "美国劳工统计局（BLS）官方发布日；日期取自新闻稿归档。",
            "type": etype, "scope": "all", "source": "BLS 官方发布日程",
            "url": "https://www.bls.gov/bls/news-release/",
            "scheduled": False,
            "time_precision": "day",
        }
    return list(found.values())


def parse_bls_ics(text: str) -> list[dict]:
    """解析 BLS 官方发布日历（iCalendar）。

    BLS 官网直连会返回 403，这里走文本代理取官方 ICS；
    只保留 CPI / PPI / 非农三个指标 —— 其他发布（州就业、JOLTS 等）对看盘没意义。
    """
    wanted = {
        "consumer price index": ("cpi", "美国 CPI"),
        "producer price index": ("ppi", "美国 PPI"),
        "employment situation": ("payroll", "美国非农就业"),
    }
    records = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"^SUMMARY:(.+)$", block, re.M)
        start = re.search(r"^DTSTART[^:]*:(\d{8})", block, re.M)
        if not summary or not start:
            continue
        title = summary.group(1).strip()
        key = title.lower()
        if key not in wanted:
            continue
        etype, label = wanted[key]
        raw = start.group(1)
        date = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        records.append({
            "id": f"bls-{etype}-{date}",
            "date": date,
            "time": int(datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp()) + 8 * 3600,   # 美东 08:30 ≈ UTC 13:30，取近似整点
            "title": f"{label}发布",
            "brief": "美国劳工统计局（BLS）官方发布日程；日期为公布日（美东时间）。",
            "type": etype, "scope": "all", "source": "BLS 官方发布日程",
            "url": "https://www.bls.gov/schedule/news_release/",
            "scheduled": date > datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "time_precision": "day",
        })
    return records


def refresh() -> None:
    global _running, _attempt, _error
    with _lock:
        if _running or time.time() - _attempt < 900:
            return
        _running, _attempt = True, time.time()

    def run():
        global _running, _error
        try:
            from .news import _get
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            url = f"https://www.panewslab.com/zh/calendar?mode=calendar&period=month&date={day}"
            errors = []
            # (名称, 地址, 解析器, 超时秒)
            sources = (
                ("BLS", "https://r.jina.ai/https://www.bls.gov/schedule/news_release/bls.ics",
                 parse_bls_ics, 40),
                ("PANews", url, parse_panews, 10),
                ("FOMC", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                 parse_fed_calendar, 15),
            )
            for name, source_url, parser, timeout in sources:
                try:
                    records = parser(_get(source_url, timeout=timeout, direct=True).decode("utf-8"))
                    if not records:
                        raise ValueError("no calendar records")
                    archive(records)
                except Exception as exc:
                    errors.append(f"{name}: {exc}")

            # 历史回补：BLS 归档页能提供 2008 年至今的 CPI/非农/PPI 发布日
            stale = (not os.path.exists(BACKFILL_MARK)
                     or time.time() - os.path.getmtime(BACKFILL_MARK) > BACKFILL_TTL)
            if stale:
                got = 0
                for etype, page, label in BLS_ARCHIVE_PAGES:
                    try:
                        text = _get("https://r.jina.ai/" + page, timeout=50,
                                    direct=True).decode("utf-8")
                        records = parse_bls_archive(text, etype, label)
                        if records:
                            archive(records)
                            got += len(records)
                    except Exception as exc:
                        errors.append(f"BLS归档{etype}: {exc}")
                # 一条都没拿到就别写标记，下一轮继续重试
                if got:
                    try:
                        os.makedirs(C.DATA_DIR, exist_ok=True)
                        with open(BACKFILL_MARK, "w", encoding="utf-8") as fh:
                            fh.write(datetime.now(timezone.utc).isoformat())
                    except OSError:
                        pass

            _error = "; ".join(errors)[:300]
        except Exception as exc:
            _error = str(exc)[:200]
        finally:
            with _lock:
                _running = False
    threading.Thread(target=run, name="calendar-refresh", daemon=True).start()


def status() -> dict:
    return {"source": "PANews 日历 / 美联储官方日程", "refreshing": _running, "error": _error,
            "last_attempt": int(_attempt), "archived": len(read())}
