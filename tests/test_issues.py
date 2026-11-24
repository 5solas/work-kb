"""kb.py issues: jira/*.md 캐시 목록(JSON·표). 파일만 읽고 아무것도 쓰지 않는다."""
from __future__ import annotations

import datetime as dt
import json

import issues
import jira_sync
import pytest
import serve
from lib import core

STAMP = dt.datetime(2026, 11, 25, 9, 10, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def _write(vault, key, **over):
    """jira_sync가 쓰는 형식 그대로 일감 파일을 만든다."""
    values = dict(
        key=key, project=key.split("-")[0], project_name="", summary=f"{key} 제목", status="할 일", status_category="new",
        issuetype="Task", priority="High", assignee="김철수", reporter="홍길동", sprint="S1", labels=["a"], components=[],
        created="2026-11-01T09:00:00+09:00", updated="2026-11-20T09:00:00+09:00", due="2026-11-28",
        url=f"https://jira.example.com/browse/{key}", description="", comment_lines=[], comment_text="",
    )
    values.update(over)
    path = vault / "jira" / f"{key}.md"
    path.write_text(jira_sync.render_doc(jira_sync.IssueDoc(**values), STAMP), encoding="utf-8", newline="\n")
    return path


def _raw(vault, key, text):
    (vault / "jira" / f"{key}.md").write_text(text, encoding="utf-8", newline="\n")


def _view(vault, name, keys):
    rows = "".join(f"| [[{k}]] | {k} 표 제목 | 표 상태 | 표 담당 | 2026-12-01 | 2026-11-19 |\n" for k in keys)
    head = f"---\nview: {name}\n---\n# {name}\n\n| 키 | 제목 | 상태 | 담당 | 마감 | 갱신 |\n|---|---|---|---|---|---|\n"
    (vault / "jira" / "_views" / f"{name}.md").write_text(head + rows, encoding="utf-8", newline="\n")


def _json(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _keys(data):
    return [i["key"] for i in data["issues"]]


def test_json_schema_and_fields(vault, run_kb):
    _write(vault, "KB-12", status="진행 중", status_category="indeterminate")
    _write(vault, "KB-2", status_category="done")
    out = _json(run_kb("issues", "--json"))
    assert set(out) == {"synced_at", "views", "issues"}
    assert _keys(out) == ["KB-2", "KB-12"]
    row = out["issues"][1]
    assert set(row) == set(issues.FIELDS)
    assert row["labels"] == ["a"] and row["components"] == []
    assert (row["status"], row["priority"], row["assignee"], row["due"]) == ("진행 중", "High", "김철수", "2026-11-28")


def test_skips_stub_views_and_out_of_scope(vault, run_kb):
    _write(vault, "KB-1")
    _write(vault, "KB-2", in_scope=False)
    _raw(vault, "KB-3", "---\nkey: KB-3\nstub: true\n---\n")
    _raw(vault, "메모", "---\nstatus_category: new\n---\n")
    _view(vault, "today", ["KB-1"])
    out = _json(run_kb("issues", "--json"))
    assert _keys(out) == ["KB-1"]
    assert out["views"] == ["today"]


def test_all_includes_out_of_scope_with_flag(vault, run_kb):
    _write(vault, "KB-1")
    _write(vault, "KB-2", in_scope=False)
    rows = _json(run_kb("issues", "--json", "--all"))["issues"]
    assert "in_scope" not in rows[0]
    assert rows[1]["key"] == "KB-2" and rows[1]["in_scope"] is False


def test_view_order_and_uncached_rows(vault, run_kb):
    _write(vault, "KB-12")
    _write(vault, "KB-7", in_scope=False)
    _write(vault, "KB-1")
    _raw(vault, "KB-3", "---\nkey: KB-3\nstub: true\n---\n")
    _view(vault, "today", ["KB-12", "ZZ-9", "KB-3", "KB-7"])
    rows = _json(run_kb("issues", "--json", "--view", "today"))["issues"]
    assert [r["key"] for r in rows] == ["KB-12", "ZZ-9", "KB-3", "KB-7"]
    assert "cached" not in rows[0]
    for row in rows[1:3]:
        assert row["cached"] is False
        assert row["summary"] == f"{row['key']} 표 제목"
        assert (row["status"], row["assignee"], row["due"], row["updated"]) == ("표 상태", "표 담당", "2026-12-01", "2026-11-19")
        assert (row["priority"], row["status_category"], row["labels"]) == ("", "", [])
    assert rows[3]["in_scope"] is False and rows[3]["priority"] == "High"


@pytest.mark.parametrize("name", ["nope", "../KB-1"])
def test_unknown_view_exits_1_and_lists_names(vault, run_kb, name):
    _write(vault, "KB-1")
    _view(vault, "today", [])
    result = run_kb("issues", "--json", f"--view={name}")
    assert result.returncode == 1
    assert "today" in result.stderr
    assert "Traceback" not in result.stderr


def test_broken_front_matter_is_skipped(vault, run_kb):
    _write(vault, "KB-1")
    _raw(vault, "KB-9", "---\nkey: [깨짐\n---\n")
    result = run_kb("issues", "--json")
    assert _keys(_json(result)) == ["KB-1"]
    assert "jira/KB-9.md" in result.stderr


def test_yaml_typed_values_become_strings(vault, run_kb):
    _raw(vault, "KB-1", "---\nkey: KB-1\nstatus_category: new\ndue: 2026-11-28\nsummary: 123\nassignee:\nlabels: a\n---\n")
    row = _json(run_kb("issues", "--json"))["issues"][0]
    assert (row["due"], row["summary"], row["labels"]) == ("2026-11-28", "123", ["a"])
    assert row["assignee"] == ""


def test_synced_at_is_last_sync_or_null(vault, run_kb):
    assert _json(run_kb("issues", "--json"))["synced_at"] is None
    core.save_state(vault, "sync", {"last_sync": "2026-11-25T09:10:00+09:00", "last_run": "2026-11-25T10:00:00+09:00"})
    assert _json(run_kb("issues", "--json"))["synced_at"] == "2026-11-25T09:10:00+09:00"


def test_broken_sync_state_still_lists(vault, run_kb):
    _write(vault, "KB-1")
    (vault / ".state" / "sync.json").write_text("{깨짐", encoding="utf-8")
    out = _json(run_kb("issues", "--json"))
    assert _keys(out) == ["KB-1"] and out["synced_at"] is None


def test_empty_vault(vault, run_kb):
    (vault / "jira" / "_views").rmdir()
    (vault / "jira").rmdir()
    assert _json(run_kb("issues", "--json")) == {"synced_at": None, "views": [], "issues": []}


def test_plain_table_output(vault, run_kb):
    _write(vault, "KB-1", status_category="done", status="완료")
    result = run_kb("issues")
    assert result.returncode == 0
    (line,) = [x for x in result.stdout.splitlines() if x.startswith("KB-1")]
    for part in ("완료", "KB-1 제목", "김철수", "2026-11-28"):
        assert part in line


def test_allowed_under_kb_agent(vault, run_kb):
    assert run_kb("issues", "--json", env={"KB_AGENT": "1"}).returncode == 0


def test_writes_nothing(vault, run_kb):
    _write(vault, "KB-1")
    _view(vault, "today", ["KB-1"])

    def snapshot():
        return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in vault.rglob("*") if p.is_file()}

    before = snapshot()
    _json(run_kb("issues", "--json", "--view", "today"))
    assert snapshot() == before


def test_served(vault, monkeypatch):
    _write(vault, "KB-1")
    assert serve.SERVED["issues"] is None
    code, out = serve.run_argv(["issues", "--root", str(vault), "--json"])
    assert code == 0 and _keys(json.loads(out)) == ["KB-1"]
