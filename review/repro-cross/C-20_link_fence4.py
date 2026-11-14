"""link 정규화가 4백틱(````) 코드 울타리 안의 맨 글자 키를 [[KEY]]로 바꾼다(코드 블록은 건드리지 않아야 함).

notes.FENCE = ^(```|~~~) 가 여는 울타리 길이를 3으로만 기억해서, ```` 안의 ``` 줄이 블록을 닫는다.
"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
import link
from lib import notes

ctx = link._Ctx(cfg=None, project_keys=("KB",), key_re=notes.key_pattern(("KB",)), prefix="@", normalize=True, today="2026-11-15")
data = (
    "## 기록\n"
    "- 09:00 예시 코드\n"
    "````markdown\n"
    "```\n"
    "KB-1 은 코드 예시 안의 키\n"
    "```\n"
    "````\n"
).encode("utf-8")
new, count = link._normalize_daily("daily/2026-11-15.md", data, ctx)
print(new.decode("utf-8"))
print("changed lines:", count)
assert new == data, "bare key inside a ```` fenced code block was normalized"
