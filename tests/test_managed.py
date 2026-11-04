"""lib/managed.py: 관리 구역 바이트 보존 테스트(D17·D24·D25)."""
from __future__ import annotations

import pytest

from lib import managed
from lib.managed import MarkerError

BOM = b"\xef\xbb\xbf"

SAMPLE = (
    "---\ntype: project\n---\n"
    "# KB — 지식 저장고\n\n"
    "## 개요\n사람이 쓴 글. 손대지 않는다.\n\n"
    "## 현재 일감\n"
    "<!-- kb:issues:start -->\n"
    "| 키 | 제목 |\n|---|---|\n"
    "<!-- kb:issues:end -->\n\n"
    "## 언급\n"
    "<!-- kb:mentions:start -->\n"
    "- [[2026-11-04]] 09:12 @김철수 와 front matter 논의\n"
    "<!-- kb:mentions:end -->\n"
    "꼬리 문장\n"
).encode("utf-8")


def crlf(data: bytes) -> bytes:
    return data.replace(b"\n", b"\r\n")


def span_of(data: bytes, name: str) -> tuple[int, int]:
    for found, start, end in managed.spans(data):
        if found == name:
            return start, end
    raise AssertionError(name)


def assert_only_region_changed(old: bytes, new: bytes, name: str) -> None:
    """구역 본문 밖의 바이트(다른 구역, 마커 밖)가 모두 같다."""
    old_start, old_end = span_of(old, name)
    new_start, new_end = span_of(new, name)
    assert new[:new_start] == old[:old_start]
    assert new[new_end:] == old[old_end:]


# ---------------------------------------------------------------- detect_eol·spans


@pytest.mark.parametrize(
    ("data", "eol"),
    [
        (b"", b"\n"),
        (b"a", b"\n"),
        (b"a\nb\n", b"\n"),
        (b"a\r\nb\r\n", b"\r\n"),
        (b"a\r\nb\r\nc\n", b"\r\n"),
        (b"a\r\nb\nc\n", b"\n"),
        (b"a\r\nb\n", b"\n"),  # 같으면 LF
    ],
)
def test_detect_eol(data, eol):
    assert managed.detect_eol(data) == eol


def test_spans_and_read_region():
    found = managed.spans(SAMPLE)
    assert [name for name, _, _ in found] == ["issues", "mentions"]
    start, end = span_of(SAMPLE, "issues")
    assert SAMPLE[start:end] == "| 키 | 제목 |\n|---|---|\n".encode("utf-8")
    assert SAMPLE[:start].endswith(b"<!-- kb:issues:start -->\n")
    assert SAMPLE[end:].startswith(b"<!-- kb:issues:end -->\n")
    assert managed.read_region(SAMPLE, "mentions") == "- [[2026-11-04]] 09:12 @김철수 와 front matter 논의\n".encode("utf-8")
    assert managed.read_region(SAMPLE, "summary") is None
    assert managed.spans(b"") == []
    assert managed.spans("마커 없음\n".encode("utf-8")) == []


def test_empty_region_body():
    data = b"<!-- kb:summary:start -->\n<!-- kb:summary:end -->\n"
    assert managed.read_region(data, "summary") == b""


def test_marker_trailing_whitespace_and_end_at_eof():
    data = b"x\n<!-- kb:lint:start -->  \r\nbody\r\n<!-- kb:lint:end -->"
    assert managed.read_region(data, "lint") == b"body\r\n"


def test_lookalikes_are_not_markers():
    data = (
        b"    <!-- kb:summary:start -->\n"  # 들여쓴 코드 예시
        b"<!-- kb:Summary:start -->\n"  # 대문자
        b"text <!-- kb:summary:end -->\n"  # 줄 중간
        b"<!-- kb:summary9:end -->\n"  # 숫자
        b"<!--kb:summary:end-->\n"  # 공백 없음
    )
    assert managed.spans(data) == []


def test_invalid_name_rejected():
    with pytest.raises(ValueError):
        managed.replace_region(b"", "Bad-Name", "x")
    with pytest.raises(ValueError):
        managed.read_region(b"", "")


# ---------------------------------------------------------------- MarkerError


