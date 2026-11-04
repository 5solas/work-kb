"""jira_client 쓰기 메서드(W3.1)와 모의 Jira 쓰기 경로: 요청 모양, 상태 코드 → 오류 종류(D52), 모드."""
from __future__ import annotations

import datetime as dt

import pytest

import mock_jira
from lib import core, net
from lib import jira_client as jc

TOKEN = "write-test-token-1234"
KST = dt.timezone(dt.timedelta(hours=9))


@pytest.fixture
def start_jira():
    started = []

    def _start(**modes):
        server = mock_jira.start(**modes)
        started.append(server)
        return server

    yield _start
    for server in started:
        server.stop()


def client_for(server, *, timeout: float = 5) -> jc.JiraClient:
    return jc.JiraClient(server.base_url, TOKEN, conn={"trust_env": False, "timeout": timeout})


def writes(server) -> list[dict]:
    return [e for e in server.log if e["method"] in ("POST", "PUT")]


# ---------------------------------------------------------------- 성공 경로와 본문 모양


def test_add_comment_posts_body_and_updates_issue(start_jira):
    server = start_jira()
    with client_for(server) as client:
        data = client.add_comment("KB-12", "배포 확인했습니다\n두 번째 줄")
        fetched = client.comments("KB-12")
    entry = writes(server)[0]
    assert (entry["method"], entry["path"]) == ("POST", "/rest/api/2/issue/KB-12/comment")
    assert entry["json"] == {"body": "배포 확인했습니다\n두 번째 줄"}
    assert entry["has_auth"] is True
    assert data["id"] and data["author"]["name"] == "honggildong" and data["body"].startswith("배포 확인")
    assert fetched[-1]["id"] == data["id"]
    assert server.issues["KB-12"]["fields"]["updated"] == data["created"]


def test_transitions_list_per_status(start_jira):
    server = start_jira()
    with client_for(server) as client:
        progress = client.transitions("KB-12")  # In Progress
        review = client.transitions("KB-9")  # In Review: 이름 'Done'이 둘
        todo = client.transitions("KB-10")
    assert [(t["id"], t["name"], t["to"]["name"]) for t in progress] == [
        ("21", "In Review", "In Review"), ("22", "Blocked", "Blocked"), ("31", "Done", "Done"),
    ]  # fmt: skip
    assert [t["name"] for t in review].count("Done") == 2
    assert {t["to"]["name"] for t in review if t["name"] == "Done"} == {"Done", "Closed"}
    assert [t["name"] for t in todo] == ["In Progress", "Blocked"]
    assert progress[2]["to"]["statusCategory"]["key"] == "done"


def test_do_transition_changes_status(start_jira):
    server = start_jira()
    with client_for(server) as client:
        assert client.do_transition("KB-12", "31") is None
    entry = writes(server)[0]
    assert entry["path"] == "/rest/api/2/issue/KB-12/transitions"
    assert entry["json"] == {"transition": {"id": "31"}}
    status = server.issues["KB-12"]["fields"]["status"]
    assert status["name"] == "Done" and status["statusCategory"]["key"] == "done"


def test_do_transition_wrong_id_is_500_server_error(start_jira):
    server = start_jira()
    with client_for(server) as client, pytest.raises(jc.WriteServerError) as info:
        client.do_transition("KB-12", "41")  # In Review에서만 가능
    assert info.value.status == 500
    assert info.value.after_send is True
    assert server.issues["KB-12"]["fields"]["status"]["name"] == "In Progress"
    assert len(writes(server)) == 1  # 쓰기는 다시 보내지 않는다


def test_transition_500_mode(start_jira):
    server = start_jira(transition_500=True)
    with client_for(server) as client, pytest.raises(jc.WriteServerError):
        client.do_transition("KB-12", "31")


def test_assign_puts_name(start_jira):
    server = start_jira()
    with client_for(server) as client:
        client.assign("KB-12", "kimcs")
    entry = writes(server)[0]
    assert (entry["method"], entry["path"], entry["json"]) == ("PUT", "/rest/api/2/issue/KB-12/assignee", {"name": "kimcs"})
    assert server.issues["KB-12"]["fields"]["assignee"]["displayName"] == "김철수"


