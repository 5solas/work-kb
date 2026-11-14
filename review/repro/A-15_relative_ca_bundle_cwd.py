"""A-15 재현: 상대 경로 `ca_bundle`이 `doctor`에선 config_dir 기준 통과, `sync`에선 CWD 기준 실패한다."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "scripts" / "kb.py"
SAMPLE = ROOT / "examples" / "sample-vault"

with tempfile.TemporaryDirectory(prefix="repro_a15_") as tmp:
    tmp_path = Path(tmp)
    vault = tmp_path / "vault"
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()
    shutil.copytree(SAMPLE, vault)
    (vault / "certs").mkdir()
    (vault / "certs" / "corp.pem").write_text("DUMMY PEM\n", encoding="utf-8")

    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text(json.dumps({"work-kb\njira_pat": "mock-tok"}), encoding="utf-8")
    (vault / "config.local.yaml").write_text(
        "environment: home\njira:\n  base_url: https://127.0.0.1:9\n"
        "  pat_ref: keyring:work-kb/jira_pat\n  project_keys: [KB]\n  ca_bundle: certs/corp.pem\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
    env.update({
        "PYTHONPATH": str(ROOT / "tests"),
        "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
        "KB_FAKE_KEYRING_FILE": str(keyring_file),
        "PYTHONUTF8": "1",
    })
    res = subprocess.run(
        [sys.executable, "-X", "utf8", str(KB), "sync", "--root", str(vault)],
        cwd=str(other_cwd), capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
    )
    print("sync exit:", res.returncode, "stderr:", res.stderr.strip())
    assert "ca_bundle 파일이 없습니다" not in res.stderr, "JiraClient failed to resolve relative ca_bundle against config_dir"
