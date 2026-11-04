"""jira_act (W3.1): outbox 큐 경유 Jira 쓰기. 지시서 §6.3, D26·D52·D53·D57.

각 하위 명령의 성공 경로(요청 본문 모양), 다운 → pending → 복구 → flush → done → 캐시 갱신,
5회 실패 → failed, 401/403 재확인, 불확실(읽기 시간 초과), 전환 후보, 확인 질문, 승격 줄 덧붙이기와
해시 불일치 거부, KB_AGENT 거부, 범위 밖 프로젝트, 아무 일 없을 때 outbox 바이트 불변, 전송 중 잠금 없음.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import daily_prepare
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
    assert [e["path"] for e in env.jira.log][-1] == "/rest/api/2/myself"  # 403도 D52 재확인


def test_403_then_myself_401_stays_pending_exit_2_and_stops(make_env, capsys):
    """403 뒤 /myself가 401이면 토큰 문제: pending(시도 수 그대로), 종료 2, flush 중단."""
    env = make_env(down=True, worklog_denied=True)
    assert run(capsys, env.vault, "worklog", "KB-12", "1h")[0] == 3
    assert run(capsys, env.vault, "comment", "KB-12", "--body", "둘째")[0] == 3

    def expire_token(req):
        req.mock.modes["bad_token"] = TOKEN  # 쓰기는 통과했고, 그 뒤로 토큰이 죽었다
        return None

    env.jira.modes["on_write"] = expire_token
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 2 and "인증 실패" in err and "나머지 1건" in err
    assert [(r["status"], r["attempts"]) for r in records(env.vault)] == [("pending", 1), ("pending", 1)]
    assert "401" in records(env.vault)[0]["last_error"]
    assert [e["path"] for e in env.jira.log][-1] == "/rest/api/2/myself"
    assert env.jira.count("write") == 1


def test_403_then_myself_down_stays_pending_exit_3(make_env, capsys):
    env = make_env(worklog_denied=True, myself_status=503)
    code, _out, _err = run(capsys, env.vault, "worklog", "KB-12", "1h")
    assert code == 3
    rec = only(env.vault)
    assert rec["status"] == "pending" and rec["attempts"] == 1 and "503" in rec["last_error"]
    assert [e["path"] for e in env.jira.log][-1] == "/rest/api/2/myself"


def test_other_4xx_is_failed_with_jira_message_and_not_resent(env, capsys):
    code, _out, err = run(capsys, env.vault, "assign", "KB-12", "nobody")  # 모의 400
    assert code == 1 and "실패" in err
    rec = only(env.vault)
    assert rec["status"] == "failed" and rec["attempts"] == 1
    assert "400" in rec["last_error"] and "does not exist" in rec["last_error"]
    code, _out, _err = run(capsys, env.vault, "comment", "KB-999", "--body", "없는 일감")  # 모의 404
    assert code == 1
    rec = records(env.vault)[1]
    assert rec["status"] == "failed" and rec["attempts"] == 1
    assert "404" in rec["last_error"] and "이슈가 존재하지 않거나" in rec["last_error"]
    sent = env.jira.count("write")
    assert sent == 2
    code, out, _err = run(capsys, env.vault, "flush")
    assert code == 0 and "보낼 항목이 없습니다" in out
    assert env.jira.count("write") == sent  # 그냥 flush는 실패 항목을 다시 보내지 않는다
    assert [r["status"] for r in records(env.vault)] == ["failed", "failed"]


def test_5xx_keeps_pending_and_flush_continues(make_env, capsys):
    env = make_env(down=True)
    for body in ("첫째", "둘째"):
        assert run(capsys, env.vault, "comment", "KB-12", "--body", body)[0] == 3
    env.jira.modes["write_500_times"] = 1
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 3 and "나머지" not in err
    first, second = records(env.vault)
    assert (first["status"], first["attempts"]) == ("pending", 2) and "500" in first["last_error"]
    assert (second["status"], second["attempts"]) == ("done", 2)
    assert [e["json"]["body"] for e in writes(env.jira)] == ["첫째", "둘째"]


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


def test_lock_not_held_during_flush_or_promote(make_env, run_kb, capsys):
    """하위 프로세스 flush(두 건)와 promote: 보내는 동안 잠금이 없고, promote는 줄에 키를 붙인 뒤
    잠금 없이 sync_key(GET)를 한다."""
    probes: list[str] = []
    reads: list[tuple[str, str, bool]] = []
    holder: dict = {}

    def lock_state() -> str:
        try:
            with core.kb_lock(holder["vault"], "probe", timeout=0.2):
                return "ok"
        except core.LockBusy:
            return "busy"

    def probe(req):
        probes.append(lock_state())
        return None

    def read_probe(req):
        if holder.get("watch_reads"):
            linked = "[[KB-16]]" in holder["daily"].read_text(encoding="utf-8")
            reads.append((req.path, lock_state(), linked))
        return None

    env = make_env(down=True, on_write=probe, on_read=read_probe)
    holder.update(vault=env.vault, daily=env.daily)
    for body in ("하나", "둘"):
        assert run(capsys, env.vault, "comment", "KB-12", "--body", body)[0] == 3
    env.jira.come_up()
    result = run_kb("act", "flush")
    assert result.returncode == 0, result.stderr
    assert probes == ["ok", "ok"]
    assert [r["status"] for r in records(env.vault)] == ["done", "done"]

    holder["watch_reads"] = True
    result = run_kb("act", "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes")
    assert result.returncode == 0, result.stderr
    assert probes == ["ok", "ok", "ok"]
    assert reads, "sync_key가 Jira를 읽지 않았다"
    assert all(state == "ok" and linked for _path, state, linked in reads), reads


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


# ---------------------------------------------------------------- 보내는 중 표시(claim): 한 번만 보낸다

SCRIPTS = Path(jira_act.__file__).resolve().parent
TESTS = Path(__file__).resolve().parent


def _spawn(tmp_path: Path, vault: Path, *args: str, extra: dict[str, str] | None = None) -> subprocess.Popen:
    """run_kb와 같은 환경으로 `kb.py …`를 띄우고 기다리지 않는다(동시 실행 시험용)."""
    drop = {"DEEPINFRA_API_KEY", "PYTHONUTF8", "PYTHONIOENCODING"}
    child = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k not in drop and "PROXY" not in k.upper()}
    child.update({
        "PYTHONPATH": str(TESTS), "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
        "KB_FAKE_KEYRING_FILE": str(tmp_path / "keyring.json"), "KB_ROOT": str(vault), "PYTHONUTF8": "1",
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
    })  # fmt: skip
    if "KB_FAKE_NOW" in os.environ:
        child["KB_FAKE_NOW"] = os.environ["KB_FAKE_NOW"]
    child.update(extra or {})
    return subprocess.Popen(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "kb.py"), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        env=child, cwd=str(SCRIPTS.parent),
    )  # fmt: skip


def _bodies(server, key: str = "KB-12") -> list[str]:
    return [c["body"] for c in server.issues[key]["fields"]["comment"]["comments"]]


def _dead_pid() -> int:
    done = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    return int(done.stdout.strip())


def _real_now_iso(seconds_ago: float = 0) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


def test_two_concurrent_flush_processes_send_once(make_env, capsys, tmp_path):
    env = make_env(down=True)
    assert run(capsys, env.vault, "comment", "KB-12", "--body", "한 번만 보내야 함")[0] == 3
    env.jira.come_up()
    env.jira.modes["slow_write_sec"] = 2.5
    procs = [_spawn(tmp_path, env.vault, "act", "flush") for _ in range(2)]
    outputs = [proc.communicate(timeout=120) for proc in procs]
    assert [proc.returncode for proc in procs] == [0, 0], outputs
    assert env.jira.count("write") == 1
    assert _bodies(env.jira).count("한 번만 보내야 함") == 1
    rec = only(env.vault)
    assert rec["status"] == "done" and "pid" not in rec and "claimed_at" not in rec


def test_comment_and_concurrent_flush_send_once(make_env, capsys, tmp_path):
    env = make_env(slow_write_sec=2.5)
    proc = _spawn(tmp_path, env.vault, "act", "comment", "KB-12", "--body", "겹친 전송")
    try:
        deadline = time.monotonic() + 60
        while not records(env.vault):
            assert time.monotonic() < deadline and proc.poll() is None, proc.communicate()
            time.sleep(0.05)
        assert only(env.vault)["status"] == "sending"  # 넣기와 표시가 한 걸음
        code, out, _err = run(capsys, env.vault, "flush")
        assert code == 0 and "보내는 중 1건은 건너뜀" in out
    finally:
        stdout, stderr = proc.communicate(timeout=120)
    assert proc.returncode == 0, stderr
    assert env.jira.count("write") == 1
    assert _bodies(env.jira).count("겹친 전송") == 1
    assert only(env.vault)["status"] == "done"


def test_dead_owner_sending_is_uncertain_skipped_and_listed(make_env, capsys):
    env = make_env()
    record = outbox.make_record(action="comment", key="KB-12", payload={"body": "끊긴 전송"}, ts=NOW)
    record.update(status="sending", attempts=1, claimed_at=_real_now_iso(), pid=_dead_pid())
    outbox.append(env.vault, record)
    before = outbox_bytes(env.vault)

    code, out, _err = run(capsys, env.vault, "flush")
    assert code == 0 and "불확실 1건은 건너뜀" in out
    code, out, _err = run(capsys, env.vault, "flush", "--retry-failed")
    assert code == 0 and "보낼 항목이 없습니다" in out
    assert writes(env.jira) == [] and outbox_bytes(env.vault) == before
    code, _out, err = run(capsys, env.vault, "flush", "--retry-failed", record["id"])
    assert code == 4 and "failed 상태가 아닙니다" in err

    code, out, _err = run(capsys, env.vault, "list")
    assert f"{record['id']} uncertain 시도 1" in out and "보내다 끊김" in out and "불확실 1" in out
    code, out, _err = run(capsys, env.vault, "list", "--json")
    assert json.loads(out)[0]["state"] == "uncertain"

    code, _out, err = run(capsys, env.vault, "flush", "--retry-uncertain", record["id"])
    assert code == 0, err
    rec = only(env.vault)
    assert rec["status"] == "done" and rec["attempts"] == 2 and "pid" not in rec
    assert _bodies(env.jira).count("끊긴 전송") == 1


def test_live_owner_is_sending_and_old_claim_is_stale(make_env, capsys):
    env = make_env()
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        record = outbox.make_record(action="comment", key="KB-12", payload={"body": "보내는 중"}, ts=NOW)
        record.update(status="sending", attempts=1, claimed_at=_real_now_iso(), pid=sleeper.pid)
        outbox.append(env.vault, record)
        code, out, _err = run(capsys, env.vault, "flush", "--retry-uncertain")
        assert code == 0 and "보내는 중 1건은 건너뜀" in out
        code, _out, err = run(capsys, env.vault, "flush", "--retry-uncertain", record["id"])
        assert code == 4 and "uncertain 상태가 아닙니다" in err
        code, out, _err = run(capsys, env.vault, "list")
        assert f"{record['id']} sending" in out
        assert writes(env.jira) == []
    finally:
        sleeper.kill()
        sleeper.wait(timeout=30)
    assert jira_act.stale_after(env.cfg) == 120
    alive_old = {"pid": os.getpid(), "claimed_at": _real_now_iso(seconds_ago=121)}
    alive_new = {"pid": os.getpid(), "claimed_at": _real_now_iso(seconds_ago=5)}
    assert jira_act.claim_is_stale(alive_old, 120) and not jira_act.claim_is_stale(alive_new, 120)
    assert jira_act.claim_is_stale({"pid": _dead_pid(), "claimed_at": _real_now_iso()}, 120)


def test_interrupted_result_write_leaves_sending_not_pending(make_env, capsys, run_kb):
    """보낸 뒤 결과를 적지 못하면(잠금 시간 초과) 기록은 sending으로 남고, 그냥 flush는 다시 보내지 않는다."""
    threads: list[threading.Thread] = []
    holder: dict = {}

    def grab_lock(req):
        started = threading.Event()

        def hold():
            with core.kb_lock(holder["vault"], "holder"):
                started.set()
                time.sleep(4)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        threads.append(thread)
        started.wait(10)
        return None

    env = make_env(on_write=grab_lock)
    holder["vault"] = env.vault
    result = run_kb("act", "comment", "KB-12", "--body", "결과를 못 적음", env={"KB_LOCK_TIMEOUT": "1.5"})
    for thread in threads:
        thread.join(timeout=30)
    assert result.returncode == 6, result.stderr
    assert "Jira에는 보냈지만" in result.stderr and "다시 보내지 마세요" in result.stderr
    rec = only(env.vault)
    assert rec["status"] == "sending" and rec["attempts"] == 1
    env.jira.modes.pop("on_write")
    code, out, _err = run(capsys, env.vault, "flush")
    assert code == 0 and "불확실 1건은 건너뜀" in out
    assert env.jira.count("write") == 1 and _bodies(env.jira).count("결과를 못 적음") == 1


# ---------------------------------------------------------------- 불확실은 실패가 되지 않는다


def test_uncertain_on_fifth_attempt_stays_uncertain(make_env, capsys):
    env = make_env(jira={"timeout": 0.4}, write_500_times=4)
    assert run(capsys, env.vault, "comment", "KB-12", "--body", "다섯째는 느림")[0] == 3
    for attempt in (2, 3, 4):
        assert run(capsys, env.vault, "flush")[0] == 3
        assert only(env.vault)["attempts"] == attempt
    env.jira.modes["slow_write_sec"] = 1.5
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 3 and "불확실" in err and "5번 시도" not in err
    rec = only(env.vault)
    assert (rec["status"], rec["last_error"], rec["attempts"]) == ("pending", "uncertain", 5)
    sent = env.jira.count("write")
    code, out, _err = run(capsys, env.vault, "flush", "--retry-failed")
    assert code == 0 and "보낼 항목이 없습니다" in out
    assert run(capsys, env.vault, "flush", "--retry-failed", rec["id"])[0] == 4
    assert env.jira.count("write") == sent
    assert _bodies(env.jira).count("다섯째는 느림") == 1


def test_retry_uncertain_with_ids_only_sends_those(make_env, capsys):
    env = make_env(jira={"timeout": 0.4}, slow_write_sec=1.5)
    for body in ("가", "나"):
        assert run(capsys, env.vault, "comment", "KB-12", "--body", body)[0] == 3
    first, second = records(env.vault)
    env.jira.modes["slow_write_sec"] = 0
    assert run(capsys, env.vault, "flush", "--retry-uncertain", "없는id")[0] == 4
    code, _out, err = run(capsys, env.vault, "flush", "--retry-uncertain", second["id"])
    assert code == 0, err
    assert [(r["status"], r["last_error"]) for r in records(env.vault)] == [("pending", "uncertain"), ("done", None)]
    assert [e["json"]["body"] for e in writes(env.jira)] == ["가", "나", "나"]


def test_redirect_on_write_is_failed_and_stops_flush(make_env, capsys):
    env = make_env(down=True)
    for body in ("리다이렉트 하나", "리다이렉트 둘"):
        assert run(capsys, env.vault, "comment", "KB-12", "--body", body)[0] == 3
    before = _bodies(env.jira)
    env.jira.modes["redirect_writes"] = True
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 1 and "리다이렉트" in err and "나머지 1건" in err
    first, second = records(env.vault)
    assert first["status"] == "failed" and "https 주소로 고치세요" in first["last_error"]
    assert (second["status"], second["attempts"]) == ("pending", 1)
    assert env.jira.count("write") == 1 and [e["method"] for e in env.jira.log] == ["POST"]  # GET으로 따라가지 않음
    assert _bodies(env.jira) == before


def test_html_200_is_uncertain_not_done(make_env, capsys):
    env = make_env(html_200=True)
    before = _bodies(env.jira)
    code, _out, err = run(capsys, env.vault, "comment", "KB-12", "--body", "화면이 옴")
    assert code == 3 and "불확실" in err
    rec = only(env.vault)
    assert (rec["status"], rec["last_error"]) == ("pending", "uncertain") and "JSON이 아닙니다" in rec["error_detail"]
    assert env.jira.count("write") == 1 and _bodies(env.jira) == before
    code, _out, err = run(capsys, env.vault, "transition", "KB-12", "Done", "--yes")
    assert code == 3 and records(env.vault)[1]["last_error"] == "uncertain"
    assert env.jira.issues["KB-12"]["fields"]["status"]["name"] == "In Progress"
    code, out, _err = run(capsys, env.vault, "flush")
    assert code == 0 and "불확실 2건" in out and env.jira.count("write") == 2


def test_create_201_without_key_is_uncertain(make_env, capsys):
    env = make_env(on_write=lambda req: (201, {}) if req.path == "/rest/api/2/issue" else None)
    code, out, err = run(capsys, env.vault, "create", "KB", "--summary", "키 없는 응답", "--type", "Task", "--yes")
    assert code == 3 and "불확실" in err and "새 일감: " not in out
    rec = only(env.vault)
    assert (rec["status"], rec["last_error"], rec["attempts"]) == ("pending", "uncertain", 1)
    assert "키가 없습니다" in rec["error_detail"]
    for args in (("flush",), ("flush", "--retry-failed")):
        code, out, _err = run(capsys, env.vault, *args)
        assert code == 0 and "보낼 항목이 없습니다" in out
    assert env.jira.count("write") == 1


# ---------------------------------------------------------------- 토큰이 든 본문


def test_token_in_payload_is_refused_everywhere(env, capsys, monkeypatch):
    text = DAILY_TEXT + f"- 09:30 토큰 붙여넣음 {TOKEN} 실수\n"  # 13번째 줄
    env.daily.write_bytes(text.encode("utf-8"))
    asked = answer(monkeypatch, "y")
    cases = [
        ("comment", "KB-12", "--from-line", f"daily/{DAY}.md:13"),
        ("comment", "KB-12", "--body", f"토큰 {TOKEN}"),
        ("create", "KB", "--summary", "제목", "--type", "Task", "--description", f"설명 {TOKEN}", "--yes"),
        ("create", "KB", "--summary", f"제목 {TOKEN}", "--type", "Task"),
        ("promote", "--from", f"daily/{DAY}.md:13", "KB", "--yes"),
        ("promote", "--from", f"daily/{DAY}.md:13", "KB"),
    ]
    for args in cases:
        code, out, err = run(capsys, env.vault, *args)
        assert code == 4, (args, err)
        assert "본문에 토큰이 들어 있어 보내지 않습니다" in err
        assert TOKEN not in out and TOKEN not in err
    assert asked == []  # 확인 질문(제목이 보임)보다 먼저 거부
    assert outbox_bytes(env.vault) is None and writes(env.jira) == []
    assert env.daily.read_bytes() == text.encode("utf-8")
    log = env.vault / ".state" / "logs" / "kb.log"
    assert not log.exists() or TOKEN not in log.read_text(encoding="utf-8")


def test_queued_record_with_token_is_not_sent(env, capsys):
    """예전에 큐에 들어간 기록에 토큰이 있으면 보내지 않고 실패로 둔다. 출력에도 토큰을 보이지 않는다."""
    outbox.append(env.vault, outbox.make_record(action="comment", key="KB-12", payload={"body": f"옛 기록 {TOKEN}"}, ts=NOW))
    code, out, err = run(capsys, env.vault, "flush")
    assert code == 1 and "본문에 토큰이 들어 있어" in err
    assert TOKEN not in out and TOKEN not in err
    assert writes(env.jira) == [] and only(env.vault)["status"] == "failed"


# ---------------------------------------------------------------- promote: 줄 고르기


PROMOTE_TEXT = DAILY_TEXT + (
    "```\n"  # 13
    "- [ ] 코드 안 할 일\n"  # 14
    "```\n"  # 15
    "## 이월\n"  # 16
    "<!-- kb:carryover:start -->\n"  # 17
    "- [ ] 개인: 옮긴 일 (← 2026-11-04)\n"  # 18
    "<!-- kb:carryover:end -->\n"  # 19
    "- \n"  # 20 빈 목록 항목
)


@pytest.mark.parametrize(
    ("lineno", "why"),
    [
        (1, "front matter"), (2, "front matter"), (4, "제목 줄"), (5, "관리 구역 표시"), (6, "관리 구역(today)"),
        (7, "관리 구역 표시"), (13, "코드 블록"), (14, "코드 블록"), (15, "코드 블록"), (17, "관리 구역 표시"),
        (18, "daily/2026-11-04.md"), (20, "빈 줄"),
    ],
)  # fmt: skip
def test_promote_refuses_lines_that_are_not_human_items(env, capsys, lineno, why):
    env.daily.write_bytes(PROMOTE_TEXT.encode("utf-8"))
    code, _out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{lineno}", "KB", "--yes")
    assert code == 4, err
    assert why in err
    assert writes(env.jira) == [] and outbox_bytes(env.vault) is None
    assert env.daily.read_bytes() == PROMOTE_TEXT.encode("utf-8")


def test_promote_fallback_ignores_lines_in_code_fence(make_env, capsys):
    env = make_env(down=True)
    assert run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_REVIEW}", "KB", "--yes")[0] == 3
    edited = env.daily.read_text(encoding="utf-8").replace("PR 리뷰 요청 옴\n", "PR 리뷰 요청 옴 (취소함)\n")
    edited += "```\n- 11:40 개인: PR 리뷰 요청 옴\n```\n"  # 같은 글이 코드 블록 안에만 있다
    env.daily.write_bytes(edited.encode("utf-8"))
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 0, err
    assert "붙이지 않았습니다" in err
    assert env.daily.read_text(encoding="utf-8") == edited
    assert only(env.vault)["result"] == {"key": "KB-16", "linked": False}


@pytest.mark.parametrize("adjacent", [False, True])
def test_promote_duplicate_lines_are_not_guessed(make_env, capsys, adjacent):
    """같은 줄이 여럿이고 파일이 바뀌었으면 어느 줄인지 모르므로 붙이지 않는다(linked:false, daily 그대로)."""
    env = make_env(down=True)
    dup = "- [ ] 개인: 11월 회의실 예약\n"  # 12번째 줄과 같은 줄
    text = DAILY_TEXT + ("" if adjacent else "- 13:00 사이 줄\n") + dup
    env.daily.write_bytes(text.encode("utf-8"))
    target = 13 if adjacent else 14
    assert run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{target}", "KB", "--yes")[0] == 3
    inserted = text.replace("## 기록\n", "## 기록\n- 08:00 새로 끼운 줄\n")
    env.daily.write_bytes(inserted.encode("utf-8"))
    env.jira.come_up()
    code, _out, err = run(capsys, env.vault, "flush")
    assert code == 0, err
    assert "어느 줄인지 모릅니다" in err
    assert env.daily.read_text(encoding="utf-8") == inserted
    assert only(env.vault)["result"] == {"key": "KB-16", "linked": False}


def test_promote_duplicate_line_unchanged_file_links_exact_line(env, capsys):
    dup = "- [ ] 개인: 11월 회의실 예약\n"
    text = DAILY_TEXT + dup  # 12·13번째 줄이 같다
    env.daily.write_bytes(text.encode("utf-8"))
    code, _out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:13", "KB", "--yes")
    assert code == 0, err
    lines = env.daily.read_text(encoding="utf-8").split("\n")
    assert lines[11] == "- [ ] 개인: 11월 회의실 예약" and lines[12] == "- [ ] 개인: 11월 회의실 예약 [[KB-16]]"


# ---------------------------------------------------------------- promote와 이월(D53)


def _day_text(vault: Path, day: str) -> str:
    return (vault / "daily" / f"{day}.md").read_text(encoding="utf-8")


def test_pending_promote_is_not_carried_over(make_env, capsys):
    env = make_env(down=True)
    assert run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_TODO}", "KB", "--yes")[0] == 3
    daily_prepare.run(env.cfg, "2026-11-06")
    assert "회의실 예약" not in _day_text(env.vault, "2026-11-06")
    assert "- [ ] 개인: 11월 회의실 예약\n" in _day_text(env.vault, DAY)  # [>] 표시도 하지 않는다
    env.jira.come_up()
    assert run(capsys, env.vault, "flush")[0] == 0
    assert "- [ ] 개인: 11월 회의실 예약 [[KB-16]]\n" in _day_text(env.vault, DAY)
    daily_prepare.run(env.cfg, "2026-11-07")
    assert "회의실 예약" not in _day_text(env.vault, "2026-11-07")


def test_promote_after_carryover_links_the_carried_copy(env, capsys):
    daily_prepare.run(env.cfg, "2026-11-06")
    assert "- [ ] 개인: 11월 회의실 예약 (← 2026-11-05)\n" in _day_text(env.vault, "2026-11-06")
    code, out, err = run(capsys, env.vault, "promote", "--from", f"daily/{DAY}.md:{LINE_TODO}", "KB", "--yes")
    assert code == 0, err
    assert "이월 사본 1곳" in out
    assert "- [>] 개인: 11월 회의실 예약 (2026-11-06로 이월) [[KB-16]]\n" in _day_text(env.vault, DAY)
    assert "- [ ] 개인: 11월 회의실 예약 (← 2026-11-05) [[KB-16]]\n" in _day_text(env.vault, "2026-11-06")
    daily_prepare.run(env.cfg, "2026-11-07")
    assert "회의실 예약" not in _day_text(env.vault, "2026-11-07")


# ---------------------------------------------------------------- outbox 읽기: 공유 위반


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 공유 위반")
def test_outbox_read_retries_while_another_holder_locks_the_file(vault, monkeypatch):
    import msvcrt

    record = outbox.append(vault, outbox.make_record(action="assign", key="KB-1", payload={}, ts=NOW))
    path = outbox.path_for(vault)

    def hold(seconds: float, locked: threading.Event) -> None:
        size = path.stat().st_size
        with open(path, "rb+") as fh:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, size)
            locked.set()
            time.sleep(seconds)
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, size)

    locked = threading.Event()
    thread = threading.Thread(target=hold, args=(0.4, locked))
    thread.start()
    assert locked.wait(10)
    assert [r["id"] for r in outbox.records(vault)] == [record["id"]]  # 풀릴 때까지 다시 읽는다
    assert outbox.update(vault, record["id"], {"status": "done"})["status"] == "done"  # 그 뒤 다시 쓰기
    thread.join(timeout=10)
    assert outbox.get(vault, record["id"])["status"] == "done"

    monkeypatch.setattr(outbox, "READ_RETRIES", 1)
    locked = threading.Event()
    thread = threading.Thread(target=hold, args=(1.5, locked))
    thread.start()
    assert locked.wait(10)
    try:
        with pytest.raises(core.FileBusy):
            outbox.records(vault)
    finally:
        thread.join(timeout=10)