def test_assign_unknown_user_is_rejected_with_jira_message(start_jira):
    server = start_jira()
    with client_for(server) as client, pytest.raises(jc.WriteRejected) as info:
        client.assign("KB-12", "nobody")
    assert info.value.status == 400
    assert any("does not exist" in m for m in info.value.messages)
    assert "assignee:" in str(info.value)


def test_assign_denied_is_401_with_valid_token(start_jira):
    server = start_jira(assign_denied=True)
    with client_for(server) as client:
        with pytest.raises(jc.WriteDenied) as info:
            client.assign("KB-12", "kimcs")
        assert client.myself()["name"] == "honggildong"  # 토큰은 살아 있다
    assert info.value.status == 401
    assert isinstance(info.value, core.AuthError)


def test_add_worklog_started_format(start_jira):
    server = start_jira()
    started = dt.datetime(2026, 11, 5, 14, 0, 7, 123456, tzinfo=KST)
    with client_for(server) as client:
        data = client.add_worklog("KB-12", 5400, "리뷰", started)
    entry = writes(server)[0]
    assert entry["path"] == "/rest/api/2/issue/KB-12/worklog"
    assert entry["json"] == {"timeSpentSeconds": 5400, "started": "2026-11-05T14:00:07.123+0900", "comment": "리뷰"}
    assert data["timeSpentSeconds"] == 5400
    assert server.worklogs["KB-12"][0]["comment"] == "리뷰"


def test_format_started_rules():
    assert jc.format_started(dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)) == "2026-01-02T03:04:05.000+0000"
    assert jc.format_started(dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone(dt.timedelta(hours=-5, minutes=-30)))).endswith("-0530")
    with pytest.raises(core.ConfigError):
        jc.format_started(dt.datetime(2026, 1, 2, 3, 4, 5))


def test_mock_rejects_bad_started_format(start_jira):
    server = start_jira()
    with client_for(server) as client:
        with pytest.raises(jc.WriteRejected) as info:
            client._send("POST", "/issue/KB-12/worklog", {"timeSpentSeconds": 60, "started": "2026-11-05T14:00:00+09:00"})
    assert any(m.startswith("started:") for m in info.value.messages)


def test_worklog_denied_is_403(start_jira):
    server = start_jira(worklog_denied=True)
    with client_for(server) as client, pytest.raises(jc.WriteDenied) as info:
        client.add_worklog("KB-12", 60, "", dt.datetime(2026, 11, 5, 9, 0, tzinfo=KST))
    assert info.value.status == 403


def test_create_issue_body_and_new_key(start_jira):
    server = start_jira()
    with client_for(server) as client:
        data = client.create_issue("KB", "새 캐시 정리", "Task", description="설명 본문")
        issue = client.issue(data["key"])
        second = client.create_issue("OPS", "운영 일감", "Bug")
    entry = writes(server)[0]
    assert (entry["method"], entry["path"]) == ("POST", "/rest/api/2/issue")
    assert entry["json"] == {
        "fields": {"project": {"key": "KB"}, "summary": "새 캐시 정리", "issuetype": {"name": "Task"}, "description": "설명 본문"}
    }
    assert data["key"] == "KB-16"  # 픽스처의 KB 최댓값 15 + 1
    assert data["self"].endswith(f"/rest/api/2/issue/{data['id']}")
    assert issue["fields"]["summary"] == "새 캐시 정리"
    assert issue["fields"]["status"]["name"] == "To Do"
    assert second["key"] == "OPS-11"


def test_create_issue_400_carries_field_errors(start_jira):
    server = start_jira()
    with client_for(server) as client, pytest.raises(jc.WriteRejected) as info:
        client.create_issue("KB", "제목", "Epic")
    assert info.value.status == 400
    assert any(m.startswith("issuetype:") for m in info.value.messages)
    with client_for(server) as client, pytest.raises(jc.WriteRejected) as info:
        client._send("POST", "/issue", {"fields": {"project": {"key": "KB"}}})
    assert {m.split(":")[0] for m in info.value.messages} == {"summary", "issuetype"}