@pytest.mark.parametrize(
    "text",
    [
        "<!-- kb:summary:start -->\n본문\n",  # 시작만
        "본문\n<!-- kb:summary:end -->\n",  # 끝만
        "<!-- kb:a:start -->\n<!-- kb:a:end -->\n<!-- kb:a:start -->\n<!-- kb:a:end -->\n",  # 중복
        "<!-- kb:a:start -->\n<!-- kb:b:start -->\n<!-- kb:b:end -->\n<!-- kb:a:end -->\n",  # 중첩
        "<!-- kb:a:start -->\n<!-- kb:b:start -->\n<!-- kb:a:end -->\n<!-- kb:b:end -->\n",  # 겹침
        "<!-- kb:a:start -->\n<!-- kb:a:start -->\n<!-- kb:a:end -->\n",  # 같은 이름 중첩
        "<!-- kb:a:start -->",  # 줄끝 없는 시작만
    ],
)
def test_marker_errors(text):
    data = text.encode("utf-8")
    with pytest.raises(MarkerError):
        managed.spans(data)
    with pytest.raises(MarkerError):
        managed.read_region(data, "other")
    with pytest.raises(MarkerError):
        managed.replace_region(data, "other", "x")
    with pytest.raises(MarkerError):
        managed.append_to_region(data, "other", ["x"])
    with pytest.raises(MarkerError):
        managed.strip_markers(data)


def test_marker_error_is_value_error_with_korean_line_number():
    with pytest.raises(ValueError, match="3번째 줄"):
        managed.spans(b"a\nb\n<!-- kb:x:end -->\n")


def test_body_with_marker_line_refused():
    with pytest.raises(MarkerError):
        managed.replace_region(SAMPLE, "summary", "a\n<!-- kb:summary:end -->\nb")
    with pytest.raises(MarkerError):
        managed.append_to_region(SAMPLE, "mentions", ["<!-- kb:other:start -->"])


# ---------------------------------------------------------------- replace_region


def test_replace_preserves_everything_else():
    new = managed.replace_region(SAMPLE, "issues", "| 키 | 제목 |\n|---|---|\n| [[KB-12]] | 캐시 확정 |")
    assert_only_region_changed(SAMPLE, new, "issues")
    assert managed.read_region(new, "issues") == "| 키 | 제목 |\n|---|---|\n| [[KB-12]] | 캐시 확정 |\n".encode("utf-8")
    # 다른 구역도 바이트 그대로
    assert managed.read_region(new, "mentions") == managed.read_region(SAMPLE, "mentions")


def test_replace_is_idempotent():
    once = managed.replace_region(SAMPLE, "mentions", "- 한 줄\n- 두 줄\n")
    twice = managed.replace_region(once, "mentions", "- 한 줄\n- 두 줄\n")
    assert once == twice
    assert managed.replace_region(once, "mentions", "- 한 줄\n- 두 줄") == once


def test_replace_empty_body_gives_adjacent_markers():
    new = managed.replace_region(SAMPLE, "mentions", "")
    assert b"<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n" in new
    assert_only_region_changed(SAMPLE, new, "mentions")
    assert managed.read_region(new, "mentions") == b""


def test_replace_crlf_file_stays_crlf():
    data = crlf(SAMPLE)
    new = managed.replace_region(data, "issues", "첫째\n둘째\r\n셋째")
    assert managed.read_region(new, "issues") == "첫째\r\n둘째\r\n셋째\r\n".encode("utf-8")
    assert new.count(b"\n") == new.count(b"\r\n")
    assert_only_region_changed(data, new, "issues")


def test_replace_keeps_bom_and_marker_on_first_line():
    data = BOM + b"<!-- kb:extracted:start -->\nold\n<!-- kb:extracted:end -->\n"
    new = managed.replace_region(data, "extracted", "화이트보드 글자")
    assert new == BOM + "<!-- kb:extracted:start -->\n화이트보드 글자\n<!-- kb:extracted:end -->\n".encode("utf-8")


def test_replace_keeps_undecodable_bytes_outside():
    data = b"\xff\xfe raw \x80\n<!-- kb:summary:start -->\nx\n<!-- kb:summary:end -->\n\xc3(\n"
    new = managed.replace_region(data, "summary", "요약")
    assert_only_region_changed(data, new, "summary")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"", b"<!-- kb:summary:start -->\nS\n<!-- kb:summary:end -->\n"),
        (b"text\n", b"text\n<!-- kb:summary:start -->\nS\n<!-- kb:summary:end -->\n"),
        (b"text", b"text\n<!-- kb:summary:start -->\nS\n<!-- kb:summary:end -->\n"),
        (b"text\n\n", b"text\n\n<!-- kb:summary:start -->\nS\n<!-- kb:summary:end -->\n"),
        (b"a\r\nb", b"a\r\nb\r\n<!-- kb:summary:start -->\r\nS\r\n<!-- kb:summary:end -->\r\n"),
        (BOM, BOM + b"<!-- kb:summary:start -->\nS\n<!-- kb:summary:end -->\n"),
    ],
)
def test_replace_creates_region_at_eof(before, after):
    new = managed.replace_region(before, "summary", "S")
    assert new == after
    assert new.startswith(before)
    assert managed.replace_region(new, "summary", "S") == new


