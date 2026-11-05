"""작업 스케줄러 등록 준비 (`kb.py schedule`, Phase 6, D58·D59).

작업 스케줄러 XML(UTF-16)을 만들고, 사용자가 직접 실행할 `schtasks` 명령을 출력한다.
**이 모듈은 schtasks를 실행하지 않는다.** 하위 프로세스를 쓰지 않는다(테스트가 확인한다).

작업(평면 이름, D58 검토)
- work-kb-sync     `kb.py sync`     15분마다
- work-kb-prepare  `kb.py today`    매일 08:30
- work-kb-link     `kb.py link`     매일 12:30·18:30
- work-kb-ingest   `kb.py ingest`   매일 12:31·18:31   (회사: 꺼진 상태로 만듦, D59)
- work-kb-compile  `kb.py compile`  매일 12:32·18:32   (회사: 꺼진 상태로 만듦, D59)
- work-kb-lint     `kb.py lint`     금요일 17:00

XML 공통(D58)
- LogonType InteractiveToken(로그온한 사용자 권한, 비밀번호 저장 없음 → keyring을 그대로 씀). `/NP`·SYSTEM 금지.
- WorkingDirectory = 저장소 폴더, StartWhenAvailable, MultipleInstancesPolicy IgnoreNew,
  배터리에서도 실행, 작업별 실행 시간 제한.
- 명령: `<저장소>\\.venv\\Scripts\\pythonw.exe -X utf8 scripts\\kb.py <명령>` (콘솔 창 없음, `-X utf8`이라
  PYTHONUTF8 환경변수가 필요 없다). 볼트 루트가 저장소가 아니면 `--root "<볼트>"`를 붙인다
  (예약 작업에는 KB_ROOT가 없다).
- 로그는 각 스크립트가 `.state/logs/`에 쓴다. 종료 코드는 schtasks /Query의 'Last Result'에 남는다.

옵션
- (없음): `<볼트>\\.state\\schedule\\<이름>.xml`에 쓰고 명령을 출력.
- `--xml-dir DIR`: XML을 DIR에 쓴다.
- `--print`: 파일을 쓰지 않고 XML 내용과 명령만 출력.
- `--company`: 설정과 상관없이 회사용(ingest·compile 꺼짐). 없으면 config.local.yaml의 environment
  (없거나 잘못되면 company, D44).
"""
from __future__ import annotations

import datetime as dt
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from lib import core

CODE_ROOT = Path(__file__).resolve().parents[1]
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
PYTHONW = Path(".venv") / "Scripts" / "pythonw.exe"
XML_SUBDIR = "schedule"  # .state/ 아래


@dataclass(frozen=True)
class Trigger:
    """매일(days 없음) 또는 매주(days) 시각. every_minutes가 있으면 그 시각부터 하루 동안 반복."""

    time: str  # "HH:MM"
    days: tuple[str, ...] = ()  # 매주: ("Friday",)
    every_minutes: int | None = None


@dataclass(frozen=True)
class Task:
    name: str
    command: tuple[str, ...]  # kb.py 뒤의 인수
    triggers: tuple[Trigger, ...]
    what: str  # 한국어 설명
    when: str  # 사람이 읽는 일정
    time_limit: str  # ISO 8601 기간
    llm: bool = False  # True면 회사에서 꺼진 상태로 만든다(D59)


TASKS: tuple[Task, ...] = (
    Task("work-kb-sync", ("sync",), (Trigger("00:00", every_minutes=15),), "Jira 일감 캐시 동기화", "15분마다", "PT30M"),
    Task("work-kb-prepare", ("today",), (Trigger("08:30"),), "오늘 daily 준비(오늘·이월 구역)", "매일 08:30", "PT30M"),
    Task("work-kb-link", ("link",), (Trigger("12:30"), Trigger("18:30")), "A단계 연결(LLM 없음)", "매일 12:30, 18:30", "PT30M"),
    Task(
        "work-kb-ingest", ("ingest",), (Trigger("12:31"), Trigger("18:31")), "이미지 글 추출(비전 LLM)", "매일 12:31, 18:31", "PT2H", llm=True
    ),
    Task(
        "work-kb-compile", ("compile",), (Trigger("12:32"), Trigger("18:32")), "B단계 편찬(LLM)", "매일 12:32, 18:32", "PT2H", llm=True
    ),
    Task("work-kb-lint", ("lint",), (Trigger("17:00", days=("Friday",)),), "C단계 점검 보고(LLM 없음)", "금요일 17:00", "PT1H"),
)

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class Planned:
    task: Task
    path: Path  # XML 파일 자리
    xml: str
    enabled: bool
    arguments: str


