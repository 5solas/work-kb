"""Confluence Data Center REST 모의 서버 (Phase 0 골격 + Phase 5 페이지 저장).

표준 라이브러리만 쓴다. 127.0.0.1에만 바인딩한다. 가상 데이터만 담는다.

- `start(port=0, **modes) -> RunningMock`
- `python tests/mock_confluence.py` → 고정 포트 8082에서 Ctrl+C까지 실행.

경로(ROUTES):
- GET  /rest/api/user/current
- GET  /rest/api/space                 (`spaceKey`, `type`, `start`, `limit`)
- GET  /rest/api/space/{key}           (`expand=homepage`)
- GET  /rest/api/content               (`spaceKey`, `title`, `type`, `expand`, `start`, `limit`)
- GET  /rest/api/content/{id}          (`expand=version,body.storage,ancestors,space`)
- POST /rest/api/content               생성: 제목은 스페이스 안에서 하나뿐, 부모는 같은 스페이스
- PUT  /rest/api/content/{id}          갱신: `version.number`가 지금 버전 + 1이 아니면 409

생성·갱신 본문(`body.storage.value`)은 `ac:`·`ri:` 이름공간을 선언한 뿌리로 감싸 XML로 읽어 보고,
잘 짜인(well-formed) XML이 아니면 400을 준다. 각 스페이스에는 홈 페이지가 하나 있다.

모드:
- `bad_token="x"`: 그 토큰은 401.
- `require_token="x"`: `Authorization: Bearer x`가 아니면 401.
- `version_conflict_once=True`: 첫 PUT은 다른 사람이 먼저 고친 것처럼 버전을 하나 올리고 409.
- `forbid_create=True`: POST(생성)는 403.
- `anonymous=True`: `/user/current`가 익명 사용자를 돌려준다.
- `personal_spaces=[{key, name, type}, …]`: 스페이스 목록을 이것으로 바꾼다(기본: `~honggildong` 하나).
- `redirect_to="http://…"`: `/rest/api/content…` 요청(`redirect_methods`, 기본 GET·POST·PUT)에 307과
  `Location: <redirect_to><경로>`로 답한다.
- `reject_title="제목"`: 그 제목의 생성·갱신은 400(Confluence 문장 포함).
- `server_error=N`: 다음 N개의 `/rest/api/content…` 요청은 503.

`GET /rest/api/content…`의 응답에 `space`는 expand에 `space`가 있거나 expand가 없을 때(DC 기본값
history,space,version)만 들어간다. `spaceKey`는 대소문자 없이 찾는다.

`mock.pages`(id → 페이지 사전)에 저장된 페이지가 있다. `mock.user_pages()`는 홈 페이지를 뺀 목록.
"""
from __future__ import annotations

import json
import re
import socketserver
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlsplit

FIXED_PORT = 8082
KNOWN_MODES = {
    "bad_token",
    "require_token",
    "version_conflict_once",
    "forbid_create",
    "anonymous",
    "personal_spaces",
    "redirect_to",
    "redirect_methods",
    "reject_title",
    "server_error",
}
REDIRECT_METHODS = ("GET", "POST", "PUT")
STORAGE_ROOT = (
    '<ac:root xmlns:ac="http://atlassian.com/content" xmlns:ri="http://atlassian.com/resource/identifier" '
    'xmlns:at="http://atlassian.com/template">'
)

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
        self.lock = threading.Lock()
        self.spaces: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}
        self._next_id = 1000
        self._conflict_used = False
        _seed(self)

    def user_pages(self) -> list[dict]:
        """홈 페이지를 뺀 저장 페이지(만든 순서)."""
        with self.lock:
            return [dict(p) for p in self.pages.values() if not p.get("homepage")]

    def page_by_title(self, space_key: str, title: str) -> dict | None:
        with self.lock:
            for page in self.pages.values():
                if page["space"] == space_key and page["title"] == title:
                    return dict(page)
        return None

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
        path = unquote(parts.path)
        mock.log.append({"method": self.command, "path": path, "query": query, "has_auth": token is not None, "json": body})
        req = Request(method=self.command, path=path, query=query, headers=self.headers, json_body=body, bad_json=bad_json, token=token, mock=mock)
        result = handle(req)
        status, payload = result[0], result[1]
        extra = result[2] if len(result) > 2 else {}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for name, value in extra.items():
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


# ---------------------------------------------------------------- Confluence 데이터

PERSONAL_SPACE = {"id": 123, "key": "~honggildong", "name": "홍길동", "type": "personal"}
USER = {"type": "known", "username": "honggildong", "userKey": "8a7f1c2e3d4b5a6f", "displayName": "홍길동"}
ANONYMOUS = {"type": "anonymous", "displayName": "Anonymous"}