def test_create_keeps_other_regions():
    new = managed.replace_region(SAMPLE, "summary", "")
    assert new == SAMPLE + b"<!-- kb:summary:start -->\n<!-- kb:summary:end -->\n"


# ---------------------------------------------------------------- append_to_region


def test_append_never_changes_existing_bytes():
    old_start, old_end = span_of(SAMPLE, "mentions")
    new = managed.append_to_region(SAMPLE, "mentions", ["- [[2026-11-05]] 10:00 새 결정", "- 둘째"])
    new_start, new_end = span_of(new, "mentions")
    assert new[:old_end] == SAMPLE[:old_end]  # 기존 본문까지 그대로
    assert new[new_end:] == SAMPLE[old_end:]
    assert new[old_end:new_end] == "- [[2026-11-05]] 10:00 새 결정\n- 둘째\n".encode("utf-8")
    assert new_start == old_start


def test_append_crlf_and_multiline_entry():
    data = crlf(SAMPLE)
    _, old_end = span_of(data, "mentions")
    new = managed.append_to_region(data, "mentions", ["줄1\n줄2"])
    assert new[:old_end] == data[:old_end]
    assert new[old_end:].startswith("줄1\r\n줄2\r\n<!-- kb:mentions:end -->\r\n".encode("utf-8"))


def test_append_creates_region_and_handles_empty():
    new = managed.append_to_region(b"# decisions", "decisions", ["- 2026-11-05 결정"])
    assert new == "# decisions\n<!-- kb:decisions:start -->\n- 2026-11-05 결정\n<!-- kb:decisions:end -->\n".encode("utf-8")
    assert managed.append_to_region(new, "decisions", []) == new
    assert managed.append_to_region(b"", "actions", iter([])) == b"<!-- kb:actions:start -->\n<!-- kb:actions:end -->\n"


def test_append_into_empty_region_and_generator():
    data = b"<!-- kb:actions:start -->\n<!-- kb:actions:end -->\n"
    new = managed.append_to_region(data, "actions", (f"- {i}" for i in range(2)))
    assert new == b"<!-- kb:actions:start -->\n- 0\n- 1\n<!-- kb:actions:end -->\n"


def test_append_twice_accumulates():
    once = managed.append_to_region(SAMPLE, "mentions", ["- a"])
    twice = managed.append_to_region(once, "mentions", ["- b"])
    assert managed.read_region(twice, "mentions").endswith(b"- a\n- b\n")
    assert twice.startswith(once[: span_of(once, "mentions")[1]])


# ---------------------------------------------------------------- strip_markers


def test_strip_markers_keeps_bodies():
    out = managed.strip_markers(SAMPLE)
    assert b"<!--" not in out
    assert out == SAMPLE.replace(b"<!-- kb:issues:start -->\n", b"").replace(b"<!-- kb:issues:end -->\n", b"").replace(
        b"<!-- kb:mentions:start -->\n", b""
    ).replace(b"<!-- kb:mentions:end -->\n", b"")


def test_strip_markers_drop_regions():
    out = managed.strip_markers(SAMPLE, drop_regions=["mentions"])
    assert "@김철수".encode("utf-8") not in out
    assert "| 키 | 제목 |".encode("utf-8") in out
    assert out.endswith("## 언급\n꼬리 문장\n".encode("utf-8"))
    assert managed.strip_markers(SAMPLE, drop_regions=("issues", "mentions", "absent")).count(b"|") == 0


def test_strip_markers_bom_crlf_and_eof():
    data = BOM + crlf(b"<!-- kb:a:start -->\nA\n<!-- kb:a:end -->\nmid\n<!-- kb:b:start -->\nB\n<!-- kb:b:end -->")
    assert managed.strip_markers(data) == BOM + b"A\r\nmid\r\nB\r\n"
    assert managed.strip_markers(data, drop_regions={"a", "b"}) == BOM + b"mid\r\n"
    assert managed.strip_markers(b"no markers") == b"no markers"
