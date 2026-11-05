"""docs/README.md·DEPLOY.md·TROUBLESHOOTING.md가 코드와 맞는지 검사한다.

- 문서에 나오는 `kb.py <명령>`은 kb.COMMANDS에 있고, 그 줄의 `--옵션`은 그 명령의 --help에 있다.
- DEPLOY.md에 §9 체크리스트 11단계가 순서대로, 원문 그대로 들어 있다.
- README의 종료 코드·Emacs 키가 코어·work-kb.el과 같다.
- 문서에 실제 회사·호스트가 없다.
"""
from __future__ import annotations

import functools
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOCS = ROOT / "docs"
DOC_FILES = ("README.md", "DEPLOY.md", "TROUBLESHOOTING.md")

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


_CMD_RE = re.compile(r"kb\.py\s+([a-z][a-z-]*)([^`\n]*)")
_FLAG_RE = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def _mentions(text: str):
    """(명령, 그 명령 줄 조각의 --옵션 목록). 표 줄이 `kb.py 명령`으로 시작하면 줄 전체의 옵션을 본다."""
    for line in text.splitlines():
        row = re.match(r"\|\s*`kb\.py\s+([a-z][a-z-]*)`", line)
        if row:
            yield row.group(1), _FLAG_RE.findall(line)
            continue
        for match in _CMD_RE.finditer(line):
            yield match.group(1), _FLAG_RE.findall(match.group(2))


@pytest.mark.parametrize("name", DOC_FILES)
def test_doc_exists_and_utf8(name):
    text = _read(name)
    assert len(text) > 500


@pytest.mark.parametrize("name", DOC_FILES)
def test_mentioned_commands_exist(name):
    commands = _kb().COMMANDS
    seen = set()
    for command, _flags in _mentions(_read(name)):
        seen.add(command)
        assert command in commands, f"{name}: kb.py {command}는 COMMANDS에 없다"
    assert seen


@pytest.mark.parametrize("name", DOC_FILES)
def test_mentioned_flags_in_help(name):
    commands = _kb().COMMANDS
    for command, flags in _mentions(_read(name)):
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


def test_no_real_hosts_or_secrets():
    pattern = re.compile(r"https?://([A-Za-z0-9.-]+)")
    allowed = ("example.invalid", "127.0.0.1", "localhost")
    for name in DOC_FILES:
        text = _read(name)
        for host in pattern.findall(text):
            assert host.endswith(allowed) or host in allowed, f"{name}: 허용되지 않은 호스트 {host}"
        assert not re.search(r"Bearer [A-Za-z0-9]", text)
        assert not re.search(r"[A-Za-z0-9]{32,}", text)
