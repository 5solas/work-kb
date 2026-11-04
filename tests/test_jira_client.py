"""lib/jira_client.py와 tests/mock_jira.py(Phase 2 읽기) 시험."""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest
import yaml

import mock_jira
from lib import config, core, net
from lib import secrets as kb_secrets
from lib.jira_client import (
    ISSUE_FIELDS,
    JiraClient,
    JqlError,
    NotFoundError,
    parse_datetime,
    parse_sprint,
    split_order_by,
    with_default_order,
)

TOKEN = "pat-for-tests"
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "jira"
JIRA_CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["jira"]  # 저장소 config.yaml만(local 제외)
ALL_KEYS_ASC = [
    "KB-3", "KB-5", "KB-7", "KB-9", "KB-10", "KB-11", "KB-12", "KB-14", "KB-15",
    "OPS-1", "OPS-2", "OPS-4", "OPS-7", "OPS-8", "OPS-10",
]  # fmt: skip
SP = "com.atlassian.greenhopper.service.sprint.Sprint"
KB6 = f"{SP}@3f2a1b[id=4,rapidViewId=2,state=CLOSED,name=KB 스프린트 6,startDate=2026-10-13T09:00:00.000+09:00,sequence=4,goal=]"
KB7 = f"{SP}@5c1d2e[id=5,rapidViewId=2,state=ACTIVE,name=KB 스프린트 7,startDate=2026-10-27T09:00:00.000+09:00,completeDate=<null>,goal=캐시 마무리, 문서 정리]"


@pytest.fixture
def jira():
    with mock_jira.start(require_token=TOKEN) as server:
        yield server