def test_comment_on_missing_issue_is_rejected(start_jira):
    server = start_jira()
    with client_for(server) as client, pytest.raises(jc.WriteRejected) as info:
        client.add_comment("KB-999", "x")
    assert info.value.status == 404


def test_client_side_validation_sends_nothing(start_jira):
    server = start_jira()
    with client_for(server) as client:
        for call in (
            lambda: client.add_comment("KB-12", "   "),
            lambda: client.add_comment("kb12", "x"),
            lambda: client.do_transition("KB-12", ""),
            lambda: client.assign("KB-12", " "),
            lambda: client.add_worklog("KB-12", 30, "", dt.datetime(2026, 11, 5, tzinfo=KST)),
            lambda: client.create_issue("kb-1", "x", "Task"),
            lambda: client.create_issue("KB", " ", "Task"),
        ):
            with pytest.raises(core.ConfigError):
                call()
    assert writes(server) == []


# ---------------------------------------------------------------- 실패 종류


def test_bad_token_write_is_write_denied_401(start_jira):
    server = start_jira(bad_token=TOKEN)
    with client_for(server) as client:
        with pytest.raises(jc.WriteDenied) as info:
            client.add_comment("KB-12", "x")
        assert info.value.status == 401
        with pytest.raises(core.AuthError):
            client.myself()


def test_write_500_times_then_success_without_client_retry(start_jira):
    server = start_jira(write_500_times=2)
    with client_for(server) as client:
        for _ in range(2):
            with pytest.raises(jc.WriteServerError):
                client.add_comment("KB-12", "x")
        client.add_comment("KB-12", "x")
    assert len(writes(server)) == 3
    assert server.count("write") == 3
    bodies = [c["body"] for c in server.issues["KB-12"]["fields"]["comment"]["comments"]]
    assert bodies.count("x") == 1


def test_rate_limited_after_net_retries(start_jira, monkeypatch):
    waits = []
    monkeypatch.setattr(net, "_sleep", waits.append)
    server = start_jira(on_write=lambda req: (429, {"errorMessages": ["too many"]}, {"Retry-After": "2"}))
    with client_for(server) as client, pytest.raises(jc.RateLimited) as info:
        client.add_comment("KB-12", "x")
    assert info.value.after_send is False
    assert waits == [2.0] * 5
    assert len(writes(server)) == 6


def test_down_refuses_connection_on_kept_port_then_recovers(start_jira):
    server = start_jira(down=True)
    port = server.port
    with client_for(server) as client:
        with pytest.raises(core.NetworkError) as info:
            client.add_comment("KB-12", "x")
        assert info.value.after_send is False
        assert "연결이 거부" in str(info.value)
        server.come_up()
        assert server.port == port
        client.add_comment("KB-12", "y")
        server.go_down()
        with pytest.raises(core.NetworkError):
            client.transitions("KB-12")
        server.come_up()
        assert client.transitions("KB-12")
    assert [e["json"] for e in writes(server)] == [{"body": "y"}]  # 꺼진 동안의 요청은 닿지 않았다


def test_slow_write_is_read_timeout_after_send(start_jira):
    server = start_jira(slow_write_sec=1.5)
    with client_for(server, timeout=0.4) as client:
        with pytest.raises(core.NetworkError) as info:
            client.add_comment("KB-12", "느린 쓰기")
        assert info.value.after_send is True
        assert "응답 시간이 초과" in str(info.value)
        assert client.transitions("KB-12")  # 읽기는 느리지 않다
    bodies = [c["body"] for c in server.issues["KB-12"]["fields"]["comment"]["comments"]]
    assert "느린 쓰기" in bodies  # 서버에는 반영됐다(그래서 '불확실')


def test_on_write_hook_sees_request(start_jira):
    seen = []
    server = start_jira(on_write=lambda req: seen.append((req.method, req.path, req.json)))
    with client_for(server) as client:
        client.assign("KB-12", "kimcs")
    assert seen == [("PUT", "/rest/api/2/issue/KB-12/assignee", {"name": "kimcs"})]


# ---------------------------------------------------------------- 리다이렉트·엄격한 성공 모양


def _comment_count(server, key: str = "KB-12") -> int:
    return len(server.issues[key]["fields"]["comment"]["comments"])


