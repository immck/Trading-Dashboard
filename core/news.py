"""实时新闻聚合：中文快讯（金十）+ 英文财经/加密 RSS。

输出统一结构，前端只按 tag 过滤，不关心来源差异。
所有请求都走 feed 的通道（自动代理），单个源失败不影响其它源。
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import re
import time
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import config as C, feed

CN_TZ = timezone(timedelta(hours=8))
_cache: dict[str, tuple[float, list]] = {}
_translation_cache: dict[str, str] = {}
_translation_lock = threading.Lock()
_translation_running = False

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

# 关键词 -> 分类
CRYPTO_KW = ("比特币", "以太", "加密", "数字货币", "稳定币", "btc", "eth", "sol", "币安",
             "coinbase", "defi", "nft", "区块链", "crypto", "bitcoin", "ethereum", "token")
STOCK_KW = ("美股", "纳指", "标普", "道指", "纳斯达克", "苹果", "英伟达", "特斯拉", "微软",
            "亚马逊", "谷歌", "财报", "股价", "纳斯达克", "nasdaq", "s&p", "dow", "earnings",
            "apple", "nvidia", "tesla", "microsoft")
MACRO_KW = ("美联储", "降息", "加息", "通胀", "cpi", "非农", "国债", "美元", "原油", "黄金",
            "关税", "gdp", "fed", "rate", "inflation", "treasury", "oil", "gold")


def _clean(s: str) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub("", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&quot;", '"').replace("&#39;", "'")
    return _SPACE_RE.sub(" ", s).strip()


def _translate_zh(text: str) -> str:
    """Best-effort title translation; preserve source text if translation is unavailable."""
    text = _clean(text)
    if not _needs_translation(text):
        return text
    cached = _translation_cache.get(text)
    if cached:
        return cached
    query = urllib.parse.urlencode({
        "client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text[:4000],
    })
    url = "https://translate.googleapis.com/translate_a/single?" + query
    try:
        data = json.loads(_get(url, timeout=2))
        translated = "".join(part[0] for part in data[0] if part and part[0]).strip()
        if translated:
            _translation_cache[text] = translated
            if len(_translation_cache) > 1000:
                _translation_cache.pop(next(iter(_translation_cache)))
            return translated
    except Exception:  # noqa: BLE001
        pass
    return text


def _needs_translation(text: str) -> bool:
    return bool(text and len(re.findall(r"[A-Za-z]{3,}", text)) >= 2
                and len(re.findall(r"[\u3400-\u9fff]", text)) < len(re.findall(r"[A-Za-z]", text)) / 2)


def _translate_items(items: list[dict]) -> list[dict]:
    """Queue every English title/body, then apply completed translations without blocking feeds."""
    global _translation_running
    texts = list(dict.fromkeys(_clean(it.get(field) or "") for it in items for field in ("title", "body")
                              if _needs_translation(it.get(field) or "") and _clean(it[field]) not in _translation_cache))
    with _translation_lock:
        start = bool(texts) and not _translation_running
        if start:
            _translation_running = True
    if start:
        def run():
            global _translation_running
            from concurrent.futures import ThreadPoolExecutor
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(_translate_zh, texts))
            finally:
                with _translation_lock:
                    _translation_running = False
        threading.Thread(target=run, name="news-translation", daemon=True).start()
    result = []
    for item in items:
        row = dict(item)
        row["original_title"], row["original_body"] = row.get("title", ""), row.get("body", "")
        for field in ("title", "body"):
            row[field] = _translation_cache.get(_clean(row.get(field) or ""), row.get(field) or "")
        row["translation_pending"] = any(_needs_translation(row[field]) for field in ("title", "body"))
        result.append(row)
    return result


def _classify(text: str) -> str:
    t = (text or "").lower()
    cs = sum(1 for k in CRYPTO_KW if k in t)
    ss = sum(1 for k in STOCK_KW if k in t)
    ms = sum(1 for k in MACRO_KW if k in t)
    best = max(cs, ss, ms)
    if best == 0:
        return "other"
    if cs == best:
        return "crypto"
    if ss == best:
        return "stock"
    return "macro"


def _get(url: str, headers: dict | None = None, timeout: float = 12,
         direct: bool = False) -> bytes:
    """取数据。direct=True 先走直连（国内站点），失败再退回代理通道。"""
    req = urllib.request.Request(url, headers=dict({"User-Agent": C.UA}, **(headers or {})))
    if direct:
        try:
            with feed.direct_opener().open(req, timeout=timeout) as r:
                return r.read()
        except Exception:  # noqa: BLE001
            pass
    with feed.opener().open(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- 金十快讯
def _jin10(limit: int = 40) -> list[dict]:
    url = "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1"
    raw = _get(url, {"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0"},
               timeout=8, direct=True)
    data = json.loads(raw)
    out = []
    for it in (data.get("data") or [])[:limit]:
        d = it.get("data") or {}
        content = _clean(d.get("content") or "")
        title = _clean(d.get("title") or "")
        if not content and not title:
            continue
        # 金十把标题写成【xxx】，优先抽出来
        m = re.match(r"^【(.+?)】(.*)$", content, re.S)
        if m:
            title = m.group(1).strip()
            body = m.group(2).strip()
        else:
            body = content
        ts = _parse_cn_time(it.get("time"))
        out.append({
            "id": f"jin10-{it.get('id')}",
            "ts": ts,
            "source": "金十数据",
            "tag": _classify(title + " " + body),
            "title": title or body[:40],
            "body": body,
            "url": "",
            "important": bool(it.get("important")),
        })
    return out


def _parse_cn_time(s: str) -> int:
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return int(time.time() * 1000)


# ---------------------------------------------------------------- RSS
def _rss(url: str, source: str, tag_hint: str = "", limit: int = 20,
         headers: dict | None = None) -> list[dict]:
    raw = _get(url, headers=headers, timeout=15, direct=True)
    root = ET.fromstring(raw)
    out = []
    for it in root.findall(".//item")[:limit]:
        title = _clean(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        desc = _clean(it.findtext("description") or "")
        pub = it.findtext("pubDate") or ""
        ts = _parse_rss_time(pub)
        if not title:
            continue
        tag = tag_hint or _classify(title + " " + desc)
        out.append({
            "id": f"{source}-{hashlib.sha256((link or title).encode()).hexdigest()[:20]}",
            "ts": ts,
            "source": source,
            "tag": tag,
            "title": title,
            "body": desc[:300],
            "url": link,
            "important": False,
        })
    return out


def _odaily(limit: int = 20) -> list[dict]:
    url = "https://api.odaily.news/api/v1/newsflash?page=1&size=20&lang=zh-cn"
    data = json.loads(_get(url, {"Accept": "application/json"}, timeout=10, direct=True))
    out = []
    for item in ((data.get("data") or {}).get("list") or [])[:limit]:
        title = _clean(item.get("title") or "")
        body = _clean(item.get("content") or "")
        if not title:
            continue
        out.append({
            "id": f"odaily-{item.get('id')}",
            "ts": _news_timestamp(item),
            "source": "Odaily星球日报",
            "tag": _classify(title + " " + body),
            "title": title,
            "body": body[:500],
            "url": item.get("link") or item.get("sourceUrl") or "",
            "important": bool(item.get("isImportant")),
        })
    return out


def _news_timestamp(item: dict) -> int:
    stamp = item.get("publishTimestamp")
    if stamp:
        stamp = int(stamp)
        return stamp * 1000 if stamp < 10**11 else stamp
    return _parse_cn_time(item.get("publishDate") or "")


def _blockbeats(limit: int = 20) -> list[dict]:
    """BlockBeats public API RSS feed; the language header selects Chinese content."""
    url = "https://api.theblockbeats.news/v2/rss/newsflash"
    return _rss(url, "律动BlockBeats", "crypto", limit, {"language": "cn"})


def _parse_rss_time(s: str) -> int:
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return int(time.time() * 1000)


# ---------------------------------------------------------------- 汇总
# ---------------------------------------------------------------- 汇总
# (名称, 抓取函数, 是否启用)
# 关闭的 4 个源在本机网络下分别返回 403 / SSL 失败，且每个都要耗满超时，
# 会把整轮抓取拖到 30 秒以上。需要时可在 fetch(sources=...) 里临时打开。
SOURCES = [
    ("jin10", _jin10, True),
    ("odaily", _odaily, True),
    ("coindesk", lambda: _rss("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", "crypto"), True),
    ("cointelegraph", lambda: _rss("https://cointelegraph.com/rss", "Cointelegraph", "crypto"), True),
    ("cnbc", lambda: _rss("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC", "stock"), True),
    ("jinse", lambda: _rss("https://rsshub.app/jinse/lives", "金色财经", "crypto", 20), False),
    ("panews", lambda: _rss("https://rsshub.app/panews/newsflash", "PANews", "crypto", 20), False),
    ("foresight", lambda: _rss("https://rsshub.app/foresightnews/news", "Foresight News", "crypto", 15), False),
    ("blockbeats", _blockbeats, False),
]

CACHE_TTL = 120          # 新鲜期：直接命中
STALE_TTL = 1800         # 过期但还能兜底返回，同时后台刷新
FETCH_DEADLINE = 8.0     # 整轮抓取的硬上限，超时的源直接丢弃
FIRST_WAIT = 3.0         # 首次请求最多等这么久，慢源留给后台补齐
ARCHIVE_INTERVAL = 600   # 归档落盘最小间隔，避免每次刷新都重写 200KB

_fetching = False
_fetch_lock = threading.Lock()
_last_archive = 0.0


def _collect() -> tuple[list[dict], list[dict]]:
    """并行抓取，带全局 deadline。用 daemon 线程，超时源不会拖住进程退出。"""
    active = [(name, fn) for name, fn, on in SOURCES if on]
    items: list[dict] = []
    errs: list[dict] = []
    lock = threading.Lock()
    done = threading.Event()
    state = {"left": len(active)}
    if not active:
        done.set()

    def one(name, fn):
        try:
            got = fn()
            with lock:
                items.extend(got)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errs.append({"source": name, "error": str(exc)[:120]})
        finally:
            with lock:
                state["left"] -= 1
                if state["left"] <= 0:
                    done.set()

    for name, fn in active:
        threading.Thread(target=one, args=(name, fn), daemon=True,
                         name=f"news-{name}").start()

    if not done.wait(FETCH_DEADLINE):
        with lock:
            errs.append({"source": "慢源", "error": f"超过 {FETCH_DEADLINE:.0f}s 未返回，已跳过"})
    with lock:
        return list(items), list(errs)


def _archive_news(items: list[dict]) -> None:
    """把快讯落盘成新闻日记（节流，避免频繁重写归档文件）。"""
    global _last_archive
    now = time.time()
    if now - _last_archive < ARCHIVE_INTERVAL:
        return
    _last_archive = now
    from . import calendar_feed
    try:
        calendar_feed.archive([
            {"id": "news-" + str(it["id"]), "time": int(it["ts"] / 1000),
             "date": datetime.fromtimestamp(it["ts"] / 1000, timezone.utc).strftime("%Y-%m-%d"),
             "title": it["title"], "brief": it.get("body") or "",
             "type": calendar_feed.category(it["title"]),
             "scope": "all", "source": it["source"], "url": it.get("url") or "",
             "scheduled": False}
            for it in items if it.get("ts") and it.get("title")
        ])
    except (OSError, ValueError):
        pass


def _store(items: list[dict], errs: list[dict]) -> list[dict]:
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: -x["ts"]):
        key = it["title"][:48]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    _cache["__all__"] = (time.time(), uniq)
    _cache["__errors__"] = (time.time(), errs)
    _archive_news(uniq)
    return uniq


def _start_refresh() -> bool:
    """单飞：同一时刻只允许一个后台刷新在跑。"""
    global _fetching
    with _fetch_lock:
        if _fetching:
            return False
        _fetching = True

    def run():
        global _fetching
        try:
            items, errs = _collect()
            if items:
                _store(items, errs)
        finally:
            with _fetch_lock:
                _fetching = False

    threading.Thread(target=run, name="news-refresh", daemon=True).start()
    return True


def _payload(items: list[dict], errs: list[dict], tag: str, limit: int) -> dict:
    items = _translate_items(items)
    if tag and tag != "all":
        filtered = [it for it in items if it["tag"] == tag]
    else:
        filtered = items
    counts: dict[str, int] = {}
    for it in items:
        counts[it["tag"]] = counts.get(it["tag"], 0) + 1
    return {
        "items": filtered[:limit],
        "total": len(items),
        "counts": counts,
        "errors": errs,
        "warming": not items,
        "cached": time.time() - _cache.get("__all__", (time.time(), []))[0],
        "ts": int(time.time() * 1000),
    }


def fetch(tag: str = "all", limit: int = 60) -> dict:
    """stale-while-revalidate：有旧数据立即返回，后台刷新，绝不阻塞在慢源上。

    首次请求（完全没有缓存）最多等 FIRST_WAIT 秒拿首批结果，慢源交给后台线程补齐。
    """
    now = time.time()
    hit = _cache.get("__all__")
    errs = _cache.get("__errors__", (0, []))[1]
    age = now - hit[0] if hit else 1e9

    if hit and age < CACHE_TTL:
        return _payload(hit[1], errs, tag, limit)

    started = _start_refresh()

    # 有旧数据：立刻返回，不等待刷新
    if hit and hit[1] and age < STALE_TTL:
        return _payload(hit[1], errs, tag, limit)

    # 完全没有可用数据：最多等 FIRST_WAIT 秒
    if started or not hit:
        until = time.time() + FIRST_WAIT
        while time.time() < until:
            time.sleep(0.05)
            cur = _cache.get("__all__")
            if cur and cur[0] > now:
                break

    hit = _cache.get("__all__")
    errs = _cache.get("__errors__", (0, []))[1]
    return _payload(hit[1] if hit else [], errs, tag, limit)
