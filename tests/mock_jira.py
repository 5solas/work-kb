"""Jira Data Center REST v2 모의 서버 (Phase 0 골격 + Phase 2 읽기 + Phase 3 쓰기).

표준 라이브러리만 쓴다. 127.0.0.1에만 바인딩한다. 가상 데이터만 담는다(tests/fixtures/jira/).

- `start(port=0, **modes) -> RunningMock`
- `python tests/mock_jira.py` → 고정 포트 8081에서 Ctrl+C까지 실행.

경로(GET만, 쓰기는 Phase 3):
- /rest/api/2/myself, /project, /serverInfo (Phase 0)
- /rest/api/2/search?jql=&startAt=&maxResults=&fields=  작은 JQL 해석기(`evaluate_jql`)로 거른다
- /rest/api/2/issue/{key}  (없으면 404), /rest/api/2/issue/{key}/comment (페이지)
- /rest/api/2/field  (스프린트 필드 customfield_10020, schema.custom gh-sprint)

모드:
- `bad_token="x"`: 그 토큰은 401.
- `require_token="x"`: `Authorization: Bearer x`가 아니면 401.
- `rate_limit=N`: 처음 N번의 검색은 429 + `Retry-After: 1`.
- `search_500_once=True`: (429가 끝난 뒤) 첫 검색 한 번은 500.
- `no_sprint_field=True`: /field에 스프린트 필드가 없고, 이슈에도 스프린트 값이 없고, JQL `sprint`는 400.
- `page_size_cap=N`: 서버가 maxResults를 N으로 줄인다(검색·댓글).
- `now_iso="2026-11-05T09:12:00+09:00"`: 모의 '지금'. 없으면 픽스처의 가장 늦은 updated.
- `sprint_form="object"`: 스프린트 값을 DC 문자열 대신 객체 꼴 `{id, name, state, …}`로 준다.
- `search_comment_limit=N`: 검색 결과에 싣는 댓글 수(기본 3, total은 그대로). 이슈 조회는 전부 싣는다.
- `updates={"KB-12": {"updated": "…", "summary": "…"}}`: 시작할 때 이슈 필드를 바꾼다.
- `repeat_page_boundary=True`: startAt > 0인 검색 페이지가 한 칸 앞에서 시작하고 total이 하나 크다
  (페이지를 넘기는 사이 앞쪽에 이슈 하나가 끼어든 것처럼. 앞 페이지의 마지막 이슈가 다시 온다).

실행 중에는 `mock.touch("KB-12", updated=None, **fields)`로 이슈를 바꾸고, `mock.issues`로 데이터를 본다.
`mock.move("KB-12", "OPS-40")`은 이슈를 다른 키(프로젝트)로 옮긴다. 옛 키로 이슈 조회·JQL `key`를 하면
새 이슈가 나온다(Jira처럼). `mock.remove("KB-12")`는 이슈를 지운다(조회 404, JQL `key = KB-12`는 400).
`mock.count("search")`는 받은 검색 요청 수(429·500 포함).

쓰기 경로(Phase 3, Jira DC 9.17 문서의 모양):
- POST /rest/api/2/issue/{key}/comment {"body"} → 201 댓글(id·author·created). 이슈 댓글에 더하고 updated를 새로.
- GET  /rest/api/2/issue/{key}/transitions → {"transitions": [{id, name, to}]} 지금 상태별(`TRANSITIONS`).
  In Review에는 이름이 같은 'Done' 두 개(→ Done, → Closed)가 있다(후보 여럿 시험용).
- POST /rest/api/2/issue/{key}/transitions {"transition": {"id"}} → 204. id가 없으면 400, 지금 상태에서
  못 하는 id면 500(DC 문서대로).
- PUT  /rest/api/2/issue/{key}/assignee {"name"} → 204. 모르는 사용자는 400.
- POST /rest/api/2/issue/{key}/worklog {"timeSpentSeconds", "started", "comment"} → 201. started는
  `yyyy-MM-dd'T'HH:mm:ss.SSSZ`가 아니면 400. `mock.worklogs[KEY]`에 쌓인다.
- POST /rest/api/2/issue {"fields": {project, summary, issuetype, …}} → 201 {id, key, self}. 빠진 필드는
  400 {"errors": {필드: 문장}}. 새 키는 그 프로젝트의 가장 큰 번호 + 1.

쓰기 모드:
- `down=True`: 시작하자마자 듣기 소켓을 닫는다(같은 포트에 연결 거부). 실행 중에는 `mock.go_down()`·
  `mock.come_up()`으로 끄고 켠다(포트 유지, 데이터·로그 유지).
- `write_500_times=N`: 처음 N번의 쓰기는 500(반영하지 않음).
- `slow_write_sec=S`: 쓰기를 반영한 뒤 S초 기다렸다 응답한다(클라이언트 읽기 시간 초과 → '불확실').
- `transition_500=True`: 전환 POST는 늘 500.
- `assign_denied=True`: 담당 PUT은 토큰이 맞아도 401(DC 문서: 권한 없음도 401).
- `worklog_denied=True`: 워크로그 POST는 403.
- `on_write=callable(req)`: 쓰기 요청을 처리하기 전에 부른다(테스트 훅). (상태, 본문[, 헤더])를
  돌려주면 그것으로 응답하고 반영하지 않는다(예: 429).
실행 중에 `mock.modes[...]`를 바꾸면 다음 요청부터 따른다. `mock.count("write")`는 받은 쓰기 수.

라우팅은 `ROUTES` 표(메서드, 경로 정규식, 처리 함수)로 한다. 처리 함수는 (상태, 본문) 또는
(상태, 본문, 추가 헤더)를 돌려준다. 본문이 None이면 빈 본문(204).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

FIXED_PORT = 8081
KNOWN_MODES = {
    "bad_token",
    "require_token",
    "rate_limit",
    "search_500_once",
    "no_sprint_field",
    "page_size_cap",
    "now_iso",
    "sprint_form",
    "search_comment_limit",
    "updates",
    "repeat_page_boundary",
    # Phase 3 쓰기
    "down",
    "write_500_times",
    "slow_write_sec",
    "transition_500",
    "assign_denied",
    "worklog_denied",
    "on_write",
}

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jira"
SPRINT_FIELD = "customfield_10020"
SPRINT_SCHEMA = "com.pyxis.greenhopper.jira:gh-sprint"
CURRENT_USER = "honggildong"
TZ = dt.timezone(dt.timedelta(hours=9))
JIRA_TIME = "%Y-%m-%dT%H:%M:%S.000%z"
DEFAULT_MAX_RESULTS = 50
SERVER_MAX_RESULTS = 1000  # jira.search.views.default.max
DEFAULT_SEARCH_COMMENT_LIMIT = 3

USERS = {
    "honggildong": {"name": "honggildong", "key": "honggildong", "displayName": "홍길동"},
    "kimcs": {"name": "kimcs", "key": "kimcs", "displayName": "김철수"},
}
PROJECTS = [{"id": "10000", "key": "KB", "name": "지식 저장고"}, {"id": "10001", "key": "OPS", "name": "운영"}]
# 워크플로의 모든 상태(픽스처에 안 쓰인 것 포함): 이름 소문자 → 상태 범주 키
STATUSES = {"to do": "new", "open": "new", "in progress": "indeterminate", "in review": "indeterminate", "blocked": "indeterminate", "done": "done", "closed": "done"}
CATEGORY_ALIASES = {
    "to do": "new", "new": "new", "2": "new",
    "in progress": "indeterminate", "indeterminate": "indeterminate", "4": "indeterminate",
    "done": "done", "complete": "done", "3": "done",
}  # fmt: skip

_FIXTURE_CACHE: dict[str, Any] = {}


def load_fixture(name: str) -> Any:
    """tests/fixtures/jira/<name>.json (한 번 읽어 두고 깊은 복사본을 준다)."""
    if name not in _FIXTURE_CACHE:
        _FIXTURE_CACHE[name] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(_FIXTURE_CACHE[name])


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def format_time(value: dt.datetime) -> str:
    return value.astimezone(TZ).strftime(JIRA_TIME)


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
        self._verbose = bool(server.verbose)
        self.port: int = server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.log: list[dict] = []
        self.modes = dict(modes)
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._flags: set[str] = set()
        self.issues: dict[str, dict] = {item["key"]: item for item in load_fixture("issues")}
        self.moved: dict[str, str] = {}  # 옛 키 → 새 키
        self.worklogs: dict[str, list[dict]] = {}
        for key, changes in (modes.get("updates") or {}).items():
            self.issues[key]["fields"].update(copy.deepcopy(changes))

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        thread.join(timeout=10)

    # ------------------------------------------------------------ Phase 3: 끄고 켜기

    def _serve(self) -> None:
        thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, name=f"{__name__}:{self.port}", daemon=True
        )
        self._thread = thread
        thread.start()

    def go_down(self) -> None:
        """듣기 소켓을 닫는다. 같은 포트로 오는 연결은 거부된다. 데이터·로그는 그대로."""
        self.stop()
        self.modes["down"] = True

    def come_up(self, wait: float = 5.0) -> None:
        """같은 포트에서 다시 듣는다. 포트를 잠깐 못 잡으면 wait초까지 다시 시도한다."""
        if self._thread is not None:
            return
        deadline = time.monotonic() + wait
        while True:
            try:
                server = _Server(("127.0.0.1", self.port), _Handler)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        server.verbose = self._verbose
        server.mock = self
        self._server = server
        self.modes["down"] = False
        self._serve()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------ Phase 2 도움

    def bump(self, name: str) -> int:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + 1
            return self._counts[name]

    def count(self, name: str) -> int:
        with self._lock:
            return self._counts.get(name, 0)

    def once(self, flag: str) -> bool:
        """처음 부를 때만 True."""
        with self._lock:
            if flag in self._flags:
                return False
            self._flags.add(flag)
            return True

    def now(self) -> dt.datetime:
        """모의 '지금': now_iso 모드, 없으면 이슈들의 가장 늦은 updated."""
        fixed = self.modes.get("now_iso")
        if fixed:
            value = dt.datetime.fromisoformat(str(fixed))
            return (value if value.tzinfo else value.replace(tzinfo=TZ)).astimezone(TZ)
        with self._lock:
            return max(parse_time(item["fields"]["updated"]) for item in self.issues.values()).astimezone(TZ)

    def touch(self, key: str, updated: str | None = None, **fields: Any) -> str:
        """이슈 필드를 바꾸고 updated를 새로 매긴다(없으면 지금+1분). 새 updated 문자열을 돌려준다."""
        if updated is None:
            updated = format_time(self.now() + dt.timedelta(minutes=1))
        with self._lock:
            target = self.issues[key]["fields"]
            target.update(copy.deepcopy(fields))
            target["updated"] = updated
        return updated

    def move(self, old: str, new: str) -> None:
        """이슈를 새 키로 옮긴다(프로젝트는 새 키의 접두). 옛 키는 새 키를 가리킨다."""
        project = next(p for p in PROJECTS if p["key"] == new.split("-", 1)[0])
        with self._lock:
            issue = self.issues.pop(old)
            issue["key"] = new
            issue["fields"]["project"] = {**issue["fields"]["project"], "key": project["key"], "name": project["name"], "id": project["id"]}
            self.issues[new] = issue
            self.moved[old] = new

    def remove(self, key: str) -> None:
        with self._lock:
            del self.issues[key]

    def resolve(self, key: str) -> str:
        """옮겨진 키를 따라간 지금 키(없으면 받은 그대로)."""
        key = key.upper()
        with self._lock:
            seen = set()
            while key in self.moved and key not in seen:
                seen.add(key)
                key = self.moved[key]
        return key


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
    server_version = "mock-jira/0"

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
        result = handle(req)
        status, payload = result[0], result[1]
        extra = result[2] if len(result) > 2 else {}
        data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        slow = float(mock.modes.get("slow_write_sec") or 0) if req.method in WRITE_METHODS else 0.0
        if slow > 0:
            time.sleep(slow)  # 반영은 끝났고 응답만 늦다 → 클라이언트는 결과를 모른다
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        if data:
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
    try:
        mock = RunningMock(server, modes)
    except Exception:
        server.server_close()
        raise
    server.mock = mock
    mock._serve()
    if modes.get("down"):
        mock.go_down()
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


# ---------------------------------------------------------------- JQL 해석기


class JqlError(Exception):
    """400 {"errorMessages": [문장]}으로 바뀌는 JQL 오류."""


class _Tok:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind: str, value: str, pos: int):
        self.kind, self.value, self.pos = kind, value, pos

    def word(self, *names: str) -> bool:
        return self.kind == "word" and self.value.upper() in names


_TOKEN = re.compile(
    r"""(?P<ws>\s+)
    |(?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    |(?P<op>!=|>=|<=|!~|=|>|<|~)
    |(?P<punc>[(),])
    |(?P<word>[^\s()",'=!<>~]+)""",
    re.VERBOSE | re.DOTALL,
)


def _where(pos: int) -> str:
    return f"(line 1, character {pos + 1})"


def _tokenize(jql: str) -> list[_Tok]:
    out: list[_Tok] = []
    pos = 0
    while pos < len(jql):
        match = _TOKEN.match(jql, pos)
        if match is None:
            ch = jql[pos]
            if ch in "'\"":
                raise JqlError(f"Error in the JQL Query: The quoted string has not been completed. {_where(pos)}")
            raise JqlError(f"Error in the JQL Query: The character '{ch}' is a reserved JQL character. {_where(pos)}")
        kind = match.lastgroup
        text = match.group()
        if kind == "str":
            out.append(_Tok("str", re.sub(r"\\(.)", r"\1", text[1:-1]), pos))
        elif kind != "ws":
            out.append(_Tok(kind, text, pos))
        pos = match.end()
    return out


class _Parser:
    """query := [expr] [ORDER BY sort {, sort}]; expr := term {OR term}; term := factor {AND factor}
    factor := NOT factor | '(' expr ')' | clause."""

    def __init__(self, tokens: list[_Tok]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> _Tok:
        tok = self.peek()
        if tok is None:
            raise JqlError("Error in the JQL Query: Expecting more input but reached the end of the query.")
        self.i += 1
        return tok

    def expect_punc(self, ch: str) -> None:
        tok = self.take()
        if tok.kind != "punc" or tok.value != ch:
            raise JqlError(f"Error in the JQL Query: Expecting '{ch}' but got '{tok.value}'. {_where(tok.pos)}")

    def parse(self):
        tok = self.peek()
        where = None
        if tok is not None and not tok.word("ORDER"):
            where = self.expr()
        order: list[tuple[str, bool, int]] = []
        tok = self.peek()
        if tok is not None and tok.word("ORDER"):
            self.take()
            by = self.take()
            if not by.word("BY"):
                raise JqlError(f"Error in the JQL Query: Expecting 'BY' but got '{by.value}'. {_where(by.pos)}")
            order.append(self.sort_item())
            while (tok := self.peek()) is not None and tok.kind == "punc" and tok.value == ",":
                self.take()
                order.append(self.sort_item())
        tok = self.peek()
        if tok is not None:
            if order:
                raise JqlError(f"Error in the JQL Query: Expecting either ',' or the end of the query but got '{tok.value}'. {_where(tok.pos)}")
            raise JqlError(f"Error in the JQL Query: Expecting either 'OR' or 'AND' but got '{tok.value}'. {_where(tok.pos)}")
        return where, order

    def sort_item(self) -> tuple[str, bool, int]:
        tok = self.take()
        if tok.kind not in ("word", "str"):
            raise JqlError(f"Error in the JQL Query: Expecting a field name but got '{tok.value}'. {_where(tok.pos)}")
        desc = False
        nxt = self.peek()
        if nxt is not None and nxt.word("ASC", "DESC"):
            desc = self.take().value.upper() == "DESC"
        return tok.value, desc, tok.pos

    def expr(self):
        node = self.term()
        while (tok := self.peek()) is not None and tok.word("OR"):
            self.take()
            node = ("or", node, self.term())
        return node

    def term(self):
        node = self.factor()
        while (tok := self.peek()) is not None and tok.word("AND"):
            self.take()
            node = ("and", node, self.factor())
        return node

    def factor(self):
        tok = self.peek()
        if tok is None:
            raise JqlError("Error in the JQL Query: Expecting a field name but reached the end of the query.")
        if tok.word("NOT"):
            self.take()
            return ("not", self.factor())
        if tok.kind == "punc" and tok.value == "(":
            self.take()
            node = self.expr()
            self.expect_punc(")")
            return node
        return self.clause()

    def value(self):
        tok = self.take()
        if tok.kind == "str":
            return ("lit", tok.value)
        if tok.kind != "word" or tok.word("AND", "OR", "ORDER", "NOT"):
            raise JqlError(f"Error in the JQL Query: Expecting a value but got '{tok.value}'. {_where(tok.pos)}")
        nxt = self.peek()
        if nxt is not None and nxt.kind == "punc" and nxt.value == "(":
            self.take()
            args = []
            if not ((p := self.peek()) is not None and p.kind == "punc" and p.value == ")"):
                args.append(self.value()[1])
                while (p := self.peek()) is not None and p.kind == "punc" and p.value == ",":
                    self.take()
                    args.append(self.value()[1])
            self.expect_punc(")")
            return ("func", tok.value, args)
        return ("lit", tok.value)

    def clause(self):
        field = self.take()
        if field.kind not in ("word", "str") or field.word("AND", "OR", "ORDER", "BY"):
            raise JqlError(f"Error in the JQL Query: Expecting a field name but got '{field.value}'. {_where(field.pos)}")
        tok = self.take()
        if tok.word("NOT"):
            nxt = self.take()
            if not nxt.word("IN"):
                raise JqlError(f"Error in the JQL Query: Expecting 'IN' but got '{nxt.value}'. {_where(nxt.pos)}")
            return ("clause", field.value, "not in", self.in_values(), field.pos)
        if tok.word("IN"):
            return ("clause", field.value, "in", self.in_values(), field.pos)
        if tok.word("IS"):
            negate = False
            nxt = self.take()
            if nxt.word("NOT"):
                negate, nxt = True, self.take()
            if not nxt.word("EMPTY", "NULL"):
                raise JqlError(f"Error in the JQL Query: Expecting 'EMPTY' but got '{nxt.value}'. {_where(nxt.pos)}")
            return ("clause", field.value, "is not" if negate else "is", None, field.pos)
        if tok.kind != "op":
            raise JqlError(f"Error in the JQL Query: Expecting operator but got '{tok.value}'. {_where(tok.pos)}")
        return ("clause", field.value, tok.value, self.value(), field.pos)

    def in_values(self):
        tok = self.peek()
        if tok is not None and tok.kind == "punc" and tok.value == "(":
            self.take()
            values = [self.value()]
            while (p := self.peek()) is not None and p.kind == "punc" and p.value == ",":
                self.take()
                values.append(self.value())
            self.expect_punc(")")
            return ("list", values)
        return self.value()  # 함수: sprint in openSprints()


# ---------------------------------------------------------------- 절 → 판정 함수


class _Ctx:
    def __init__(self, mock: RunningMock):
        self.mock = mock
        self.now = mock.now()
        self.user = CURRENT_USER
        self.no_sprint = bool(mock.modes.get("no_sprint_field"))
        self.keys = set(mock.issues)


def _no_field(name: str) -> JqlError:
    return JqlError(f"Field '{name}' does not exist or you do not have permission to view it.")


def _bad_value(value: str, field: str) -> JqlError:
    return JqlError(f"The value '{value}' does not exist for the field '{field}'.")


def _sprints(issue: dict) -> list[dict]:
    out = []
    for raw in issue["fields"].get(SPRINT_FIELD) or []:
        body = re.search(r"\[(.*)\]\s*$", raw, re.DOTALL)
        attrs = {}
        if body:
            for part in re.split(r",(?=[A-Za-z][A-Za-z0-9_]*=)", body.group(1)):
                k, _, v = part.partition("=")
                attrs[k] = v
        out.append({"id": attrs.get("id"), "name": attrs.get("name"), "state": (attrs.get("state") or "").upper()})
    return out


def _literals(value, field: str) -> list:
    """("lit", x) 또는 ("list", [...]) → 값 목록. 함수는 ("func", 이름, 인수) 그대로."""
    if value is None:
        return []
    if value[0] == "list":
        return [item for v in value[1] for item in _literals(v, field)]
    return [value]


def _set_clause(field: str, op: str, value, getter: Callable[[dict], list[str]], norm: Callable[[Any], str]):
    """=, !=, in, not in, is [not] empty 를 값 집합으로 판정한다."""
    if op in ("is", "is not"):
        empty = op == "is"
        return lambda issue: (not getter(issue)) == empty
    if op not in ("=", "!=", "in", "not in"):
        raise JqlError(f"The operator '{op}' is not supported by the '{field}' field.")
    wanted = {norm(v) for v in _literals(value, field)}
    if op in ("=", "in"):
        return lambda issue: bool(wanted & set(getter(issue)))
    return lambda issue: bool(getter(issue)) and not (wanted & set(getter(issue)))


def _name_norm(field: str, known: Callable[[str], str | None]):
    def norm(v):
        if v[0] == "func":
            raise JqlError(f"Unable to find JQL function '{v[1]}()'.")
        out = known(v[1])
        if out is None:
            raise _bad_value(v[1], field)
        return out

    return norm


def _user_norm(field: str, ctx: _Ctx):
    def norm(v):
        if v[0] == "func":
            if v[1].lower() == "currentuser":
                return ctx.user
            raise JqlError(f"Unable to find JQL function '{v[1]}()'.")
        name = v[1].lower()
        for user in USERS.values():
            if name in (user["name"], user["displayName"].lower()):
                return user["name"]
        raise _bad_value(v[1], field)

    return norm


_REL = re.compile(r"^([+-]?)(\d+)([wdhm])$")


def _date_value(v, field: str, ctx: _Ctx) -> dt.datetime:
    if v[0] == "func":
        name, args = v[1].lower(), v[2]
        base = {"now": ctx.now, "startofday": ctx.now.replace(hour=0, minute=0, second=0, microsecond=0),
                "endofday": ctx.now.replace(hour=23, minute=59, second=59, microsecond=999000)}.get(name)  # fmt: skip
        if base is None:
            raise JqlError(f"Unable to find JQL function '{v[1]}()'.")
        if args:
            return base + _offset(args[0], field)
        return base
    text = v[1].strip()
    if _REL.match(text):
        return ctx.now + _offset(text, field)
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    raise JqlError(
        f"Date value '{text}' for field '{field}' is invalid. Valid formats include: 'yyyy/MM/dd HH:mm', "
        "'yyyy-MM-dd HH:mm', 'yyyy/MM/dd', 'yyyy-MM-dd', or a period format e.g. '-5d', '4w 2d'."
    )


def _offset(text: str, field: str) -> dt.timedelta:
    match = _REL.match(text.strip())
    if match is None:
        raise JqlError(f"Date value '{text}' for field '{field}' is invalid.")
    sign = -1 if match.group(1) == "-" else 1
    amount = int(match.group(2)) * sign
    unit = {"w": "weeks", "d": "days", "h": "hours", "m": "minutes"}[match.group(3)]
    return dt.timedelta(**{unit: amount})


def _date_clause(field: str, op: str, value, getter: Callable[[dict], dt.datetime | None], ctx: _Ctx):
    if op in ("is", "is not"):
        empty = op == "is"
        return lambda issue: (getter(issue) is None) == empty
    compare = {
        "=": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    }.get(op)  # fmt: skip
    if compare is None or value[0] == "list":
        raise JqlError(f"The operator '{op}' is not supported by the '{field}' field.")
    bound = _date_value(value, field, ctx)

    def pred(issue):
        got = getter(issue)
        return got is not None and compare(got, bound)

    return pred


def _due(issue: dict) -> dt.datetime | None:
    raw = issue["fields"].get("duedate")
    return dt.datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=TZ) if raw else None


def _stamp(name: str):
    def get(issue):
        raw = issue["fields"].get(name)
        return parse_time(raw) if raw else None

    return get


def _text_clause(field: str, op: str, value, getter: Callable[[dict], str]):
    if op not in ("~", "!~") or value[0] != "lit":
        raise JqlError(f"The operator '{op}' is not supported by the '{field}' field.")
    needle = value[1].lower().strip("*")
    if op == "~":
        return lambda issue: needle in getter(issue).lower()
    return lambda issue: needle not in getter(issue).lower()


def _key_known(ctx: _Ctx, field: str):
    def known(text: str) -> str | None:
        key = text.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key):
            raise JqlError(f"The issue key '{text}' for field '{field}' is invalid.")
        current = ctx.mock.resolve(key)  # 옮겨진 옛 키는 새 이슈를 찾는다
        if current in ctx.keys:
            return current
        if key not in ctx.keys:
            raise JqlError(f"An issue with key '{key}' does not exist for field '{field}'.")
        return key

    return known


def _project_known(text: str) -> str | None:
    for project in PROJECTS:
        if text.upper() == project["key"] or text == project["name"]:
            return project["key"]
    return None


def _sprint_clause(field: str, op: str, value, ctx: _Ctx):
    if ctx.no_sprint:
        raise _no_field(field)
    if op in ("is", "is not"):
        empty = op == "is"
        return lambda issue: (not _sprints(issue)) == empty
    if op not in ("=", "!=", "in", "not in"):
        raise JqlError(f"The operator '{op}' is not supported by the '{field}' field.")
    tests: list[Callable[[dict], bool]] = []
    for v in _literals(value, field):
        if v[0] == "func":
            states = {"opensprints": {"ACTIVE", "FUTURE"}, "closedsprints": {"CLOSED"}, "futuresprints": {"FUTURE"}}.get(v[1].lower())
            if states is None:
                raise JqlError(f"Unable to find JQL function '{v[1]}()'.")
            tests.append(lambda s, states=states: s["state"] in states)
        else:
            text = v[1]
            tests.append(lambda s, text=text: s["id"] == text or (s["name"] or "").lower() == text.lower())

    def hit(issue):
        return any(t(s) for s in _sprints(issue) for t in tests)

    if op in ("=", "in"):
        return hit
    return lambda issue: bool(_sprints(issue)) and not hit(issue)


def _clause(node, ctx: _Ctx):
    _, field, op, value, _pos = node
    name = field.lower()
    if name == "project":
        return _set_clause(field, op, value, lambda i: [i["fields"]["project"]["key"]], _name_norm(field, _project_known))
    if name in ("key", "issuekey", "issue", "id"):
        return _set_clause(field, op, value, lambda i: [i["key"]], _name_norm(field, _key_known(ctx, field)))
    if name == "status":
        return _set_clause(field, op, value, lambda i: [i["fields"]["status"]["name"].lower()],
                           _name_norm(field, lambda t: t.lower() if t.lower() in STATUSES else None))  # fmt: skip
    if name in ("statuscategory", "category"):
        return _set_clause(field, op, value, lambda i: [i["fields"]["status"]["statusCategory"]["key"]],
                           _name_norm(field, lambda t: CATEGORY_ALIASES.get(t.lower())))  # fmt: skip
    if name in ("assignee", "reporter"):
        def users(issue, name=name):
            person = issue["fields"].get(name)
            return [person["name"]] if person else []

        return _set_clause(field, op, value, users, _user_norm(field, ctx))
    if name == "watcher":
        return _set_clause(field, op, value, lambda i: [ctx.user] if i["fields"]["watches"]["isWatching"] else [], _user_norm(field, ctx))
    if name in ("labels", "label"):
        return _set_clause(field, op, value, lambda i: [x.lower() for x in i["fields"].get("labels") or []],
                           _name_norm(field, lambda t: t.lower()))  # fmt: skip
    if name in ("component", "components"):
        return _set_clause(field, op, value, lambda i: [c["name"].lower() for c in i["fields"].get("components") or []],
                           _name_norm(field, lambda t: t.lower()))  # fmt: skip
    if name == "priority":
        return _set_clause(field, op, value, lambda i: [i["fields"]["priority"]["name"].lower()], _name_norm(field, lambda t: t.lower()))
    if name in ("issuetype", "type"):
        return _set_clause(field, op, value, lambda i: [i["fields"]["issuetype"]["name"].lower()], _name_norm(field, lambda t: t.lower()))
    if name == "sprint":
        return _sprint_clause(field, op, value, ctx)
    if name in ("updated", "updateddate"):
        return _date_clause(field, op, value, _stamp("updated"), ctx)
    if name in ("created", "createddate"):
        return _date_clause(field, op, value, _stamp("created"), ctx)
    if name in ("duedate", "due"):
        return _date_clause(field, op, value, _due, ctx)
    if name in ("summary", "description", "text"):
        def text(issue, name=name):
            f = issue["fields"]
            if name == "text":
                return f"{f.get('summary') or ''}\n{f.get('description') or ''}"
            return f.get(name) or ""

        return _text_clause(field, op, value, text)
    raise _no_field(field)


def _compile(node, ctx: _Ctx) -> Callable[[dict], bool]:
    if node is None:
        return lambda issue: True
    kind = node[0]
    if kind == "and":
        left, right = _compile(node[1], ctx), _compile(node[2], ctx)
        return lambda issue: left(issue) and right(issue)
    if kind == "or":
        left, right = _compile(node[1], ctx), _compile(node[2], ctx)
        return lambda issue: left(issue) or right(issue)
    if kind == "not":
        inner = _compile(node[1], ctx)
        return lambda issue: not inner(issue)
    return _clause(node, ctx)


def _key_order(issue: dict):
    project, _, number = issue["key"].partition("-")
    return (project, int(number))


_SORTS: dict[str, Callable[[dict], Any]] = {
    "key": _key_order,
    "issuekey": _key_order,
    "updated": lambda i: parse_time(i["fields"]["updated"]),
    "created": lambda i: parse_time(i["fields"]["created"]),
    "duedate": lambda i: i["fields"].get("duedate") or "",
    "due": lambda i: i["fields"].get("duedate") or "",
}


def evaluate_jql(mock: RunningMock, jql: str) -> list[dict]:
    """JQL에 맞는 이슈(픽스처 원본 dict)를 정렬해 돌려준다. 틀리면 JqlError."""
    where, order = _Parser(_tokenize(jql or "")).parse()
    ctx = _Ctx(mock)
    pred = _compile(where, ctx)
    for name, _desc, _pos in order:
        if name.lower() not in _SORTS:
            raise _no_field(name)
    with mock._lock:
        found = [copy.deepcopy(issue) for issue in mock.issues.values()]
    found = [issue for issue in found if pred(issue)]
    if not order:
        order = [("updated", True, 0)]  # 정렬을 주지 않으면 키 순서가 아니다(클라이언트가 붙여야 한다)
    for name, desc, _pos in reversed(order):
        found.sort(key=_SORTS[name.lower()], reverse=desc)
    return found


# ---------------------------------------------------------------- 응답 만들기


def _sprint_object(raw: str) -> dict:
    body = re.search(r"\[(.*)\]\s*$", raw, re.DOTALL)
    attrs: dict[str, str] = {}
    if body:
        for part in re.split(r",(?=[A-Za-z][A-Za-z0-9_]*=)", body.group(1)):
            k, _, v = part.partition("=")
            attrs[k] = v
    out: dict[str, Any] = {"id": int(attrs["id"]) if attrs.get("id", "").isdigit() else attrs.get("id"), "name": attrs.get("name"),
                           "state": (attrs.get("state") or "").lower(), "boardId": int(attrs.get("rapidViewId") or 0)}  # fmt: skip
    for key in ("startDate", "endDate", "completeDate", "goal"):
        value = attrs.get(key)
        if value not in (None, "", "<null>"):
            out[key] = value
    return out


def _wanted_fields(raw: str | None, all_names: list[str], *, navigable_default: bool) -> list[str]:
    navigable = [n for n in all_names if n != "comment"]
    if raw is None or raw.strip() == "":
        return navigable if navigable_default else list(all_names)
    include: list[str] = []
    exclude: set[str] = set()
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if part == "*all":
            include.extend(all_names)
        elif part == "*navigable":
            include.extend(navigable)
        elif part.startswith("-"):
            exclude.add(part[1:])
        else:
            include.append(part)
    if not include:
        include = navigable if navigable_default else list(all_names)
    seen: list[str] = []
    for name in include:
        if name in all_names and name not in exclude and name not in seen:
            seen.append(name)
    return seen


def render_issue(mock: RunningMock, issue: dict, fields_param: str | None, *, search: bool) -> dict:
    fields = copy.deepcopy(issue["fields"])
    if mock.modes.get("no_sprint_field"):
        fields.pop(SPRINT_FIELD, None)
    elif mock.modes.get("sprint_form") == "object" and fields.get(SPRINT_FIELD):
        fields[SPRINT_FIELD] = [_sprint_object(raw) for raw in fields[SPRINT_FIELD]]
    if search and isinstance(fields.get("comment"), dict):
        limit = int(mock.modes.get("search_comment_limit", DEFAULT_SEARCH_COMMENT_LIMIT))
        comment = fields["comment"]
        comment["comments"] = comment["comments"][:limit]
        comment["maxResults"] = limit
    names = _wanted_fields(fields_param, list(fields), navigable_default=search)
    return {
        "expand": "operations,versionedRepresentations,editmeta,changelog,renderedFields",
        "id": issue["id"],
        "self": f"{mock.base_url}/rest/api/2/issue/{issue['id']}",
        "key": issue["key"],
        "fields": {name: fields[name] for name in names},
    }


def _int_param(req: Request, name: str, default: int) -> int:
    raw = req.query.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise JqlError(f"'{name}' 값이 정수가 아닙니다: {raw}") from None


def _cap(req: Request, wanted: int) -> int:
    cap = req.mock.modes.get("page_size_cap")
    limit = min(SERVER_MAX_RESULTS, int(cap)) if cap else SERVER_MAX_RESULTS
    return max(0, min(wanted, limit))


def _error(status: int, *messages: str):
    return status, {"errorMessages": list(messages), "errors": {}}


# ---------------------------------------------------------------- Jira 동작


def _myself(req: Request):
    return 200, {"name": "honggildong", "key": "honggildong", "displayName": "홍길동", "emailAddress": "hong@example.invalid", "timeZone": "Asia/Seoul"}


def _projects(req: Request):
    return 200, [{"id": "10000", "key": "KB", "name": "지식 저장고"}, {"id": "10001", "key": "OPS", "name": "운영"}]


def _server_info(req: Request):
    return 200, {"baseUrl": req.mock.base_url, "version": "9.17.0", "deploymentType": "Server"}


def _fields(req: Request):
    items = load_fixture("fields")
    if req.mock.modes.get("no_sprint_field"):
        items = [f for f in items if (f.get("schema") or {}).get("custom") != SPRINT_SCHEMA]
    return 200, items


def _search(req: Request):
    mock = req.mock
    calls = mock.bump("search")
    limit = int(mock.modes.get("rate_limit") or 0)
    if calls <= limit:
        return 429, {"errorMessages": ["요청이 너무 많습니다. 잠시 뒤 다시 시도하세요."]}, {"Retry-After": "1"}
    if mock.modes.get("search_500_once") and mock.once("search_500"):
        return _error(500, "내부 서버 오류 (모의)")
    try:
        start = max(0, _int_param(req, "startAt", 0))
        wanted = _int_param(req, "maxResults", DEFAULT_MAX_RESULTS)
        found = evaluate_jql(mock, req.query.get("jql", ""))
    except JqlError as exc:
        return _error(400, str(exc))
    max_results = _cap(req, wanted)
    shifted = start > 0 and bool(mock.modes.get("repeat_page_boundary"))
    first = start - 1 if shifted else start
    page = found[first : first + max_results] if max_results else []
    return 200, {
        "expand": "schema,names",
        "startAt": start,
        "maxResults": max_results,
        "total": len(found) + (1 if shifted else 0),  # 앞에 하나가 끼어든 것처럼(그래서 한 칸 밀림)
        "issues": [render_issue(mock, issue, req.query.get("fields"), search=True) for issue in page],
    }


def _find_issue(req: Request) -> dict | None:
    wanted = req.mock.resolve(req.params["key"]) if not req.params["key"].isdigit() else req.params["key"]
    with req.mock._lock:
        for issue in req.mock.issues.values():
            if issue["key"] == wanted.upper() or issue["id"] == wanted:
                return copy.deepcopy(issue)
    return None


def _issue(req: Request):
    issue = _find_issue(req)
    if issue is None:
        return _error(404, "이슈가 존재하지 않거나 볼 권한이 없습니다.")
    return 200, render_issue(req.mock, issue, req.query.get("fields"), search=False)


def _comments(req: Request):
    issue = _find_issue(req)
    if issue is None:
        return _error(404, "이슈가 존재하지 않거나 볼 권한이 없습니다.")
    try:
        start = max(0, _int_param(req, "startAt", 0))
        max_results = _cap(req, _int_param(req, "maxResults", DEFAULT_MAX_RESULTS))
    except JqlError as exc:
        return _error(400, str(exc))
    items = issue["fields"]["comment"]["comments"]
    return 200, {"startAt": start, "maxResults": max_results, "total": len(items), "comments": items[start : start + max_results]}


# ---------------------------------------------------------------- Phase 3 쓰기

WRITE_METHODS = frozenset({"POST", "PUT"})
STARTED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{4}$")
CATEGORIES = {
    "new": {"id": 2, "key": "new", "colorName": "blue-gray", "name": "To Do"},
    "indeterminate": {"id": 4, "key": "indeterminate", "colorName": "yellow", "name": "In Progress"},
    "done": {"id": 3, "key": "done", "colorName": "green", "name": "Done"},
}
STATUS_OBJECTS = {
    "To Do": ("10000", "new"),
    "In Progress": ("3", "indeterminate"),
    "In Review": ("10101", "indeterminate"),
    "Blocked": ("10102", "indeterminate"),
    "Done": ("10001", "done"),
    "Closed": ("6", "done"),
}
# 지금 상태(소문자) → [(전환 id, 전환 이름, 도착 상태)]. In Review의 'Done' 두 개는 후보 여럿 시험용.
TRANSITIONS: dict[str, list[tuple[str, str, str]]] = {
    "to do": [("11", "In Progress", "In Progress"), ("12", "Blocked", "Blocked")],
    "open": [("11", "In Progress", "In Progress")],
    "in progress": [("21", "In Review", "In Review"), ("22", "Blocked", "Blocked"), ("31", "Done", "Done")],
    "in review": [("41", "Done", "Done"), ("42", "Done", "Closed"), ("43", "In Progress", "In Progress")],
    "blocked": [("51", "In Progress", "In Progress")],
    "done": [("61", "Reopen", "To Do")],
    "closed": [("61", "Reopen", "To Do")],
}
ISSUE_TYPES = {"Task": "10002", "Story": "10001", "Bug": "10004"}


def status_object(name: str) -> dict:
    sid, category = STATUS_OBJECTS[name]
    return {"id": sid, "name": name, "statusCategory": dict(CATEGORIES[category])}


def _user(name: str) -> dict:
    user = USERS[name]
    return {**user, "emailAddress": f"{name}@example.invalid", "active": True}


def _write_gate(req: Request):
    """모든 쓰기 경로의 앞단: 훅, 횟수, write_500_times. 막으면 응답을, 통과면 None."""
    mock = req.mock
    hook = mock.modes.get("on_write")
    calls = mock.bump("write")
    if callable(hook):
        answer = hook(req)
        if answer is not None:  # 훅이 (상태, 본문[, 헤더])를 주면 그것으로 응답한다
            return answer
    if calls <= int(mock.modes.get("write_500_times") or 0):
        return _error(500, "내부 서버 오류 (모의 쓰기)")
    return None


def _current_key(req: Request) -> str | None:
    """경로의 키(옮겨졌으면 새 키)가 가리키는 이슈의 지금 키. 없으면 None."""
    raw = req.params["key"]
    with req.mock._lock:
        if raw.isdigit():
            return next((k for k, v in req.mock.issues.items() if v["id"] == raw), None)
    wanted = req.mock.resolve(raw)
    with req.mock._lock:
        return wanted if wanted in req.mock.issues else None


def _no_issue():
    return _error(404, "이슈가 존재하지 않거나 볼 권한이 없습니다.")


def _add_comment(req: Request):
    blocked = _write_gate(req)
    if blocked:
        return blocked
    key = _current_key(req)
    if key is None:
        return _no_issue()
    body = req.json.get("body") if isinstance(req.json, dict) else None
    if not isinstance(body, str) or not body.strip():
        return 400, {"errorMessages": [], "errors": {"comment": "Comment body can not be empty!"}}
    mock = req.mock
    stamp = mock.touch(key)
    number = 90000 + mock.bump("comment_id")
    with mock._lock:
        comment_field = mock.issues[key]["fields"]["comment"]
        comment = {
            "self": f"{mock.base_url}/rest/api/2/issue/{mock.issues[key]['id']}/comment/{number}",
            "id": str(number),
            "author": _user(CURRENT_USER),
            "body": body,
            "updateAuthor": _user(CURRENT_USER),
            "created": stamp,
            "updated": stamp,
        }
        comment_field["comments"].append(copy.deepcopy(comment))
        comment_field["total"] = len(comment_field["comments"])
        comment_field["maxResults"] = len(comment_field["comments"])
    return 201, comment


def _available(issue: dict) -> list[tuple[str, str, str]]:
    return TRANSITIONS.get(issue["fields"]["status"]["name"].lower(), [])


def _transitions(req: Request):
    key = _current_key(req)
    if key is None:
        return _no_issue()
    with req.mock._lock:
        options = _available(req.mock.issues[key])
    items = [
        {"id": tid, "name": name, "opsbarSequence": 10 * (i + 1), "to": status_object(target),
         "hasScreen": False, "isGlobal": False, "isInitial": False, "isConditional": False}
        for i, (tid, name, target) in enumerate(options)
    ]  # fmt: skip
    return 200, {"expand": "transitions", "transitions": items}


def _do_transition(req: Request):
    blocked = _write_gate(req)
    if blocked:
        return blocked
    key = _current_key(req)
    if key is None:
        return _no_issue()
    if req.mock.modes.get("transition_500"):
        return _error(500, "내부 서버 오류 (모의 전환)")
    transition = req.json.get("transition") if isinstance(req.json, dict) else None
    tid = transition.get("id") if isinstance(transition, dict) else None
    if not tid:
        return _error(400, "Transition id or name must be specified.")
    with req.mock._lock:
        options = _available(req.mock.issues[key])
    target = next((to for i, _name, to in options if i == str(tid)), None)
    if target is None:
        return _error(500, f"java.lang.NullPointerException (전환 {tid}은(는) 지금 상태에서 할 수 없음)")
    req.mock.touch(key, status=status_object(target))
    return 204, None


def _assign(req: Request):
    blocked = _write_gate(req)
    if blocked:
        return blocked
    key = _current_key(req)
    if key is None:
        return _no_issue()
    if req.mock.modes.get("assign_denied"):
        return 401, {"errorMessages": ["You do not have permission to assign issues."], "errors": {}}
    name = req.json.get("name") if isinstance(req.json, dict) else None
    if not isinstance(name, str) or name not in USERS:
        return 400, {"errorMessages": [], "errors": {"assignee": f"User '{name}' does not exist."}}
    req.mock.touch(key, assignee=_user(name))
    return 204, None


def _worklog(req: Request):
    blocked = _write_gate(req)
    if blocked:
        return blocked
    key = _current_key(req)
    if key is None:
        return _no_issue()
    if req.mock.modes.get("worklog_denied"):
        return 403, {"errorMessages": ["You do not have the permission to associate a worklog to this issue."], "errors": {}}
    body = req.json if isinstance(req.json, dict) else {}
    seconds = body.get("timeSpentSeconds")
    started = body.get("started")
    errors = {}
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        errors["timeLogged"] = "You must indicate the time spent working."
    if not isinstance(started, str) or not STARTED_RE.match(started):
        errors["started"] = "You must specify a valid date (yyyy-MM-dd'T'HH:mm:ss.SSSZ)."
    if errors:
        return 400, {"errorMessages": [], "errors": errors}
    mock = req.mock
    mock.touch(key)
    number = 70000 + mock.bump("worklog_id")
    with mock._lock:
        item = {
            "id": str(number),
            "issueId": mock.issues[key]["id"],
            "author": _user(CURRENT_USER),
            "comment": body.get("comment") or "",
            "started": started,
            "timeSpentSeconds": seconds,
            "timeSpent": f"{seconds // 3600}h {seconds % 3600 // 60}m",
        }
        mock.worklogs.setdefault(key, []).append(copy.deepcopy(item))
    return 201, item


def _create_issue(req: Request):
    blocked = _write_gate(req)
    if blocked:
        return blocked
    fields = req.json.get("fields") if isinstance(req.json, dict) else None
    if not isinstance(fields, dict):
        return _error(400, "fields가 필요합니다.")
    errors: dict[str, str] = {}
    project_ref = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    project = next((p for p in PROJECTS if p["key"] == project_ref.get("key") or p["id"] == project_ref.get("id")), None)
    if project is None:
        errors["project"] = "project is required"
    summary = fields.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors["summary"] = "You must specify a summary of the issue."
    type_ref = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    type_name = next((n for n, i in ISSUE_TYPES.items() if n == type_ref.get("name") or i == type_ref.get("id")), None)
    if type_name is None:
        errors["issuetype"] = "issue type is required" if not type_ref else "The issue type selected is invalid."
    if errors:
        return 400, {"errorMessages": [], "errors": errors}
    mock = req.mock
    stamp = format_time(mock.now() + dt.timedelta(minutes=1))
    assignee = fields.get("assignee") if isinstance(fields.get("assignee"), dict) else None
    with mock._lock:
        taken = [int(k.split("-", 1)[1]) for k in [*mock.issues, *mock.moved] if k.split("-", 1)[0] == project["key"]]
        key = f"{project['key']}-{max(taken, default=0) + 1}"
        new_id = str(max(int(v["id"]) for v in mock.issues.values()) + 1)
        issue = {
            "id": new_id,
            "key": key,
            "fields": {
                "summary": summary.strip(),
                "issuetype": {"id": ISSUE_TYPES[type_name], "name": type_name, "subtask": False},
                "project": dict(project),
                "status": status_object("To Do"),
                "priority": {"id": "3", "name": "Medium"},
                "assignee": _user(assignee["name"]) if assignee and assignee.get("name") in USERS else None,
                "reporter": _user(CURRENT_USER),
                "creator": _user(CURRENT_USER),
                "labels": list(fields.get("labels") or []),
                "components": [],
                "created": stamp,
                "updated": stamp,
                "duedate": fields.get("duedate"),
                "resolution": None,
                "resolutiondate": None,
                "description": fields.get("description"),
                SPRINT_FIELD: None,
                "watches": {"watchCount": 1, "isWatching": True},
                "comment": {"comments": [], "maxResults": 0, "total": 0, "startAt": 0},
            },
        }
        mock.issues[key] = issue
    return 201, {"id": new_id, "key": key, "self": f"{mock.base_url}/rest/api/2/issue/{new_id}"}


ROUTES = [
    ("GET", r"/rest/api/2/myself", _myself),
    ("GET", r"/rest/api/2/project", _projects),
    ("GET", r"/rest/api/2/serverInfo", _server_info),
    ("GET", r"/rest/api/2/field", _fields),
    ("GET", r"/rest/api/2/search", _search),
    ("GET", r"/rest/api/2/issue/(?P<key>[^/]+)/comment", _comments),
    ("GET", r"/rest/api/2/issue/(?P<key>[^/]+)/transitions", _transitions),
    ("GET", r"/rest/api/2/issue/(?P<key>[^/]+)", _issue),
    # Phase 3 쓰기
    ("POST", r"/rest/api/2/issue/(?P<key>[^/]+)/comment", _add_comment),
    ("POST", r"/rest/api/2/issue/(?P<key>[^/]+)/transitions", _do_transition),
    ("PUT", r"/rest/api/2/issue/(?P<key>[^/]+)/assignee", _assign),
    ("POST", r"/rest/api/2/issue/(?P<key>[^/]+)/worklog", _worklog),
    ("POST", r"/rest/api/2/issue", _create_issue),
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
        return 401, {"errorMessages": ["인증이 필요합니다"]}
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
