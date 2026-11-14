"""A-17 재현: `act promote`가 대상 project 키만 검사해 이미 `[[OPS-1]]`로 승격된 줄도 `KB`로 중복 승격한다."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

import mock_jira  # noqa: E402

KB = ROOT / "scripts" / "kb.py"
SAMPLE = ROOT / "examples" / "sample-vault"

with tempfile.TemporaryDirectory(prefix="repro_a17_") as tmp:
    tmp_path = Path(tmp)
    vault = tmp_path / "vault"
    shutil.copytree(SAMPLE, vault)
    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text(json.dumps({"work-kb\njira_pat": "mock-tok"}), encoding="utf-8")

    with mock_jira.start(port=0, require_token="mock-tok") as mj:
        (vault / "config.local.yaml").write_text(
            f"environment: home\njira:\n  base_url: {mj.base_url}\n"
            f"  pat_ref: keyring:work-kb/jira_pat\n  project_keys: [KB, OPS]\n",
            encoding="utf-8",
        )
        daily = vault / "daily" / "2026-11-15.md"
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text("---\ndate: 2026-11-15\n---\n## 기록\n- 09:00 결제 오류 확인 [[OPS-1]]\n", encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
        env.update({
            "PYTHONPATH": str(ROOT / "tests"),
            "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
            "KB_FAKE_KEYRING_FILE": str(keyring_file),
            "PYTHONUTF8": "1",
        })
        res = subprocess.run(
            [sys.executable, "-X", "utf8", str(KB), "act", "promote", "--from", "daily/2026-11-15.md:5", "KB", "--yes", "--root", str(vault)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=20,
        )
        print("promote exit:", res.returncode, "out:", res.stdout.strip())
        print("daily line after:", daily.read_text(encoding="utf-8").splitlines()[-1])
        assert res.returncode != 0, "Expected promote to refuse line already ending with [[OPS-1]]"
