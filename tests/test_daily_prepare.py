"""daily_prepare: 뼈대, today 구역(merge), carryover(이월·[>] 표시), 사람 편집 보호, CRLF, 한글 경로."""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest

import daily_prepare
from lib import config, core, managed, notes

D05 = dt.date(2026, 11, 5)
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LOCK_CHILD = r"""
import sys
sys.path.insert(0, sys.argv[1])
from lib import core
with core.kb_lock(sys.argv[2], "다른 작업"):
    print("locked", flush=True)
    sys.stdin.read()
"""


@contextlib.contextmanager
def held_lock(root: Path):
    """다른 프로세스가 `.state/kb.lock`을 잡고 있는 동안."""
    child = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", LOCK_CHILD, str(SCRIPTS), str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        yield
    finally:
        child.stdin.close()
        child.wait(timeout=10)


@pytest.fixture
def cfg(vault):
    config.save_local(vault, {"environment": "home", "jira": {"project_keys": ["KB", "OPS"]}})
    return config.load(vault)


def _write(path: Path, text: str, *, crlf: bool = False) -> bytes:
    data = text.encode("utf-8")
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _daily(vault: Path, day: str) -> Path:
    return vault / "daily" / f"{day}.md"


def _view(vault: Path, name: str, rows: list[tuple[str, str, str, str]]) -> None:
    lines = [
        "---",
        f"view: {name}",
        'jql: "x"',
        "synced_at: 2026-11-05T08:00:00+09:00",
        "---",
        f"# {name}",
        "",
        "| 키 | 제목 | 상태 | 담당 | 마감 | 갱신 |",
        "|---|---|---|---|---|---|",
    ]
    for key, title, status, due in rows:
        lines.append(f"| [[{key}]] | {title} | {status} | 홍길동 | {due} | 2026-11-04 |")
    _write(vault / "jira" / "_views" / f"{name}.md", "\n".join(lines) + "\n")


def _region(data: bytes, name: str) -> list[str]:
    body = managed.read_region(data, name)
    return notes.split_lines(body.decode("utf-8")) if body else []


def _set_region(path: Path, name: str, lines: list[str]) -> None:
    path.write_bytes(managed.replace_region(path.read_bytes(), name, "\n".join(lines)))


def _snapshot(vault: Path) -> dict[str, bytes]:
    return {p.relative_to(vault).as_posix(): p.read_bytes() for p in sorted((vault / "daily").glob("*.md"))}


# ---------------------------------------------------------------- 뼈대


def test_skeleton_created_with_note_when_views_missing(cfg, vault):
    result = daily_prepare.run(cfg, D05)
    path = _daily(vault, "2026-11-05")
    assert result.path == path and result.created and path.exists()
    data = path.read_bytes()
    assert data.startswith(b"---\ndate: 2026-11-05\n---\n## \xec\x98\xa4\xeb\x8a\x98\n")  # ## 오늘
    assert _region(data, "today") == ["- (Jira 동기화 전)"]
    assert _region(data, "carryover") == []
    text = data.decode("utf-8")
    assert text.index("## 오늘") < text.index("## 이월") < text.index("## 기록")
    assert text.endswith("## 기록\n")
    # 같은 조건에서 다시 돌리면 바이트 그대로
    again = daily_prepare.run(cfg, D05)
    assert not again.changed and path.read_bytes() == data


def test_prepare_contract_returns_path(cfg, vault):
    assert daily_prepare.prepare(cfg, D05) == _daily(vault, "2026-11-05")


def test_missing_headings_are_appended_below_existing_text(cfg, vault):
    path = _daily(vault, "2026-11-05")
    original = _write(path, "- 09:00 아침 메모\n## 기록\n- 09:12 [[KB-12]] 논의\n- [ ] 개인: 오늘 할 일\n")
    daily_prepare.run(cfg, D05)
    data = path.read_bytes()
    assert data.startswith(original)  # 기존 바이트는 그대로, 아래에 덧붙임
    tail = data[len(original):].decode("utf-8")
    assert tail.startswith("\n## 오늘\n<!-- kb:today:start -->\n")
    assert "## 이월\n<!-- kb:carryover:start -->\n<!-- kb:carryover:end -->\n" in tail
    assert "## 기록" not in tail
    # 기록 절의 내용은 그대로
    lines = [line.text for line in notes.record_lines("daily/2026-11-05.md", data)]
    assert lines == ["- 09:12 [[KB-12]] 논의", "- [ ] 개인: 오늘 할 일"]
    # 오늘 파일의 [ ]는 이월 대상이 아니다
    assert "[>]" not in data.decode("utf-8")


