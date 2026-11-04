"""Jira Data Center REST v2 읽기 클라이언트 (Phase 2, D49·D51·D54).

- 인증은 Bearer PAT. 모든 HTTP는 `net.session_for`·`net.request`를 거친다(429 재시도는 net이 한다).
- 401/403 → AuthError(종료 2), 연결 실패 → NetworkError(3), 검색 400 → JqlError(4).
- 읽기(GET)가 5xx면 1초 쉬고 한 번만 다시 묻는다. 그래도 5xx면 NetworkError.
- `search`는 `startAt`을 받은 이슈 수만큼 늘리며 페이지를 넘긴다. 최상위 ORDER BY가 없으면
  `ORDER BY key ASC`를 붙여 페이지 사이 순서를 고정한다(`split_order_by`).
- 스프린트 필드 id는 `/rest/api/2/field`에서 schema.custom으로 찾는다(인스턴스마다 다르다).

Jira 쓰기(댓글·전환·생성)는 Phase 3에서 더한다.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping
from urllib.parse import quote

import requests

from lib import core, net
from lib import secrets as kb_secrets

if TYPE_CHECKING:
    from lib.config import Config

API = "/rest/api/2"
SPRINT_SCHEMA = "com.pyxis.greenhopper.jira:gh-sprint"
DEFAULT_ORDER = "key ASC"
ISSUE_FIELDS: tuple[str, ...] = (
    "summary",
    "status",
    "issuetype",
    "priority",
    "assignee",
    "reporter",
    "project",
    "labels",
    "components",
    "created",
    "updated",
    "duedate",
    "resolution",
    "description",
    "comment",
)
"""jira_sync가 이슈 파일(§5.3)을 만드는 데 쓰는 필드. 스프린트 필드는 `sync_fields()`가 더한다."""

RETRY_5XX_WAIT = 1.0
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-[1-9][0-9]*$")
_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)
_UNSET: Any = object()


class JqlError(core.ConfigError):
    """Jira가 JQL을 거부했다(400). `messages`에 Jira가 준 문장들이 있다."""

    def __init__(self, message: str, *, jql: str = "", messages: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.jql = jql
        self.messages = list(messages)


class NotFoundError(core.KbError):
    """이슈가 없거나 볼 권한이 없다(404)."""

    status = 404


# ---------------------------------------------------------------- JQL 도움 함수


def _mask_nested(jql: str) -> str:
    """따옴표 안과 괄호 안의 글자를 공백으로 바꾼 같은 길이의 문자열."""
    out: list[str] = []
    quote_ch: str | None = None
    escaped = False
    depth = 0
    for ch in jql:
        if quote_ch is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote_ch:
                quote_ch = None
            out.append(" ")
        elif ch in ("'", '"'):
            quote_ch = ch
            out.append(" ")
        elif ch == "(":
            depth += 1
            out.append(" ")
        elif ch == ")":
            depth = max(depth - 1, 0)
            out.append(" ")
        else:
            out.append(ch if depth == 0 else " ")
    return "".join(out)


def split_order_by(jql: str) -> tuple[str, str | None]:
    """(조건, 정렬)로 나눈다. 따옴표·괄호 밖의 첫 ORDER BY만 본다. 없으면 정렬은 None.

    정렬 부분은 ORDER BY 뒤의 글자 그대로다(두 번째 ORDER BY가 있어도 손대지 않는다).
    """
    text = jql or ""
    match = _ORDER_BY.search(_mask_nested(text))
    if match is None:
        return text.strip(), None
    return text[: match.start()].strip(), text[match.end() :].strip()


def with_default_order(jql: str) -> str:
    """최상위 ORDER BY가 없을 때만 `ORDER BY key ASC`를 붙인다."""
    where, order = split_order_by(jql)
    if order is not None:
        return (jql or "").strip()
    return f"{where} ORDER BY {DEFAULT_ORDER}" if where else f"ORDER BY {DEFAULT_ORDER}"


# ---------------------------------------------------------------- 값 해석

_SPRINT_BODY = re.compile(r"\[(.*)\]\s*$", re.DOTALL)
_SPRINT_SPLIT = re.compile(r",(?=[A-Za-z][A-Za-z0-9_]*=)")


def _sprint_entry(value: Any) -> tuple[str, str] | None:
    """스프린트 값 하나 → (이름, 상태 대문자). 해석하지 못하면 None."""
    if isinstance(value, Mapping):
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        return name.strip(), str(value.get("state") or "").strip().upper()
    if isinstance(value, str):
        match = _SPRINT_BODY.search(value)
        if match is None:
            # 대괄호 없는 문자열: 객체 표현이 아니면 이름 그대로로 본다
            text = value.strip()
            return (text, "") if text and "@" not in text else None
        attrs: dict[str, str] = {}
        for part in _SPRINT_SPLIT.split(match.group(1)):
            key, sep, val = part.partition("=")
            if sep:
                attrs[key.strip()] = val
        name = (attrs.get("name") or "").strip()
        if not name or name == "<null>":
            return None
        return name, (attrs.get("state") or "").strip().upper()
    return None


def parse_sprint(value: Any) -> str | None:
    """스프린트 필드 값 → 스프린트 이름. ACTIVE가 있으면 그중 마지막, 없으면 목록의 마지막.

    DC 문자열 꼴(`com.atlassian.greenhopper.service.sprint.Sprint@…[id=5,…,state=ACTIVE,name=…]`)과
    객체 꼴(`{"id": 5, "name": …, "state": "active"}`)을 모두 받는다. 하나든 목록이든 된다.
    """
    if value is None:
        return None
    items = value if isinstance(value, (list, tuple)) else [value]
    entries = [entry for entry in (_sprint_entry(item) for item in items) if entry is not None]
    if not entries:
        return None
    active = [entry for entry in entries if entry[1] == "ACTIVE"]
    return (active or entries)[-1][0]


def parse_datetime(value: Any) -> dt.datetime | None:
    """Jira 시각(`2026-11-04T16:05:11.000+0900`) → 시간대 있는 datetime. 해석 못 하면 None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# ---------------------------------------------------------------- 응답 해석


