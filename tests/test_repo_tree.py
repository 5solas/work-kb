"""저장소 트리 최종 점검 (지시서 §4·§7·§10, DECISIONS D13·D60, W6.4).

- §4 레이아웃의 추적 대상 경로가 모두 있다(실행 중 생기는 파일은 .gitignore·DEPLOY 제외 목록으로 본다).
- 비밀값이 없다: `Bearer <긴 토큰>`, 참조가 아닌 `api_key:` 값, DeepInfra 키 모양(영숫자 32자 이상).
- fixture·배포 코드의 URL은 `*.invalid`·`127.0.0.1`만. `api.deepinfra.com`은 config.local.example.yaml과
  tests/test_live.py에서만.
- AGENTS.md 앵커(절 제목)와 명령 목록이 kb.COMMANDS와 맞다.
- config.yaml에 연결 값이 없다.
- .gitignore와 DEPLOY.md 제외 목록이 실행 산출물을 모두 덮는다.
- nvim/capture.ahk(AutoHotkey v2)의 형식. 이 PC에는 AutoHotkey가 없어 실행은 확인하지 않는다.

검사 대상 파일은 .gitignore가 무시하지 않는 것(= 저장소에 올라가는 것)이다. .gitignore 해석은 이 저장소가
쓰는 문법(`!`, 끝 `/`, 앞 `/`, `*`, `**`)만 구현한다. git은 부르지 않는다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath

import pytest
import yaml

import kb

ROOT = Path(__file__).resolve().parents[1]

# ── §4 레이아웃 ──────────────────────────────────────────────

ROOT_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "config.yaml",
    "config.local.example.yaml",
    "requirements.txt",
    ".gitignore",
]
SCRIPTS = [
    "kb.py",
    "init.py",
    "doctor.py",
    "link.py",
    "jira_sync.py",
    "jira_act.py",
    "daily_prepare.py",
    "compile.py",
    "ingest_images.py",
    "lint.py",
    "confluence_push.py",
    "clip_image.ps1",
]
LIB_MODULES = [
    "__init__",
    "config",
    "secrets",
    "frontmatter",
    "managed",
    "jira_client",
    "llm_client",
    "llm_detect",
    "wikimarkup",
    # §4 목록 밖이지만 다른 모듈이 기대는 것
    "core",
    "net",
    "notes",
    "outbox",
    "prompts",
    "fts",
    "confluence_client",
]
PROMPT_TASKS = ["tag", "decisions", "entity_summary", "topic_proposal", "image_ingest", "contradictions"]
DOCS = ["README", "DEPLOY", "SCHEDULING", "DECISIONS", "TROUBLESHOOTING", "LIVE"]
# work-kb 플러그인 진입점. 플러그인 폴더를 옮기면 이 상수 하나만 고친다.
PLUGIN_INIT = "nvim/lua/work-kb/init.lua"
OTHER_FILES = [
    PLUGIN_INIT,
    "install.ps1",
    "uninstall.ps1",
    "obsidian/setup.md",
    "jira/_views/일감.base",
    "wiki/엔티티.base",
    "tests/mock_jira.py",
    "tests/mock_confluence.py",
    "tests/mock_llm.py",
    "tests/conftest.py",
    "examples/sample-vault/README.md",
    "examples/sample-vault/config.yaml",
    "examples/sample-vault/config.local.example.yaml",
]
# 빈 채로 복사돼야 하는 볼트 폴더(.gitkeep으로 유지)
KEEP_DIRS = ["daily", "inbox", "raw/assets", "jira/_views", "wiki/projects", "wiki/people", "wiki/topics"]


def _required_paths() -> list[str]:
    paths = list(ROOT_FILES)
    paths += [f"scripts/{name}" for name in SCRIPTS]
    paths += [f"scripts/lib/{name}.py" for name in LIB_MODULES]
    paths += [f"prompts/{task}.md" for task in PROMPT_TASKS]
    paths += [f"docs/{name}.md" for name in DOCS]
    paths += OTHER_FILES
    return paths


@pytest.mark.parametrize("rel", _required_paths())
def test_layout_path_exists(rel):
    path = ROOT / rel
    assert path.is_file(), f"§4 경로 없음: {rel}"
    assert path.stat().st_size > 0, f"빈 파일: {rel}"


@pytest.mark.parametrize("rel", KEEP_DIRS)
def test_vault_folders_are_kept(rel):
    folder = ROOT / rel
    assert folder.is_dir(), rel
    assert (folder / ".gitkeep").is_file() or any(p.suffix == ".base" for p in folder.iterdir()), rel
    assert not _ignored(rel + "/.gitkeep"), f".gitkeep이 무시됨: {rel}"


@pytest.mark.parametrize("name", sorted(kb.COMMANDS))
def test_every_command_module_exists(name):
    command = kb.COMMANDS[name]
    assert (ROOT / "scripts" / f"{command.module}.py").is_file(), f"kb.py {name} → scripts/{command.module}.py 없음"


def test_claude_md_points_to_agents_md():
    assert (ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "AGENTS.md를 읽어라."


def test_requirements_are_the_five_dependencies():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    names = {re.split(r"[<>=!~; \[]", ln.strip(), maxsplit=1)[0].lower() for ln in lines if ln.strip() and not ln.startswith("#")}
    assert names == {"requests", "pyyaml", "markdown", "keyring", "pillow"}


def test_fixture_spec():
    """§7: 허구 프로젝트 KB·OPS, 이슈 15개, 한국어 본문, wiki markup, 이미지 3장."""
    fixtures = ROOT / "tests" / "fixtures"
    issues = json.loads((fixtures / "jira" / "issues.json").read_text(encoding="utf-8"))
    if isinstance(issues, dict):
        issues = issues["issues"]
    assert len(issues) == 15
    assert {issue["key"].split("-")[0] for issue in issues} == {"KB", "OPS"}
    descriptions = [issue["fields"].get("description") or "" for issue in issues]
    assert any(re.search(r"[가-힣]", text) for text in descriptions), "한국어 본문 없음"
    assert any(re.search(r"(?m)^h[1-6]\. |\{code|\{noformat|^\|\||\[[^\]]+\|https?://", text) for text in descriptions), "wiki markup 없음"
    assert (fixtures / "jira" / "fields.json").is_file()
    images = sorted(p.name for p in (fixtures / "images").iterdir() if p.suffix.lower() == ".png")
    assert images == ["document.png", "mail.png", "whiteboard.png"]
    for name in images:
        assert (fixtures / "images" / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", name


# ── .gitignore 해석 ──────────────────────────────────────────

def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def _gitignore_rules(text: str) -> list[tuple[bool, bool, bool, re.Pattern[str]]]:
    """(부정, 폴더만, 경로 고정, 정규식) 목록."""
    rules = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        line = line[1:] if negate else line
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = "/" in line
        rules.append((negate, dir_only, anchored, _glob_regex(line.lstrip("/"))))
    return rules


RULES = _gitignore_rules((ROOT / ".gitignore").read_text(encoding="utf-8"))


def _ignored(rel: str, rules=None) -> bool:
    """rel(볼트 기준 POSIX 경로, 파일)이 무시되는가. 무시된 폴더 아래는 되살릴 수 없다(git 규칙)."""
    rules = RULES if rules is None else rules
    parts = rel.strip("/").split("/")
    for depth in range(1, len(parts) + 1):
        sub = "/".join(parts[:depth])
        is_dir = depth < len(parts) or rel.endswith("/")
        state = False
        for negate, dir_only, anchored, regex in rules:
            if dir_only and not is_dir:
                continue
            target = sub if anchored else sub.rsplit("/", 1)[-1]
            if regex.match(target):
                state = not negate
        if state:
            return True
    return False


def test_gitignore_matcher_self_check():
    rules = _gitignore_rules("/daily/*.md\n.venv*/\n/inbox/*\njira/.cache.sqlite*\n!**/.gitkeep\n")
    assert _ignored("daily/2026-11-05.md", rules)
    assert not _ignored("examples/sample-vault/daily/2026-11-05.md", rules)
    assert _ignored(".venv313/Lib/site.py", rules) and not _ignored(".venvx", rules)
    assert _ignored("inbox/메모.md", rules) and not _ignored("inbox/.gitkeep", rules)
    assert _ignored("jira/.cache.sqlite-wal", rules)


# 실행 중 생기는 산출물(§4·§5.9·D60). 저장소에 올라가면 안 되고(.gitignore) 회사로 복사하지도 않는다(DEPLOY).
RUNTIME_ARTEFACTS = [
    "config.local.yaml",
    ".state/sync.json",
    ".state/logs/kb.log",
    "_trash/2026-11-05/x.md",
    "jira/.cache.sqlite",
    "jira/.cache.sqlite-wal",
    "jira/.cache.sqlite-shm",
    "jira/outbox.jsonl",
    "jira/KB-12.md",
    "jira/_views/today.md",
    "wiki/index.md",
    "wiki/decisions.md",
    "wiki/actions.md",
    "wiki/_proposals.md",
    "wiki/_lint.md",
    "wiki/projects/KB.md",
    "wiki/people/김철수.md",
    "wiki/topics/캐시.md",
    "daily/2026-11-05.md",
    "inbox/20261105-0930-회의 메모.md",
    "inbox/회의실 화이트보드.png",
    "raw/assets/2026-11-05-1420-메일캡처.png",
    "__pycache__/x.pyc",
    "scripts/__pycache__/kb.cpython-314.pyc",
    ".pytest_cache/README.md",
    ".venv/Scripts/python.exe",
    ".venv313/Scripts/python.exe",
]
# 저장소에는 올리지 않지만(무시) 회사로는 가져가는 것
IGNORED_ONLY = [".env", "wheels/PyYAML-6.0.3-cp314-cp314-win_amd64.whl", ".obsidian/app.json"]
# 반드시 추적되는 것
TRACKED = [
    "jira/_views/일감.base",
    "wiki/엔티티.base",
    "config.yaml",
    "config.local.example.yaml",
    "examples/sample-vault/daily/2026-11-05.md",
    "examples/sample-vault/inbox/회의실 화이트보드.png",
    "examples/sample-vault/config.local.example.yaml",
    "tests/fixtures/llm/vault/jira/KB-12.md",
    "tests/fixtures/notes/daily/2026-11-04.md",
    PLUGIN_INIT,
    "install.ps1",
    "docs/DEPLOY.md",
]


@pytest.mark.parametrize("rel", RUNTIME_ARTEFACTS + IGNORED_ONLY)
def test_gitignore_covers_runtime_artefacts(rel):
    assert _ignored(rel), f".gitignore가 덮지 않음: {rel}"


@pytest.mark.parametrize("rel", TRACKED)
def test_gitignore_keeps_tracked_files(rel):
    assert not _ignored(rel), f".gitignore가 추적 파일을 무시함: {rel}"


# DEPLOY.md 1단계 제외 목록에 있어야 하는 이름(D60). 앞 RUNTIME_ARTEFACTS의 각 묶음에 대응한다.
DEPLOY_EXCLUSIONS = {
    "config.local.yaml": ["config.local.yaml"],
    ".state": [".state"],
    "_trash": ["_trash"],
    "examples": ["examples/"],
    "daily": ["daily/*.md", "daily/"],
    "inbox": ["inbox/"],
    "raw/assets": ["raw/assets"],
    "jira/*.md": ["jira/*.md"],
    "jira/_views/*.md": ["jira/_views/*.md"],
    "outbox": ["jira/outbox.jsonl"],
    "cache": ["jira/.cache.sqlite"],
    "wiki": ["wiki/index.md", "wiki/projects/*.md", "wiki/people/*.md", "wiki/topics/*.md"],
    "venv": [".venv"],
    "git": ["`.git`"],
    "pycache": ["__pycache__"],
    "pytest_cache": [".pytest_cache"],
    "tests": ["tests/"],
}


def test_deploy_md_lists_every_exclusion():
    text = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    step1 = re.search(r"(?ms)^#+ *1\..*?(?=^#+ *2\.)", text)
    scope = step1.group(0) if step1 else text
    missing = [name for name, spellings in DEPLOY_EXCLUSIONS.items() if not all(s in scope for s in spellings)]
    assert not missing, f"DEPLOY.md 1단계 제외 목록에 없음: {missing}"


def test_deploy_exclusions_are_gitignored_runtime_artefacts():
    """DEPLOY가 빼는 실행 산출물은 모두 .gitignore에도 있다(examples/·tests/·.git은 추적 대상이라 예외)."""
    for name in DEPLOY_EXCLUSIONS:
        if name in {"examples", "tests", "git"}:
            continue
        hits = [rel for rel in RUNTIME_ARTEFACTS if _artefact_group(rel) == name]
        assert hits, f"RUNTIME_ARTEFACTS에 {name} 예가 없음"
        assert all(_ignored(rel) for rel in hits), name


def _artefact_group(rel: str) -> str:
    if rel == "config.local.yaml":
        return rel
    if rel.startswith(".state/"):
        return ".state"
    if rel.startswith("_trash/"):
        return "_trash"
    if rel.startswith("jira/.cache.sqlite"):
        return "cache"
    if rel == "jira/outbox.jsonl":
        return "outbox"
    if rel.startswith("jira/_views/"):
        return "jira/_views/*.md"
    if rel.startswith("jira/"):
        return "jira/*.md"
    if rel.startswith("wiki/"):
        return "wiki"
    if rel.startswith("raw/assets/"):
        return "raw/assets"
    if rel.split("/", 1)[0] in {"daily", "inbox"}:
        return rel.split("/", 1)[0]
    if "__pycache__" in rel:
        return "pycache"
    if rel.startswith(".pytest_cache"):
        return "pytest_cache"
    if rel.startswith(".venv"):
        return "venv"
    return ""


# ── 추적 파일 훑기 ────────────────────────────────────────────

HARD_SKIP = {".git"}
# 이진 파일: NUL 바이트(UTF-16 BOM이 없을 때) 또는 PNG·JPEG·ZIP(whl·docx 포함)·GIF 매직 넘버. 확장자로 고르지 않는다.
BINARY_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"PK\x03\x04", b"PK\x05\x06", b"GIF87a", b"GIF89a")
UTF16_BOMS = ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))


def _decode_text(data: bytes) -> str | None:
    """텍스트면 문자열, 이진이면 None. UTF-16 BOM을 풀고, UTF-8이 아니면 바꿔 읽는다(ASCII 키 모양은 남는다)."""
    for bom, codec in UTF16_BOMS:
        if data.startswith(bom):
            return data[len(bom):].decode(codec, errors="replace")
    if data.startswith(BINARY_MAGIC) or b"\x00" in data:
        return None
    return data.decode("utf-8-sig", errors="replace")


def _tracked_files(root: Path = ROOT) -> list[tuple[str, bytes, str]]:
    """.gitignore가 무시하지 않는 이진이 아닌 모든 파일 (POSIX 상대 경로, 원본 바이트, 해독한 내용)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [d for d in dirnames if d not in HARD_SKIP and not _ignored(rel_dir + d + "/")]
        for name in filenames:
            rel = rel_dir + name
            if _ignored(rel):
                continue
            data = (root / rel).read_bytes()
            text = _decode_text(data)
            if text is not None:
                found.append((rel, data, text))
    return found