def test_file_without_any_heading_gets_full_skeleton_parts(cfg, vault):
    path = _daily(vault, "2026-11-05")
    original = _write(path, "자유 메모 (줄끝 없음)")
    daily_prepare.run(cfg, D05)
    data = path.read_bytes()
    assert data.startswith(original + b"\n\n## ")
    text = data.decode("utf-8")
    assert text.index("## 오늘") < text.index("## 이월") < text.index("## 기록")
    assert {name for name, _s, _e in managed.spans(data)} == {"today", "carryover"}


def test_heading_without_region_gets_region_under_heading(cfg, vault):
    path = _daily(vault, "2026-11-05")
    _write(path, "## 오늘\n사람이 쓴 줄\n\n## 이월\n\n## 기록\n- 10:00 기록\n")
    daily_prepare.run(cfg, D05)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("## 오늘\n<!-- kb:today:start -->\n- (Jira 동기화 전)\n<!-- kb:today:end -->\n사람이 쓴 줄\n")
    assert "## 이월\n<!-- kb:carryover:start -->\n<!-- kb:carryover:end -->\n\n## 기록\n- 10:00 기록\n" in text
    assert text.count("## 오늘") == 1 and text.count("## 기록") == 1


def test_malformed_markers_in_today_file_refused(cfg, vault):
    path = _daily(vault, "2026-11-05")
    original = _write(path, "## 오늘\n<!-- kb:today:start -->\n## 기록\n")
    with pytest.raises(core.KbError, match="관리 구역"):
        daily_prepare.run(cfg, D05)
    assert path.read_bytes() == original


# ---------------------------------------------------------------- today 구역


def test_today_region_from_views_and_merge_keeps_checked(cfg, vault):
    _view(vault, "today", [("KB-12", "캐시 front matter 확정", "In Progress", "2026-11-06"), ("KB-7", "색인 정리", "To Do", "")])
    _view(vault, "blocked", [("OPS-3", "배포 체크리스트", "Blocked", "")])
    path = _daily(vault, "2026-11-05")
    daily_prepare.run(cfg, D05)
    assert _region(path.read_bytes(), "today") == [
        "- [ ] [[KB-12]] 캐시 front matter 확정 · In Progress · 마감 11-06",
        "- [ ] [[KB-7]] 색인 정리 · To Do",
        "- [ ] [[OPS-3]] 배포 체크리스트 · 막힘",
    ]
    # 사람이 KB-12와 OPS-3에 체크
    data = path.read_bytes()
    data = data.replace("- [ ] [[KB-12]]".encode("utf-8"), "- [x] [[KB-12]]".encode("utf-8"))
    data = data.replace("- [ ] [[OPS-3]]".encode("utf-8"), "- [x] [[OPS-3]]".encode("utf-8"))
    path.write_bytes(data)
    # 뷰 변경: KB-12·KB-7 빠짐, OPS-3 제목 바뀜, KB-20 새로
    _view(vault, "today", [("KB-20", "새 일감", "To Do", "2026-11-10")])
    _view(vault, "blocked", [("OPS-3", "배포 체크리스트 v2", "Blocked", "")])
    daily_prepare.run(cfg, D05)
    assert _region(path.read_bytes(), "today") == [
        "- [ ] [[KB-20]] 새 일감 · To Do · 마감 11-10",
        "- [x] [[OPS-3]] 배포 체크리스트 v2 · 막힘",
        "- [x] [[KB-12]] 캐시 front matter 확정 · In Progress · 마감 11-06",
    ]
    before = path.read_bytes()
    daily_prepare.run(cfg, D05)
    assert path.read_bytes() == before  # 멱등


def test_note_line_replaced_once_views_appear(cfg, vault):
    path = _daily(vault, "2026-11-05")
    daily_prepare.run(cfg, D05)
    assert _region(path.read_bytes(), "today") == ["- (Jira 동기화 전)"]
    _view(vault, "today", [])  # 동기화했지만 일감 없음
    daily_prepare.run(cfg, D05)
    assert _region(path.read_bytes(), "today") == []


