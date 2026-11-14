"""A-01 재현: `--root` 등 전역 옵션이 하위 명령 앞에 오면 KB_AGENT=1과 serve가 허용 명령을 거부한다."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "scripts" / "kb.py"
SAMPLE = ROOT / "examples" / "sample-vault"

with tempfile.TemporaryDirectory(prefix="repro_a01_") as tmp:
    vault = Path(tmp) / "vault"
    import shutil
    shutil.copytree(SAMPLE, vault)

    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
    env["KB_AGENT"] = "1"
    env["PYTHONUTF8"] = "1"

    cli = subprocess.run(
        [sys.executable, "-X", "utf8", str(KB), "act", "--root", str(vault), "list"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
    )
    print(f"CLI `act --root <vault> list` exit={cli.returncode} stderr={cli.stderr.strip()!r}")

    req = json.dumps({"id": 1, "argv": ["proposals", "--root", str(vault), "list"]}) + "\n"
    srv = subprocess.run(
        [sys.executable, "-X", "utf8", str(KB), "serve", "--root", str(vault), "--idle-sec", "5"],
        input=req, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
    )
    print(f"SERVE `proposals --root <vault> list` out={srv.stdout.strip()!r}")
    assert cli.returncode == 0, "Expected exit 0 for read-only act list with --root before subcommand"
