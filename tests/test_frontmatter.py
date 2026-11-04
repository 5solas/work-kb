"""lib/frontmatter.py: BaseLoader 문자열 보존과 줄 단위 편집(D27)."""
from __future__ import annotations

import datetime as dt

import pytest

from lib import core, frontmatter

BOM = b"\xef\xbb\xbf"

JIRA = (
    "---\n"
    "key: KB-12\n"
    "summary: 캐시 front matter 확정\n"
    "# 사람이 남긴 주석\n"
    "status: In Progress\n"
    "labels: [cache, schema]\n"
    "components: []\n"
    "created: 2026-10-28T09:00:00+09:00\n"
    "due: 2026-11-06\n"
    "flag: on\n"
    "answer: NO\n"
    "---\n"
    "# KB-12 캐시 front matter 확정\n"
    "status: 본문은 front matter가 아니다\n"
).encode("utf-8")


def crlf(data: bytes) -> bytes:
    return data.replace(b"\n", b"\r\n")


# ---------------------------------------------------------------- split


def test_split_basic():
    fm, body = frontmatter.split(JIRA)
    assert fm.startswith(b"key: KB-12\n") and fm.endswith(b"answer: NO\n")
    assert body == "# KB-12 캐시 front matter 확정\nstatus: 본문은 front matter가 아니다\n".encode("utf-8")


@pytest.mark.parametrize(
    "data",
    [b"", b"# title\n", b"---\nkey: v\n", b"text\n---\nkey: v\n---\n", b"----\nkey: v\n---\n", b"---"],
)
def test_split_without_front_matter(data):
    assert frontmatter.split(data) == (b"", data)
    assert frontmatter.parse(data) == {}


def test_split_bom_crlf_and_fence_at_eof():
    assert frontmatter.split(BOM + crlf(b"---\na: b\n---\nbody\n")) == (b"a: b\r\n", b"body\r\n")
    assert frontmatter.split(b"---\na: b\n---") == (b"a: b\n", b"")
    assert frontmatter.split(b"---\n---\nbody") == (b"", b"body")
    assert frontmatter.split(b"--- \na: b\n---  \nbody") == (b"a: b\n", b"body")


# ---------------------------------------------------------------- parse


def test_parse_keeps_everything_as_strings():
    fields = frontmatter.parse(JIRA)
    assert fields["due"] == "2026-11-06"
    assert fields["created"] == "2026-10-28T09:00:00+09:00"
    assert fields["flag"] == "on"
    assert fields["answer"] == "NO"
    assert fields["labels"] == ["cache", "schema"]
    assert fields["components"] == []
    assert fields["summary"] == "캐시 front matter 확정"
    assert list(fields) == ["key", "summary", "status", "labels", "components", "created", "due", "flag", "answer"]


def test_parse_daily_date_and_crlf_bom():
    assert frontmatter.parse(BOM + crlf(b"---\ndate: 2026-11-05\ncount: 3\nok: true\n---\n")) == {
        "date": "2026-11-05",
        "count": "3",
        "ok": "true",
    }


def test_parse_block_list_and_nested():
    data = b"---\ntags:\n- a\n- 1\nmeta:\n  x: off\n---\n"
    assert frontmatter.parse(data) == {"tags": ["a", "1"], "meta": {"x": "off"}}


def test_parse_invalid_yaml_raises_config_error():
    with pytest.raises(core.ConfigError) as info:
        frontmatter.parse(b"---\nkey: v\nbad: [unclosed\n---\nbody\n")
    assert info.value.exit_code == 4
    assert "front matter YAML이 올바르지 않습니다" in str(info.value)


def test_parse_non_mapping_and_bad_utf8():
    with pytest.raises(core.ConfigError):
        frontmatter.parse(b"---\n- a\n- b\n---\n")
    with pytest.raises(core.ConfigError):
        frontmatter.parse(b"---\nkey: \xff\n---\n")


# ---------------------------------------------------------------- render


def test_render_exact_bytes():
    fields = {
        "key": "KB-12",
        "summary": "캐시 front matter 확정",
        "labels": ["cache", "schema"],
        "components": [],
        "assignee": "홍길동",
        "skip": None,
    }
    assert frontmatter.render(fields) == (
        "---\nkey: KB-12\nsummary: 캐시 front matter 확정\nlabels: [cache, schema]\ncomponents: []\nassignee: 홍길동\n---\n"
    ).encode("utf-8")


def test_render_quotes_ambiguous_strings_and_crlf():
    out = frontmatter.render({"due": "2026-11-06", "flag": "on", "date": dt.date(2026, 11, 5)}, eol=b"\r\n")
    assert out == b"---\r\ndue: '2026-11-06'\r\nflag: 'on'\r\ndate: 2026-11-05\r\n---\r\n"


def test_render_long_value_stays_on_one_line():
    long = "가" * 300
    out = frontmatter.render({"summary": long})
    assert out.count(b"\n") == 3


