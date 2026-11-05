"""`kb.py schedule`와 init 스케줄 단계 (Phase 6 W6.2, D58·D59).

출력만 파싱한다. 작업을 등록하지 않는다: schtasks를 부르는 코드가 없음을 AST로, 프로세스 안 실행에서는
subprocess·os.system을 막아 확인한다. XML은 xml.etree로 읽고 시각·반복을 표로 비교한다.
Windows에서는 작업 스케줄러 COM의 메모리 안 정의(NewTask + XmlText, 등록 없음)로 스키마도 검사한다.
"""
from __future__ import annotations

import ast
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

import init
import mock_jira as mock_jira_mod
import schedule
from lib import config, core

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
NS = {"t": schedule.TASK_NS}
TOKEN = "jira-pat-sched-0006"  # 가짜 PAT

# 이름 → (kb.py 인수, [(매일|요일, HH:MM, 반복 분)], 회사에서 꺼짐)
EXPECTED = {
    "work-kb-sync": (["sync"], [("daily", "00:00", 15)], False),
    "work-kb-prepare": (["today"], [("daily", "08:30", None)], False),
    "work-kb-link": (["link"], [("daily", "12:30", None), ("daily", "18:30", None)], False),
    "work-kb-ingest": (["ingest"], [("daily", "12:31", None), ("daily", "18:31", None)], True),
    "work-kb-compile": (["compile"], [("daily", "12:32", None), ("daily", "18:32", None)], True),
    "work-kb-lint": (["lint"], [("Friday", "17:00", None)], False),
}


# ---------------------------------------------------------------- 도우미


