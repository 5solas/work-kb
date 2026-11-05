"""work-kb 단일 진입점.

사용법: python scripts/kb.py <명령> [옵션]
        python scripts/kb.py --argv-stdin   (stdin에서 UTF-8 JSON 배열로 인수를 받음, Emacs 연동)

명령 모듈은 필요할 때만 가져온다. 명령 표의 agent_ok가 거짓이면 KB_AGENT=1 세션에서 거부한다.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

_REQUIRED = (("yaml", "pyyaml"), ("requests", "requests"), ("keyring", "keyring"), ("markdown", "markdown"), ("PIL", "pillow"))


def _err(text: str) -> None:
    """stderr가 없으면(pythonw.exe) 저장소의 .state/logs/kb.log에 적는다. 표준 라이브러리만 쓴다."""
    stream = getattr(sys, "stderr", None)
    if stream is not None:
        stream.write(text)
        return
    root = HERE.parent
    args = sys.argv[1:]
    for i, arg in enumerate(args):  # 스케줄 작업은 볼트를 --root로 넘기므로 그 볼트의 로그에 적는다
        if arg == "--root" and i + 1 < len(args) and Path(args[i + 1]).is_dir():
            root = Path(args[i + 1])
        elif arg.startswith("--root=") and Path(arg[7:]).is_dir():
            root = Path(arg[7:])
    try:
        log_dir = root / ".state" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "kb.log", "a", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def _preflight() -> None:
    """표준 라이브러리만으로 Python 버전과 의존성을 확인한다(D02). 실패하면 종료 4."""
    if sys.version_info < (3, 13):
        _err(f"오류: Python 3.13 이상이 필요합니다 (현재 {sys.version.split()[0]}).\n")
        sys.exit(4)
    missing = [pkg for mod, pkg in _REQUIRED if importlib.util.find_spec(mod) is None]
    if missing:
        lines = [f"오류: 패키지가 없습니다: {', '.join(missing)}"]
        lines.append("  설치: python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt")
        lines.append("  오프라인: .venv\\Scripts\\pip install --no-index --find-links wheels -r requirements.txt")
        if sys.version_info >= (3, 15) and "pyyaml" in missing:
            lines.append("  Python 3.15에는 PyYAML wheel이 아직 없습니다. Python 3.14(또는 3.13)로 venv를 만드세요.")
        _err("\n".join(lines) + "\n")
        sys.exit(4)


@dataclass(frozen=True)
class Command:
    """부록 A §2 계약: Command(module, func="main", agent_ok=True). help는 키워드 전용."""

    module: str
    func: str = "main"
    agent_ok: bool = True
    help: str = field(default="", kw_only=True)
    agent_ok_sub: tuple[str, ...] = field(default=(), kw_only=True)  # agent_ok=False여도 허용되는 하위 명령


# Phase가 진행되며 늘어난다. 이름 → Command. 모듈은 scripts/ 바로 아래.
COMMANDS: dict[str, Command] = {
    "init": Command("init", agent_ok=False, help="연결 설정 마법사 (--dev: 집 모의 환경)"),
    "doctor": Command("doctor", help="연결·환경 점검"),
    # Phase 1
    "today": Command("daily_prepare", "today_main", help="오늘 daily 준비(오늘·이월 구역) 후 경로 출력"),
    "link": Command("link", help="A단계: Jira 키·@이름을 엔티티 페이지에 연결 (LLM 없음)"),
    "status": Command("status", help="상태 요약"),
    "search": Command("search", help='검색: kb.py search "질의"'),
    # Phase 2
    "sync": Command("jira_sync", help="Jira 일감 캐시 동기화 (--full, --key, --jql)"),
    # Phase 3
    "act": Command("jira_act", agent_ok=False, agent_ok_sub=("list", "transitions"), help="Jira 쓰기(큐): comment·transition·assign·worklog·create·promote·flush·list"),
    # Phase 4
    "compile": Command("compile", help="B단계: 태그·결정·할 일·엔티티 요약·주제 제안 (LLM, 없으면 조용히 건너뜀)"),
    "ingest": Command("ingest_images", help="B단계: 이미지에서 글 추출 → 사이드카 (비전 LLM)"),
    "proposals": Command("compile", "proposals_main", agent_ok=False, agent_ok_sub=("list",), help="주제 제안: list | accept <n|all>"),
    # Phase 5
    "lint": Command("lint", help="C단계: 점검 보고 wiki/_lint.md (--llm: 모순 후보 T5)"),
    "push": Command("confluence_push", agent_ok=False, help="Confluence 개인 스페이스로 wiki 페이지 게시 (--dry-run, --only)"),
    "demo": Command("demo", agent_ok=False, help="모의 서버 3개로 전체 흐름 시연 (임시 볼트)"),
    # Phase 6
    "schedule": Command("schedule", agent_ok=False, help="작업 스케줄러 XML·schtasks 명령 생성 (등록은 하지 않음)"),
    "wheels": Command("wheels", agent_ok=False, help="오프라인 설치용 wheels/ 내려받기"),
}

ALIASES: dict[str, str] = {"jira_sync": "sync"}


def usage() -> str:
    width = max(len(name) for name in COMMANDS)
    rows = [f"  {name:<{width}}  {cmd.help}" for name, cmd in COMMANDS.items()]
    return "사용법: python scripts/kb.py <명령> [옵션]\n\n명령:\n" + "\n".join(rows) + "\n\n각 명령의 옵션: python scripts/kb.py <명령> --help\n"


def main(argv: list[str]) -> int:
    from lib import core

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage(), end="")
        return 0 if argv else 4
    name = ALIASES.get(argv[0], argv[0])
    command = COMMANDS.get(name)
    if command is None:
        raise core.ConfigError(f"알 수 없는 명령: {argv[0]}\n{usage()}")
    sub_allowed = len(argv) > 1 and argv[1] in command.agent_ok_sub
    if not command.agent_ok and not sub_allowed:
        core.refuse_if_agent(name)
    module = importlib.import_module(command.module)
    return int(getattr(module, command.func)(argv[1:]) or 0)


if __name__ == "__main__":
    _preflight()
    from lib import core

    core.run_main(main)