# ---------------------------------------------------------------- 명령줄


def quote_arg(arg: str) -> str:
    """Windows 명령줄 인수 하나(CommandLineToArgvW 규칙). 공백·따옴표가 없으면 그대로."""
    if arg and not any(ch in arg for ch in ' \t"'):
        return arg
    out = ['"']
    backslashes = 0
    for ch in arg:
        if ch == "\\":
            backslashes += 1
            continue
        if ch == '"':
            out.append("\\" * (2 * backslashes + 1) + '"')
        else:
            out.append("\\" * backslashes + ch)
        backslashes = 0
    out.append("\\" * (2 * backslashes) + '"')
    return "".join(out)


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def task_arguments(task: Task, repo: Path, root: Path) -> str:
    """pythonw.exe 뒤의 인수. 스크립트는 저장소 기준 상대 경로(WorkingDirectory = 저장소)."""
    parts = ["-X", "utf8", str(Path("scripts") / "kb.py"), *task.command]
    if not _same_path(repo, root):
        parts += ["--root", str(root)]
    return " ".join(quote_arg(p) for p in parts)


# ---------------------------------------------------------------- XML


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = text
    return element


def _trigger(parent: ET.Element, trigger: Trigger, start: dt.date) -> None:
    hh, mm = (int(x) for x in trigger.time.split(":"))
    node = _sub(parent, "CalendarTrigger")
    if trigger.every_minutes:
        rep = _sub(node, "Repetition")
        _sub(rep, "Interval", f"PT{trigger.every_minutes}M")
        _sub(rep, "Duration", "P1D")
        _sub(rep, "StopAtDurationEnd", "false")
    _sub(node, "StartBoundary", dt.datetime.combine(start, dt.time(hh, mm)).strftime("%Y-%m-%dT%H:%M:%S"))
    _sub(node, "Enabled", "true")
    if trigger.days:
        week = _sub(node, "ScheduleByWeek")
        days = _sub(week, "DaysOfWeek")
        for day in trigger.days:
            if day not in _WEEKDAYS:
                raise ValueError(day)
            _sub(days, day)
        _sub(week, "WeeksInterval", "1")
    else:
        by_day = _sub(node, "ScheduleByDay")
        _sub(by_day, "DaysInterval", "1")


