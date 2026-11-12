"""实时新闻聚合：中文快讯（金十）+ 英文财经/加密 RSS。

输出统一结构，前端只按 tag 过滤，不关心来源差异。
所有请求都走 feed 的通道（自动代理），单个源失败不影响其它源。
"""
from __future__ import annotations

import email.utils
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import config as C, feed

CN_TZ = timezone(timedelta(hours=8))
CACHE_TTL = 45
_cache: dict[str, tuple[float, list]] = {}

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
def _rss(url: str, source: str, tag_hint: str = "", limit: int = 20) -> list[dict]:
    raw = _get(url, timeout=15)
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
            "id": f"{source}-{abs(hash(link or title)) % 10**10}",
            "ts": ts,
            "source": source,
            "tag": tag,
            "title": title,
            "body": desc[:300],
            "url": link,
            "important": False,
        })
    return out


def _parse_rss_time(s: str) -> int:
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return int(time.time() * 1000)


# ---------------------------------------------------------------- 汇总
SOURCES = [
    ("jin10", _jin10),
    ("coindesk", lambda: _rss("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", "crypto")),
    ("cointelegraph", lambda: _rss("https://cointelegraph.com/rss", "Cointelegraph", "crypto")),
    ("cnbc", lambda: _rss("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC", "stock")),
]


def fetch(tag: str = "all", limit: int = 60) -> dict:
    key = "__all__"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        items = hit[1]
    else:
        # 各源并行拉取，单个源失败不影响其它源
        from concurrent.futures import ThreadPoolExecutor
        items, errs = [], []
        with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
            futs = {name: pool.submit(fn) for name, fn in SOURCES}
            for name, f in futs.items():
                try:
                    items.extend(f.result())
                except Exception as e:  # noqa: BLE001
                    errs.append({"source": name, "error": str(e)[:120]})
        # 去重（同一标题只留一条），按时间倒序
        seen, uniq = set(), []
        for it in sorted(items, key=lambda x: -x["ts"]):
            k = it["title"][:48]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(it)
        items = uniq
        _cache[key] = (now, items)
        _cache["__errors__"] = (now, errs)

    errs = _cache.get("__errors__", (0, []))[1]
    if tag and tag != "all":
        filtered = [it for it in items if it["tag"] == tag]
    else:
        filtered = items

    counts = {}
    for it in items:
        counts[it["tag"]] = counts.get(it["tag"], 0) + 1

    return {
        "items": filtered[:limit],
        "total": len(items),
        "counts": counts,
        "errors": errs,
        "ts": int(now * 1000),
    }
