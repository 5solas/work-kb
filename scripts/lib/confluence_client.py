"""Confluence Data Center REST 클라이언트 (Phase 5, D54·D55·D56).

- 인증은 Bearer PAT. 모든 HTTP는 `net.session_for`·`net.request`를 거친다(429 재시도는 net이 한다).
- 401/403 → AuthError(종료 2), 연결 실패 → NetworkError(3). 리다이렉트는 따라가지 않는다
  (3xx → `RedirectRefused`, ConfigError 4). 그 밖의 4xx → `ConfluenceError`(Confluence가 준 문장을 담음),
  404는 `NotFound`, 409는 `VersionConflict`. 읽기(GET)가 5xx면 1초 쉬고 한 번만 다시 묻는다.
  쓰기(POST·PUT)는 다시 보내지 않는다(5xx → NetworkError, after_send=True).
- 페이지 조회·생성·갱신은 언제나 `expand=version`을 붙인다(id로 읽을 때는 `version,space`). 갱신은 호출자가
  `지금 버전 + 1`을 넘긴다. 시간 제한은 `confluence.timeout`(초, 기본 30).
- 개인 스페이스(D55): `/user/current`(익명이면 중단) → `/rest/api/space?spaceKey=~<username>` →
  `spaceKey=~<userKey>` → `type=personal` 목록(페이지 넘김). 키는 대소문자 없이 비교한다. 본인 스페이스를 못 찾으면 후보 목록만 돌려주고 고르는 일은 호출자가 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import quote

import requests

from lib import core, net
from lib import secrets as kb_secrets

if TYPE_CHECKING:
    from lib.config import Config

API = "/rest/api"
PAGE_LIMIT = 25
MAX_SPACE_PAGES = 40  # type=personal 목록은 최대 1,000개까지만 넘긴다
RETRY_5XX_WAIT = 1.0
SETUP_HINT = "`python scripts/kb.py init --section confluence`로 설정하세요"


class ConfluenceError(core.KbError):
    """Confluence가 요청을 받아들이지 않았다. `status`와 Confluence가 준 문장 `detail`을 담는다."""

    def __init__(self, message: str, *, status: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


class NotFound(ConfluenceError):
    """404: 없거나 볼 권한이 없다."""


class VersionConflict(ConfluenceError):
    """409: 보낸 버전이 `지금 버전 + 1`이 아니다(그사이 누가 고쳤다)."""


class RedirectRefused(core.ConfigError):
    """base_url이 다른 곳으로 리다이렉트된다. 따라가지 않는다."""


@dataclass(frozen=True)
class PersonalSpace:
    """개인 스페이스 탐지 결과(D55).

    - `user`: `/rest/api/user/current` 응답.
    - `own`: `~<username>`, 다음 `~<userKey>`로 찾은 본인 스페이스. 없으면 None.
    - `candidates`: `own`이 있으면 `[own]`, 없으면 `type=personal` 목록 전부(남의 것일 수 있으므로
      하나뿐이어도 사용자에게 번호로 고르게 한다). 비었으면 Confluence 화면에서 개인 스페이스를 만들어야 한다.
    """

    user: dict
    own: dict | None
    candidates: list[dict] = field(default_factory=list)

    @property
    def username(self) -> str:
        return str(self.user.get("username") or "")


def _detail(resp: requests.Response) -> str:
    """오류 응답에서 사람이 읽을 문장(`message`, `errorMessages`, `errors`, 아니면 본문 앞부분)."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    parts: list[str] = []
    if isinstance(data, Mapping):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            parts.append(message.strip())
        for item in data.get("errorMessages") or []:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
        errors = data.get("errors")
        if isinstance(errors, Mapping):
            parts.extend(f"{k}: {v}" for k, v in errors.items() if isinstance(v, str) and v.strip())
    if not parts:
        try:
            text = " ".join(resp.text.split())
        except Exception:  # 표시용이므로 디코딩 실패는 넘어간다
            text = ""
        if text:
            parts.append(text[:200] + ("…" if len(text) > 200 else ""))
    return core.redact("; ".join(parts))