def _seed(mock: RunningMock) -> None:
    """스페이스와 각 스페이스의 홈 페이지를 만든다."""
    listed = mock.modes.get("personal_spaces")
    spaces = [dict(PERSONAL_SPACE)] if listed is None else [dict(s) for s in listed]
    for number, space in enumerate(spaces):
        space.setdefault("id", 200 + number)
        space.setdefault("type", "personal")
        space.setdefault("name", space["key"])
        home_id = _new_id(mock)
        mock.pages[home_id] = {
            "id": home_id,
            "title": f"{space['name']} 홈",
            "space": space["key"],
            "version": 1,
            "body": "<p>홈</p>",
            "ancestors": [],
            "homepage": True,
        }
        mock.spaces[space["key"]] = {"space": space, "homepage": home_id}


def _new_id(mock: RunningMock) -> str:
    mock._next_id += 1
    return str(mock._next_id)


def _error(status: int, message: str):
    return status, {"statusCode": status, "message": message}


def _expand(req: Request) -> set[str]:
    return {part.strip() for part in req.query.get("expand", "").split(",") if part.strip()}


def _int(value, default: int) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


def _page_view(mock: RunningMock, page: dict, expand: set[str]) -> dict:
    """expand에 맞춘 페이지. `space`는 expand에 있거나 expand가 비었을 때(DC 기본값)만 넣는다."""
    space = mock.spaces[page["space"]]["space"]
    out = {
        "id": page["id"],
        "type": "page",
        "status": "current",
        "title": page["title"],
        "_links": {"webui": f"/spaces/{space['key']}/pages/{page['id']}"},
    }
    if not expand or "space" in expand:
        out["space"] = {"key": space["key"], "name": space["name"]}
    if "version" in expand:
        out["version"] = {"number": page["version"]}
    if "body.storage" in expand:
        out["body"] = {"storage": {"value": page["body"], "representation": "storage"}}
    if "ancestors" in expand:
        out["ancestors"] = [{"id": a, "type": "page"} for a in page["ancestors"]]
    return out


def _write_view(req: "Request", page: dict) -> dict:
    """생성·갱신 응답: 언제나 version·ancestors, space는 `_page_view` 규칙대로."""
    expand = _expand(req)
    shown = expand | {"version", "ancestors"} | (set() if expand else {"space"})
    return _page_view(req.mock, page, shown)


def _storage_problem(value) -> str | None:
    """잘 짜인 storage XML이면 None, 아니면 이유."""
    if not isinstance(value, str):
        return "body.storage.value가 문자열이 아닙니다"
    try:
        ET.fromstring(STORAGE_ROOT + value + "</ac:root>")
    except ET.ParseError as exc:
        return f"Error parsing xhtml: {exc}"
    return None


def _title_taken(mock: RunningMock, space_key: str, title: str, except_id: str | None = None) -> bool:
    return any(
        p["space"] == space_key and p["title"] == title and p["id"] != except_id for p in mock.pages.values()
    )


# ---------------------------------------------------------------- 처리 함수


def _current_user(req: Request):
    if req.mock.modes.get("anonymous"):
        return 200, dict(ANONYMOUS)
    return 200, dict(USER)


def _space_view(entry: dict) -> dict:
    space = entry["space"]
    return {k: space[k] for k in ("id", "key", "name", "type")}


def _spaces(req: Request):
    mock = req.mock
    with mock.lock:
        entries = list(mock.spaces.values())
    if "spaceKey" in req.query:
        wanted = req.query["spaceKey"].casefold()
        results = [_space_view(e) for e in entries if e["space"]["key"].casefold() == wanted]
    else:
        kind = req.query.get("type", "personal")
        results = [_space_view(e) for e in entries if e["space"]["type"] == kind]
    payload: dict = {}
    if "start" in req.query or "limit" in req.query:
        start = _int(req.query.get("start"), 0)
        limit = _int(req.query.get("limit"), 25) or 25
        results = results[start : start + limit]
        payload = {"start": start, "limit": limit}
    return 200, {"results": results, **payload, "size": len(results)}


def _space_one(req: Request):
    mock = req.mock
    key = req.params["key"]
    with mock.lock:
        entry = mock.spaces.get(key)
        if entry is None:
            return _error(404, f"No space with key : {key}")
        out = _space_view(entry)
        if "homepage" in _expand(req):
            home = mock.pages[entry["homepage"]]
            out["homepage"] = {"id": home["id"], "type": "page", "title": home["title"]}
    return 200, out


def _content_list(req: Request):
    mock = req.mock
    space_key = req.query.get("spaceKey")
    title = req.query.get("title")
    kind = req.query.get("type", "page")
    expand = _expand(req)
    with mock.lock:
        found = [
            _page_view(mock, p, expand)
            for p in mock.pages.values()
            if kind == "page"
            and (space_key is None or p["space"] == space_key)
            and (title is None or p["title"] == title)
        ]
    start = _int(req.query.get("start"), 0)
    limit = _int(req.query.get("limit"), 25) or 25
    results = found[start : start + limit]
    return 200, {"results": results, "start": start, "limit": limit, "size": len(results), "_links": {}}


def _content_get(req: Request):
    mock = req.mock
    with mock.lock:
        page = mock.pages.get(req.params["id"])
        if page is None:
            return _error(404, f"No content found with id: {req.params['id']}")
        return 200, _page_view(mock, page, _expand(req))


