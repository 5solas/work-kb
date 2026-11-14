import datetime as dt
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repro_vault import make_vault  # noqa: E402

from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

import daily_prepare  # noqa: E402
from lib import core, managed, notes  # noqa: E402

SET = settings(max_examples=300, deadline=None, suppress_health_check=list(HealthCheck))

LINES = [
    "- [ ] 할 일 A", "- [ ] 할 일 A", "- [ ] 09:00 할 일 B", "- [ ] 할 일 C  ", "- [x] 끝난 일", "- [ ] KB-1 키 있는 일",
    "- [ ] [[KB-2]] 키", "- 10:00 메모", "", "```", "~~~", "- [ ] 할 일 `코드` D", "- [ ]  ", "- [ ] 09:00",
    "- [ ] 할 일　E", "- [ ] 할 일 A (← 2026-11-10)", "  - [ ] 들여쓴 일", "- [>] 옮긴 일 (2026-11-11로 이월)",
    "- [ ] 할 일 F [[KB-3]]", "- [ ] https://x.com/KB-9 링크",
]

day_st = st.lists(st.sampled_from(LINES), max_size=8)
file_st = st.tuples(day_st, st.booleans(), st.booleans())  # lines, crlf, with skeleton carryover region


def render(lines, crlf, skel, carry_lines):
    text = ""
    if skel:
        text += "## 이월\n<!-- kb:carryover:start -->\n" + "".join(l + "\n" for l in carry_lines) + "<!-- kb:carryover:end -->\n\n"
    text += "## 기록\n" + "".join(l + "\n" for l in lines)
    data = text.encode("utf-8")
    return data.replace(b"\n", b"\r\n") if crlf else data


def snapshot(root):
    return {p.name: p.read_bytes() for p in sorted((root / "daily").glob("*.md"))}


def carry_items(root, day):
    data = (root / "daily" / f"{day}.md").read_bytes()
    body = managed.read_region(data, "carryover") or b""
    return [l for l in notes.split_lines(body.decode("utf-8")) if l.strip()]


@SET
@given(st.lists(st.tuples(file_st, day_st), min_size=1, max_size=3))
def test_carryover_idempotent_no_dupes(days):
    root, cfg = make_vault()
    try:
        base = dt.date(2026, 11, 10)
        for i, ((lines, crlf, skel), carry_lines) in enumerate(days):
            carry = [l for l in carry_lines if l.startswith("- [")]
            (root / "daily" / f"{base + dt.timedelta(days=i)}.isoformat().md").unlink(missing_ok=True)
            (root / "daily" / f"{(base + dt.timedelta(days=i)).isoformat()}.md").write_bytes(render(lines, crlf, skel, carry))
        target = base + dt.timedelta(days=len(days))
        for d in (target, target + dt.timedelta(days=1)):
            daily_prepare.run(cfg, d)
            snap = snapshot(root)
            r2 = daily_prepare.run(cfg, d)
            assert snapshot(root) == snap, f"second run changed files for {d}"
            assert r2.carried == 0 and r2.marked == 0
            items = carry_items(root, d.isoformat())
            keys = [daily_prepare._carry_key(l) for l in items]
            assert len(keys) == len(set(keys)), items
            # open keyless tasks that remain in earlier days' 기록 (outside code) must not exist
            for p in sorted((root / "daily").glob("*.md")):
                if p.stem >= d.isoformat():
                    continue
                data = p.read_bytes()
                span = notes.section_span(data, "기록")
                for line in notes.iter_lines("daily/" + p.name, data):
                    if line.in_code or line.region is not None or span is None or not (span[0] <= line.offset < span[1]):
                        continue
                    m = daily_prepare._OPEN_TASK.match(line.text)
                    if not m or notes.find_keys(line.text, ["KB", "OPS"]) or daily_prepare._is_promoted(line.text, "", {}):
                        continue
                    body = daily_prepare._TIME.sub("", m.group(1).strip(), count=1).strip()
                    assert not body, f"open task left behind in {p.name}: {line.text!r}"
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)
