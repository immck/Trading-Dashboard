"""AI 接入层自检：用本地 mock 服务模拟 OpenAI 兼容接口，验证整条链路。

不依赖任何真实 API Key。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def free_port() -> int:
    """让系统分配一个空闲端口，避免和常驻 mock 冲突（Windows 下 SO_REUSEADDR 会静默抢端口）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


MOCK_PORT = free_port()
PRICE_TEXT = "73646.91"      # mock 输出里要引用的「真实」价格，启动前替换


# ---------------------------------------------------------------- mock LLM
class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            req = {}
        self.server.last_request = req

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # 模拟流式输出；故意混入一个数据里没有的数字 12345.67
        pieces = ["这是", "一段", "测试", "解读：", f"价格 {PRICE_TEXT} ", "，",
                  "另外 12345.67 是编的。"]
        for p in pieces:
            chunk = "data: " + json.dumps(
                {"choices": [{"delta": {"content": p}}]}, ensure_ascii=False) + "\n\n"
            self._send_chunk(chunk.encode("utf-8"))
            time.sleep(0.005)
        self._send_chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _send_chunk(self, data: bytes):
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def do_GET(self):  # noqa: N802
        self.send_response(404)
        self.end_headers()


def main():
    global PRICE_TEXT
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'OK ' if cond else 'FAIL'}] {name}{('  ' + extra) if extra else ''}")

    # 用已保存的 fixture，测试不依赖实时行情
    fixture = os.path.join(ROOT, "data", "fixtures", "report.json")
    if not os.path.exists(fixture):
        print(f"缺少 fixture: {fixture}")
        print("先启动服务并访问 /api/report?symbol=BTC 生成一次即可")
        return 2
    with open(fixture, encoding="utf-8") as f:
        rep_fixture = json.load(f)
    PRICE_TEXT = f"{rep_fixture['price']:,.2f}"
    print(f"使用 fixture：{rep_fixture['symbol']} 价格 {PRICE_TEXT}")

    # 必须在本进程导入 core.config 之前设置环境变量
    os.environ["TD_AI_KEY"] = "test-key-not-real"
    os.environ["TD_AI_BASE"] = f"http://127.0.0.1:{MOCK_PORT}/v1"
    os.environ["TD_AI_MODEL"] = "mock-model"
    os.environ["TD_PROXY"] = ""          # 走直连，避免代理拦截 localhost

    srv = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from core import ai  # noqa: E402

    print("=" * 70)
    print("1) 配置识别")
    print("=" * 70)
    st = ai.status()
    check("configured", st["configured"])
    check("model 生效", st["model"] == "mock-model", st["model"])
    check("base_url 生效", st["base_url"].startswith("http://127.0.0.1"), st["base_url"])
    check("presets 可枚举", len(st["presets"]) >= 5, f"{len(st['presets'])} 个预设")

    print("\n" + "=" * 70)
    print("2) 提示词构造")
    print("=" * 70)
    rep = rep_fixture
    msgs = ai.build_messages(rep, "周线为什么和整体方向冲突？")
    check("两条消息", len(msgs) == 2)
    check("system 含硬约束", "禁止编造数字" in msgs[0]["content"])
    ctx = msgs[1]["content"]
    check("带上了标的名", rep["symbol"] in ctx)
    check("带上了价格", f"{rep['price']}" in ctx, str(rep["price"]))
    check("带上了追问", "周线为什么" in ctx)
    check("上下文可控", len(ctx) < 20000, f"{len(ctx)} 字符")

    print("\n" + "=" * 70)
    print("3) 流式调用（mock 服务）")
    print("=" * 70)
    t0 = time.time()
    got = []
    kinds = []
    try:
        for kind, payload in ai.stream_chat(msgs):
            kinds.append(kind)
            if kind == "text":
                got.append(payload)
    except Exception as e:  # noqa: BLE001
        check("流式调用", False, repr(e))
    text = "".join(got)
    check("收到文本片段", len(got) >= 5, f"{len(got)} 段")
    check("结束事件", kinds and kinds[-1] == "done")
    check("文本拼装正确", text.endswith("是编的。"), text[-12:])
    check("请求体是流式", bool(getattr(srv, "last_request", {}).get("stream")))
    check("模型名正确", getattr(srv, "last_request", {}).get("model") == "mock-model")
    check("耗时合理", time.time() - t0 < 10, f"{time.time() - t0:.2f}s")

    print("\n" + "=" * 70)
    print("4) 数值核对")
    print("=" * 70)
    v = ai.verify(text, rep)
    check("识别出编造数字", any("12345" in x for x in v["unknown"]), str(v["unknown"]))
    check("没有把真实价格误报", not any(PRICE_TEXT.replace(",", "")[:5] in x.replace(",", "")
                                   for x in v["unknown"]), str(v["unknown"]))
    v2 = ai.verify(f"价格是 {rep['price']}，多空比 2.72。", rep)
    check("真实数字不误报", len(v2["unknown"]) == 0, str(v2["unknown"]))

    print("\n" + "=" * 70)
    print("5) 未配置时降级")
    print("=" * 70)
    ai.C.AI_API_KEY = ""
    st2 = ai.status()
    check("status 报告未配置", not st2["configured"])
    check("给出提示语", "TD_AI_KEY" in st2["hint"], st2["hint"][:40])
    try:
        list(ai.stream_chat(msgs))
        check("未配置时报错", False)
    except ai.AIError as e:
        check("未配置时报错", "API Key" in str(e), str(e)[:40])

    srv.shutdown()
    print("\n" + "=" * 70)
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
