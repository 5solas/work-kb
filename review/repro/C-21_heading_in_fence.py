"""notes.section_span이 코드 울타리를 무시해서, `## 기록` 안 코드 블록의 `## ...` 줄에서 절이 끝난다.
그 뒤의 `- [ ]` 할 일은 이월되지 않는다(링크 정규화 대상에서도 빠진다)."""
import datetime as dt
from repro_vault import make_vault

import daily_prepare
from lib import managed

root, cfg = make_vault()
(root / "daily" / "2026-11-14.md").write_bytes(
    (
        "## 기록\n"
        "- [ ] 할 일 A\n"
        "```bash\n"
        "## 셸 주석처럼 보이는 줄\n"
        "```\n"
        "- [ ] 할 일 B\n"
    ).encode("utf-8")
)
daily_prepare.run(cfg, dt.date(2026, 11, 15))
today = (root / "daily" / "2026-11-15.md").read_bytes()
carry = managed.read_region(today, "carryover").decode("utf-8")
print("carryover region:\n" + carry)
print("source after run:\n" + (root / "daily" / "2026-11-14.md").read_text(encoding="utf-8"))
assert "할 일 B" in carry, "task after a fenced block containing '## ' was not carried over"
