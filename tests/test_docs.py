"""docs/*.md·obsidian/setup.md·AGENTS.md가 코드와 맞는지 검사한다.

- 문서에 나오는 `kb.py <명령>`은 kb.COMMANDS(또는 별칭)에 있고, 같은 줄에서 그 뒤에 오는 `--옵션`은
  그 명령의 --help에 있다(뒤따르는 pip·schtasks·git 등 다른 명령의 옵션은 보지 않는다).
- DEPLOY.md에 §9 체크리스트 11단계가 순서대로, 원문 그대로 들어 있다.
- README의 종료 코드·Emacs 키가 코어·work-kb.el과 같다.
- 문서에 실제 회사·호스트(`*.invalid`·127.0.0.1·localhost만)와 비밀값 모양이 없다.
"""
from __future__ import annotations

import functools
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOCS = ROOT / "docs"
DOC_FILES = ("README.md", "DEPLOY.md", "TROUBLESHOOTING.md")
# 명령·옵션·호스트·비밀값 검사 대상: docs/*.md 전부(SCHEDULING·PACKAGING·LIVE·DECISIONS 포함),
# obsidian/setup.md, AGENTS.md. 저장소 기준 POSIX 경로.
CHECKED_DOCS = tuple(
    [f"docs/{p.name}" for p in sorted(DOCS.glob("*.md"))] + ["obsidian/setup.md", "AGENTS.md"]
)

CHECKLIST = [
    "저장소 폴더 복사(`config.local.yaml`, `.state/`, `_trash/`, `examples/`, 집에서 만든 daily 제외).",
    "Python 최신 확인, `pip install -r requirements.txt`(불가 시 `--no-index --find-links wheels`). 환경변수 `PYTHONUTF8=1`.",
    "Jira·Confluence PAT 생성.",
    "`python scripts/kb.py init` → environment=company → Jira(프로젝트 키 선택) → Confluence(개인 스페이스) → LLM main(사내 Nemotron, 자동 탐지) → fallback(Gemma) → 스케줄.",
    "`python scripts/kb.py doctor` 전부 ✓. 공개 호스트 ✗가 하나라도 있으면 진행 금지.",
    '`jira_sync --jql "key = <실제키>" --dry-run` 확인 후 `kb.py sync --full`.',
    "Emacs: `(load \"…/emacs/work-kb.el\")`, `work-kb-root`, 필요하면 `work-kb-prefix-key` 변경, 캡처 테스트. 서버 모드 + `capture.ahk`(선택).",
    "Obsidian: 볼트 열기, `obsidian/setup.md`.",
    "스케줄 등록(`SCHEDULING.md` 또는 init이 만든 명령).",
    "첫 2주: 캡처 + `link` + `sync`. 그다음 `ingest`·`compile`, 마지막 Confluence 푸시 검토 후 상시화.",
    "보안: 회사에서 생긴 파일은 집으로 가져오지 않는다. 개선점은 말로 적어 집에서 반영.",
]


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _kb():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("kb")


@functools.lru_cache(maxsize=None)
def _help(*argv: str) -> str:
    env = {**os.environ, "PYTHONUTF8": "1", "KB_AGENT": ""}
    env.pop("KB_AGENT", None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "kb.py"), *argv, "--help"],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT), timeout=60,
    )
    return proc.stdout + proc.stderr


def _command_help(command: str) -> str:
    text = _help(command)
    if command == "act":  # 하위 명령의 옵션도 포함
        for sub in ("comment", "transition", "transitions", "assign", "worklog", "create", "promote", "flush", "list"):
            text += _help("act", sub)
    return text