def test_key_in_both_views_rendered_once_as_blocked(cfg, vault):
    _view(vault, "today", [("KB-12", "제목", "In Progress", "2026-11-06")])
    _view(vault, "blocked", [("KB-12", "제목", "Blocked", "2026-11-06")])
    daily_prepare.run(cfg, D05)
    assert _region(_daily(vault, "2026-11-05").read_bytes(), "today") == ["- [ ] [[KB-12]] 제목 · 막힘 · 마감 11-06"]


def test_today_merge_keeps_human_lines_verbatim_and_in_order(cfg, vault):
    _view(vault, "today", [("KB-12", "제목", "In Progress", "2026-11-06")])
    path = _daily(vault, "2026-11-05")
    daily_prepare.run(cfg, D05, carryover=False)
    assert _region(path.read_bytes(), "today") == ["- [ ] [[KB-12]] 제목 · In Progress · 마감 11-06"]
    _set_region(path, "today", [
        "- 사람이 맨 앞에 쓴 메모",
        "- [x] [[KB-12]] 제목 · In Progress · 마감 11-06",
        "- [ ] [[KB-12]] 리뷰 코멘트도 답하기 (사람이 쓴 할 일)",  # 같은 키의 둘째 줄
        "- [x] [[KB-99]] 사람 메모: 어제 끝냄",  # 뷰에 없는 키의 사람 줄
        "- [ ] [[KB-99]] 사람 메모 둘째 줄",  # 뷰에 없고 체크도 안 된 사람 줄
        "그냥 글",
    ])
    _view(vault, "today", [("KB-12", "제목 v2", "In Review", "2026-11-06"), ("KB-7", "새 일감", "To Do", "")])
    result = daily_prepare.run(cfg, D05, carryover=False)
    assert _region(path.read_bytes(), "today") == [
        "- 사람이 맨 앞에 쓴 메모",
        "- [x] [[KB-12]] 제목 v2 · In Review · 마감 11-06",  # 도구 줄만 다시 그림(체크 유지)
        "- [ ] [[KB-12]] 리뷰 코멘트도 답하기 (사람이 쓴 할 일)",
        "- [x] [[KB-99]] 사람 메모: 어제 끝냄",
        "- [ ] [[KB-99]] 사람 메모 둘째 줄",
        "그냥 글",
        "- [ ] [[KB-7]] 새 일감 · To Do",
    ]
    assert result.today_items == 2  # 도구가 그린 일감 줄 수(사람 줄 제외)
    before = path.read_bytes()
    daily_prepare.run(cfg, D05, carryover=False)
    assert path.read_bytes() == before  # 멱등


def test_today_line_with_human_annotation_is_kept_verbatim(cfg, vault):
    _view(vault, "today", [("KB-12", "제목", "In Progress", ""), ("KB-7", "색인", "To Do", "")])
    path = _daily(vault, "2026-11-05")
    daily_prepare.run(cfg, D05, carryover=False)
    _set_region(path, "today", [
        "- [x] [[KB-12]] 제목 · In Progress — 오후에 확인함",  # 사람이 생성된 줄 끝에 덧붙임
        "- [ ] [[KB-7]] 색인 · To Do",
    ])
    _view(vault, "today", [("KB-12", "제목 v2", "Done", ""), ("KB-7", "색인 v2", "To Do", "")])
    daily_prepare.run(cfg, D05, carryover=False)
    assert _region(path.read_bytes(), "today") == [
        "- [x] [[KB-12]] 제목 · In Progress — 오후에 확인함",  # 덮지도 겹쳐 더하지도 않는다
        "- [ ] [[KB-7]] 색인 v2 · To Do",
    ]


def test_merge_today_unit_rules():
    items = [("KB-1", "[[KB-1]] 제목 · To Do")]
    # 지금 뷰 본문과 같은 줄은 도구 줄: 체크 유지
    assert daily_prepare.merge_today(["- [x] [[KB-1]] 제목 · To Do"], items) == ["- [x] [[KB-1]] 제목 · To Do"]
    # 지난번 본문(previous)과 같으면 도구 줄로 보고 다시 그린다
    assert daily_prepare.merge_today(
        ["- [x] [[KB-1]] 옛 제목 · To Do"], items, previous={"KB-1": "[[KB-1]] 옛 제목 · To Do"}
    ) == ["- [x] [[KB-1]] 제목 · To Do"]
    # 기록이 없으면 사람 줄로 보고 그대로 둔다(지우지 않는다)
    assert daily_prepare.merge_today(["- [x] [[KB-1]] 옛 제목 · To Do"], items) == ["- [x] [[KB-1]] 옛 제목 · To Do"]
    # 뷰에서 빠진 도구 줄은 [x]만 남는다
    assert daily_prepare.merge_today(["- [ ] [[KB-1]] 제목 · To Do", "- [x] [[KB-2]] 끝 · Done"], [],
                                     previous={"KB-1": "[[KB-1]] 제목 · To Do", "KB-2": "[[KB-2]] 끝 · Done"}) == [
        "- [x] [[KB-2]] 끝 · Done"
    ]


