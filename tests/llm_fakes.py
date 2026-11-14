"""LLM 테스트용 가짜 OpenAI-compatible HTTP 서버 도우미."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def chat_reply(content, *, reasoning=None, reasoning_content=None, finish="stop", usage=True, model="fake-model"):
    """OpenAI 호환 chat/completions 응답 본문을 만든다."""
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    body = {"id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    return body


class FakeLLM:
    """요청을 기록하고 `replies` 큐(없으면 `default`)로 답한다. 답: (상태, 본문[, 머리글]) 또는 함수(req)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list = []
        self.default = (200, chat_reply('{"ok": true}'))
        self.models = (200, {"object": "list", "data": [{"id": "fake-model"}]})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _go(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                req = {"method": self.command, "path": self.path, "json": body,
                       "auth": self.headers.get("Authorization")}
                fake.requests.append(req)
                if self.path.endswith("/models"):
                    reply = fake.models
                else:
                    reply = fake.replies.pop(0) if fake.replies else fake.default
                if callable(reply):
                    reply = reply(req)
                status, payload, *rest = reply
                headers = rest[0] if rest else {}
                data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _go

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def chats(self) -> list[dict]:
        return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
