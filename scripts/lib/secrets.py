"""비밀값(세 번째 겹) 참조를 해석하고 저장한다.

참조 형식
- `keyring:<service>/<name>`: Windows 자격 증명 저장소(keyring)
- `env:<NAME>`: 환경변수. 집(home)에서 `DEEPINFRA_API_KEY`만 허용, 회사(company)에서는 거부

가져올 때는 `from lib import secrets as kb_secrets`로만 쓴다(표준 `secrets`와 구분).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import keyring

from lib import core

if TYPE_CHECKING:
    from lib.config import Config

WINDOWS_BLOB_LIMIT = 2560  # CRED_MAX_CREDENTIAL_BLOB_SIZE(바이트), 값은 UTF-16LE로 저장된다
HOME_ENV_ALLOWED = frozenset({"DEEPINFRA_API_KEY"})

_KEYRING_REF = re.compile(r"^keyring:([A-Za-z0-9._@-]+)/([A-Za-z0-9._@-]+)$")
_ENV_REF = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]*)$")
_REF_FORM = "keyring:<서비스>/<이름> 또는 env:<이름>"
_HINT = "Windows 자격 증명 관리자가 열리는지 확인하세요. 예약 작업이면 '사용자가 로그온할 때만 실행'으로 등록하세요"
_SECTIONS = ("jira", "confluence", "llm")


@dataclass(frozen=True)
class Ref:
    scheme: Literal["keyring", "env"]
    service: str
    name: str

    def __str__(self) -> str:
        return f"env:{self.name}" if self.scheme == "env" else f"keyring:{self.service}/{self.name}"


def parse_ref(ref: str) -> Ref:
    """참조 문자열을 엄격하게 해석한다. 형식이 틀리면 ConfigError."""
    if isinstance(ref, str):
        match = _KEYRING_REF.match(ref)
        if match:
            return Ref("keyring", match.group(1), match.group(2))
        match = _ENV_REF.match(ref)
        if match:
            return Ref("env", "", match.group(1))
        if ref.startswith(("keyring:", "env:")):
            raise core.ConfigError(f"비밀값 참조 형식이 잘못되었습니다: '{ref}'. {_REF_FORM} 꼴로 쓰세요")
    # 참조가 아닌 값은 비밀값 원문일 수 있으므로 메시지에 싣지 않는다
    raise core.ConfigError(f"비밀값 참조가 아닙니다. {_REF_FORM} 꼴로 쓰세요")


def _describe_exc(exc: BaseException) -> str:
    text = core.redact(str(exc)).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _get_password(service: str, name: str) -> str | None:
    try:
        return keyring.get_password(service, name)
    except Exception as exc:  # 백엔드마다 오류 종류가 다르다
        raise core.KbError(
            f"자격 증명 저장소에서 {service}/{name}을(를) 읽지 못했습니다 ({backend_name()}, {_describe_exc(exc)}). {_HINT}"
        ) from exc


def resolve(ref: str | None, cfg: "Config") -> str | None:
    """참조를 실제 값으로 바꾼다. 값이 없으면 None. 얻은 값은 로그 가림에 등록한다."""
    if ref is None or ref == "":
        return None
    parsed = parse_ref(ref)
    if parsed.scheme == "env":
        environment = getattr(cfg, "environment", "company")
        if environment != "home":
            raise core.ConfigError(
                f"회사 환경에서는 환경변수 참조({parsed})를 쓸 수 없습니다. 자격 증명 저장소(keyring:…)를 쓰세요"
            )
        if parsed.name not in HOME_ENV_ALLOWED:
            allowed = ", ".join(sorted(HOME_ENV_ALLOWED))
            raise core.ConfigError(f"환경변수 참조는 {allowed}만 허용됩니다: {parsed}")
        value = os.environ.get(parsed.name)
    else:
        value = _get_password(parsed.service, parsed.name)
    if not value:
        return None
    core.register_secret(value)
    return value


def _backend_problem() -> str | None:
    from keyring.backends import chainer, fail

    kr = keyring.get_keyring()
    if isinstance(kr, fail.Keyring):
        return "사용할 수 있는 자격 증명 저장소가 없습니다"
    try:
        from keyring.backends import null
    except ImportError:  # pragma: no cover - 오래된 keyring
        null = None  # type: ignore[assignment]
    if null is not None and isinstance(kr, null.Keyring):
        return "자격 증명 저장소가 꺼져 있습니다(null 백엔드)"
    if isinstance(kr, chainer.ChainerBackend) and not list(kr.backends):
        return "연결된 자격 증명 저장소가 하나도 없습니다"
    return None


def store(service: str, username: str, value: str) -> None:
    """자격 증명 저장소에 저장하고 다시 읽어 확인한다. 파일·환경변수로 우회하지 않는다."""
    if not service or not username:
        raise core.ConfigError("자격 증명 저장소의 서비스와 이름이 필요합니다")
    if not isinstance(value, str) or not value.strip():
        raise core.ConfigError(f"{service}/{username}: 빈 값은 저장할 수 없습니다")
    size = len(value.encode("utf-16-le"))
    if size > WINDOWS_BLOB_LIMIT:
        raise core.ConfigError(
            f"{service}/{username}: 값이 너무 깁니다({size}바이트). "
            f"Windows 자격 증명 저장소는 {WINDOWS_BLOB_LIMIT}바이트(영문 약 1280자)까지 저장합니다"
        )
    problem = _backend_problem()
    if problem:
        raise core.KbError(f"{problem} ({backend_name()}). {_HINT}")
    core.register_secret(value)
    try:
        keyring.set_password(service, username, value)
    except Exception as exc:
        raise core.KbError(
            f"자격 증명 저장소에 {service}/{username}을(를) 쓰지 못했습니다 ({backend_name()}, {_describe_exc(exc)}). {_HINT}"
        ) from exc
    if _get_password(service, username) != value:
        raise core.KbError(
            f"자격 증명 저장소에 쓴 {service}/{username} 값을 다시 읽었더니 달랐습니다 ({backend_name()}). {_HINT}"
        )


def backend_name() -> str:
    """현재 keyring 백엔드 이름. 체인이면 안에 든 백엔드도 적는다."""
    kr = keyring.get_keyring()
    name = f"{type(kr).__module__}.{type(kr).__qualname__}"
    try:
        from keyring.backends import chainer

        if isinstance(kr, chainer.ChainerBackend):
            inner = ", ".join(f"{type(b).__module__}.{type(b).__qualname__}" for b in kr.backends)
            name += f"({inner})"
    except Exception:  # 이름 표시용이므로 실패해도 넘어간다
        pass
    return name


def describe_missing(ref: str | None) -> str:
    """빠진 비밀값을 채우는 방법을 한 문장으로 알려 준다."""
    if ref is None or ref == "":
        return "비밀값 참조가 설정되지 않았습니다. `python scripts/kb.py init`으로 설정하세요."
    try:
        parsed = parse_ref(ref)
    except core.ConfigError as exc:
        return f"{exc}."
    if parsed.scheme == "env":
        return (
            f"환경변수 {parsed.name}이(가) 비어 있습니다. "
            f"PowerShell에서 `[Environment]::SetEnvironmentVariable('{parsed.name}', '<값>', 'User')`로 설정하고 터미널을 다시 여세요."
        )
    section = next((s for s in _SECTIONS if parsed.name.lower().startswith(s)), None)
    command = f"python scripts/kb.py init --section {section}" if section else "python scripts/kb.py init"
    return f"자격 증명 저장소에 {parsed.service}/{parsed.name} 값이 없습니다. `{command}`로 다시 입력하세요."
