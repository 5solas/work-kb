"""설정 세 겹 중 구조(`config.yaml`)와 환경(`config.local.yaml`)을 읽어 합친다.

병합 순서: 코드 기본값 → config.yaml → config.local.yaml → 환경변수 `KB_<SECTION>_<KEY>`.
`environment`는 config.local.yaml에서만 읽고, 없거나 잘못되면 'company'로 본다(안전 쪽).
비밀값은 여기서 다루지 않는다. `*_ref` 참조만 두고 `lib/secrets.py`가 해석한다.
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Mapping
from urllib.parse import urlsplit

import yaml

from lib import core

CODE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_NAME = "config.yaml"
LOCAL_NAME = "config.local.yaml"
ENVIRONMENTS = ("home", "company")

# 지시서 §5.7의 공개 클라우드 호스트. 설정은 여기에 더할 수만 있고 뺄 수는 없다(D44).
CODE_BLOCKLIST: frozenset[str] = frozenset(
    {
        "deepinfra.com",
        "openai.com",
        "together.xyz",
        "openrouter.ai",
        "anthropic.com",
        "googleapis.com",
        "groq.com",
        "fireworks.ai",
        "mistral.ai",
        "baseten.co",
        "nebius.com",
    }
)

# 코드 기본값. 연결 값(주소·참조)은 두지 않는다.
CODE_DEFAULTS: dict[str, Any] = {
    "kb_root": ".",
    "timezone": "Asia/Seoul",
    "people_prefix": "@",
    "normalize_keys": True,
    "jira": {
        "api_version": 2,
        "auth": "pat",
        "project_keys": [],
        "views": {},
        "poll_minutes": 15,
        "max_results": 500,
    },
    "confluence": {
        "enabled": True,
        "title_prefix": "[KB] ",
        "push_globs": [],
    },
    "llm": {
        "enabled": False,
        "tasks": {},
        "profiles": {},
        "fallback_order": ["main", "fallback"],
        "temperature": 0,
        "timeout_sec": 180,
        "public_hosts_blocklist": [],
    },
}

_SECTIONS = frozenset(k for k, v in CODE_DEFAULTS.items() if isinstance(v, dict))
_TOP_LEVEL_ENV_KEYS = frozenset(k for k, v in CODE_DEFAULTS.items() if not isinstance(v, dict)) - {"kb_root"}
_RESERVED_ENV = frozenset(
    {"KB_AGENT", "KB_ROOT", "KB_REQUIRE_LIVE", "KB_SKIP_EMACS", "KB_FAKE_NOW", "KB_ENVIRONMENT"}
)
_RESERVED_ENV_PREFIXES = ("KB_INIT_", "KB_FAKE_")

_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]+$")
_REF_PREFIXES = ("keyring:", "env:")
_SECRET_WORDS = frozenset({"pat", "token", "password", "passwd", "secret", "apikey"})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SPLIT = re.compile(r"[_\-.\s]+")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)

_LOCAL_HEADER = "# kb.py init이 만든 환경 설정입니다. git에 올리지 않습니다.\n# 비밀값은 쓰지 않고 keyring:… 또는 env:… 참조만 씁니다.\n"


# ---------------------------------------------------------------- 공개 호스트


def _norm_host(entry: Any) -> str:
    if not isinstance(entry, str):
        return ""
    text = entry.strip()
    if "/" in text or ":" in text:
        text = _hostname(text) or ""
    text = text.lower().strip(".")
    if text.startswith("*."):
        text = text[2:]
    return text.strip(".")


def _hostname(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text:
        return None
    if not _SCHEME.match(text) and not text.startswith("//"):
        text = "//" + text  # 스킴 없는 'host/path'도 호스트로 읽는다(안전 쪽)
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    return host or None


def is_public_host(url: str, blocklist: Iterable[str]) -> bool:
    """URL의 호스트가 목록의 도메인이거나 그 하위 도메인이면 True. 잘못된 URL은 False."""
    host = _hostname(url)
    if host is None:
        return False
    for entry in blocklist:
        domain = _norm_host(entry)
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


# ---------------------------------------------------------------- 값 정리


def normalize_reasoning(value: Any, where: str = "reasoning") -> str:
    """YAML이 bool로 바꾼 on/off까지 받아 'on'/'off' 문자열로 돌려준다."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    if isinstance(value, str) and value.strip().lower() in ("on", "off"):
        return value.strip().lower()
    raise core.ConfigError(f"{where}: 'on' 또는 'off'만 쓸 수 있습니다 (지금 값: {value!r})")


