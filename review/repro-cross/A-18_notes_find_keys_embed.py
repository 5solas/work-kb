"""A-18 재현: `notes.find_keys`가 이미지 임베드·비-키 위키링크 안에서도 키를 매칭해 `daily_prepare` 개인 할 일 이월이 누락된다."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import notes  # noqa: E402

with tempfile.TemporaryDirectory(prefix="repro_a18_") as tmp:
    vault = Path(tmp) / "vault"
    shutil.copytree(ROOT / "examples" / "sample-vault", vault)
    (vault / "config.local.yaml").write_text("environment: home\njira:\n  project_keys: [KB]\n", encoding="utf-8")

    sample_line = "- [ ] 개인: ![[raw/assets/KB-12-whiteboard.png]] 정리"
    matches = notes.find_keys(sample_line, ["KB"])
    print("notes.find_keys:", matches)

    yday = vault / "daily" / "2026-11-14.md"
    yday.parent.mkdir(parents=True, exist_ok=True)
    yday.write_text(f"---\ndate: 2026-11-14\n---\n## 기록\n{sample_line}\n", encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
    env.update({"KB_FAKE_NOW": "2026-11-15T09:00:00+09:00", "PYTHONUTF8": "1"})
    res = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "kb.py"), "today", "--root", str(vault)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=20,
    )
    today_text = (vault / "daily" / "2026-11-15.md").read_text(encoding="utf-8")
    print("today exit:", res.returncode)
    print("2026-11-15.md:\n" + today_text)
    assert not matches and "KB-12-whiteboard.png" in today_text, (
        f"D20 violation: embed matched {matches} and skipped carryover in 2026-11-15.md"
    )