TRACKED = _tracked_files()
TRACKED_TEXT = [(rel, text) for rel, _, text in TRACKED]
# 실제 키와 바이트 단위로 비교한다(값은 어디에도 출력하지 않는다). conftest가 환경을 지우기 전에 읽어 둔다.
REAL_DEEPINFRA_KEY = os.environ.get("DEEPINFRA_API_KEY", "").strip()


def test_scan_sees_the_repo():
    names = {rel for rel, _ in TRACKED_TEXT}
    assert {"AGENTS.md", "scripts/kb.py", "tests/test_repo_tree.py", "config.local.example.yaml"} <= names
    assert not any(rel.startswith((".venv", "wheels/", ".state/")) for rel in names)
    assert "config.local.yaml" not in names


# ── 비밀값 ───────────────────────────────────────────────────

BEARER = re.compile(r"Bearer\s+([A-Za-z0-9+/=_.~-]{24,})")
LONG_ALNUM = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{32,}(?![A-Za-z0-9])")
HEX = re.compile(r"[0-9a-fA-F]+")
ALNUM_ALLOWED = {"0123456789ABCDEFGHJKMNPQRSTVWXYZ"}  # outbox id의 Crockford base32 글자표
API_KEY_LINE = re.compile(r"""["']?\bapi_key["']?\s*:\s*["']?([^"'\s,#}]*)""")
REF_PREFIXES = ("keyring:", "env:")


