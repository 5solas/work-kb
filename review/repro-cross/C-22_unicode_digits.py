"""notes.key_pattern의 `\\d`가 유니코드 숫자(전각 １, 아랍 ١ 등)를 받아들여
(1) normalize_text가 멱등이 아니고, (2) `[[KB-１２]]` 같은 가짜 키로 정규화된다."""
import re
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
import link
from lib import notes

fail = False
# (1) idempotence
text = "KB-١KB-1"
once, _ = link.normalize_text(text, ("KB",))
twice, _ = link.normalize_text(once, ("KB",))
print("idempotence:", repr(text), "->", repr(once), "->", repr(twice))
fail |= once != twice
# (2) full-width digits typed with a Korean IME become a key/link/target
text = "KB-１２ 배포 확인"
out, n = link.normalize_text(text, ("KB",))
keys = [m.key for m in notes.find_keys(out, ("KB",))]
print("fullwidth:", repr(text), "->", repr(out), "keys:", keys, "targets:", link._targets(notes.find_keys(out, ("KB",)), []))
fail |= any(not re.fullmatch(r"[A-Z][A-Z0-9_]+-[0-9]+", k) for k in keys)
assert not fail, "Unicode digits accepted as Jira key digits"
