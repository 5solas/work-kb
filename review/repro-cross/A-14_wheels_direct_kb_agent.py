"""A-14 재현: `wheels.main` 직접 호출 시 `KB_AGENT=1` 거부(`AgentRefused`, 종료 7)가 누락되어 실행된다."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ["KB_AGENT"] = "1"
import wheels  # noqa: E402
from lib import core  # noqa: E402

try:
    rc = wheels.main(["--dry-run"])
    print(f"wheels.main(['--dry-run']) under KB_AGENT=1 returned {rc} instead of raising PolicyRefused")
    raise AssertionError(f"Expected PolicyRefused (exit 7), got return code {rc}")
except core.PolicyRefused as exc:
    print(f"Properly refused: {exc}")
