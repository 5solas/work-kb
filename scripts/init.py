"""연결 설정 (`kb.py init`).

대화형 마법사 (§6.11, Phase 2 범위)
1. environment 선택(home/company). company면 공개 호스트 금지 안내.
2. Jira: base_url → PAT(가려서 입력) → 자격 증명 저장소 work-kb/jira → `/myself` 확인 →
   `/project` 목록에서 번호·키로 고르기 → project_keys. sprint 필드를 찾아 jira.sprint_field에 적고(D51),
   jql_mine·뷰 JQL을 maxResults=0으로 확인한다(D49, 실패해도 저장은 한다).
3. Confluence·LLM 단계는 이후 Phase에서 더한다(지금은 안내만).
4. config.local.yaml 저장(config.save_local, PAT는 쓰지 않는다) → doctor 실행, 그 종료 코드를 돌려준다.

재실행하면 기존 config.local.yaml 값이 기본값이다. 입력이 끝나면(EOF) 취소하고 종료 1, 설정은 그대로다.

옵션
- `--section jira`: 기존 설정 위에서 Jira 단계만. confluence·llm은 해당 Phase 안내(종료 4).
- `--non-interactive --from-env`: 묻지 않고 KB_INIT_ENVIRONMENT, KB_INIT_JIRA_URL, KB_INIT_JIRA_PAT,
  KB_INIT_JIRA_PROJECTS(쉼표 목록, 키 또는 번호)를 읽는다. 비어 있으면 기존 값, 그것도 없으면 종료 4.

`--dev` (D45)
- `config.local.example.yaml`의 `home` 블록을 그대로 읽는다. 코드에는 LLM 주소를 두지 않는다.
- 기존 config.local.yaml이 company 환경이면 거부한다(종료 7).
- 시험용 주소 덮어쓰기: KB_INIT_JIRA_URL, KB_INIT_CONFLUENCE_URL, KB_INIT_LLM_URL(mock 프로필).
  KB_INIT_LLM_KEY가 있으면 키를 자격 증명 저장소 work-kb/llm에 넣고 main 프로필이 그것을 가리키게 한다.
- 모의 PAT `mock-token`을 자격 증명 저장소 work-kb/jira, work-kb/confluence에 넣는다.
- config.local.yaml을 쓰고 doctor를 실행해 그 종료 코드를 돌려준다.
"""
from __future__ import annotations

import copy
import getpass
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from lib import core

CODE_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_NAME = "config.local.example.yaml"
SERVICE = "work-kb"
DEV_TOKEN = "mock-token"  # 모의 서버용 가짜 PAT. 비밀값이 아니다
REDETECT_PHASE_MESSAGE = "LLM 탐지 다시 하기(--redetect)는 Phase 4에서 추가됩니다."
SECTION_PHASE_MESSAGES = {
    "confluence": "Confluence 설정(--section confluence)은 Phase 5에서 추가됩니다.",
    "llm": "LLM 설정(--section llm)은 Phase 4에서 추가됩니다.",
}
MAX_TRIES = 3

# --from-env가 읽는 환경변수. 필드 이름 → 변수 이름
ENV_FIELDS = {
    "environment": "KB_INIT_ENVIRONMENT",
    "jira.base_url": "KB_INIT_JIRA_URL",
    "jira.pat": "KB_INIT_JIRA_PAT",
    "jira.projects": "KB_INIT_JIRA_PROJECTS",
}

_ENV_WORDS = {"home": "home", "집": "home", "company": "company", "회사": "company"}
_URL = re.compile(r"^https?://[^\s/]+", re.IGNORECASE)
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]+$")


class Cancelled(core.KbError):
    """입력이 끝나(EOF) 마법사를 취소함. 종료 1, 설정 파일은 바뀌지 않는다."""

    exit_code = 1


CANCEL_MESSAGE = "취소했습니다 (입력이 끝났습니다). config.local.yaml은 바뀌지 않았습니다"


# ---------------------------------------------------------------- 입력 함수

