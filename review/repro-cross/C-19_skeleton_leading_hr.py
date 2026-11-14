"""daily_prepare.run이 첫 줄이 `---`(닫는 울타리 없음)인 오늘 daily에 실행할 때마다 `## 기록` 제목을 하나씩 더 붙인다.

원인: notes.iter_lines가 닫히지 않은 `---`부터 파일 전체를 front matter(in_code)로 보아 ensure_skeleton이
`## 기록` 제목을 못 찾는다. today·carryover는 구역이 있어 건너뛰지만 `## 기록`은 구역이 없어 매번 덧붙는다.
"""
import datetime as dt
from repro_vault import make_vault

import daily_prepare

root, cfg = make_vault()
path = root / "daily" / "2026-11-15.md"
path.write_bytes("---\n## 기록\n- 10:00 메모\n".encode("utf-8"))
counts = []
for _ in range(3):
    daily_prepare.run(cfg, dt.date(2026, 11, 15), carryover=False)
    counts.append(path.read_text(encoding="utf-8").count("## 기록"))
print(path.read_text(encoding="utf-8"))
print("'## 기록' count after each run:", counts)
assert counts[0] == counts[-1], "ensure_skeleton is not idempotent: '## 기록' appended on every run"
