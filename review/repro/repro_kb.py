"""Shared helpers for repro scripts: temp vault + kb.py subprocess runner (fake keyring, loopback guard)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(tempfile.gettempdir()) / "work-kb-repro"; SCRATCH.mkdir(exist_ok=True)
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
TOKEN = "repro-token-abcdef"


def make_vault(base_url: str = "http://127.0.0.1:1") -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp(prefix="볼트 repro ", dir=SCRATCH)) / "work-kb"
    shutil.copytree(REPO / "examples" / "sample-vault", root)
    shutil.copy(REPO / "config.yaml", root / "config.yaml")
    (root / "config.local.example.yaml").unlink()
    (root / "config.local.yaml").write_text(
        "environment: home\njira:\n"
        f"  base_url: \"{base_url}\"\n  project_keys: [KB, OPS]\n"
        "  pat_ref: \"keyring:work-kb/jira\"\n  trust_env: false\n  timeout: 5\n"
        "confluence:\n  enabled: false\nllm:\n  enabled: false\n",
        encoding="utf-8",
    )
    keyring = root.parent / "keyring.json"
    keyring.write_text(json.dumps({"work-kb\njira": TOKEN}), encoding="utf-8")
    return root, keyring


def run(vault: Path, keyring: Path, *args: str, extra: dict | None = None, script: str = "kb.py", stdin: str | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
    env.update({
        "PYTHONPATH": str(REPO / "tests"),
        "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
        "KB_FAKE_KEYRING_FILE": str(keyring),
        "KB_ROOT": str(vault),
        "PYTHONUTF8": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "KB_LOCK_TIMEOUT": "3",
    })
    env.update(extra or {})
    cmd = [sys.executable, "-X", "utf8", str(REPO / "scripts" / script), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                       cwd=str(REPO), input=stdin, timeout=120)
    print(f"$ {script} {' '.join(args)}  -> exit {p.returncode}")
    text = (p.stdout + (("\n[stderr] " + p.stderr) if p.stderr.strip() else "")).strip()
    print("   " + text[:600].replace("\n", "\n   "))
    return p