# ---------------------------------------------------------------- carryover


EARLY = """---
date: 2026-11-01
---
## 오늘
<!-- kb:today:start -->
- [ ] [[KB-12]] 뷰 일감 · In Progress
<!-- kb:today:end -->

## 이월
<!-- kb:carryover:start -->
<!-- kb:carryover:end -->

## 기록
- 09:00 메모
- [ ] 개인: 경비 정산
- [ ] 10:30 개인: 회의실 예약
- [x] 개인: 끝난 일
- [ ] [[KB-12]] 키 있는 할 일
- [ ] OPS-3 맨 글자 키도 제외
- [>] 개인: 이미 옮긴 일 (2026-10-31로 이월)
```
- [ ] 코드 블록 안
```
"""

MID = """## 기록
- [ ] 개인: 셋째 날 할 일
- [ ] 링크만 https://jira.example.invalid/browse/KB-9 참고

## 메모
- [ ] 기록 절 밖이라 제외
"""


def test_carryover_across_skipped_days_marks_sources(cfg, vault):
    _write(_daily(vault, "2026-11-01"), EARLY)
    _write(_daily(vault, "2026-11-03"), MID)
    result = daily_prepare.run(cfg, D05)
    today = _daily(vault, "2026-11-05").read_bytes()
    assert _region(today, "carryover") == [
        "- [ ] 개인: 경비 정산 (← 2026-11-01)",
        "- [ ] 개인: 회의실 예약 (← 2026-11-01)",
        "- [ ] 개인: 셋째 날 할 일 (← 2026-11-03)",
        "- [ ] 링크만 https://jira.example.invalid/browse/KB-9 참고 (← 2026-11-03)",
    ]
    assert result.carried == 4 and result.marked == 4 and not result.skipped
    early = _daily(vault, "2026-11-01").read_text(encoding="utf-8")
    assert "- [>] 개인: 경비 정산 (2026-11-05로 이월)\n" in early
    assert "- [>] 10:30 개인: 회의실 예약 (2026-11-05로 이월)\n" in early
    for kept in ("- [x] 개인: 끝난 일\n", "- [ ] [[KB-12]] 키 있는 할 일\n", "- [ ] OPS-3 맨 글자 키도 제외\n",
                 "- [ ] 코드 블록 안\n", "- [ ] [[KB-12]] 뷰 일감 · In Progress\n",
                 "- [>] 개인: 이미 옮긴 일 (2026-10-31로 이월)\n"):
        assert kept in early
    mid = _daily(vault, "2026-11-03").read_text(encoding="utf-8")
    assert "- [>] 개인: 셋째 날 할 일 (2026-11-05로 이월)\n" in mid
    assert "- [ ] 기록 절 밖이라 제외\n" in mid


def test_carryover_rerun_idempotent_and_chain_to_next_day(cfg, vault):
    _write(_daily(vault, "2026-11-01"), EARLY)
    daily_prepare.run(cfg, D05)
    snap = _snapshot(vault)
    again = daily_prepare.run(cfg, D05)
    assert _snapshot(vault) == snap and again.carried == 0 and again.marked == 0 and not again.changed

    # 사람이 하나를 끝냄 → 다음 날에는 남은 것만, 원래 날짜를 지닌 채로
    path5 = _daily(vault, "2026-11-05")
    data = path5.read_bytes().replace("- [ ] 개인: 경비 정산".encode("utf-8"), "- [x] 개인: 경비 정산".encode("utf-8"))
    path5.write_bytes(data + "- [ ] 14:00 개인: 새 할 일\n".encode("utf-8"))  # 기록 끝에 캡처
    daily_prepare.run(cfg, dt.date(2026, 11, 6))
    assert _region(_daily(vault, "2026-11-06").read_bytes(), "carryover") == [
        "- [ ] 개인: 회의실 예약 (← 2026-11-01)",
        "- [ ] 개인: 새 할 일 (← 2026-11-05)",
    ]
    d5 = path5.read_text(encoding="utf-8")
    assert "- [x] 개인: 경비 정산 (← 2026-11-01)\n" in d5  # 끝낸 것은 그대로
    assert "- [>] 개인: 회의실 예약 (← 2026-11-01) (2026-11-06로 이월)\n" in d5
    assert "- [>] 14:00 개인: 새 할 일 (2026-11-06로 이월)\n" in d5
    snap = _snapshot(vault)
    daily_prepare.run(cfg, dt.date(2026, 11, 6))
    assert _snapshot(vault) == snap