def split_args(text: str) -> list[str]:
    """CommandLineToArgvW 규칙으로 명령줄을 나눈다(따옴표·역슬래시)."""
    args: list[str] = []
    cur: list[str] = []
    in_quotes = started = False
    backslashes = 0
    for ch in text:
        if ch == "\\":
            backslashes += 1
            started = True
            continue
        if ch == '"':
            cur.append("\\" * (backslashes // 2))
            if backslashes % 2:
                cur.append('"')
            else:
                in_quotes = not in_quotes
            backslashes = 0
            started = True
            continue
        cur.append("\\" * backslashes)
        backslashes = 0
        if ch in " \t" and not in_quotes:
            if started:
                args.append("".join(cur))
                cur, started = [], False
            continue
        cur.append(ch)
        started = True
    cur.append("\\" * backslashes)
    if started:
        args.append("".join(cur))
    return args


def _text(node: ET.Element, path: str) -> str:
    found = node.find(path, NS)
    assert found is not None, path
    return (found.text or "").strip()


def read_task(path: Path) -> ET.Element:
    raw = path.read_bytes()
    assert raw.startswith(b"\xff\xfe"), "UTF-16 LE BOM"
    head = raw.decode("utf-16").splitlines()[0]
    assert head == '<?xml version="1.0" encoding="UTF-16"?>'
    root = ET.parse(path).getroot()  # expat이 BOM으로 UTF-16을 읽는다
    assert root.tag == f"{{{schedule.TASK_NS}}}Task"
    return root


def triggers_of(task: ET.Element) -> list[tuple[str, str, int | None]]:
    """크론 비슷한 표: (daily|요일, HH:MM, 반복 분)."""
    out: list[tuple[str, str, int | None]] = []
    triggers = task.find("t:Triggers", NS)
    assert triggers is not None
    for trig in triggers:
        assert trig.tag == f"{{{schedule.TASK_NS}}}CalendarTrigger"
        assert _text(trig, "t:Enabled") == "true"
        start = dt.datetime.strptime(_text(trig, "t:StartBoundary"), "%Y-%m-%dT%H:%M:%S")
        every = None
        rep = trig.find("t:Repetition", NS)
        if rep is not None:
            m = re.fullmatch(r"PT(\d+)M", _text(rep, "t:Interval"))
            assert m, "Interval"
            every = int(m.group(1))
            assert _text(rep, "t:Duration") == "P1D"  # 하루 내내
            assert _text(rep, "t:StopAtDurationEnd") == "false"
        by_day = trig.find("t:ScheduleByDay", NS)
        by_week = trig.find("t:ScheduleByWeek", NS)
        if by_day is not None:
            assert _text(by_day, "t:DaysInterval") == "1"
            kind = "daily"
        else:
            assert by_week is not None
            assert _text(by_week, "t:WeeksInterval") == "1"
            days = [d.tag.split("}")[1] for d in by_week.find("t:DaysOfWeek", NS)]
            kind = ",".join(days)
        out.append((kind, start.strftime("%H:%M"), every))
    return out


def check_task(path: Path, *, repo: Path, root: Path, enabled: bool) -> list[str]:
    """XML 한 개의 D58 공통 사항을 확인하고 kb.py 뒤 인수(--root 제외)를 돌려준다."""
    name = path.stem
    task = read_task(path)
    assert _text(task, "t:RegistrationInfo/t:URI") == "\\" + name
    principal = task.find("t:Principals/t:Principal", NS)
    assert _text(principal, "t:LogonType") == "InteractiveToken"
    assert _text(principal, "t:RunLevel") == "LeastPrivilege"
    for forbidden in ("t:UserId", "t:Password", "t:GroupId"):
        assert principal.find(forbidden, NS) is None  # SYSTEM·저장 비밀번호 없음
    settings = task.find("t:Settings", NS)
    assert _text(settings, "t:MultipleInstancesPolicy") == "IgnoreNew"
    assert _text(settings, "t:StartWhenAvailable") == "true"
    assert _text(settings, "t:DisallowStartIfOnBatteries") == "false"
    assert _text(settings, "t:Enabled") == ("true" if enabled else "false")
    assert re.fullmatch(r"PT\d+[MH]", _text(settings, "t:ExecutionTimeLimit"))
    execs = task.findall("t:Actions/t:Exec", NS)
    assert len(execs) == 1
    command = split_args(_text(execs[0], "t:Command"))
    assert command == [str(repo / ".venv" / "Scripts" / "pythonw.exe")]
    assert _text(execs[0], "t:WorkingDirectory") == str(repo)
    args = split_args(_text(execs[0], "t:Arguments"))
    assert args[:3] == ["-X", "utf8", "scripts\\kb.py"]
    rest = args[3:]
    if "--root" in rest:
        i = rest.index("--root")
        assert rest[i + 1] == str(root)
        rest = rest[:i] + rest[i + 2 :]
    else:
        assert os.path.normcase(str(repo)) == os.path.normcase(str(root))
    expected_args, expected_triggers, _ = EXPECTED[name]
    assert rest == expected_args
    assert triggers_of(task) == expected_triggers
    return rest


def create_lines(stdout: str) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("schtasks /Create"):
            parts = split_args(line)
            assert parts[:2] == ["schtasks", "/Create"] and parts[-1] == "/F"
            name = parts[parts.index("/TN") + 1]
            found[name] = Path(parts[parts.index("/XML") + 1])
    return found


def schtasks_lines(stdout: str) -> list[str]:
    return [line.strip() for line in stdout.splitlines() if line.strip().startswith("schtasks ")]


@pytest.fixture
def no_subprocess(monkeypatch):
    """프로세스 안에서 하위 프로세스·셸 실행을 막는다(schtasks 등록 금지)."""

    def refuse(*args, **kwargs):
        raise AssertionError(f"하위 프로세스를 실행하면 안 됩니다: {args!r}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    monkeypatch.setattr(os, "system", refuse)
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", refuse)


@pytest.fixture
def mock_jira():
    with mock_jira_mod.start(require_token=TOKEN) as server:
        yield server


def _write_local(vault: Path, environment: str) -> None:
    config.save_local(vault, {"environment": environment})


# ---------------------------------------------------------------- 표·인용


def test_task_table_matches_contract():
    assert [t.name for t in schedule.TASKS] == list(EXPECTED)
    for task in schedule.TASKS:
        args, triggers, llm = EXPECTED[task.name]
        assert list(task.command) == args
        assert task.llm is llm
        assert [((",".join(t.days) or "daily"), t.time, t.every_minutes) for t in task.triggers] == triggers
        assert re.fullmatch(r"[a-z0-9-]+", task.name)  # 평면 이름, 따옴표가 필요 없음(D58)


@pytest.mark.parametrize(
    "arg",
    ["sync", "D:\\work\\work-kb", "C:\\한글 경로 (테스트)\\work-kb", "D:\\", "C:\\a b\\", 'say "hi"', "", "a\\\\b c"],
)
def test_quote_arg_round_trips(arg):
    assert split_args(schedule.quote_arg(arg)) == [arg]


@pytest.mark.parametrize("folder", ["업무(KB)", "a;b", "plain", "공백 있는 폴더"])
def test_create_command_always_quotes_xml_path(tmp_path, folder, no_subprocess):
    # PowerShell은 따옴표 없는 `(`·`;`를 문법으로 읽는다. 공백이 없어도 /XML 경로는 늘 감싼다.
    xml_dir = tmp_path / folder
    planned = schedule.generate(root=ROOT, company=False, xml_dir=xml_dir, out=lambda _: None)
    for item in planned:
        line = schedule.create_command(item)
        assert f'/XML "{item.path}" /F' in line
        assert line.startswith(f"schtasks /Create /TN {item.task.name} /XML ")
        assert create_lines(line) == {item.task.name: item.path}


# ---------------------------------------------------------------- XML


def test_home_xml_parses_and_follows_d58(tmp_path, no_subprocess):
    out: list[str] = []
    xml_dir = tmp_path / "xml"
    planned = schedule.generate(root=ROOT, company=False, xml_dir=xml_dir, out=out.append)
    assert sorted(p.name for p in xml_dir.iterdir()) == sorted(f"{name}.xml" for name in EXPECTED)
    for item in planned:
        assert item.enabled
        check_task(item.path, repo=ROOT, root=ROOT, enabled=True)
    said = "\n".join(out)
    assert "home (모든 작업 켜짐)" in said
    assert "/ENABLE" not in said
    assert "--root" not in said  # 볼트가 저장소면 --root를 붙이지 않는다


def test_start_boundary_uses_local_today(tmp_path, fake_now, no_subprocess):
    fake_now("2026-11-05T12:00:00+09:00")
    planned = schedule.generate(root=ROOT, company=False, xml_dir=tmp_path, out=lambda _: None)
    today = core.now(dt.datetime.now().astimezone().tzinfo).date().isoformat()
    for item in planned:
        for trig in read_task(item.path).find("t:Triggers", NS):
            assert _text(trig, "t:StartBoundary").startswith(today + "T")


def test_company_disables_ingest_and_compile(tmp_path, no_subprocess):
    out: list[str] = []
    planned = schedule.generate(root=ROOT, company=True, xml_dir=tmp_path, out=out.append)
    for item in planned:
        disabled = EXPECTED[item.task.name][2]
        assert item.enabled is not disabled
        check_task(item.path, repo=ROOT, root=ROOT, enabled=not disabled)
    lines = schtasks_lines("\n".join(out))
    assert [l for l in lines if "/ENABLE" in l] == [
        "schtasks /Change /TN work-kb-ingest /ENABLE",
        "schtasks /Change /TN work-kb-compile /ENABLE",
    ]
    assert "company (ingest·compile은 꺼진 상태로 만듭니다, D59)" in "\n".join(out)


def test_separate_vault_gets_root_argument(tmp_path, vault, no_subprocess):
    planned = schedule.generate(root=vault, company=False, xml_dir=tmp_path, out=lambda _: None)
    for item in planned:
        check_task(item.path, repo=ROOT, root=vault, enabled=True)
        args = split_args(item.arguments)
        assert args[-2:] == ["--root", str(vault)]  # 한글·공백·괄호 경로가 한 인수로


# ---------------------------------------------------------------- 명령줄 (kb.py schedule)


def test_cli_writes_utf16_under_state_and_prints_commands(run_kb, vault):
    result = run_kb("schedule")
    assert result.returncode == 0, result.stdout + result.stderr
    target = vault / ".state" / "schedule"
    creates = create_lines(result.stdout)
    assert list(creates) == list(EXPECTED)
    for name, path in creates.items():
        assert path == target / f"{name}.xml" and path.is_file()
        # config.local.yaml이 없으면 company(D44) → ingest·compile 꺼짐
        check_task(path, repo=ROOT, root=vault, enabled=not EXPECTED[name][2])
    lines = schtasks_lines(result.stdout)
    assert "schtasks /Run /TN work-kb-sync" in lines
    assert "schtasks /Query /TN work-kb-sync /V /FO LIST" in lines
    assert [l for l in lines if l.startswith("schtasks /Delete")] == [f"schtasks /Delete /TN {n} /F" for n in EXPECTED]
    for line in lines:
        assert "/NP" not in line.split() and "/RU" not in line.split() and "/RP" not in line.split()
    assert "등록은 하지 않았습니다" in result.stdout
    assert str(vault / ".state" / "logs") in result.stdout


def test_cli_environment_from_config_and_company_flag(run_kb, vault):
    _write_local(vault, "home")
    home = run_kb("schedule", "--print")
    assert home.returncode == 0, home.stderr
    assert "home (모든 작업 켜짐)" in home.stdout and "/ENABLE" not in home.stdout
    forced = run_kb("schedule", "--print", "--company")
    assert forced.returncode == 0, forced.stderr
    assert "schtasks /Change /TN work-kb-compile /ENABLE" in forced.stdout


def test_cli_print_writes_nothing_and_shows_xml(run_kb, vault):
    result = run_kb("schedule", "--print")
    assert result.returncode == 0, result.stderr
    assert not (vault / ".state" / "schedule").exists()
    assert "XML 파일은 쓰지 않았습니다" in result.stdout
    blocks = re.findall(r"----- (work-kb-[a-z]+)\.xml -----\n(.*?</Task>)", result.stdout, re.S)
    assert [name for name, _ in blocks] == list(EXPECTED)
    for name, body in blocks:
        task = ET.fromstring(body.split("\n", 1)[1])  # 선언 줄은 UTF-16이라 str 파싱에서 뺀다
        assert triggers_of(task) == EXPECTED[name][1]


def test_cli_xml_dir(run_kb, vault, tmp_path):
    target = tmp_path / "다른 폴더 (xml)"
    result = run_kb("schedule", "--xml-dir", str(target))
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in target.iterdir()) == sorted(f"{n}.xml" for n in EXPECTED)
    assert set(create_lines(result.stdout).values()) == {target / f"{n}.xml" for n in EXPECTED}
    assert not (vault / ".state" / "schedule").exists()


def test_cli_print_and_xml_dir_are_exclusive(run_kb, tmp_path):
    result = run_kb("schedule", "--print", "--xml-dir", str(tmp_path))
    assert result.returncode == 4


def test_agent_session_refused(run_kb, vault, monkeypatch):
    result = run_kb("schedule", env={"KB_AGENT": "1"})
    assert result.returncode == 7
    assert not (vault / ".state" / "schedule").exists()
    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(core.PolicyRefused):
        schedule.main(["--root", str(vault)])


def test_main_in_process_never_spawns(vault, capsys, no_subprocess):
    _write_local(vault, "home")
    assert schedule.main(["--root", str(vault)]) == 0
    out = capsys.readouterr().out
    assert len(create_lines(out)) == 6


def test_hangul_repo_copy(tmp_path):
    """저장소 자체가 한글·공백 경로에 있을 때(회사 복사본): WorkingDirectory·Command가 그 경로."""
    repo = tmp_path / "한글 경로 (테스트)" / "work-kb"
    shutil.copytree(SCRIPTS, repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "config.yaml", repo / "config.yaml")
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k != "PYTHONUTF8"}
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "kb.py"), "schedule"],  # UTF-8 모드 없이(cp949 로캘)
        capture_output=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    stdout = result.stdout.decode("utf-8")  # core.setup_stdio가 UTF-8로 낸다
    assert result.returncode == 0, stdout + result.stderr.decode("utf-8", "replace")
    creates = create_lines(stdout)
    assert list(creates) == list(EXPECTED)
    for name, path in creates.items():
        assert path == repo / ".state" / "schedule" / f"{name}.xml"
        check_task(path, repo=repo, root=repo, enabled=not EXPECTED[name][2])
        assert "--root" not in split_args(_text(read_task(path), "t:Actions/t:Exec/t:Arguments"))
    assert "주의: 아직 없습니다" in stdout  # 복사본에는 .venv가 없다


