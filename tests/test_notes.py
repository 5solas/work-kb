import datetime as dt

import pytest

from lib import config, core, frontmatter, managed, notes

DAILY = """---
date: 2026-11-05
---
## 오늘
<!-- kb:today:start -->
- [ ] [[KB-12]] 캐시 front matter 확정 · 진행중
<!-- kb:today:end -->

## 이월
<!-- kb:carryover:start -->
- [ ] 개인: 경비 정산 (← 2026-11-04)
<!-- kb:carryover:end -->

## 기록
- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로.
- 11:40 개인: PR 리뷰 요청 옴
- [ ] 개인: 11월 회의실 예약
- [x] 14:00 OPS-3 배포 체크리스트 확인
```
KB-99 코드 블록 안의 키
```
- 16:05 KB-12 댓글 올림 https://jira.example.invalid/browse/KB-77 참고 `KB-88`

"""


def _cfg(vault):
    return config.load(vault)


def test_iter_lines_marks_code_front_matter_regions_and_time():
    lines = list(notes.iter_lines("daily/2026-11-05.md", DAILY.encode("utf-8")))
    assert lines[0].in_code and lines[1].in_code and lines[2].in_code  # front matter
    assert lines[0].day == "2026-11-05"
    by_text = {l.text: l for l in lines}
    assert by_text["<!-- kb:today:start -->"].in_code
    today_item = by_text["- [ ] [[KB-12]] 캐시 front matter 확정 · 진행중"]
    assert today_item.region == "today" and not today_item.in_code
    rec = by_text["- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로."]
    assert rec.time == "09:12" and rec.region is None and not rec.in_code
    assert by_text["- [ ] 개인: 11월 회의실 예약"].time is None
    assert by_text["- [x] 14:00 OPS-3 배포 체크리스트 확인"].time == "14:00"
    assert by_text["KB-99 코드 블록 안의 키"].in_code
    assert by_text["```"].in_code
    assert rec.hash == core.line_hash(rec.text)
    assert [l.lineno for l in lines[:3]] == [1, 2, 3]


def test_iter_lines_offsets_match_bytes_with_bom_and_crlf():
    data = b"\xef\xbb\xbf" + "## 기록\r\n- 09:00 한글\r\n".encode("utf-8")
    lines = list(notes.iter_lines("daily/2026-11-05.md", data))
    second = lines[1]
    assert data[second.offset:second.offset + len(second.text.encode("utf-8"))].decode("utf-8") == "- 09:00 한글"


def test_section_span_and_record_lines():
    data = DAILY.encode("utf-8")
    span = notes.section_span(data, "기록")
    assert span is not None
    start, end = span
    body = data[start:end].decode("utf-8")
    assert body.startswith("- 09:12") and body.rstrip().endswith("`KB-88`")
    assert not body.endswith("\n\n")  # 끝의 빈 줄 제외
    assert notes.section_span(data, "없음") is None
    records = notes.record_lines("daily/2026-11-05.md", data)
    assert records[0].text.startswith("- 09:12")
    assert all(r.region is None for r in records)
    assert any(r.in_code for r in records)  # 코드 블록 줄도 포함되나 in_code


def test_section_span_last_section_without_trailing_newline():
    data = "## 기록\n- 09:00 a".encode("utf-8")
    start, end = notes.section_span(data, "기록")
    assert data[start:end] == b"- 09:00 a"


def test_find_keys_bare_bracketed_and_exclusions():
    text = "- 16:05 KB-12 댓글 [[KB-13]] [[KB-14|별칭]] https://jira.example.invalid/browse/KB-77 `KB-88` XKB-1 KB-2x OPS-3"
    found = notes.find_keys(text, ["KB", "OPS"])
    assert [(m.key, m.bracketed) for m in found] == [("KB-12", False), ("KB-13", True), ("KB-14", True), ("OPS-3", False)]
    assert text[found[0].start:found[0].end] == "KB-12"
    assert notes.find_keys(text, []) == []
    assert [m.key for m in notes.find_keys("ab-KB-5 KB-6", ["KB"])] == ["KB-6"]


@pytest.mark.parametrize(
    "text",
    ["[[jira/KB-12]]", "[[jira/KB-12.md]]", "[[jira/KB-12|x]]", "[[jira/KB-12#설명]]", "[[jira/KB-12.md|별칭]]"],
)
def test_find_keys_obsidian_absolute_path_links_are_bracketed(text):
    """D64: Obsidian 절대 경로 링크 형식으로 자동 완성하면 `[[jira/KB-12]]`가 된다."""
    found = notes.find_keys(f"- 19:00 {text} 경로 링크", ["KB"])
    assert [(m.key, m.bracketed) for m in found] == [("KB-12", True)]