def test_render_parse_round_trip():
    fields = {
        "key": "KB-12",
        "summary": "따옴표 ' 와 \" 콜론: 해시 # 포함",
        "due": "2026-11-06",
        "flag": "on",
        "answer": "NO",
        "labels": ["cache", "on", "2026-11-05", "공백 있는 값"],
        "components": [],
        "empty": "",
        "multi": "첫 줄\n둘째 줄",
    }
    for eol in (b"\n", b"\r\n"):
        data = frontmatter.render(fields, eol=eol) + "# 본문\n".encode("utf-8")
        assert frontmatter.parse(data) == fields


# ---------------------------------------------------------------- set_fields


def test_set_fields_edits_only_named_lines():
    new = frontmatter.set_fields(JIRA, {"status": "Done", "summary": "새 제목"})
    expected = JIRA.replace(b"status: In Progress\n", b"status: Done\n").replace(
        "summary: 캐시 front matter 확정\n".encode("utf-8"), "summary: 새 제목\n".encode("utf-8")
    )
    assert new == expected
    assert "# 사람이 남긴 주석\n".encode("utf-8") in new


def test_set_fields_appends_missing_before_closing_fence():
    new = frontmatter.set_fields(JIRA, {"synced_at": "2026-11-04T16:20:03+09:00", "sprint": "KB 스프린트 7"})
    expected = JIRA.replace(
        b"answer: NO\n---\n",
        "answer: NO\nsynced_at: '2026-11-04T16:20:03+09:00'\nsprint: KB 스프린트 7\n---\n".encode("utf-8"),
    )
    assert new == expected
    assert frontmatter.parse(new)["synced_at"] == "2026-11-04T16:20:03+09:00"


def test_set_fields_same_value_keeps_original_bytes():
    same = {"due": "2026-11-06", "flag": "on", "labels": ["cache", "schema"], "created": "2026-10-28T09:00:00+09:00"}
    assert frontmatter.set_fields(JIRA, same) == JIRA


def test_set_fields_is_idempotent():
    updates = {"status": "Done", "labels": ["a", "b"], "new_key": "2026-11-05"}
    once = frontmatter.set_fields(JIRA, updates)
    assert frontmatter.set_fields(once, updates) == once


def test_set_fields_none_removes_key_with_continuation_lines():
    data = b"---\na: 1\ntags:\n- x\n  # indented note\n- y\n\nb: 2\n---\nbody\n"
    new = frontmatter.set_fields(data, {"tags": None, "missing": None})
    assert new == b"---\na: 1\n\nb: 2\n---\nbody\n"


def test_set_fields_replaces_block_list_and_comment_boundary():
    data = b"---\ntags:\n  - x\n  - y\n# keep me\nb: 2\n---\n"
    new = frontmatter.set_fields(data, {"tags": ["z"]})
    assert new == b"---\ntags: [z]\n# keep me\nb: 2\n---\n"


def test_set_fields_keeps_crlf_and_bom():
    data = BOM + crlf(JIRA)
    new = frontmatter.set_fields(data, {"status": "Done", "extra": "값"})
    assert new.startswith(BOM)
    assert new.count(b"\n") == new.count(b"\r\n")
    assert new == BOM + crlf(
        JIRA.replace(b"status: In Progress\n", b"status: Done\n").replace(
            b"answer: NO\n---\n", "answer: NO\nextra: 값\n---\n".encode("utf-8")
        )
    )


def test_set_fields_creates_block_when_absent():
    assert frontmatter.set_fields(b"# title\n", {"type": "topic", "x": None}) == b"---\ntype: topic\n---\n# title\n"
    assert frontmatter.set_fields(BOM + b"a\r\nb\r\n", {"k": "v"}) == BOM + b"---\r\nk: v\r\n---\r\na\r\nb\r\n"
    assert frontmatter.set_fields(b"", {"k": "v"}) == b"---\nk: v\n---\n"
    assert frontmatter.set_fields(b"# title\n", {"x": None}) == b"# title\n"


def test_set_fields_empty_block_and_duplicates():
    assert frontmatter.set_fields(b"---\n---\nbody", {"k": "v"}) == b"---\nk: v\n---\nbody"
    data = b"---\nk: 1\nother: x\nk: 2\n---\n"
    assert frontmatter.set_fields(data, {"k": "3"}) == b"---\nk: '3'\nother: x\n---\n"


def test_set_fields_quoted_key_and_trailing_comment():
    data = b"---\n\"status\": open  # memo\n'it''s': x\n---\n"
    assert frontmatter.set_fields(data, {"status": "open"}) == data
    assert frontmatter.set_fields(data, {"it's": "y"}) == b"---\n\"status\": open  # memo\nit's: y\n---\n"


def test_set_fields_multiline_value_round_trip():
    data = b"---\na: 1\nb: 2\n---\n"
    once = frontmatter.set_fields(data, {"a": "첫 줄\n둘째 줄"})
    assert once.endswith(b"b: 2\n---\n")
    assert frontmatter.parse(once) == {"a": "첫 줄\n둘째 줄", "b": "2"}
    assert frontmatter.set_fields(once, {"a": "첫 줄\n둘째 줄"}) == once
    assert frontmatter.set_fields(once, {"a": "x"}) == b"---\na: x\nb: 2\n---\n"


def test_set_fields_ignores_body_lines():
    new = frontmatter.set_fields(JIRA, {"status": "Done"})
    assert new.endswith("status: 본문은 front matter가 아니다\n".encode("utf-8"))