# ---------------------------------------------------------------- 등록하지 않음


def _calls(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


@pytest.mark.parametrize("name", ["schedule.py", "init.py"])
def test_code_cannot_run_schtasks(name):
    tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"subprocess", "ctypes", "win32com", "pythoncom", "multiprocessing", "asyncio"}
    assert not _calls(tree) & {"system", "popen", "startfile", "spawnv", "spawnl", "execv", "execl"}


# ---------------------------------------------------------------- init 스케줄 단계


class Script:
    def __init__(self, answers: list[str], secrets: list[str] | None = None) -> None:
        self.answers = list(answers)
        self.secrets = list(secrets or [])
        self.asked: list[tuple[str, str | None]] = []

    def ask(self, prompt: str, default: str | None = None) -> str:
        self.asked.append((prompt, default))
        if not self.answers:
            raise EOFError
        return self.answers.pop(0) or (default or "")

    def ask_secret(self, prompt: str) -> str:
        if not self.secrets:
            raise EOFError
        return self.secrets.pop(0)


def _wizard(vault: Path, script: Script, out: list[str], values=None) -> init.Wizard:
    return init.Wizard(
        vault, ask=script.ask, ask_secret=script.ask_secret, out=out.append, steps=[init.step_schedule],
        values=values, cfg=config.load(vault),
    )


