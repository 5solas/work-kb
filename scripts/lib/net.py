"""HTTP 연결 계층. Jira·Confluence 호출과 doctor 점검이 함께 쓴다(D09, D54).

- `session_for(conn, token)`: 연결별 세션. `ca_bundle`·`verify`·`trust_env`·`proxies` 키를 따른다.
- `request(...)`: 401/403 → AuthError, 429 → 기다렸다 재시도, 연결 실패 → NetworkError.
  인증서(SSL)·프록시·주소(DNS)·연결 거부를 구분해 한국어로 알려 준다.
- `get_json(...)`: request + JSON 해석.
- `probe(url, ...)`: 예외 없이 (상태 코드, 설명)만 돌려준다(doctor용).

오류 메시지는 모두 core.redact를 거친다. 만든 오류에는 `problem`·`hint` 속성이 붙는다.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import math
import socket
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests
from requests import exceptions as rex
from urllib3 import exceptions as u3

from lib import core

USER_AGENT = "work-kb"
RETRY_AFTER_CAP = 60.0
_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})

_sleep = time.sleep  # 테스트에서 바꿔 끼운다


class KbSession(requests.Session):
    """연결 설정의 proxies를 환경변수 프록시보다 앞세우려고 따로 들고 있는 세션."""

    kb_proxies: dict[str, str] | None = None


# ---------------------------------------------------------------- 세션


def _as_bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE_WORDS | _FALSE_WORDS:
        return value.strip().lower() in _TRUE_WORDS
    if isinstance(value, int):
        return bool(value)
    raise core.ConfigError(f"{where}: true 또는 false여야 합니다 (지금 값: {value!r})")


def session_for(conn: Mapping[str, Any] | None, token: str | None) -> requests.Session:
    """연결 하나에 쓸 세션을 만든다. 토큰은 Bearer 헤더로 붙이고 로그 가림에 등록한다."""
    conn = conn or {}
    session = KbSession()
    session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    if token:
        core.register_secret(token)
        session.headers["Authorization"] = f"Bearer {token}"
    if "trust_env" in conn and conn["trust_env"] is not None:
        session.trust_env = _as_bool(conn["trust_env"], "trust_env")
    ca_bundle = conn.get("ca_bundle")
    if ca_bundle:
        path = Path(str(ca_bundle)).expanduser()
        if not path.is_file():
            raise core.ConfigError(f"ca_bundle 파일이 없습니다: {path}. 회사 루트 인증서(PEM) 경로를 확인하세요")
        session.verify = str(path)
    elif "verify" in conn and conn["verify"] is not None:
        session.verify = _as_bool(conn["verify"], "verify")
    proxies = conn.get("proxies")
    if proxies:
        if not isinstance(proxies, Mapping):
            raise core.ConfigError("proxies: {http: 주소, https: 주소} 꼴이어야 합니다")
        session.kb_proxies = {str(k): str(v) for k, v in proxies.items() if v}
        session.proxies.update(session.kb_proxies)
    return session


# ---------------------------------------------------------------- 오류 만들기


def _host(url: str) -> str:
    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        return url
    return netloc.rsplit("@", 1)[-1] or url


def _error(cls: type[core.KbError], problem: str, hint: str = "", **kw: Any) -> core.KbError:
    problem = core.redact(problem)
    hint = core.redact(hint)
    exc = cls(f"{problem}. {hint}" if hint else problem, **kw)
    exc.problem = problem  # type: ignore[attr-defined]
    exc.hint = hint  # type: ignore[attr-defined]
    return exc


def _chain(exc: BaseException):
    """예외와 그 원인(reason, args, __cause__, __context__)을 모두 훑는다."""
    seen: set[int] = set()
    stack: list[Any] = [exc]
    while stack:
        item = stack.pop()
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        stack.append(getattr(item, "reason", None))
        stack.extend(arg for arg in getattr(item, "args", ()) if isinstance(arg, BaseException))
        stack.append(item.__cause__)
        stack.append(item.__context__)


def _short(exc: BaseException, limit: int = 160) -> str:
    text = " ".join(str(exc).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _translate(exc: rex.RequestException, url: str, timeout: float) -> core.KbError:
    host = _host(url)
    if isinstance(exc, rex.ProxyError):
        return _error(
            core.NetworkError,
            f"프록시에 연결하지 못했습니다 ({host})",
            "HTTPS_PROXY·NO_PROXY 환경변수를 확인하세요. 프록시 없이 닿는 주소면 이 연결에 trust_env: false를 넣으세요",
        )
    if isinstance(exc, rex.SSLError):
        return _error(
            core.NetworkError,
            f"인증서 확인에 실패했습니다 ({host})",
            "회사 인증서라면 config.local.yaml의 이 연결에 ca_bundle: <PEM 경로>를 넣으세요",
        )
    if isinstance(exc, rex.ConnectTimeout):
        return _error(
            core.NetworkError,
            f"연결 시간이 초과되었습니다 ({host})",
            "사내망·VPN·프록시 연결을 확인하세요",
        )
    if isinstance(exc, rex.ReadTimeout):
        return _error(
            core.NetworkError,
            f"응답 시간이 초과되었습니다 ({host}, {timeout:g}초)",
            "요청이 서버에 닿았을 수 있습니다. 결과를 확인한 뒤 다시 시도하세요",
            after_send=True,
        )
    if isinstance(exc, (rex.MissingSchema, rex.InvalidSchema, rex.InvalidURL)):
        return _error(
            core.ConfigError,
            f"주소 형식이 잘못되었습니다 ({url})",
            "http:// 또는 https://로 시작하는 base_url을 쓰세요",
        )
    if isinstance(exc, rex.ConnectionError):
        chain = list(_chain(exc))
        if any(isinstance(e, (u3.NameResolutionError, socket.gaierror)) for e in chain):
            return _error(
                core.NetworkError,
                f"주소를 찾지 못했습니다 ({host})",
                "base_url 철자와 사내망·VPN 연결을 확인하세요",
            )
        if any(isinstance(e, ConnectionRefusedError) for e in chain):
            return _error(
                core.NetworkError,
                f"연결이 거부되었습니다 ({host})",
                "서버가 꺼져 있거나 주소·포트가 틀렸습니다",
            )
        if any(isinstance(e, (u3.ConnectTimeoutError, u3.NewConnectionError)) for e in chain):
            return _error(
                core.NetworkError,
                f"서버에 연결하지 못했습니다 ({host})",
                "주소와 사내망·VPN 연결을 확인하세요",
            )
        # 요청을 보낸 뒤 끊김(ProtocolError 등) 또는 알 수 없는 경우: 결과가 불확실하다고 본다
        return _error(
            core.NetworkError,
            f"연결이 도중에 끊겼습니다 ({host}, {_short(exc)})",
            "요청이 서버에 닿았을 수 있습니다. 결과를 확인한 뒤 다시 시도하세요",
            after_send=True,
        )
    return _error(
        core.NetworkError,
        f"요청 중 오류가 났습니다 ({host}, {_short(exc)})",
        "요청이 서버에 닿았을 수 있습니다. 결과를 확인한 뒤 다시 시도하세요",
        after_send=True,
    )


def _auth_error(status: int, method: str, url: str) -> core.KbError:
    host = _host(url)
    if status == 401:
        return _error(
            core.AuthError,
            f"인증에 실패했습니다 (401, {method} {host})",
            "토큰(PAT·키)이 틀렸거나 만료되었습니다. `python scripts/kb.py init`으로 다시 입력하세요",
        )
    return _error(
        core.AuthError,
        f"권한이 없습니다 (403, {method} {host})",
        "계정 권한을 확인하세요. Jira라면 브라우저로 한 번 로그인해 CAPTCHA를 푸세요",
    )


# ---------------------------------------------------------------- 요청


def _retry_after(resp: requests.Response, attempt: int) -> float:
    raw = (resp.headers.get("Retry-After") or "").strip()
    if raw:
        seconds: float | None
        try:
            seconds = float(raw)
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(raw)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                seconds = (when - dt.datetime.now(dt.timezone.utc)).total_seconds()
            except (TypeError, ValueError, IndexError):
                seconds = None
        if seconds is not None and not math.isnan(seconds):
            return min(max(seconds, 0.0), RETRY_AFTER_CAP)
    return min(2.0**attempt, RETRY_AFTER_CAP)


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float = 30,
    max_429: int = 5,
    auth_raises: bool = True,
    root: Path | str | None = None,
    **kw: Any,
) -> requests.Response:
    """요청 하나. 401/403은 AuthError(auth_raises=False면 그대로 반환), 429는 재시도, 5xx는 반환."""
    logger = core.get_logger(root) if root is not None else None
    proxies = getattr(session, "kb_proxies", None)
    if proxies and "proxies" not in kw:
        kw["proxies"] = dict(proxies)  # 요청 단위로 넘겨야 환경변수 프록시보다 앞선다
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            resp = session.request(method, url, timeout=timeout, **kw)
        except rex.RequestException as exc:
            err = _translate(exc, url, timeout)
            if logger is not None:
                logger.debug(f"{method} {url} -> {type(exc).__name__} ({time.monotonic() - started:.2f}s)")
            raise err from exc
        status = resp.status_code
        if logger is not None:
            logger.debug(f"{method} {url} -> {status} ({time.monotonic() - started:.2f}s)")
        if status in (401, 403) and auth_raises:
            resp.close()
            raise _auth_error(status, method, url)
        if status == 429 and attempt < max_429:
            wait = _retry_after(resp, attempt)
            resp.close()
            _sleep(wait)
            attempt += 1
            continue
        return resp


def _snippet(resp: requests.Response, limit: int = 80) -> str:
    try:
        text = " ".join(resp.text.split())
    except Exception:  # 디코딩 실패 등, 표시용이므로 넘어간다
        return ""
    return core.redact(text[:limit] + ("…" if len(text) > limit else ""))


def get_json(session: requests.Session, url: str, *, timeout: float = 30) -> Any:
    """GET 후 JSON을 돌려준다. 5xx는 NetworkError, 그 밖의 2xx 아닌 응답과 JSON 아님은 KbError."""
    resp = request(session, "GET", url, timeout=timeout)
    status = resp.status_code
    host = _host(url)
    if status >= 500:
        exc = _error(core.NetworkError, f"서버 오류입니다 ({status}, {host})", "잠시 뒤 다시 시도하세요", after_send=True)
        exc.status = status  # type: ignore[attr-defined]
        raise exc
    if not 200 <= status < 300:
        exc = _error(core.KbError, f"요청이 실패했습니다 ({status}, {host}): {_snippet(resp)}")
        exc.status = status  # type: ignore[attr-defined]
        raise exc
    try:
        return resp.json()
    except ValueError:
        exc = _error(
            core.KbError,
            f"JSON이 아닙니다 ({status}, {host}): '{_snippet(resp)}'",
            "base_url이 웹 화면이 아니라 서버 주소인지 확인하세요",
        )
        exc.status = status  # type: ignore[attr-defined]
        raise exc from None


# ---------------------------------------------------------------- 점검용


def describe_status(resp: requests.Response) -> str:
    """상태 코드를 짧은 한국어 설명으로."""
    status = resp.status_code
    if 200 <= status < 300:
        return f"정상 ({status})"
    if 300 <= status < 400:
        location = resp.headers.get("Location") or "?"
        return f"리디렉션 ({status} → {core.redact(location)})"
    if status == 401:
        return "인증 거부 (401)"
    if status == 403:
        return "권한 거부 (403)"
    if status == 404:
        return "경로 없음 (404)"
    if status == 429:
        return "요청 제한 (429)"
    if status >= 500:
        return f"서버 오류 ({status})"
    return f"요청 실패 ({status})"


def error_text(exc: BaseException) -> str:
    """doctor 표시용 '문제 → 해결' 문자열."""
    problem = getattr(exc, "problem", None)
    hint = getattr(exc, "hint", None)
    if problem:
        return f"{problem} → {hint}" if hint else str(problem)
    return core.redact(str(exc)) or type(exc).__name__


def probe(
    url: str,
    *,
    token: str | None = None,
    conn: Mapping[str, Any] | None = None,
    timeout: float = 10,
) -> tuple[int | None, str]:
    """GET 한 번. 예외를 내지 않고 (상태 코드 또는 None, 설명)을 돌려준다. 리디렉션은 따라가지 않는다."""
    try:
        session = session_for(conn or {}, token)
        with session:
            resp = request(session, "GET", url, timeout=timeout, max_429=0, auth_raises=False, allow_redirects=False)
            with resp:
                return resp.status_code, describe_status(resp)
    except core.KbError as exc:
        return None, error_text(exc)
    except Exception as exc:  # 점검은 절대 예외를 밖으로 내지 않는다
        return None, core.redact(_short(exc))