class ConfluenceClient:
    """Confluence DC REST 클라이언트. `with ConfluenceClient(...) as client:`로 쓸 수 있다."""

    def __init__(
        self,
        base_url: str,
        pat: str,
        *,
        conn: Mapping | None = None,
        space_key: str | None = None,
        parent_page_id: str | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise core.ConfigError(f"confluence.base_url이 비어 있습니다. {SETUP_HINT}")
        conn = dict(conn or {})
        self.base_url = base_url.strip().rstrip("/")
        self.space_key = (space_key or "").strip() or None
        self.parent_page_id = str(parent_page_id).strip() if parent_page_id not in (None, "") else None
        try:
            self.timeout = float(conn.get("timeout") or 30)
        except (TypeError, ValueError):
            raise core.ConfigError(f"confluence.timeout: 초 단위 숫자여야 합니다 (지금 값: {conn.get('timeout')!r})") from None
        self._session = net.session_for(conn, pat)
        self._log_root: Any = None

    @classmethod
    def from_config(cls, cfg: "Config") -> "ConfluenceClient":
        """설정의 confluence 구역으로 만든다. PAT는 `confluence.pat_ref`를 secrets.resolve로 푼다."""
        conf = cfg.get("confluence") or {}
        if not isinstance(conf, Mapping):
            raise core.ConfigError("confluence: '키: 값' 묶음이어야 합니다")
        base_url = conf.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise core.ConfigError(f"confluence.base_url이 설정되지 않았습니다. {SETUP_HINT}")
        ref = conf.get("pat_ref")
        pat = kb_secrets.resolve(ref, cfg)
        if not pat:
            raise core.AuthError(kb_secrets.describe_missing(ref))
        parent = conf.get("parent_page_id")
        client = cls(
            base_url,
            pat,
            conn=conf,
            space_key=str(conf.get("space_key") or "") or None,
            parent_page_id=None if parent is None else str(parent),
        )
        client._log_root = cfg.root
        return client

    # ------------------------------------------------------------ 수명

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "ConfluenceClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ HTTP

    def _url(self, path: str) -> str:
        return f"{self.base_url}{API}{path}"

    def _call(self, method: str, path: str, *, params: Mapping[str, Any] | None = None, body: Any = None) -> requests.Response:
        url = self._url(path)
        kw: dict[str, Any] = {"params": dict(params or {}), "allow_redirects": False}
        if body is not None:
            kw["json"] = body
        if method == "GET":
            for attempt in (0, 1):
                resp = net.request(self._session, method, url, timeout=self.timeout, root=self._log_root, **kw)
                if resp.status_code < 500 or attempt:
                    return resp
                resp.close()
                net._sleep(RETRY_5XX_WAIT)
        return net.request(self._session, method, url, timeout=self.timeout, root=self._log_root, **kw)

    def _json(self, resp: requests.Response, what: str, *, write: bool = False) -> Any:
        status = resp.status_code
        with resp:
            if 200 <= status < 300:
                try:
                    return resp.json()
                except ValueError:
                    raise ConfluenceError(
                        f"Confluence 응답이 JSON이 아닙니다 ({status}, {what}). base_url이 Confluence 서버 주소인지 확인하세요",
                        status=status,
                    ) from None
            if 300 <= status < 400:
                location = core.redact(resp.headers.get("Location") or "?")
                raise RedirectRefused(
                    f"confluence.base_url이 리다이렉트됩니다 ({status}, {what} → {location}). 따라가지 않았습니다. "
                    f"리다이렉트 없는 주소로 고치세요({SETUP_HINT})"
                )
            detail = _detail(resp)
        suffix = f": {detail}" if detail else ""
        if status == 404:
            raise NotFound(f"Confluence에 없거나 볼 권한이 없습니다 ({what}){suffix}", status=status, detail=detail)
        if status == 409:
            raise VersionConflict(f"Confluence 버전 충돌입니다 ({what}){suffix}", status=status, detail=detail)
        if status == 429:
            raise core.NetworkError(f"Confluence 요청 제한(429)이 계속됩니다 ({what}). 잠시 뒤 다시 시도하세요", after_send=False)
        if status >= 500:
            raise core.NetworkError(f"Confluence 서버 오류입니다 ({status}, {what}){suffix}. 잠시 뒤 다시 시도하세요", after_send=write)
        raise ConfluenceError(f"Confluence가 요청을 받아들이지 않았습니다 ({status}, {what}){suffix}", status=status, detail=detail)

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._json(self._call("GET", path, params=params), f"GET {API}{path}")

    def _space(self, space_key: str | None) -> str:
        key = (space_key or self.space_key or "").strip()
        if not key:
            raise core.ConfigError(f"confluence.space_key가 비어 있습니다. {SETUP_HINT}")
        return key

    # ------------------------------------------------------------ 사용자·스페이스

    def current_user(self) -> dict:
        data = self._get("/user/current")
        if not isinstance(data, dict):
            raise ConfluenceError("Confluence /user/current 응답 형식이 아닙니다")
        return data

    def space(self, key: str, *, expand: str | None = None) -> dict | None:
        """스페이스 하나. 없으면 None."""
        params = {"expand": expand} if expand else None
        try:
            data = self._get(f"/space/{quote(key, safe='~')}", params)
        except NotFound:
            return None
        if not isinstance(data, dict):
            raise ConfluenceError(f"Confluence 스페이스 응답 형식이 아닙니다 ({key})")
        return data

    def homepage_id(self, space_key: str | None = None) -> str | None:
        """스페이스 홈 페이지 id. 스페이스나 홈 페이지가 없으면 None."""
        key = self._space(space_key)
        data = self.space(key, expand="homepage")
        if data is None:
            raise core.ConfigError(f"Confluence 스페이스가 없거나 볼 권한이 없습니다: {key}. {SETUP_HINT}")
        home = data.get("homepage")
        if isinstance(home, Mapping) and home.get("id") not in (None, ""):
            return str(home["id"])
        return None

    def _space_results(self, params: Mapping[str, Any]) -> list[dict]:
        data = self._get("/space", params)
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ConfluenceError("Confluence /space 응답 형식이 아닙니다 (results 없음)")
        return [item for item in results if isinstance(item, dict)]

    def personal_spaces(self) -> list[dict]:
        """`type=personal` 목록 전부(`start`·`limit`로 넘긴다). 남의 개인 스페이스도 섞여 있을 수 있다."""
        out: list[dict] = []
        seen: set[str] = set()
        start = 0
        for _ in range(MAX_SPACE_PAGES):
            batch = self._space_results({"type": "personal", "start": start, "limit": PAGE_LIMIT})
            for item in batch:
                key = str(item.get("key") or "")
                if key and key not in seen:
                    seen.add(key)
                    out.append(item)
            if len(batch) < PAGE_LIMIT:
                break
            start += len(batch)
        return out

    def personal_space(self) -> PersonalSpace:
        """D55 순서로 개인 스페이스를 찾는다. 익명으로 인식되면 AuthError."""
        user = self.current_user()
        username = user.get("username")
        if user.get("type") == "anonymous" or not isinstance(username, str) or not username.strip():
            raise core.AuthError(
                "Confluence가 익명 사용자로 인식합니다. PAT가 틀렸거나 만료되었습니다. "
                "`python scripts/kb.py init --section confluence`로 다시 입력하세요"
            )
        keys = [f"~{username.strip()}"]
        user_key = user.get("userKey")
        if isinstance(user_key, str) and user_key.strip():
            keys.append(f"~{user_key.strip()}")
        for own_key in dict.fromkeys(keys):
            wanted = own_key.casefold()
            own = next(
                (s for s in self._space_results({"spaceKey": own_key}) if str(s.get("key") or "").casefold() == wanted),
                None,
            )
            if own is not None:
                return PersonalSpace(user=user, own=own, candidates=[own])
        return PersonalSpace(user=user, own=None, candidates=self.personal_spaces())

    # ------------------------------------------------------------ 페이지

    def find_page(self, space_key: str | None, title: str) -> dict | None:
        """스페이스 안에서 제목이 정확히 같은 현재 페이지(version 포함). 없으면 None."""
        key = self._space(space_key)
        data = self._get("/content", {"spaceKey": key, "title": title, "type": "page", "expand": "version"})
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ConfluenceError("Confluence 페이지 검색 응답 형식이 아닙니다 (results 없음)")
        for item in results:
            if isinstance(item, dict) and item.get("title") == title:
                return item
        return None

    def get_page(self, page_id: str) -> dict | None:
        """페이지 하나(version·space 포함). 없으면 None.

        expand를 주면 DC 기본값(history,space,version)을 대신하므로 space를 꼭 함께 청한다.
        """
        try:
            data = self._get(f"/content/{quote(str(page_id), safe='')}", {"expand": "version,space"})
        except NotFound:
            return None
        if not isinstance(data, dict):
            raise ConfluenceError(f"Confluence 페이지 응답 형식이 아닙니다 ({page_id})")
        return data

    @staticmethod
    def _checked_page(data: Any, what: str) -> dict:
        if not isinstance(data, dict) or data.get("id") in (None, "") or page_version(data) is None:
            raise ConfluenceError(f"Confluence {what} 응답에 id·version이 없습니다. Confluence에서 반영됐는지 확인하세요")
        return data

    def create_page(self, space_key: str | None, title: str, storage: str, parent_id: str | None = None) -> dict:
        """새 페이지. parent_id가 있으면 그 아래에 만든다."""
        key = self._space(space_key)
        body: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": key},
            "body": {"storage": {"value": storage, "representation": "storage"}},
        }
        if parent_id not in (None, ""):
            body["ancestors"] = [{"id": str(parent_id)}]
        resp = self._call("POST", "/content", params={"expand": "version"}, body=body)
        return self._checked_page(self._json(resp, f"POST {API}/content", write=True), "생성")

    def update_page(self, page_id: str, title: str, storage: str, version: int, *, space_key: str | None = None) -> dict:
        """페이지 갱신. `version`은 보낼 새 버전 번호(지금 버전 + 1)."""
        body: dict[str, Any] = {
            "id": str(page_id),
            "type": "page",
            "title": title,
            "body": {"storage": {"value": storage, "representation": "storage"}},
            "version": {"number": int(version)},
        }
        key = (space_key or self.space_key or "").strip()
        if key:
            body["space"] = {"key": key}
        path = f"/content/{quote(str(page_id), safe='')}"
        resp = self._call("PUT", path, params={"expand": "version"}, body=body)
        return self._checked_page(self._json(resp, f"PUT {API}{path}", write=True), "갱신")


def page_version(page: Mapping[str, Any]) -> int | None:
    """페이지 사전의 `version.number`. 없으면 None."""
    version = page.get("version")
    number = version.get("number") if isinstance(version, Mapping) else None
    if isinstance(number, bool) or not isinstance(number, int):
        return None
    return number