def test_init_step_yes_generates_without_running(vault, no_subprocess):
    script, out = Script(["y"]), []
    data = _wizard(vault, script, out).run({"environment": "company"})
    assert data == {"environment": "company"}  # 설정은 바꾸지 않는다
    assert script.asked[0][1] == "y"
    said = "\n".join(out)
    creates = create_lines(said)
    assert list(creates) == list(EXPECTED)
    for name, path in creates.items():
        assert path.parent == vault / ".state" / "schedule"
        check_task(path, repo=ROOT, root=vault, enabled=not EXPECTED[name][2])
    assert "실행하지 않았습니다" in said


def test_init_step_no_and_eof_skip(vault, no_subprocess):
    out: list[str] = []
    assert _wizard(vault, Script(["n"]), out).run({"environment": "home"}) == {"environment": "home"}
    assert not (vault / ".state" / "schedule").exists()
    assert any("kb.py schedule" in line for line in out)
    out = []
    _wizard(vault, Script([]), out).run({"environment": "home"})  # 입력 끝: 취소하지 않고 건너뜀
    assert any("입력이 끝나" in line for line in out)
    assert not (vault / ".state" / "schedule").exists()


def test_init_step_non_interactive_only_hints(vault, no_subprocess):
    script, out = Script([]), []
    _wizard(vault, script, out, values={}).run({"environment": "company"})
    assert script.asked == []
    assert any("kb.py schedule" in line for line in out)
    assert not (vault / ".state" / "schedule").exists()


