"""jira_act (W3.1): outbox 큐 경유 Jira 쓰기. 지시서 §6.3, D26·D52·D53·D57.

각 하위 명령의 성공 경로(요청 본문 모양), 다운 → pending → 복구 → flush → done → 캐시 갱신,
5회 실패 → failed, 401/403 재확인, 불확실(읽기 시간 초과), 전환 후보, 확인 질문, 승격 줄 덧붙이기와
해시 불일치 거부, KB_AGENT 거부, 범위 밖 프로젝트, 아무 일 없을 때 outbox 바이트 불변, 전송 중 잠금 없음.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import jira_act
import mock_jira
from lib import config, core, frontmatter, net, outbox
from lib import secrets as kb_secrets

TOKEN = "act-test-token-1234"
NOW = "2026-11-05T16:05:00+09:00"
DAY = "2026-11-05"
DAILY_TEXT = (
    "---\n"
    f"date: {DAY}\n"
    "---\n"
    "## 오늘\n"
    "<!-- kb:today:start -->\n"
    "- [ ] [[KB-12]] 캐시 front matter 확정 · 진행중\n"
    "<!-- kb:today:end -->\n"
    "\n"
    "## 기록\n"
    "- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로 저장하기로.\n"  # 10번째 줄
    "- 11:40 개인: PR 리뷰 요청 옴\n"  # 11
    "- [ ] 개인: 11월 회의실 예약\n"  # 12
)
LINE_DISCUSS = 10
LINE_REVIEW = 11
LINE_TODO = 12


# ---------------------------------------------------------------- 준비


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


def _configure(vault: Path, base_url: str, **jira) -> config.Config:
    kb_secrets.store("work-kb", "jira", TOKEN)
    section = {"base_url": base_url, "project_keys": ["KB", "OPS"], "pat_ref": "keyring:work-kb/jira", "trust_env": False}
    section.update(jira)
    config.save_local(vault, {"environment": "home", "jira": section})
    return config.load(vault)


def _daily(vault: Path, text: str = DAILY_TEXT) -> Path:
    path = vault / "daily" / f"{DAY}.md"
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture
def make_env(vault, start_jira, fake_now, monkeypatch):
    fake_now(NOW)
    monkeypatch.setattr(net, "_sleep", lambda seconds: None)

    def _make(*, jira: dict | None = None, **modes):
        server = start_jira(**modes)
        cfg = _configure(vault, server.base_url, **(jira or {}))
        daily = _daily(vault)
        return SimpleNamespace(vault=vault, jira=server, cfg=cfg, daily=daily)

    return _make


@pytest.fixture
def env(make_env):
    return make_env()


def run(capsys, vault: Path, *args: str) -> tuple[int, str, str]:
    """core.run_main처럼 KbError를 종료 코드로 바꾼다."""
    capsys.readouterr()
    try:
        code = jira_act.main([*args, "--root", str(vault)])
    except core.KbError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        code = exc.exit_code
    out, err = capsys.readouterr()
    return code, out, err


def records(vault: Path) -> list[dict]:
    return outbox.records(vault)


def only(vault: Path) -> dict:
    items = records(vault)
    assert len(items) == 1, items
    return items[0]


def outbox_bytes(vault: Path) -> bytes | None:
    path = outbox.path_for(vault)
    return path.read_bytes() if path.exists() else None


def writes(server) -> list[dict]:
    return [e for e in server.log if e["method"] in ("POST", "PUT")]


def comments_section(vault: Path, key: str) -> str:
    text = (vault / "jira" / f"{key}.md").read_text(encoding="utf-8")
    return text.split("## 댓글", 1)[1].split("## 노트에서 언급", 1)[0]


def answer(monkeypatch, reply: str | None) -> list[str]:
    """확인 질문에 reply로 답한다(None이면 EOF). 받은 질문 목록을 돌려준다."""
    asked: list[str] = []

    def fake(prompt: str) -> str:
        asked.append(prompt)
        if reply is None:
            raise EOFError
        return reply

    monkeypatch.setattr(jira_act, "_input", fake)
    return asked


# ---------------------------------------------------------------- outbox 단위


def test_outbox_ids_sort_by_time_and_are_unique():
    ids = [outbox.new_id(1_700_000_000_000) for _ in range(50)]
    assert ids == sorted(ids) and len(set(ids)) == 50
    later = outbox.new_id(1_700_000_000_001 + 10_000)
    assert later > ids[-1]
    assert all(len(i) == 26 for i in ids)


def test_outbox_record_shape_and_noop_update(vault):
    record = outbox.make_record(action="comment", key="KB-12", payload={"body": "한글"}, ts=NOW, source="daily/x.md:1")
    outbox.append(vault, record)
    raw = outbox_bytes(vault)
    line = json.loads(raw.decode("utf-8"))
    assert list(line)[:10] == list(outbox.FIELD_ORDER)
    assert line["status"] == "pending" and line["attempts"] == 0 and line["last_error"] is None and line["done_at"] is None
    assert "한글" in raw.decode("utf-8")  # ensure_ascii=False
    assert raw.endswith(b"\n") and b"\r" not in raw
    outbox.update(vault, record["id"], {"status": "pending"})  # 바뀐 것 없음
    assert outbox_bytes(vault) == raw
    with pytest.raises(KeyError):
        outbox.update(vault, "없는id", {"status": "done"})


def test_outbox_keeps_unreadable_lines_byte_for_byte(vault):
    path = outbox.path_for(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{broken\n")
    record = outbox.append(vault, outbox.make_record(action="assign", key="KB-1", payload={}, ts=NOW))
    outbox.update(vault, record["id"], {"status": "done"})
    data = path.read_bytes()
    assert data.startswith(b"{broken\n")
    assert outbox.broken_lines(vault) == 1
    assert outbox.get(vault, record["id"])["status"] == "done"


# ---------------------------------------------------------------- 하위 명령 성공 경로


def test_comment_body_sends_marks_done_and_syncs_cache(env, capsys):
    code, out, err = run(capsys, env.vault, "comment", "KB-12", "--body", "배포 확인했습니다")
    assert code == 0, err
    entry = writes(env.jira)[0]
    assert (entry["method"], entry["path"], entry["json"]) == ("POST", "/rest/api/2/issue/KB-12/comment", {"body": "배포 확인했습니다"})
    rec = only(env.vault)
    assert rec["action"] == "comment" and rec["key"] == "KB-12" and rec["payload"] == {"body": "배포 확인했습니다"}
    assert rec["status"] == "done" and rec["attempts"] == 1 and rec["last_error"] is None
    assert rec["done_at"] == NOW and rec["ts"] == NOW
    assert rec["result"]["comment_id"]
    assert rec["source"] is None
    assert "홍길동: 배포 확인했습니다" in comments_section(env.vault, "KB-12")
    assert "outbox에 넣음" in out and "보냄: 댓글 KB-12" in out and "jira/KB-12.md: 동기화함" in out


def test_comment_from_line_uses_line_text_and_records_source(env, capsys):
    code, out, err = run(capsys, env.vault, "comment", "KB-12", "--from-line", f"daily/{DAY}.md:{LINE_DISCUSS}")
    assert code == 0, err
    body = writes(env.jira)[0]["json"]["body"]
    assert body == "KB-12 @김철수 와 front matter 논의. updated는 UTC로 저장하기로."
    rec = only(env.vault)
    assert rec["source"] == f"daily/{DAY}.md:{LINE_DISCUSS}"
    # --body가 있으면 본문은 --body, 출처는 줄
    code, _, _ = run(capsys, env.vault, "comment", "KB-12", "--body", "직접 쓴 글", "--from-line", f"daily/{DAY}.md:{LINE_REVIEW}")
    assert code == 0
    assert writes(env.jira)[1]["json"] == {"body": "직접 쓴 글"}
    assert records(env.vault)[1]["source"] == f"daily/{DAY}.md:{LINE_REVIEW}"


@pytest.mark.parametrize(
    "args",
    [
        ("comment", "KB-12"),
        ("comment", "KB-12", "--body", "  "),
        ("comment", "KB-12", "--from-line", f"daily/{DAY}.md:99"),
        ("comment", "KB-12", "--from-line", "daily/../config.yaml:1"),
        ("comment", "kb12", "--body", "x"),
        ("worklog", "KB-12", "1d"),
        ("worklog", "KB-12", "abc"),
        ("worklog", "KB-12", "30s"),
        ("worklog", "KB-12", "1h", "--started", "어제"),
        ("create", "KB", "--summary", "x"),
        ("promote", "--from", f"daily/{DAY}.md:8", "KB", "--yes"),  # 빈 줄
        ("promote", "--from", "inbox/메모.md:1", "KB", "--yes"),  # daily만
        (),
    ],
)
def test_usage_errors_exit_4_and_send_nothing(env, capsys, args):
    code, _out, err = run(capsys, env.vault, *args)
    assert code == 4, err
    assert writes(env.jira) == []
    assert outbox_bytes(env.vault) is None


def test_transition_by_name_case_insensitive(env, capsys):
    code, out, err = run(capsys, env.vault, "transition", "KB-12", "in review", "--yes")
    assert code == 0, err
    entry = writes(env.jira)[0]
    assert (entry["path"], entry["json"]) == ("/rest/api/2/issue/KB-12/transitions", {"transition": {"id": "21"}})
    rec = only(env.vault)
    assert rec["payload"] == {"transition_id": "21", "name": "In Review", "to": "In Review"}
    meta = frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())
    assert meta["status"] == "In Review"


def test_transition_multiple_candidates_lists_and_exits_4(env, capsys):
    code, out, err = run(capsys, env.vault, "transition", "KB-9", "done", "--yes")
    assert code == 4
    assert "2개" in err
    assert "41\tDone → Done" in out and "42\tDone → Closed" in out
    assert "43" not in out
    assert writes(env.jira) == []
    assert outbox_bytes(env.vault) is None
    # 목록의 id로 고르면 보낸다
    code, out, err = run(capsys, env.vault, "transition", "KB-9", "42", "--yes")
    assert code == 0, err
    assert writes(env.jira)[0]["json"] == {"transition": {"id": "42"}}
    assert env.jira.issues["KB-9"]["fields"]["status"]["name"] == "Closed"


def test_transition_no_candidate_lists_options(env, capsys):
    code, out, err = run(capsys, env.vault, "transition", "KB-12", "Reopen", "--yes")
    assert code == 4
    assert "없습니다" in err
    assert "21\tIn Review → In Review" in out and "31\tDone → Done" in out
    assert writes(env.jira) == [] and outbox_bytes(env.vault) is None


def test_transition_by_target_status_name(env, capsys):
    code, _out, err = run(capsys, env.vault, "transition", "KB-9", "closed", "--yes")  # 전환 이름은 Done, 도착이 Closed
    assert code == 0, err
    assert writes(env.jira)[-1]["json"] == {"transition": {"id": "42"}}
    code, _out, err = run(capsys, env.vault, "transition", "KB-7", "in progress", "--yes")
    assert code == 0, err
    assert writes(env.jira)[-1]["json"] == {"transition": {"id": "51"}}


def test_transition_confirm_declined_and_accepted(env, capsys, monkeypatch):
    asked = answer(monkeypatch, None)
    code, out, _err = run(capsys, env.vault, "transition", "KB-12", "Done")
    assert code == 1 and "취소" in out
    assert asked and "KB-12" in asked[0] and "Done" in asked[0]
    answer(monkeypatch, "n")
    assert run(capsys, env.vault, "transition", "KB-12", "Done")[0] == 1
    assert writes(env.jira) == [] and outbox_bytes(env.vault) is None
    answer(monkeypatch, "y")
    code, _out, err = run(capsys, env.vault, "transition", "KB-12", "Done")
    assert code == 0, err
    assert env.jira.issues["KB-12"]["fields"]["status"]["name"] == "Done"


def test_transition_500_is_failed_immediately(make_env, capsys):
    env = make_env(transition_500=True)
    code, _out, err = run(capsys, env.vault, "transition", "KB-12", "Done", "--yes")
    assert code == 1
    rec = only(env.vault)
    assert rec["status"] == "failed" and rec["attempts"] == 1
    assert "500" in rec["last_error"]
    assert "act transitions KB-12" in err


def test_transitions_lists_without_writing(env, capsys, monkeypatch):
    monkeypatch.setenv("KB_AGENT", "1")  # 읽기라서 에이전트도 된다
    code, out, err = run(capsys, env.vault, "transitions", "KB-12", "--json")
    assert code == 0, err
    rows = json.loads(out)
    assert rows == [
        {"id": "21", "name": "In Review", "to": "In Review", "to_category": "indeterminate"},
        {"id": "22", "name": "Blocked", "to": "Blocked", "to_category": "indeterminate"},
        {"id": "31", "name": "Done", "to": "Done", "to_category": "done"},
    ]
    code, out, _ = run(capsys, env.vault, "transitions", "KB-9")
    assert code == 0 and "41\tDone → Done" in out
    assert writes(env.jira) == [] and outbox_bytes(env.vault) is None


def test_assign_sends_name_and_syncs(env, capsys):
    code, _out, err = run(capsys, env.vault, "assign", "KB-12", "kimcs")
    assert code == 0, err
    entry = writes(env.jira)[0]
    assert (entry["method"], entry["path"], entry["json"]) == ("PUT", "/rest/api/2/issue/KB-12/assignee", {"name": "kimcs"})
    assert frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())["assignee"] == "김철수"
    assert only(env.vault)["payload"] == {"username": "kimcs"}


def test_worklog_payload_and_started_format(env, capsys):
    code, _out, err = run(capsys, env.vault, "worklog", "KB-12", "1h 30m", "--comment", "리뷰", "--started", "2026-11-05T14:00")
    assert code == 0, err
    assert writes(env.jira)[0]["json"] == {"timeSpentSeconds": 5400, "started": "2026-11-05T14:00:00.000+0900", "comment": "리뷰"}
    rec = only(env.vault)
    assert rec["payload"]["time_spent"] == "1h30m" and rec["payload"]["seconds"] == 5400
    assert rec["result"]["worklog_id"]
    # 시작 시각을 주지 않으면 지금(KB_FAKE_NOW)
    code, _out, err = run(capsys, env.vault, "worklog", "KB-12", "45m")
    assert code == 0, err
    assert writes(env.jira)[1]["json"] == {"timeSpentSeconds": 2700, "started": "2026-11-05T16:05:00.000+0900"}


def test_create_with_yes_makes_issue_and_cache(env, capsys):
    code, out, err = run(capsys, env.vault, "create", "KB", "--summary", "로그 회전 정리", "--type", "Task", "--description", "설명", "--yes")
    assert code == 0, err
    assert writes(env.jira)[0]["json"] == {
        "fields": {"project": {"key": "KB"}, "summary": "로그 회전 정리", "issuetype": {"name": "Task"}, "description": "설명"}
    }
    rec = only(env.vault)
    assert rec["key"] == "KB" and rec["result"] == {"key": "KB-16"}
    assert "새 일감: KB-16" in out
    meta = frontmatter.parse((env.vault / "jira" / "KB-16.md").read_bytes())
    assert meta["summary"] == "로그 회전 정리" and meta["status"] == "To Do"


def test_create_confirm_eof_declines_exit_1(env, capsys, monkeypatch):
    answer(monkeypatch, None)
    code, out, _err = run(capsys, env.vault, "create", "KB", "--summary", "x", "--type", "Task")
    assert code == 1 and "취소" in out
    assert writes(env.jira) == [] and outbox_bytes(env.vault) is None


def test_create_confirm_eof_in_subprocess(env, run_kb):
    result = run_kb("act", "create", "KB", "--summary", "확인 없이", "--type", "Task", stdin="")
    assert result.returncode == 1, result.stderr
    assert "취소" in result.stdout
    assert writes(env.jira) == []
    # --argv-stdin(Emacs 경로)은 stdin을 인수로 다 쓰므로 확인 질문은 EOF → --yes가 필요하다
    argv = json.dumps(["act", "create", "KB", "--summary", "에맥스", "--type", "Task", "--yes"], ensure_ascii=False)
    result = run_kb("--argv-stdin", stdin=argv)
    assert result.returncode == 0, result.stderr
    assert writes(env.jira)[0]["json"]["fields"]["summary"] == "에맥스"


def test_create_refuses_project_outside_project_keys(env, capsys):
    code, _out, err = run(capsys, env.vault, "create", "ZZ", "--summary", "x", "--type", "Task", "--yes")
    assert code == 4 and "project_keys" in err
    code, _out, err = run(capsys, env.vault, "comment", "ZZ-1", "--body", "x")
    assert code == 4
    assert writes(env.jira) == [] and outbox_bytes(env.vault) is None


# ---------------------------------------------------------------- promote


def test_promote_appends_key_to_source_line(env, capsys):
    before = env.daily.read_bytes()
    code, out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes")
    assert code == 0, err
    assert writes(env.jira)[0]["json"]["fields"] == {"project": {"key": "KB"}, "summary": "개인: PR 리뷰 요청 옴", "issuetype": {"name": "Task"}}
    after = env.daily.read_bytes()
    assert after == before.replace("- 11:40 개인: PR 리뷰 요청 옴\n".encode(), "- 11:40 개인: PR 리뷰 요청 옴 [[KB-16]]\n".encode())
    rec = only(env.vault)
    assert rec["action"] == "promote" and rec["source"] == f"daily/{DAY}.md:{LINE_REVIEW}"
    assert rec["payload"]["line_hash"] == core.line_hash("- 11:40 개인: PR 리뷰 요청 옴")
    assert rec["result"] == {"key": "KB-16", "linked": True}
    assert (env.vault / "jira" / "KB-16.md").exists()
    # 같은 줄을 다시 승격하면 거부(이미 키가 붙음)
    code, _out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes")
    assert code == 4 and "이미" in err


def test_promote_keeps_crlf_and_bom(make_env, capsys):
    env = make_env()
    data = b"\xef\xbb\xbf" + DAILY_TEXT.replace("\n", "\r\n").encode("utf-8")
    env.daily.write_bytes(data)
    code, _out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_TODO}", "OPS", "--yes")
    assert code == 0, err
    assert writes(env.jira)[0]["json"]["fields"]["summary"] == "개인: 11월 회의실 예약"
    assert env.daily.read_bytes() == data.replace("- [ ] 개인: 11월 회의실 예약\r\n".encode(), "- [ ] 개인: 11월 회의실 예약 [[OPS-11]]\r\n".encode())


def test_promote_line_changed_while_pending_is_refused(make_env, capsys):
    env = make_env(down=True)
    code, _out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes")
    assert code == 3, err
    assert only(env.vault)["status"] == "pending"
    # 대기 중에 같은 줄을 또 승격하면 거부
    code, _out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes")
    assert code == 4 and "대기 중" in err
    edited = env.daily.read_bytes().replace("PR 리뷰 요청 옴".encode(), "PR 리뷰 끝냄".encode())
    env.daily.write_bytes(edited)
    env.jira.come_up()
    code, out, err = run(capsys, env.vault, "flush")
    assert code == 0, err
    assert "새 일감: KB-16" in out
    assert "붙이지 않았습니다" in err and "[[KB-16]]" in err
    assert env.daily.read_bytes() == edited  # 사람이 고친 줄은 건드리지 않는다
    assert only(env.vault)["result"] == {"key": "KB-16", "linked": False}


def test_promote_finds_line_moved_by_insert_above(make_env, capsys):
    env = make_env(down=True)
    code, _out, _err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes")
    assert code == 3
    text = env.daily.read_text(encoding="utf-8").replace("## 기록\n", "## 기록\n- 08:00 새로 끼운 줄\n")
    env.daily.write_bytes(text.encode("utf-8"))
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 0, err
    assert "- 11:40 개인: PR 리뷰 요청 옴 [[KB-16]]\n" in env.daily.read_text(encoding="utf-8")


def test_promote_matches_carried_over_form(make_env, capsys):
    env = make_env(down=True)
    code, _out, _err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_TODO}", "KB", "--yes")
    assert code == 3
    text = env.daily.read_text(encoding="utf-8").replace("- [ ] 개인: 11월 회의실 예약", "- [>] 개인: 11월 회의실 예약 (2026-11-06로 이월)")
    env.daily.write_bytes(text.encode("utf-8"))
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 0, err
    assert "- [>] 개인: 11월 회의실 예약 (2026-11-06로 이월) [[KB-16]]\n" in env.daily.read_text(encoding="utf-8")


def test_promote_confirm_declined(env, capsys, monkeypatch):
    asked = answer(monkeypatch, "")
    code, out, _err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB")
    assert code == 1 and "취소" in out and "PR 리뷰 요청 옴" in asked[0]
    assert writes(env.jira) == [] and outbox_bytes(env.vault) is None


# ---------------------------------------------------------------- 다운 → 복구 (Phase 3 관문)


def test_down_pending_recover_flush_done_cache_updated(make_env, capsys):
    env = make_env(down=True)
    code, out, err = run(capsys, env.vault, "comment", "KB-12", "--body", "다운 중에 쓴 댓글")
    assert code == 3
    assert "outbox에 남겼습니다" in err
    rec = only(env.vault)
    assert rec["status"] == "pending" and rec["attempts"] == 1 and "연결이 거부" in rec["last_error"]
    assert not (env.vault / "jira" / "KB-12.md").exists()
    assert writes(env.jira) == []

    env.jira.come_up()  # 같은 포트에서 복구
    code, out, err = run(capsys, env.vault, "flush")
    assert code == 0, err
    rec = only(env.vault)
    assert rec["status"] == "done" and rec["attempts"] == 2 and rec["last_error"] is None and rec["done_at"] == NOW
    assert writes(env.jira)[0]["json"] == {"body": "다운 중에 쓴 댓글"}
    assert "홍길동: 다운 중에 쓴 댓글" in comments_section(env.vault, "KB-12")
    assert "flush: 보냄 1" in out


def test_flush_stops_at_connection_error_and_keeps_order(make_env, capsys):
    env = make_env(down=True)
    for body in ("첫째", "둘째", "셋째"):
        assert run(capsys, env.vault, "comment", "KB-12", "--body", body)[0] == 3
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 3 and "나머지 2건" in err
    assert [r["attempts"] for r in records(env.vault)] == [2, 1, 1]  # 첫 항목만 다시 시도
    env.jira.come_up()
    assert run(capsys, env.vault, "flush")[0] == 0
    assert [e["json"]["body"] for e in writes(env.jira)] == ["첫째", "둘째", "셋째"]  # id 순서


def test_five_failures_become_failed_then_retry_failed(make_env, capsys):
    env = make_env(write_500_times=5)
    assert run(capsys, env.vault, "comment", "KB-12", "--body", "다섯 번 실패")[0] == 3
    for attempt in (2, 3, 4):
        code, _out, _err = run(capsys, env.vault, "flush")
        assert code == 3
        assert only(env.vault)["attempts"] == attempt and only(env.vault)["status"] == "pending"
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 1 and "5번 시도" in err
    rec = only(env.vault)
    assert rec["status"] == "failed" and rec["attempts"] == 5
    before = outbox_bytes(env.vault)
    code, out, _ = run(capsys, env.vault, "flush")
    assert code == 0 and "보낼 항목이 없습니다" in out
    assert outbox_bytes(env.vault) == before
    assert env.jira.count("write") == 5
    code, _out, err = run(capsys, env.vault, "flush", "--retry-failed")
    assert code == 0, err
    rec = only(env.vault)
    assert rec["status"] == "done" and rec["attempts"] == 1


def test_permission_denied_with_valid_token_is_failed_not_pending(make_env, capsys):
    env = make_env(assign_denied=True)
    code, _out, err = run(capsys, env.vault, "assign", "KB-12", "kimcs")
    assert code == 1
    rec = only(env.vault)
    assert rec["status"] == "failed" and rec["attempts"] == 1 and "권한이 없습니다" in rec["last_error"]
    assert [e["path"] for e in env.jira.log][-1] == "/rest/api/2/myself"  # D52 재확인


def test_worklog_403_is_failed(make_env, capsys):
    env = make_env(worklog_denied=True)
    code, _out, _err = run(capsys, env.vault, "worklog", "KB-12", "1h")
    assert code == 1
    assert only(env.vault)["status"] == "failed"


def test_bad_token_stays_pending_exit_2(make_env, capsys):
    env = make_env(bad_token=TOKEN)
    code, _out, err = run(capsys, env.vault, "comment", "KB-12", "--body", "토큰 문제")
    assert code == 2
    assert "인증 실패" in err and "init --section jira" in err
    rec = only(env.vault)
    assert rec["status"] == "pending" and rec["attempts"] == 0 and "401" in rec["last_error"]
    env.jira.modes.pop("bad_token")  # 토큰을 고쳤다
    assert run(capsys, env.vault, "flush")[0] == 0
    assert only(env.vault)["status"] == "done"


def test_bad_token_stops_flush(make_env, capsys):
    env = make_env(down=True)
    for body in ("a", "b"):
        run(capsys, env.vault, "comment", "KB-12", "--body", body)
    env.jira.modes["bad_token"] = TOKEN
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 2 and "나머지 1건" in err
    assert [r["status"] for r in records(env.vault)] == ["pending", "pending"]


def test_rate_limit_consumes_no_attempt(make_env, capsys):
    env = make_env(on_write=lambda req: (429, {"errorMessages": ["too many"]}, {"Retry-After": "0"}))
    code, _out, _err = run(capsys, env.vault, "comment", "KB-12", "--body", "x")
    assert code == 3
    rec = only(env.vault)
    assert rec["status"] == "pending" and rec["attempts"] == 0 and "429" in rec["last_error"]


def test_read_timeout_is_uncertain_and_needs_retry_uncertain(make_env, capsys):
    env = make_env(jira={"timeout": 0.4}, slow_write_sec=1.5)
    code, _out, err = run(capsys, env.vault, "comment", "KB-12", "--body", "느린 댓글")
    assert code == 3
    assert "불확실" in err and "--retry-uncertain" in err
    rec = only(env.vault)
    assert rec["status"] == "pending" and rec["last_error"] == "uncertain" and rec["attempts"] == 1
    assert "응답 시간이 초과" in rec["error_detail"]

    before = outbox_bytes(env.vault)
    writes_before = env.jira.count("write")
    code, out, _err = run(capsys, env.vault, "flush")
    assert code == 0 and "불확실 1건은 건너뜀" in out
    assert outbox_bytes(env.vault) == before
    assert env.jira.count("write") == writes_before

    env.jira.modes["slow_write_sec"] = 0
    code, _out, err = run(capsys, env.vault, "flush", "--retry-uncertain")
    assert code == 0, err
    rec = only(env.vault)
    assert rec["status"] == "done" and rec["last_error"] is None and rec["error_detail"] is None


def test_flush_refuses_foreign_project(env, capsys):
    outbox.append(env.vault, outbox.make_record(action="comment", key="ZZ-1", payload={"body": "밖"}, ts=NOW))
    outbox.append(env.vault, outbox.make_record(action="create", key="ZZ", payload={"summary": "밖", "issuetype": "Task"}, ts=NOW))
    outbox.append(env.vault, outbox.make_record(action="comment", key="KB-12", payload={"body": "안"}, ts=NOW))
    code, out, err = run(capsys, env.vault, "flush")
    assert code == 4
    assert err.count("거부") == 2 and "ZZ" in err
    assert [e["json"] for e in writes(env.jira)] == [{"body": "안"}]
    assert [r["status"] for r in records(env.vault)] == ["pending", "pending", "done"]
    assert "거부 2" in out


def test_outbox_byte_stable_when_nothing_to_do(env, capsys):
    assert run(capsys, env.vault, "flush")[0] == 0
    assert outbox_bytes(env.vault) is None  # 없으면 만들지도 않는다
    assert run(capsys, env.vault, "comment", "KB-12", "--body", "하나")[0] == 0
    before = outbox_bytes(env.vault)
    mtime = outbox.path_for(env.vault).stat().st_mtime_ns
    log_before = len(env.jira.log)
    for args in (("flush",), ("flush", "--retry-failed"), ("flush", "--retry-uncertain"), ("list",), ("list", "--json", "--all")):
        assert run(capsys, env.vault, *args)[0] == 0
    assert outbox_bytes(env.vault) == before
    assert outbox.path_for(env.vault).stat().st_mtime_ns == mtime
    assert len(env.jira.log) == log_before  # 보낼 것이 없으면 Jira에 묻지도 않는다


def test_list_shows_pending_and_json(make_env, capsys):
    env = make_env(down=True)
    run(capsys, env.vault, "comment", "KB-12", "--body", "대기 중")
    code, out, _ = run(capsys, env.vault, "list")
    assert code == 0
    assert "pending" in out and "댓글 KB-12: 대기 중" in out and "대기 1 · 실패 0 · 완료 0" in out
    code, out, _ = run(capsys, env.vault, "list", "--json")
    items = json.loads(out)
    assert [i["status"] for i in items] == ["pending"] and items[0]["payload"] == {"body": "대기 중"}
    env.jira.come_up()
    run(capsys, env.vault, "flush")
    code, out, _ = run(capsys, env.vault, "list", "--json")
    assert json.loads(out) == []
    code, out, _ = run(capsys, env.vault, "list", "--json", "--all")
    assert [i["status"] for i in json.loads(out)] == ["done"]


# ---------------------------------------------------------------- KB_AGENT


@pytest.mark.parametrize(
    "args",
    [
        ("comment", "KB-12", "--body", "x"),
        ("transition", "KB-12", "Done", "--yes"),
        ("assign", "KB-12", "kimcs"),
        ("worklog", "KB-12", "1h"),
        ("create", "KB", "--summary", "x", "--type", "Task", "--yes"),
        ("promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes"),
        ("flush",),
    ],
)
def test_agent_refused_for_writes(env, capsys, monkeypatch, args):
    monkeypatch.setenv("KB_AGENT", "1")
    before = env.daily.read_bytes()
    code, _out, err = run(capsys, env.vault, *args)
    assert code == 7 and "KB_AGENT" in err
    assert env.jira.log == []
    assert outbox_bytes(env.vault) is None
    assert env.daily.read_bytes() == before


def test_agent_allowed_list(env, capsys, monkeypatch):
    monkeypatch.setenv("KB_AGENT", "1")
    code, out, _err = run(capsys, env.vault, "list")
    assert code == 0 and "outbox: 대기 0" in out


def test_agent_refused_through_kb_py(env, run_kb):
    result = run_kb("act", "comment", "KB-12", "--body", "x", env={"KB_AGENT": "1"})
    assert result.returncode == 7, result.stderr
    result = run_kb("act", "list", env={"KB_AGENT": "1"})
    assert result.returncode == 0, result.stderr
    # kb.py를 거치지 않고 직접 실행해도 거부된다
    import subprocess

    direct = subprocess.run(
        [sys.executable, "-X", "utf8", str(Path(jira_act.__file__)), "flush", "--root", str(env.vault)],
        capture_output=True, text=True, encoding="utf-8", env={**_child_env(), "KB_AGENT": "1"}, timeout=60,
    )  # fmt: skip
    assert direct.returncode == 7, direct.stderr
    assert env.jira.log == []


def _child_env() -> dict[str, str]:
    import os

    tests = str(Path(__file__).resolve().parent)
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
    env.update({"PYTHONPATH": tests, "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring", "PYTHONUTF8": "1"})
    return env


# ---------------------------------------------------------------- 잠금


def test_lock_not_held_during_send(make_env, run_kb):
    """하위 프로세스가 보내는 동안 이 프로세스(모의 서버 스레드)가 kb_lock을 0.2초 안에 잡을 수 있어야 한다."""
    probes: list[str] = []
    holder: dict = {}

    def probe(req):
        try:
            with core.kb_lock(holder["vault"], "probe", timeout=0.2):
                probes.append("ok")
        except core.LockBusy:
            probes.append("busy")
        return None

    env = make_env(on_write=probe)
    holder["vault"] = env.vault
    result = run_kb("act", "comment", "KB-12", "--body", "잠금 확인")
    assert result.returncode == 0, result.stderr
    result = run_kb("act", "create", "KB", "--summary", "잠금 확인", "--type", "Task", "--yes")
    assert result.returncode == 0, result.stderr
    assert probes == ["ok", "ok"]
    assert [r["status"] for r in records(env.vault)] == ["done", "done"]


def test_lock_probe_detects_a_holder(vault, tmp_path):
    """대조: 다른 프로세스가 잠금을 쥐고 있으면 같은 시험이 busy를 본다(위 시험이 헛통과가 아님)."""
    import subprocess
    import time

    flag = tmp_path / "held"
    code = (
        "import sys, time, pathlib; sys.path.insert(0, sys.argv[1]);"
        "from lib import core;"
        "ctx = core.kb_lock(sys.argv[2], 'holder'); ctx.__enter__();"
        "pathlib.Path(sys.argv[3]).write_text('1', encoding='utf-8'); time.sleep(3)"
    )
    scripts = str(Path(jira_act.__file__).resolve().parent)
    proc = subprocess.Popen([sys.executable, "-c", code, scripts, str(vault), str(flag)])
    try:
        deadline = time.monotonic() + 20
        while not flag.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        with pytest.raises(core.LockBusy):
            with core.kb_lock(vault, "probe", timeout=0.2):
                pass
    finally:
        proc.wait(timeout=30)
