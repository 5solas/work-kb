"""NFD로 저장된 `## 기록` 제목을 ensure_skeleton·section_span이 못 알아봐서 NFC `## 기록`을 하나 더 붙이고,
원래 절의 할 일은 이월·정규화 대상에서 빠진다(제목 비교에 NFC 정규화가 없음)."""
import sys
import unicodedata
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
import daily_prepare
from lib import notes

data = unicodedata.normalize("NFD", "## 기록\n- [ ] 할 일\n").encode("utf-8")
print("section_span(NFD file, '기록'):", notes.section_span(data, "기록"))
out = daily_prepare.ensure_skeleton(data)
n = unicodedata.normalize("NFC", out.decode("utf-8")).count("## 기록")
print("'## 기록' headings after ensure_skeleton:", n)
assert n == 1 and notes.section_span(data, "기록") is not None