def test_full_wizard_ends_with_schedule_step(vault, mock_jira):
    script = Script(["home", mock_jira.base_url, "1", "n", "n", "y"], [TOKEN])
    out: list[str] = []
    code = init.run_wizard(
        vault, ask=script.ask, ask_secret=script.ask_secret, out=out.append, run_doctor=False, schedule_step=True
    )
    assert code == 0, "\n".join(out)
    assert [p.split(" ")[0] for p, _ in script.asked] == ["환경", "Jira", "동기화할", "Confluence를", "LLM을", "작업"]
    assert yaml.safe_load((vault / "config.local.yaml").read_text(encoding="utf-8"))["environment"] == "home"
    for name in EXPECTED:
        check_task(vault / ".state" / "schedule" / f"{name}.xml", repo=ROOT, root=vault, enabled=True)
    assert out.index("\n[5] 스케줄") > out.index("\n[4] LLM")


def test_kb_init_piped_answers_generate_schedule(run_kb, vault, mock_jira):
    result = run_kb("init", stdin=f"company\n{mock_jira.base_url}\n{TOKEN}\n1\nn\nn\ny\n", timeout=90)
    assert result.returncode in (0, 1), result.stdout + result.stderr  # doctor 결과(✗가 있으면 1)
    assert "[5] 스케줄" in result.stdout
    assert TOKEN not in result.stdout + result.stderr
    assert "schtasks /Change /TN work-kb-ingest /ENABLE" in result.stdout
    assert (vault / "config.local.yaml").is_file()
    path = vault / ".state" / "schedule" / "work-kb-compile.xml"
    check_task(path, repo=ROOT, root=vault, enabled=False)


