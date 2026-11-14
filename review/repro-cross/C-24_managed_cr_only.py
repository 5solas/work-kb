"""managed.replace_region: CR-only 파일에서 본문이 빈 줄로 시작하면 멱등이 아니다(실행할 때마다 CR이 하나씩 늘어남)."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
from lib import managed

data = b"# t\r<!-- kb:x:start -->\r<!-- kb:x:end -->\r"   # CR-only (classic Mac) file
body = "\nitem"                                          # body starting with a blank line
once = managed.replace_region(data, "x", body)
twice = managed.replace_region(once, "x", body)
print("once :", once)
print("twice:", twice)
print("read_region(once):", managed.read_region(once, "x"))
assert twice == once, "replace_region is not idempotent on CR-only file (blank first body line merged into marker line)"
