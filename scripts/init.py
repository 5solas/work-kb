"""연결 설정 (`kb.py init`).

Phase 0에는 `--dev`(집 모의 환경)만 있다. 대화형 마법사는 Phase 2부터 단계(`Wizard.steps`)를 더한다.

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
from pathlib import Path
from typing import Any, Callable, Mapping

from lib import core

CODE_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_NAME = "config.local.example.yaml"
SERVICE = "work-kb"
DEV_TOKEN = "mock-token"  # 모의 서버용 가짜 PAT. 비밀값이 아니다
WIZARD_PHASE_MESSAGE = "대화형 마법사는 Phase 2에서 추가됩니다. 지금은 --dev만 쓸 수 있습니다."
REDETECT_PHASE_MESSAGE = "LLM 탐지 다시 하기(--redetect)는 Phase 4에서 추가됩니다."


# ---------------------------------------------------------------- 마법사 뼈대

Ask = Callable[[str, "str | None"], str]
AskSecret = Callable[[str], str]


def default_ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ")
    except EOFError:
        raise core.KbError("입력이 끝나 중단했습니다") from None
    return answer.strip() or (default or "")


def default_ask_secret(prompt: str) -> str:
    try:
        value = getpass.getpass(f"{prompt} (입력이 보이지 않습니다): ")
    except EOFError:
        raise core.KbError("입력이 끝나 중단했습니다") from None
    value = value.strip()
    core.register_secret(value)
    return value


class Wizard:
    """대화형 설정의 뼈대. 입력 함수를 주입할 수 있다(테스트·--from-env용).

    `steps`의 각 함수는 (wizard, data)를 받아 data(config.local.yaml 내용)를 고친다.
    Phase 0에서는 비어 있고 `run_dev`만 쓴다. Phase 2부터 environment·jira 단계를 더한다.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        ask: Ask | None = None,
        ask_secret: AskSecret | None = None,
        out: Callable[[str], None] | None = None,
        steps: list[Callable[["Wizard", dict], None]] | None = None,
    ) -> None:
        self.root = Path(root)
        self.ask = ask or default_ask
        self.ask_secret = ask_secret or default_ask_secret
        self.out = out or print
        self.steps: list[Callable[[Wizard, dict], None]] = list(steps or [])

    def run(self, data: Mapping[str, Any] | None = None) -> dict:
        result = copy.deepcopy(dict(data or {}))
        for step in self.steps:
            step(self, result)
        return result


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


def _section(node: dict, key: str) -> dict:
    value = node.get(key)
    if value is None:
        value = node[key] = {}
    if not isinstance(value, dict):
        raise core.ConfigError(f"{EXAMPLE_NAME}: '{key}'는 '키: 값' 묶음이어야 합니다")
    return value


def _keyring_ref(name: str) -> str:
    return f"keyring:{SERVICE}/{name}"


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


def main(argv: list[str]) -> int:
    core.refuse_if_agent("init")  # kb.py도 막지만 직접 실행도 막는다
    parser = core.make_parser("kb.py init", "연결 설정. Phase 0에는 --dev(집 모의 환경)만 있습니다")
    parser.add_argument("--dev", action="store_true", help="집 개발용: 모의 Jira·Confluence와 예시 LLM 설정을 씀")
    parser.add_argument("--yes", action="store_true", help="확인 질문 없이 진행")
    parser.add_argument("--section", choices=("jira", "confluence", "llm"), help="한 구역만 다시 설정 (Phase 2 이후)")
    parser.add_argument("--non-interactive", action="store_true", help="묻지 않고 진행 (--from-env와 함께, Phase 2 이후)")
    parser.add_argument("--from-env", action="store_true", help="KB_INIT_* 환경변수에서 값을 읽음 (Phase 2 이후)")
    parser.add_argument("--redetect", action="store_true", help="LLM 탐지만 다시 (Phase 4)")
    args = parser.parse_args(argv)

    if args.redetect:
        print(REDETECT_PHASE_MESSAGE)
        return 4
    if not args.dev:
        print(WIZARD_PHASE_MESSAGE)
        return 4
    if args.section or args.from_env:
        raise core.ConfigError("--dev는 --section·--from-env와 함께 쓸 수 없습니다")
    return run_dev(args.root)


if __name__ == "__main__":
    core.run_main(main)
