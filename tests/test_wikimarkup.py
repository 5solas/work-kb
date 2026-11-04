from __future__ import annotations

import pytest

from lib import wikimarkup
from lib.wikimarkup import WikimarkupError, convert_or_raw, to_markdown


def test_empty_and_none():
    assert to_markdown(None) == ""
    assert to_markdown("") == ""
    assert to_markdown("  \n ") == ""
    assert convert_or_raw(None) == ""


def test_non_str_raises():
    with pytest.raises(WikimarkupError):
        to_markdown(123)  # type: ignore[arg-type]
    assert "```" in convert_or_raw(123)  # type: ignore[arg-type]


def test_headings():
    assert to_markdown("h1. 제목\nh3. 소제목") == "# 제목\n### 소제목"
    assert to_markdown("h6. 끝") == "###### 끝"


def test_inline_styles():
    assert to_markdown("*굵게* 와 _기울임_ 와 {{코드}}") == "**굵게** 와 *기울임* 와 `코드`"
    assert to_markdown("이*굵게*는 좋다") == "이**굵게**는 좋다"
    assert to_markdown("snake_case_name 그대로") == "snake_case_name 그대로"


def test_mono_protects_markup():
    assert to_markdown("{{a_b *c*}}") == "`a_b *c*`"


def test_code_block_with_lang_and_markup_inside():
    src = "앞\n{code:java}\nint *x* = _y_; h1. no\n{code}\n뒤"
    assert to_markdown(src) == "앞\n\n```java\nint *x* = _y_; h1. no\n```\n\n뒤"


def test_noformat_and_backticks_in_content():
    out = to_markdown("{noformat}\n```x```\n{noformat}")
    assert out.startswith("````\n") and out.endswith("\n````")


def test_code_title_param():
    assert "```python\n" in to_markdown("{code:title=a.py|language=python}\nx\n{code}")


def test_quote_and_bq():
    assert to_markdown("bq. 인용 *굵게*") == "> 인용 **굵게**"
    assert to_markdown("{quote}\n하나\n둘\n{quote}") == "> 하나\n> 둘"


def test_lists_nested():
    src = "* 가\n** 나\n*** 다\n* 라"
    assert to_markdown(src) == "- 가\n  - 나\n    - 다\n- 라"
    src = "# 하나\n## 둘\n# 셋"
    assert to_markdown(src) == "1. 하나\n   1. 둘\n1. 셋"
    assert to_markdown("*# 혼합") == "  1. 혼합"


def test_list_vs_bold_line():
    assert to_markdown("*굵은 줄* 시작") == "**굵은 줄** 시작"


def test_links():
    assert to_markdown("[문서|http://docs.example.invalid/a_b_c]") == "[문서](http://docs.example.invalid/a_b_c)"
    assert to_markdown("[http://x.example.invalid]") == "[http://x.example.invalid](http://x.example.invalid)"
    assert to_markdown("[KB-12]") == "[KB-12]"


def test_images():
    assert to_markdown("!image.png!") == "![image.png]"
    assert to_markdown("!shot.png|thumbnail,width=100!") == "![shot.png]"
    assert to_markdown("좋아요! 감사해요!") == "좋아요! 감사해요!"


def test_table():
    src = "||이름||상태||\n|홍길동|진행|\n|김철수|완료|"
    assert to_markdown(src) == "| 이름 | 상태 |\n| --- | --- |\n| 홍길동 | 진행 |\n| 김철수 | 완료 |"


def test_table_with_link_pipe_and_no_header():
    out = to_markdown("|[a|http://x.invalid]|*b*|\n|c|d|")
    assert out.splitlines()[0] == "| [a](http://x.invalid) | **b** |"
    assert out.splitlines()[1] == "| --- | --- |"


def test_table_ragged():
    out = to_markdown("||a||b||c||\n|1|2|")
    assert out.splitlines()[2] == "| 1 | 2 |  |"


def test_color_and_panel_stripped():
    assert to_markdown("{color:red}경고{color} 끝") == "경고 끝"
    assert to_markdown("{panel:title=참고}\n내용\n{panel}") == "**참고**\n내용"


def test_unknown_macro_left():
    assert to_markdown("{toc} 와 {status:colour=Green|title=OK}") == "{toc} 와 {status:colour=Green|title=OK}"


def test_unclosed_code_left_as_text():
    assert to_markdown("{code}\n열림") == "{code}\n열림"


def test_hr_and_line_breaks_and_crlf():
    assert to_markdown("가\r\n나\r\n----\r\n다") == "가\n나\n---\n다"


def test_control_chars_stripped():
    assert to_markdown("a\x00b\x05c") == "abc"


def test_convert_or_raw_failure(monkeypatch):
    def boom(_t):
        raise RuntimeError("x")

    monkeypatch.setattr(wikimarkup, "_convert", boom)
    out = convert_or_raw("*원문*")
    assert out.splitlines()[0] == "(Jira wiki markup 변환 실패, 원문)"
    assert "```\n*원문*\n```" in out
    with pytest.raises(WikimarkupError):
        to_markdown("x")


def test_convert_or_raw_never_raises_fence_safe(monkeypatch):
    monkeypatch.setattr(wikimarkup, "to_markdown", lambda t: (_ for _ in ()).throw(ValueError()))
    out = convert_or_raw("```a```")
    assert out.count("````") == 2


def test_realistic_korean_issue():
    src = (
        "h2. 배경\n서버 *재시작* 시 {{timeout}} 오류 발생.\n\n"
        "* 원인 확인\n** 로그 분석\n* 조치\n\n"
        "{code}\nretry(*3*)\n{code}\n||항목||값||\n|담당|홍길동|\n"
    )
    out = to_markdown(src)
    assert "## 배경" in out
    assert "서버 **재시작** 시 `timeout` 오류 발생." in out
    assert "- 원인 확인\n  - 로그 분석\n- 조치" in out
    assert "```\nretry(*3*)\n```" in out
    assert "| 담당 | 홍길동 |" in out
