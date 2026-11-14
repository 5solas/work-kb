"""docs/*.md·obsidian/setup.md·AGENTS.md·README.md가 코드와 맞는지 검사한다.

- 문서에 나오는 `kb.py <명령>`은 kb.COMMANDS(또는 별칭)에 있고, 같은 줄에서 그 뒤에 오는 `--옵션`은
  그 명령의 --help에 있다(뒤따르는 pip·schtasks·git 등 다른 명령의 옵션은 보지 않는다).
- DEPLOY.md에 §9 체크리스트 11단계가 순서대로 들어 있다. 원문 그대로이고, 7단계만 Neovim에 맞게 고친
  문구다(지시서 원문은 Emacs, DECISIONS 1장).
- README의 종료 코드가 코어와 같고, USER-GUIDE의 Neovim 키 표가 work-kb 플러그인(lua)과 키·이름까지 같다.
- 문서에 실제 회사·호스트(`*.invalid`·127.0.0.1·localhost만, 루트 README의 저장소 주소는 허용 목록)와
  비밀값 모양이 없다.
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
# 명령·옵션·호스트·비밀값 검사 대상: docs/*.md 전부(USER-GUIDE·SCHEDULING·PACKAGING·LIVE·DECISIONS 포함),
# obsidian/setup.md, AGENTS.md, 루트 README.md. 저장소 기준 POSIX 경로.
CHECKED_DOCS = tuple(
    [f"docs/{p.name}" for p in sorted(DOCS.glob("*.md"))] + ["obsidian/setup.md", "AGENTS.md", "README.md"]
)

CHECKLIST = [
    "저장소 폴더 복사(`config.local.yaml`, `.state/`, `_trash/`, `examples/`, 집에서 만든 daily 제외).",
    "Python 최신 확인, `pip install -r requirements.txt`(불가 시 `--no-index --find-links wheels`). 환경변수 `PYTHONUTF8=1`.",
    "Jira·Confluence PAT 생성.",
    "`python scripts/kb.py init` → environment=company → Jira(프로젝트 키 선택) → Confluence(개인 스페이스) → LLM main(사내 Nemotron, 자동 탐지) → fallback(Gemma) → 스케줄.",
    "`python scripts/kb.py doctor` 전부 ✓. 공개 호스트 ✗가 하나라도 있으면 진행 금지.",
    '`jira_sync --jql "key = <실제키>" --dry-run` 확인 후 `kb.py sync --full`.',
    "Neovim: nvim-config 설치 확인, `install.ps1`, 새 터미널에서 `nvd --restart`, `:checkhealth work-kb`, 캡처 테스트. 사용법: `docs/USER-GUIDE.md`.",
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
_NEXT_COMMAND = re.compile(r"\s(?:;|&&|\|\||\||→)\s|(?<![\w-])(?:pip|schtasks|git|python|py|nvim|neovide)(?:\.exe)?\s")


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
    assert {"docs/SCHEDULING.md", "docs/PACKAGING.md", "docs/LIVE.md", "docs/DECISIONS.md", "docs/USER-GUIDE.md",
            "obsidian/setup.md", "AGENTS.md", "README.md"} <= set(CHECKED_DOCS)
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
    if name.startswith("docs/") and PurePosixPath(name).name in DOC_FILES:
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
                   "3.14", "3.15", "PyYAML", "--no-index", "python -m venv", "kb.py demo", "집 전용",
                   "doctor", "sync --jql"):
        assert needle in deploy, needle
    assert "첫 2주" in deploy and "uv" not in re.findall(r"\buv\b", deploy)
    assert "7단계는 Neovim에 맞게" in deploy  # 지시서 원문(Emacs)과 다른 단계를 밝힌다


# 지원 범위(D-NV1·D-NV2)를 적는 문서. 3.13·Neovim 0.11을 지원한다고 적지 않는다.
SUPPORT_DOCS = ("README.md", "docs/DEPLOY.md", "docs/PACKAGING.md", "docs/TROUBLESHOOTING.md", "docs/USER-GUIDE.md")


@pytest.mark.parametrize("name", SUPPORT_DOCS)
def test_docs_name_python_314_and_nvim_012_only(name):
    text = _read_rel(name)
    assert "3.14" in text, name
    for stale in ("3.13 또는 3.14", "3.13/3.14", "3.13·3.14", "3.14 또는 3.13", "3.14(또는 3.13)", "3.13,3.14",
                  "cp313", "0.11 이상"):
        assert stale not in text, (name, stale)


def test_readme_exit_codes_match_core():
    sys.path.insert(0, str(SCRIPTS))
    from lib import core

    readme = _read("README.md")
    table = readme.split("### 종료 코드", 1)[1].split("\n## ", 1)[0]
    codes = {int(m.group(1)) for m in re.finditer(r"^\|\s*(\d)\s*\|", table, re.M)}
    expected = {0} | {getattr(core, name).exit_code for name in dir(core)
                      if isinstance(getattr(core, name), type) and issubclass(getattr(core, name), core.KbError)}
    assert codes == expected


# work-kb 플러그인 진입점. 플러그인 폴더를 옮기면 이 상수 하나만 고친다.
PLUGIN_INIT = "nvim/lua/work-kb/init.lua"
NVIM_LUA = (ROOT / PLUGIN_INIT).parent


def test_nvim_front_end_files_exist():
    for rel in ("install.ps1", "uninstall.ps1", PLUGIN_INIT):
        assert (ROOT / rel).is_file(), rel


def _init_keys() -> list[tuple[str, str]]:
    """init.lua M.keys의 (키, 이름). 여러 줄로 쓴 항목(g·G)도 잡는다."""
    lua = (NVIM_LUA / "init.lua").read_text(encoding="utf-8")
    block = lua.split("M.keys = {", 1)[1].split("\n}\n", 1)[0]
    return re.findall(r'key = "(.)",\s*name = "([a-z-]+)"', block)


def _jira_menu() -> list[tuple[str, str]]:
    """jira.lua 메뉴의 (키, 작업 이름). 함수 이름의 _는 :WorkKb jira 작업 이름에서 -다."""
    jira = (NVIM_LUA / "jira.lua").read_text(encoding="utf-8")
    return [(k, fn.replace("_", "-")) for k, fn in re.findall(r'\{ key = "(.)", fn = "([a-z_]+)"', jira)]


def _row(text: str, key: str, name: str) -> str | None:
    m = re.search(rf"(?m)^\|\s*`{re.escape(key)}`\s*\|\s*`{re.escape(name)}`\s*\|.*$", text)
    return m.group(0) if m else None


def test_user_guide_keys_match_nvim_lua():
    """Neovim 키 표(접두 키 다음)와 j 메뉴가 docs/USER-GUIDE.md의 표에 키·이름까지 같게 있다."""
    keys = _init_keys()
    assert {k for k, _ in keys} >= {"c", "C", "F", "i", "t", "s", "o", "l", "?", "j", "g", "G", "a"}, keys
    jira = _jira_menu()
    assert {k for k, _ in jira} >= {"v", "c", "t", "a", "w", "n", "p", "q", "S", "f", "d"}, jira
    guide = _read("USER-GUIDE.md")
    for key, name in keys + jira:
        assert _row(guide, key, name), f"USER-GUIDE 키 표: `{key}` | `{name}`"


def test_agents_serve_row_matches_served():
    """AGENTS.md·docs/README.md의 serve 설명이 serve.SERVED와 같다: 서버가 도는 명령을 모두 적고, 읽기 전용이라
    하지 않는다(today·link는 파일을 쓴다). act transitions는 서버에서 돌지 않는다(P-01)."""
    _kb()
    serve = importlib.import_module("serve")
    names = []
    for name, subs in serve.SERVED.items():
        names += [name] if subs is None else [f"{name} {sub}" for sub in sorted(subs)]
    for doc, pattern in (("AGENTS.md", r"(?m)^\|\s*`serve`\s*\|.*$"), ("docs/README.md", r"(?m)^\|\s*`kb\.py serve`\s*\|.*$")):
        m = re.search(pattern, _read_rel(doc))
        assert m, doc
        row = m.group(0)
        for name in names:
            assert f"`{name}`" in row or name in row.replace("`", ""), (doc, name, row)
        assert "transitions" not in row and "읽기 계열" not in row and "읽기 전용" not in row, (doc, row)
        assert "today" in row and "link" in row and "쓴다" in row, (doc, row)


def test_user_guide_search_row_names_backends():
    """`s`는 fzf-lua(정규식, 대화형) → rg(구 그대로) → kb.py search(공백 AND) 순으로 쓴다(search.lua, K6)."""
    lua = (NVIM_LUA / "search.lua").read_text(encoding="utf-8").split("function M.search(query)", 1)[1]
    order = [lua.index(s) for s in ("fzf.live_grep(", "M.rg_search(rg", "M.kb_search(query")]
    assert order == sorted(order)
    row = _row(_read("USER-GUIDE.md"), "s", "search")
    assert row, "USER-GUIDE 키 표: s"
    positions = [row.find(s) for s in ("fzf-lua", "rg", "kb.py search")]
    assert -1 not in positions and positions == sorted(positions), row
    assert "공백은 AND" not in row.split("kb.py search", 1)[0], row  # AND는 kb.py search에만


def test_vault_setup_mentions_neovim_vault_option():
    """볼트를 따로 둘 때 KB_ROOT만으로는 Neovim에 효과가 없다: setup의 vault(nvim-config opts.vault)를 같이 적는다."""
    for doc in ("docs/USER-GUIDE.md", "docs/README.md"):
        paragraphs = [p for p in _read_rel(doc).split("\n\n") if "KB_ROOT" in p and "볼트" in p]
        assert any("`vault`" in p and "opts.vault" in p and "Neovim" in p for p in paragraphs), doc


def test_prefix_rebinding_documented_two_ways():
    """§6.8: 접두 키는 한 줄로 바꿀 수 있어야 한다. 두 방법(setup의 prefix, 키 하나 직접 붙이기)을 적는다."""
    init = (NVIM_LUA / "init.lua").read_text(encoding="utf-8")
    assert 'prefix = "<F8>"' in init and 'require("work-kb").call("capture")' in init  # 플러그인 머리말과 같은 꼴
    for doc in ("docs/USER-GUIDE.md", "docs/DEPLOY.md", "docs/README.md"):
        text = _read_rel(doc)
        assert 'prefix = "<F8>"' in text, doc
        assert 'vim.keymap.set("n", "<F8>c", function() require("work-kb").call("capture") end)' in text, doc
    section0 = _read("DECISIONS.md").split("\n## 1.", 1)[0]
    assert 'prefix = "<F8>"' in section0


def test_decisions_has_no_stale_emacs_rules():
    """DECISIONS가 Neovim 판의 현재 상태를 적는다: Emacs 판 전용 규칙은 지우거나 '대체됨'으로 표시한다(P-26)."""
    text = _read("DECISIONS.md")
    assert "KB_SKIP_EMACS" not in text
    head = text.split("\n## 부록 A", 1)[0]  # 현재 규칙(0~3장). 부록은 원 설계 기록이다
    for stale in ("work-kb.el", "C-c k", "emacsclientw", "work-kb-prefix-key", "Emacs 키 표"):
        assert stale not in head, stale
    rows0 = [ln for ln in head.split("\n## 1.", 1)[0].splitlines() if ln.startswith("|")]
    assert rows0 and not [ln for ln in rows0 if "Emacs" in ln or "dired" in ln]  # §0 확인 목록은 Neovim 항목
    assert "Phase 0~6은 Emacs 판으로 만들었다" in head  # §3의 Emacs 항목은 그때 기록임을 밝힌다
    assert text.splitlines()[2].count("6장") == 1  # 우선순위 줄에 6장
    chapter6 = text.split("\n## 6. ", 1)[1]
    assert "nvim 판에서 대체된 기록" in chapter6
    for record in ("D-NV1", "D-NV2", "D-NV3", "D-NV4"):
        assert record in chapter6, record
    for d in ("D08", "D58", "D62", "D64"):
        row = re.search(rf"(?m)^\| {d} \|.*$", text).group(0)
        assert "emacs/" not in row and "Emacs" not in row and "work-kb.el" not in row, row
    d58 = re.search(r"(?m)^\| D58 \|.*$", text).group(0)
    assert "ingest" in d58 and "work-kb-<name>" in d58, d58


def test_user_guide_capture_keys_match_capture_lua():
    """`c`·`i`는 분할 버퍼에서 쓰고 capture.lua의 키로 저장·취소한다. `G`도 LLM을 부른다. status는 연결을 보지 않는다."""
    lua = (NVIM_LUA / "capture.lua").read_text(encoding="utf-8")
    save = re.search(r'"<C-c><C-c>", call_entry\("finish"\)', lua)
    cancel = re.search(r'"<C-c><C-k>", call_entry\("cancel"\)', lua)
    assert save and cancel
    guide = _read("USER-GUIDE.md")
    for key, name in (("c", "capture"), ("i", "capture-inbox")):
        row = _row(guide, key, name)
        assert row and "C-c C-c" in row and "C-c C-k" in row and "버퍼" in row, row
    row = _row(guide, "G", "ingest-dry-run")
    assert row and "LLM" in row, row
    assert "../" not in guide and not re.search(r'rtp:append\("[^"]*\\', guide)  # Lua 예시는 슬래시 경로
    status = re.search(r"(?m)^\|\s*`status`\s*\|.*$", _read_rel("AGENTS.md")).group(0)
    assert "doctor" in status and "확인하지 않" in status, status


def test_agents_region_table_matches_managed_names():
    """AGENTS.md 관리 구역: 방식 줄의 이름과 소유 표의 이름이 같고, 코드가 쓰는 구역이 모두 있다."""
    section = _read_rel("AGENTS.md").split("## 관리 구역", 1)[1].split("\n## ", 1)[0]
    methods = set()
    for line in section.splitlines():
        if line.startswith("- ") and "대상은" in line:
            methods |= set(re.findall(r"`([a-z]+)`", line.split("대상은", 1)[1]))
    table: dict[str, str] = {}
    for line in section.splitlines():
        m = re.match(r"^\|\s*(`[a-z]+`(?:,\s*`[a-z]+`)*)\s*\|\s*([a-z_]+)\s*\|$", line)
        if m:
            for name in re.findall(r"`([a-z]+)`", m.group(1)):
                table[name] = m.group(2)
    assert methods and methods == set(table), (methods ^ set(table))
    assert table["proposals"] == "compile" and table["lint"] == "lint"
    code = "\n".join(p.read_text(encoding="utf-8") for p in SCRIPTS.glob("*.py"))
    used = set(re.findall(r'managed\.replace_region\([^,]+,\s*"([a-z]+)"', code))
    used |= set(re.findall(r'(?m)^REGION = "([a-z]+)"', code))
    assert used <= set(table), used - set(table)


def test_readme_points_to_user_guide_and_keeps_core_items():
    readme = _read("README.md")
    for item in ("docs/USER-GUIDE.md", "KB_AGENT=1", "AGENTS.md", "obsidian/setup.md", "SCHEDULING.md"):
        assert item in readme, item
    for state in ("pending", "sending", "failed", "done", "uncertain", "--retry-failed", "--retry-uncertain"):
        assert state in readme, state

def test_troubleshooting_topics():
    text = _read("TROUBLESHOOTING.md")
    for needle in ("keyring", "PAT", "ca_bundle", "공개 호스트", "PowerShell", "`F`", "py -3", "cp949", "UTF-8",
                   "종료 6", "uncertain", "reasoning_budget_tokens", "gave_up", "moved", "Bases", "--retry-uncertain"):
        assert needle in text, needle


_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")
# 실제 호스트를 써도 되는 문서: 루트 README의 저장소 주소(받는 곳)만. 회사·서비스 호스트는 여전히 안 된다.
REAL_HOSTS_ALLOWED = {"README.md": {"github.com"}}


def _host_allowed(host: str, name: str = "") -> bool:
    """문서에 쓸 수 있는 호스트: `*.invalid`, 127.0.0.1, localhost (DECISIONS), 그리고 문서별 허용 목록."""
    host = host.rstrip(".").lower()
    if host in REAL_HOSTS_ALLOWED.get(name, ()):
        return True
    return host in ("invalid", "127.0.0.1", "localhost") or host.endswith(".invalid")


def _doc_problems(name: str, text: str) -> list[str]:
    problems = [f"{name}: 허용되지 않은 호스트 {h}" for h in _HOST_RE.findall(text) if not _host_allowed(h, name)]
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
    assert not _doc_problems("README.md", "https://github.com/5solas/work-kb")  # 허용 목록
    assert _doc_problems("docs/README.md", "https://github.com/5solas/work-kb")  # 다른 문서에서는 안 된다
    assert _doc_problems("README.md", "https://api.deepinfra.com/v1")


@pytest.mark.parametrize("name", CHECKED_DOCS)
def test_no_real_hosts_or_secrets(name):
    problems = _doc_problems(name, _read_rel(name))
    assert not problems, "\n".join(problems)