def _jira_messages(resp: requests.Response) -> list[str]:
    """Jira 오류 응답 `{errorMessages: [...], errors: {필드: 문장}}`의 문장들."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    out: list[str] = []
    if isinstance(data, Mapping):
        for message in data.get("errorMessages") or []:
            if isinstance(message, str) and message.strip():
                out.append(message.strip())
        errors = data.get("errors")
        if isinstance(errors, Mapping):
            for name, message in errors.items():
                if isinstance(message, str) and message.strip():
                    out.append(f"{name}: {message.strip()}")
    if not out:
        try:
            text = " ".join(resp.text.split())
        except Exception:  # 표시용이므로 디코딩 실패는 넘어간다
            text = ""
        if text:
            out.append(text[:200] + ("…" if len(text) > 200 else ""))
    return [core.redact(message) for message in out]


def _page_size(value: Any) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = 0
    if isinstance(value, bool) or size < 1:
        raise core.ConfigError(f"jira.max_results: 1 이상의 정수여야 합니다 (지금 값: {value!r})")
    return size


def _short_jql(jql: str, limit: int = 200) -> str:
    text = " ".join((jql or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- 클라이언트


class JiraClient:
    """Jira DC REST v2 읽기 전용 클라이언트. `with JiraClient(...) as client:`로 쓸 수 있다."""

    def __init__(self, base_url: str, pat: str, *, conn: Mapping | None = None, page_size: int = 500) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise core.ConfigError("jira.base_url이 비어 있습니다. `python scripts/kb.py init --section jira`로 설정하세요")
        conn = dict(conn or {})
        self.base_url = base_url.strip().rstrip("/")
        self.page_size = _page_size(page_size)
        try:
            self.timeout = float(conn.get("timeout") or 30)
        except (TypeError, ValueError):
            raise core.ConfigError(f"jira.timeout: 초 단위 숫자여야 합니다 (지금 값: {conn.get('timeout')!r})") from None
        self._session = net.session_for(conn, pat)
        self._log_root: Any = None
        self._fields: list[dict] | None = None
        self._sprint_field: Any = _UNSET

    @classmethod
    def from_config(cls, cfg: "Config") -> "JiraClient":
        """설정의 jira 구역으로 만든다. PAT는 `jira.pat_ref`를 secrets.resolve로 푼다.

        `jira.sprint_field`가 설정에 있으면 스프린트 필드 탐지를 건너뛰고 그 값을 쓴다(D51).
        """
        jira = cfg.get("jira") or {}
        if not isinstance(jira, Mapping):
            raise core.ConfigError("jira: '키: 값' 묶음이어야 합니다")
        base_url = jira.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise core.ConfigError("jira.base_url이 설정되지 않았습니다. `python scripts/kb.py init --section jira`로 설정하세요")
        ref = jira.get("pat_ref")
        pat = kb_secrets.resolve(ref, cfg)
        if not pat:
            raise core.AuthError(kb_secrets.describe_missing(ref))
        client = cls(base_url, pat, conn=jira, page_size=jira.get("max_results") or 500)
        client._log_root = cfg.root
        preset = jira.get("sprint_field")
        if isinstance(preset, str) and preset.strip():
            client._sprint_field = preset.strip()
        return client

    # ------------------------------------------------------------ 수명

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ HTTP

    def _url(self, path: str) -> str:
        return f"{self.base_url}{API}{path}"

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> requests.Response:
        """GET 하나. 5xx면 한 번만 다시 묻는다(읽기는 멱등)."""
        url = self._url(path)
        for attempt in (0, 1):
            resp = net.request(self._session, "GET", url, params=dict(params or {}), timeout=self.timeout, root=self._log_root)
            if resp.status_code < 500 or attempt:
                return resp
            resp.close()
            net._sleep(RETRY_5XX_WAIT)
        raise AssertionError("도달하지 않음")  # pragma: no cover

    def _json(self, resp: requests.Response, what: str) -> Any:
        """2xx면 JSON, 아니면 알맞은 KbError."""
        status = resp.status_code
        with resp:
            if 200 <= status < 300:
                try:
                    return resp.json()
                except ValueError:
                    raise core.KbError(
                        f"Jira 응답이 JSON이 아닙니다 ({status}, {what}). base_url이 Jira 서버 주소인지 확인하세요"
                    ) from None
            detail = "; ".join(_jira_messages(resp))
        suffix = f": {detail}" if detail else ""
        exc: core.KbError
        if status == 404:
            exc = NotFoundError(f"Jira에 없거나 볼 권한이 없습니다 ({what}){suffix}")
        elif status == 429:
            exc = core.NetworkError(f"Jira 요청 제한(429)이 계속됩니다 ({what}). 잠시 뒤 다시 시도하세요")
        elif status >= 500:
            exc = core.NetworkError(f"Jira 서버 오류입니다 ({status}, {what}){suffix}. 잠시 뒤 다시 시도하세요")
        else:
            exc = core.KbError(f"Jira 요청이 실패했습니다 ({status}, {what}){suffix}")
        exc.status = status  # type: ignore[attr-defined]
        raise exc

    def _get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._json(self._get(path, params), f"GET {API}{path}")

    # ------------------------------------------------------------ 단순 조회

    def myself(self) -> dict:
        data = self._get_json("/myself")
        if not isinstance(data, dict):
            raise core.KbError("Jira /myself 응답 형식이 아닙니다")
        return data

    def projects(self) -> list[dict]:
        data = self._get_json("/project")
        if not isinstance(data, list):
            raise core.KbError("Jira /project 응답이 목록이 아닙니다")
        return [item for item in data if isinstance(item, dict)]

    def fields(self) -> list[dict]:
        """`/rest/api/2/field` 목록. 인스턴스 안에 저장해 두고 다시 묻지 않는다."""
        if self._fields is None:
            data = self._get_json("/field")
            if not isinstance(data, list):
                raise core.KbError("Jira /field 응답이 목록이 아닙니다")
            self._fields = [item for item in data if isinstance(item, dict)]
        return list(self._fields)

    def server_info(self) -> dict:
        data = self._get_json("/serverInfo")
        if not isinstance(data, dict):
            raise core.KbError("Jira /serverInfo 응답 형식이 아닙니다")
        return data

    def sprint_field_id(self) -> str | None:
        """스프린트 사용자 정의 필드 id(예: customfield_10020). Jira Software가 없으면 None."""
        if self._sprint_field is _UNSET:
            found: str | None = None
            for item in self.fields():
                schema = item.get("schema")
                if isinstance(schema, Mapping) and schema.get("custom") == SPRINT_SCHEMA and isinstance(item.get("id"), str):
                    found = item["id"]
                    break
            self._sprint_field = found
        return self._sprint_field

    def sync_fields(self) -> list[str]:
        """ISSUE_FIELDS에 스프린트 필드(있으면)를 더한 목록."""
        out = list(ISSUE_FIELDS)
        sprint = self.sprint_field_id()
        if sprint:
            out.append(sprint)
        return out

    # ------------------------------------------------------------ 검색

    def _search_page(self, jql: str, start: int, max_results: int, fields: str | None) -> dict:
        params: dict[str, Any] = {"jql": jql, "startAt": start, "maxResults": max_results}
        if fields:
            params["fields"] = fields
        resp = self._get("/search", params)
        if resp.status_code == 400:
            with resp:
                messages = _jira_messages(resp)
            detail = "; ".join(messages) or "Jira가 검색을 거부했습니다 (400)"
            raise JqlError(f"JQL 오류: {detail} (JQL: {_short_jql(jql)})", jql=jql, messages=messages)
        data = self._json(resp, f"GET {API}/search")
        if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
            raise core.KbError("Jira 검색 응답 형식이 아닙니다 (issues 목록 없음)")
        return data

    def search(self, jql: str, *, fields: list[str]) -> Iterator[dict]:
        """JQL 결과 이슈를 하나씩 돌려준다. 첫 페이지는 부를 때 바로 받는다(오류가 그 자리에서 난다).

        `startAt`은 받은 이슈 수만큼 늘린다(서버가 maxResults를 줄여도 빠짐없이). 빈 페이지나
        total에 닿으면 멈춘다. 같은 키가 다시 오면 건너뛴다.
        """
        query = with_default_order(jql)
        if isinstance(fields, str):
            field_param: str | None = fields
        else:
            field_param = ",".join(str(f) for f in fields) if fields else None
        first = self._search_page(query, 0, self.page_size, field_param)
        return self._pages(query, field_param, first)

    def _pages(self, query: str, field_param: str | None, page: dict) -> Iterator[dict]:
        seen: set[str] = set()
        start = 0
        while True:
            issues = page["issues"]
            for issue in issues:
                key = issue.get("key") if isinstance(issue, dict) else None
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                yield issue
            start += len(issues)
            total = page.get("total")
            if not issues or (isinstance(total, int) and start >= total):
                return
            page = self._search_page(query, start, self.page_size, field_param)

    def validate_jql(self, jql: str) -> str | None:
        """JQL이 맞으면 None, 틀리면 Jira가 준 오류 문장(여럿이면 '; '로 이음). maxResults=0으로 묻는다."""
        resp = self._get("/search", {"jql": jql, "startAt": 0, "maxResults": 0})
        if resp.status_code == 400:
            with resp:
                messages = _jira_messages(resp)
            return "; ".join(messages) or "Jira가 JQL을 거부했습니다 (400)"
        self._json(resp, f"GET {API}/search")
        return None

    # ------------------------------------------------------------ 이슈·댓글

    @staticmethod
    def _check_key(key: str) -> str:
        if not isinstance(key, str) or not _KEY_RE.match(key.strip()):
            raise core.ConfigError(f"이슈 키 형식이 아닙니다: {key!r}. 예: KB-12")
        return key.strip()

    def issue(self, key: str, *, fields: list[str] | None = None) -> dict:
        """이슈 하나. 없으면 NotFoundError."""
        key = self._check_key(key)
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = fields if isinstance(fields, str) else ",".join(str(f) for f in fields)
        data = self._get_json(f"/issue/{quote(key)}", params)
        if not isinstance(data, dict) or not isinstance(data.get("fields"), dict):
            raise core.KbError(f"Jira 이슈 응답 형식이 아닙니다 ({key})")
        return data

    def comments(self, key: str) -> list[dict]:
        """이슈의 댓글 전부(`/issue/{key}/comment`를 total까지 페이지로 넘긴다)."""
        key = self._check_key(key)
        path = f"/issue/{quote(key)}/comment"
        out: list[dict] = []
        start = 0
        while True:
            data = self._get_json(path, {"startAt": start, "maxResults": self.page_size})
            batch = data.get("comments") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                raise core.KbError(f"Jira 댓글 응답 형식이 아닙니다 ({key})")
            out.extend(item for item in batch if isinstance(item, dict))
            start += len(batch)
            total = data.get("total")
            if not batch:
                break
            if isinstance(total, int):
                if start >= total:
                    break
            elif len(batch) < self.page_size:
                break
        return out

    def issue_comments(self, issue: Mapping[str, Any]) -> list[dict]:
        """검색·이슈 응답에 실린 댓글이 전부면 그대로, total보다 적거나 없으면 `comments()`로 받는다."""
        fields = issue.get("fields") if isinstance(issue, Mapping) else None
        field = fields.get("comment") if isinstance(fields, Mapping) else None
        if isinstance(field, Mapping):
            listed = [item for item in (field.get("comments") or []) if isinstance(item, dict)]
            total = field.get("total")
            if not isinstance(total, int) or total <= len(listed):
                return listed
        return self.comments(str(issue.get("key")))
