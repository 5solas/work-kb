"""scripts/compile.py (W4.4) 테스트: T1~T4, 원장·멱등, dry-run, 회사 차단, daily 불변, proposals.

LLM은 tests/mock_llm.py의 canned 모드(시스템 메시지 첫 줄 `KB-TASK: <작업>`으로 답을 고름)를 쓴다.
답은 tests/fixtures/llm/*.json, 볼트는 tests/fixtures/llm/vault(허구 데이터).
"""
from __future__ import annotations

import json
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
    assert tags[f"{DAY4}#{core.line_hash(LINE_L1)}"] == "결정"
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
    key = f"{DAY5}#{core.line_hash('- 09:30 [[OPS-3]] 막힘, @이영희 확인 대기')}"
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