def task_xml(task: Task, *, repo: Path, root: Path, enabled: bool, start: dt.date, vault: Path | None = None) -> str:
    """작업 하나의 XML 문자열(선언은 UTF-16). 파일로 쓸 때는 UTF-16으로 인코딩한다.

    root는 `--root`로 넘길 설정 폴더, vault는 `.state/`가 있는 볼트 루트(설정의 kb_root, 보통 같다).
    """
    top = ET.Element("Task", {"version": "1.2", "xmlns": TASK_NS})
    info = _sub(top, "RegistrationInfo")
    log_dir = core.state_dir(vault or root) / "logs"
    _sub(info, "Description", f"work-kb: {task.what} (kb.py {' '.join(task.command)}). 로그: {log_dir}")
    _sub(info, "URI", "\\" + task.name)

    triggers = _sub(top, "Triggers")
    for trigger in task.triggers:
        _trigger(triggers, trigger, start)

    principal = _sub(_sub(top, "Principals"), "Principal", id="Author")
    _sub(principal, "LogonType", "InteractiveToken")  # D58: 로그온한 사용자로, 비밀번호 저장 없이(keyring 접근)
    _sub(principal, "RunLevel", "LeastPrivilege")

    settings = _sub(top, "Settings")
    _sub(settings, "MultipleInstancesPolicy", "IgnoreNew")
    _sub(settings, "DisallowStartIfOnBatteries", "false")
    _sub(settings, "StopIfGoingOnBatteries", "false")
    _sub(settings, "AllowHardTerminate", "true")
    _sub(settings, "StartWhenAvailable", "true")
    _sub(settings, "RunOnlyIfNetworkAvailable", "false")
    _sub(settings, "AllowStartOnDemand", "true")
    _sub(settings, "Enabled", "true" if enabled else "false")
    _sub(settings, "Hidden", "false")
    _sub(settings, "RunOnlyIfIdle", "false")
    _sub(settings, "WakeToRun", "false")
    _sub(settings, "ExecutionTimeLimit", task.time_limit)
    _sub(settings, "Priority", "7")

    exec_node = _sub(_sub(top, "Actions", Context="Author"), "Exec")
    _sub(exec_node, "Command", '"' + str(repo / PYTHONW) + '"')
    _sub(exec_node, "Arguments", task_arguments(task, repo, root))
    _sub(exec_node, "WorkingDirectory", str(repo))

    ET.indent(top, space="  ")
    body = ET.tostring(top, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-16"?>\n' + body + "\n").replace("\n", "\r\n")


def encode_xml(text: str) -> bytes:
    """schtasks /XML이 기대하는 UTF-16(BOM 포함, 리틀 엔디언)."""
    return b"\xff\xfe" + text.encode("utf-16-le")


# ---------------------------------------------------------------- 계획·출력


def plan(
    *,
    repo: Path,
    root: Path,
    xml_dir: Path,
    company: bool,
    start: dt.date,
    vault: Path | None = None,
    tasks: Iterable[Task] = TASKS,
) -> list[Planned]:
    result: list[Planned] = []
    for task in tasks:
        enabled = not (company and task.llm)
        result.append(
            Planned(
                task=task,
                path=xml_dir / f"{task.name}.xml",
                xml=task_xml(task, repo=repo, root=root, enabled=enabled, start=start, vault=vault),
                enabled=enabled,
                arguments=task_arguments(task, repo, root),
            )
        )
    return result


def write_all(planned: Iterable[Planned]) -> None:
    for item in planned:
        core.atomic_write(item.path, encode_xml(item.xml))


def create_command(item: Planned) -> str:
    """등록 명령. /XML 경로는 늘 큰따옴표로 감싼다.

    PowerShell은 따옴표 없는 인수의 `(`·`)`·`;`·`&` 등을 문법으로 읽으므로 공백이 없어도 깨진다.
    Windows 경로에는 `"`가 들어갈 수 없어 그대로 감싸면 된다. 작업 이름은 `[a-z0-9-]+` 평면 이름이라 감싸지 않는다(D58).
    """
    return f'schtasks /Create /TN {item.task.name} /XML "{item.path}" /F'


def command_lines(planned: list[Planned]) -> list[str]:
    """사용자가 직접 실행할 명령(설명 줄 포함). 실행하지 않는다."""
    lines = ["등록 (PowerShell 또는 cmd에서 직접 실행. 관리자 권한 필요 없음, /NP·/RU SYSTEM 쓰지 않음):"]
    lines += [f"  {create_command(item)}" for item in planned]
    lines.append("확인 (Logon Mode가 'Interactive only'인지, Last Result가 0인지):")
    lines.append("  schtasks /Query /TN work-kb-sync /V /FO LIST")
    lines.append("지금 한 번 실행:")
    lines.append("  schtasks /Run /TN work-kb-sync")
    disabled = [item for item in planned if not item.enabled]
    if disabled:
        lines.append("켜기 (회사에서 꺼진 상태로 만든 작업. LLM 설정·doctor 확인 뒤):")
        lines += [f"  schtasks /Change /TN {item.task.name} /ENABLE" for item in disabled]
    lines.append("끄기 (예):")
    lines.append("  schtasks /Change /TN work-kb-sync /DISABLE")
    lines.append("지우기:")
    lines += [f"  schtasks /Delete /TN {item.task.name} /F" for item in planned]
    return lines


def summary_lines(planned: list[Planned], *, repo: Path, root: Path, company: bool, vault: Path | None = None) -> list[str]:
    python = repo / PYTHONW
    lines = [
        f"환경: {'company (ingest·compile은 꺼진 상태로 만듭니다, D59)' if company else 'home (모든 작업 켜짐)'}",
        f"저장소(WorkingDirectory): {repo}",
        f"Python: {python}" + ("" if python.is_file() else "  (주의: 아직 없습니다. 등록 전에 .venv를 만드세요)"),
    ]
    if not _same_path(repo, root):
        lines.append(f"볼트(--root): {root}")
    lines += [f"로그: {core.state_dir(vault or root) / 'logs'}", "", "작업:"]
    width = max(len(item.task.name) for item in planned)
    for item in planned:
        state = "" if item.enabled else "  [꺼짐]"
        lines.append(f"  {item.task.name:<{width}}  {item.task.when:<18}  kb.py {' '.join(item.task.command)}{state}")
    return lines


def local_today() -> dt.date:
    tz = dt.datetime.now().astimezone().tzinfo or dt.timezone.utc
    return core.now(tz).date()


def generate(
    *,
    root: Path,
    company: bool,
    vault: Path | None = None,
    xml_dir: Path | None = None,
    write: bool = True,
    repo: Path = CODE_ROOT,
    out: Callable[[str], None] = print,
    show_xml: bool = False,
) -> list[Planned]:
    """XML을 만들고(write면 파일로) 요약·명령을 out으로 출력한다. init의 스케줄 단계도 이것을 쓴다.

    root: 설정 폴더(저장소와 다르면 `--root`로 넘김). vault: `.state/`가 있는 볼트 루트(기본 root).
    """
    repo = Path(repo).resolve()
    root = Path(root).resolve()
    vault = Path(vault).resolve() if vault is not None else root
    target = Path(xml_dir).resolve() if xml_dir is not None else core.state_dir(vault) / XML_SUBDIR
    planned = plan(repo=repo, root=root, xml_dir=target, company=company, start=local_today(), vault=vault)
    if write:
        write_all(planned)
        out(f"작업 스케줄러 XML을 만들었습니다 (UTF-16, 등록은 하지 않았습니다): {target}")
    else:
        out("XML 파일은 쓰지 않았습니다 (--print). 등록하려면 --print 없이 다시 실행하세요")
    for line in summary_lines(planned, repo=repo, root=root, company=company, vault=vault):
        out(line)
    if show_xml:
        for item in planned:
            out("")
            out(f"----- {item.path.name} -----")
            for line in item.xml.splitlines():
                out(line)
    out("")
    for line in command_lines(planned):
        out(line)
    return planned


# ---------------------------------------------------------------- 진입점


def main(argv: list[str]) -> int:
    core.refuse_if_agent("schedule")
    parser = core.make_parser(
        "kb.py schedule", "작업 스케줄러 XML과 schtasks 명령을 만든다. 등록(schtasks 실행)은 하지 않는다"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--print", dest="print_only", action="store_true", help="파일을 쓰지 않고 XML과 명령만 출력")
    group.add_argument("--xml-dir", default=None, metavar="DIR", help="XML을 쓸 폴더 (기본: <볼트>\\.state\\schedule)")
    parser.add_argument("--company", action="store_true", help="회사용: ingest·compile 작업을 꺼진 상태로 만듦 (기본: 설정의 environment)")
    args = parser.parse_args(argv)

    from lib import config

    cfg = config.load(args.root)
    company = bool(args.company) or cfg.environment == "company"
    generate(
        root=Path(cfg.config_dir or cfg.root),
        vault=Path(cfg.root),
        company=company,
        xml_dir=Path(args.xml_dir) if args.xml_dir else None,
        write=not args.print_only,
        show_xml=args.print_only,
    )
    return 0


if __name__ == "__main__":
    core.run_main(main)