@pytest.fixture
def sleeps(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(net, "_sleep", waits.append)
    return waits


def client_for(server, **kw) -> JiraClient:
    return JiraClient(server.base_url, TOKEN, conn={"trust_env": False}, **kw)


def search_log(server) -> list[dict]:
    return [entry["query"] for entry in server.log if entry["path"] == "/rest/api/2/search"]


def keys(issues) -> list[str]:
    return [issue["key"] for issue in issues]


# ---------------------------------------------------------------- 픽스처


def test_fixture_shape():
    issues = json.loads((FIXTURES / "issues.json").read_text(encoding="utf-8"))
    assert len(issues) == 15
    assert {i["key"].split("-")[0] for i in issues} == {"KB", "OPS"}
    categories = {i["fields"]["status"]["statusCategory"]["key"] for i in issues}
    assert categories == {"new", "indeterminate", "done"}
    statuses = {i["fields"]["status"]["name"] for i in issues}
    assert {"Blocked", "In Review"} <= statuses
    assert any(i["fields"]["duedate"] is None for i in issues) and any(i["fields"]["duedate"] for i in issues)
    assert any(i["fields"]["customfield_10020"] for i in issues)
    assert max(i["fields"]["comment"]["total"] for i in issues) >= 3
    for issue in issues:
        for name in ("created", "updated"):
            stamp = issue["fields"][name]
            assert stamp.endswith(".000+0900") and parse_datetime(stamp) is not None
        for person in (issue["fields"]["assignee"], issue["fields"]["reporter"]):
            assert person is None or person["displayName"] in ("홍길동", "김철수")
    text = (FIXTURES / "issues.json").read_text(encoding="utf-8")
    for markup in ("h2.", "*front matter*", "{code", "{noformat}", "||경우||", "[설계 메모|http://wiki.example.invalid/"):
        assert markup in text
    for url in re.findall(r"https?://[^\s|\]\"]+", text):
        assert ".invalid" in url.split("/")[2]


# ---------------------------------------------------------------- ORDER BY 도움 함수


@pytest.mark.parametrize(
    ("jql", "expected"),
    [
        ("project = KB", ("project = KB", None)),
        ("project = KB ORDER BY updated DESC", ("project = KB", "updated DESC")),
        ("project = KB order   by key", ("project = KB", "key")),
        ("summary ~ 'order by' AND project = KB", ("summary ~ 'order by' AND project = KB", None)),
        ('summary ~ "x \\" ORDER BY y"', ('summary ~ "x \\" ORDER BY y"', None)),
        ("project in (KB) AND (status = A ORDER BY key)", ("project in (KB) AND (status = A ORDER BY key)", None)),
        ("ORDER BY key DESC", ("", "key DESC")),
        ("a = 1 ORDER BY key ORDER BY updated", ("a = 1", "key ORDER BY updated")),
        ("border = 1", ("border = 1", None)),
        ("", ("", None)),
    ],
)
def test_split_order_by(jql, expected):
    assert split_order_by(jql) == expected


def test_with_default_order():
    assert with_default_order("project = KB") == "project = KB ORDER BY key ASC"
    assert with_default_order("") == "ORDER BY key ASC"
    assert with_default_order("project = KB ORDER BY updated DESC") == "project = KB ORDER BY updated DESC"


# ---------------------------------------------------------------- 검색·페이지


def test_paging_15_issues_page_size_4(jira):
    client = client_for(jira, page_size=4)
    found = list(client.search('project in ("KB", "OPS")', fields=["summary", "status"]))
    assert keys(found) == ALL_KEYS_ASC  # 숫자 순서(KB-9 < KB-10)
    calls = search_log(jira)
    assert [c["startAt"] for c in calls] == ["0", "4", "8", "12"]
    assert all(c["maxResults"] == "4" and c["fields"] == "summary,status" for c in calls)
    assert calls[0]["jql"] == 'project in ("KB", "OPS") ORDER BY key ASC'
    assert set(found[0]["fields"]) == {"summary", "status"}


def test_paging_survives_server_cap(sleeps):
    with mock_jira.start(page_size_cap=3) as server:
        client = JiraClient(server.base_url, "t", conn={"trust_env": False}, page_size=4)
        found = list(client.search("project in (KB, OPS)", fields=["summary"]))
    assert keys(found) == ALL_KEYS_ASC
    assert [c["startAt"] for c in search_log(server)] == ["0", "3", "6", "9", "12"]


def test_paging_skips_key_repeated_across_pages(sleeps):
    # 페이지를 넘기는 사이 이슈가 앞으로 옮겨져 앞 페이지의 마지막 이슈가 다시 온다
    with mock_jira.start(repeat_page_boundary=True) as server:
        client = JiraClient(server.base_url, "t", conn={"trust_env": False}, page_size=4)
        found = list(client.search("project in (KB, OPS)", fields=["summary"]))
        pages = [c["startAt"] for c in search_log(server)]
    assert keys(found) == ALL_KEYS_ASC  # 키마다 한 번, 빠짐없이
    assert pages == ["0", "4", "8", "12"]


def test_mock_repeat_page_boundary_overlaps():
    # 시험이 의미 있는지: 이 모드에서 두 번째 페이지는 첫 페이지의 마지막 이슈로 시작한다
    with mock_jira.start(repeat_page_boundary=True) as server:
        client = JiraClient(server.base_url, "t", conn={"trust_env": False}, page_size=4)
        first = client._search_page("project = KB ORDER BY key ASC", 0, 4, "summary")
        second = client._search_page("project = KB ORDER BY key ASC", 4, 4, "summary")
    assert keys(second["issues"])[0] == keys(first["issues"])[-1] == "KB-9"


def test_mock_move_and_remove(jira):
    client = client_for(jira)
    jira.move("KB-12", "OPS-40")
    jira.remove("KB-3")
    moved = client.issue("KB-12")  # 옛 키 → 새 이슈(Jira처럼)
    assert moved["key"] == "OPS-40" and moved["fields"]["project"]["key"] == "OPS"
    assert keys(client.search("key in (KB-12, KB-5)", fields=["summary"])) == ["KB-5", "OPS-40"]
    with pytest.raises(NotFoundError):
        client.issue("KB-3")
    with pytest.raises(JqlError):
        client.search("key in (KB-3, KB-5)", fields=["summary"])


def test_search_first_page_errors_raise_at_call(jira):
    client = client_for(jira)
    with pytest.raises(JqlError):
        client.search("project = NOPE", fields=["summary"])  # list() 없이도 바로


def test_user_order_by_is_kept(jira):
    client = client_for(jira)
    jql = "project = KB ORDER BY updated DESC"
    found = list(client.search(jql, fields=["updated"]))
    assert search_log(jira)[0]["jql"] == jql
    stamps = [parse_datetime(issue["fields"]["updated"]) for issue in found]
    assert stamps == sorted(stamps, reverse=True) and len(found) == 9


def test_no_order_by_appends_key_asc_and_mock_default_differs(jira):
    client = client_for(jira)
    assert keys(client.search("project = OPS", fields=["summary"])) == ["OPS-1", "OPS-2", "OPS-4", "OPS-7", "OPS-8", "OPS-10"]
    # 정렬이 없을 때 모의 서버는 키 순서가 아니다: 클라이언트가 붙인 ORDER BY가 실제로 일한다
    raw = mock_jira.evaluate_jql(jira, "project = OPS")
    assert keys(raw) != ["OPS-1", "OPS-2", "OPS-4", "OPS-7", "OPS-8", "OPS-10"]


def test_double_order_by_is_jql_error(jira):
    client = client_for(jira)
    with pytest.raises(JqlError) as exc:
        list(client.search("project = KB ORDER BY key ORDER BY updated", fields=["summary"]))
    assert exc.value.exit_code == 4
    assert isinstance(exc.value, core.ConfigError)
    assert "ORDER" in str(exc.value) and exc.value.messages
    # 괄호 안 ORDER BY는 최상위가 아니므로 클라이언트가 key ASC를 붙이고, 서버가 거부한다
    with pytest.raises(JqlError):
        client.search("project = KB AND (status = Done ORDER BY key)", fields=["summary"])


def test_search_500_once_is_retried(sleeps):
    with mock_jira.start(search_500_once=True) as server:
        client = JiraClient(server.base_url, "t", conn={"trust_env": False}, page_size=10)
        assert len(list(client.search("project in (KB, OPS)", fields=["summary"]))) == 15
    assert sleeps == [1.0]
    assert server.count("search") == 3  # 500, 페이지 1, 페이지 2


def test_429_honours_retry_after(sleeps):
    with mock_jira.start(rate_limit=2) as server:
        client = JiraClient(server.base_url, "t", conn={"trust_env": False}, page_size=10)
        found = list(client.search("project = KB", fields=["summary"]))
    assert len(found) == 9
    assert sleeps == [1.0, 1.0]  # Retry-After: 1을 두 번 따랐다
    assert server.count("search") == 3  # 429 두 번 + 성공 한 번


# ---------------------------------------------------------------- JQL 검증


def test_validate_jql(jira):
    client = client_for(jira)
    assert client.validate_jql("assignee = currentUser() AND status = Blocked") is None
    assert client.validate_jql(JIRA_CONFIG["jql_mine"]) is None
    for jql in JIRA_CONFIG["views"].values():
        assert client.validate_jql(jql) is None
    message = client.validate_jql("status = 없음")
    assert message == "The value '없음' does not exist for the field 'status'."
    assert "Expecting" in client.validate_jql("project = KB AND")
    assert "does not exist" in client.validate_jql("nosuchfield = 1")
    assert all(q["maxResults"] == "0" for q in search_log(jira))


def test_validate_jql_auth_error_propagates():
    with mock_jira.start(require_token="right") as server:
        client = JiraClient(server.base_url, "wrong", conn={"trust_env": False})
        with pytest.raises(core.AuthError):
            client.validate_jql("project = KB")


# ---------------------------------------------------------------- 스프린트


def test_sprint_field_discovery_cached(jira):
    client = client_for(jira)
    assert client.sprint_field_id() == "customfield_10020"
    assert client.sprint_field_id() == "customfield_10020"
    assert [e["path"] for e in jira.log].count("/rest/api/2/field") == 1
    assert client.sync_fields() == [*ISSUE_FIELDS, "customfield_10020"]


def test_no_sprint_field():
    with mock_jira.start(no_sprint_field=True) as server:
        client = JiraClient(server.base_url, "t", conn={"trust_env": False})
        assert client.sprint_field_id() is None
        assert client.sync_fields() == list(ISSUE_FIELDS)
        assert "customfield_10020" not in client.issue("KB-12")["fields"]
        assert "sprint" in client.validate_jql("sprint in openSprints()")


def test_parse_sprint_string_form():
    assert parse_sprint([KB6, KB7]) == "KB 스프린트 7"  # ACTIVE 우선
    assert parse_sprint([KB7, KB6]) == "KB 스프린트 7"
    assert parse_sprint([KB6]) == "KB 스프린트 6"
    assert parse_sprint(KB6) == "KB 스프린트 6"
    comma = f"{SP}@1a2b3c[id=8,rapidViewId=3,state=ACTIVE,name=OPS 스프린트 3, 야간,startDate=<null>,goal=a, b]"
    assert parse_sprint([comma]) == "OPS 스프린트 3, 야간"
    assert parse_sprint(f"{SP}@1[id=1,state=ACTIVE,name=<null>]") is None
    assert parse_sprint(None) is None and parse_sprint([]) is None and parse_sprint(42) is None
    # goal(자유 글)에 든 `,name=`·`,state=`가 앞의 진짜 값을 덮지 못한다(첫 값이 이긴다)
    hostile = f"{SP}@5c1d2e[id=5,rapidViewId=2,state=ACTIVE,name=KB 스프린트 7,startDate=<null>,goal=릴리스 준비,name=X,state=CLOSED,synced=false]"
    assert parse_sprint([hostile]) == "KB 스프린트 7"
    closed = f"{SP}@1[id=4,state=CLOSED,name=KB 스프린트 6,goal=a,state=ACTIVE,name=가짜]"
    assert parse_sprint([closed, hostile]) == "KB 스프린트 7"  # 가짜 ACTIVE로 뽑히지 않는다
    assert parse_sprint([closed]) == "KB 스프린트 6"


def test_parse_sprint_object_form():
    closed = {"id": 4, "name": "KB 스프린트 6", "state": "closed"}
    active = {"id": 5, "name": "KB 스프린트 7", "state": "active"}
    future = {"id": 6, "name": "KB 스프린트 8", "state": "future"}
    assert parse_sprint([closed, active, future]) == "KB 스프린트 7"
    assert parse_sprint([closed, future]) == "KB 스프린트 8"  # ACTIVE 없음 → 마지막
    assert parse_sprint(active) == "KB 스프린트 7"
    assert parse_sprint([{"id": 1}]) is None


def test_sprint_through_mock_both_forms(jira):
    client = client_for(jira)
    sprint_id = client.sprint_field_id()
    issue = client.issue("KB-5", fields=["summary", sprint_id])
    assert isinstance(issue["fields"][sprint_id][0], str)
    assert parse_sprint(issue["fields"][sprint_id]) == "KB 스프린트 7"
    with mock_jira.start(sprint_form="object") as server:
        other = JiraClient(server.base_url, "t", conn={"trust_env": False})
        values = other.issue("OPS-2")["fields"]["customfield_10020"]
        assert values[0]["state"] == "active" and values[0]["id"] == 8
        assert parse_sprint(values) == "OPS 스프린트 3, 야간"


# ---------------------------------------------------------------- 이슈·댓글


def test_issue_and_404(jira):
    client = client_for(jira)
    issue = client.issue("KB-12")
    assert issue["key"] == "KB-12" and issue["self"].startswith(jira.base_url)
    assert issue["fields"]["comment"]["total"] == 5 and len(issue["fields"]["comment"]["comments"]) == 5
    assert set(client.issue("KB-12", fields=["summary"])["fields"]) == {"summary"}
    with pytest.raises(NotFoundError):
        client.issue("KB-999")
    with pytest.raises(core.ConfigError):
        client.issue("../myself")


def test_comments_paging(jira):
    client = client_for(jira, page_size=2)
    comments = client.comments("KB-12")
    assert len(comments) == 5
    assert comments[0]["body"] == "필드는 최소로 가자" and comments[0]["author"]["displayName"] == "김철수"
    calls = [e["query"] for e in jira.log if e["path"] == "/rest/api/2/issue/KB-12/comment"]
    assert [c["startAt"] for c in calls] == ["0", "2", "4"]
    assert client.comments("KB-10") == []
    with pytest.raises(NotFoundError):
        client.comments("KB-999")


def test_issue_comments_uses_embedded_or_pages(jira):
    client = client_for(jira)
    by_key = {i["key"]: i for i in client.search("key in (KB-7, KB-12)", fields=["comment"])}
    truncated = by_key["KB-12"]["fields"]["comment"]
    assert truncated["total"] == 5 and len(truncated["comments"]) == 3  # 검색은 3개만 싣는다
    before = len(jira.log)
    assert len(client.issue_comments(by_key["KB-7"])) == 2
    assert len(jira.log) == before  # 다 실려 있으면 묻지 않는다
    full = client.issue_comments(by_key["KB-12"])
    assert [c["id"] for c in full] == [c["id"] for c in client.comments("KB-12")]
    assert len(full) == 5


# ---------------------------------------------------------------- 오류 종류


def test_401_is_auth_error():
    with mock_jira.start(require_token="right") as server:
        client = JiraClient(server.base_url, "wrong", conn={"trust_env": False})
        for call in (client.myself, client.projects, lambda: client.search("project = KB", fields=["summary"])):
            with pytest.raises(core.AuthError) as exc:
                call()
            assert exc.value.exit_code == 2
            assert "wrong" not in str(exc.value)


def test_refused_port_is_network_error():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = JiraClient(f"http://127.0.0.1:{port}", "t", conn={"trust_env": False})
    with pytest.raises(core.NetworkError) as exc:
        client.myself()
    assert exc.value.exit_code == 3


def test_simple_endpoints(jira):
    client = client_for(jira)
    assert client.myself()["displayName"] == "홍길동"
    assert [p["key"] for p in client.projects()] == ["KB", "OPS"]
    assert client.server_info()["version"] == "9.17.0"
    assert any(f["id"] == "customfield_10020" for f in client.fields())


def test_bad_page_size():
    with pytest.raises(core.ConfigError):
        JiraClient("http://127.0.0.1:9", "t", page_size=0)
    with pytest.raises(core.ConfigError):
        JiraClient("", "t")


# ---------------------------------------------------------------- from_config


def _write_local(vault, base_url, **jira):
    config.save_local(vault, {"environment": "home", "jira": {"base_url": base_url, "project_keys": ["KB", "OPS"], "pat_ref": "keyring:work-kb/jira", "trust_env": False, **jira}})


def test_from_config_with_keyring_pat(vault, jira):
    kb_secrets.store("work-kb", "jira", TOKEN)
    _write_local(vault, jira.base_url, max_results=7)
    with JiraClient.from_config(config.load(vault)) as client:
        assert client.page_size == 7
        assert client.myself()["name"] == "honggildong"
    assert jira.log[-1]["has_auth"] is True


def test_from_config_sprint_field_preset(vault, jira):
    kb_secrets.store("work-kb", "jira", TOKEN)
    _write_local(vault, jira.base_url, sprint_field="customfield_99999")
    client = JiraClient.from_config(config.load(vault))
    assert client.sprint_field_id() == "customfield_99999"
    assert all(e["path"] != "/rest/api/2/field" for e in jira.log)


def test_from_config_missing_pat(vault, jira):
    _write_local(vault, jira.base_url)
    with pytest.raises(core.AuthError) as exc:
        JiraClient.from_config(config.load(vault))
    assert exc.value.exit_code == 2
    assert str(exc.value) == kb_secrets.describe_missing("keyring:work-kb/jira")


def test_from_config_missing_base_url(vault):
    config.save_local(vault, {"environment": "home", "jira": {"pat_ref": "keyring:work-kb/jira"}})
    with pytest.raises(core.ConfigError):
        JiraClient.from_config(config.load(vault))


# ---------------------------------------------------------------- 모의 서버 JQL


def _jql(server, jql):
    return keys(mock_jira.evaluate_jql(server, jql))


def test_mock_config_queries(jira):
    mine = _jql(jira, f'project in ("KB", "OPS") AND ({JIRA_CONFIG["jql_mine"]}) ORDER BY key ASC')
    assert "KB-11" not in mine and "KB-15" not in mine and "OPS-1" not in mine  # 남의 것, 30일 넘음
    assert len(mine) == 12
    views = JIRA_CONFIG["views"]
    assert sorted(_jql(jira, views["today"])) == sorted(["KB-5", "KB-7", "KB-10", "KB-12", "KB-14", "OPS-2", "OPS-10"])
    assert _jql(jira, views["blocked"]) == ["KB-7"]
    assert sorted(_jql(jira, views["review"])) == ["KB-14", "KB-9", "OPS-8"]


@pytest.mark.parametrize(
    ("jql", "expected"),
    [
        ("project = KB AND key = KB-12", ["KB-12"]),
        ("key in (KB-3, OPS-10) ORDER BY key DESC", ["OPS-10", "KB-3"]),
        ("updated >= '-60m' ORDER BY key", ["KB-12"]),
        ("updated >= \"-1d\" ORDER BY key", ["KB-5", "KB-9", "KB-12", "OPS-2"]),
        ("status = 'In Review' OR status = Blocked ORDER BY key", ["KB-7", "KB-9", "KB-14", "OPS-7", "OPS-8"]),
        ("statusCategory = Done ORDER BY key", ["KB-3", "KB-15", "OPS-1"]),
        ("watcher = currentUser() ORDER BY key", ["KB-12", "KB-15", "OPS-4"]),
        ("sprint in openSprints() AND project = KB ORDER BY key", ["KB-5", "KB-7", "KB-11", "KB-12", "KB-14"]),
        ("duedate <= endOfDay() AND statusCategory != Done ORDER BY key", ["KB-7", "KB-10", "OPS-10"]),
        ("NOT (project = KB) AND assignee is EMPTY", []),
        ("assignee is EMPTY", ["KB-11"]),
        ("project = OPS ORDER BY updated", ["OPS-1", "OPS-8", "OPS-10", "OPS-4", "OPS-7", "OPS-2"]),
    ],
)
def test_mock_jql_evaluator(jira, jql, expected):
    assert _jql(jira, jql) == expected


@pytest.mark.parametrize(
    "jql",
    [
        "project = KB ORDER BY key ORDER BY updated",
        "project = NOPE",
        "key = KB-999",
        "status = Unknown",
        "nosuch = 1",
        "project = KB AND",
        "(project = KB",
        "updated >= -30",
        "project = KB ORDER BY nosuch",
        "sprint in someSprints()",
        "summary = 'unterminated",
    ],
)
def test_mock_jql_errors_are_400(jira, jql):
    client = client_for(jira)
    message = client.validate_jql(jql)
    assert message
    with pytest.raises(JqlError):
        client.search(jql, fields=["summary"])


def test_mock_search_max_results_zero_and_defaults(jira):
    import urllib.parse
    import urllib.request

    send = urllib.request.build_opener(urllib.request.ProxyHandler({})).open  # HTTP 요청(파일 open 아님)
    url = f"{jira.base_url}/rest/api/2/search?" + urllib.parse.urlencode({"jql": "project = KB", "maxResults": 0})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with send(req, timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    assert body["total"] == 9 and body["issues"] == [] and body["maxResults"] == 0
    with send(urllib.request.Request(f"{jira.base_url}/rest/api/2/search", headers={"Authorization": f"Bearer {TOKEN}"}), timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    assert body["total"] == 15 and body["maxResults"] == 50
    assert "comment" not in body["issues"][0]["fields"]  # 기본(navigable)에는 댓글이 없다


def test_mock_now_iso_and_touch():
    with mock_jira.start(now_iso="2026-11-05T09:00:00+09:00") as server:
        assert _jql(server, "updated >= '-60m'") == []
        assert _jql(server, "updated >= '-17h'") == ["KB-12"]  # 16:05:11 ≥ 16:00
        stamp = server.touch("KB-3", summary="바뀐 제목")
        assert stamp == "2026-11-05T09:01:00.000+0900"  # 지금 + 1분
        assert _jql(server, "updated >= '-5m'") == ["KB-3"]
    with mock_jira.start() as server:
        before = server.now()
        assert before.isoformat() == "2026-11-04T16:05:11+09:00"  # 픽스처의 가장 늦은 updated
        stamp = server.touch("KB-3", summary="바뀐 제목")
        assert parse_datetime(stamp) > before
        assert _jql(server, "updated > '-1m'") == ["KB-3"]
        assert server.issues["KB-3"]["fields"]["summary"] == "바뀐 제목"
        client = JiraClient(server.base_url, "t", conn={"trust_env": False})
        assert client.issue("KB-3")["fields"]["summary"] == "바뀐 제목"


def test_mock_updates_mode():
    with mock_jira.start(updates={"OPS-1": {"updated": "2026-11-04T16:30:00.000+0900"}}) as server:
        assert server.now().isoformat() == "2026-11-04T16:30:00+09:00"
        assert _jql(server, "updated >= '-30m'") == ["OPS-1", "KB-12"]  # 정렬 없음 → updated 내림차순
    with pytest.raises(KeyError):
        mock_jira.start(updates={"KB-999": {"summary": "x"}})
