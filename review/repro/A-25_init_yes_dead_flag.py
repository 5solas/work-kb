"""A-25 재현: `scripts/init.py --yes` 옵션이 파서에 정의돼 있으나 실제 코드에서 전혀 참조되지 않는 dead flag다."""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INIT_PY = ROOT / "scripts" / "init.py"

tree = ast.parse(INIT_PY.read_text(encoding="utf-8"))
yes_refs = [
    node.lineno
    for node in ast.walk(tree)
    if isinstance(node, ast.Attribute) and node.attr == "yes"
]
print("AST references to `.yes` in scripts/init.py:", yes_refs)

with tempfile.TemporaryDirectory(prefix="repro_a25_") as tmp:
    vault = Path(tmp) / "vault"
    shutil.copytree(ROOT / "examples" / "sample-vault", vault)
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
    env["PYTHONUTF8"] = "1"
    res = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "kb.py"), "init", "--root", str(vault), "--yes"],
        input="", capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
    )
    print(f"CLI `kb.py init --yes` with empty stdin exit={res.returncode}")
    print("stdout tail:", res.stdout.strip().splitlines()[-2:] if res.stdout.strip() else [])
    assert yes_refs, "Dead flag: `--yes` is defined in init.py build parser but `args.yes` is never referenced"
