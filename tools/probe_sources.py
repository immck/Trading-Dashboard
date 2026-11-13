"""探测美股 / 新闻数据源可达性。"""
from __future__ import annotations

import json
import time
import urllib.request

PROXY = "http://127.0.0.1:7897"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

op_proxy = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
op_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TARGETS = [
    # ---- 美股行情 ----
    ("yahoo chart AAPL", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?interval=1d&range=5m",
     {"User-Agent": UA}),
    ("yahoo quote AAPL", "https://query1.finance.yahoo.com/v7/finance/quote?symbols=AAPL",
     {"User-Agent": UA}),
    ("stooq AAPL csv", "https://stooq.com/q/d/l/?s=aapl.us&i=d", {"User-Agent": UA}),
    ("sina us AAPL", "https://hq.sinajs.cn/list=gb_aapl",
     {"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}),
    ("eastmoney us", "https://push2.eastmoney.com/api/qt/stock/get?secid=105.AAPL"
                     "&fields=f43,f44,f45,f46,f60,f170", {"User-Agent": UA}),
    ("tencent us", "https://qt.gtimg.cn/q=usAAPL", {"User-Agent": UA}),
    # ---- 新闻 ----
    ("coindesk rss", "https://www.coindesk.com/arc/outboundfeeds/rss/", {"User-Agent": UA}),
    ("cointelegraph rss", "https://cointelegraph.com/rss", {"User-Agent": UA}),
    ("blockbeats flash", "https://api.theblockbeats.news/v1/open-api/open-flash?size=5&page=1&type=push",
     {"User-Agent": UA}),
    ("jin10 flash", "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1",
     {"User-Agent": UA, "x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0"}),
    ("cls telegraph", "https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=7.7.5",
     {"User-Agent": UA}),
    ("odaily flash", "https://api.odaily.news/v1/openapi/open/flash?page=1&size=5", {"User-Agent": UA}),
    ("reuters rss", "https://feeds.reuters.com/reuters/businessNews", {"User-Agent": UA}),
    ("cnbc rss", "https://www.cnbc.com/id/100003114/device/rss/rss.html", {"User-Agent": UA}),
]


def probe(name, url, headers, opener, tag):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=12) as r:
            raw = r.read(4000)
        dt = (time.time() - t0) * 1000
        body = raw.decode("utf-8", "replace")
        preview = body[:90].replace("\n", " ")
        print(f"  [{tag}] {name:<20} {r.status} {dt:6.0f}ms  {len(raw):>5}B  {preview}")
        return True
    except Exception as e:  # noqa: BLE001
        dt = (time.time() - t0) * 1000
        print(f"  [{tag}] {name:<20} ERR {dt:6.0f}ms  {type(e).__name__}: {str(e)[:70]}")
        return False


print("=" * 100)
print("直连")
print("=" * 100)
for t in TARGETS:
    probe(*t, op_direct, "direct")

print("\n" + "=" * 100)
print("走代理")
print("=" * 100)
for t in TARGETS:
    probe(*t, op_proxy, "proxy")