def _storage_of(body) -> tuple[str | None, str | None]:
    """(값, 오류)."""
    storage = (body.get("body") or {}).get("storage") if isinstance(body.get("body"), dict) else None
    if not isinstance(storage, dict):
        return None, "body.storage가 없습니다"
    if storage.get("representation") != "storage":
        return None, "representation은 storage여야 합니다"
    problem = _storage_problem(storage.get("value"))
    if problem:
        return None, problem
    return storage["value"], None


def _content_create(req: Request):
    mock = req.mock
    body = req.json
    if req.bad_json or not isinstance(body, dict):
        return _error(400, "JSON 본문이 필요합니다")
    if mock.modes.get("forbid_create"):
        return _error(403, "이 스페이스에 페이지를 만들 권한이 없습니다")
    if body.get("type") != "page":
        return _error(400, "type은 page여야 합니다")
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        return _error(400, "제목이 비어 있습니다")
    if title == mock.modes.get("reject_title"):
        return _error(400, f"이 제목은 받을 수 없습니다(모의 거부): {title}")
    space_key = (body.get("space") or {}).get("key") if isinstance(body.get("space"), dict) else None
    value, problem = _storage_of(body)
    if problem:
        return _error(400, problem)
    with mock.lock:
        if space_key not in mock.spaces:
            return _error(400, f"No space with key : {space_key}")
        ancestors = body.get("ancestors") or []
        parent_ids: list[str] = []
        if ancestors:
            if not isinstance(ancestors, list) or not isinstance(ancestors[-1], dict):
                return _error(400, "ancestors 형식이 잘못되었습니다")
            parent_id = str(ancestors[-1].get("id"))
            parent = mock.pages.get(parent_id)
            if parent is None or parent["space"] != space_key:
                return _error(400, f"부모 페이지가 이 스페이스에 없습니다: {parent_id}")
            parent_ids = parent["ancestors"] + [parent_id]
        if _title_taken(mock, space_key, title):
            return _error(400, f"A page with this title already exists: A page already exists with the title {title} in the space with key {space_key}")
        page_id = _new_id(mock)
        page = {"id": page_id, "title": title, "space": space_key, "version": 1, "body": value, "ancestors": parent_ids}
        mock.pages[page_id] = page
        return 200, _write_view(req, page)


def _content_update(req: Request):
    mock = req.mock
    body = req.json
    if req.bad_json or not isinstance(body, dict):
        return _error(400, "JSON 본문이 필요합니다")
    with mock.lock:
        page = mock.pages.get(req.params["id"])
        if page is None:
            return _error(404, f"No content found with id: {req.params['id']}")
        if mock.modes.get("version_conflict_once") and not mock._conflict_used:
            mock._conflict_used = True
            page["version"] += 1  # 다른 사람이 먼저 고쳤다
            return _error(409, f"Version must be incremented on update. Current version is: {page['version']}")
        if body.get("type") != "page":
            return _error(400, "type은 page여야 합니다")
        version = (body.get("version") or {}).get("number") if isinstance(body.get("version"), dict) else None
        if not isinstance(version, int) or isinstance(version, bool):
            return _error(400, "version.number가 필요합니다")
        if version != page["version"] + 1:
            return _error(409, f"Version must be incremented on update. Current version is: {page['version']}")
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            return _error(400, "제목이 비어 있습니다")
        if title == mock.modes.get("reject_title"):
            return _error(400, f"이 제목은 받을 수 없습니다(모의 거부): {title}")
        space = body.get("space")
        if isinstance(space, dict) and space.get("key") not in (None, page["space"]):
            return _error(400, "다른 스페이스로 옮길 수 없습니다")
        value, problem = _storage_of(body)
        if problem:
            return _error(400, problem)
        if _title_taken(mock, page["space"], title, except_id=page["id"]):
            return _error(400, f"A page with this title already exists: {title}")
        page.update(title=title, body=value, version=version)
        return 200, _write_view(req, page)


ROUTES = [
    ("GET", r"/rest/api/user/current", _current_user),
    ("GET", r"/rest/api/space", _spaces),
    ("GET", r"/rest/api/space/(?P<key>[^/]+)", _space_one),
    ("GET", r"/rest/api/content/?", _content_list),
    ("GET", r"/rest/api/content/(?P<id>\d+)", _content_get),
    ("POST", r"/rest/api/content/?", _content_create),
    ("PUT", r"/rest/api/content/(?P<id>\d+)", _content_update),
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
    mock = req.mock
    if req.path.startswith("/rest/api/content"):
        target = mock.modes.get("redirect_to")
        if target and req.method in tuple(mock.modes.get("redirect_methods") or REDIRECT_METHODS):
            location = str(target).rstrip("/") + req.path
            return 307, {"message": "다른 곳으로 옮겼습니다"}, {"Location": location}
        with mock.lock:
            pending = int(mock.modes.get("server_error") or 0)
            if pending > 0:
                mock.modes["server_error"] = pending - 1
                return _error(503, "일시적인 서버 오류(모의)")
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
