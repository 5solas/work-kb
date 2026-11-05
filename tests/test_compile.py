"""scripts/compile.py (W4.4) 테스트: T1~T4, 원장·멱등, dry-run, 회사 차단, daily 불변, proposals.

LLM은 tests/mock_llm.py의 canned 모드(시스템 메시지 첫 줄 `KB-TASK: <작업>`으로 답을 고름)를 쓴다.
답은 tests/fixtures/llm/*.json, 볼트는 tests/fixtures/llm/vault(허구 데이터).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import socket
from pathlib import Path

import pytest

import compile as kb_compile
import link
import mock_llm
from lib import config, core, frontmatter, managed, notes, prompts
from lib.llm_client import SchemaError

FIX = Path(__file__).resolve().parent / "fixtures" / "llm"
DAY4 = "daily/2026-11-04.md"
DAY5 = "daily/2026-11-05.md"
LINE_L1 = "- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로 저장하기로."


def canned(**override) -> dict[str, str]:
    replies = {task: (FIX / f"{task}.json").read_text(encoding="utf-8") for task in kb_compile.TASKS}
    for task, value in override.items():
        replies[task] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return replies


@pytest.fixture
def kb(vault, fake_now) -> Path:
    shutil.copytree(FIX / "vault", vault, dirs_exist_ok=True)
    (vault / "raw" / "assets" / "2026-11-04-1420-whiteboard.png").write_bytes(b"\x89PNG\r\n\x1a\n fictional")
    fake_now("2026-11-05T18:30:00+09:00")
    return vault


@pytest.fixture
def llm():
    started: list = []

    def _start(**modes):
        mock = mock_llm.start(**modes)
        started.append(mock)
        return mock

    yield _start
    for mock in started:
        mock.stop()


@pytest.fixture
def dead_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/v1"


def url(mock) -> str:
    return f"{mock.base_url}/v1"


def make_cfg(root: Path, profiles: dict, *, environment="home", enabled=True, order=("main",)) -> config.Config:
    data = {
        "environment": environment,
        "jira": {"project_keys": ["KB", "OPS"]},
        "llm": {"enabled": enabled, "profiles": profiles, "fallback_order": list(order)},
    }
    config.save_local(root, data)
    return config.load(root)


def main_cfg(root: Path, mock, **kw) -> config.Config:
    return make_cfg(root, {"main": {"base_url": url(mock), "model": "mock"}}, **kw)


def files(root: Path, subdirs=("daily", "inbox", "raw")) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for sub in subdirs:
        base = root / sub
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def whole_tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def ledger(root: Path) -> dict:
    return json.loads((root / ".state" / "compile.json").read_text(encoding="utf-8"))


def text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def region(path: Path, name: str) -> str:
    return managed.read_region(path.read_bytes(), name).decode("utf-8")


def task_requests(mock, task: str) -> list[dict]:
    out = []
    for entry in mock.log:
        body = entry.get("json")
        if entry["path"].endswith("/chat/completions") and isinstance(body, dict):
            system = body["messages"][0]["content"]
            if isinstance(system, str) and system.startswith(f"KB-TASK: {task}\n"):
                out.append(body)
    return out


def user_text(body: dict) -> str:
    return body["messages"][-1]["content"]


def run(cfg, **kw):
    lines: list[str] = []
    result = kb_compile.run(cfg, out=lines.append, **kw)
    result["output"] = "\n".join(lines)
    return result


# ---------------------------------------------------------------- 전체 흐름


def test_full_run_writes_all_tasks_and_keeps_daily_bytes(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    link.run(cfg)
    before = files(kb)

    result = run(cfg)

    assert result["status"] == "ok", result
    assert not result["problems"], result["problems"]
    assert files(kb) == before  # daily·inbox·raw 바이트 불변
    assert mock.counts["chat"] == 1 + 2 + 4 + 1  # T1 묶음 1 · T2 하루 2 · T3 엔티티 4 · T4 1

    led = ledger(kb)
    # T1: 두 날 6줄을 한 요청으로, 줄마다 원장에 태그
    tags = {k: v["tag"] for k, v in led["tag"].items() if v["status"] == "ok"}
    assert len(tags) == 6
    assert tags[f"{DAY4}#{kb_compile.line_hashes(LINE_L1)[0]}"] == "결정"
    assert kb_compile.line_hashes(LINE_L1)[0] == core.line_hash(LINE_L1.replace("[[KB-12]]", "KB-12"))
    assert len(task_requests(mock, "tag")) == 1
    tag_user = user_text(task_requests(mock, "tag")[0])
    assert "[L1] 2026-11-04 09:12 [[KB-12]]" in tag_user and "[L6] 2026-11-05 10:15" in tag_user
    assert "고정 태그: 회의, 결정, 요청, 이슈, 학습, 아이디어, 개인, 참고, 일정, 리뷰" in tag_user

    # T2: decisions.md(제안, 출처, src), 걸러진 출력
    decisions = text(kb / "wiki" / "decisions.md")
    assert "## 2026-11-04 캐시 updated 시각은 UTC로 저장\n- 상태: 제안\n" in decisions
    assert "- 관련: [[KB-12]] · [[projects/KB]] · @김철수\n" in decisions
    assert "- 내용: jira 캐시의 updated 값을 UTC로 저장한다.\n- 근거: 시간대 혼동을 막기 위해서다.\n" in decisions
    assert "- 출처: [[2026-11-04]] 09:12 <!-- src:" in decisions
    assert "ZZ-9" not in decisions and "없는사람" not in decisions  # 범위 밖 키, 입력에 없는 사람
    assert "입력에 없는 줄" not in decisions  # source_line L99는 버림
    assert decisions.count("- 상태: 제안") == 2  # 두 날 각각
    actions = text(kb / "wiki" / "actions.md")
    assert "- [ ] 배포 체크리스트 초안 작성 · 관련 [[OPS-3]] · 출처 [[2026-11-04]] 11:40 <!-- src:" in actions
    assert "- [ ] 캐시 만료 24시간 반영 · 관련 [[KB-12]] · 출처 [[inbox/2026-11-04-1420-whiteboard]] <!-- src:" in actions
    assert "경비 정산" not in actions  # 체크박스 줄의 할 일은 버림
    assert actions.count("- [ ] ") == 3
    assert led["decisions"][DAY4]["status"] == "ok" and led["decisions"][DAY5]["status"] == "ok"
    assert result["stats"]["decisions"]["dropped"] >= 1
    assert result["stats"]["decisions"]["rejected_keys"] >= 1

    # T3: summary 구역 통째 교체, 출처는 언급 줄의 링크
    kb_summary = region(kb / "wiki" / "projects" / "KB.md", "summary")
    assert kb_summary.startswith("최근 언급은 캐시 front matter 정리와 배포 준비에 관한 것이다.")
    assert "- 출처: [[2026-11-04]] 09:12\n" in kb_summary
    assert "- 갱신: 2026-11-05 (main)" in kb_summary
    assert "최근 언급은" in region(kb / "wiki" / "people" / "김철수.md", "summary")
    assert set(led["entity_summary"]) == {"wiki/projects/KB.md", "wiki/projects/OPS.md", "wiki/people/김철수.md", "wiki/people/이영희.md"}
    project_req = [b for b in task_requests(mock, "entity_summary") if "대상: 프로젝트 KB" in user_text(b)]
    assert project_req and "## 미완료 일감 표" in user_text(project_req[0])
    person_req = [b for b in task_requests(mock, "entity_summary") if "대상: 사람 김철수" in user_text(b)]
    assert person_req and "개인 정보는 쓰지 않는다" in user_text(person_req[0])

    # T4: 번호가 붙은 제안
    proposals = region(kb / "wiki" / "_proposals.md", "proposals")
    assert proposals.startswith("- [1] 캐시 설계 · 제안 · 2026-11-05\n  - 이유: ")
    assert "  - 예: [[2026-11-04]] 09:12 · [[2026-11-05]] 10:15" in proposals
    assert led["_proposals"]["items"]["1"]["status"] == "제안"
    assert "태그 6줄" in result["output"] and "결정 2건" in result["output"]


def test_t2_request_has_ids_sidecar_jira_summary_and_no_carried_lines(kb, llm):
    (kb / "daily" / "2026-11-04.md").write_bytes(
        (FIX / "vault" / "daily" / "2026-11-04.md").read_bytes() + "- [>] 개인: 지난 일 (2026-11-05로 이월)\n".encode("utf-8")
    )
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["decisions"], day="2026-11-04")
    [body] = task_requests(mock, "decisions")
    user = user_text(body)
    assert "날짜: 2026-11-04\n프로젝트 키: KB, OPS" in user
    assert "[L1] 09:12 [[KB-12]] @김철수 와 front matter 논의." in user
    assert "[L3] [ ] 개인: 경비 정산" in user
    assert "지난 일" not in user  # [>] 이월 줄은 입력에서 뺀다
    assert "[X1] (2026-11-04-1420-whiteboard) 캐시 만료는 24시간으로 한다 [[KB-12]]" in user
    assert "[X2] (2026-11-04-1420-whiteboard) 요약: 화이트보드 회의 메모" in user
    assert "- KB-12: 캐시 front matter 확정 · In Progress · 홍길동 · 2026-11-06" in user
    system = body["messages"][0]["content"]
    assert system.startswith("KB-TASK: decisions\n\n## 역할")
    assert "# 작업: 결정과 할 일 (T2)" in system


# ---------------------------------------------------------------- 멱등·재실행


def test_second_run_makes_no_requests(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    link.run(cfg)
    run(cfg)
    counts = dict(mock.counts)
    snapshot = whole_tree(kb)

    result = run(cfg)

    assert result["status"] == "nothing"
    assert mock.counts == counts  # /models도 부르지 않는다
    after = {k: v for k, v in whole_tree(kb).items() if not k.startswith(".state/logs/")}
    assert after == {k: v for k, v in snapshot.items() if not k.startswith(".state/logs/")}


def test_new_line_reruns_only_that_day_and_marks_consumed_lines(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    link.run(cfg)
    run(cfg)
    decisions_before = text(kb / "wiki" / "decisions.md")
    actions_before = text(kb / "wiki" / "actions.md")
    path = kb / "daily" / "2026-11-04.md"
    path.write_bytes(path.read_bytes() + "- 15:00 캐시 키 이름은 다음 회의에서\n".encode("utf-8"))
    chats = mock.counts["chat"]

    result = run(cfg)

    assert mock.counts["chat"] - chats == 2  # T1 새 줄 1 + T2 그날 1 (T3 새 언급 없음, T4 기한 전)
    assert len(task_requests(mock, "tag")) == 2
    last_tag = user_text(task_requests(mock, "tag")[-1])
    assert "캐시 키 이름은" in last_tag and "09:12" not in last_tag
    last_t2 = user_text(task_requests(mock, "decisions")[-1])
    assert "[L1] (이미 반영) 09:12 [[KB-12]]" in last_t2
    assert "[L2] (이미 반영) 11:40" in last_t2
    assert "[X1] (이미 반영) (2026-11-04-1420-whiteboard)" in last_t2
    assert "[L5] 15:00 캐시 키 이름은" in last_t2
    # 같은 답이 다시 와도 (파일, 줄 해시)로 중복 없음
    assert text(kb / "wiki" / "decisions.md") == decisions_before
    assert text(kb / "wiki" / "actions.md") == actions_before
    assert result["stats"]["decisions"]["decisions"] == 0


def test_force_resends_but_does_not_duplicate(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["decisions"], day="2026-11-04")
    before = text(kb / "wiki" / "decisions.md")
    run(cfg, tasks=["decisions"], day="2026-11-04", force=True)
    assert len(task_requests(mock, "decisions")) == 2
    assert text(kb / "wiki" / "decisions.md") == before


def test_skipped_three_times_gives_up(kb, llm):
    mock = llm(canned=canned(tag={"tags": {"L1": "없는태그"}}))
    cfg = main_cfg(kb, mock)
    key = f"{DAY5}#{kb_compile.line_hashes('- 09:30 [[OPS-3]] 막힘, @이영희 확인 대기')[0]}"
    for n in (1, 2, 3):
        result = run(cfg, tasks=["tag"], day="2026-11-05")
        record = ledger(kb)["tag"][key]
        assert record["skips"] == n
        assert record["status"] == ("gave_up" if n == 3 else "skipped")
    assert mock.counts["chat"] == 3 * 2  # 스키마 실패는 같은 프로필에서 한 번 더
    assert result["stats"]["tag"]["gave_up"] == 2
    assert any("포기" in p for p in result["problems"])
    counts = dict(mock.counts)
    assert run(cfg, tasks=["tag"], day="2026-11-05")["status"] == "nothing"
    assert mock.counts == counts
    # --force는 포기한 단위도 다시 보낸다
    run(cfg, tasks=["tag"], day="2026-11-05", force=True)
    assert mock.counts["chat"] == counts["chat"] + 2


# ---------------------------------------------------------------- LLM 동작


def test_broken_json_once_retries_same_profile(kb, llm):
    mock = llm(canned=canned(), broken_json_once=True)
    cfg = main_cfg(kb, mock)
    result = run(cfg, tasks=["tag"], day="2026-11-05")
    assert mock.counts["chat"] == 2
    assert result["stats"]["tag"]["ok"] == 2
    assert all(v["status"] == "ok" and v["profile"] == "main" for v in ledger(kb)["tag"].values())


@pytest.mark.parametrize("trace_field", ["think_tag", "orphan_think", "reasoning_content", "reasoning"])
def test_thinking_trace_is_stripped(kb, llm, trace_field):
    mock = llm(canned=canned(), default_thinking=True, trace_field=trace_field)
    cfg = main_cfg(kb, mock)
    result = run(cfg, tasks=["tag", "decisions"], day="2026-11-04")
    assert mock.counts["chat"] == 2, result
    led = ledger(kb)
    assert all(v["status"] == "ok" for v in led["tag"].values())
    assert led["decisions"][DAY4]["status"] == "ok"
    decisions = text(kb / "wiki" / "decisions.md")
    assert "생각" not in decisions and "<think>" not in decisions


def test_fallback_profile_is_used_when_main_is_down(kb, llm, dead_url):
    mock = llm(canned=canned())
    cfg = make_cfg(
        kb,
        {"main": {"base_url": dead_url, "model": "mock"}, "fallback": {"base_url": url(mock), "model": "mock"}},
        order=("main", "fallback"),
    )
    result = run(cfg, tasks=["tag", "decisions"], day="2026-11-04")
    assert result["status"] == "ok"
    led = ledger(kb)
    assert {v["profile"] for v in led["tag"].values()} == {"fallback"}
    assert led["decisions"][DAY4]["profile"] == "fallback"
    assert "## 2026-11-04 캐시 updated 시각은 UTC로 저장" in text(kb / "wiki" / "decisions.md")


def test_tag_batches_respect_output_budget(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    client = kb_compile.LlmClient(cfg)  # 밖에서 넘긴 클라이언트: 줄당 64 그대로 → 64 // 16 = 4줄씩
    kb_compile.run(cfg, tasks=["tag"], client=client, out=lambda line: None)
    requests = task_requests(mock, "tag")
    assert [user_text(b).count("\n[L") for b in requests] == [4, 2]
    assert all(b["max_tokens"] == 64 for b in requests)
    # compile이 직접 만든 클라이언트는 줄당 × 40
    mock2 = llm(canned=canned())
    cfg2 = main_cfg(kb, mock2)
    run(cfg2, tasks=["tag"], force=True)
    [body] = task_requests(mock2, "tag")
    assert body["max_tokens"] == 64 * kb_compile.TAG_BATCH_LINES


# ---------------------------------------------------------------- 꺼짐·연결 없음·회사 차단


def test_disabled_logs_one_line_and_makes_no_requests(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock, enabled=False)
    result = run(cfg)
    assert result["status"] == "disabled"
    assert result["output"].count("\n") == 0 and "llm.enabled: false" in result["output"]
    assert mock.counts == {"models": 0, "chat": 0}
    assert not (kb / ".state" / "compile.json").exists()
    log = (kb / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert log.count("compile:") == 1


def test_disabled_via_cli_exits_zero(kb, llm, run_kb):
    mock = llm(canned=canned())
    main_cfg(kb, mock, enabled=False)
    proc = run_kb("compile")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().count("\n") == 0 and "꺼져" in proc.stdout
    assert mock.counts == {"models": 0, "chat": 0}


def test_no_connected_profile_exits_zero_without_writing(kb, dead_url):
    cfg = make_cfg(kb, {"main": {"base_url": dead_url, "model": "mock"}})
    before = files(kb, ("daily", "inbox", "raw", "wiki"))
    result = run(cfg)
    assert result["status"] == "no_profile"
    assert "연결되는 LLM 프로필이 없어" in result["output"]
    assert files(kb, ("daily", "inbox", "raw", "wiki")) == before
    assert not (kb / ".state" / "compile.json").exists()


@pytest.mark.parametrize("enabled", [True, False])
def test_company_public_host_is_refused(kb, enabled):
    cfg = make_cfg(kb, {"main": {"base_url": "https://api.deepinfra.com/v1/openai", "model": "x"}}, environment="company", enabled=enabled)
    with pytest.raises(core.PublicHostRefused):
        kb_compile.run(cfg, out=lambda line: None)
    assert not (kb / ".state" / "compile.json").exists()


def test_company_public_host_cli_exit_5(kb, run_kb):
    make_cfg(kb, {"main": {"base_url": "https://api.deepinfra.com/v1/openai", "model": "x"}}, environment="company")
    before = whole_tree(kb)
    proc = run_kb("compile", "--date", "2026-11-04")
    assert proc.returncode == 5, (proc.stdout, proc.stderr)
    assert "공개 LLM 호스트" in proc.stderr
    assert whole_tree(kb) == before


# ---------------------------------------------------------------- dry-run


def test_dry_run_calls_llm_but_writes_nothing(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    link.run(cfg)
    before = whole_tree(kb)

    result = run(cfg, dry_run=True)

    assert mock.counts["chat"] == 8
    assert whole_tree(kb) == before  # 원장·로그·위키 모두 그대로
    diff = result["diff"]
    assert "+++ b/wiki/decisions.md" in diff and "+- 상태: 제안" in diff
    assert "+++ b/wiki/actions.md" in diff
    assert "+++ b/wiki/projects/KB.md" in diff and "+최근 언급은" in diff
    assert "+++ b/wiki/_proposals.md" in diff and "+- [1] 캐시 설계" in diff
    assert "태그 [[2026-11-04]] 09:12 → 결정" in result["output"]
    assert "+++ b/wiki/decisions.md" in result["output"]


# ---------------------------------------------------------------- 쓰기 안전


def test_broken_markers_skip_the_file_and_keep_ledger_pending(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    page = kb / "wiki" / "decisions.md"
    page.write_bytes("# 결정 로그\n\n<!-- kb:decisions:start -->\n깨진 구역\n".encode("utf-8"))
    before = page.read_bytes()
    result = run(cfg, tasks=["decisions"], day="2026-11-04")
    assert page.read_bytes() == before
    assert any("decisions.md" in p or "결정" in p for p in result["problems"])
    assert DAY4 not in ledger_or_empty(kb).get("decisions", {})
    assert not (kb / "wiki" / "actions.md").exists()  # 한 단위의 편집은 전부 아니면 전무


def ledger_or_empty(root: Path) -> dict:
    path = root / ".state" / "compile.json"
    return ledger(root) if path.exists() else {}


def test_entity_summary_unresolved_source_is_schema_failure(kb, llm):
    mock = llm(canned=canned(entity_summary={"summary": "근거 없는 요약이다.", "sources": ["M77"]}))
    cfg = main_cfg(kb, mock)
    link.run(cfg)
    before = text(kb / "wiki" / "projects" / "KB.md")
    run(cfg, tasks=["entity_summary"])
    assert text(kb / "wiki" / "projects" / "KB.md") == before
    led = ledger(kb)["entity_summary"]
    assert {v["status"] for v in led.values()} == {"skipped"}
    assert mock.counts["chat"] == 4 * 2


def test_entity_summary_jira_key_source_and_prior_summary(kb, llm):
    mock = llm(canned=canned(entity_summary={"summary": "KB-12 리뷰가 진행 중이다.", "sources": ["KB-12"]}))
    cfg = main_cfg(kb, mock)
    link.run(cfg)
    page = kb / "wiki" / "projects" / "KB.md"
    page.write_bytes(managed.replace_region(page.read_bytes(), "summary", "사람이 둔 이전 요약.\n\n- 출처: [[2026-10-01]] 10:00"))
    run(cfg, tasks=["entity_summary"])
    body = region(page, "summary")
    assert body.startswith("KB-12 리뷰가 진행 중이다.")
    assert "- 출처: [[KB-12]]" in body
    [req] = [b for b in task_requests(mock, "entity_summary") if "대상: 프로젝트 KB" in user_text(b)]
    assert "[S1] 사람이 둔 이전 요약." in user_text(req)
    assert "[[2026-10-01]]" not in user_text(req)  # 출처 줄은 요약 본문이 아니다


def test_entity_summary_only_new_mentions_are_sent(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    link.run(cfg)
    run(cfg, tasks=["entity_summary"])
    path = kb / "daily" / "2026-11-05.md"
    path.write_bytes(path.read_bytes() + "- 17:10 [[KB-12]] 캐시 만료 테스트 통과\n".encode("utf-8"))
    link.run(cfg)
    run(cfg, tasks=["entity_summary"])
    requests = [b for b in task_requests(mock, "entity_summary") if "대상: 프로젝트 KB" in user_text(b)]
    assert len(requests) == 2
    second = user_text(requests[-1])
    assert "[M1] [[2026-11-05]] 17:10" in second and "09:12" not in second
    assert "[S1] 최근 언급은" in second


# ---------------------------------------------------------------- T4 일정과 제안


def test_topic_proposal_runs_weekly_or_on_request(kb, llm, fake_now):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["tag", "topic_proposal"])  # 직접 지정
    assert len(task_requests(mock, "topic_proposal")) == 1
    path = kb / "daily" / "2026-11-05.md"
    path.write_bytes(path.read_bytes() + "- 18:00 캐시 정리 계속\n".encode("utf-8"))

    run(cfg)  # 기본: 마지막 성공 뒤 7일이 안 됐다
    assert len(task_requests(mock, "topic_proposal")) == 1

    run(cfg, tasks=["topic_proposal"])  # 직접 지정 + 새 줄
    assert len(task_requests(mock, "topic_proposal")) == 2
    items = ledger(kb)["_proposals"]["items"]
    assert list(items) == ["1"]  # 같은 이름은 같은 번호

    run(cfg, tasks=["topic_proposal"])  # 바뀐 것이 없으면 다시 보내지 않는다
    assert len(task_requests(mock, "topic_proposal")) == 2

    fake_now("2026-11-13T09:00:00+09:00")
    run(cfg)
    assert len(task_requests(mock, "topic_proposal")) == 3
    user = user_text(task_requests(mock, "topic_proposal")[-1])
    assert user.startswith("기간: 2026-10-31 ~ 2026-11-13")
    assert "[L1] 2026-11-04 09:12" in user


def test_topic_proposal_skips_existing_topic_and_rejects_unknown_merge(kb, llm):
    cfg0 = make_cfg(kb, {})
    notes.ensure_page(cfg0, "topic", "캐시 설계")
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["topic_proposal"])
    assert "(대기 중인 제안 없음)" in region(kb / "wiki" / "_proposals.md", "proposals")
    assert not ledger(kb)["_proposals"]["items"]
    assert "캐시 설계" in user_text(task_requests(mock, "topic_proposal")[0])  # 기존 주제를 알려 준다

    bad = {"proposals": [{"name": "배포", "why": "x", "sample_sources": ["L1"], "merge_with": "없는주제"}]}
    mock2 = llm(canned=canned(topic_proposal=bad))
    cfg2 = main_cfg(kb, mock2)
    run(cfg2, tasks=["topic_proposal"], force=True)
    assert ledger(kb)["topic_proposal"]["2026-11-05"]["status"] == "skipped"
    assert mock2.counts["chat"] == 2


def test_proposals_list_accept_and_agent_refusal(kb, llm, run_kb):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["topic_proposal"])

    proc = run_kb("proposals", "list", env={"KB_AGENT": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "[1] 캐시 설계 — 캐시 구조와 시각 저장 방식이 여러 날 논의되었다." in proc.stdout

    proc = run_kb("proposals", "accept", "1", env={"KB_AGENT": "1"})
    assert proc.returncode == 7
    assert not (kb / "wiki" / "topics" / "캐시 설계.md").exists()

    proc = run_kb("proposals", "accept", "--root", str(kb), "1")  # Emacs 인수 순서
    assert proc.returncode == 0, proc.stderr
    page = kb / "wiki" / "topics" / "캐시 설계.md"
    fields = frontmatter.parse(page.read_bytes())
    assert fields["type"] == "topic" and fields["name"] == "캐시 설계" and fields["aliases"] == []
    assert ledger(kb)["_proposals"]["items"]["1"]["status"] == "승인"
    body = region(kb / "wiki" / "_proposals.md", "proposals")
    assert "(대기 중인 제안 없음)" in body and "처리됨 1건" in body

    proc = run_kb("proposals", "accept", "1")
    assert proc.returncode == 4 and "이미 처리" in proc.stderr
    proc = run_kb("proposals", "accept", "9")
    assert proc.returncode == 4
    proc = run_kb("proposals", "list")
    assert "대기 중인 주제 제안이 없습니다" in proc.stdout


def test_accept_merge_with_appends_alias(kb, llm):
    cfg0 = make_cfg(kb, {})
    target = notes.ensure_page(cfg0, "topic", "캐시")
    reply = {"proposals": [
        {"name": "캐시 설계", "why": "캐시와 같은 주제다.", "sample_sources": ["L1"], "merge_with": "캐시"},
        {"name": "배포 [준비]", "why": "배포 이야기가 반복된다.", "sample_sources": ["L2", "L5"], "merge_with": None},
    ]}
    mock = llm(canned=canned(topic_proposal=reply))
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["topic_proposal"])
    body = region(kb / "wiki" / "_proposals.md", "proposals")
    assert "- [1] 캐시 설계 · 제안" in body and "  - 합치기: [[topics/캐시|캐시]]" in body
    assert "- [2] 배포 준비 · 제안" in body  # 위키링크에 못 쓰는 문자는 뺀다

    done = kb_compile.accept(cfg, "all", out=lambda line: None)
    assert done == ["wiki/topics/캐시.md", "wiki/topics/배포 준비.md"]
    assert frontmatter.parse(target.read_bytes())["aliases"] == ["캐시 설계"]
    assert not (kb / "wiki" / "topics" / "캐시 설계.md").exists()
    assert (kb / "wiki" / "topics" / "배포 준비.md").exists()
    assert kb_compile.accept(cfg, "all", out=lambda line: None) == []


# ---------------------------------------------------------------- CLI


def test_cli_compile_with_mock(kb, llm, run_kb):
    mock = llm(canned=canned())
    main_cfg(kb, mock)
    before = files(kb)
    proc = run_kb("compile", "--date", "2026-11-04", "--task", "decisions")
    assert proc.returncode == 0, proc.stderr
    assert "결정 1건" in proc.stdout and "할 일 2건" in proc.stdout
    assert "- 상태: 제안" in text(kb / "wiki" / "decisions.md")
    assert files(kb) == before


@pytest.mark.parametrize(
    "args",
    [("--date", "2026-13-01"), ("--date", "2026-11-04", "--since", "3"), ("--task", "nosuch"), ("--since", "-1")],
)
def test_cli_usage_errors_exit_4(kb, run_kb, args):
    make_cfg(kb, {}, enabled=True)
    proc = run_kb("compile", *args)
    assert proc.returncode == 4, (proc.stdout, proc.stderr)


def test_run_rejects_unknown_task(kb):
    cfg = make_cfg(kb, {})
    with pytest.raises(core.ConfigError):
        kb_compile.run(cfg, tasks=["contradictions"], out=lambda line: None)


# ---------------------------------------------------------------- 스키마·프롬프트·도우미


@pytest.mark.parametrize(
    "task, data",
    [
        ("tag", {"tags": {"L1": "회의"}}),
        ("tag", {"tags": {"L1": "#결정 "}}),
        ("decisions", {"decisions": [], "actions": []}),
        ("decisions", {"decisions": [{"title": "t", "content": "", "source_line": "L1"}], "actions": [{"text": "a", "source_line": "X2", "related": ["KB-1"]}]}),
        ("entity_summary", {"summary": "요약.", "sources": ["M1"]}),
        ("topic_proposal", {"proposals": []}),
        ("topic_proposal", {"proposals": [{"name": "n", "why": "w", "sample_sources": ["L1"], "merge_with": None}]}),
    ],
)
def test_schemas_accept(task, data):
    kb_compile.SCHEMAS[task](data)


@pytest.mark.parametrize(
    "task, data",
    [
        ("tag", {}),
        ("tag", {"tags": {}}),
        ("tag", {"tags": {"L1": "없는태그"}}),
        ("tag", {"tags": {"L1": 3}}),
        ("tag", {"tag": "회의"}),
        ("decisions", {"decisions": []}),
        ("decisions", {"decisions": {}, "actions": []}),
        ("decisions", {"decisions": [{"title": "", "content": "c", "source_line": "L1"}], "actions": []}),
        ("decisions", {"decisions": [{"title": "t", "content": "c"}], "actions": []}),
        ("decisions", {"decisions": [], "actions": [{"text": "a", "source_line": 1}]}),
        ("decisions", {"decisions": [], "actions": [{"text": "a", "source_line": "L1", "related": "KB-1"}]}),
        ("entity_summary", {"summary": "", "sources": ["M1"]}),
        ("entity_summary", {"summary": "s", "sources": []}),
        ("entity_summary", {"summary": "s"}),
        ("topic_proposal", {"proposals": {}}),
        ("topic_proposal", {"proposals": [{"name": "n", "why": "w", "sample_sources": []}]}),
        ("topic_proposal", {"proposals": [{"name": "n", "sample_sources": ["L1"], "merge_with": 3}]}),
        ("topic_proposal", {"ok": True}),
    ],
)
def test_schemas_reject(task, data):
    with pytest.raises(SchemaError):
        kb_compile.SCHEMAS[task](data)


def test_schemas_cover_t1_to_t4():
    assert set(kb_compile.SCHEMAS) == set(kb_compile.TASKS) == {"tag", "decisions", "entity_summary", "topic_proposal"}


def test_fixed_tags_come_from_agents_md():
    assert kb_compile.fixed_tags() == ("회의", "결정", "요청", "이슈", "학습", "아이디어", "개인", "참고", "일정", "리뷰")


@pytest.mark.parametrize("task", kb_compile.TASKS)
def test_prompts_build_with_marker_and_json_rule(task):
    messages = prompts.build_messages(task, "입력")
    system = messages[0]["content"]
    assert system.startswith(f"KB-TASK: {task}\n\n## 역할")
    for heading in prompts.AGENTS_SECTIONS:
        assert f"## {heading}" in system
    body = prompts.load_prompt(task)
    assert "JSON 객체 하나만" in body
    example = body.strip().splitlines()[-1]
    kb_compile.SCHEMAS[task](json.loads(example))  # 프롬프트의 예시가 스키마를 통과한다


def test_tag_prompt_lists_every_fixed_tag():
    body = prompts.load_prompt("tag")
    assert "고정 태그: " + ", ".join(kb_compile.fixed_tags()) in body


def test_fixture_replies_pass_schemas():
    for task in kb_compile.TASKS:
        kb_compile.SCHEMAS[task](json.loads((FIX / f"{task}.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("raw, expected", [("L1", "L1"), (" [L12] ", "L12"), ("l3", "L3"), ("X 2", "X2"), ("1", None), ("L", None), (3, None)])
def test_norm_id(raw, expected):
    assert kb_compile.norm_id(raw) == expected


def test_related_filters_keys_and_people():
    related, rejected = kb_compile._related(
        ["KB-12", "[[OPS-3]]", "ZZ-9", "@김철수", "이영희", "@모름", "KB", "projects/OPS", 5, "[[jira/KB-7|별칭]]"],
        ["KB", "OPS"],
        {"김철수", "이영희"},
        with_projects=True,
    )
    assert related == ["[[KB-12]]", "[[OPS-3]]", "[[KB-7]]", "[[projects/KB]]", "[[projects/OPS]]", "@김철수", "@이영희"]
    assert rejected == 1


def test_one_line_strips_comments_and_newlines():
    assert kb_compile._one_line("a\n<!-- kb:x:start -->\nb -->", 100) == "a kb:x:start b"
    assert kb_compile._one_line("가" * 10, 5) == "가가가가…"


def test_fit_newest_keeps_latest_items():
    items = [kb_compile.Item(rel="r", hash=str(i), text="가" * 20, day="d", source="s", id=f"L{i}") for i in range(1, 6)]
    chosen = kb_compile._fit_newest(items, 20, lambda it: it.text)  # 50자 → 2개
    assert [it.id for it in chosen] == ["L4", "L5"]
    assert [it.id for it in kb_compile._fit_newest(items, 1, lambda it: it.text)] == ["L5"]


def test_topic_name_cleans_link_characters():
    assert kb_compile.topic_name(" 배포 [준비] | 2단계 ") == "배포 준비 2단계"
    assert kb_compile.topic_name("a/b:c") == "a b c"
    assert kb_compile.topic_name("###") == ""


def test_import_is_side_effect_free(tmp_path, monkeypatch):
    import importlib

    monkeypatch.chdir(tmp_path)
    importlib.reload(kb_compile)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- 리뷰 수정(Phase 4): 중복·기각·입력 크기·구역·묶음·창


def cfg_with(root: Path, profiles: dict, *, order=("main",), tasks: dict | None = None) -> config.Config:
    data = {
        "environment": "home",
        "jira": {"project_keys": ["KB", "OPS"]},
        "llm": {"enabled": True, "profiles": profiles, "fallback_order": list(order)},
    }
    if tasks:
        data["llm"]["tasks"] = tasks
    config.save_local(root, data)
    return config.load(root)


def write_daily(root: Path, day: str, lines: list[str]) -> Path:
    path = root / "daily" / f"{day}.md"
    body = notes.daily_skeleton(dt.date.fromisoformat(day)) + "".join(f"{line}\n" for line in lines).encode("utf-8")
    path.write_bytes(body)
    return path


def long_lines(n: int, width: int = 60) -> list[str]:
    out = []
    for i in range(1, n + 1):
        head = f"- {8 + i // 60:02d}:{i % 60:02d} 줄{i:03d} "
        out.append(head + "가" * max(width - len(head), 1))
    return out


def all_tags(n: int, tag: str = "회의") -> dict:
    return {"tags": {f"L{i}": tag for i in range(1, n + 1)}}


def test_link_wrap_then_compile_does_not_duplicate_decision(kb, llm):
    """맨 키 → compile → link(키를 [[ ]]로 감쌈) → 새 줄 → compile: 결정은 하나, 태그 원장도 줄마다 하나."""
    path = kb / "daily" / "2026-11-04.md"
    path.write_bytes(path.read_bytes().replace("[[KB-12]] @김철수".encode("utf-8"), "KB-12 @김철수".encode("utf-8")))
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["tag", "decisions"])
    assert text(kb / "wiki" / "decisions.md").count("## 2026-11-04 캐시 updated 시각은 UTC로 저장") == 1

    link.run(cfg)
    assert "- 09:12 [[KB-12]] @김철수" in text(path)  # link가 줄을 바꿨다
    path.write_bytes(path.read_bytes() + "- 15:00 캐시 키 이름은 다음 회의에서\n".encode("utf-8"))
    result = run(cfg, tasks=["tag", "decisions"])

    assert text(kb / "wiki" / "decisions.md").count("## 2026-11-04 캐시 updated 시각은 UTC로 저장") == 1
    assert text(kb / "wiki" / "actions.md").count("배포 체크리스트 초안 작성 · 관련 [[OPS-3]] · 출처 [[2026-11-04]] 11:40") == 1
    assert result["stats"]["decisions"]["decisions"] == 0
    last_t2 = user_text(task_requests(mock, "decisions")[-1])
    assert "[L1] (이미 반영) 09:12 [[KB-12]]" in last_t2
    last_tag = user_text(task_requests(mock, "tag")[-1])
    assert "15:00" in last_tag and "09:12" not in last_tag  # 감싼 줄은 다시 태그하지 않는다
    tag_units = ledger(kb)["tag"]
    assert len(tag_units) == 7  # 11-04 다섯 줄 + 11-05 두 줄, 옛 키 없음
    assert sum(n for _tag, n in link.tag_counts(kb)) == 7


def test_promote_suffix_then_compile_does_not_duplicate_action(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["tag", "decisions"])
    path = kb / "daily" / "2026-11-04.md"
    line = "- 11:40 [[OPS-3]] 배포 체크리스트 초안 작성 필요"
    path.write_bytes(path.read_bytes().replace(line.encode("utf-8"), f"{line} [[KB-99]]".encode("utf-8")))  # promote 꼴
    path.write_bytes(path.read_bytes() + "- 15:00 새 줄\n".encode("utf-8"))
    run(cfg, tasks=["tag", "decisions"])

    actions = text(kb / "wiki" / "actions.md")
    assert actions.count("배포 체크리스트 초안 작성 · 관련 [[OPS-3]] · 출처 [[2026-11-04]] 11:40") == 1
    assert text(kb / "wiki" / "decisions.md").count("- 상태: 제안") == 2
    last_t2 = user_text(task_requests(mock, "decisions")[-1])
    assert "(이미 반영) 11:40 [[OPS-3]] 배포 체크리스트 초안 작성 필요 [[KB-99]]" in last_t2
    last_tag = user_text(task_requests(mock, "tag")[-1])
    assert "11:40" not in last_tag and "15:00" in last_tag
    tag_units = ledger(kb)["tag"]
    assert len(tag_units) == 7 and sum(n for _tag, n in link.tag_counts(kb)) == 7
    promoted = f"{DAY4}#{kb_compile.line_hashes(line + ' [[KB-99]]')[0]}"
    assert tag_units[promoted]["status"] == "ok"  # 옛 키의 태그를 새 키로 옮겼다


def test_old_format_src_and_ledger_keys_are_still_recognised(kb, llm):
    """이전 꼴(원문 해시)로 쓴 src 표시와 원장 키도 같은 줄로 본다."""
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["tag", "decisions"], day="2026-11-04")
    led = ledger(kb)
    # 원장과 src를 이전 꼴(core.line_hash(원문))로 되돌린다
    old_tag = {}
    for key, record in led["tag"].items():
        rel = key.split("#", 1)[0]
        raw = next(l.text for l in notes.record_lines(rel, (kb / rel).read_bytes()) if kb_compile.line_hashes(l.text)[0] == key.split("#", 1)[1])
        old_tag[f"{rel}#{core.line_hash(raw)}"] = {**record, "line_hashes": [core.line_hash(raw)]}
    led["tag"] = old_tag
    core.save_state(kb, "compile", led)
    page = kb / "wiki" / "decisions.md"
    new_src = kb_compile._src_of(DAY4, kb_compile.line_hashes(LINE_L1)[0])
    old_src = kb_compile._src_of(DAY4, core.line_hash(LINE_L1))
    page.write_bytes(page.read_bytes().replace(new_src.encode(), old_src.encode()))
    led = ledger(kb)
    led["decisions"][DAY4].pop("emitted_srcs", None)
    core.save_state(kb, "compile", led)
    before = page.read_bytes()
    path = kb / "daily" / "2026-11-04.md"
    path.write_bytes(path.read_bytes() + "- 15:00 새 줄\n".encode("utf-8"))
    run(cfg, tasks=["tag", "decisions"], day="2026-11-04")
    assert page.read_bytes() == before  # 이전 꼴 src를 알아보고 다시 붙이지 않는다
    assert len(task_requests(mock, "tag")) == 2 and "09:12" not in user_text(task_requests(mock, "tag")[-1])
    assert len(ledger(kb)["tag"]) == 5  # 옛 키는 대표 키로 옮겨졌다


def test_deleted_proposed_decision_does_not_come_back(kb, llm):
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["decisions"], day="2026-11-04")
    page = kb / "wiki" / "decisions.md"
    assert "캐시 updated 시각은 UTC로 저장" in text(page)
    page.write_bytes(managed.replace_region(page.read_bytes(), "decisions", ""))  # 사람이 지움 = 기각
    path = kb / "daily" / "2026-11-04.md"
    path.write_bytes(path.read_bytes() + "- 15:00 배포는 금요일에 하기로 결정\n".encode("utf-8"))
    reply = {
        "decisions": [
            {"title": "캐시 updated 시각은 UTC로 저장", "content": "다시", "rationale": "", "related": [], "source_line": "L1"},
            {"title": "배포는 금요일", "content": "금요일에 배포한다.", "rationale": "", "related": [], "source_line": "L5"},
        ],
        "actions": [],
    }
    mock2 = llm(canned=canned(decisions=reply))
    cfg2 = main_cfg(kb, mock2)
    run(cfg2, tasks=["decisions"], day="2026-11-04")
    body = text(page)
    assert "캐시 updated 시각은 UTC로 저장" not in body  # 지운 것은 돌아오지 않는다
    assert "## 2026-11-04 배포는 금요일" in body  # 새 줄의 결정은 붙는다
    assert "[L1] (이미 반영) 09:12" in user_text(task_requests(mock2, "decisions")[-1])
    run(cfg2, tasks=["decisions"], day="2026-11-04", force=True)  # --force도 되살리지 않는다
    assert "캐시 updated 시각은 UTC로 저장" not in text(page)
    assert text(page).count("배포는 금요일") == 1


def test_t2_splits_by_max_input_tokens(kb, llm):
    """max_input_tokens ≈ 200, 긴 줄 50개 → 여러 요청, 요청마다 한도 안, 모든 줄이 한 번씩."""
    write_daily(kb, "2026-11-03", long_lines(50))
    mock = llm(canned=canned())
    cfg = cfg_with(kb, {"main": {"base_url": url(mock), "model": "mock"}}, tasks={"decisions": {"max_input_tokens": 200}})
    run(cfg, tasks=["decisions"], day="2026-11-03")
    users = [user_text(b) for b in task_requests(mock, "decisions")]
    assert len(users) > 1
    assert all(len(u) <= 200 * 2.5 for u in users), [len(u) for u in users]
    seen = [n for u in users for n in range(1, 51) if f"줄{n:03d} " in u]
    assert sorted(seen) == list(range(1, 51))
    record = ledger(kb)["decisions"]["daily/2026-11-03.md"]
    assert record["status"] == "ok" and len(record["line_hashes"]) == 50


def small_fallback(kb, mock, llm, *, context_tokens: int = 250, tasks: dict | None = None) -> config.Config:
    """main은 모든 요청을 400으로 거절하고(큰 기본 컨텍스트로 묶음을 나눔), fallback은 컨텍스트가 작다.

    fallback 입력 한도는 0.8 × 250 = 200토큰. 죽은 포트 대신 거절하는 mock을 써서 빠르다.
    """
    refusing = llm(context_length=1)
    return cfg_with(
        kb,
        {
            "main": {"base_url": url(refusing), "model": "mock"},
            "fallback": {"base_url": url(mock), "model": "mock", "detected": {"context_tokens": context_tokens}},
        },
        order=("main", "fallback"),
        tasks=tasks,
    )


def test_t2_resplits_for_smaller_fallback_context(kb, llm):
    """main용으로 나눈 묶음을 fallback 한도에 맞게 다시 나눈다. 잘라 버리고 ok로 적지 않는다(D41)."""
    write_daily(kb, "2026-11-03", long_lines(50))
    system = prompts.build_messages("decisions", "")[0]["content"]
    mock = llm(canned=canned(), context_length=int((len(system) + 520) / 2.5))  # 한도를 넘는 요청은 400
    cfg = small_fallback(kb, mock, llm)
    run(cfg, tasks=["decisions"], day="2026-11-03")
    users = [user_text(b) for b in task_requests(mock, "decisions")]
    assert len(users) > 1
    assert all(len(u) <= 200 * 2.5 for u in users), [len(u) for u in users]
    seen = [n for u in users for n in range(1, 51) if f"줄{n:03d} " in u]
    assert sorted(seen) == list(range(1, 51))  # 앞쪽 줄도 빠짐없이
    record = ledger(kb)["decisions"]["daily/2026-11-03.md"]
    assert record["status"] == "ok" and record["profile"] == "fallback" and len(record["line_hashes"]) == 50
    chats = mock.counts["chat"]
    assert run(cfg, tasks=["decisions"], day="2026-11-03")["status"] == "nothing"
    assert mock.counts["chat"] == chats


def test_t1_keeps_newest_lines_for_smaller_fallback(kb, llm):
    write_daily(kb, "2026-11-03", long_lines(50))
    mock = llm(canned=canned(tag=all_tags(50)))
    cfg = small_fallback(kb, mock, llm)
    run(cfg, tasks=["tag"], day="2026-11-03")
    users = [user_text(b) for b in task_requests(mock, "tag")]
    assert users and all(len(u) <= 200 * 2.5 + len(kb_compile._tag_user([])) for u in users)
    first = users[0]
    numbers = [n for n in range(1, 51) if f"줄{n:03d} " in first]
    assert numbers and numbers[-1] == 40 and 1 not in numbers  # 첫 묶음(40줄)의 가장 최근 줄들
    units = ledger(kb)["tag"]
    assert len(units) < 50  # 보내지 않은 줄은 원장에 적지 않는다(다음 실행에서)
    assert all(v["status"] == "ok" for v in units.values())


def test_t3_keeps_newest_mentions_for_smaller_fallback(kb, llm):
    write_daily(kb, "2026-11-03", [f"- 09:{i:02d} [[KB-12]] 언급{i:02d} " + "나" * 50 for i in range(1, 41)])
    cfg0 = make_cfg(kb, {})
    link.run(cfg0)
    mentions = [l for l in region(kb / "wiki" / "projects" / "KB.md", "mentions").splitlines() if l.startswith("- ")]
    n = len(mentions)
    mock = llm(canned=canned(entity_summary={"summary": "최근 요약.", "sources": [f"M{n}"]}))
    cfg = small_fallback(kb, mock, llm, context_tokens=2000)
    run(cfg, tasks=["entity_summary"])
    [req] = [b for b in task_requests(mock, "entity_summary") if "대상: 프로젝트 KB" in user_text(b)]
    user = user_text(req)
    assert f"[M{n}] " in user and "[M1] " not in user  # 가장 최근 언급만
    assert ledger(kb)["entity_summary"]["wiki/projects/KB.md"]["status"] == "ok"


class FakeClient:
    """compile이 쓰는 LlmClient 표면만. run_task는 messages_for(fallback_limit)으로 만든 입력의 첫 줄 id로 답한다."""

    def __init__(self, primary_limit: int, fallback_limit: int, reply):
        self.primary_limit, self.fallback_limit, self.reply = primary_limit, fallback_limit, reply
        self.enabled = True
        self.last_probe: dict = {}
        self.users: list[str] = []

    def connected_profiles(self, names=None):
        return ["main"]

    def input_limit(self, task, profile=None):
        return self.primary_limit

    def max_output_tokens(self, task, profile=None):
        return 8192

    def _log(self, line):
        pass

    def run_task(self, task, messages_for, *, validate, needs_vision=False):
        from lib.llm_client import TaskOutcome

        messages = messages_for(self.fallback_limit)
        user = messages[-1]["content"]
        self.users.append(user)
        ids = re.findall(r"^\[(L\d+)\]", user, re.M)
        data = self.reply(ids)
        try:
            validate(data)
        except Exception as exc:  # noqa: BLE001
            return TaskOutcome("skipped", None, None, [{"profile": "fallback", "status": "schema", "error": str(exc)}])
        return TaskOutcome("ok", data, "fallback", [{"profile": "fallback", "status": "ok", "error": None}])


def test_t4_resplits_and_merges_partial_proposals(kb):
    write_daily(kb, "2026-11-03", long_lines(50))
    cfg = make_cfg(kb, {"main": {"base_url": "http://127.0.0.1:9/v1", "model": "mock"}})
    client = FakeClient(60000, 200, lambda ids: {"proposals": [{"name": "캐시 설계", "why": "반복", "sample_sources": [ids[0]], "merge_with": None}]})
    kb_compile.run(cfg, tasks=["topic_proposal"], client=client, out=lambda line: None)
    assert len(client.users) > 2
    assert all(len(u) <= 200 * 2.5 for u in client.users), [len(u) for u in client.users]
    seen = {n for u in client.users for n in range(1, 51) if f"줄{n:03d} " in u}
    assert seen == set(range(1, 51))
    led = ledger(kb)
    assert led["topic_proposal"]["2026-11-05"]["status"] == "ok"
    items = led["_proposals"]["items"]
    assert list(items) == ["1"]  # 부분 결과를 이름으로 합친다
    assert len(items["1"]["sources"]) == len(client.users)  # 묶음마다 하나씩 모은 출처


def test_t4_unknown_sample_source_is_schema_failure(kb, llm):
    bad = {"proposals": [{"name": "배포", "why": "x", "sample_sources": ["L99"], "merge_with": None}]}
    mock = llm(canned=canned(topic_proposal=bad))
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["topic_proposal"])
    assert ledger(kb)["topic_proposal"]["2026-11-05"]["status"] == "skipped"
    assert mock.counts["chat"] == 2
    page = kb / "wiki" / "_proposals.md"
    assert not page.exists() or "배포" not in text(page)


def test_broken_target_regions_make_no_llm_calls(kb, llm):
    """결정·제안 파일의 관리 구역이 망가졌으면 LLM을 부르기 전에 그 작업을 접는다(비용 반복 방지)."""
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    (kb / "wiki" / "decisions.md").write_bytes("# 결정 로그\n\n<!-- kb:decisions:start -->\n깨진 구역\n".encode("utf-8"))
    (kb / "wiki" / "_proposals.md").write_bytes("# 주제 제안\n\n<!-- kb:proposals:end -->\n".encode("utf-8"))
    for _ in range(2):
        result = run(cfg, tasks=["decisions", "topic_proposal"])
        assert mock.counts == {"models": 0, "chat": 0}
        assert result["status"] == "nothing"
        assert any("decisions.md" in p for p in result["problems"])
        assert any("_proposals.md" in p for p in result["problems"])
    assert ledger_or_empty(kb).get("decisions", {}) == {}
    # actions.md만 망가져도 T2는 하지 않는다
    (kb / "wiki" / "decisions.md").unlink()
    (kb / "wiki" / "actions.md").write_bytes("<!-- kb:actions:end -->\n".encode("utf-8"))
    result = run(cfg, tasks=["decisions"])
    assert mock.counts["chat"] == 0 and any("actions.md" in p for p in result["problems"])


def test_t1_packs_1000_lines_into_25_requests(kb, llm):
    lines = [f"- 09:00 {i:04d} " + "다" * 74 for i in range(1000)]  # 약 83자
    write_daily(kb, "2026-11-05", lines)
    mock = llm(canned=canned(tag=all_tags(40)))
    cfg = main_cfg(kb, mock)
    result = run(cfg, tasks=["tag"], day="2026-11-05")
    assert len(task_requests(mock, "tag")) <= 26
    assert result["stats"]["tag"]["ok"] == 1000
    short = [f"- {i}" for i in range(1000)]
    write_daily(kb, "2026-11-04", short)
    mock2 = llm(canned=canned(tag=all_tags(40)))
    run(main_cfg(kb, mock2), tasks=["tag"], day="2026-11-04")
    assert len(task_requests(mock2, "tag")) <= 26


def test_t1_batches_respect_char_budget(kb):
    items = [kb_compile.Item(rel="r", hash=str(i), text="가" * 200, day="2026-11-05", source="s") for i in range(30)]
    batches = kb_compile._tag_batches(items, 40, 1000)  # 2500자 - 프롬프트 몫 → 200자 줄 열 개 남짓
    assert len(batches) > 1 and sum(len(b) for b in batches) == 30
    assert all(sum(len(f"[L40] {it.day} {it.text}") + 1 for it in b) <= 2500 for b in batches)
    assert [len(b) for b in kb_compile._tag_batches(items[:5], 2, 100000)] == [2, 2, 1]


def test_t2_schema_failure_skips_day_and_continues(kb, llm):
    mock = llm(canned=canned(decisions={"decisions": []}))  # actions 없음
    cfg = main_cfg(kb, mock)
    result = run(cfg, tasks=["decisions"])
    assert mock.counts["chat"] == 2 * 2  # 두 날 모두 시도(같은 프로필에서 한 번 더)
    led = ledger(kb)["decisions"]
    assert led[DAY4]["status"] == "skipped" and led[DAY5]["status"] == "skipped"
    assert not (kb / "wiki" / "decisions.md").exists() and not (kb / "wiki" / "actions.md").exists()
    assert result["stats"]["decisions"]["skipped"] == 2


def test_default_window_is_last_7_days_and_since(kb, llm):
    write_daily(kb, "2026-10-20", ["- 08:00 오래된 기록"])
    mock = llm(canned=canned())
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["tag", "decisions"])
    assert not any("오래된 기록" in user_text(b) for b in task_requests(mock, "tag") + task_requests(mock, "decisions"))
    assert "daily/2026-10-20.md" not in ledger(kb)["decisions"]

    run(cfg, tasks=["tag", "decisions"], since=0)
    assert "오래된 기록" in user_text(task_requests(mock, "tag")[-1])
    assert "날짜: 2026-10-20" in user_text(task_requests(mock, "decisions")[-1])

    n_tag, n_t2 = len(task_requests(mock, "tag")), len(task_requests(mock, "decisions"))
    run(cfg, tasks=["tag", "decisions"], since=1, force=True)
    tag_users = [user_text(b) for b in task_requests(mock, "tag")[n_tag:]]
    t2_users = [user_text(b) for b in task_requests(mock, "decisions")[n_t2:]]
    assert tag_users and all("2026-11-05" in u and "2026-11-04" not in u and "2026-10-20" not in u for u in tag_users)
    assert [u.splitlines()[0] for u in t2_users] == ["날짜: 2026-11-05"]


def test_accepting_yaml_indicator_topic_names_makes_parseable_pages(kb, llm):
    names = ["- 배포 절차", "!중요 일정", "@배포", "&앵커 문제", "'따옴표", "%TAG 설정", "{배포}"]
    reply = {"proposals": [{"name": n, "why": "x", "sample_sources": ["L1"], "merge_with": None} for n in names]}
    mock = llm(canned=canned(topic_proposal=reply))
    cfg = main_cfg(kb, mock)
    run(cfg, tasks=["topic_proposal"])
    done = kb_compile.accept(cfg, "all", out=lambda line: None)
    assert len(done) == len(names)
    parsed = sorted(frontmatter.parse((kb / rel).read_bytes())["name"] for rel in done)
    assert parsed == sorted(names)
    index = kb_compile._topic_index(cfg)
    assert all(kb_compile._name_key(n) in index for n in names)


@pytest.mark.parametrize("name", ["", "   ", "###", "[[]]"])
def test_topic_proposal_schema_rejects_unusable_names(name):
    with pytest.raises(SchemaError):
        kb_compile.validate_topic_proposal({"proposals": [{"name": name, "why": "w", "sample_sources": ["L1"], "merge_with": None}]})


def _code_root_copy(tmp_path: Path, agents: str) -> Path:
    root = tmp_path / "code"
    shutil.copytree(prompts.CODE_ROOT / "prompts", root / "prompts")
    (root / "AGENTS.md").write_text(agents, encoding="utf-8")
    return root


def test_agents_sections_use_level_two_headings_only(tmp_path):
    agents = (prompts.CODE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    decoy = "## 폴더\n### 역할\n가짜 역할\n\n"
    root = _code_root_copy(tmp_path, decoy + agents)
    system = prompts.system_prompt("tag", root=root)
    assert "가짜 역할" not in system and "\n## 역할" in "\n" + system.split("\n\n", 1)[1]

    without = re.sub(r"(?m)^## 역할\n", "## 역할 아님\n", agents, count=1)
    root2 = _code_root_copy(tmp_path / "b", decoy + without)
    with pytest.raises(core.ConfigError, match="## 역할"):
        prompts.system_prompt("tag", root=root2)

    root3 = _code_root_copy(tmp_path / "c", agents + "\n## 금지\n- 두 번째\n")
    with pytest.raises(core.ConfigError, match="두 번"):
        prompts.system_prompt("tag", root=root3)
    assert prompts.section("# a\n### 역할\nx\n## 역할\ny\n", "역할", level=2) == "## 역할\ny"
    assert prompts.section("### 역할\nx\n", "역할", level=2) is None
