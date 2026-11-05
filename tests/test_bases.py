"""Obsidian Bases 파일과 setup.md 점검 (지시서 §6.9, DECISIONS D64).

문법 기준: obsidian-help `en/Bases/Bases syntax.md`·`Functions.md` commit 578f73cc (2026-10-23),
Obsidian 1.15.0. 앱 렌더링은 사용자 확인 항목(D31)이고, 여기서는 YAML 구조와 필터 의미를 본다.
필터 의미는 이 파일들이 쓰는 원자식만 해석하는 작은 평가기로 가짜 파일에 적용해 확인한다.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path, PurePosixPath

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ISSUES_BASE = ROOT / "jira" / "_views" / "일감.base"
ENTITY_BASE = ROOT / "wiki" / "엔티티.base"
SETUP_MD = ROOT / "obsidian" / "setup.md"
BASE_FILES = [ISSUES_BASE, ENTITY_BASE]

TOP_KEYS = {"filters", "formulas", "properties", "summaries", "views"}
VIEW_KEYS = {"type", "name", "limit", "groupBy", "groupOrder", "filters", "order", "sort", "summaries"}
VIEW_TYPES = {"table", "list", "cards", "kanban", "map"}


# ── 공통 도우미 ──────────────────────────────────────────────

def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_bytes().decode("utf-8"))
    assert isinstance(data, dict), path
    return data


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(node, list):
        return [s for item in node for s in _strings(item)]
    return []


def _bare(prop: str) -> str:
    return prop[len("note."):] if prop.startswith("note.") else prop


_IN_FOLDER = re.compile(r'^(!?)file\.inFolder\("([^"]*)"\)$')
_HAS_PROP = re.compile(r'^(!?)file\.hasProperty\("([^"]*)"\)$')
_COMPARE = re.compile(r'^((?:note\.)?[A-Za-z_][A-Za-z0-9_]*)\s*(==|!=)\s*"([^"]*)"$')


def _atom(expr: str, path: str, props: dict) -> bool:
    """이 저장소의 .base가 쓰는 원자식만 해석한다. 모르는 식은 실패로 드러낸다."""
    expr = expr.strip()
    if m := _IN_FOLDER.match(expr):
        folder = m.group(2).strip("/")
        result = PurePosixPath(path).as_posix().startswith(folder + "/")  # 하위 폴더 포함
        return result != bool(m.group(1))
    if m := _HAS_PROP.match(expr):
        return (m.group(2) in props) != bool(m.group(1))
    if m := _COMPARE.match(expr):
        value = props.get(_bare(m.group(1)))
        equal = value is not None and str(value) == m.group(3)
        return equal if m.group(2) == "==" else not equal
    raise AssertionError(f"평가기가 모르는 필터 식: {expr!r}")


def _eval(node, path: str, props: dict) -> bool:
    if node is None:
        return True
    if isinstance(node, str):
        return _atom(node, path, props)
    assert isinstance(node, dict) and len(node) == 1, f"필터 객체는 and/or/not 하나: {node!r}"
    (op, items), = node.items()
    assert op in ("and", "or", "not") and isinstance(items, list), node
    results = [_eval(item, path, props) for item in items]
    if op == "and":
        return all(results)
    if op == "or":
        return any(results)
    return not any(results)


def _visible(base: dict, view: dict, path: str, props: dict) -> bool:
    return _eval(base.get("filters"), path, props) and _eval(view.get("filters"), path, props)


def _view(base: dict, name: str) -> dict:
    found = [v for v in base["views"] if v.get("name") == name]
    assert len(found) == 1, f"뷰 {name!r} 없음: {[v.get('name') for v in base['views']]}"
    return found[0]


def _columns(view: dict) -> set[str]:
    return {_bare(c) for c in view.get("order") or []}


def _sorted_by_updated_desc(view: dict) -> bool:
    return any(_bare(s.get("property", "")) == "updated" and s.get("direction") == "DESC" for s in view.get("sort") or [])


# ── 파일 형식 ────────────────────────────────────────────────

@pytest.mark.parametrize("path", [*BASE_FILES, SETUP_MD], ids=lambda p: p.name)
def test_utf8_without_bom(path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    raw.decode("utf-8")


@pytest.mark.parametrize("path", BASE_FILES, ids=lambda p: p.name)
def test_base_structure(path):
    data = _load(path)
    assert set(data) <= TOP_KEYS, set(data) - TOP_KEYS
    assert "from" not in data  # Bases에는 원천 키가 없다
    for prop, conf in (data.get("properties") or {}).items():
        assert isinstance(conf, dict) and isinstance(conf.get("displayName"), str), prop
    views = data["views"]
    assert isinstance(views, list) and views
    names = [v["name"] for v in views]
    assert len(names) == len(set(names))
    for view in views:
        assert set(view) <= VIEW_KEYS, set(view) - VIEW_KEYS
        assert view["type"] in VIEW_TYPES
        assert view.get("order"), view["name"]
        if "groupBy" in view:
            assert set(view["groupBy"]) == {"property", "direction"}
            assert view["groupBy"]["direction"] in ("ASC", "DESC")
        for item in view.get("sort") or []:
            assert set(item) == {"property", "direction"} and item["direction"] in ("ASC", "DESC")


@pytest.mark.parametrize("path", BASE_FILES, ids=lambda p: p.name)
def test_no_absolute_paths(path):
    data = _load(path)
    for text in _strings(data):
        assert not re.search(r"(^|[\s\"'(])([A-Za-z]:[\\/]|/|\\\\|~[\\/])", text), text
        assert "\\" not in text, text
        for folder in re.findall(r'inFolder\("([^"]*)"\)', text):
            assert folder and not folder.startswith("/") and ":" not in folder, text
    raw = path.read_bytes().decode("utf-8")
    assert not re.search(r"[A-Za-z]:[\\/]", raw)
    assert "work-kb" not in raw


@pytest.mark.parametrize("path", BASE_FILES, ids=lambda p: p.name)
def test_base_files_not_gitignored(path):
    rel = path.relative_to(ROOT).as_posix()
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    patterns = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith(("#", "!"))]
    for pattern in patterns:
        pat = pattern.lstrip("/").rstrip("/")
        parts = rel.split("/")
        prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        assert not any(fnmatch.fnmatchcase(p, pat) for p in prefixes), (pattern, rel)


# ── 일감.base ────────────────────────────────────────────────

ISSUE_FILES = {
    "jira/KB-12.md": ({"key": "KB-12", "project": "KB", "status": "In Progress", "status_category": "indeterminate"}, True),
    "jira/KB-13.md": ({"key": "KB-13", "project": "KB", "status": "To Do", "status_category": "new"}, True),
    "jira/OPS-3.md": ({"key": "OPS-3", "project": "OPS", "status": "Blocked", "status_category": "indeterminate"}, True),
    "jira/KB-1.md": ({"key": "KB-1", "project": "KB", "status": "Done", "status_category": "done"}, False),
    "jira/KB-20.md": ({"key": "KB-20", "project": "KB", "status": "In Progress", "status_category": "indeterminate", "in_scope": False}, False),
    "jira/KB-99.md": ({"key": "KB-99", "stub": True}, False),
    "jira/_views/today.md": ({"view": "today", "jql": "x", "status_category": "new"}, False),
    "jira/_views/일감.base": ({}, False),
    "wiki/projects/KB.md": ({"type": "project", "key": "KB", "status_category": "new"}, False),
    "daily/2026-11-05.md": ({"date": "2026-11-05"}, False),
}


def test_issues_global_filter_atoms():
    data = _load(ISSUES_BASE)
    filters = data["filters"]
    assert set(filters) == {"and"}
    atoms = _strings(filters)
    assert 'file.inFolder("jira")' in atoms
    assert 'file.hasProperty("status_category")' in atoms
    assert 'status_category != "done"' in atoms
    excluded = 'file.inFolder("jira/_views")' in _strings([n["not"] for n in filters["and"] if isinstance(n, dict) and "not" in n]) \
        or '!file.inFolder("jira/_views")' in atoms
    assert excluded


@pytest.mark.parametrize("path", sorted(ISSUE_FILES))
def test_issues_main_view_membership(path):
    data = _load(ISSUES_BASE)
    view = _view(data, "미완료 (프로젝트별)")
    props, expected = ISSUE_FILES[path]
    assert _visible(data, view, path, props) is expected


def test_issues_main_view_columns_group_sort():
    data = _load(ISSUES_BASE)
    view = _view(data, "미완료 (프로젝트별)")
    assert view["type"] == "table"
    cols = _columns(view)
    assert {"summary", "status", "assignee", "due", "updated"} <= cols
    assert "key" in cols or "file.name" in cols  # 파일 이름 = KEY
    assert _bare(view["groupBy"]["property"]) == "project"
    assert _sorted_by_updated_desc(view)
    labels = {_bare(k): v["displayName"] for k, v in data["properties"].items()}
    assert labels["summary"] == "제목" and labels["status"] == "상태" and labels["updated"] == "갱신"


def test_issues_blocked_view():
    data = _load(ISSUES_BASE)
    view = _view(data, "막힘")
    assert 'status == "Blocked"' in _strings(view["filters"])
    shown = {p for p, (props, _) in ISSUE_FILES.items() if _visible(data, view, p, props)}
    assert shown == {"jira/OPS-3.md"}
    done_blocked = {"key": "KB-2", "status": "Blocked", "status_category": "done"}
    assert not _visible(data, view, "jira/KB-2.md", done_blocked)
    assert _sorted_by_updated_desc(view)


# ── 엔티티.base ──────────────────────────────────────────────

ENTITY_FILES = {
    "wiki/projects/KB.md": ({"type": "project", "key": "KB", "title": "지식 저장고", "updated": "2026-11-05"}, True),
    "wiki/people/김철수.md": ({"type": "person", "name": "김철수", "updated": "2026-11-05"}, True),
    "wiki/topics/캐시.md": ({"type": "topic", "name": "캐시", "aliases": [], "updated": "2026-11-04"}, True),
    "wiki/index.md": ({}, False),
    "wiki/decisions.md": ({"type": "decisions"}, False),
    "wiki/projects/메모.md": ({}, False),
    "jira/KB-12.md": ({"key": "KB-12", "status_category": "new"}, False),
    "examples/sample-vault/wiki/projects/KB.md": ({"type": "project"}, False),
}


def test_entity_filter_atoms():
    atoms = _strings(_load(ENTITY_BASE)["filters"])
    for folder in ("wiki/projects", "wiki/people", "wiki/topics"):
        assert f'file.inFolder("{folder}")' in atoms
    assert 'file.hasProperty("type")' in atoms


@pytest.mark.parametrize("path", sorted(ENTITY_FILES))
def test_entity_main_view_membership(path):
    data = _load(ENTITY_BASE)
    view = _view(data, "전체 (종류별)")
    props, expected = ENTITY_FILES[path]
    assert _visible(data, view, path, props) is expected


def test_entity_views_columns():
    data = _load(ENTITY_BASE)
    main = _view(data, "전체 (종류별)")
    assert {"type", "title", "name", "updated"} <= _columns(main)
    assert _bare(main["groupBy"]["property"]) == "type"
    assert _sorted_by_updated_desc(main)
    for name, kind, label in (("프로젝트", "project", "title"), ("사람", "person", "name"), ("주제", "topic", "name")):
        view = _view(data, name)
        assert {label, "updated"} <= _columns(view)
        shown = {p for p, (props, _) in ENTITY_FILES.items() if _visible(data, view, p, props)}
        assert shown and all(ENTITY_FILES[p][0]["type"] == kind for p in shown), (name, shown)


# ── setup.md (§6.9 체크리스트) ───────────────────────────────

def test_setup_md_checklist():
    text = SETUP_MD.read_text(encoding="utf-8")
    assert "볼트 루트 = 저장소 루트" in text
    assert "Bases" in text and "Daily notes" in text
    assert "`daily`" in text and "`YYYY-MM-DD`" in text
    assert "Use [[Wikilinks]]" in text and "Absolute path in vault" in text
    assert "`raw/assets`" in text
    assert "`inbox/`" in text and "kb.py ingest" in text
    assert ".obsidian/" in text
    for name in ("jira/_views/일감.base", "wiki/엔티티.base"):
        assert name in text
    block = re.search(r"제외할 폴더:\s*```\n(.*?)```", text, re.S)
    assert block, "제외 폴더 블록 없음"
    excluded = {ln.strip().rstrip("/") for ln in block.group(1).splitlines() if ln.strip()}
    assert {"examples", "tests", "scripts", "nvim", "docs", "prompts", "obsidian", "_trash", "wheels"} <= excluded
    assert any(e.startswith(".venv") for e in excluded)
    assert not {"daily", "inbox", "raw", "jira", "wiki"} & excluded


def test_setup_md_single_dataview_block():
    text = SETUP_MD.read_text(encoding="utf-8")
    assert len(re.findall(r"^```dataview\s*$", text, re.M)) == 1
    assert "```dataviewjs" not in text
    assert not re.search(r"[A-Za-z]:[\\/]", text)