FIXTURE_ALNUM_ALLOWED: set[str] = set()  # fixture에 있는 알려진 가짜 값(키 모양이면 여기에 명시)


def _secret_hits(rel: str, text: str) -> list[str]:
    hits = [f"Bearer {m.group(1)[:6]}…" for m in BEARER.finditer(text)]
    allowed = ALNUM_ALLOWED | (FIXTURE_ALNUM_ALLOWED if rel.startswith("tests/fixtures/") else set())
    for m in LONG_ALNUM.finditer(text):
        run = m.group(0)
        if run in allowed or HEX.fullmatch(run):
            continue
        if re.search(r"\d", run) and re.search(r"[A-Za-z]", run):
            hits.append(f"키 모양 {run[:6]}…({len(run)}자)")
    return hits


def _real_key_parts(key: str) -> list[str]:
    """실제 키와, 대시로 나눈 16자 이상 조각(앞뒤를 바꿔 붙여도 잡히게)."""
    if not key:
        return []
    return [key] + [part for part in re.split(r"[-_]", key) if len(part) >= 16 and part != key]


def _real_key_hits(data: bytes, text: str, key: str) -> list[str]:
    """원본 바이트와 해독한 내용(UTF-16 등)에 실제 키가 있는가. 값은 돌려주지 않는다."""
    for part in _real_key_parts(key):
        if part.encode("utf-8") in data or part in text:
            return ["DEEPINFRA_API_KEY 값(또는 그 조각)이 들어 있음"]
    return []