def _read_rel(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


_CMD_RE = re.compile(r"kb\.py\s+([a-z][a-z_-]*)([^`\n]*)")
_FLAG_RE = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
# `kb.py 명령` 뒤 조각은 다음 셸 명령(구분자, 또는 pip·schtasks·git 등)에서 끊는다: 그 옵션은 kb.py 것이 아니다.
_NEXT_COMMAND = re.compile(r"\s(?:;|&&|\|\||\||→)\s|(?<![\w-])(?:pip|schtasks|git|python|py|emacs|emacsclient)(?:\.exe)?\s")


def _mentions(text: str):
    """(명령, 그 명령 줄 조각의 --옵션 목록). 표 줄이 `kb.py 명령`으로 시작하면 줄 전체의 옵션을 본다."""
    for line in text.splitlines():
        row = re.match(r"\|\s*`kb\.py\s+([a-z][a-z_-]*)`", line)
        if row:
            yield row.group(1), _FLAG_RE.findall(line)
            continue
        for match in _CMD_RE.finditer(line):
            fragment = _NEXT_COMMAND.split(match.group(2), maxsplit=1)[0]
            yield match.group(1), _FLAG_RE.findall(fragment)


# 기록 문서가 검토 때 '고르지 않은 안'으로 적어 둔 명령 (문서, 명령). 새 문서에는 늘리지 않는다.
HISTORICAL_MENTIONS = {("docs/DECISIONS.md", "daily_prepare")}


def _known_commands() -> set[str]:
    kb = _kb()
    return set(kb.COMMANDS) | set(kb.ALIASES)


def _canonical(command: str) -> str:
    return _kb().ALIASES.get(command, command)


def test_mentions_cut_at_other_commands():
    text = (
        "kb.py wheels --dir x; pip install --no-index --find-links wheels\n"
        "kb.py schedule --print → schtasks /Create /TN a /XML b /F\n"
        "kb.py sync --full && git status --short\n"
        "| `kb.py act` | --retry-failed |\n"
    )
    assert list(_mentions(text)) == [
        ("wheels", ["--dir"]), ("schedule", ["--print"]), ("sync", ["--full"]), ("act", ["--retry-failed"])
    ]


@pytest.mark.parametrize("name", DOC_FILES)
def test_doc_exists_and_utf8(name):
    text = _read(name)
    assert len(text) > 500


def test_checked_docs_cover_all_docs():
    assert {"docs/SCHEDULING.md", "docs/PACKAGING.md", "docs/LIVE.md", "docs/DECISIONS.md",
            "obsidian/setup.md", "AGENTS.md"} <= set(CHECKED_DOCS)
    assert {f"docs/{name}" for name in DOC_FILES} <= set(CHECKED_DOCS)


@pytest.mark.parametrize("name", CHECKED_DOCS)
def test_mentioned_commands_exist(name):
    known = _known_commands()
    seen = set()
    for command, _flags in _mentions(_read_rel(name)):
        seen.add(command)
        if (name, command) in HISTORICAL_MENTIONS:
            continue
        assert command in known, f"{name}: kb.py {command}는 COMMANDS에 없다"
    if PurePosixPath(name).name in DOC_FILES:
        assert seen


@pytest.mark.parametrize("name", CHECKED_DOCS)
def test_mentioned_flags_in_help(name):
    commands = _kb().COMMANDS
    for command, flags in _mentions(_read_rel(name)):
        command = _canonical(command)
        if not flags or command not in commands:
            continue
        module = commands[command].module
        if not (SCRIPTS / f"{module}.py").exists():
            continue  # 다른 작업이 아직 만들지 않은 모듈
        text = _command_help(command)
        for flag in flags:
            assert flag in text, f"{name}: kb.py {command} {flag}가 --help에 없다"


def test_readme_command_table_covers_all_commands():
    readme = _read("README.md")
    for command in _kb().COMMANDS:
        assert re.search(rf"\|\s*`kb\.py {command}`", readme), f"README 명령 표에 {command}가 없다"


def test_deploy_checklist_present_in_order():
    deploy = _read("DEPLOY.md")
    headings = [int(m.group(1)) for m in re.finditer(r"^### (\d+)\. ", deploy, re.M)]
    assert headings == list(range(1, 12))
    position = -1
    for number, step in enumerate(CHECKLIST, 1):
        index = deploy.find(step)
        assert index > position, f"{number}단계 원문이 없거나 순서가 다르다: {step}"
        position = index


def test_deploy_exclusions_and_interpreters():
    deploy = _read("DEPLOY.md")
    for needle in ("jira/.cache.sqlite*", ".pytest_cache", "__pycache__", "tests/", ".venv*", "jira/outbox.jsonl",
                   "3.13", "3.14", "3.15", "PyYAML", "--no-index", "python -m venv", "kb.py demo", "집 전용",
                   "first-run" if False else "doctor", "sync --jql"):
        assert needle in deploy, needle
    assert "첫 2주" in deploy and "uv" not in re.findall(r"\buv\b", deploy)


def test_readme_exit_codes_match_core():
    sys.path.insert(0, str(SCRIPTS))
    from lib import core

    readme = _read("README.md")
    table = readme.split("### 종료 코드", 1)[1].split("\n## ", 1)[0]
    codes = {int(m.group(1)) for m in re.finditer(r"^\|\s*(\d)\s*\|", table, re.M)}
    expected = {0} | {getattr(core, name).exit_code for name in dir(core)
                      if isinstance(getattr(core, name), type) and issubclass(getattr(core, name), core.KbError)}
    assert codes == expected


def test_readme_emacs_keys_match_el():
    el = (ROOT / "emacs" / "work-kb.el").read_text(encoding="utf-8")
    keymap = re.search(r"defconst work-kb--keys(.*?)\"`work-kb-command-map'", el, re.S).group(1)
    el_keys = dict(re.findall(r'\("(.)" \. (work-kb-[a-z-]+)\)', keymap))
    readme = _read("README.md")
    for key, func in el_keys.items():
        assert re.search(rf"\|\s*`{re.escape(key)}`\s*\|\s*`{func}`", readme), f"README 키 표: {key} {func}"
    transient = re.search(r"transient-define-prefix work-kb-jira(.*?)\n\n", el, re.S)
    jira_keys = re.findall(r'\("(.)" "', transient.group(1) if transient else el)
    section = readme.split("### `j` Jira transient", 1)[1].split("\n###", 1)[0]
    for key in jira_keys:
        assert re.search(rf"\|\s*`{re.escape(key)}`\s*\|", section), f"README j 표: {key}"
    for item in ("work-kb-root", "work-kb-vault", "work-kb-python", "work-kb-prefix-key",
                 '(setq work-kb-prefix-key "C-c c")', '(keymap-set global-map "<f8>" work-kb-command-map)',
                 "work-kb-rebind-prefix", "capture.ahk", "KB_AGENT=1", "AGENTS.md", "obsidian/setup.md", "SCHEDULING.md"):
        assert item in readme, item
    for state in ("pending", "sending", "failed", "done", "uncertain", "--retry-failed", "--retry-uncertain"):
        assert state in readme, state


def test_troubleshooting_topics():
    text = _read("TROUBLESHOOTING.md")
    for needle in ("keyring", "PAT", "ca_bundle", "공개 호스트", "PowerShell", "`F`", "py -3", "cp949", "UTF-8",
                   "종료 6", "uncertain", "reasoning_budget_tokens", "gave_up", "moved", "Bases", "--retry-uncertain"):
        assert needle in text, needle


_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")


def _host_allowed(host: str) -> bool:
    """문서에 쓸 수 있는 호스트: `*.invalid`, 127.0.0.1, localhost (DECISIONS)."""
    host = host.rstrip(".").lower()
    return host in ("invalid", "127.0.0.1", "localhost") or host.endswith(".invalid")


def _doc_problems(name: str, text: str) -> list[str]:
    problems = [f"{name}: 허용되지 않은 호스트 {h}" for h in _HOST_RE.findall(text) if not _host_allowed(h)]
    if re.search(r"Bearer [A-Za-z0-9]", text):
        problems.append(f"{name}: Bearer 토큰")
    for run in re.findall(r"(?<![A-Za-z0-9])[A-Za-z0-9]{32,}(?![A-Za-z0-9])", text):
        problems.append(f"{name}: 키 모양 {run[:6]}…({len(run)}자)")
    return problems


def test_doc_problem_rule_self_check():
    assert not _doc_problems("x.md", "http://jira.example.invalid/a http://127.0.0.1:8081 http://localhost/x")
    assert _doc_problems("x.md", "https://jira.example.com/browse/KB-1")  # 문서용 예약 도메인: 규칙이 거부해야 한다
    assert _doc_problems("x.md", "https://api.deepinfra.com/v1/openai")
    assert _doc_problems("x.md", "Authorization: Bearer abc")
    assert _doc_problems("x.md", "Kq7" * 11)


@pytest.mark.parametrize("name", CHECKED_DOCS)
def test_no_real_hosts_or_secrets(name):
    problems = _doc_problems(name, _read_rel(name))
    assert not problems, "\n".join(problems)
