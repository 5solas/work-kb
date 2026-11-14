"""Repro: compile._one_line strips '<!--' / '-->' in a single pass, so nested input rebuilds them.

LLM output (T2 title/content/rationale, T3 summary, T4 why) can therefore inject an HTML comment
(e.g. a forged `<!-- src:... -->` marker, or an unclosed `<!--`) into wiki/decisions.md.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import compile as kbc  # noqa: E402
import confluence_push as cp  # noqa: E402

payload_src = "결정 <!<!---- src:0123456789abcdef ---->>"
payload_open = "결정 <!<!---- 이후 전부 숨김"
out1 = kbc._one_line(payload_src, kbc.MAX_TEXT)
out2 = kbc._one_line(payload_open, kbc.MAX_TEXT)
print("in :", payload_src)
print("out:", out1)
print("src marker rebuilt:", bool(kbc._SRC_RE.search(out1)))
print("out:", out2, "| contains '<!--':", "<!--" in out2)

# What compile would append for a decision whose LLM content is payload_open
item = kbc.Item(rel="daily/2026-11-05.md", hash="h" * 12, text="x", day="2026-11-05", source="[[2026-11-05]] 10:00")
entry = kbc._decision_entry("2026-11-05", item, {"title": "제목", "content": payload_open}, [])
print("--- decisions.md entry ---")
print(entry)

# Confluence: once a human marks it 확정, the unclosed comment makes push fail for the whole file
confirmed = "# 결정\n\n" + entry.replace("- 상태: 제안", "- 상태: 확정")
try:
    doc = cp.prepare("wiki/decisions.md", confirmed.encode("utf-8"), "[KB] ")
    print("prepare ok:", doc.body[:200])
except Exception as exc:
    print("prepare FAILS:", type(exc).__name__, exc)

if "<!--" in kbc._one_line("결정 <!<!---- src:0123456789abcdef ---->>", kbc.MAX_TEXT):
    print("FAIL (C-39): _one_line rebuilds HTML comment markers from nested input")
    sys.exit(1)
print("OK")