Ask = Callable[[str, "str | None"], str]
AskSecret = Callable[[str], str]


def default_ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ")
    except EOFError:
        raise Cancelled(CANCEL_MESSAGE) from None
    return answer.strip() or (default or "")


def default_ask_secret(prompt: str) -> str:
    try:
        value = getpass.getpass(f"{prompt} (입력이 보이지 않습니다): ")
    except EOFError:
        raise Cancelled(CANCEL_MESSAGE) from None
    value = value.strip()
    core.register_secret(value)
    return value


# ---------------------------------------------------------------- 마법사


class Wizard:
    """대화형 설정. 입력 함수를 주입할 수 있다(테스트용). `values`를 주면 묻지 않고 그 값을 쓴다(--from-env).

    `steps`의 각 함수는 (wizard, data)를 받아 data(config.local.yaml 내용)를 고친다.
    단계는 `wizard.answer`·`wizard.secret`으로 값을 얻는다. 주입한 ask가 EOFError를 내도 취소로 바뀐다.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        ask: Ask | None = None,
        ask_secret: AskSecret | None = None,
        out: Callable[[str], None] | None = None,
        steps: list[Callable[["Wizard", dict], None]] | None = None,
        values: Mapping[str, str] | None = None,
        cfg: Any = None,
    ) -> None:
        self.root = Path(root)
        self.ask = ask or default_ask
        self.ask_secret = ask_secret or default_ask_secret
        self.out = out or print
        self.steps: list[Callable[[Wizard, dict], None]] = list(steps or [])
        self.values = values  # None이면 대화형
        self.cfg = cfg  # 병합된 설정(lib.config.Config). 뷰 JQL·연결 설정을 읽는다

    @property
    def interactive(self) -> bool:
        return self.values is None

    def run(self, data: Mapping[str, Any] | None = None) -> dict:
        result = copy.deepcopy(dict(data or {}))
        try:
            for step in self.steps:
                step(self, result)
        except EOFError:
            raise Cancelled(CANCEL_MESSAGE) from None
        return result

    # 값 얻기 -------------------------------------------------------

    def _from_env(self, field: str) -> str:
        name = ENV_FIELDS[field]
        return str(self.values.get(name, "") if self.values is not None else "").strip()

    def answer(self, field: str, prompt: str, default: str | None = None) -> str:
        """필드 하나의 답. 비대화형이면 환경변수 → 기본값, 둘 다 없으면 ConfigError."""
        if not self.interactive:
            value = self._from_env(field) or (default or "")
            if not value:
                raise core.ConfigError(f"{ENV_FIELDS[field]}이(가) 필요합니다 ({prompt})")
            return value
        return (self.ask(prompt, default) or "").strip()

    def secret(self, field: str, prompt: str) -> str:
        if not self.interactive:
            value = self._from_env(field)
        else:
            value = (self.ask_secret(prompt) or "").strip()
        core.register_secret(value)
        return value

    def choose(self, field: str, prompt: str, default: str | None, parse: Callable[[str], Any]) -> Any:
        """답을 parse로 검사한다. 대화형이면 틀린 답에 이유를 알리고 다시 묻는다(MAX_TRIES번)."""
        for attempt in range(1, MAX_TRIES + 1):
            raw = self.answer(field, prompt, default)
            try:
                return parse(raw)
            except core.ConfigError as exc:
                if not self.interactive:
                    raise core.ConfigError(f"{ENV_FIELDS[field]}: {exc}") from None
                if attempt == MAX_TRIES:
                    raise
                self.out(f"  {exc}. 다시 입력하세요")
        raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------- 도우미


def _section(node: dict, key: str, where: str = EXAMPLE_NAME) -> dict:
    value = node.get(key)
    if value is None:
        value = node[key] = {}
    if not isinstance(value, dict):
        raise core.ConfigError(f"{where}: '{key}'는 '키: 값' 묶음이어야 합니다")
    return value


def _keyring_ref(name: str) -> str:
    return f"keyring:{SERVICE}/{name}"


def parse_environment(raw: str) -> str:
    value = _ENV_WORDS.get(raw.strip().lower())
    if value is None:
        raise core.ConfigError(f"'{raw}'은(는) 환경이 아닙니다. home 또는 company로 답하세요")
    return value


def parse_base_url(raw: str) -> str:
    text = raw.strip().rstrip("/")
    if not _URL.match(text) or any(ch.isspace() for ch in text):
        raise core.ConfigError(f"'{raw}'은(는) 주소가 아닙니다. http:// 또는 https://로 시작하는 Jira 주소를 쓰세요")
    return text


def parse_project_choice(raw: str, projects: list[dict]) -> list[str]:
    """'1,3'·'KB, OPS'·'2 KB' 꼴을 프로젝트 키 목록으로. 번호는 1부터, 키는 목록에 있어야 한다.

    목록이 비어 있으면(권한 등으로 안 보이면) 키 형식만 확인한다.
    """
    tokens = [t for t in re.split(r"[\s,]+", raw.strip()) if t]
    if not tokens:
        raise core.ConfigError("프로젝트를 하나 이상 고르세요")
    known = [str(p.get("key", "")).upper() for p in projects]
    keys: list[str] = []
    for token in tokens:
        if token.isdigit():
            index = int(token)
            if not known:
                raise core.ConfigError("프로젝트 목록이 비어 있어 번호를 쓸 수 없습니다. 키를 쓰세요")
            if not 1 <= index <= len(known):
                raise core.ConfigError(f"번호 {token}은(는) 목록에 없습니다 (1~{len(known)})")
            key = known[index - 1]
        else:
            key = token.upper()
            if not _PROJECT_KEY.match(key):
                raise core.ConfigError(f"'{token}'은(는) 프로젝트 키 형식이 아닙니다")
            if known and key not in known:
                raise core.ConfigError(f"'{token}'은(는) 볼 수 있는 프로젝트 목록에 없습니다")
        if key not in keys:
            keys.append(key)
    return keys


def _existing_keys(jira: Mapping[str, Any]) -> list[str]:
    raw = jira.get("project_keys")
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[\s,]+", raw) if x]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x).strip().upper() for x in raw if isinstance(x, (str, int)) and not isinstance(x, bool) and str(x).strip()]


def _conn(wizard: Wizard, jira: Mapping[str, Any]) -> dict[str, Any]:
    """연결 설정(ca_bundle·verify·trust_env·proxies). 상대 ca_bundle은 설정 폴더 기준."""
    merged: dict[str, Any] = {}
    if wizard.cfg is not None and isinstance(wizard.cfg.get("jira"), Mapping):
        merged.update(wizard.cfg.get("jira"))
    merged.update(jira)
    conn = {k: merged[k] for k in ("ca_bundle", "verify", "trust_env", "proxies") if merged.get(k) is not None}
    ca = conn.get("ca_bundle")
    if ca and not Path(str(ca)).expanduser().is_absolute():
        conn["ca_bundle"] = str(wizard.root / str(ca))
    return conn


def _stored_pat(ref: Any, cfg: Any) -> str | None:
    if not isinstance(ref, str) or not ref:
        return None
    from lib import secrets as kb_secrets

    try:
        return kb_secrets.resolve(ref, cfg)
    except core.KbError:
        return None


def _error_text(exc: BaseException) -> str:
    from lib import net

    return net.error_text(exc)


# ---------------------------------------------------------------- 단계: environment


def step_environment(wizard: Wizard, data: dict) -> None:
    from lib import config

    current = data.get("environment")
    if isinstance(current, str) and current.strip().lower() in config.ENVIRONMENTS:
        default: str | None = current.strip().lower()
    else:
        # 처음이면 대화형은 안전 쪽(company)을 기본값으로 보여 주고, --from-env는 값을 요구한다
        default = "company" if wizard.interactive else None
    wizard.out("\n[1] 환경")
    environment = wizard.choose("environment", "환경 (home=집 개발용, company=회사)", default, parse_environment)
    data["environment"] = environment
    if environment != "company":
        return
    examples = ", ".join(sorted(config.CODE_BLOCKLIST)[:3])
    wizard.out(
        f"  회사 환경입니다. 공개 클라우드 LLM 주소({examples} 등)는 쓸 수 없습니다. "
        "코드가 호출을 막고 doctor가 ✗로 알립니다. 사내 서버 주소만 쓰세요."
    )
    profiles = (data.get("llm") or {}).get("profiles") if isinstance(data.get("llm"), Mapping) else None
    if isinstance(profiles, Mapping):
        blocklist = config.CODE_BLOCKLIST
        if wizard.cfg is not None:
            blocklist = blocklist | wizard.cfg.blocklist
        for name, profile in profiles.items():
            url = profile.get("base_url") if isinstance(profile, Mapping) else None
            if isinstance(url, str) and config.is_public_host(url, blocklist):
                wizard.out(f"  주의: LLM 프로필 '{name}'이(가) 공개 호스트를 가리킵니다 → kb.py init --section llm(Phase 4)으로 바꾸세요")


# ---------------------------------------------------------------- 단계: jira


def _connect(wizard: Wizard, jira: dict):
    """base_url → PAT → 저장 → /myself. (client, myself)를 돌려준다. 대화형이면 실패 시 다시 묻는다."""
    from lib import jira_client
    from lib import secrets as kb_secrets

    url_default = jira.get("base_url") if isinstance(jira.get("base_url"), str) and jira.get("base_url") else None
    existing_pat = _stored_pat(jira.get("pat_ref"), wizard.cfg)
    for attempt in range(1, MAX_TRIES + 1):
        base_url = wizard.choose("jira.base_url", "Jira 주소 (예: https://jira.example.invalid)", url_default, parse_base_url)
        url_default = base_url
        hint = " (비우면 저장된 값 유지)" if existing_pat else ""
        pat = wizard.secret("jira.pat", f"Jira 개인 액세스 토큰(PAT){hint}")
        if not pat:
            if not existing_pat:
                if not wizard.interactive:
                    raise core.ConfigError(f"{ENV_FIELDS['jira.pat']}이(가) 필요합니다 (자격 증명 저장소에 저장된 PAT도 없습니다)")
                if attempt == MAX_TRIES:
                    raise core.ConfigError("PAT를 입력하지 않았습니다")
                wizard.out("  PAT가 비었습니다. 다시 입력하세요")
                continue
            pat = existing_pat
            wizard.out(f"  저장된 PAT를 씁니다 ({SERVICE}/jira)")
        else:
            kb_secrets.store(SERVICE, "jira", pat)
            wizard.out(f"  자격 증명 저장소에 저장했습니다 ({SERVICE}/jira)")
            existing_pat = pat
        client = jira_client.JiraClient(base_url, pat, conn=_conn(wizard, jira))
        try:
            me = client.myself()
        except core.KbError as exc:
            client.close()
            if not wizard.interactive or attempt == MAX_TRIES:
                raise
            wizard.out(f"  연결하지 못했습니다: {_error_text(exc)}")
            wizard.out("  주소와 PAT를 다시 입력하세요")
            existing_pat = None if isinstance(exc, core.AuthError) else existing_pat
            continue
        jira["base_url"] = base_url
        jira["pat_ref"] = _keyring_ref("jira")
        return client, me if isinstance(me, Mapping) else {}
    raise AssertionError("unreachable")  # pragma: no cover


def _choose_projects(wizard: Wizard, client, jira: dict) -> list[str]:
    projects = [p for p in (client.projects() or []) if isinstance(p, Mapping) and p.get("key")]
    if projects:
        wizard.out("  볼 수 있는 프로젝트:")
        width = len(str(len(projects)))
        for i, project in enumerate(projects, 1):
            wizard.out(f"    {i:>{width}}. {project.get('key')}  {project.get('name') or ''}".rstrip())
    else:
        wizard.out("  볼 수 있는 프로젝트가 없습니다. 프로젝트 키를 직접 입력하세요")
    existing = _existing_keys(jira)
    default = ", ".join(existing) if existing else None
    return wizard.choose(
        "jira.projects",
        "동기화할 프로젝트 (번호 또는 키, 쉼표로 구분)",
        default,
        lambda raw: parse_project_choice(raw, projects),
    )


def _detect_sprint(wizard: Wizard, client, jira: dict) -> None:
    try:
        field_id = client.sprint_field_id()
    except core.KbError as exc:
        wizard.out(f"  sprint 필드: 확인하지 못했습니다 ({_error_text(exc)}). 기존 값을 둡니다")
        return
    if field_id:
        jira["sprint_field"] = field_id
        wizard.out(f"  sprint 필드: {field_id}")
    else:
        jira.pop("sprint_field", None)
        wizard.out("  sprint 필드: 없음 (sprint 값은 비워 둡니다)")


def _validate_jql(wizard: Wizard, client) -> None:
    """jql_mine과 뷰 JQL을 확인한다(D49). 결과는 알리기만 하고 저장을 막지 않는다."""
    if wizard.cfg is None:
        return
    items: list[tuple[str, Any]] = [("jql_mine", wizard.cfg.get("jira.jql_mine"))]
    views = wizard.cfg.get("jira.views")
    if isinstance(views, Mapping):
        items.extend((f"뷰 {name}", jql) for name, jql in views.items())
    bad = 0
    for label, jql in items:
        if not isinstance(jql, str) or not jql.strip():
            wizard.out(f"  ✗ JQL {label}: 비어 있습니다")
            bad += 1
            continue
        try:
            error = client.validate_jql(jql)
        except core.KbError as exc:
            wizard.out(f"  - JQL {label}: 확인하지 못했습니다 ({_error_text(exc)})")
            continue
        if error:
            wizard.out(f"  ✗ JQL {label}: {error}")
            bad += 1
        else:
            wizard.out(f"  ✓ JQL {label}")
    if bad:
        wizard.out("  JQL 오류는 config.local.yaml의 jira.jql_mine·jira.views에서 고치세요 (설정은 그대로 저장합니다)")


def step_jira(wizard: Wizard, data: dict) -> None:
    wizard.out("\n[2] Jira")
    jira = _section(data, "jira", "config.local.yaml")
    client, me = _connect(wizard, jira)
    with client:
        who = me.get("displayName") or me.get("name") or "?"
        name = me.get("name")
        wizard.out(f"  연결했습니다: {who}" + (f" ({name})" if name and name != who else ""))
        keys = _choose_projects(wizard, client, jira)
        jira["project_keys"] = keys
        wizard.out(f"  project_keys: {', '.join(keys)}")
        _detect_sprint(wizard, client, jira)
        _validate_jql(wizard, client)


# ---------------------------------------------------------------- 단계: 이후 Phase


def step_later(wizard: Wizard, data: dict) -> None:
    """Confluence·LLM 단계 자리. Phase 2에는 안내만 한다."""
    wizard.out("\n[3] Confluence")
    wizard.out("  " + SECTION_PHASE_MESSAGES["confluence"])
    if "confluence" not in data:
        data["confluence"] = {"enabled": False}
        wizard.out("  지금은 비활성(enabled: false)으로 둡니다")
    else:
        wizard.out("  기존 Confluence 설정을 그대로 둡니다")
    wizard.out("\n[4] LLM")
    wizard.out("  " + SECTION_PHASE_MESSAGES["llm"])
    if "llm" in data:
        wizard.out("  기존 LLM 설정을 그대로 둡니다")
    else:
        wizard.out("  지금은 설정하지 않습니다 (LLM 비활성)")


FULL_STEPS = [step_environment, step_jira, step_later]
SECTION_STEPS = {"jira": [step_jira]}


def read_local(config_dir: Path) -> dict:
    """config.local.yaml 원본(병합 전). 없으면 빈 묶음."""
    import yaml

    from lib import config

    path = Path(config_dir) / config.LOCAL_NAME
    if not path.is_file():
        return {}
    try:
        text = path.read_bytes().decode("utf-8-sig")
        data = yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise core.ConfigError(f"{path}: 읽지 못했습니다 ({exc})") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise core.ConfigError(f"{path}: 맨 위는 '키: 값' 묶음이어야 합니다")
    return data


def run_wizard(
    root: Path | str | None,
    *,
    section: str | None = None,
    values: Mapping[str, str] | None = None,
    ask: Ask | None = None,
    ask_secret: AskSecret | None = None,
    out: Callable[[str], None] | None = None,
    run_doctor: bool = True,
) -> int:
    """마법사 전체 또는 한 구역을 돌리고 config.local.yaml을 저장한다. doctor를 돌리면 그 종료 코드."""
    from lib import config

    cfg = config.load(root)
    config_dir = Path(cfg.config_dir)
    local_path = config_dir / config.LOCAL_NAME
    existing = read_local(config_dir)
    if section is not None and not cfg.local_exists:
        raise core.ConfigError(f"{local_path}이(가) 없습니다. 먼저 `python scripts/kb.py init`으로 전체 설정을 하세요")
    steps = SECTION_STEPS[section] if section is not None else FULL_STEPS
    say = out or print
    wizard = Wizard(config_dir, ask=ask, ask_secret=ask_secret, out=say, steps=steps, values=values, cfg=cfg)
    if cfg.local_exists:
        say(f"기존 설정을 기본값으로 씁니다: {local_path}")
    data = wizard.run(existing)  # 구역만 다시 할 때 environment 등 다른 값은 그대로 남는다
    config.save_local(config_dir, data)
    say(f"\n설정을 저장했습니다: {local_path}" + (" (덮어씀)" if cfg.local_exists else ""))
    if not run_doctor:
        return 0
    say("점검을 시작합니다.\n")
    import doctor

    return doctor.main(["--root", str(config_dir)])


# ---------------------------------------------------------------- --dev


def load_example_block(name: str = "home", path: Path | None = None) -> dict:
    """config.local.example.yaml에서 맨 위 키 하나(home/company)의 내용을 읽는다."""
    import yaml

    path = path or CODE_ROOT / EXAMPLE_NAME
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise core.ConfigError(f"{path}: 읽지 못했습니다 ({exc.strerror or exc})") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise core.ConfigError(f"{path}: YAML 문법 오류: {exc}") from None
    block = data.get(name) if isinstance(data, Mapping) else None
    if not isinstance(block, Mapping):
        raise core.ConfigError(f"{path}: '{name}:' 블록이 없습니다")
    return copy.deepcopy(dict(block))


def build_dev_config(environ: Mapping[str, str]) -> tuple[dict, dict[str, str]]:
    """home 블록에 KB_INIT_* 덮어쓰기를 적용한다. (설정, {저장소 이름: 값})을 돌려준다."""
    data = load_example_block("home")
    data["environment"] = "home"
    to_store: dict[str, str] = {}

    jira = _section(data, "jira")
    confluence = _section(data, "confluence")
    llm = _section(data, "llm")
    profiles = _section(llm, "profiles")

    if environ.get("KB_INIT_JIRA_URL", "").strip():
        jira["base_url"] = environ["KB_INIT_JIRA_URL"].strip()
    if environ.get("KB_INIT_CONFLUENCE_URL", "").strip():
        confluence["base_url"] = environ["KB_INIT_CONFLUENCE_URL"].strip()
    if environ.get("KB_INIT_LLM_URL", "").strip():
        mock = _section(profiles, "mock")
        mock.setdefault("model", "mock")
        mock["base_url"] = environ["KB_INIT_LLM_URL"].strip()

    jira["pat_ref"] = _keyring_ref("jira")
    confluence["pat_ref"] = _keyring_ref("confluence")
    to_store["jira"] = DEV_TOKEN
    to_store["confluence"] = DEV_TOKEN

    llm_key = environ.get("KB_INIT_LLM_KEY", "").strip()
    if llm_key:
        core.register_secret(llm_key)
        to_store["llm"] = llm_key
        main = _section(profiles, "main")
        old_ref = main.get("api_key_ref")
        # main과 같은 참조를 쓰던 프로필(같은 서버의 fallback)도 함께 옮긴다
        for profile in profiles.values():
            if isinstance(profile, dict) and (profile is main or (old_ref and profile.get("api_key_ref") == old_ref)):
                profile["api_key_ref"] = _keyring_ref("llm")
    return data, to_store


def run_dev(root: str | Path | None, environ: Mapping[str, str] | None = None) -> int:
    from lib import config
    from lib import secrets as kb_secrets

    env = os.environ if environ is None else environ
    cfg = config.load(root, environ=env)
    local_path = Path(cfg.config_dir) / config.LOCAL_NAME
    if cfg.local_exists and cfg.environment == "company":
        raise core.PolicyRefused(
            f"{local_path}의 environment가 company입니다(값이 없거나 잘못되어도 company로 봅니다). "
            "--dev는 집 개발용이라 회사 설정을 덮지 않습니다. 집 PC라면 environment: home으로 고친 뒤 다시 실행하세요"
        )

    data, to_store = build_dev_config(env)
    for name, value in to_store.items():
        kb_secrets.store(SERVICE, name, value)
    existed = cfg.local_exists
    config.save_local(cfg.config_dir, data)

    print("집 개발 환경(--dev) 설정을 썼습니다.")
    print(f"  설정 파일: {local_path}" + (" (덮어씀)" if existed else ""))
    stored = ", ".join(f"{SERVICE}/{name}" for name in to_store)
    print(f"  자격 증명 저장소: {stored} (jira·confluence는 모의 토큰)")
    print("점검을 시작합니다.\n", flush=True)

    import doctor

    return doctor.main(["--root", str(cfg.config_dir)])


# ---------------------------------------------------------------- 진입점


def _flush_print(line: str) -> None:
    print(line, flush=True)


def main(argv: list[str]) -> int:
    core.refuse_if_agent("init")  # kb.py도 막지만 직접 실행도 막는다
    parser = core.make_parser("kb.py init", "연결 설정 마법사. 재실행하면 기존 값이 기본값입니다")
    parser.add_argument("--dev", action="store_true", help="집 개발용: 모의 Jira·Confluence와 예시 LLM 설정을 씀")
    parser.add_argument("--yes", action="store_true", help="확인 질문 없이 진행")
    parser.add_argument("--section", choices=("jira", "confluence", "llm"), help="한 구역만 다시 설정")
    parser.add_argument("--non-interactive", action="store_true", help="묻지 않고 진행 (--from-env와 함께)")
    parser.add_argument("--from-env", action="store_true", help="KB_INIT_* 환경변수에서 값을 읽음 (--non-interactive와 함께)")
    parser.add_argument("--redetect", action="store_true", help="LLM 탐지만 다시 (Phase 4)")
    args = parser.parse_args(argv)

    if args.redetect:
        print(REDETECT_PHASE_MESSAGE)
        return 4
    if args.dev:
        if args.section or args.from_env or args.non_interactive:
            raise core.ConfigError("--dev는 --section·--from-env·--non-interactive와 함께 쓸 수 없습니다")
        return run_dev(args.root)
    if args.section in SECTION_PHASE_MESSAGES:
        print(SECTION_PHASE_MESSAGES[args.section])
        return 4
    if args.non_interactive != args.from_env:
        raise core.ConfigError("--non-interactive와 --from-env는 함께 씁니다 (KB_INIT_* 환경변수에서 값을 읽음)")
    values = {name: os.environ.get(name, "") for name in ENV_FIELDS.values()} if args.from_env else None
    return run_wizard(args.root, section=args.section, values=values, out=_flush_print)


if __name__ == "__main__":
    core.run_main(main)
