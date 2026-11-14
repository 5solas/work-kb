"""A-19 재현: `confluence_push.prepare`가 코드 블록·인라인 코드 안의 HTML 주석까지 삭제한다."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import confluence_push  # noqa: E402

md = "# Title\n\n```markdown\n<!-- example marker -->\nhello\n```\n\ninline `<!-- keep -->` code\n"
doc = confluence_push.prepare("wiki/topics/demo.md", md.encode("utf-8"), "[KB] ")
print("prepared body:\n" + doc.body)
assert "<!-- example marker -->" in doc.body and "<!-- keep -->" in doc.body, (
    "HTML comment inside fenced/inline code was stripped"
)