def test_existing_item_not_duplicated_even_if_source_unmarked(cfg, vault):
    _write(_daily(vault, "2026-11-04"), "## 기록\n- [ ] 개인: 할 일\n")
    _write(_daily(vault, "2026-11-05"), "## 이월\n<!-- kb:carryover:start -->\n- [x] 개인: 할 일 (← 2026-11-04)\n<!-- kb:carryover:end -->\n")
    result = daily_prepare.run(cfg, D05)
    assert _region(_daily(vault, "2026-11-05").read_bytes(), "carryover") == ["- [x] 개인: 할 일 (← 2026-11-04)"]
    assert result.carried == 0 and result.marked == 1
    assert "- [>] 개인: 할 일 (2026-11-05로 이월)" in _daily(vault, "2026-11-04").read_text(encoding="utf-8")


def test_no_carryover_option_leaves_sources(cfg, vault):
    src = _write(_daily(vault, "2026-11-04"), "## 기록\n- [ ] 개인: 할 일\n")
    daily_prepare.run(cfg, D05, carryover=False)
    assert _daily(vault, "2026-11-04").read_bytes() == src
    assert _region(_daily(vault, "2026-11-05").read_bytes(), "carryover") == []


def test_malformed_earlier_file_skipped_and_reported(cfg, vault):
    bad = _write(_daily(vault, "2026-11-04"), "<!-- kb:carryover:start -->\n## 기록\n- [ ] 개인: 할 일\n")
    result = daily_prepare.run(cfg, D05)
    assert _daily(vault, "2026-11-04").read_bytes() == bad
    assert result.skipped and "daily/2026-11-04.md" in result.skipped[0]
    assert _region(_daily(vault, "2026-11-05").read_bytes(), "carryover") == []


def test_project_keys_required_for_carryover(vault):
    cfg = config.load(vault)  # config.local.yaml 없음 → 키 없음
    if cfg.project_keys:
        pytest.skip("저장소 config.yaml에 project_keys가 있음")
    with pytest.raises(core.ConfigError, match="project_keys") as info:
        daily_prepare.run(cfg, D05)
    assert info.value.exit_code == 4
    assert not _daily(vault, "2026-11-05").exists()
    daily_prepare.run(cfg, D05, carryover=False)
    assert _daily(vault, "2026-11-05").exists()


def test_carryover_keeps_separator_chars_inside_item(cfg, vault):
    """\\u2028·\\x0c는 줄 끝이 아니다: 한 항목으로 옮기고, 원래 줄을 쪼개거나 순서를 바꾸지 않는다."""
    src_path = _daily(vault, "2026-11-04")
    _write(src_path, "## 기록\n- [ ] 보고서 초안 담당 확인 후 제출\n- [ ] 양식\x0c다음 장 \t\n- [ ] 끝에 폼피드\x0c\n- 10:00 끝\n")
    result = daily_prepare.run(cfg, D05)
    assert result.carried == 3 and result.marked == 3
    assert src_path.read_text(encoding="utf-8") == (
        "## 기록\n- [>] 보고서 초안 담당 확인 후 제출 (2026-11-05로 이월)\n"
        "- [>] 양식\x0c다음 장 (2026-11-05로 이월)\n"
        "- [>] 끝에 폼피드\x0c (2026-11-05로 이월)\n- 10:00 끝\n"  # 줄 끝의 공백·탭만 뗀다
    )
    carried = [
        "- [ ] 보고서 초안 담당 확인 후 제출 (← 2026-11-04)",
        "- [ ] 양식\x0c다음 장 (← 2026-11-04)",
        "- [ ] 끝에 폼피드 (← 2026-11-04)",
    ]
    today = _daily(vault, "2026-11-05")
    assert _region(today.read_bytes(), "carryover") == carried
    snap = _snapshot(vault)
    again = daily_prepare.run(cfg, D05)
    assert _snapshot(vault) == snap and not again.changed
    # 구역을 다시 쓸 때도  이 든 기존 항목을 쪼개지 않는다
    with src_path.open("ab") as fh:
        fh.write("- [ ] 새 할 일\n".encode("utf-8"))
    daily_prepare.run(cfg, D05)
    assert _region(today.read_bytes(), "carryover") == [*carried, "- [ ] 새 할 일 (← 2026-11-04)"]