def _normalize_environment(value: Any) -> Literal["home", "company"]:
    if isinstance(value, str) and value.strip().lower() in ENVIRONMENTS:
        return value.strip().lower()  # type: ignore[return-value]
    return "company"


def _has_top_level_order_by(jql: str) -> bool:
    """따옴표·괄호 밖의 ORDER BY가 있는지 본다."""
    outside: list[str] = []
    quote: str | None = None
    depth = 0
    escaped = False
    for ch in jql:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            outside.append(" ")
            continue
        if ch in ("'", '"'):
            quote = ch
            outside.append(" ")
        elif ch == "(":
            depth += 1
            outside.append(" ")
        elif ch == ")":
            depth = max(depth - 1, 0)
            outside.append(" ")
        else:
            outside.append(ch if depth == 0 else " ")
    return bool(_ORDER_BY.search("".join(outside)))


# ---------------------------------------------------------------- Config


@dataclass(frozen=True)
class Config:
    root: Path
    data: Mapping[str, Any]
    environment: Literal["home", "company"]
    local_exists: bool = False
    config_dir: Path | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.config_dir is None:
            object.__setattr__(self, "config_dir", self.root)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, *parts: str) -> Path:
        return _inside(self.root, parts)

    def code_path(self, *parts: str) -> Path:
        return _inside(CODE_ROOT, parts)

    @property
    def tz(self) -> dt.tzinfo:
        return core.resolve_tz(str(self.get("timezone") or "Asia/Seoul"))

    @property
    def project_keys(self) -> list[str]:
        raw = self.get("jira.project_keys")
        if raw is None:
            return []
        if isinstance(raw, str):
            items: list[Any] = [x for x in re.split(r"[\s,]+", raw) if x]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            raise core.ConfigError("jira.project_keys: 목록이어야 합니다. 예: [KB, OPS]")
        keys: list[str] = []
        for item in items:
            if isinstance(item, bool) or not isinstance(item, (str, int)):
                raise core.ConfigError(
                    f"jira.project_keys: {item!r}은(는) 프로젝트 키가 아닙니다. "
                    "YAML은 ON·NO 같은 값을 참·거짓으로 읽습니다. 따옴표로 감싸세요"
                )
            key = str(item).strip().upper()
            if not _PROJECT_KEY.match(key):
                raise core.ConfigError(
                    f"jira.project_keys: '{item}'은(는) 프로젝트 키 형식이 아닙니다. 영문 대문자로 시작하는 2자 이상이어야 합니다"
                )
            if key not in keys:
                keys.append(key)
        return keys

    @property
    def blocklist(self) -> frozenset[str]:
        raw = self.get("llm.public_hosts_blocklist") or []
        if isinstance(raw, str):
            raw = [x for x in re.split(r"[\s,]+", raw) if x]
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise core.ConfigError("llm.public_hosts_blocklist: 도메인 목록이어야 합니다")
        extra = {_norm_host(x) for x in raw}
        return CODE_BLOCKLIST | frozenset(x for x in extra if x)

    def profiles(self) -> dict[str, dict]:
        raw = self.get("llm.profiles") or {}
        if not isinstance(raw, Mapping):
            raise core.ConfigError("llm.profiles: 이름별 묶음이어야 합니다")
        out: dict[str, dict] = {}
        for name, prof in raw.items():
            if not isinstance(prof, Mapping):
                raise core.ConfigError(f"llm.profiles.{name}: 키: 값 묶음이어야 합니다")
            out[str(name)] = copy.deepcopy(dict(prof))
        return out

    def profile(self, name: str) -> dict:
        profiles = self.profiles()
        if name not in profiles:
            raise core.ConfigError(f"LLM 프로필 '{name}'이(가) 설정에 없습니다. `kb.py init --section llm`으로 추가하세요")
        return profiles[name]

    def llm_task(self, name: str) -> dict:
        """작업 설정. profile 기본 'main', reasoning은 'on'/'off'로 정리한다."""
        tasks = self.get("llm.tasks") or {}
        task = tasks.get(name) if isinstance(tasks, Mapping) else None
        if not isinstance(task, Mapping):
            raise core.ConfigError(f"LLM 작업 '{name}'이(가) config.yaml의 llm.tasks에 없습니다")
        out: dict[str, Any] = {"profile": "main", "reasoning": "off"}
        out.update(copy.deepcopy(dict(task)))
        out["reasoning"] = normalize_reasoning(out["reasoning"], f"llm.tasks.{name}.reasoning")
        return out


