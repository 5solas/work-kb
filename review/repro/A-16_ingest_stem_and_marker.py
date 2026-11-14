"""A-16 재현: `ingest_images`가 동일 stem 이미지(`shot.png`/`shot.jpg`)의 두 번째 파일을 건너뛰며, `<!--`를 이스케이프하지 않는다."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_images  # noqa: E402
from lib import config, managed  # noqa: E402

SAMPLE_IMG = ROOT / "examples" / "sample-vault" / "raw" / "assets" / "2026-11-05-1420-메일캡처.png"

with tempfile.TemporaryDirectory(prefix="repro_a16_") as tmp:
    vault = Path(tmp) / "vault"
    shutil.copytree(ROOT / "examples" / "sample-vault", vault)
    (vault / "config.local.yaml").write_text("environment: home\njira:\n  project_keys: [KB]\n", encoding="utf-8")
    (vault / "raw" / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SAMPLE_IMG, vault / "raw" / "assets" / "shot.png")
    shutil.copyfile(SAMPLE_IMG, vault / "raw" / "assets" / "shot.jpg")
    (vault / "inbox" / "shot.md").write_text("---\nsource_image: raw/assets/shot.png\n---\n", encoding="utf-8")
    daily = vault / "daily" / "2026-11-15.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "---\ndate: 2026-11-15\n---\n## 기록\n- ![[raw/assets/shot.png]]\n- ![[raw/assets/shot.jpg]]\n",
        encoding="utf-8",
    )

    cfg = config.load(vault)
    targets = [t.rel for t in ingest_images.collect(cfg)]
    print("collect(cfg) targets:", targets)

    ex = ingest_images.Extraction(
        kind="screenshot",
        text="line1\n<!-- kb:extracted:end -->\nline2",
        summary="sum",
        jira_keys=(),
        people=(),
    )
    rendered = ingest_images.render_extracted(ex)
    sidecar = ingest_images.render_sidecar(
        source_image="raw/assets/shot.jpg",
        ingested_at="2026-11-15T10:00:00+09:00",
        model="m",
        kind="screenshot",
        extracted=rendered,
    )
    read_back = managed.read_region(sidecar, "extracted").decode("utf-8")
    print("raw <!-- present in extracted:", "<!-- kb:extracted:end -->" in read_back)
    assert "raw/assets/shot.jpg" in targets, f"shot.jpg skipped due to shared inbox/shot.md stem: {targets}"