# ---------------------------------------------------------------- 잠금·동시 생성


def test_lock_busy_when_another_process_holds_kb_lock(cfg, vault, run_kb, monkeypatch):
    _write(_daily(vault, "2026-11-04"), "## 기록\n- [ ] 개인: 할 일\n")
    monkeypatch.setenv("KB_LOCK_TIMEOUT", "0.3")
    before = _snapshot(vault)
    with held_lock(vault):
        with pytest.raises(core.LockBusy, match="다른 작업"):
            daily_prepare.run(cfg, D05)
        proc = run_kb("today", "--date", "2026-11-05", env={"KB_LOCK_TIMEOUT": "0.3"})
    assert proc.returncode == 6, proc.stderr
    assert proc.stderr.startswith("오류: 다른 작업이 실행 중입니다")
    assert _snapshot(vault) == before
    assert not _daily(vault, "2026-11-05").exists()


def test_today_file_created_during_write_is_not_overwritten(cfg, vault, monkeypatch):
    """임시 파일을 쓰는 사이에 편집기가 오늘 파일을 먼저 저장하면 덮지 않는다(없을 때만 만들기)."""
    path = _daily(vault, "2026-11-05")
    human = "## 기록\n- 09:00 편집기에서 먼저 저장\n".encode("utf-8")
    real_fsync = os.fsync

    def fsync_then_editor_saves(fd):
        real_fsync(fd)
        if not path.exists():
            path.write_bytes(human)

    monkeypatch.setattr(core.os, "fsync", fsync_then_editor_saves)
    result = daily_prepare.run(cfg, D05, carryover=False)
    assert result.today_skipped and not result.created
    assert path.read_bytes() == human


# ---------------------------------------------------------------- 사람 편집 보호


def test_human_edit_of_source_mid_run_is_skipped_then_fixed_by_rerun(cfg, vault, monkeypatch):
    src_path = _daily(vault, "2026-11-04")
    _write(src_path, "## 기록\n- [ ] 개인: 할 일\n")
    real = daily_prepare._read_bytes

    def edit_after_read(path: Path) -> bytes:
        data = real(path)
        if path == src_path:
            path.write_bytes(data + "- 15:00 사람이 방금 씀\n".encode("utf-8"))
        return data

    monkeypatch.setattr(daily_prepare, "_read_bytes", edit_after_read)
    result = daily_prepare.run(cfg, D05)
    assert result.marked == 0 and any("2026-11-04" in s for s in result.skipped)
    assert src_path.read_text(encoding="utf-8") == "## 기록\n- [ ] 개인: 할 일\n- 15:00 사람이 방금 씀\n"  # 사람 편집 보존
    assert _region(_daily(vault, "2026-11-05").read_bytes(), "carryover") == ["- [ ] 개인: 할 일 (← 2026-11-04)"]

    monkeypatch.setattr(daily_prepare, "_read_bytes", real)
    again = daily_prepare.run(cfg, D05)
    assert again.marked == 1 and again.carried == 0 and not again.skipped
    assert _region(_daily(vault, "2026-11-05").read_bytes(), "carryover") == ["- [ ] 개인: 할 일 (← 2026-11-04)"]
    assert src_path.read_text(encoding="utf-8") == "## 기록\n- [>] 개인: 할 일 (2026-11-05로 이월)\n- 15:00 사람이 방금 씀\n"


