"""notes.iter_lines는 1번째 줄이 `---`이면 닫는 `---`가 없어도 파일 전체를 front matter(in_code)로 본다.
frontmatter._locate는 닫는 울타리가 없으면 front matter가 아니라고 본다(불일치).
결과: 첫 줄이 구분선 `---`인 daily의 할 일은 이월되지 않고, 키 언급도 링크되지 않는다."""
import datetime as dt
from repro_vault import make_vault

import daily_prepare
from lib import frontmatter, managed, notes

root, cfg = make_vault()
src = "---\n## 기록\n- [ ] 개인 할 일\n- 10:00 KB-7 논의\n".encode("utf-8")
(root / "daily" / "2026-11-14.md").write_bytes(src)
print("frontmatter.split ->", frontmatter.split(src)[0] or b"(no front matter)")
print("iter_lines in_code ->", [(l.text, l.in_code) for l in notes.iter_lines("daily/2026-11-14.md", src)])
daily_prepare.run(cfg, dt.date(2026, 11, 15))
carry = managed.read_region((root / "daily" / "2026-11-15.md").read_bytes(), "carryover").decode("utf-8")
print("carryover region:", repr(carry))
assert "개인 할 일" in carry, "file starting with '---' (no closing fence) is treated entirely as front matter"