def _inside(base: Path, parts: tuple[str, ...]) -> Path:
    candidate = Path(os.path.normpath(base.joinpath(*parts)))
    if candidate != base and base not in candidate.parents:
        raise core.ConfigError(f"경로가 루트 밖을 가리킵니다: {'/'.join(str(p) for p in parts)}")
    return candidate


# ---------------------------------------------------------------- 읽기


def _read_yaml(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise core.ConfigError(f"{path}: 읽지 못했습니다 ({exc.strerror or exc})") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise core.ConfigError(f"{path}: UTF-8 파일이 아닙니다. UTF-8로 다시 저장하세요") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" ({mark.line + 1}번째 줄)" if mark is not None else ""
        problem = getattr(exc, "problem", None) or "형식 오류"
        raise core.ConfigError(f"{path}: YAML 문법 오류{where}: {problem}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise core.ConfigError(f"{path}: 맨 위는 '키: 값' 묶음이어야 합니다")
    return data


def _deep_merge(base: dict, over: Mapping) -> dict:
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _parse_env_value(name: str, value: str) -> Any:
    if value == "":
        return ""
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError:
        return value
    if isinstance(parsed, dict):
        raise core.ConfigError(f"{name}: 환경변수에는 단일 값이나 목록만 쓸 수 있습니다")
    return parsed


def parse_env_overrides(environ: Mapping[str, str]) -> dict:
    """`KB_<SECTION>_<KEY>`를 {section: {key: 값}}으로 바꾼다. 값은 YAML 스칼라로 읽는다.

    예약된 이름(KB_AGENT, KB_ROOT, KB_REQUIRE_LIVE, KB_SKIP_EMACS, KB_FAKE_*, KB_INIT_*,
    KB_ENVIRONMENT)과 모르는 구역은 무시한다. 구역 없는 이름은 최상위 단일 값(timezone 등)만 받는다.
    """
    out: dict[str, Any] = {}
    for raw_name in sorted(environ):
        name = raw_name.upper()
        if not name.startswith("KB_") or name in _RESERVED_ENV or name.startswith(_RESERVED_ENV_PREFIXES):
            continue
        rest = name[3:].lower()
        section, _, key = rest.partition("_")
        if section in _SECTIONS and key:
            out.setdefault(section, {})[key] = _parse_env_value(raw_name, environ[raw_name])
        elif rest in _TOP_LEVEL_ENV_KEYS:
            out[rest] = _parse_env_value(raw_name, environ[raw_name])
    return out


def _apply_overrides(data: dict, overrides: Mapping[str, Any]) -> None:
    for key, value in overrides.items():
        if key in _SECTIONS and isinstance(value, Mapping):
            section = data.setdefault(key, {})
            for sub, sub_value in value.items():
                if isinstance(section.get(sub), Mapping):
                    raise core.ConfigError(
                        f"KB_{key.upper()}_{sub.upper()}: 묶음 설정은 환경변수로 바꿀 수 없습니다"
                    )
                section[sub] = sub_value
        else:
            data[key] = value


def _validate(data: dict) -> None:
    for section in _SECTIONS:
        if not isinstance(data.get(section), dict):
            raise core.ConfigError(f"{section}: '키: 값' 묶음이어야 합니다")
    llm = data["llm"]
    for name in ("tasks", "profiles"):
        if llm.get(name) is None:
            llm[name] = {}
        if not isinstance(llm[name], dict):
            raise core.ConfigError(f"llm.{name}: 이름별 묶음이어야 합니다")
    for task_name, task in llm["tasks"].items():
        if not isinstance(task, dict):
            raise core.ConfigError(f"llm.tasks.{task_name}: '키: 값' 묶음이어야 합니다")
        if "reasoning" in task:
            task["reasoning"] = normalize_reasoning(task["reasoning"], f"llm.tasks.{task_name}.reasoning")
    jql = data["jira"].get("jql_mine")
    if isinstance(jql, str) and _has_top_level_order_by(jql):
        raise core.ConfigError("jira.jql_mine: ORDER BY는 쓸 수 없습니다. 정렬은 동기화가 정합니다")


def _resolve_root(root: Path | str | None, environ: Mapping[str, str]) -> Path:
    if root is not None and str(root) != "":
        base = Path(root)
    elif environ.get("KB_ROOT"):
        base = Path(environ["KB_ROOT"])
    else:
        base = CODE_ROOT
    base = base.expanduser().resolve()
    if not base.is_dir():
        raise core.ConfigError(f"{base}: 볼트 루트 폴더가 없습니다. --root 또는 KB_ROOT를 확인하세요")
    return base


def load(root: Path | str | None = None, *, environ: Mapping[str, str] | None = None) -> Config:
    """설정을 읽어 합친다. 루트: 인수 → KB_ROOT → 저장소 폴더."""
    env = os.environ if environ is None else environ
    config_dir = _resolve_root(root, env)
    config_path = config_dir / CONFIG_NAME
    if not config_path.is_file():
        raise core.ConfigError(f"{config_path}: config.yaml이 없습니다. 볼트 루트(--root 또는 KB_ROOT)를 확인하세요")

    data = copy.deepcopy(CODE_DEFAULTS)
    structure = _read_yaml(config_path)
    structure.pop("environment", None)  # 환경은 config.local.yaml에서만 정한다
    _deep_merge(data, structure)

    local_path = config_dir / LOCAL_NAME
    local_exists = local_path.is_file()
    local = _read_yaml(local_path) if local_exists else {}
    environment = _normalize_environment(local.get("environment"))
    _deep_merge(data, {k: v for k, v in local.items() if k != "environment"})

    _apply_overrides(data, parse_env_overrides(env))
    data["environment"] = environment
    _validate(data)

    kb_root = data.get("kb_root") or "."
    vault_root = Path(os.path.normpath(config_dir / str(kb_root)))
    return Config(
        root=vault_root,
        data=data,
        environment=environment,
        local_exists=local_exists,
        config_dir=config_dir,
    )


# ---------------------------------------------------------------- 쓰기


def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    words = [w for w in _KEY_SPLIT.split(_CAMEL.sub("_", key).lower()) if w]
    if any(w in _SECRET_WORDS for w in words):
        return True
    return any(a == "api" and b == "key" for a, b in zip(words, words[1:]))


def _secret_violations(node: Any, prefix: str = "") -> Iterator[str]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            where = f"{prefix}.{key}" if prefix else str(key)
            if _is_secret_key(key):
                ok = value is None or value == "" or (isinstance(value, str) and value.startswith(_REF_PREFIXES))
                if not ok:
                    yield where
                    continue
            yield from _secret_violations(value, where)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            yield from _secret_violations(item, f"{prefix}[{i}]")


def _plain(node: Any) -> Any:
    if isinstance(node, Mapping):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_plain(v) for v in node]
    if isinstance(node, Path):
        return str(node)
    return node


def save_local(root: Path | str, data: Mapping[str, Any]) -> None:
    """config.local.yaml을 원자적으로 쓴다. 비밀값처럼 보이는 키에 참조가 아닌 값이 있으면 거부한다."""
    if not isinstance(data, Mapping):
        raise core.ConfigError("config.local.yaml 내용은 '키: 값' 묶음이어야 합니다")
    bad = list(_secret_violations(data))
    if bad:
        raise core.ConfigError(
            "config.local.yaml에 비밀값을 쓸 수 없습니다: "
            + ", ".join(bad)
            + ". 자격 증명 저장소에 넣고 keyring:… 참조만 쓰세요"
        )
    try:
        body = yaml.safe_dump(_plain(data), allow_unicode=True, sort_keys=False, default_flow_style=False)
    except yaml.YAMLError as exc:
        raise core.ConfigError(f"config.local.yaml로 바꿀 수 없는 값이 있습니다: {exc}") from exc
    core.atomic_write(Path(root) / LOCAL_NAME, (_LOCAL_HEADER + body).encode("utf-8"))