def test_human_edit_of_today_file_mid_run_is_skipped(cfg, vault, monkeypatch):
    src_path = _daily(vault, "2026-11-04")
    src = _write(src_path, "## 기록\n- [ ] 개인: 할 일\n")
    path = _daily(vault, "2026-11-05")
    _write(path, "## 기록\n- 09:00 메모\n")
    real = daily_prepare._read_bytes
    human = "## 기록\n- 09:00 메모\n- 09:01 캡처 도중\n".encode("utf-8")

    def edit_after_read(p: Path) -> bytes:
        data = real(p)
        if p == path:
            p.write_bytes(human)
        return data

    monkeypatch.setattr(daily_prepare, "_read_bytes", edit_after_read)
    result = daily_prepare.run(cfg, D05)
    assert result.today_skipped and not result.changed
    assert path.read_bytes() == human  # 덮지 않음
    assert src_path.read_bytes() == src  # 오늘 파일을 못 썼으면 [>] 표시도 하지 않는다


# ---------------------------------------------------------------- CRLF·BOM


def test_crlf_files_keep_crlf(cfg, vault):
    src_path = _daily(vault, "2026-11-04")
    _write(src_path, "## 기록\n- 09:00 메모\n- [ ] 개인: 할 일\n", crlf=True)
    path = _daily(vault, "2026-11-05")
    original = _write(path, "﻿---\ndate: 2026-11-05\n---\n## 기록\n- 09:00 오늘 메모\n", crlf=True)
    _view(vault, "today", [("KB-12", "제목", "To Do", "")])
    daily_prepare.run(cfg, D05)
    data = path.read_bytes()
    assert data.startswith(original)
    assert data.count(b"\n") == data.count(b"\r\n")
    assert _region(data, "today") == ["- [ ] [[KB-12]] 제목 · To Do"]
    assert _region(data, "carryover") == ["- [ ] 개인: 할 일 (← 2026-11-04)"]
    src = src_path.read_bytes()
    assert src == "## 기록\r\n- 09:00 메모\r\n- [>] 개인: 할 일 (2026-11-05로 이월)\r\n".encode("utf-8")
    snap = _snapshot(vault)
    daily_prepare.run(cfg, D05)
    assert _snapshot(vault) == snap


# ---------------------------------------------------------------- CLI·시간


def test_cli_print_path_korean_vault_and_fake_now(cfg, vault, run_kb, fake_now):
    assert "한글 경로" in str(vault)
    fake_now("2026-11-05T23:30:00+00:00")  # 서울 시각으로는 11-06
    proc = run_kb("today", "--print-path")
    assert proc.returncode == 0, proc.stderr
    expected = _daily(vault, "2026-11-06")
    assert Path(proc.stdout.strip()).resolve() == expected.resolve()
    assert expected.exists()
    assert expected.read_bytes().startswith(b"---\ndate: 2026-11-06\n---\n")


def test_kb_fake_now_in_process(cfg, vault, fake_now):
    fake_now("2026-11-05T08:30:00")
    assert daily_prepare.today_main(["--root", str(vault), "--print-path"]) == 0
    assert _daily(vault, "2026-11-05").exists()


def test_cli_summary_date_and_errors(cfg, vault, run_kb):
    _write(_daily(vault, "2026-11-01"), "## 기록\n- [ ] 개인: 할 일\n")
    proc = run_kb("today", "--date", "2026-11-02")
    assert proc.returncode == 0, proc.stderr
    assert "daily/2026-11-02.md: 만듦" in proc.stdout
    assert "새로 이월 1건" in proc.stdout
    assert proc.stdout.strip().splitlines()[-1].endswith("2026-11-02.md")

    proc = run_kb("today", "--date", "2026-13-01")
    assert proc.returncode == 4 and "YYYY-MM-DD" in proc.stderr

    proc = run_kb("today", "--check-done")  # Phase 3: 읽기 전용 제안, daily가 없어도 종료 0 (test_check_done.py)
    assert proc.returncode == 0


def test_cli_without_project_keys_exit_4(vault, run_kb):
    if config.load(vault).project_keys:
        pytest.skip("저장소 config.yaml에 project_keys가 있음")
    proc = run_kb("today", "--date", "2026-11-05")
    assert proc.returncode == 4 and "project_keys" in proc.stderr
    proc = run_kb("today", "--date", "2026-11-05", "--no-carryover", "--print-path")
    assert proc.returncode == 0, proc.stderr


def test_agent_mode_allowed(cfg, vault, run_kb):
    proc = run_kb("today", "--date", "2026-11-05", env={"KB_AGENT": "1"})
    assert proc.returncode == 0, proc.stderr