@pytest.mark.parametrize("text", ["[[jira/KB-12-설계]]", "[[docs/KB-12]]", "[[jira/KB-12.mdx]]", "jira/KB-12"])
def test_find_keys_other_paths_are_not_bracketed(text):
    assert [m.bracketed for m in notes.find_keys(text, ["KB"])] == [False]


@pytest.mark.parametrize(
    "text, keys",
    [
        ("``KB-5`` 이중 백틱", []),
        ("``KB-5 ` 안의 백틱`` 과 KB-6", ["KB-6"]),
        ("```a `KB-7` b``` 끝", []),
        ("`KB-8` 과 ``KB-9``", []),
        ("짝 없는 `` 과 KB-10", ["KB-10"]),
        ("``짝이 다른` KB-11", ["KB-11"]),
    ],
)
def test_find_keys_commonmark_code_spans(text, keys):
    assert [m.key for m in notes.find_keys(text, ["KB"])] == keys


@pytest.mark.parametrize("sep", ["\x0c", "\x0b", "\x1c", "\x85", " ", " "])
def test_iter_lines_splits_only_on_newline_and_lone_cr(sep):
    data = f"## 기록\n- [ ] 보고서 초안{sep}담당 확인 후 제출\n- 메모{sep}## 제목 아님\n- 09:00 다음\r마지막\r\n".encode("utf-8")
    lines = list(notes.iter_lines("daily/2026-11-04.md", data))
    assert [l.text for l in lines] == [
        "## 기록", f"- [ ] 보고서 초안{sep}담당 확인 후 제출", f"- 메모{sep}## 제목 아님", "- 09:00 다음", "마지막"
    ]
    for line in lines:
        assert data[line.offset:line.offset + len(line.text.encode("utf-8"))].decode("utf-8") == line.text
    start, end = notes.section_span(data, "기록")  # 줄 가운데의 `## `는 제목이 아니다
    assert data[start:end].decode("utf-8") == (
        f"- [ ] 보고서 초안{sep}담당 확인 후 제출\n- 메모{sep}## 제목 아님\n- 09:00 다음\r마지막\r\n"
    )
    assert notes.split_lines(f"a{sep}b\r\nc\rd\n") == [f"a{sep}b", "c", "d"]


def test_find_people_korean_and_email_exclusion():
    assert notes.find_people("@김철수 와 @이영희, @kim_2 그리고 mail@example.invalid (@박민수)") == ["김철수", "이영희", "kim_2", "박민수"]
    assert notes.find_people("`@코드` @김철수 @김철수") == ["김철수"]
    assert notes.find_people("https://x.invalid/@user 와 @홍길동") == ["홍길동"]


def test_wikilinks_and_embeds():
    text = "![[raw/assets/2026-11-05-1420-whiteboard.png]] [[KB-12]] [[projects/KB|지식]] [[2026-11-04#기록]]"
    assert notes.wikilinks(text) == ["raw/assets/2026-11-05-1420-whiteboard.png", "KB-12", "projects/KB", "2026-11-04"]
    assert notes.image_embeds(text) == ["raw/assets/2026-11-05-1420-whiteboard.png"]


def test_daily_skeleton_parses():
    data = notes.daily_skeleton(dt.date(2026, 11, 5))
    assert frontmatter.parse(data) == {"date": "2026-11-05"}
    assert [name for name, _s, _e in managed.spans(data)] == ["today", "carryover"]
    assert notes.section_span(data, "기록") is not None
    assert b"\r" not in data


