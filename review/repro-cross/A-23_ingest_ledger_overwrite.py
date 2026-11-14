"""A-23 재현: `ingest_images._Run`이 `kb_lock` 밖에서 읽은 `self.ledger` 전체를 저장해 동시 원장 갱신을 덮어쓴다."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_images  # noqa: E402
from lib import config, core  # noqa: E402

SAMPLE_IMG = ROOT / "examples" / "sample-vault" / "raw" / "assets" / "2026-11-05-1420-메일캡처.png"


class FakeClient:
    pass


with tempfile.TemporaryDirectory(prefix="repro_a23_") as tmp:
    vault = Path(tmp) / "vault"
    shutil.copytree(ROOT / "examples" / "sample-vault", vault)
    (vault / "config.local.yaml").write_text("environment: home\njira:\n  project_keys: [KB]\n", encoding="utf-8")
    shutil.copyfile(SAMPLE_IMG, vault / "inbox" / "a.png")
    cfg = config.load(vault)

    run = ingest_images._Run(cfg, FakeClient(), dry_run=False, out=lambda _s: None, profiles=["main=m"])
    # run.__init__ 이후 다른 프로세스가 .state/images.json에 다른 이미지 기록을 저장한 상황 시뮬레이션
    concurrent_ledger = ingest_images.load_ledger(cfg)
    concurrent_ledger["images"]["concurrent-sha"] = {"source_image": "raw/assets/other.png", "sidecar": "inbox/other.md"}
    core.save_state(cfg.root, ingest_images.STATE, concurrent_ledger)

    # run._skip이 self.ledger를 그대로 save_state해 버리면 concurrent-sha가 유실됨
    target = ingest_images.Target("inbox", vault / "inbox" / "a.png", "inbox/a.png")
    run._skip(target, "sha-a", "simulated failure")

    after = ingest_images.load_ledger(cfg)
    print("ledger keys after _skip:", list(after["images"].keys()))
    assert "concurrent-sha" in after["images"], "Concurrent ledger update lost because _Run wrote stale self.ledger"
