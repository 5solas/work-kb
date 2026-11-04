"""OpenAI 호환 LLM 모의 서버 (Phase 0 골격).

표준 라이브러리만 쓴다. 127.0.0.1에만 바인딩한다.

- `start(port=0, **modes) -> RunningMock`
- `python tests/mock_llm.py` → 고정 포트 8083에서 Ctrl+C까지 실행.

모드:
- `require_key="k"`: `Authorization: Bearer k`가 아니면 401.
- `delay_sec=1.5`: 모든 응답 전에 그만큼 잔다(타임아웃 시험용).
- `redirect_to="http://…"`: `GET /models`(`/v1/models`)에 302와 `Location: <값>`으로 답한다.

라우팅은 `ROUTES` 표(메서드, 경로 정규식, 처리 함수)로 한다. 이후 단계(Phase 4)는 표에 줄을
더하고 `KNOWN_MODES`에 모드 이름을 더한다.
"""
from __future__ import annotations

import json
import re
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

FIXED_PORT = 8083
KNOWN_MODES = {"require_key", "delay_sec", "redirect_to"}

# ---------------------------------------------------------------- 공통 골격


class Request:
    """처리 함수에 넘기는 요청 정보."""

    def __init__(self, *, method, path, query, headers, json_body, bad_json, token, mock):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.json = json_body
        self.bad_json = bad_json
        self.token = token
        self.mock = mock
        self.params: dict[str, str] = {}


class RunningMock:
    """실행 중인 모의 서버. `stop()`은 서버를 닫고 스레드를 합쳐 포트를 돌려준다."""

    def __init__(self, server: "_Server", modes: dict):
        self._server = server
        self._thread: threading.Thread | None = None
        self.port: int = server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.log: list[dict] = []
        self.modes = dict(modes)

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        thread.join(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    verbose = False
    mock: RunningMock

    def server_bind(self):
        # HTTPServer.server_bind의 getfqdn(역방향 조회)을 피한다.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        if self.verbose:
            super().handle_error(request, client_address)


def _bearer(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class _Handler(BaseHTTPRequestHandler):
    server_version = "mock-llm/0"

    def _dispatch(self):
        mock = self.server.mock
        parts = urlsplit(self.path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        body, bad_json = None, False
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                bad_json = True
        token = _bearer(self.headers.get("Authorization"))
        mock.log.append({"method": self.command, "path": parts.path, "query": query, "has_auth": token is not None, "json": body})
        req = Request(method=self.command, path=parts.path, query=query, headers=self.headers, json_body=body, bad_json=bad_json, token=token, mock=mock)
        status, payload = handle(req)
        headers: dict[str, str] = {}
        if isinstance(payload, Redirect):
            headers["Location"] = payload.location
            payload = {"redirect": payload.location}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch

    def log_message(self, format, *args):
        if self.server.verbose:
            super().log_message(format, *args)


def _start(port: int, modes: dict, verbose: bool) -> RunningMock:
    unknown = set(modes) - KNOWN_MODES
    if unknown:
        raise TypeError(f"알 수 없는 모드: {sorted(unknown)}")
    server = _Server(("127.0.0.1", port), _Handler)
    server.verbose = verbose
    mock = RunningMock(server, modes)
    server.mock = mock
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, name=f"{__name__}:{mock.port}", daemon=True)
    mock._thread = thread
    thread.start()
    return mock


def start(port: int = 0, **modes) -> RunningMock:
    return _start(port, modes, verbose=False)


def main() -> None:
    mock = _start(FIXED_PORT, {}, verbose=True)
    print(mock.base_url, flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        mock.stop()


# ---------------------------------------------------------------- LLM 동작

MODELS = {"object": "list", "data": [{"id": "mock", "object": "model", "metadata": {"context_length": 32768}}]}
COMPLETION_TOKENS = 5


class Redirect:
    """처리 함수가 돌려주면 `Location` 머리글을 붙인다."""

    def __init__(self, location: str):
        self.location = location


def _models(req: Request):
    target = req.mock.modes.get("redirect_to")
    if target:
        return 302, Redirect(str(target))
    return 200, MODELS


def _prompt_tokens(messages) -> int:
    """모든 메시지 content의 문자 수 합 // 4. 문자열만 센다(문자열이 아닌 content 조각은 무시)."""
    total = 0
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += sum(len(part) for part in content if isinstance(part, str))
    return total // 4


def _chat(req: Request):
    if req.bad_json or not isinstance(req.json, dict):
        return 400, {"error": {"message": "잘못된 JSON"}}
    prompt = _prompt_tokens(req.json.get("messages"))
    return 200, {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": req.json.get("model"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok": true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": COMPLETION_TOKENS, "total_tokens": prompt + COMPLETION_TOKENS},
    }


ROUTES = [
    ("GET", r"(/v1)?/models", _models),
    ("POST", r"(/v1)?/chat/completions", _chat),
]


def handle(req: Request):
    delay = req.mock.modes.get("delay_sec")
    if delay:
        time.sleep(float(delay))
    key = req.mock.modes.get("require_key")
    if key is not None and req.token != key:
        return 401, {"error": {"message": "인증이 필요합니다"}}
    for method, pattern, func in ROUTES:
        if method != req.method:
            continue
        match = re.fullmatch(pattern, req.path)
        if match:
            req.params = match.groupdict()
            return func(req)
    return 404, {"error": f"없는 경로: {req.method} {req.path}"}


if __name__ == "__main__":
    main()
