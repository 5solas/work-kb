"""A-12 재현: `link.py`가 `wiki/topics/*.md` 연결·별칭·언급(`mentions`) 갱신을 완전히 누락한다."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "scripts" / "kb.py"
SAMPLE = ROOT / "examples" / "sample-vault"

with tempfile.TemporaryDirectory(prefix="repro_a12_") as tmp:
    vault = Path(tmp) / "vault"
    shutil.copytree(SAMPLE, vault)
    (vault / "config.local.yaml").write_text(
        "environment: home\njira:\n  project_keys: [KB]\n", encoding="utf-8"
    )
    topics = vault / "wiki" / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    topic_file = topics / "캐시.md"
    topic_file.write_text(
        "---\ntopic: 캐시\naliases: [TTL]\n---\n# 캐시\n\n"
        "## 언급\n<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n",
        encoding="utf-8",
    )
    daily = vault / "daily" / "2026-11-15.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "---\ndate: 2026-11-15\n---\n## 기록\n"
        "- 09:00 [[topics/캐시]] 구조와 TTL 정책 검토\n",
        encoding="utf-8",
    )

    res = subprocess.run(
        [sys.executable, "-X", "utf8", str(KB), "link", "--root", str(vault)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    print("link exit:", res.returncode, "out:", res.stdout.strip(), "err:", res.stderr.strip())
    assert res.returncode == 0, f"link itself failed: {res.stderr}"
    content = topic_file.read_text(encoding="utf-8")
    print("topic mentions content:\n" + content)
    assert "2026-11-15" in content, "Expected wiki/topics/캐시.md mentions to contain daily backlink"
