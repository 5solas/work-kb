"""A-13 재현: `uncertain`(attempts=5) outbox 레코드가 `--retry-uncertain` + 429 시 `failed`로 전락한다."""
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
sys.path.insert(0, str(ROOT / "scripts"))

import mock_jira  # noqa: E402

KB = ROOT / "scripts" / "kb.py"
SAMPLE = ROOT / "examples" / "sample-vault"

with tempfile.TemporaryDirectory(prefix="repro_a13_") as tmp:
    tmp_path = Path(tmp)
    vault = tmp_path / "vault"
    shutil.copytree(SAMPLE, vault)
    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text(json.dumps({"work-kb\njira_pat": "mock-tok"}), encoding="utf-8")

    with mock_jira.start(
        port=0,
        require_token="mock-tok",
        on_write=lambda _req: (429, {"errorMessages": ["rate limited"]}, {"Retry-After": "0"}),
    ) as mj:
        (vault / "config.local.yaml").write_text(
            f"environment: home\njira:\n  base_url: {mj.base_url}\n"
            f"  pat_ref: keyring:work-kb/jira_pat\n  project_keys: [KB]\n",
            encoding="utf-8",
        )
        outbox_file = vault / "jira" / "outbox.jsonl"
        outbox_file.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "id": "01TESTUNCERTAIN000000000001",
            "action": "comment",
            "key": "KB-12",
            "payload": {"body": "이미 반영됐을 수 있는 댓글"},
            "status": "pending",
            "attempts": 5,
            "last_error": "uncertain",
            "created_at": "2026-11-15T09:00:00+09:00",
        }
        outbox_file.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
        env.update({
            "PYTHONPATH": str(ROOT / "tests"),
            "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
            "KB_FAKE_KEYRING_FILE": str(keyring_file),
            "PYTHONUTF8": "1",
        })
        res = subprocess.run(
            [sys.executable, "-X", "utf8", str(KB), "act", "flush", "--retry-uncertain", "--root", str(vault)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
        )
        print("flush exit:", res.returncode, "stderr:", res.stderr.strip())
        final_lines = [json.loads(line) for line in outbox_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        final_rec = final_lines[-1]
        print("final record:", json.dumps(final_rec, ensure_ascii=False))
        assert final_rec["status"] != "failed", (
            f"D52 violation: uncertain record degraded to failed ({final_rec['last_error']!r})"
        )
