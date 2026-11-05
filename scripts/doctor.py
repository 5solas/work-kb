"""연결·환경 점검 (`kb.py doctor`).

한 줄에 하나씩 세 상태로 알린다(D32).
- `✓ 이름: 설명`  정상
- `✗ 이름: 문제 → 해결 방법`  문제. 종료 코드 1 (회사 환경의 공개 LLM 호스트면 5)
- `- 이름: 참고`  선택·안내. 종료 코드에 영향 없음

점검은 `CHECKS` 목록 순서로 돈다. 이후 Phase는 목록에 함수를 더한다.
Python·의존성 점검은 외부 패키지를 가져오기 전에 한다. 외부 호출은 lib/net으로만, 짧은 시간 제한으로 한다.
LLM 연결 점검은 프로필마다 `GET {base_url}/models` 한 번이다(D28, 키를 보내 401/403이면 ✗). 보내기 전에
`llm_client.assert_host_allowed`로 회사 차단을 한 번 더 확인한다.
Jira가 ✓이면 이어서 서버 버전, sprint 필드(D51), jql_mine·뷰 JQL(maxResults=0, D49)을 lib/jira_client로 확인한다.

탐지 재검증(D48): 기록된 `detected`가 있고 연결된 프로필마다 채팅 3회 이하로 확인하고 저장하지 않는다.
모든 채팅은 `LlmClient`(회사 차단, D44)로, 실제 작업과 같은 방법(기록된 토글·JSON 모드)으로 보낸다.
1. 추론 끄기 + (json_mode면) response_format: 생각 흔적 없음·JSON 객체. method none이면 기준 상태와 같은지.
2. 추론 켜기(토글이 있을 때만): 생각 흔적(없으면 토큰 수 2배·차이 30 이상).
3. 비전(vision이 참일 때만): 64×64 빨강 PNG에 빨강이라고 답하는지.
`--redetect`는 같은 자리에서 `llm_detect.detect`를 다시 돌려 config.local.yaml에 저장한다(KB_AGENT=1이면 거부).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from lib import core

OK, FAIL, INFO = "✓", "✗", "-"
TIMEOUT = 10
CODE_ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = (("requests", "requests"), ("yaml", "pyyaml"), ("keyring", "keyring"), ("markdown", "markdown"), ("PIL", "pillow"))
_CORE_MODULES = ("requests", "yaml", "keyring")
_NOT_DETECTED = "탐지 안 됨 → kb.py doctor --redetect (또는 kb.py init --section llm)"
_PENDING = "탐지 대기 → 키를 설정한 뒤 kb.py doctor --redetect"
_RED = re.compile(r"빨강|빨간|빨갛|\bred\b", re.IGNORECASE)


# ---------------------------------------------------------------- 결과 모음


@dataclass(frozen=True)
class Check:
    state: str
    name: str
    text: str
    public_host: bool = False

    def line(self) -> str:
        return f"{self.state} {self.name}: {self.text}"


class Report:
    def __init__(self, echo: Callable[[str], None] | None = None) -> None:
        self.checks: list[Check] = []
        self._echo = echo

    def add(self, state: str, name: str, text: str, *, public_host: bool = False) -> None:
        check = Check(state, name, core.redact(text), public_host)
        self.checks.append(check)
        if self._echo is not None:
            self._echo(check.line())

    def ok(self, name: str, text: str) -> None:
        self.add(OK, name, text)

    def fail(self, name: str, text: str, *, public_host: bool = False) -> None:
        self.add(FAIL, name, text, public_host=public_host)

    def info(self, name: str, text: str) -> None:
        self.add(INFO, name, text)

    def counts(self) -> dict[str, int]:
        out = {OK: 0, FAIL: 0, INFO: 0}
        for check in self.checks:
            out[check.state] += 1
        return out

    def summary(self) -> str:
        c = self.counts()
        return f"{OK} {c[OK]} {FAIL} {c[FAIL]} {INFO} {c[INFO]}"

    def exit_code(self) -> int:
        if any(c.state == FAIL and c.public_host for c in self.checks):
            return 5
        if any(c.state == FAIL for c in self.checks):
            return 1
        return 0

    def as_list(self) -> list[dict[str, str]]:
        return [{"state": c.state, "name": c.name, "text": c.text} for c in self.checks]


@dataclass
class Context:
    root_arg: str | None
    report: Report
    deps_ok: bool = True
    cfg: Any = None  # lib.config.Config
    secrets: dict[str, str | None] = field(default_factory=dict)  # 참조 이름 → 값
    secret_errors: dict[str, str] = field(default_factory=dict)  # 참조 이름 → 해결 안내
    public_profiles: set[str] = field(default_factory=set)
    # 회사 환경에서는 공개 호스트 검사가 끝까지 돈 뒤에만 LLM을 부른다(D44, 안전 쪽).
    public_check_done: bool = False
    jira_base: str | None = None  # Jira ✓일 때만 채운다. 세부 점검(JQL·sprint·버전)은 이것이 있을 때만 돈다
    llm_reachable: set[str] = field(default_factory=set)  # /models가 2xx·404로 답한 프로필. 탐지 점검은 이것만
    redetect: bool = False  # --redetect: 탐지 점검 자리에서 다시 탐지해 저장한다

    def vault_root(self) -> Path:
        if self.cfg is not None:
            return Path(self.cfg.root)
        raw = self.root_arg or os.environ.get("KB_ROOT") or str(CODE_ROOT)
        return Path(raw).expanduser()


# ---------------------------------------------------------------- 도우미


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _conn(cfg: Any, section: Mapping[str, Any] | None) -> dict[str, Any]:
    """연결 설정 묶음. 상대 경로 ca_bundle은 설정 폴더 기준으로 바꾼다."""
    conn = dict(section or {})
    ca = conn.get("ca_bundle")
    if ca and not Path(str(ca)).expanduser().is_absolute():
        conn["ca_bundle"] = str(Path(cfg.config_dir) / str(ca))
    return conn


def _get(url: str, token: str | None, conn: Mapping[str, Any], *, auth_raises: bool):
    """(응답, None) 또는 (None, '문제 → 해결'). 예외를 내지 않는다."""
    import requests

    from lib import net

    try:
        session = net.session_for(conn, token)
        with session:
            resp = net.request(
                session, "GET", url, timeout=TIMEOUT, max_429=1, auth_raises=auth_raises, allow_redirects=False
            )
            resp.content  # noqa: B018 - 세션을 닫기 전에 본문을 읽어 둔다
            return resp, None
    except core.KbError as exc:
        return None, net.error_text(exc)
    except requests.RequestException as exc:
        return None, core.redact(f"{type(exc).__name__}: {exc}")


def _json(resp) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _profiles_to_check(cfg: Any) -> list[str]:
    order = cfg.get("llm.fallback_order") or []
    if isinstance(order, str):
        order = [x for x in order.replace(",", " ").split() if x]
    names = [str(x) for x in order] if isinstance(order, (list, tuple)) else []
    if "mock" in cfg.profiles():
        names.append("mock")
    return list(dict.fromkeys(names))


# ---------------------------------------------------------------- 점검들

Checker = Callable[[Context], None]


def check(label: str) -> Callable[[Checker], Checker]:
    def wrap(func: Checker) -> Checker:
        func.label = label  # type: ignore[attr-defined]
        return func

    return wrap


@check("Python")
def check_python(ctx: Context) -> None:
    version = ".".join(str(x) for x in sys.version_info[:3])
    if sys.version_info < (3, 13):
        ctx.report.fail("Python", f"{version} ({sys.executable}) → Python 3.13 이상으로 venv를 다시 만드세요")
    else:
        ctx.report.ok("Python", f"{version} ({sys.executable})")
    if sys.version_info >= (3, 15):
        ctx.report.info("Python 3.15", "PyYAML wheel이 아직 없습니다. 회사 배포(wheels/)에는 3.14 또는 3.13을 쓰세요")


@check("의존성")
def check_dependencies(ctx: Context) -> None:
    missing = [pkg for mod, pkg in DEPENDENCIES if importlib.util.find_spec(mod) is None]
    ctx.deps_ok = all(importlib.util.find_spec(mod) is not None for mod in _CORE_MODULES)
    if missing:
        ctx.report.fail("의존성", f"없음: {', '.join(missing)} → pip install -r requirements.txt")
    else:
        ctx.report.ok("의존성", ", ".join(pkg for _, pkg in DEPENDENCIES))


@check("UTF-8 모드")
def check_utf8(ctx: Context) -> None:
    if sys.flags.utf8_mode:
        ctx.report.ok("UTF-8 모드", "켜짐")
    else:
        ctx.report.info("UTF-8 모드", "꺼짐. 환경변수 PYTHONUTF8=1을 설정하면 더 안전합니다")


@check("config.yaml")
def check_config(ctx: Context) -> None:
    if not ctx.deps_ok:
        ctx.report.info("설정", "의존성이 없어 설정·연결 점검을 건너뜁니다")
        return
    from lib import config

    try:
        ctx.cfg = config.load(ctx.root_arg)
    except config.BlocklistError as exc:
        # 차단 목록을 믿을 수 없다. 설정이 없으니 LLM도 부르지 않는다. 회사 환경이면 종료 5.
        ctx.report.fail("config.yaml", str(exc), public_host=exc.environment == "company")
        return
    except core.ConfigError as exc:
        ctx.report.fail("config.yaml", str(exc))
        return
    ctx.report.ok("config.yaml", f"읽음 ({Path(ctx.cfg.config_dir) / config.CONFIG_NAME})")


@check("config.local.yaml")
def check_local(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None:
        return
    if not cfg.local_exists:
        ctx.report.fail("config.local.yaml", "없음 → kb.py init 을 실행하세요")
        return
    ctx.report.ok("config.local.yaml", f"environment: {cfg.environment}")


@check("공개 호스트 차단")
def check_public_hosts(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None:
        return
    if cfg.environment != "company":
        ctx.report.info("공개 호스트 차단", "집 환경이라 검사하지 않습니다")
        ctx.public_check_done = True
        return
    try:
        _scan_public_hosts(ctx)
    except Exception as exc:  # 검사가 깨지면 모든 프로필을 공개로 본다(안전 쪽)
        ctx.public_profiles.update(_all_profile_names(cfg))
        ctx.report.fail(
            "공개 호스트 차단",
            f"검사하지 못했습니다 ({type(exc).__name__}: {exc}) → LLM을 부르지 않습니다. "
            "llm.profiles와 llm.public_hosts_blocklist를 확인하세요",
            public_host=True,
        )
        return
    ctx.public_check_done = True


def _all_profile_names(cfg: Any) -> set[str]:
    names: set[str] = set()
    try:
        raw = cfg.get("llm.profiles")
        if isinstance(raw, Mapping):
            names.update(str(k) for k in raw)
    except Exception:
        pass
    try:
        names.update(_profiles_to_check(cfg))
    except Exception:
        pass
    return names


def _scan_public_hosts(ctx: Context) -> None:
    from lib import config

    cfg = ctx.cfg
    blocklist = cfg.blocklist | config.CODE_BLOCKLIST
    for name, profile in cfg.profiles().items():
        url = profile.get("base_url")
        if not isinstance(url, str) or not config.is_public_host(url, blocklist):
            continue
        ctx.public_profiles.add(name)
        host = config.checked_host(url)
        if host is None:
            text = (
                f"주소에서 호스트를 하나로 정할 수 없습니다 ({url!r}) → '\\'·'@'·공백·한글 없이 "
                "사내 LLM 주소를 다시 쓰세요 (kb.py init --section llm)"
            )
        else:
            text = (
                f"공개 LLM 호스트입니다 ({host}) → 회사 자료가 밖으로 나갈 수 있습니다. "
                "사내 LLM 주소로 바꾸세요 (kb.py init --section llm)"
            )
        ctx.report.fail(f"LLM {name} 주소", text, public_host=True)
    if not ctx.public_profiles:
        ctx.report.ok("공개 호스트 차단", "공개 LLM 주소 없음")


def _resolve(ctx: Context, label: str, ref: Any) -> None:
    from lib import secrets as kb_secrets

    try:
        value = kb_secrets.resolve(ref, ctx.cfg)
    except core.KbError as exc:
        ctx.secrets[label] = None
        ctx.secret_errors[label] = core.redact(str(exc))
        return
    ctx.secrets[label] = value
    if value is None:
        ctx.secret_errors[label] = kb_secrets.describe_missing(ref)


@check("비밀값")
def check_secrets(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    items: list[tuple[str, Any, bool]] = [("jira.pat_ref", cfg.get("jira.pat_ref"), True)]
    if _truthy(cfg.get("confluence.enabled"), True):
        items.append(("confluence.pat_ref", cfg.get("confluence.pat_ref"), True))
    llm_on = _truthy(cfg.get("llm.enabled"), False)
    for name, profile in cfg.profiles().items():
        if profile.get("api_key_ref") not in (None, ""):
            items.append((f"llm.profiles.{name}.api_key_ref", profile["api_key_ref"], llm_on))
    for label, ref, counts in items:
        _resolve(ctx, label, ref)
        name = f"비밀값 {label}"
        error = ctx.secret_errors.get(label)
        if error is None:
            ctx.report.ok(name, "설정됨")
        elif counts:
            ctx.report.fail(name, error)
        else:
            ctx.report.info(name, f"{error} (LLM 비활성)")


@check("keyring")
def check_keyring(ctx: Context) -> None:
    if not ctx.deps_ok:
        return
    from lib import secrets as kb_secrets

    ctx.report.info("keyring", kb_secrets.backend_name())


def _base_url(ctx: Context, section: str, name: str) -> str | None:
    url = ctx.cfg.get(f"{section}.base_url")
    if not isinstance(url, str) or not url.strip():
        ctx.report.fail(name, f"{section}.base_url이 없습니다 → kb.py init 을 실행하세요")
        return None
    return url.strip().rstrip("/")


@check("Jira")
def check_jira(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    base = _base_url(ctx, "jira", "Jira")
    if base is None:
        return
    token = ctx.secrets.get("jira.pat_ref")
    if not token:
        ctx.report.fail("Jira", f"PAT가 없어 확인하지 못했습니다 → {ctx.secret_errors.get('jira.pat_ref', '')}")
        return
    resp, error = _get(f"{base}/rest/api/2/myself", token, _conn(cfg, cfg.get("jira")), auth_raises=True)
    if resp is None:
        ctx.report.fail("Jira", error or "알 수 없는 오류")
        return
    from lib import net

    data = _json(resp)
    if resp.status_code == 200 and isinstance(data, Mapping):
        who = data.get("displayName") or data.get("name") or "?"
        ctx.report.ok("Jira", f"{who} 으로 연결 ({base})")
        ctx.jira_base = base
    elif resp.status_code == 200:
        ctx.report.fail("Jira", "응답이 JSON이 아닙니다 → base_url이 Jira 서버 주소인지 확인하세요")
    else:
        ctx.report.fail("Jira", f"{net.describe_status(resp)} → base_url을 확인하세요 ({base})")


def _jira_client(ctx: Context):
    from lib import jira_client

    cfg = ctx.cfg
    return jira_client.JiraClient(ctx.jira_base, ctx.secrets.get("jira.pat_ref"), conn=_conn(cfg, cfg.get("jira")))


@check("Jira 세부")
def check_jira_details(ctx: Context) -> None:
    """Jira에 연결된 뒤에만: 서버 버전, sprint 필드, jql_mine·뷰 JQL 확인(D49, D51).

    JQL은 maxResults=0 검색으로 확인하고, 400이면 Jira의 메시지를 그대로 보여 준다.
    """
    cfg = ctx.cfg
    if cfg is None or not ctx.jira_base or not ctx.secrets.get("jira.pat_ref"):
        return
    with _jira_client(ctx) as client:
        _jira_details(ctx, client)


def _jira_details(ctx: Context, client: Any) -> None:
    from lib import net

    cfg = ctx.cfg
    try:
        info = client.server_info()
    except core.KbError as exc:
        ctx.report.info("Jira 서버", f"버전을 읽지 못했습니다 ({net.error_text(exc)})")
    else:
        info = info if isinstance(info, Mapping) else {}
        version = info.get("version") or "?"
        kind = info.get("deploymentType")
        ctx.report.info("Jira 서버", f"{version}" + (f" ({kind})" if kind else ""))

    configured = cfg.get("jira.sprint_field")
    try:
        field_id = client.sprint_field_id()
    except core.KbError as exc:
        ctx.report.info("sprint 필드", f"확인하지 못했습니다 ({net.error_text(exc)})")
    else:
        text = field_id or "없음"
        if configured and configured != field_id:
            text += f" (설정값 {configured}와 다릅니다 → kb.py init --section jira)"
        ctx.report.info("sprint 필드", text)

    items: list[tuple[str, Any, str]] = [("JQL jql_mine", cfg.get("jira.jql_mine"), "jira.jql_mine")]
    views = cfg.get("jira.views")
    if isinstance(views, Mapping):
        items.extend((f"JQL 뷰 {name}", jql, f"jira.views.{name}") for name, jql in views.items())
    elif views not in (None, ""):
        ctx.report.fail("JQL 뷰", "jira.views는 '이름: JQL' 묶음이어야 합니다 → config.yaml·config.local.yaml을 고치세요")
    for label, jql, where in items:
        if not isinstance(jql, str) or not jql.strip():
            ctx.report.fail(label, f"비어 있습니다 → {where}에 JQL을 쓰세요")
            continue
        try:
            error = client.validate_jql(jql)
        except core.KbError as exc:
            ctx.report.fail(label, f"확인하지 못했습니다: {net.error_text(exc)}")
            continue
        if error:
            ctx.report.fail(label, f"{error} → config.local.yaml(또는 config.yaml)의 {where}를 고치세요")
        else:
            ctx.report.ok(label, "유효")


@check("Confluence")
def check_confluence(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    if not _truthy(cfg.get("confluence.enabled"), True):
        ctx.report.info("Confluence", "비활성")
        return
    base = _base_url(ctx, "confluence", "Confluence")
    if base is None:
        return
    token = ctx.secrets.get("confluence.pat_ref")
    if not token:
        ctx.report.fail(
            "Confluence", f"PAT가 없어 확인하지 못했습니다 → {ctx.secret_errors.get('confluence.pat_ref', '')}"
        )
        return
    resp, error = _get(f"{base}/rest/api/user/current", token, _conn(cfg, cfg.get("confluence")), auth_raises=True)
    if resp is None:
        ctx.report.fail("Confluence", error or "알 수 없는 오류")
        return
    from lib import net

    data = _json(resp)
    if resp.status_code == 200 and isinstance(data, Mapping):
        if data.get("type") == "anonymous" or not data.get("username"):
            ctx.report.fail("Confluence", "익명 사용자로 응답했습니다 → PAT를 확인하세요")
            return
        shown = data["username"]
        if data.get("displayName"):
            shown = f"{shown} ({data['displayName']})"
        ctx.report.ok("Confluence", f"{shown} 으로 연결 ({base})")
    elif resp.status_code == 200:
        ctx.report.fail("Confluence", "응답이 JSON이 아닙니다 → base_url이 Confluence 서버 주소인지 확인하세요")
    else:
        ctx.report.fail("Confluence", f"{net.describe_status(resp)} → base_url을 확인하세요 ({base})")


def _model_ids(data: Any) -> list[str] | None:
    items = data.get("data") if isinstance(data, Mapping) else data
    if not isinstance(items, list):
        return None
    return [str(x.get("id")) for x in items if isinstance(x, Mapping) and x.get("id") is not None]


def _check_profile(ctx: Context, name: str, profiles: Mapping[str, dict]) -> None:
    from lib import net

    label = f"LLM {name}"
    profile = profiles.get(name)
    if profile is None:
        ctx.report.fail(label, f"프로필이 없습니다 → llm.fallback_order에서 빼거나 llm.profiles.{name}을 추가하세요")
        return
    if name in ctx.public_profiles:
        ctx.report.info(label, "공개 LLM 호스트라 호출하지 않았습니다")
        return
    if ctx.cfg.environment != "home" and not ctx.public_check_done:
        ctx.report.info(label, "공개 호스트 검사가 끝나지 않아 호출하지 않았습니다")
        return
    base = profile.get("base_url")
    if not isinstance(base, str) or not base.strip():
        ctx.report.fail(label, f"llm.profiles.{name}.base_url이 없습니다 → kb.py init --section llm")
        return
    base = base.strip().rstrip("/")
    from lib import llm_client

    try:
        llm_client.assert_host_allowed(ctx.cfg, f"{base}/models")  # 공개 호스트 검사와 겹치지만 보내기 직전에 한 번 더
    except core.PublicHostRefused as exc:
        ctx.report.fail(label, f"{exc}", public_host=True)
        return
    model = str(profile.get("model") or "")
    ref_label = f"llm.profiles.{name}.api_key_ref"
    has_ref = profile.get("api_key_ref") not in (None, "")
    key = ctx.secrets.get(ref_label) if has_ref else None
    key_missing = has_ref and not key
    resp, error = _get(f"{base}/models", key, _conn(ctx.cfg, profile), auth_raises=False)
    if resp is None:
        ctx.report.fail(label, error or "알 수 없는 오류")
        return
    status = resp.status_code
    if status in (401, 403):
        if key:
            ctx.report.fail(label, f"키 거부 ({status}) → 키를 다시 저장하세요 (kb.py init --section llm)")
        elif key_missing:
            ctx.report.fail(label, f"키가 필요합니다 ({status}) → {ctx.secret_errors.get(ref_label, '')}")
        else:
            ctx.report.fail(label, f"키가 필요합니다 ({status}) → 프로필에 api_key_ref를 넣으세요 (kb.py init --section llm)")
        return
    if 300 <= status < 400:
        ctx.report.fail(label, f"{net.describe_status(resp)} → base_url을 최종 주소로 바꾸세요")
        return
    note = " (키 없이 확인)" if key_missing else ""
    if status == 404:
        ctx.llm_reachable.add(name)
        ctx.report.info(label, f"연결됨, /models 없음 (404){note}")
        return
    if not 200 <= status < 300:
        ctx.report.fail(label, f"{net.describe_status(resp)} → 서버 상태와 base_url을 확인하세요 ({base})")
        return
    ctx.llm_reachable.add(name)
    ids = _model_ids(_json(resp))
    if ids is None:
        ctx.report.info(label, f"연결됨, 모델 목록을 읽지 못함{note}")
    elif model and model in ids:
        ctx.report.ok(label, f"{model} 목록에 있음 ({base}){note}")
    else:
        ctx.report.info(label, f"연결됨, 모델 목록에 없음 ({model or '모델 미설정'}){note}")


SKIPPED_PUBLIC_TEXT = "공개 LLM 호스트라 호출하지 않았습니다"


def _company_llm_blocked(ctx: Context) -> str | None:
    """회사 환경(home 아님)에서 LLM을 하나도 부르면 안 되는 이유. 부를 수 있으면 None.

    LlmClient와 같은 규칙(D44): 어느 프로필이든 공개 호스트면 전부 거부. 공개 호스트 검사 결과와
    LlmClient 생성 둘 다 본다.
    """
    cfg = ctx.cfg
    if cfg is None or cfg.environment == "home":
        return None
    if ctx.public_profiles:
        names = ", ".join(sorted(ctx.public_profiles))
        return f"회사 환경에 공개 LLM 호스트 프로필({names})이 있어 어떤 LLM도 호출하지 않았습니다 (D44)"
    from lib import llm_client

    try:
        llm_client.LlmClient(cfg)
    except core.PublicHostRefused:
        return "회사 환경에 공개 LLM 호스트 프로필이 있어 어떤 LLM도 호출하지 않았습니다 (D44)"
    except Exception:  # 다른 설정 문제는 프로필별 점검이 알린다
        return None
    return None


@check("LLM")
def check_llm(ctx: Context) -> None:
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    if not _truthy(cfg.get("llm.enabled"), False):
        ctx.report.info("LLM", "비활성")
        return
    profiles = cfg.profiles()
    names = _profiles_to_check(cfg)
    if not names:
        ctx.report.fail("LLM", "확인할 프로필이 없습니다 → kb.py init --section llm")
        return
    blocked = _company_llm_blocked(ctx)
    if blocked is not None:
        # D44: 회사 환경에서 공개 호스트 프로필이 하나라도 있으면(쓰지 않는 프로필이어도) 어떤 LLM에도 보내지 않는다
        for name in names:
            ctx.report.info(f"LLM {name}", SKIPPED_PUBLIC_TEXT if name in ctx.public_profiles else blocked)
        return
    for name in names:
        try:
            _check_profile(ctx, name, profiles)
        except Exception as exc:  # 프로필 하나의 실패가 나머지를 막지 않게
            ctx.report.fail(f"LLM {name}", f"예상치 못한 오류: {type(exc).__name__}: {exc}")


@check("탐지")
def check_detected(ctx: Context) -> None:
    """기록된 detected 재검증(D48, 프로필마다 채팅 3회 이하, 저장 안 함). --redetect면 다시 탐지해 저장."""
    cfg = ctx.cfg
    if cfg is None or not cfg.local_exists:
        return
    if not _truthy(cfg.get("llm.enabled"), False):
        if ctx.redetect:
            ctx.report.info("탐지", "LLM이 비활성이라 다시 탐지하지 않았습니다 → llm.enabled: true (kb.py init --section llm)")
        return
    from lib import llm_client

    profiles = cfg.profiles()
    client = None
    refused: str | None = None
    try:
        client = llm_client.LlmClient(cfg)  # 회사 차단(D44): 공개 호스트 프로필이 하나라도 있으면 아무것도 보내지 않는다
    except core.PublicHostRefused as exc:
        refused = str(exc)
    for name in _profiles_to_check(cfg):
        profile = profiles.get(name)
        if profile is None:
            continue
        label = f"탐지 {name}"
        try:
            if ctx.redetect:
                _redetect_profile(ctx, client, refused, name, label)
            else:
                _verify_profile(ctx, client, refused, name, profile, label)
        except core.PublicHostRefused as exc:
            ctx.report.fail(label, str(exc), public_host=True)
        except Exception as exc:  # 프로필 하나의 실패가 나머지를 막지 않게
            ctx.report.fail(label, f"예상치 못한 오류: {type(exc).__name__}: {core.redact(str(exc))}")


def _call_blocked(ctx: Context, refused: str | None, name: str, label: str, what: str) -> bool:
    """이 프로필에 채팅을 보내면 안 되면 '-' 줄을 쓰고 True."""
    if refused is not None or name in ctx.public_profiles:
        ctx.report.info(label, f"회사 환경의 공개 호스트 차단으로 {what}하지 않았습니다")
        return True
    if ctx.cfg.environment != "home" and not ctx.public_check_done:
        ctx.report.info(label, f"공개 호스트 검사가 끝나지 않아 {what}하지 않았습니다")
        return True
    if name not in ctx.llm_reachable:
        ctx.report.info(label, f"연결되지 않아 {what}하지 않았습니다 (위 LLM {name} 줄 참고)")
        return True
    return False


def _redetect_profile(ctx: Context, client: Any, refused: str | None, name: str, label: str) -> None:
    if _call_blocked(ctx, refused, name, label, "다시 탐지"):
        return
    import init
    from lib import llm_detect, net

    try:
        detected = llm_detect.detect(client, name)
    except core.PublicHostRefused:
        raise
    except core.KbError as exc:
        ctx.report.fail(label, f"다시 탐지하지 못했습니다: {net.error_text(exc)} → 서버·모델 이름을 확인하세요 (kb.py init --section llm)")
        return
    init.save_detected(ctx.cfg.config_dir, name, detected)
    ctx.report.ok(label, f"다시 탐지해 저장했습니다: {init.summarize_detected(detected)}")
    for warning in detected.get("warnings") or []:
        ctx.report.info(f"{label} 주의", str(warning))


def _verify_profile(ctx: Context, client: Any, refused: str | None, name: str, profile: Mapping[str, Any], label: str) -> None:
    detected = profile.get("detected")
    if detected == "pending":
        ctx.report.info(label, _PENDING)
        return
    if not isinstance(detected, Mapping) or not detected:
        ctx.report.info(label, _NOT_DETECTED)
        return
    if _call_blocked(ctx, refused, name, label, "재검증"):
        return
    problems, done = verify_detected(client, name, detected)
    calls = f"호출 {len(done)}회" + (f": {', '.join(done)}" if done else "")
    if problems:
        ctx.report.fail(label, f"기록과 다릅니다 ({calls}): {'; '.join(problems)} → kb.py doctor --redetect로 다시 탐지하세요")
    else:
        ctx.report.ok(label, f"기록과 같습니다 ({calls}, 저장 안 함)")


def _verify_chat(client: Any, name: str, messages: list[dict], **kw: Any) -> tuple[Any, str | None]:
    from lib import llm_client, llm_detect, net

    try:
        return client.chat(name, messages, max_tokens=llm_detect.PROBE_MAX_TOKENS, **kw), None
    except core.PublicHostRefused:
        raise
    except llm_client.ChatHttpError as exc:
        return None, f"{exc.status} {exc.body[:120]}".strip()
    except core.KbError as exc:
        return None, net.error_text(exc)


def _json_object(text: str | None) -> bool:
    try:
        return isinstance(json.loads(text or ""), dict)
    except ValueError:
        return False


def _tokens(result: Any) -> int | None:
    usage = result.usage if isinstance(getattr(result, "usage", None), Mapping) else None
    value = usage.get("completion_tokens") if usage else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def verify_detected(client: Any, name: str, detected: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """기록된 탐지 결과를 채팅 3회 이하로 확인한다. (문제 목록, 한 호출 이름 목록). 아무것도 저장하지 않는다.

    호출은 실제 작업처럼 `client.chat(reasoning=…, json_mode=…)`로 보내 기록된 토글·JSON 모드를 그대로 쓴다.
    """
    from lib import llm_detect

    problems: list[str] = []
    done: list[str] = []
    toggle = detected.get("reasoning_toggle") if isinstance(detected.get("reasoning_toggle"), Mapping) else {}
    method = toggle.get("method")
    switchable = method in ("param", "system_prefix")
    raw_notes = detected.get("notes")
    notes = {str(x) for x in raw_notes} if isinstance(raw_notes, list) else set()
    json_on = detected.get("json_mode") is True

    # 1) 끄기(+JSON 모드)
    if json_on:
        messages = [
            {"role": "system", "content": llm_detect.JSON_SYSTEM_PROMPT},
            {"role": "user", "content": llm_detect.JSON_PROMPT},
        ]
    else:
        messages = [
            {"role": "system", "content": llm_detect.SYSTEM_PROMPT},
            {"role": "user", "content": llm_detect.ARITHMETIC_PROMPT},
        ]
    off, error = _verify_chat(client, name, messages, reasoning=False, json_mode=json_on)
    done.append("끄기·JSON" if json_on else "끄기")
    if off is None:
        problems.append(f"끄기 호출 실패({error})")
    else:
        trace = llm_detect.has_trace(off)
        if switchable and trace:
            problems.append("추론 끄기가 듣지 않습니다(생각 흔적 있음)")
        if not switchable and "baseline_reasoning" in detected and trace != bool(detected.get("baseline_reasoning")):
            recorded = "켜짐" if detected.get("baseline_reasoning") else "꺼짐"
            problems.append(f"기준 상태가 기록(생각 {recorded})과 다릅니다")
        if json_on:
            ok = _json_object(off.content)
            if not ok and "json_in_reasoning" in notes and not off.content.strip() and off.reasoning:
                try:
                    from lib import llm_client

                    llm_client.parse_json_object(off.reasoning)
                    ok = True
                except ValueError:
                    ok = False
            if not ok:
                problems.append(f"JSON 모드 답이 JSON 객체가 아닙니다(finish={off.finish_reason})")
        elif not off.content.strip() and not (trace and not switchable):
            problems.append(f"끄기 상태의 답이 비었습니다(finish={off.finish_reason})")

    # 2) 켜기: 토글이 있을 때만
    if switchable:
        on, error = _verify_chat(
            client,
            name,
            [{"role": "system", "content": llm_detect.SYSTEM_PROMPT}, {"role": "user", "content": llm_detect.ARITHMETIC_PROMPT}],
            reasoning=True,
            json_mode=False,
        )
        done.append("켜기")
        if on is None:
            problems.append(f"켜기 호출 실패({error})")
        elif not llm_detect.has_trace(on):
            on_tokens, off_tokens = _tokens(on), (_tokens(off) if off is not None else None)
            by_tokens = (
                off is not None
                and not llm_detect.has_trace(off)
                and on_tokens is not None
                and off_tokens is not None
                and on_tokens >= llm_detect.TOKEN_RATIO * off_tokens
                and on_tokens - off_tokens >= llm_detect.TOKEN_GAP
            )
            if not by_tokens:
                problems.append("추론 켜기가 듣지 않습니다(생각 흔적 없음)")

    # 3) 비전: 참으로 기록됐을 때만
    if detected.get("vision") is True:
        user = [
            {"type": "text", "text": llm_detect.VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": llm_detect.red_png_data_url()}},
        ]
        seen, error = _verify_chat(
            client, name, [{"role": "system", "content": llm_detect.SYSTEM_PROMPT}, {"role": "user", "content": user}],
            reasoning=False, json_mode=False,
        )
        done.append("비전")
        if seen is None:
            problems.append(f"비전 호출 실패({error})")
        elif not (
            _RED.search(seen.content or "")
            or ("vision_in_reasoning" in notes and not (seen.content or "").strip() and _RED.search(seen.reasoning or ""))
        ):
            problems.append("비전: 빨강 그림에 빨강이라고 답하지 않습니다(이미지를 보지 않음)")
    return problems, done


@check("쓰기 권한")
def check_write(ctx: Context) -> None:
    state = ctx.vault_root() / ".state"
    probe = state / f".doctor-{os.getpid()}.tmp"
    try:
        state.mkdir(exist_ok=True)  # 루트 자체는 만들지 않는다
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        ctx.report.fail("쓰기 권한", f"{state}: 쓰지 못했습니다 ({exc.strerror or exc}) → 폴더 권한과 동기화 프로그램 잠금을 확인하세요")
        return
    ctx.report.ok("쓰기 권한", str(state))


@check("볼트 경로")
def check_path(ctx: Context) -> None:
    root = ctx.vault_root()
    text = str(root)
    if text.isascii():
        ctx.report.info("볼트 경로", f"{text} (ASCII만)")
    else:
        ctx.report.info("볼트 경로", f"{text} (한글 등 ASCII 아닌 문자 포함, UTF-8로 처리)")


@check("PowerShell 실행 정책")
def check_powershell(ctx: Context) -> None:
    name = "PowerShell 실행 정책"
    system_root = os.environ.get("SystemRoot")
    builtin = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe" if system_root else None
    exe = str(builtin) if builtin is not None and builtin.is_file() else shutil.which("powershell.exe")
    if exe is None:
        ctx.report.info(name, "powershell.exe를 찾지 못했습니다. 클립보드 이미지는 F(파일)로 캡처하세요")
        return
    result = _run_powershell(exe, _POLICY_COMMAND)
    if isinstance(result, str):
        ctx.report.info(name, result)
        return
    scopes, effective = _parse_policies(result.stdout or "")
    if result.returncode != 0 or effective is None:
        first = next((line.strip() for line in (result.stderr or "").splitlines() if line.strip()), "출력 없음")
        ctx.report.info(name, f"확인하지 못했습니다 ({first[:120]})")
        return
    # 그룹 정책(MachinePolicy·UserPolicy)은 -ExecutionPolicy Bypass보다 앞선다(D12).
    for scope in _GPO_SCOPES:
        if scopes.get(scope) in _BLOCKING_POLICIES:
            ctx.report.fail(
                name,
                f"그룹 정책 {scope}가 {scopes[scope]}입니다. 클립보드 이미지 스크립트를 실행할 수 없습니다 → F(파일) 캡처를 쓰세요",
            )
            return
    scope = next((s for s in _SCOPE_ORDER if scopes.get(s) not in (None, "Undefined")), None)
    where = f", {scope} 범위" if scope else ", 기본값"
    ctx.report.info(name, f"{effective}{where} (클립보드 스크립트는 -ExecutionPolicy Bypass로 실행합니다)")


_POLICY_COMMAND = (
    "Get-ExecutionPolicy -List | ForEach-Object { [string]$_.Scope + '=' + [string]$_.ExecutionPolicy }; "
    "'Effective=' + [string](Get-ExecutionPolicy)"
)
_SCOPE_ORDER = ("MachinePolicy", "UserPolicy", "Process", "CurrentUser", "LocalMachine")
_GPO_SCOPES = ("MachinePolicy", "UserPolicy")
_BLOCKING_POLICIES = ("Restricted", "AllSigned")


def _parse_policies(stdout: str) -> tuple[dict[str, str], str | None]:
    scopes: dict[str, str] = {}
    effective = None
    for line in stdout.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or not value.strip():
            continue
        if key.strip() == "Effective":
            effective = value.strip()
        else:
            scopes[key.strip()] = value.strip()
    return scopes, effective


def _powershell_env() -> dict[str, str]:
    """5.1에 넘길 환경. 부모(pwsh 7 등)의 설정이 결과를 바꾸지 않게 뺀다.

    - PSModulePath: 7의 경로가 있으면 5.1이 Security 모듈을 못 읽는다.
    - PSExecutionPolicyPreference: 부모의 Process 범위 Bypass가 그대로 보인다.
    """
    drop = {"PSMODULEPATH", "PSEXECUTIONPOLICYPREFERENCE"}
    return {k: v for k, v in os.environ.items() if k.upper() not in drop}


def _run_powershell(exe: str, command: str) -> subprocess.CompletedProcess | str:
    """실행 결과 또는 안내 문장. 5.1은 출력을 콘솔 OEM 코드 페이지(cp949 등)로 쓴다."""
    try:
        return subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="oem" if sys.platform == "win32" else "utf-8",
            errors="replace",
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
            env=_powershell_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return f"{TIMEOUT}초 안에 응답하지 않았습니다"
    except OSError as exc:
        return f"실행하지 못했습니다 ({exc.strerror or exc})"


def _offset_text(offset: Any) -> str:
    if offset is None:
        return "?"
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _zoneinfo_available(name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
    except Exception:
        return False
    return True


@check("시간대")
def check_timezone(ctx: Context) -> None:
    """설정 시간대와 OS 시간대를 견준다(D04). 늘 '-'이다."""
    cfg = ctx.cfg
    if cfg is None:
        return
    import datetime as dt

    tz_name = str(cfg.get("timezone") or "Asia/Seoul")
    tz = core.resolve_tz(tz_name)
    now = dt.datetime.now(dt.timezone.utc)
    configured = now.astimezone(tz).utcoffset()
    local = now.astimezone().utcoffset()
    text = f"설정 {tz_name}({_offset_text(configured)}) · OS {_offset_text(local)}"
    notes = []
    if configured != local:
        notes.append("OS 시간대가 설정과 다릅니다. 기록 시각은 설정 시간대를 따릅니다")
    if not _zoneinfo_available(tz_name):
        if isinstance(tz, dt.timezone) and tz.tzname(None) == tz_name:
            notes.append("tzdata가 없어 고정 표를 씁니다")
        else:
            notes.append("tzdata가 없고 고정 표에도 없어 OS 시간대를 씁니다")
    if notes:
        text += " → " + ". ".join(notes)
    ctx.report.info("시간대", text)


@check("편집기")
def check_editors(ctx: Context) -> None:
    emacs = shutil.which("emacs") or shutil.which("runemacs")
    if emacs:
        client = shutil.which("emacsclient")
        ctx.report.info("Emacs", emacs + (f", emacsclient {client}" if client else ", emacsclient 없음"))
    else:
        ctx.report.info("Emacs", "PATH에 없습니다. emacs/work-kb.el을 쓰려면 Emacs 30 이상을 설치하세요")
    obsidian = shutil.which("Obsidian") or shutil.which("obsidian")
    if obsidian is None:
        local = os.environ.get("LOCALAPPDATA")
        candidate = Path(local) / "Programs" / "Obsidian" / "Obsidian.exe" if local else None
        if candidate is not None and candidate.is_file():
            obsidian = str(candidate)
    if obsidian:
        ctx.report.info("Obsidian", obsidian)
    else:
        ctx.report.info("Obsidian", "찾지 못했습니다. 위키를 보려면 Obsidian을 설치하세요 (선택)")


CHECKS: list[Checker] = [
    check_python,
    check_dependencies,
    check_utf8,
    check_config,
    check_local,
    check_public_hosts,
    check_secrets,
    check_keyring,
    check_jira,
    check_jira_details,
    check_confluence,
    check_llm,
    check_detected,
    check_write,
    check_path,
    check_timezone,
    check_powershell,
    check_editors,
]


def run_checks(ctx: Context, checks: list[Checker] | None = None) -> Report:
    for func in CHECKS if checks is None else checks:
        try:
            func(ctx)
        except Exception as exc:  # 점검 하나의 오류가 doctor 전체를 멈추지 않게
            label = getattr(func, "label", func.__name__)
            ctx.report.fail(label, f"예상치 못한 오류: {type(exc).__name__}: {exc}")
    return ctx.report


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py doctor", "연결·환경 점검. ✓ 정상, ✗ 문제, - 참고")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 목록으로 출력")
    parser.add_argument("--redetect", action="store_true", help="LLM 탐지를 다시 해서 config.local.yaml에 저장")
    args = parser.parse_args(argv)
    if args.redetect:
        core.refuse_if_agent("doctor --redetect")
    report = Report(echo=None if args.json else (lambda line: print(line, flush=True)))
    ctx = Context(root_arg=args.root, report=report, redetect=args.redetect)
    run_checks(ctx)
    if args.json:
        print(json.dumps(report.as_list(), ensure_ascii=False, indent=1))
    else:
        print(report.summary())
    return report.exit_code()


if __name__ == "__main__":
    core.run_main(main)
