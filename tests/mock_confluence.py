"""Confluence Data Center REST 모의 서버 (Phase 0 골격).

표준 라이브러리만 쓴다. 127.0.0.1에만 바인딩한다. 가상 데이터만 담는다.

- `start(port=0, **modes) -> RunningMock`
- `python tests/mock_confluence.py` → 고정 포트 8082에서 Ctrl+C까지 실행.

모드:
- `bad_token="x"`: 그 토큰은 401.
- `require_token="x"`: `Authorization: Bearer x`가 아니면 401.

라우팅은 `ROUTES` 표(메서드, 경로 정규식, 처리 함수)로 한다. 이후 단계는 표에 줄을 더하고
`KNOWN_MODES`에 모드 이름을 더한다.
"""
from __future__ import annotations

import json
import re
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

FIXED_PORT = 8082
KNOWN_MODES = {"bad_token", "require_token"}

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
    server_version = "mock-confluence/0"

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
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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


# ---------------------------------------------------------------- Confluence 동작

PERSONAL_SPACE = {"id": 123, "key": "~honggildong", "name": "홍길동", "type": "personal"}


def _current_user(req: Request):
    return 200, {"type": "known", "username": "honggildong", "userKey": "8a7f1c2e3d4b5a6f", "displayName": "홍길동"}


def _spaces(req: Request):
    if "spaceKey" in req.query:
        found = req.query["spaceKey"] == PERSONAL_SPACE["key"]
    else:
        found = req.query.get("type", "personal") == "personal"
    results = [dict(PERSONAL_SPACE)] if found else []
    return 200, {"results": results, "size": len(results)}


ROUTES = [
    ("GET", r"/rest/api/user/current", _current_user),
    ("GET", r"/rest/api/space", _spaces),
]


def _authorized(req: Request) -> bool:
    if req.token is None:
        return False
    required = req.mock.modes.get("require_token")
    if required is not None and req.token != required:
        return False
    bad = req.mock.modes.get("bad_token")
    return not (bad is not None and req.token == bad)


def handle(req: Request):
    if req.path.startswith("/rest/") and not _authorized(req):
        return 401, {"message": "인증이 필요합니다"}
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