# ---------------------------------------------------------------- 문서


def test_scheduling_doc_matches_table():
    text = (ROOT / "docs" / "SCHEDULING.md").read_text(encoding="utf-8")
    for name, (args, triggers, _) in EXPECTED.items():
        assert f"`{name}`" in text
        assert f"kb.py {' '.join(args)}" in text
        for _, time, _ in triggers:
            assert time in text or name == "work-kb-sync"
    for needle in (
        "schtasks /Query /TN work-kb-sync /V /FO LIST",
        "schtasks /Change /TN work-kb-ingest /ENABLE",
        "schtasks /Change /TN work-kb-compile /ENABLE",
        "InteractiveToken",
        "-X utf8",
        ".state\\logs",
        "kb.py schedule",
    ):
        assert needle in text, needle


# ---------------------------------------------------------------- 스키마(Windows, 등록 없음)

_COM_CHECK = r"""
$ErrorActionPreference = 'Stop'
$svc = New-Object -ComObject Schedule.Service
$svc.Connect()
foreach ($f in Get-ChildItem -LiteralPath $env:KB_SCHED_DIR -Filter *.xml) {
  $def = $svc.NewTask(0)
  try {
    $def.XmlText = [System.IO.File]::ReadAllText($f.FullName)
    "OK $($f.BaseName) $($def.Settings.Enabled) $($def.Principal.LogonType) $($def.Triggers.Count)"
  } catch { "FAIL $($f.BaseName) $($_.Exception.Message)" }
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="작업 스케줄러는 Windows 전용")
def test_task_scheduler_accepts_xml_in_memory(tmp_path):
    """COM ITaskDefinition.XmlText는 스키마를 검사하지만 등록하지 않는다(RegisterTaskDefinition 없음)."""
    assert "Register" not in _COM_CHECK
    shell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell 없음")
    repo = tmp_path / "한글 경로 (테스트)" / "work-kb"
    xml_dir = tmp_path / "xml"
    schedule.generate(root=repo, repo=repo, company=True, xml_dir=xml_dir, out=lambda _: None)
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", _COM_CHECK],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "KB_SCHED_DIR": str(xml_dir)},
        timeout=120,
    )
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    if not lines and "Schedule.Service" in result.stderr:
        pytest.skip(f"작업 스케줄러 COM을 쓸 수 없음: {result.stderr[:200]}")
    rows = {parts[1]: parts for parts in (l.split(" ", 4) for l in lines)}
    assert set(rows) == set(EXPECTED), result.stdout + result.stderr
    for name, parts in rows.items():
        assert parts[0] == "OK", " ".join(parts)
        assert parts[2] == ("False" if EXPECTED[name][2] else "True")
        assert parts[3] == "3"  # TASK_LOGON_INTERACTIVE_TOKEN
        assert int(parts[4]) == len(EXPECTED[name][1])
