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


def _preflight() -> None:
    """표준 라이브러리만으로 Python 버전과 의존성을 확인한다(D02). 실패하면 종료 4."""
    if sys.version_info < (3, 13):
        sys.stderr.write(f"오류: Python 3.13 이상이 필요합니다 (현재 {sys.version.split()[0]}).\n")
        sys.exit(4)
    missing = [pkg for mod, pkg in _REQUIRED if importlib.util.find_spec(mod) is None]
    if missing:
        lines = [f"오류: 패키지가 없습니다: {', '.join(missing)}"]
        lines.append("  설치: python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt")
        lines.append("  오프라인: .venv\\Scripts\\pip install --no-index --find-links wheels -r requirements.txt")
        if sys.version_info >= (3, 15) and "pyyaml" in missing:
            lines.append("  Python 3.15에는 PyYAML wheel이 아직 없습니다. Python 3.14(또는 3.13)로 venv를 만드세요.")
        sys.stderr.write("\n".join(lines) + "\n")
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
    "act": Command("jira_act", agent_ok=False, agent_ok_sub=("list",), help="Jira 쓰기(큐): comment·transition·assign·worklog·create·promote·flush·list"),
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
    if not command.agent_ok and argv[1:2] != [sub for sub in argv[1:2] if sub in command.agent_ok_sub]:
        core.refuse_if_agent(name)
    module = importlib.import_module(command.module)
    return int(getattr(module, command.func)(argv[1:]) or 0)


if __name__ == "__main__":
    _preflight()
    from lib import core

    core.run_main(main)