def test_iter_sources_skips_non_utf8_and_emacs_temp(vault):
    cfg = _cfg(vault)
    (vault / "daily" / "2026-11-04.md").write_bytes("## 기록\n- 09:00 한글\n".encode("utf-8"))
    (vault / "daily" / "2026-11-05.md").write_bytes("## 기록\n- 09:00 깨짐\n".encode("cp949"))
    (vault / "daily" / ".#2026-11-05.md").write_bytes(b"lock")
    (vault / "inbox" / "20261105-0900-메모.md").write_bytes(b"\xef\xbb\xbf---\ntitle: a\n---\nKB-1\n")
    got = [notes.rel_path(cfg, p) for p, _ in notes.iter_sources(cfg)]
    assert got == ["daily/2026-11-04.md", "inbox/20261105-0900-메모.md"]
    log = (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "2026-11-05.md" in log


def test_iter_sources_reports_skipped_and_can_stay_silent(vault):
    cfg = _cfg(vault)
    (vault / "daily" / "2026-11-05.md").write_bytes("## 기록\n- 09:00 깨짐\n".encode("cp949"))
    (vault / "daily" / "2026-11-04.md").write_bytes("## 기록\n".encode("utf-8"))
    skipped: list = []
    got = [notes.rel_path(cfg, p) for p, _ in notes.iter_sources(cfg, skipped=skipped, log=False)]
    assert got == ["daily/2026-11-04.md"]
    assert skipped == [("daily/2026-11-05.md", notes.NOT_UTF8)]
    assert not (vault / ".state" / "logs").exists()  # 읽기 전용 호출은 로그 폴더를 만들지 않는다
    list(notes.iter_sources(cfg))  # 기본은 로그를 남긴다(계약)
    assert (vault / ".state" / "logs" / "kb.log").exists()


def test_ensure_page_creates_once_and_never_overwrites(vault, fake_now):
    fake_now("2026-11-05T09:00:00")
    cfg = _cfg(vault)
    jira = notes.ensure_page(cfg, "jira", "KB-12")
    assert jira == vault / "jira" / "KB-12.md"
    fm = frontmatter.parse(jira.read_bytes())
    assert fm["key"] == "KB-12" and fm["stub"] == "true" and fm["project"] == "KB"
    assert managed.read_region(jira.read_bytes(), "mentions") == b""
    jira.write_bytes(b"custom")
    assert notes.ensure_page(cfg, "jira", "KB-12").read_bytes() == b"custom"

    project = notes.ensure_page(cfg, "project", "KB", title="지식 저장고")
    data = project.read_bytes()
    assert frontmatter.parse(data)["type"] == "project"
    assert [n for n, _s, _e in managed.spans(data)] == ["issues", "summary", "mentions"]
    assert "# KB — 지식 저장고" in data.decode("utf-8")
    assert frontmatter.parse(data)["updated"] == "2026-11-05"

    person = notes.ensure_page(cfg, "person", "김철수")
    assert person == vault / "wiki" / "people" / "김철수.md"
    assert frontmatter.parse(person.read_bytes())["type"] == "person"
    topic = notes.ensure_page(cfg, "topic", "배포/운영?")
    assert topic.name == "배포운영.md"
    assert frontmatter.parse(topic.read_bytes())["aliases"] == []
    for kind, rel in [("decisions", "wiki/decisions.md"), ("actions", "wiki/actions.md"), ("index", "wiki/index.md"), ("proposals", "wiki/_proposals.md"), ("lint", "wiki/_lint.md")]:
        path = notes.ensure_page(cfg, kind)
        assert path == vault / rel
        assert [n for n, _s, _e in managed.spans(path.read_bytes())] == [kind]
    with pytest.raises(ValueError):
        notes.ensure_page(cfg, "nope", "x")


VIEW = """---
view: today
jql: "assignee = currentUser()"
synced_at: 2026-11-05T12:30:00+09:00
---
# today

| 키 | 제목 | 상태 | 담당 | 마감 | 갱신 |
|---|---|---|---|---|---|
| [[KB-12]] | 캐시 front matter 확정 | In Progress | 홍길동 | 2026-11-06 | 2026-11-04 |
| OPS-3 | 배포 체크리스트 \\| 2차 | Blocked | 홍길동 |  | 2026-11-03 |
"""


def test_read_view_rows_and_missing(vault):
    cfg = _cfg(vault)
    assert notes.read_view(cfg, "today") == []
    (vault / "jira" / "_views" / "today.md").write_text(VIEW, encoding="utf-8")
    rows = notes.read_view(cfg, "today")
    assert [r["key"] for r in rows] == ["KB-12", "OPS-3"]
    assert rows[0]["제목"] == "캐시 front matter 확정" and rows[0]["마감"] == "2026-11-06"
    assert rows[1]["상태"] == "Blocked" and rows[1]["마감"] == ""
    assert set(notes.VIEW_COLUMNS) <= set(rows[0])


@pytest.mark.parametrize(
    "name",
    ["- 배포 절차", "!중요 일정", "@배포", "&앵커 문제", "'따옴표", "%TAG 설정", "{배포}", "[목록]", "a #b", "yes", "2026", "배포", "김철수"],
)
@pytest.mark.parametrize("kind", ["topic", "person"])
def test_page_template_name_with_yaml_indicators_parses_back(kind, name):
    """LLM이 제안한 주제 이름이 YAML 표시 문자로 시작해도 front matter는 읽히고 이름은 그대로다."""
    _rel, body = notes.page_template(kind, name, title=None, today="2026-11-05")
    fields = frontmatter.parse(body.encode("utf-8"))
    assert fields["name"] == name and fields["type"] == kind
    assert fields["updated"] == "2026-11-05"
    if kind == "topic":
        assert fields["aliases"] == []


def test_page_template_plain_names_stay_unquoted():
    _rel, body = notes.page_template("topic", "배포 준비", title=None, today="2026-11-05")
    assert body.startswith("---\ntype: topic\nname: 배포 준비\naliases: []\nupdated: 2026-11-05\n---\n# 배포 준비\n")
    _rel, body = notes.page_template("project", "KB", title="- 캐시: 정리", today="2026-11-05")
    assert frontmatter.parse(body.encode("utf-8"))["title"] == "- 캐시: 정리"