def _scan_problems(files: list[tuple[str, bytes, str]], key: str = "") -> list[str]:
    problems = []
    for rel, data, text in files:
        problems += [f"{rel}: {hit}" for hit in _secret_hits(rel, text)]
        problems += [f"{rel}: {hit}" for hit in _real_key_hits(data, text, key)]
        shipped = not (rel.startswith("tests/test_") or rel.startswith("tests/fixtures/"))
        if shipped:  # 테스트 파일은 비밀값 거부를 검사하려고 가짜 값을 일부러 쓴다
            problems += [f"{rel}: {hit}" for hit in _api_key_hits(text)]
    return problems


def _api_key_hits(text: str) -> list[str]:
    hits = []
    for m in API_KEY_LINE.finditer(text):
        value = m.group(1)
        if value and not value.startswith(REF_PREFIXES) and value not in {"null", "~", "''", '""'}:
            hits.append(f"api_key: {value[:6]}…")
    return hits


def test_secret_detectors_self_check():
    fake = "Kq7" * 11  # 33자(16진수가 아님), 소스에는 이어진 형태로 남지 않는다
    assert _secret_hits("x.md", "Authorization: Bearer " + fake)
    assert _secret_hits("x.md", "DEEPINFRA_API_KEY=" + fake)
    assert _secret_hits("tests/fixtures/x.md", "k " + fake)  # fixture도 훑는다(허용 목록만 예외)
    assert not _secret_hits("x.md", "Bearer PAT · Bearer x · Bearer sk-test-secret-123456")
    assert not _secret_hits("x.md", "sha256 " + "0a" * 32 + " " + "0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert _api_key_hits("    api_key: " + fake)
    assert _api_key_hits('{"api_key": "' + fake + '"}')
    assert not _api_key_hits("api_key_ref: keyring:work-kb/llm\napi_key: env:X\napi_key: ''\n")


def test_decode_text_detects_binary_and_utf16():
    assert _decode_text(b"\x89PNG\r\n\x1a\n" + b"abc") is None
    assert _decode_text(b"\xff\xd8\xff\xe0" + b"abc") is None
    assert _decode_text(b"PK\x03\x04" + b"abc") is None
    assert _decode_text(b"abc\x00def") is None
    assert _decode_text(b"\xff\xfe" + "키 값".encode("utf-16-le")) == "키 값"
    assert _decode_text(b"\xfe\xff" + "키 값".encode("utf-16-be")) == "키 값"
    assert _decode_text("한글".encode("cp949")) is not None  # UTF-8이 아니어도 텍스트로 훑는다


def _plant_tree(root: Path, key: str) -> None:
    """심어 둔 비밀값: 전에 놓치던 자리(fixture, .bat, UTF-16 문서, 확장자 없는 파일)."""
    files = {
        "tests/fixtures/llm/PLANT2.json": json.dumps({"auth": key}).encode("utf-8"),
        "scripts/PLANT3.bat": f"set DEEPINFRA_API_KEY={key}\r\n".encode("utf-8"),
        "docs/PLANT4.md": b"\xff\xfe" + f"키: {key}\n".encode("utf-16-le"),
        "tests/fixtures/PLANT6.md": f"값 {key}\n".encode("utf-8"),
        "scripts/PLANT7": f"KEY={key}\n".encode("utf-8"),
        "examples/PLANT8.png": b"\x89PNG\r\n\x1a\n" + key.encode("utf-8"),  # 이진은 훑지 않는다
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def test_scan_catches_planted_secrets(tmp_path):
    fake = "-".join(["Zq", "Xw", "Kq7" * 10 + "Ab"])  # 2-2-32 영숫자, 진짜 키와 같은 모양
    _plant_tree(tmp_path, fake)
    files = _tracked_files(tmp_path)
    names = {rel for rel, _, _ in files}
    assert "examples/PLANT8.png" not in names
    problems = _scan_problems(files)
    for rel in ("tests/fixtures/llm/PLANT2.json", "scripts/PLANT3.bat", "docs/PLANT4.md",
                "tests/fixtures/PLANT6.md", "scripts/PLANT7"):
        assert any(p.startswith(rel + ": 키 모양") for p in problems), (rel, problems)


def test_scan_compares_real_key_without_printing(tmp_path):
    # 키 모양 규칙에 걸리지 않는 값(짧은 조각·대시)이라도 실제 키와 같으면 잡는다.
    key = "ab-cd-" + "q" * 10 + "-" + "w" * 18
    path = tmp_path / "notes" / "x.cfg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe" + ("k=" + "w" * 18).encode("utf-16-le"))
    problems = _scan_problems(_tracked_files(tmp_path), key)
    assert problems == ["notes/x.cfg: DEEPINFRA_API_KEY 값(또는 그 조각)이 들어 있음"]
    assert not any(key in p or "w" * 18 in p for p in problems)
    assert _scan_problems(_tracked_files(tmp_path), "") == []


def test_no_secrets_in_tracked_files():
    problems = _scan_problems(TRACKED, REAL_DEEPINFRA_KEY)
    assert not problems, "\n".join(problems)


SECRET_KEYS = {"api_key", "pat", "token", "password", "secret", "client_secret", "access_token"}


def _walk_yaml(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_yaml(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_yaml(value, f"{path}[{i}]")
    else:
        yield path, node


def _config_files() -> list[Path]:
    files = [ROOT / "config.yaml", ROOT / "config.local.example.yaml"]
    files += sorted((ROOT / "examples" / "sample-vault").glob("config*.yaml"))
    local = ROOT / "config.local.yaml"  # 추적하지 않지만 토큰 원문이 들어가면 안 된다(§10)
    if local.is_file():
        files.append(local)
    return files


@pytest.mark.parametrize("path", _config_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_config_files_hold_references_only(path):
    data = yaml.safe_load(path.read_bytes().decode("utf-8-sig")) or {}
    for key, value in _walk_yaml(data):
        leaf = key.rsplit(".", 1)[-1].split("[", 1)[0].lower()
        if leaf in SECRET_KEYS:
            assert value in (None, ""), f"{key}: 비밀값은 *_ref 참조로만"
        if leaf.endswith("_ref") and value not in (None, ""):
            assert str(value).startswith(REF_PREFIXES), f"{key}={value!r}: keyring:… 또는 env:…만"


# ── URL ──────────────────────────────────────────────────────

URL = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9.\-]*)(\S*)")
# XML 이름공간(접속하지 않는다): Confluence storage format, 작업 스케줄러 XML(D58)
NAMESPACE_URLS = re.compile(
    r"http://atlassian\.com/(content|resource/identifier|template)\b"
    r"|http://schemas\.microsoft\.com/windows/2004/02/mit/task\b"
)
DEEPINFRA_OK = {"config.local.example.yaml", "tests/test_live.py"}


def _url_scope(rel: str) -> bool:
    """fixture와 배포되는 코드·설정. 테스트 모듈(test_*.py)은 공개 호스트 거부를 검사하려고 일부러 쓰므로 뺀다."""
    if rel in DEEPINFRA_OK:
        return True
    if rel.startswith(("tests/fixtures/", "examples/", "scripts/", "nvim/", "prompts/", "obsidian/")):
        return True
    if rel.startswith("tests/") and not PurePosixPath(rel).name.startswith("test_"):
        return True  # mock_*.py, conftest.py, fake_keyring.py, sitecustomize.py
    return rel in {"AGENTS.md", "CLAUDE.md", "config.yaml", "jira/_views/일감.base", "wiki/엔티티.base"}


def _url_problems(rel: str, text: str) -> list[str]:
    problems = []
    for m in URL.finditer(text):
        host = m.group(1).rstrip(".").lower()
        if host == "127.0.0.1" or host == "invalid" or host.endswith(".invalid"):
            continue
        if host == "api.deepinfra.com" and rel in DEEPINFRA_OK:
            continue
        if NAMESPACE_URLS.match(m.group(0)):
            continue
        problems.append(f"{rel}: {m.group(0)[:60]}")
    return problems


def test_url_rule_self_check():
    assert not _url_problems("tests/fixtures/x.json", "http://wiki.example.invalid/a http://127.0.0.1:8081/x")
    assert _url_problems("tests/fixtures/x.json", "https://jira.example.com/browse/KB-1")
    assert _url_problems("scripts/x.py", "https://api.deepinfra.com/v1/openai")
    assert not _url_problems("config.local.example.yaml", "https://api.deepinfra.com/v1/openai")
    assert not _url_problems("scripts/x.py", "http://atlassian.com/content 와 https://로")
    assert not _url_problems("scripts/x.py", 'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"')
    assert _url_problems("scripts/x.py", "http://schemas.microsoft.com/other")
    assert _url_scope("tests/mock_jira.py") and not _url_scope("tests/test_config.py")


def test_fixture_and_code_urls_are_placeholders():
    problems = []
    for rel, text in TRACKED_TEXT:
        if _url_scope(rel):
            problems += _url_problems(rel, text)
    assert not problems, "\n".join(problems)


# ── config.yaml (구조만) ─────────────────────────────────────

CONNECTION_KEYS = {
    "environment", "base_url", "project_keys", "space_key", "parent_page_id", "pat_ref", "api_key_ref",
    "profiles", "fallback_order", "ca_bundle", "proxy", "proxies", "username", "user",
}


def test_sample_vault_config_is_repo_copy():
    # examples/sample-vault/README.md: "저장소 config.yaml의 복사본". README의 A단계 흐름이 이 파일을 그대로 읽는다.
    sample = (ROOT / "examples" / "sample-vault" / "config.yaml").read_bytes()
    assert sample == (ROOT / "config.yaml").read_bytes(), "config.yaml을 examples/sample-vault/에 다시 복사하세요"


@pytest.mark.parametrize("rel", ["config.yaml", "examples/sample-vault/config.yaml"])
def test_config_yaml_has_no_connection_values(rel):
    text = (ROOT / rel).read_bytes().decode("utf-8")
    data = yaml.safe_load(text)
    keys = {part for path, _ in _walk_yaml(data) for part in re.split(r"[.\[]", path)}
    assert not keys & CONNECTION_KEYS, sorted(keys & CONNECTION_KEYS)
    body = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())  # 주석 설명은 허용
    assert not re.search(r"https?://|keyring:|env:|\b\d{1,3}(\.\d{1,3}){3}\b", body)


# ── AGENTS.md ────────────────────────────────────────────────

AGENTS_TEXT = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
AGENTS_ANCHORS = ["역할", "폴더 소유권", "관리 구역", "금지", "태그", "작업 스키마", "답변 규칙", "명령"]


def _section(text: str, heading: str) -> str:
    m = re.search(rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text)
    return m.group(1) if m else ""


@pytest.mark.parametrize("heading", AGENTS_ANCHORS)
def test_agents_md_anchor_once(heading):
    assert len(re.findall(rf"(?m)^## {re.escape(heading)}[ \t]*$", AGENTS_TEXT)) == 1, heading


def test_agents_md_scope_and_rules():
    from lib import prompts

    assert set(prompts.AGENTS_SECTIONS) <= set(AGENTS_ANCHORS)
    head = AGENTS_TEXT.split("\n## ", 1)[0]
    assert "런타임 편집자" in head and "work-kb-build-brief-v3.md" in head
    assert "회의, 결정, 요청, 이슈, 학습, 아이디어, 개인, 참고, 일정, 리뷰" in _section(AGENTS_TEXT, "태그")
    for folder in ("daily/", "inbox/", "raw/", "jira/", "wiki/", "_trash/", ".state/"):
        assert f"`{folder}`" in _section(AGENTS_TEXT, "폴더 소유권"), folder
    for task in ("T1", "T2", "T3", "T4", "T5", "T6"):
        assert task in _section(AGENTS_TEXT, "작업 스키마"), task
    assert "KB_AGENT=1" in _section(AGENTS_TEXT, "금지")


def _agents_commands() -> tuple[list[tuple[str, str]], set[str]]:
    """(허용 블록의 (명령, 하위 명령), 거부 줄의 명령 집합)."""
    section = _section(AGENTS_TEXT, "명령")
    block = re.search(r"```\n(.*?)```", section, re.S)
    assert block, "## 명령에 명령 블록 없음"
    allowed = []
    for line in block.group(1).splitlines():
        m = re.match(r"python scripts/kb\.py (\S+)(?: (\S+))?", line.strip())
        assert m, line
        allowed.append((m.group(1), m.group(2) or ""))
    refused_line = next((ln for ln in section.splitlines() if "거부되는 명령" in ln), "")
    refused = {token.split()[0] for token in re.findall(r"`([^`]+)`", refused_line.split(":", 1)[-1])}
    return allowed, refused


def test_agents_md_command_list_matches_kb_commands():
    allowed, refused = _agents_commands()
    names = set(kb.COMMANDS) | set(kb.ALIASES)
    for name, sub in allowed:
        assert name in names, f"AGENTS.md의 kb.py {name}: kb.COMMANDS에 없음"
        command = kb.COMMANDS[kb.ALIASES.get(name, name)]
        assert command.agent_ok or sub in command.agent_ok_sub, f"KB_AGENT=1에서 거부되는 명령이 허용 목록에 있음: {name} {sub}"
    listed = {name for name, _ in allowed}
    for name, command in kb.COMMANDS.items():
        if command.agent_ok:
            assert name in listed, f"허용 명령 {name}이 AGENTS.md 블록에 없음"
        else:
            assert name in refused, f"거부 명령 {name}이 AGENTS.md 거부 줄에 없음"
        for sub in command.agent_ok_sub:
            assert (name, sub) in allowed, f"{name} {sub}가 허용 블록에 없음"
    assert "--redetect" not in "".join(f"{n} {s}" for n, s in allowed)
    for token in re.findall(r"kb\.py ([a-z_]+)", AGENTS_TEXT):
        assert token in names, f"AGENTS.md: kb.py {token}는 없는 명령"


# ── nvim/capture.ahk ─────────────────────────────────────────

AHK = ROOT / "nvim" / "capture.ahk"
LUA_INIT = ROOT / PLUGIN_INIT
# capture.ahk는 선택 사항이다. 파일이 없으면 아래 두 검사는 건너뛴다.
pytestmark_ahk = pytest.mark.skipif(not AHK.is_file(), reason="nvim/capture.ahk 없음")


@pytestmark_ahk
def test_capture_ahk_is_autohotkey_v2():
    text = AHK.read_bytes().decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].strip() == "#Requires AutoHotkey v2.0"
    code = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(";")]
    # 설정 변수가 단축키보다 먼저 나온다
    setting = next(i for i, ln in enumerate(code) if re.match(r"\s*Nvim\s*:=\s*\"", ln))
    hotkeys = [i for i, ln in enumerate(code) if "::" in ln]
    assert hotkeys and setting < min(hotkeys)
    # D13: Ctrl+Alt+Shift+C/V → :WorkKb 명령을 nvim 서버로 보낸다
    assert re.search(r'(?m)^\^!\+c::Capture\("WorkKb capture"\)', text)
    assert re.search(r'(?m)^\^!\+v::Capture\("WorkKb capture-clipboard-image"\)', text)
    # nvim-config의 데몬(nvd)을 쓴다. 자체 서버(--remote-send)는 쓰지 않는다
    assert '"nvd-rpc.lua"' in text and '"nvd.cmd"' in text and "--remote-send" not in text
    assert not re.search(r"(?m)^[\^!+]*#[\^!+#]*\w+::", text)  # Win 조합 없음(PowerToys 충돌, D13)
    # v1 문법(명령, 쉼표) 없음
    assert not re.search(r"(?m)^\s*(Run|MsgBox|Send|WinActivate),", text)
    # 실행해 보지 못했다는 표시(이 PC에 AutoHotkey 없음)
    assert "실행해 보지 못했다" in text and "미확인" in text


@pytestmark_ahk
def test_capture_ahk_calls_existing_commands():
    lua = LUA_INIT.read_text(encoding="utf-8")
    names = set(re.findall(r'name = "([a-z-]+)"', lua))
    used = re.findall(r'Capture\("WorkKb ([a-z-]+)"\)', AHK.read_text(encoding="utf-8-sig"))
    assert used, "capture.ahk에 :WorkKb 호출이 없다"
    for name in used:
        assert name in names, f"work-kb init.lua에 명령 {name} 없음"
