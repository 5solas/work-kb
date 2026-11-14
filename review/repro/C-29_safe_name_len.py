"""C-29 repro: core.safe_name adds the reserved-name '_' after truncation, so the result exceeds max_len and is not idempotent.
Run: .venv\\Scripts\\python.exe -X utf8 review/repro/C-29_safe_name_len.py   (exit 1 = reproduced)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from lib import core  # noqa: E402

name = "CON." + "0" * 76
once = core.safe_name(name)
twice = core.safe_name(once)
print(f"len(in)={len(name)} len(once)={len(once)} idempotent={once == twice}")
if len(once) > 80 or once != twice:
    print("FAIL: safe_name result longer than max_len=80 or not idempotent")
    sys.exit(1)
print("OK")
