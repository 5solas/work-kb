"""frontmatter._FENCE(`---[ \\t]*\\r?`)가 `\\r\\r\\n` 줄끝의 울타리를 못 알아봐서 set_fields가 front matter를 하나 더 만든다.
managed.py는 같은 줄끝(텍스트 모드로 CRLF를 다시 쓴 파일)을 명시적으로 지원한다."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
from lib import frontmatter

data = b"---\r\r\ntype: project\r\r\nupdated: 2026-11-01\r\r\n---\r\r\n# KB\r\r\n"
print("parse:", frontmatter.parse(data))
out = frontmatter.set_fields(data, {"updated": "2026-11-15"})
print(out)
assert out.count(b"---") == 2, "set_fields created a second front matter block"