def test_redirect_on_write_is_rejected_and_not_followed(start_jira):
    """301을 따라가면 requests가 POST를 GET으로 바꿔 '성공'처럼 보인다. 따라가지 않고 WriteRejected."""
    server = start_jira(redirect_writes=True)
    before = _comment_count(server)
    with client_for(server) as client:
        for call in (
            lambda: client.add_comment("KB-12", "리다이렉트"),
            lambda: client.do_transition("KB-12", "31"),
            lambda: client.assign("KB-12", "kimcs"),
            lambda: client.add_worklog("KB-12", 60, "", dt.datetime(2026, 11, 5, 9, 0, tzinfo=KST)),
            lambda: client.create_issue("KB", "리다이렉트", "Task"),
        ):
            with pytest.raises(jc.WriteRejected) as info:
                call()
            assert 300 <= info.value.status < 400
            assert "리다이렉트" in str(info.value) and "https 주소로 고치세요" in str(info.value)
            assert f"{server.base_url}/rest/api/2/" in str(info.value)  # Location
    assert [e["method"] for e in server.log] == ["POST", "POST", "PUT", "POST", "POST"]  # GET으로 따라가지 않음
    assert _comment_count(server) == before
    assert server.issues["KB-12"]["fields"]["status"]["name"] == "In Progress"
    assert "KB-16" not in server.issues


def test_html_200_on_write_is_unexpected_response(start_jira):
    server = start_jira(html_200=True)
    before = _comment_count(server)
    with client_for(server) as client:
        for call in (
            lambda: client.add_comment("KB-12", "html"),
            lambda: client.do_transition("KB-12", "31"),
            lambda: client.assign("KB-12", "kimcs"),
            lambda: client.add_worklog("KB-12", 60, "", dt.datetime(2026, 11, 5, 9, 0, tzinfo=KST)),
            lambda: client.create_issue("KB", "html", "Task"),
        ):
            with pytest.raises(jc.UnexpectedResponse) as info:
                call()
            assert info.value.status == 200
            assert "JSON이 아닙니다" in str(info.value) or "다른 성공 코드" in str(info.value)  # 생성은 201만
            assert info.value.exit_code == 3
    assert server.count("write") == 5
    assert _comment_count(server) == before and "KB-16" not in server.issues


@pytest.mark.parametrize(
    ("answer", "call"),
    [
        ((201, {}), lambda c: c.add_comment("KB-12", "x")),  # id 없음
        ((201, {"id": 5}), lambda c: c.add_comment("KB-12", "x")),  # 문자열 id 아님
        ((204, None), lambda c: c.add_comment("KB-12", "x")),  # 다른 성공 코드
        ((201, {"self": "x"}), lambda c: c.add_worklog("KB-12", 60, "", dt.datetime(2026, 11, 5, 9, 0, tzinfo=KST))),
        ((201, {}), lambda c: c.do_transition("KB-12", "31")),  # 전환은 204(또는 200)
        ((202, None), lambda c: c.assign("KB-12", "kimcs")),
        ((201, {}), lambda c: c.create_issue("KB", "키 없음", "Task")),  # 키 없음
        ((200, {"id": "1", "key": "KB-99"}), lambda c: c.create_issue("KB", "200", "Task")),  # 생성은 201만
    ],
)
def test_wrong_success_shape_is_unexpected_response(start_jira, answer, call):
    server = start_jira(on_write=lambda req: answer)
    with client_for(server) as client, pytest.raises(jc.UnexpectedResponse):
        call(client)


def test_accepted_success_variants(start_jira):
    """댓글 200 + id, 전환·담당 200 + JSON 본문은 받는다."""
    server = start_jira(on_write=lambda req: (200, {"id": "777"}) if req.path.endswith("/comment") else (200, {}))
    with client_for(server) as client:
        assert client.add_comment("KB-12", "x")["id"] == "777"
        assert client.do_transition("KB-12", "31") is None
        assert client.assign("KB-12", "kimcs") is None


def test_unknown_mode_rejected():
    with pytest.raises(TypeError):
        mock_jira.start(write_fail=True)
