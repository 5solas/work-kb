"""A-20 재현: `wikimarkup.to_markdown`의 중첩 placeholder NUL 바이트 누수와 {quote} 내 여러 줄 {code} 깨짐."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import wikimarkup  # noqa: E402

nested = wikimarkup.to_markdown("[see {{KB-12}}|https://example.com]")
quoted_code = wikimarkup.to_markdown("{quote}\n{code}\nline1\nline2\n{code}\n{quote}")
print("nested:", repr(nested))
print("quoted_code:\n" + quoted_code)
assert "\x00" not in nested, f"Unexpanded NUL placeholder leaked: {nested!r}"
