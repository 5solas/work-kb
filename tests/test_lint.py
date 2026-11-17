"""scripts/lint.py (W5.1) 테스트: 절마다 허구 볼트, T5(mock_llm canned), 쓰기 범위, 종료 0.

볼트는 conftest의 `vault`(한글·공백 경로)에 파일을 직접 쓴다. 시각은 KB_FAKE_NOW로 고정한다.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import socket
import unicodedata
from pathlib import Path

import pytest

import lint
import mock_llm
import status as kb_status
from lib import config, core, managed

NOW = "2026-11-05T18:30:00+09:00"
KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.date(2026, 11, 5)
LINT_MD = "wiki/_lint.md"


# ---------------------------------------------------------------- 도우미


def write(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def region(body: str, name: str = "mentions") -> str:
    return f"<!-- kb:{name}:start -->\n{body}<!-- kb:{name}:end -->\n"


def entity(kind: str, name: str, *, mentions: str = "", summary: str = "", extra: str = "") -> str:
    head = {"project": f"type: project\nkey: {name}\ntitle: {name}", "person": f"type: person\nname: {name}", "topic": f"type: topic\nname: {name}\naliases: []"}[kind]
    return (
        f"---\n{head}\nupdated: 2026-11-05\n---\n# {name}\n\n## 개요\n{extra}\n"
        f"## 최근 요약\n{region(summary, 'summary')}\n## 언급\n{region(mentions)}"
    )


def jira_stub(key: str, mentions: str = "") -> str:
    return f"---\nkey: {key}\nproject: {key.split('-')[0]}\nstub: true\nsummary: \"\"\nstatus: \"\"\n---\n# {key}\n\n## 노트에서 언급\n{region(mentions)}"


def jira_issue(key: str, synced_at: str, *, extra: str = "", status_category: str = "indeterminate") -> str:
    return (
        f"---\nkey: {key}\nsummary: 허구 일감\nstatus: In Progress\nstatus_category: {status_category}\n"
        f"project: {key.split('-')[0]}\nsynced_at: {synced_at}\n{extra}---\n# {key} 허구 일감\n\n## 노트에서 언급\n{region('')}"
    )


def daily(day: str, records: str) -> str:
    return f"---\ndate: {day}\n---\n## 오늘\n{region('', 'today')}\n## 이월\n{region('', 'carryover')}\n## 기록\n{records}"


def local(root: Path, **extra) -> config.Config:
    data = {"environment": "home", "jira": {"project_keys": ["KB", "OPS"]}, **extra}
    config.save_local(root, data)
    return config.load(root)


def run(cfg: config.Config, **kw) -> dict:
    return lint.run(cfg, **kw)


def report(root: Path) -> str:
    return (root / LINT_MD).read_bytes().decode("utf-8")


def lint_region(root: Path) -> str:
    return managed.read_region((root / LINT_MD).read_bytes(), "lint").decode("utf-8")


def items(result: dict, key: str) -> list[dict]:
    return result["sections"][key]["items"]


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).as_posix() != ".state/kb.lock"
    }


def ts(day: str) -> float:
    return dt.datetime.fromisoformat(day).replace(tzinfo=KST).timestamp()


@pytest.fixture
def kb(vault, fake_now) -> Path:
    fake_now(NOW)
    return vault


@pytest.fixture
def cfg(kb) -> config.Config:
    return local(kb)


@pytest.fixture
def no_llm(monkeypatch):
    """--llm 없이는 LLM 클라이언트를 만들지도 않는다."""

    def refuse(*_a, **_k):
        raise AssertionError("--llm 없이 LlmClient를 만들었습니다")

    monkeypatch.setattr(lint, "LlmClient", refuse)


# ---------------------------------------------------------------- 고아 페이지


def test_orphans(kb, cfg, no_llm):
    write(kb, "wiki/topics/고아.md", entity("topic", "고아"))
    write(kb, "wiki/topics/연결됨.md", entity("topic", "연결됨"))
    write(kb, "wiki/topics/결정에서.md", entity("topic", "결정에서"))
    write(kb, "wiki/people/김철수.md", entity("person", "김철수", mentions="- [[2026-11-04]] 09:12 @김철수 와 논의\n"))
    write(kb, "wiki/projects/KB.md", entity("project", "KB"))
    write(kb, "wiki/projects/OPS.md", entity("project", "OPS"))
    write(kb, "daily/2026-11-04.md", daily("2026-11-04", "- 09:12 [[topics/연결됨]] 정리\n- 09:30 [[wiki/projects/KB]] 확인\n"))
    write(kb, "wiki/decisions.md", "# 결정 로그\n\n" + region("## 2026-11-04 X\n- 상태: 제안\n- 관련: [[topics/결정에서]]\n", "decisions"))
    # 색인 구역의 자동 목록은 링크로 치지 않는다
    write(kb, "wiki/index.md", "# 색인\n\n" + region("- [[topics/고아|고아]]\n- [[projects/OPS|OPS]]\n", "index"))
    # 자기 자신을 가리키는 링크도 치지 않는다
    write(kb, "wiki/topics/자기참조.md", entity("topic", "자기참조", extra="[[topics/자기참조]]\n"))

    result = run(cfg)
    pages = sorted(i["page"] for i in items(result, "orphans"))
    assert pages == ["wiki/projects/OPS.md", "wiki/topics/고아.md", "wiki/topics/자기참조.md"]
    text = lint_region(kb)
    assert "## 고아 페이지 (3)" in text
    assert "- [[wiki/topics/고아|topics/고아]]" in text
    # 허브 파일(index·decisions)·_lint는 대상이 아니다
    assert not any(p.endswith(("index.md", "decisions.md", "_lint.md")) for p in pages)


# ---------------------------------------------------------------- stub


def test_stale_stubs(kb, cfg, no_llm):
    write(kb, "jira/KB-1.md", jira_stub("KB-1", "- [[2026-09-01]] 10:00 옛 언급\n"))
    write(kb, "jira/KB-2.md", jira_stub("KB-2", "- [[2026-09-01]] 10:00 옛 언급\n- [[2026-11-01]] 10:00 최근 언급\n"))
    write(kb, "jira/KB-3.md", jira_stub("KB-3"))  # 언급 없음 + 오래된 파일
    os.utime(kb / "jira/KB-3.md", (ts("2026-08-01"), ts("2026-08-01")))
    write(kb, "jira/KB-4.md", jira_stub("KB-4"))  # 언급 없음 + 방금 만든 stub
    os.utime(kb / "jira/KB-4.md", (ts("2026-11-05"), ts("2026-11-05")))
    write(kb, "jira/KB-5.md", jira_stub("KB-5", "- [[inbox/20261101-0900-메모]] 최근 inbox 언급\n"))
    write(kb, "jira/KB-6.md", jira_issue("KB-6", "2026-11-05T09:00:00+09:00"))  # 동기화된 일감은 stub이 아니다

    result = run(cfg)
    found = {i["key"]: i for i in items(result, "stale_stubs")}
    assert sorted(found) == ["KB-1", "KB-3"]
    assert found["KB-1"]["last_mention"] == "2026-09-01"
    assert found["KB-3"]["last_mention"] is None
    assert "kb.py sync --key KB-1" in lint_region(kb)


# ---------------------------------------------------------------- 유사 제목


def test_similar_titles(kb, cfg, no_llm):
    write(kb, "wiki/topics/캐시 설계.md", entity("topic", "캐시 설계"))
    write(kb, "wiki/topics/캐시설계.md", entity("topic", "캐시설계"))
    write(kb, "wiki/topics/Front Matter.md", entity("topic", "Front Matter"))
    write(kb, "wiki/topics/front-matter.md", entity("topic", "front-matter"))
    write(kb, "wiki/topics/배포.md", entity("topic", "배포"))
    write(kb, "wiki/topics/회의.md", entity("topic", "회의"))
    nfd = unicodedata.normalize("NFD", "홍길동")
    write(kb, "wiki/people/홍길동.md", entity("person", "홍길동"))
    write(kb, f"wiki/people/{nfd}x.md", entity("person", nfd + "x"))  # NFD + 한 글자 → 0.857
    write(kb, "wiki/people/김철수.md", entity("person", "김철수"))
    write(kb, "wiki/people/김철호.md", entity("person", "김철호"))  # 0.67 → 아님
    # 사람과 주제는 서로 비교하지 않는다
    write(kb, "wiki/topics/김철수.md", entity("topic", "김철수"))

    result = run(cfg)
    pairs = {(i["kind"], frozenset((Path(i["a"]).stem, Path(i["b"]).stem))) for i in items(result, "similar_titles")}
    assert ("topic", frozenset({"캐시 설계", "캐시설계"})) in pairs
    assert ("topic", frozenset({"Front Matter", "front-matter"})) in pairs
    assert ("person", frozenset({"홍길동", nfd + "x"})) in pairs
    assert len(pairs) == 3
    assert all(i["ratio"] >= 0.85 for i in items(result, "similar_titles"))


# ---------------------------------------------------------------- 깨진 위키링크


def test_broken_links(kb, cfg, no_llm):
    write(kb, "jira/KB-12.md", jira_issue("KB-12", "2026-11-05T09:00:00+09:00"))
    write(kb, "wiki/projects/KB.md", entity("project", "KB"))
    write(kb, "raw/assets/2026-11-04-1420-보드.png", b"\x89PNG fictional")
    records = "\n".join([
        "- 09:00 [[KB-12]] [[jira/KB-12]] [[jira/KB-12.md]] [[KB-12|별칭]] [[KB-12#설명]]",
        "- 09:01 [[projects/KB]] [[wiki/projects/KB]] [[projects/kb]] [[../wiki/projects/KB]] [[2026-11-04]]",
        "- 09:02 ![[raw/assets/2026-11-04-1420-보드.png]] ![[2026-11-04-1420-보드.png]] [[https://example.invalid/x]]",
        "- 09:03 [[없는페이지]] [[jira/KB-99]] ![[raw/assets/없는그림.png]]",
        "- 09:04 `[[인라인코드]]` 는 링크가 아니다",
        "```",
        "[[펜스안]]",
        "```",
        "| 키 | 링크 |",
        "|---|---|",
        "| a | [[projects/KB\\|KB]] |",
        "",
    ])
    write(kb, "daily/2026-11-04.md", daily("2026-11-04", records))
    # mentions 구역은 daily 원문의 사본이라 보지 않는다
    write(kb, "wiki/people/김철수.md", entity("person", "김철수", mentions="- [[2026-11-04]] 09:03 [[없는페이지]]\n", extra="[[topics/없는주제]]\n"))

    result = run(cfg)
    broken = [(i["source"], i["target"]) for i in items(result, "broken_links")]
    assert broken == [
        ("daily/2026-11-04.md", "없는페이지"),
        ("daily/2026-11-04.md", "jira/KB-99"),
        ("daily/2026-11-04.md", "raw/assets/없는그림.png"),
        ("wiki/people/김철수.md", "topics/없는주제"),
    ]
    text = lint_region(kb)
    assert "`[[없는페이지]]`" in text  # 보고에는 링크가 아니라 코드로
    assert "- [[daily/2026-11-04]] 16번째 줄: `[[없는페이지]]`" in text
    assert [i["line"] for i in items(result, "broken_links")][:3] == [16, 16, 16]


# ---------------------------------------------------------------- 오래된 캐시


def test_stale_cache_issues_and_last_sync(kb, no_llm):
    cfg = local(kb, jira={"project_keys": ["KB"], "base_url": "http://127.0.0.1:9", "poll_minutes": 15})
    write(kb, "jira/KB-5.md", jira_issue("KB-5", "2026-10-25T09:00:00+09:00"))
    write(kb, "jira/KB-6.md", jira_issue("KB-6", "2026-10-01T09:00:00+09:00", extra="in_scope: false\n"))
    write(kb, "jira/KB-7.md", jira_issue("KB-7", "2026-11-04T09:00:00+09:00"))
    write(kb, "jira/KB-8.md", jira_issue("KB-8", "2026-10-01T09:00:00+09:00", status_category="done"))
    write(kb, "jira/KB-9.md", jira_stub("KB-9"))
    core.save_state(kb, "sync", {"last_sync": "2026-11-05T16:30:00+09:00"})  # 2시간 전 > 15분 × 4

    result = run(cfg)
    found = items(result, "stale_cache")
    assert [i["key"] for i in found if i["kind"] == "issue"] == ["KB-5"]
    sync = [i for i in found if i["kind"] == "last_sync"]
    assert len(sync) == 1 and sync[0]["age_hours"] == 2.0
    assert "마지막 동기화 2026-11-05 16:30" in lint_region(kb)

    core.save_state(kb, "sync", {"last_sync": "2026-11-05T18:00:00+09:00"})  # 30분 전
    assert [i["kind"] for i in items(run(cfg), "stale_cache")] == ["issue"]

    (kb / ".state" / "sync.json").unlink()  # Jira가 설정됐는데 기록 없음
    assert any(i["kind"] == "last_sync" and i["last_sync"] is None for i in items(run(cfg), "stale_cache"))


def test_no_sync_record_without_jira_is_quiet(kb, cfg, no_llm):
    assert items(run(cfg), "stale_cache") == []


# ---------------------------------------------------------------- outbox


def test_outbox_states(kb, cfg, no_llm):
    now = dt.datetime.now(dt.timezone.utc)
    rows = [
        {"id": "01A", "action": "comment", "key": "KB-1", "status": "pending", "attempts": 1, "last_error": "연결 거부"},
        {"id": "01B", "action": "comment", "key": "KB-2", "status": "pending", "attempts": 1, "last_error": "uncertain"},
        {"id": "01C", "action": "transition", "key": "KB-3", "status": "failed", "attempts": 5, "last_error": "500"},
        {"id": "01D", "action": "comment", "key": "KB-4", "status": "done", "attempts": 1},
        {"id": "01E", "action": "assign", "key": "KB-5", "status": "sending", "attempts": 1,
         "claimed_at": now.isoformat(timespec="seconds"), "pid": os.getpid()},
        {"id": "01F", "action": "worklog", "key": "KB-6", "status": "sending", "attempts": 1,
         "claimed_at": (now - dt.timedelta(hours=1)).isoformat(timespec="seconds"), "pid": os.getpid()},
    ]
    lines = [json.dumps(r, ensure_ascii=False) for r in rows] + ["{깨진 줄"]
    write(kb, "jira/outbox.jsonl", "\n".join(lines) + "\n")

    result = run(cfg)
    states = {i["id"]: i["state"] for i in items(result, "outbox")}
    assert states == {"01A": "pending", "01B": "uncertain", "01C": "failed", "01E": "sending", "01F": "uncertain"}
    text = lint_region(kb)
    assert "--retry-failed" in text and "--retry-uncertain" in text
    assert "읽지 못한 줄 1개" in text


def test_outbox_respects_write_timeout_sec_consistently_with_outbox(kb, no_llm):
    now = dt.datetime.now(dt.UTC)
    row = {"id": "01G", "action": "comment", "key": "KB-7", "status": "sending", "attempts": 1,
           "claimed_at": (now - dt.timedelta(seconds=30)).isoformat(timespec="seconds"), "pid": os.getpid()}
    write(kb, "jira/outbox.jsonl", json.dumps(row, ensure_ascii=False) + "\n")

    write(kb, "config.local.yaml", "environment: home\njira:\n  write_timeout_sec: 10\n")
    assert items(run(config.load(kb, environ={})), "outbox")[0]["state"] == "uncertain"

    write(kb, "config.local.yaml", "environment: home\njira:\n  write_timeout_sec: -5\n")
    assert items(run(config.load(kb, environ={})), "outbox")[0]["state"] == "sending"


# ---------------------------------------------------------------- 오래된 '제안'·미처리 제안


DECISIONS = (
    "# 결정 로그\n\n> LLM이 쓴 항목은 '제안'. 사람이 '확정'/'기각'으로 바꾼다.\n\n"
    + region(
        "## 2026-10-01 캐시 updated 시각은 UTC로 저장\n- 상태: 제안   <!-- LLM이 쓴 것 -->\n- 관련: [[KB-12]] · @김철수\n"
        "- 내용: jira 캐시의 updated 값을 UTC로 저장한다.\n- 출처: [[2026-10-01]] 09:12 <!-- src:0123456789abcdef -->\n\n"
        "## 2026-11-01 배포는 금요일에 하지 않는다\n- 상태: 제안\n- 관련: [[OPS-3]]\n- 내용: 금요일 배포 금지.\n\n"
        "## 2026-09-01 표 열은 여섯 개\n- 상태: 확정\n- 내용: 표 열 고정.\n\n"
        "## 2026-09-02 버린 안\n- 상태: 기각\n- 내용: 버림.\n\n",
        "decisions",
    )
)


def test_old_proposed_decisions(kb, cfg, no_llm):
    write(kb, "wiki/decisions.md", DECISIONS)
    result = run(cfg)
    assert [(i["day"], i["title"]) for i in items(result, "old_proposed")] == [("2026-10-01", "캐시 updated 시각은 UTC로 저장")]
    assert "'확정' 또는 '기각'" in lint_region(kb)


def test_pending_proposals(kb, cfg, no_llm):
    core.save_state(kb, "compile", {"_proposals": {"next_id": 3, "items": {
        "1": {"name": "배포 절차", "status": "제안", "created": "2026-11-01T12:30:00+09:00", "why": "자주 나옴"},
        "2": {"name": "회의록", "status": "승인", "created": "2026-10-20T12:30:00+09:00"},
    }}})
    result = run(cfg)
    assert [(i["id"], i["name"]) for i in items(result, "pending_proposals")] == [("1", "배포 절차")]
    assert "kb.py proposals accept" in lint_region(kb)


# ---------------------------------------------------------------- 이미지·포기


def test_images_same_rule_as_status_plus_gave_up(kb, cfg, no_llm):
    write(kb, "inbox/새그림.png", b"\x89PNG a")
    write(kb, "inbox/처리됨.png", b"\x89PNG b")
    write(kb, "inbox/처리됨.md", "---\nsource_image: inbox/처리됨.png\n---\n")
    write(kb, "raw/assets/2026-11-04-1420-보드.png", b"\x89PNG c")
    write(kb, "raw/assets/2026-11-04-1500-안쓰임.png", b"\x89PNG d")  # daily가 가리키지 않음
    write(kb, "raw/assets/2026-11-03-0900-x.png", b"\x89PNG x")
    write(kb, "raw/assets/2026-11-03-0901-y.png", b"\x89PNG y")
    write(kb, "daily/2026-11-04.md", daily(
        "2026-11-04",
        "- 09:00 ![[raw/assets/2026-11-03-0900-x.png]] 포기한 그림\n"
        "- 09:01 ![[raw/assets/2026-11-03-0901-y.png]] 한 번 실패\n"
        "- 14:20 ![[raw/assets/2026-11-04-1420-보드.png]] 회의\n",
    ))
    core.save_state(kb, "images", {"skipped": {
        core.sha256_hex(b"\x89PNG x"): {"image": "raw/assets/2026-11-03-0900-x.png", "count": 3, "status": "gave_up", "error": "형식 오류"},
        core.sha256_hex(b"\x89PNG y"): {"image": "raw/assets/2026-11-03-0901-y.png", "count": 1, "status": "skipped"},
        core.sha256_hex(b"\x89PNG d"): {"image": "raw/assets/2026-11-04-1500-안쓰임.png", "count": 3, "status": "gave_up"},  # 임베드 없음
        "aa": {"image": "inbox/지운그림.png", "count": 3, "status": "gave_up", "error": "형식 오류"},  # 파일 없음
    }})
    result = run(cfg)
    found = items(result, "images")
    unprocessed = [i["image"] for i in found if i["state"] == "unprocessed"]
    assert unprocessed == [
        "inbox/새그림.png", "raw/assets/2026-11-03-0900-x.png", "raw/assets/2026-11-03-0901-y.png", "raw/assets/2026-11-04-1420-보드.png",
    ]
    assert len(unprocessed) == kb_status.unprocessed_images(cfg)
    gave_up = [i for i in found if i["state"] == "gave_up"]
    assert gave_up == [{"image": "raw/assets/2026-11-03-0900-x.png", "state": "gave_up", "error": "형식 오류"}]
    assert len(gave_up) == kb_status.images_gave_up(cfg, [])
    assert "- 포기: `raw/assets/2026-11-03-0900-x.png` · 형식 오류 → `kb.py ingest --force`" in lint_region(kb)
    assert "지운그림" not in lint_region(kb) and "- 포기: `raw/assets/2026-11-04-1500-안쓰임.png`" not in lint_region(kb)


def test_images_user_note_with_same_stem_is_not_a_sidecar(kb, cfg, no_llm):
    """`inbox/<stem>.md`가 이 이미지의 사이드카가 아니면(사용자 노트) status·ingest처럼 미처리로 센다."""
    write(kb, "inbox/a.png", b"\x89PNG a")
    write(kb, "inbox/a.md", "# 그냥 내 노트\n")
    write(kb, "raw/assets/2026-11-04-1420-보드.png", b"\x89PNG c")
    write(kb, "inbox/2026-11-04-1420-보드.md", "# 회의 메모\n")
    write(kb, "daily/2026-11-04.md", daily("2026-11-04", "- 14:20 ![[raw/assets/2026-11-04-1420-보드.png]] 회의\n"))
    core.save_state(kb, "images", {"skipped": {
        core.sha256_hex(b"\x89PNG a"): {"image": "inbox/a.png", "count": 3, "status": "gave_up", "error": "형식 오류"},
    }})
    found = items(run(cfg), "images")
    unprocessed = [i["image"] for i in found if i["state"] == "unprocessed"]
    assert unprocessed == ["inbox/a.png", "raw/assets/2026-11-04-1420-보드.png"]
    assert len(unprocessed) == kb_status.unprocessed_images(cfg)
    assert [i["image"] for i in found if i["state"] == "gave_up"] == ["inbox/a.png"]
    assert "- `inbox/a.png`" in lint_region(kb)


def test_images_gave_up_only_while_pending(kb, cfg, no_llm):
    """포기한 이미지는 지금도 미처리일 때만 알린다(P-101). 지우거나 바꾸면 사라지고, 이름을 바꾸면 새 경로로 알린다."""
    raw = b"\x89PNG a"
    image = write(kb, "inbox/a.png", raw)
    core.save_state(kb, "images", {"skipped": {
        core.sha256_hex(raw): {"image": "inbox/a.png", "count": 3, "status": "gave_up", "error": "형식 오류"},
    }})
    ledger = (kb / ".state" / "images.json").read_bytes()

    def gave_up() -> list[str]:
        return [i["image"] for i in items(run(cfg), "images") if i["state"] == "gave_up"]

    assert gave_up() == ["inbox/a.png"]
    image.rename(kb / "inbox" / "새이름.png")  # 이름만 바뀜: ingest는 여전히 포기한다
    assert gave_up() == ["inbox/새이름.png"]
    assert "- 포기: `inbox/새이름.png` · 형식 오류 → `kb.py ingest --force`" in lint_region(kb)
    write(kb, "inbox/새이름.png", b"\x89PNG other")  # 내용이 바뀜
    assert gave_up() == []
    write(kb, "inbox/새이름.png", raw)
    assert gave_up() == ["inbox/새이름.png"]
    (kb / "inbox" / "새이름.png").unlink()  # 삭제
    assert gave_up() == []
    assert "포기:" not in lint_region(kb)
    assert (kb / ".state" / "images.json").read_bytes() == ledger  # 원장은 그대로


def test_compile_gave_up_units(kb, cfg, no_llm):
    core.save_state(kb, "compile", {
        "decisions": {"2026-11-01": {"status": "gave_up"}, "2026-11-02": {"status": "ok"}},
        "entity_summary": {"wiki/projects/KB.md": {"status": "gave_up"}},
        "_proposals": {"items": {}},
    })
    result = run(cfg)
    assert [(i["task"], i["unit"]) for i in items(result, "gave_up")] == [
        ("decisions", "2026-11-01"), ("entity_summary", "wiki/projects/KB.md"),
    ]


# ---------------------------------------------------------------- 마커 손상


def test_broken_markers_and_non_utf8(kb, cfg, no_llm):
    write(kb, "wiki/topics/깨짐.md", "# 깨짐\n<!-- kb:mentions:start -->\n- 끝 없음\n")
    write(kb, "jira/KB-1.md", jira_stub("KB-1") + "<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n")  # 두 번
    write(kb, "wiki/projects/KB.md", entity("project", "KB"))  # 정상
    write(kb, "inbox/cp949.md", "# 메모\n".encode("cp949"))
    result = run(cfg)
    found = {i["page"]: i["error"] for i in items(result, "broken_markers")}
    assert sorted(found) == ["inbox/cp949.md", "jira/KB-1.md", "wiki/topics/깨짐.md"]
    assert "끝이 없습니다" in found["wiki/topics/깨짐.md"]
    assert "두 번" in found["jira/KB-1.md"]
    assert found["inbox/cp949.md"] == "UTF-8이 아님"


# ---------------------------------------------------------------- 쓰기 범위·멱등·종료 코드


def busy_vault(root: Path) -> None:
    write(root, "wiki/topics/고아.md", entity("topic", "고아"))
    write(root, "daily/2026-11-04.md", daily("2026-11-04", "- 09:00 [[없는페이지]]\n"))
    write(root, "wiki/decisions.md", DECISIONS)
    write(root, "jira/KB-1.md", jira_stub("KB-1", "- [[2026-09-01]] 10:00 옛 언급\n"))
    write(root, "inbox/새그림.png", b"\x89PNG a")


def test_writes_only_lint_region_and_is_idempotent(kb, cfg, no_llm):
    busy_vault(kb)
    write(kb, LINT_MD, "# 점검 보고\n\n사람이 쓴 메모.\r\n\n" + region("옛 내용\n", "lint") + "\n끝 메모\n")
    before = tree(kb)
    first = run(cfg)
    after = tree(kb)
    changed = {rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel)}
    assert changed == {LINT_MD}
    assert first["written"] is True
    data = (kb / LINT_MD).read_bytes()
    assert data.startswith("# 점검 보고\n\n사람이 쓴 메모.\r\n\n".encode("utf-8"))
    assert data.endswith("\n끝 메모\n".encode("utf-8"))
    assert first["total"] >= 5
    assert not (kb / ".state" / "logs").exists()

    second = run(cfg)  # 같은 시각, 같은 볼트 → 쓰지 않음
    assert second["written"] is False
    assert tree(kb) == after


def test_creates_lint_page_from_template(kb, cfg, no_llm):
    result = run(cfg)
    assert result["written"] is True
    text = report(kb)
    assert text.startswith("# 점검 보고\n")
    assert "<!-- kb:lint:start -->" in text and "## 마커 손상 파일 (0)" in text


def test_broken_lint_page_is_reported_not_overwritten(kb, cfg, no_llm, capsys):
    broken = "# 점검 보고\n<!-- kb:lint:start -->\n사람이 망가뜨림\n"
    write(kb, LINT_MD, broken)
    result = run(cfg)
    assert result["write_error"] and result["written"] is False
    assert report(kb) == broken
    assert any(i["page"] == LINT_MD for i in items(result, "broken_markers"))
    assert lint.main(["--root", str(kb)]) == 0
    out = capsys.readouterr().out
    assert "쓰지 못했습니다" in out and "## 고아 페이지" in out


def test_cli_exit_zero_json_and_no_network(kb, run_kb):
    local(kb)
    busy_vault(kb)
    proc = run_kb("lint")
    assert proc.returncode == 0, proc.stderr
    assert "lint: 고아 페이지 1" in proc.stdout and "→ wiki/_lint.md" in proc.stdout
    proc = run_kb("lint", "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["counts"]["orphans"] == 1
    # daily의 [[없는페이지]] + decisions.md의 [[KB-12]]·[[2026-10-01]]·[[OPS-3]](파일 없음)
    assert sorted(i["target"] for i in data["sections"]["broken_links"]["items"]) == ["2026-10-01", "KB-12", "OPS-3", "없는페이지"]
    assert data["contradictions"] == {"status": "not_run"}
    assert "body" not in data
    assert not (kb / ".state" / "logs").exists()


def test_main_text_output(kb, cfg, no_llm, capsys):
    busy_vault(kb)
    assert lint.main(["--root", str(kb)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("lint: 고아 페이지 1 · 오래된 stub 1")


# ---------------------------------------------------------------- T5 모순 후보


SUMMARY_KB = "KB 캐시의 updated 시각은 KST로 저장한다.\n\n- 출처: [[2026-11-04]] 09:12\n- 갱신: 2026-11-05 (main)\n"


def t5_vault(root: Path) -> None:
    write(root, "wiki/projects/KB.md", entity("project", "KB", summary=SUMMARY_KB, mentions="- [[2026-11-04]] 09:12 [[KB-12]] 논의\n"))
    write(root, "wiki/people/김철수.md", entity("person", "김철수", summary="김철수는 리뷰를 맡는다.\n", mentions="- [[2026-11-04]] 09:12 @김철수\n"))
    write(root, "wiki/topics/배포.md", entity("topic", "배포", summary="배포는 매주 금요일에 한다.\n", extra="[[projects/KB]]\n"))
    write(root, "wiki/decisions.md", DECISIONS)


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


def llm_cfg(root: Path, base_url: str, *, environment="home", enabled=True, **tasks) -> config.Config:
    data = {
        "environment": environment,
        "jira": {"project_keys": ["KB", "OPS"]},
        "llm": {"enabled": enabled, "profiles": {"main": {"base_url": base_url, "model": "mock"}}, "fallback_order": ["main"]},
    }
    if tasks:
        data["llm"]["tasks"] = tasks
    config.save_local(root, data)
    return config.load(root)


def chat_requests(mock) -> list[dict]:
    return [e["json"] for e in mock.log if e["path"].endswith("/chat/completions") and isinstance(e.get("json"), dict)]


def test_t5_reports_conflicts(kb, llm):
    t5_vault(kb)
    reply = {"conflicts": [
        {"a": "S1", "b": "D1", "why": "요약은 KST, 결정은 UTC로 저장한다."},
        {"a": "[D1]", "b": "s1", "why": "중복(순서만 다름)"},
    ]}
    mock = llm(canned={"contradictions": json.dumps(reply, ensure_ascii=False)})
    cfg = llm_cfg(kb, f"{mock.base_url}/v1")
    result = run(cfg, llm=True)

    requests_ = chat_requests(mock)
    assert len(requests_) == 1
    system = requests_[0]["messages"][0]["content"]
    user = requests_[0]["messages"][-1]["content"]
    assert system.startswith("KB-TASK: contradictions\n")
    assert "모순 후보 (T5)" in system
    assert "[S1] KB 캐시의 updated 시각은 KST로 저장한다." in user
    assert "- 출처:" not in user  # 요약의 출처·갱신 줄은 빼고 보낸다
    assert "[D1] (제안) 2026-10-01 캐시 updated 시각은 UTC로 저장" in user
    assert "src:" not in user and "<!--" not in user
    assert "버린 안" not in user  # 기각은 보내지 않는다
    assert "## 사람 김철수" in user  # @김철수 결정과 사람 요약이 같은 묶음
    assert "## 주제 배포" in user and "배포는 금요일에 하지 않는다" in user

    assert result["contradictions"]["status"] == "ok"
    conflicts = items(result, "contradictions")
    assert len(conflicts) == 1  # 같은 쌍은 한 번만
    text = lint_region(kb)
    assert "## 모순 후보 (1)" in text
    assert "- [[wiki/projects/KB|projects/KB]] 요약 ↔ 결정 「2026-10-01 캐시 updated 시각은 UTC로 저장」(제안): 요약은 KST, 결정은 UTC로 저장한다." in text
    assert "- 검사: 2026-11-05 18:30 (main)" in text

    # --llm 없이 다시 돌면 이전 결과를 그대로 두고 LLM은 부르지 않는다
    again = run(cfg)
    assert len(chat_requests(mock)) == 1
    assert again["contradictions"] == {"status": "not_run"}
    text = lint_region(kb)
    assert "## 모순 후보 · 이전 결과" in text
    assert lint.T5_CARRIED in text and "요약은 KST, 결정은 UTC로 저장한다." in text
    run(cfg)  # 두 번 옮겨도 안내 줄이 쌓이지 않는다
    assert lint_region(kb).count(lint.T5_CARRIED) == 1


def test_t5_no_conflicts(kb, llm):
    t5_vault(kb)
    mock = llm(canned={"contradictions": '{"conflicts": []}'})
    result = run(llm_cfg(kb, f"{mock.base_url}/v1"), llm=True)
    assert result["contradictions"]["status"] == "ok" and result["counts"]["contradictions"] == 0
    assert "- 모순 후보 없음" in lint_region(kb)


def test_t5_unknown_ids_are_rejected(kb, llm):
    t5_vault(kb)
    mock = llm(canned={"contradictions": '{"conflicts": [{"a": "S1", "b": "D99", "why": "지어냄"}]}'})
    result = run(llm_cfg(kb, f"{mock.base_url}/v1"), llm=True)
    assert result["contradictions"]["status"] == "skipped"
    assert len(chat_requests(mock)) == 2  # 스키마 실패 1회 재시도(D40)
    assert "지어냄" not in report(kb)
    assert "LLM 응답을 받지 못해" in lint_region(kb)


def test_t5_disabled_is_a_note(kb, llm):
    t5_vault(kb)
    mock = llm(canned={"contradictions": '{"conflicts": []}'})
    result = run(llm_cfg(kb, f"{mock.base_url}/v1", enabled=False), llm=True)
    assert result["contradictions"]["status"] == "disabled"
    assert mock.log == []
    assert "LLM이 꺼져 있어 모순 검사를 건너뜁니다" in lint_region(kb)
    assert "## 모순 후보\n" in lint_region(kb)  # 검사하지 않았으니 개수 없음


def test_t5_company_public_host_refused_exit_zero(kb, run_kb):
    t5_vault(kb)
    llm_cfg(kb, "https://api.deepinfra.com/v1/openai", environment="company")
    proc = run_kb("lint", "--llm")
    assert proc.returncode == 0, proc.stderr
    assert "회사 환경에서 공개 LLM 주소" in proc.stdout
    assert "회사 환경에서 공개 LLM 주소" in lint_region(kb)


def test_t5_nothing_to_compare(kb, llm):
    write(kb, "wiki/projects/KB.md", entity("project", "KB", summary="요약만 있다.\n"))
    mock = llm(canned={"contradictions": '{"conflicts": []}'})
    result = run(llm_cfg(kb, f"{mock.base_url}/v1"), llm=True)
    assert result["contradictions"]["status"] == "nothing"
    assert chat_requests(mock) == []


def test_t5_unreachable_profile_is_skipped(kb):
    t5_vault(kb)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    result = run(llm_cfg(kb, f"http://127.0.0.1:{port}/v1"), llm=True)
    assert result["contradictions"]["status"] == "skipped"
    assert lint.main(["--root", str(kb), "--llm"]) == 0


def test_t5_splits_large_input_and_merges(kb, llm):
    t5_vault(kb)
    long = "가" * 900
    write(kb, "wiki/topics/긴주제.md", entity("topic", "긴주제", summary=f"긴주제 요약 {long}\n"))
    extra = "".join(f"## 2026-11-0{n} 긴주제 결정 {n}\n- 상태: 확정\n- 내용: 긴주제 {long}\n\n" for n in (2, 3))
    write(kb, "wiki/decisions.md", DECISIONS.replace("<!-- kb:decisions:end -->", extra + "<!-- kb:decisions:end -->"))
    reply = {"conflicts": [{"a": "S1", "b": "D1", "why": "어긋남"}]}
    mock = llm(canned={"contradictions": json.dumps(reply, ensure_ascii=False)})
    cfg = llm_cfg(kb, f"{mock.base_url}/v1", contradictions={"profile": "main", "reasoning": "on", "max_input_tokens": 100, "max_output_tokens": 512})
    result = run(cfg, llm=True)
    sent = chat_requests(mock)
    assert len(sent) >= 2  # 한도를 넘어 나눠 보냈다
    for body in sent:
        user = body["messages"][-1]["content"]
        assert user.count("[") >= 2
    assert result["contradictions"]["calls"] == len(sent)
    assert result["counts"]["contradictions"] == 1  # 같은 쌍은 합친다


def test_t5_over_budget_pair_rejected_by_server_is_skipped(kb, llm):
    """P-100: 한도가 아주 작으면 최소 한 쌍이 한도를 넘는다. 서버가 거절하면 그 묶음은 못 본 채 skipped, 종료 0."""
    long = "가" * 3000
    write(kb, "wiki/topics/큰주제.md", entity("topic", "큰주제", summary=f"{long}\n"))
    decisions = "".join(
        f"## 2026-11-0{n} 큰주제 결정 {n}\n- 상태: 확정\n- 관련: 큰주제 {'마' * 800}\n- 내용: 큰주제 {'바' * 800}\n- 근거: {'사' * 800}\n\n"
        for n in (1, 2)
    )
    write(kb, "wiki/decisions.md", "# 결정 로그\n\n" + region(decisions, "decisions"))
    mock = llm(canned={"contradictions": '{"conflicts": []}'}, context_length=2000)
    data = {
        "environment": "home",
        "jira": {"project_keys": ["KB", "OPS"]},
        "llm": {
            "enabled": True,
            "profiles": {"main": {"base_url": f"{mock.base_url}/v1", "model": "mock", "detected": {"context_tokens": 2000}}},
            "fallback_order": ["main"],
        },
    }
    config.save_local(kb, data)
    cfg = config.load(kb)
    result = run(cfg, llm=True)
    sent = chat_requests(mock)
    assert sent, "한도를 넘어도 최소 한 쌍은 보낸다"
    for body in sent:
        user = body["messages"][-1]["content"]
        assert "[S1]" in user and ("[D1]" in user or "[D2]" in user)  # 요청마다 요약 + 결정 하나
        total = lint.estimate_tokens("".join(m["content"] for m in body["messages"]))
        assert total > lint.LlmClient(cfg).input_limit(lint.TASK)  # 최소 한 쌍이 프로필의 입력 한도보다 크다
    t5 = result["contradictions"]
    assert t5["status"] == "skipped" and result["counts"]["contradictions"] == 0
    assert t5["note"].startswith("LLM 응답을 받지 못해") and "입력 한도 초과" in t5["note"]
    assert "입력 한도 초과" in lint_region(kb)
    assert lint.main(["--root", str(kb), "--llm"]) == 0


def _many_topics(root: Path, count: int = 5) -> None:
    """t5_vault + 주제 count개(요약 400자)와 주제마다 관련 결정 하나(400자)."""
    t5_vault(root)
    extra = ""
    for n in range(1, count + 1):
        write(root, f"wiki/topics/주제{n}.md", entity("topic", f"주제{n}", summary=f"주제{n} 요약 " + "나" * 400 + "\n"))
        extra += f"## 2026-11-0{n} 주제{n} 결정\n- 상태: 확정\n- 내용: 주제{n} " + "다" * 400 + "\n\n"
    write(root, "wiki/decisions.md", DECISIONS.replace("<!-- kb:decisions:end -->", extra + "<!-- kb:decisions:end -->"))


def test_t5_small_context_fallback_sends_every_group(kb, llm):
    """답한 프로필(fallback)의 한도가 작으면 그 한도로 다시 나눠 나머지를 보낸다. 빠뜨린 채 'ok'라 하지 않는다."""
    _many_topics(kb)
    dead = llm(json_mode="reject")  # main: 모든 채팅 요청에 400
    small = llm(canned={"contradictions": '{"conflicts": []}'})
    data = {
        "environment": "home",
        "jira": {"project_keys": ["KB", "OPS"]},
        "llm": {
            "enabled": True,
            "profiles": {
                "main": {"base_url": f"{dead.base_url}/v1", "model": "mock", "detected": {"context_tokens": 262144}},
                "fallback": {"base_url": f"{small.base_url}/v1", "model": "mock", "detected": {"context_tokens": 2000}},
            },
            "fallback_order": ["main", "fallback"],
        },
    }
    config.save_local(kb, data)
    cfg = config.load(kb)
    groups, _ = lint._t5_groups(lint._load_vault(cfg), lint._decisions((kb / "wiki" / "decisions.md").read_bytes()))
    assert len(groups) == 8
    result = run(cfg, llm=True)

    assert chat_requests(dead), "main을 먼저 시도해야 한다"
    users = [body["messages"][-1]["content"] for body in chat_requests(small)]
    assert len(users) >= 2
    sent = "\n".join(users)
    for group in groups:
        assert f"## {group.header}" in sent, group.header
        for ident, _ in group.items:
            assert f"[{ident}]" in sent, ident
    assert result["contradictions"]["status"] == "ok"
    assert "- 모순 후보 없음" in lint_region(kb)


class _HalfClient:
    """첫 호출만 답하는 가짜 LLM 클라이언트(한도 작음). 나머지 묶음은 응답 없음."""

    enabled = True

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.users: list[str] = []

    def input_limit(self, task: str, profile: str | None = None) -> int:
        return self.limit

    def run_task(self, task, messages_for, *, validate, needs_vision=False):
        from lib.llm_client import TaskOutcome

        messages = messages_for(self.limit)
        self.users.append(messages[-1]["content"])
        if len(self.users) > 1:
            return TaskOutcome("skipped", None, None, [{"profile": "main", "status": "network"}])
        data = {"conflicts": []}
        validate(data)
        return TaskOutcome("ok", data, "main", [{"profile": "main", "status": "ok"}])


def test_t5_unsent_groups_make_partial_with_count(kb):
    _many_topics(kb)
    cfg = local(kb, llm={"enabled": True, "profiles": {"main": {"base_url": "http://127.0.0.1:9/v1", "model": "mock"}}})
    vault = lint._load_vault(cfg)
    decisions = lint._decisions((kb / "wiki" / "decisions.md").read_bytes())
    client = _HalfClient(limit=1500)
    outcome = lint.contradictions(cfg, vault, decisions, client=client)
    assert len(client.users) >= 2
    assert outcome["status"] == "partial"
    groups = outcome["groups"]
    sections: dict[str, str] = {}
    for part in client.users[0].split("\n## ")[1:]:
        header, _, body = part.partition("\n")
        sections[header] = body
    unchecked = sum(
        1 for g in lint._t5_groups(vault, decisions)[0]
        if any(f"[{ident}]" not in sections.get(g.header, "") for ident, _ in g.items)
    )
    assert 1 <= unchecked < groups
    assert f"묶음 {groups}개 중 {unchecked}개는 다 검사하지 못했습니다" in outcome["note"]
    section = lint._t5_section(vault, outcome, None)
    assert any(f"{unchecked}개는 다 검사하지 못했습니다" in note for note in section.notes)
    assert "- 모순 후보 없음" in section.notes  # 부분 결과도 개수는 센다(빠진 묶음은 위 안내로)


# ---------------------------------------------------------------- 단위


def test_validate_contradictions():
    lint.validate_contradictions({"conflicts": []})
    lint.validate_contradictions({"conflicts": [{"a": "S1", "b": "[D2]", "why": "x"}]}, {"S1", "D2"})
    bad = [
        {},
        {"conflicts": {}},
        {"conflicts": ["x"]},
        {"conflicts": [{"a": "S1", "b": "D2"}]},
        {"conflicts": [{"a": "S1", "b": "D2", "why": " "}]},
        {"conflicts": [{"a": "S1", "b": "S1", "why": "같음"}]},
    ]
    for data in bad:
        with pytest.raises(ValueError):
            lint.validate_contradictions(data)
    with pytest.raises(ValueError):
        lint.validate_contradictions({"conflicts": [{"a": "S1", "b": "D9", "why": "x"}]}, {"S1", "D2"})


def test_norm_id():
    assert lint.norm_id("[s 01]") == "S1"
    assert lint.norm_id("D12") == "D12"
    assert lint.norm_id("KB-12") is None


def test_chunks_repeat_summary_when_group_is_split():
    group = lint._Group("프로젝트 KB", [("S1", "요약 " * 50)] + [(f"D{n}", "결정 " * 100) for n in range(1, 6)])
    chunks = lint._t5_chunks([group], budget=150)
    assert len(chunks) >= 2
    for chunk in chunks:
        ids = [i for g in chunk for i, _ in g.items]
        assert ids[0] == "S1" and len(ids) >= 2
    sent = [i for chunk in chunks for g in chunk for i, _ in g.items if i.startswith("D")]
    assert sent == [f"D{n}" for n in range(1, 6)]


def test_fit_keeps_first_pair_even_over_budget():
    """P-100(의도한 동작): 첫 묶음의 두 항목은 한도를 넘어도 보낸다. 하나로는 모순을 볼 수 없다."""
    group = lint._Group("주제 큰것 (wiki/topics/큰것.md)", [("S1", "가" * 900), ("D1", "나" * 900), ("D2", "다" * 900)])
    budget = 200
    chosen = lint._fit([group, lint._Group("주제 다음", [("S2", "라"), ("D3", "마")])], budget)
    assert [[i for i, _ in g.items] for g in chosen] == [["S1", "D1"]]
    assert lint.estimate_tokens(lint._t5_user(chosen)) > budget
    roomy = lint._fit([group], 10_000)
    assert [i for i, _ in roomy[0].items] == ["S1", "D1", "D2"]


def test_chunks_without_summary_never_send_a_single_decision():
    """요약 없는 묶음을 나눌 때도 조각마다 두 항목 이상이고(하나로는 모순이 없다), 빠지는 결정이 없다."""
    group = lint._Group("관련 대상이 없는 결정", [(f"D{n}", f"결정{n} " + "나" * 900) for n in range(1, 4)])
    chunks = lint._t5_chunks([group], budget=200)
    pieces = [[i for i, _ in g.items] for chunk in chunks for g in chunk]
    assert len(pieces) >= 2
    assert all(len(ids) >= 2 for ids in pieces), pieces
    assert {i for ids in pieces for i in ids} == {"D1", "D2", "D3"}


def test_t5_no_summary_group_over_budget_checks_every_decision(kb):
    """요약 없는 묶음이 한도를 넘어도 요청마다 결정이 둘 이상이고, 다 봤으면 ok다(빠뜨린 채 ok라 하지 않는다)."""
    decisions = "".join(f"## 2026-11-0{n} 따로 결정 {n}\n- 상태: 확정\n- 내용: {'나' * 450}{n}\n\n" for n in (1, 2, 3))
    write(kb, "wiki/decisions.md", "# 결정 로그\n\n" + region(decisions, "decisions"))
    cfg = local(kb, llm={"enabled": True, "profiles": {"main": {"base_url": "http://127.0.0.1:9/v1", "model": "mock"}}})
    vault = lint._load_vault(cfg)
    parsed = lint._decisions((kb / "wiki" / "decisions.md").read_bytes())
    groups, _ = lint._t5_groups(vault, parsed)
    assert [g.header for g in groups] == ["관련 대상이 없는 결정"]

    class _Client(_HalfClient):
        def run_task(self, task, messages_for, *, validate, needs_vision=False):
            from lib.llm_client import TaskOutcome

            self.users.append(messages_for(self.limit)[-1]["content"])
            data = {"conflicts": []}
            validate(data)
            return TaskOutcome("ok", data, "main", [{"profile": "main", "status": "ok"}])

    client = _Client(limit=1)  # 사용자 프롬프트 한도는 바닥값(200)
    outcome = lint.contradictions(cfg, vault, parsed, client=client)
    assert client.users
    for user in client.users:
        assert sum(f"[D{n}]" in user for n in (1, 2, 3)) >= 2, user
    assert all(any(f"[D{n}]" in user for user in client.users) for n in (1, 2, 3))
    assert outcome["status"] == "ok"


def test_write_takes_lock(vault, monkeypatch):
    cfg = config.load(vault, environ={})
    calls: list[str] = []

    @contextlib.contextmanager
    def spy_lock(root, purpose="작업", *args, **kwargs):
        calls.append(purpose)
        raise core.LockBusy("잠금 테스트")
        yield  # pragma: no cover

    monkeypatch.setattr(core, "kb_lock", spy_lock)
    with pytest.raises(core.LockBusy):
        lint._write(cfg, "- 없음", None)
    assert calls


def test_decision_parser_handles_comments_and_headings():
    data = DECISIONS.encode("utf-8")
    parsed = lint._decisions(data)
    assert [(d.day, d.status) for d in parsed] == [("2026-10-01", "제안"), ("2026-11-01", "제안"), ("2026-09-01", "확정"), ("2026-09-02", "기각")]
    assert parsed[0].fields["출처"] == "[[2026-10-01]] 09:12"
