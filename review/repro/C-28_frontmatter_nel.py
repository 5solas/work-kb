"""frontmatter.set_fields: 값에 U+0085(NEL)가 있으면 PyYAML이 작은따옴표 안에 원문 그대로 써서 다시 읽으면 공백이 된다(값 손상)."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
from lib import frontmatter

value = "배포\x85체크"
out = frontmatter.set_fields(b"---\ntitle: x\n---\n", {"title": value})
back = frontmatter.parse(out)["title"]
print(out, "->", repr(back))
assert back == value, "front matter value not round-tripped"
