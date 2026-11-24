"""Jira 일감 캐시 목록 (`kb.py issues`).

`jira/*.md`의 front matter만 읽는다. 네트워크·비밀값 저장소를 쓰지 않고 아무것도 쓰지 않는다.
스텁(stub, status_category 없음)과 `jira/_views/`는 빼고, `in_scope: false`는 `--all`일 때만 넣는다.
`--view NAME`은 `jira/_views/NAME.md` 표의 키를 그 순서로 낸다. 일감 파일이 없거나 스텁인 키는 표의 값만
채우고 `cached: false`를 붙인다. front matter가 깨진 파일은 건너뛰고 stderr에 한 줄 남긴다.
`--json`은 JSON 객체 하나를 한 줄로 낸다: {synced_at, views, view_jql(config.yaml의 뷰 이름 → JQL), issues}.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from typing import Any

from lib import config, core, frontmatter, notes
from lib.config import Config

FIELDS = (
    "key", "project", "summary", "status", "status_category", "issuetype", "priority",
    "assignee", "sprint", "labels", "components", "updated", "due", "url",
)
LIST_FIELDS = frozenset({"labels", "components"})
VIEW_FIELDS = {"summary": "제목", "status": "상태", "assignee": "담당", "due": "마감", "updated": "갱신"}
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-[1-9][0-9]*$")  # jira_sync.KEY_RE와 같다(requests를 가져오지 않으려고 따로 둔다)


def _false(value: Any) -> bool:
    return str(value).strip().lower() == "false"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_text(v) for v in value if _text(v)]
    return [_text(value)] if _text(value) else []


def _row(key: str, meta: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {name: _values(meta.get(name)) if name in LIST_FIELDS else _text(meta.get(name)) for name in FIELDS}
    row["key"] = key
    row["project"] = row["project"] or key.rpartition("-")[0]
    if _false(meta.get("in_scope", "")):
        row["in_scope"] = False
    return row


def _sort_key(row: Mapping[str, Any]) -> tuple[str, int]:
    project, _, number = row["key"].rpartition("-")
    return project, int(number)


def read_issues(cfg: Config, *, include_out_of_scope: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    """(키 순 행, 건너뛴 파일 설명)."""
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    base = cfg.path("jira")
    for path in base.glob("*.md") if base.is_dir() else ():
        if not core.is_scannable(path) or not KEY_RE.match(path.stem):
            continue
        try:
            meta = frontmatter.parse(path.read_bytes())
        except (OSError, core.KbError) as exc:
            problems.append(f"jira/{path.name}: {exc}")
            continue
        if str(meta.get("stub", "")).strip().lower() == "true" or not meta.get("status_category"):
            continue
        row = _row(path.stem, meta)
        if include_out_of_scope or "in_scope" not in row:
            rows.append(row)
    return sorted(rows, key=_sort_key), sorted(problems)


def view_names(cfg: Config) -> list[str]:
    base = cfg.path("jira", "_views")
    return sorted(p.stem for p in base.glob("*.md") if core.is_scannable(p)) if base.is_dir() else []


def _view_rows(cfg: Config, name: str, cached: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cells in notes.read_view(cfg, name):
        key = cells["key"]
        if not KEY_RE.match(key):
            continue
        if key in cached:
            rows.append(cached[key])
            continue
        row = _row(key, {field: cells.get(column, "") for field, column in VIEW_FIELDS.items()})
        row["cached"] = False
        rows.append(row)
    return rows


def _synced_at(cfg: Config, problems: list[str]) -> str | None:
    try:
        state = core.load_state(cfg.root, "sync")
    except (core.KbError, OSError, UnicodeDecodeError) as exc:
        problems.append(str(exc))
        return None
    value = state.get("last_sync") if isinstance(state, Mapping) else None
    return str(value) if value else None


def _collect(cfg: Config, all_: bool, view: str | None) -> tuple[dict[str, Any], list[str]]:
    views = view_names(cfg)
    if view is not None and view not in views:
        raise core.KbError(f"없는 뷰: {view} (있는 뷰: {', '.join(views) or '없음'})")
    rows, problems = read_issues(cfg, include_out_of_scope=all_ or view is not None)
    if view is not None:
        rows = _view_rows(cfg, view, {row["key"]: row for row in rows})
    configured = cfg.get("jira.views")
    jql = {str(k): v.strip() for k, v in configured.items() if isinstance(v, str) and v.strip()} if isinstance(configured, Mapping) else {}
    return {"synced_at": _synced_at(cfg, problems), "views": views, "view_jql": jql, "issues": rows}, problems


def collect(cfg: Config, *, all_: bool = False, view: str | None = None) -> dict[str, Any]:
    return _collect(cfg, all_, view)[0]


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py issues", "Jira 일감 캐시 목록 (네트워크를 쓰지 않음)")
    parser.add_argument("--json", action="store_true", help="JSON 객체로 출력")
    parser.add_argument("--all", action="store_true", help="범위 밖 일감도 포함")
    parser.add_argument("--view", default=None, metavar="NAME", help="jira/_views/NAME.md의 일감만, 그 순서로")
    args = parser.parse_args(argv)
    data, problems = _collect(config.load(args.root), args.all, args.view)
    for problem in problems:
        print(core.redact(problem), file=sys.stderr)
    if args.json:
        print(core.redact(json.dumps(data, ensure_ascii=False)))
    else:
        for row in data["issues"]:
            print(core.redact("  ".join(row[name] or "-" for name in ("key", "status", "summary", "assignee", "due"))))
    return 0


if __name__ == "__main__":
    core.run_main(main)
