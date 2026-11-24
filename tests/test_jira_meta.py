"""kb.py meta: 새 일감의 생성 정보(createmeta)와 담당 후보. Jira 읽기만 한다."""
from __future__ import annotations

import json

import pytest

import jira_meta
import mock_jira
from lib import config, core, net
from lib import secrets as kb_secrets

NOW = "2026-11-25T09:00:00+09:00"


@pytest.fixture
def make(vault, fake_now, monkeypatch):
    fake_now(NOW)
    monkeypatch.setattr(net, "_sleep", lambda seconds: None)
    servers = []

    def _make(**modes):
        server = mock_jira.start(**modes)
        servers.append(server)
        kb_secrets.store("work-kb", "jira", "meta-test-token-1234")
        section = {"base_url": server.base_url, "project_keys": ["KB", "OPS"], "pat_ref": "keyring:work-kb/jira", "trust_env": False}
        config.save_local(vault, {"environment": "home", "jira": section})
        return server

    yield _make
    for server in servers:
        server.stop()


def run(capsys, vault, *args):
    try:
        code = jira_meta.main([*args, "--root", str(vault)])
    except core.KbError as exc:
        return exc.exit_code, None, str(exc)
    out, err = capsys.readouterr()
    return code, json.loads(out.strip().splitlines()[-1]), err


def gets(server):
    return [r for r in server.log if "createmeta" in r["path"]]


def _type(data, name):
    return next(t for t in data["issuetypes"] if t["name"] == name)


def _field(data, type_name, fid):
    return next(f for f in _type(data, type_name)["fields"] if f["id"] == fid)


@pytest.mark.parametrize("mode", ["new", "old"])
def test_create_meta_fields_and_kinds(make, vault, capsys, mode):
    make(createmeta=mode)
    code, data, _ = run(capsys, vault, "create", "kb", "--json")
    assert code == 0
    assert data["project"] == "KB" and data["stale"] is False and data["fallback"] is False
    assert [t["name"] for t in data["issuetypes"]] == ["Task", "Story", "Bug"]
    kinds = {f["id"]: f["kind"] for f in _type(data, "Task")["fields"]}
    assert kinds == {
        "priority": "option", "assignee": "user", "components": "options", "fixVersions": "options", "labels": "labels",
        "duedate": "date", "customfield_10001": "epic", "customfield_10002": "unsupported", "customfield_10003": "number",
        "customfield_10004": "option", "customfield_10005": "unsupported", "customfield_10006": "datetime",
        "customfield_10007": "users", "customfield_10008": "textarea", "customfield_10009": "text", "environment": "textarea",
    }  # fmt: skip
    priority = _field(data, "Task", "priority")
    assert priority["required"] is True and priority["has_default"] is True
    assert priority["allowed"] == [{"id": "2", "name": "High"}, {"id": "3", "name": "Medium"}]
    assert _field(data, "Task", "customfield_10004")["allowed"][0] == {"id": "501", "name": "운영"}
    assert _type(data, "Task")["description"] == {"available": True, "required": False}
    assert _type(data, "Bug")["description"] == {"available": True, "required": True}


def test_new_endpoint_follows_pages(make, vault, capsys):
    server = make(createmeta="new", page_size_cap=2)
    code, data, _ = run(capsys, vault, "create", "KB", "--json")
    assert code == 0 and len(data["issuetypes"]) == 3 and len(_type(data, "Task")["fields"]) == 16
    assert any(r["query"].get("startAt") == "2" for r in gets(server))


def test_cache_is_used_then_stale_then_refreshed(make, vault, capsys, fake_now):
    server = make()
    run(capsys, vault, "create", "KB", "--json")
    n = len(gets(server))
    _, data, _ = run(capsys, vault, "create", "KB", "--json")
    assert len(gets(server)) == n and data["stale"] is False
    fake_now("2026-11-26T10:00:00+09:00")
    _, data, _ = run(capsys, vault, "create", "KB", "--json")
    assert len(gets(server)) == n and data["stale"] is True
    _, data, _ = run(capsys, vault, "create", "KB", "--json", "--refresh")
    assert len(gets(server)) > n and data["stale"] is False


def test_failed_refresh_keeps_good_cache(make, vault, capsys):
    server = make()
    run(capsys, vault, "create", "KB", "--json")
    path = vault / ".state" / "createmeta-KB.json"
    before = path.read_bytes()
    server.go_down()
    code, data, _ = run(capsys, vault, "create", "KB", "--json", "--refresh")
    assert code == 0 and data["stale"] is True and data["fallback"] is False and data["error"]
    assert len(data["issuetypes"]) == 3 and path.read_bytes() == before


def test_offline_without_cache_falls_back(make, vault, capsys):
    server = make()
    server.go_down()
    code, data, _ = run(capsys, vault, "create", "KB", "--json")
    assert code == 0 and data["fallback"] is True and data["error"]
    assert [t["name"] for t in data["issuetypes"]] == ["Task", "Bug", "Story"]
    assert all(t["fields"] == [] for t in data["issuetypes"])
    assert not (vault / ".state" / "createmeta-KB.json").exists()


@pytest.mark.parametrize("bad", ["{깨짐", "[]", '{"base_url": "http://127.0.0.1:1", "issuetypes": []}', '{"issuetypes": 3}'])
def test_broken_or_foreign_cache_is_refetched(make, vault, capsys, bad):
    make()
    (vault / ".state" / "createmeta-KB.json").write_text(bad, encoding="utf-8")
    code, data, _ = run(capsys, vault, "create", "KB", "--json")
    assert code == 0 and len(data["issuetypes"]) == 3 and data["stale"] is False


def test_both_404_reports_project_not_guess(make, vault, capsys):
    make(createmeta="none")
    code, data, err = run(capsys, vault, "create", "OPS", "--json")
    assert code == 1 and data is None and "OPS" in err


def test_project_outside_keys_refused(make, vault, capsys):
    server = make()
    code, _, err = run(capsys, vault, "create", "ZZ", "--json")
    assert code == 4 and "project_keys" in err and gets(server) == []


def test_users_by_project_and_issue(make, vault, capsys):
    server = make()
    code, data, _ = run(capsys, vault, "users", "--project", "KB", "--query", "철수", "--json")
    assert code == 0
    assert data == {"me": {"name": "honggildong", "display": "홍길동"}, "users": [{"name": "kimcs", "display": "김철수"}]}
    run(capsys, vault, "users", "--issue", "KB-12", "--json")
    queries = [r["query"] for r in server.log if r["path"].endswith("/user/assignable/search")]
    assert queries[0]["project"] == "KB" and queries[0]["username"] == "철수" and "issueKey" not in queries[0]
    assert queries[1]["issueKey"] == "KB-12" and "project" not in queries[1]
    assert not any("kimcs" in p.read_text(encoding="utf-8") for p in (vault / ".state").rglob("*") if p.is_file())


def test_users_me_failure_still_lists(make, vault, capsys):
    make(myself_status=503)
    code, data, _ = run(capsys, vault, "users", "--project", "KB", "--json")
    assert code == 0 and data["me"] is None and len(data["users"]) == 2


def test_users_needs_one_scope(make, vault, capsys):
    make()
    for args in ((), ("--project", "KB", "--issue", "KB-1")):
        with pytest.raises((SystemExit, core.KbError)):
            jira_meta.main(["users", *args, "--json", "--root", str(vault)])


def test_allowed_under_agent(make, vault, run_kb):
    make()
    result = run_kb("meta", "create", "ZZ", "--json", env={"KB_AGENT": "1"})
    assert result.returncode == 4
