import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3] / 'scripts'))

from hypothesis import given, settings, strategies as st, HealthCheck

import link
from lib import notes, managed

SET = settings(max_examples=3000, deadline=None, suppress_health_check=list(HealthCheck))
ctx = link._Ctx(cfg=None, project_keys=("KB",), key_re=notes.key_pattern(("KB",)), prefix="@", normalize=True, today="x")

LINE = ["- 09:00 KB-1 논의", "KB-2", "`KB-3`", "```", "~~~", "    KB-4", "https://j/browse/KB-5", "[[KB-6]]", "",
        "<!-- kb:extracted:start -->", "<!-- kb:extracted:end -->", "## 기록", "## 다른", "---", "- [ ] KB-7 가", "\x0cKB-8", "KB-9\u2028KB-10"]
EOL = [b"\n", b"\r\n", b"\r\r\n", b"\r"]


@SET
@given(st.booleans(), st.lists(st.tuples(st.sampled_from(LINE), st.sampled_from(EOL)), max_size=10), st.lists(st.sampled_from(LINE), max_size=3))
def test_normalize_daily_bytes(bom, lines, pre):
    data = (managed.BOM if bom else b"") + b"".join(l.encode() + b"\n" for l in pre) + "## 기록\n".encode()
    data += b"".join(l.encode("utf-8") + e for l, e in lines)
    try:
        managed.spans(data)
    except managed.MarkerError:
        return
    new, n = link._normalize_daily("daily/2026-11-15.md", data, ctx)
    strip = lambda b: b.replace(b"[[", b"").replace(b"]]", b"")
    assert strip(new) == strip(data), (data, new)
    again, n2 = link._normalize_daily("daily/2026-11-15.md", new, ctx)
    assert again == new and n2 == 0, (data, new, again)
    # nothing inside fenced code or managed region changed
    for line in notes.iter_lines("daily/2026-11-15.md", data):
        if line.in_code or line.region is not None:
            seg = data[line.offset: line.offset + len(line.text.encode())]
            assert seg in new
