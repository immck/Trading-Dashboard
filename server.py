"""HTTP 服务：标准库 ThreadingHTTPServer，REST + 静态文件。

启动：python server.py
      浏览器打开 http://127.0.0.1:8770
"""
from __future__ import annotations

import gzip
import json
import mimetypes
import os
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import (ai, analysis, backtest, config as C, feed, indicators as I, liqmap,
                  market, patterns as P, report)

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/manifest+json", ".webmanifest")

_build = time.strftime("%Y%m%d-%H%M")
_lock = threading.Lock()
_stats = {"requests": 0, "started": time.time()}


# ---------------------------------------------------------------- 序列化
def _round(x, nd=8):
    """数值清洗。价格默认 8 位小数，兼容 0.00xxxx 这类小市值币。"""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, nd)


def series(arr, limit: int | None = None) -> list:
    """numpy 数组 -> JSON 列表（NaN 变 null，浮点降精度）。"""
    if arr is None:
        return []
    a = arr[-limit:] if limit else arr
    out = []
    for v in a:
        f = _round(v)
        out.append(f)
    return out


def bars_out(bars: dict, limit: int) -> dict:
    n = len(bars["ts"])
    s = max(0, n - limit)
    return {
        "time": [int(t // 1000) for t in bars["ts"][s:]],
        "open": series(bars["open"], limit),
        "high": series(bars["high"], limit),
        "low": series(bars["low"], limit),
        "close": series(bars["close"], limit),
        "volume": series(bars["volume"], limit),
    }


# ---------------------------------------------------------------- 路由
def api_symbols(q):
    market = (q.get("market", ["crypto"])[0] or "crypto").lower()
    if market == "us":
        return _symbols_us()
    # 6 个标的的 ticker 并行拉取，避免串行等待
    def one(s):
        try:
            t = feed.ticker(s, light=True)
            return {"symbol": s, "last": _round(t.get("last"), 4),
                    "change_pct": _round(t.get("change_pct"), 2)}
        except Exception:  # noqa: BLE001
            return {"symbol": s, "last": None, "change_pct": None}

    with ThreadPoolExecutor(max_workers=max(2, len(C.SYMBOLS))) as pool:
        out = list(pool.map(one, C.SYMBOLS))
    return {"symbols": out, "market": "crypto", "timeframes": list(C.TIMEFRAMES),
            "default_tf": C.DEFAULT_TF, "source": C.SOURCE, "build": _build}


def _symbols_us():
    from core import usstock
    # 个股走新浪批量（一次请求）；指数新浪格式不同，单独走 Yahoo
    try:
        quotes = usstock.quotes_batch(list(C.US_SYMBOLS))
    except Exception:  # noqa: BLE001
        quotes = {}

    idx_quotes: dict[str, dict] = {}

    def one_index(s):
        try:
            return s, usstock.quote(s)
        except Exception:  # noqa: BLE001
            return s, {}

    with ThreadPoolExecutor(max_workers=len(C.US_INDICES) or 1) as pool:
        for s, q in pool.map(one_index, list(C.US_INDICES.keys())):
            idx_quotes[s] = q

    out = []
    for s in list(C.US_INDICES.keys()) + list(C.US_SYMBOLS):
        q = idx_quotes.get(s) or quotes.get(s.upper()) or {}
        out.append({
            "symbol": s,
            "name": C.US_INDICES.get(s) or q.get("name") or s,
            "last": _round(q.get("last"), 4),
            "change_pct": _round(q.get("change_pct"), 2),
            "index": s.startswith("^"),
        })
    return {"symbols": out, "market": "us", "timeframes": list(C.US_TIMEFRAMES),
            "default_tf": "1d", "source": "yahoo", "build": _build}


def api_news(q):
    from core import news
    tag = q.get("tag", ["all"])[0]
    limit = max(1, min(int(q.get("limit", ["60"])[0]), 200))
    return news.fetch(tag, limit)


def api_ticker(q):
    sym = q.get("symbol", ["BTC"])[0]
    t = feed.ticker(sym)
    return {"symbol": feed.base_symbol(feed.norm_symbol(sym)), **{k: _round(v, 6) for k, v in t.items()}}


def api_mtf(q):
    sym = q.get("symbol", ["BTC"])[0]
    data = analysis.mtf(sym)
    # 精简：每行只保留前端需要的字段
    rows = []
    for r in data["rows"]:
        if "error" in r:
            rows.append({"tf": r["tf"], "error": r["error"]})
            continue
        rows.append({
            "tf": r["tf"],
            "score": r["score"],
            "verdict": r["verdict"],
            "verdict_label": r["verdict_label"],
            "price": r["price"],
            "signals": [{"key": s["key"], "label": s["label"], "dir": s["dir"],
                         "w": s["w"], "note": s["note"], "value": s["value"]}
                        for s in r["signals"]],
            "patterns": {
                "candles": r["patterns"]["recent"],
                "chart": r["patterns"]["chart"],
                "latest": r["patterns"]["latest"],
                "bias": r["patterns"]["bias"],
            },
            "snapshot": r["snapshot"],
            "levels": r["levels"],
            "stale": r["stale"],
        })
    data["rows"] = rows
    return data


def api_chart(q):
    sym = q.get("symbol", ["BTC"])[0]
    tf = q.get("tf", [C.DEFAULT_TF])[0]
    limit = min(int(q.get("limit", [C.SERIES_OUT])[0]), C.MAX_BARS)
    bars = feed.klines(sym, tf)
    ind = I.compute_all(bars)
    pat = P.detect(bars)
    n = len(bars["close"])
    s = max(0, n - limit)

    def cut(a):
        return series(a, limit)

    payload = {
        "symbol": feed.base_symbol(feed.norm_symbol(sym)),
        "tf": tf,
        "count": n,
        "stale": bool(bars.get("stale")),
        "bars": bars_out(bars, limit),
        "ma": {k: cut(v) for k, v in ind["ma"].items()},
        "ema": {k: cut(v) for k, v in ind["ema"].items()},
        "boll": {k: cut(v) for k, v in ind["boll"].items()},
        "macd": {k: cut(v) for k, v in ind["macd"].items()},
        "rsi": cut(ind["rsi"]),
        "kdj": {k: cut(v) for k, v in ind["kdj"].items()},
        "adx": {k: cut(v) for k, v in ind["adx"].items()},
        "atr": cut(ind["atr"]),
        "vwap": cut(ind["vwap"]),
        "obv": cut(ind["obv"]["obv"]),
        "stochrsi": {k: cut(v) for k, v in ind["stoch_rsi"].items()},
        "supertrend": {"value": cut(ind["supertrend"]["value"]),
                       "direction": [int(x) for x in ind["supertrend"]["direction"][s:]]},
        "mfi": cut(ind["mfi"]),
        "cci": cut(ind["cci"]),
        "wr": cut(ind["wr"]),
        "cmf": cut(ind["cmf"]),
        "sr": ind["sr"],
        "fib": ind["fib"],
        "volprofile": ind["volprofile"],
        "structure": ind["structure"],
        # ---- TradingView 系列 ----
        "ichimoku": {k: cut(ind["ichimoku"][k])
                     for k in ("tenkan", "kijun", "senkou_a", "senkou_b")},
        "psar": cut(ind["psar"]),
        "keltner": {k: cut(v) for k, v in ind["keltner"].items()},
        "donchian": {k: cut(v) for k, v in ind["donchian"].items()},
        "hma": cut(ind["hma"]),
        "vwma": cut(ind["vwma"]),
        "chandelier": {"value": cut(ind["chandelier"]["value"]),
                       "direction": [int(x) for x in ind["chandelier"]["direction"][s:]]},
        "pivot": ind["pivot"],
        "stoch": {"k": cut(ind["stoch"]["k"]), "d": cut(ind["stoch"]["d"])},
        "ao": cut(ind["ao"]),
        "trix": {k: cut(v) for k, v in ind["trix"].items()},
        "uo": cut(ind["uo"]),
        "vortex": {k: cut(v) for k, v in ind["vortex"].items()},
        "chaikin": {"osc": cut(ind["chaikin"]["osc"])},
        "squeeze": {"momentum": cut(ind["squeeze"]["momentum"]),
                    "on": [int(x) for x in ind["squeeze"]["on"][s:]]},
        "dpo": cut(ind["dpo"]),
        "bop": cut(ind["bop"]),
        "patterns": pat,
        "snapshot": I.snapshot(ind, bars),
    }
    # 形态标记转到相对位置
    payload["markers"] = [
        {"i": p["idx"] - s, "dir": p["dir"], "label": p["label"], "conf": p["conf"]}
        for p in (pat["candles"] + pat["chart"]) if p["idx"] >= s
    ]
    return payload


def api_scan(q):
    """全部标的快速扫描：1h/4h/1d 综合评分，用于列表排序。"""
    def one(sym):
        row = {"symbol": sym}
        try:
            t = feed.ticker(sym, light=True)
            row["last"] = _round(t.get("last"), 4)
            row["change_pct"] = _round(t.get("change_pct"), 2)
        except Exception:  # noqa: BLE001
            row["last"] = row["change_pct"] = None
        scores = {}
        for tf in ("1h", "4h", "1d"):
            try:
                scores[tf] = analysis.analyze(sym, tf)["score"]
            except Exception:  # noqa: BLE001
                scores[tf] = None
        row["scores"] = scores
        vals = [v for v in scores.values() if v is not None]
        row["avg"] = _round(sum(vals) / len(vals), 1) if vals else None
        return row

    with ThreadPoolExecutor(max_workers=max(2, len(C.SYMBOLS))) as pool:
        out = list(pool.map(one, C.SYMBOLS))
    out.sort(key=lambda r: (r["avg"] is None, -(r["avg"] or 0)))
    return {"rows": out}


def api_symbols_all(_q):
    """Binance U 本位永续全部可交易标的。"""
    try:
        return {"symbols": feed.all_symbols()}
    except Exception as e:  # noqa: BLE001
        return {"symbols": [], "error": str(e)}


def api_liqmap(q):
    sym = q.get("symbol", ["BTC"])[0]
    rng = float(q.get("range", ["12"])[0])
    return liqmap.build(sym, rng)


def api_snapshot(q):
    sym = q.get("symbol", ["BTC"])[0]
    return market.snapshot(sym)


def api_report_analysis(q):
    """生成当前标的的完整技术研判（含 Markdown 版）。"""
    sym = q.get("symbol", ["BTC"])[0]
    force = q.get("force", ["0"])[0] in ("1", "true", "yes")
    return report.build(sym, force=force)


def api_backtest(q):
    sym = q.get("symbol", ["BTC"])[0]
    tf = q.get("tf", ["1h"])[0]
    horizon = max(1, min(int(q.get("horizon", ["24"])[0]), 500))
    bars = max(backtest.MIN_BARS, min(int(q.get("bars", ["3000"])[0]), feed.PAGE_CAP))
    return backtest.run(sym, tf, horizon, bars)


def api_ai_status(_q):
    return ai.status()


def api_health(_q):
    return {
        "ok": True, "build": _build, "uptime_s": int(time.time() - _stats["started"]),
        "requests": _stats["requests"], "source": C.SOURCE,
        "transport": feed.cache_info()["transport"], "cache": feed.cache_info()["entries"],
    }


ROUTES = {
    "/api/symbols": api_symbols,
    "/api/symbols/all": api_symbols_all,
    "/api/news": api_news,
    "/api/liqmap": api_liqmap,
    "/api/ticker": api_ticker,
    "/api/mtf": api_mtf,
    "/api/chart": api_chart,
    "/api/scan": api_scan,
    "/api/snapshot": api_snapshot,
    "/api/report": api_report_analysis,
    "/api/ai/status": api_ai_status,
    "/api/backtest": api_backtest,
    "/api/health": api_health,
}


# ---------------------------------------------------------------- Handler
class Handler(BaseHTTPRequestHandler):
    server_version = "TradingDashboard/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 只打错误，保持控制台干净
        pass

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store",
              extra: dict | None = None):
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        use_gzip = accepts_gzip and len(body) > 800 and not ctype.startswith("image/")
        if use_gzip:
            body = gzip.compress(body, 6)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        try:
            body = json.dumps(obj, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError) as e:
            body = json.dumps({"error": f"序列化失败: {e}"}).encode("utf-8")
            code = 500
        self._send(code, body, "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        _stats["requests"] += 1
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        q = urllib.parse.parse_qs(parsed.query)

        if path in ROUTES:
            t0 = time.time()
            try:
                data = ROUTES[path](q)
                self._json(data)
                dt = (time.time() - t0) * 1000
                if dt > 500:
                    print(f"[slow] {path}?{parsed.query} {dt:.0f} ms", flush=True)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._json({"error": str(e), "route": path}, 500)
            return

        if path == "/api":
            self._json({"routes": list(ROUTES)})
            return

        # 静态文件
        rel = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(WEB) or not os.path.isfile(full):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        cache = "public, max-age=86400" if "/vendor/" in full.replace("\\", "/") else "no-cache"
        self._send(200, body, ctype, cache)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    # ------------------------------------------------------------ POST
    def do_POST(self):  # noqa: N802
        _stats["requests"] += 1
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}

        if path == "/api/ai":
            self._handle_ai(body)
            return
        self._json({"error": "not found", "path": path}, 404)

    # ------------------------------------------------------------ AI 流式
    def _sse_begin(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

    def _sse_send(self, obj):
        data = ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
        self.wfile.flush()

    def _sse_end(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _handle_ai(self, body):
        st = ai.status()
        if not st["configured"]:
            self._json({"error": st["hint"], "configured": False, "status": st}, 503)
            return
        if not st["enabled"]:
            self._json({"error": "AI 已在 config 中关闭", "configured": True}, 503)
            return

        sym = str(body.get("symbol") or "BTC").upper()
        question = str(body.get("question") or "")[:500]
        try:
            rep = report.build(sym)
            messages = ai.build_messages(rep, question)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"筹备上下文失败: {e}"}, 500)
            return

        acc: list[str] = []
        self._sse_begin()
        try:
            self._sse_send({"type": "start", "model": st["model"], "symbol": sym})
            for kind, payload in ai.stream_chat(messages):
                if kind == "text":
                    acc.append(payload)
                    self._sse_send({"type": "text", "content": payload})
                elif kind == "done":
                    text = "".join(acc)
                    v = ai.verify(text, rep)
                    self._sse_send({"type": "verify", "unknown": v["unknown"],
                                    "checked": v["checked"]})
                    self._sse_send({"type": "done", "chars": len(text), **payload})
        except ai.AIError as e:
            self._sse_send({"type": "error", "message": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            try:
                self._sse_send({"type": "error", "message": str(e)})
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                self._sse_end()
            except Exception:  # noqa: BLE001
                pass


def main():
    host, port = C.HOST, C.PORT
    print(f"{C.APP_NAME}  build={_build}")
    print(f"  数据源: Binance {C.SOURCE}   标的: {', '.join(C.SYMBOLS)}")
    print(f"  服务地址: http://{host}:{port}")
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    # 先把端口开起来，再在后台预热通道与默认标的
    threading.Thread(target=feed.warmup, args=(C.SYMBOLS[0], C.DEFAULT_TF),
                     daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
