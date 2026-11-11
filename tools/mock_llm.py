"""独立的 mock LLM 服务，用于验证服务端 SSE 流式输出。

用法：python tools/mock_llm.py 8891
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8891


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        pieces = ["## 核心判断\n", "BTC 当前处于", "多周期偏空", "的结构。\n\n",
                  "## 关键读数\n", "- 加权分 -46，5/6 周期同向\n",
                  "- 下方 3% 内多损 1.25B，空损 138.9M\n",
                  "- 注意：99999.99 这个数字是我编的\n"]
        for p in pieces:
            data = ("data: " + json.dumps(
                {"choices": [{"delta": {"content": p}}]}, ensure_ascii=False) + "\n\n").encode()
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()
            time.sleep(0.05)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_GET(self):  # noqa: N802
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    print(f"mock LLM on http://127.0.0.1:{PORT}/v1", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
