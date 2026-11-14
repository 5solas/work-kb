"""Repro: ingest decides whether an inbox image may be moved+renamed by scanning daily/ only.

1) An inbox image embedded from an inbox note (or a wiki page) is not 'pinned' -> it is moved to
   raw/assets/YYYY-MM-DD-HHMM-<name> and the note's ![[...]] link breaks.
2) An inbox image whose stem equals an existing inbox note (회의.png + 회의.md) is treated as
   'already has a sidecar' and is never ingested.
"""
import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from lib import config  # noqa: E402
import ingest_images  # noqa: E402

png = (REPO / "tests" / "fixtures" / "images" / "whiteboard.png").read_bytes()
root = Path(tempfile.mkdtemp(prefix="agentC-inbox-"))
try:
    for sub in ("daily", "inbox", "raw/assets", "wiki/topics"):
        (root / sub).mkdir(parents=True)
    shutil.copyfile(REPO / "config.yaml", root / "config.yaml")
    (root / "config.local.yaml").write_text("environment: home\n", encoding="utf-8")
    (root / "inbox" / "사진.png").write_bytes(png)
    (root / "inbox" / "현장 메모.md").write_text("# 현장 메모\n\n![[사진.png]]\n", encoding="utf-8")
    (root / "wiki" / "topics" / "배포.md").write_text("![[inbox/사진.png]]\n", encoding="utf-8")
    (root / "inbox" / "회의.png").write_bytes(png[:-1] + b"\x00")
    (root / "inbox" / "회의.md").write_text("# 회의 (사용자가 쓴 inbox 글)\n", encoding="utf-8")

    cfg = config.load(root, environ={})
    targets = {t.name: t for t in ingest_images.collect(cfg)}
    t = targets.get("사진.png")
    print("사진.png target:", None if t is None else {"pinned": t.pinned, "moves": t.moves})
    if t is not None and t.moves:
        dest = ingest_images.asset_destination(cfg, t.path, dt.datetime(2026, 11, 5, 18, 30))
        print("  would move to:", dest.relative_to(root).as_posix(), "-> ![[사진.png]] in inbox/현장 메모.md and wiki/topics/배포.md break")
    print("회의.png collected:", "회의.png" in targets, "(False = never ingested, inbox/회의.md is a user note, not a sidecar)")
    bugs = []
    if t is not None and t.moves:
        bugs.append("C-40: inbox image embedded from inbox/wiki notes would be moved (links break)")
    if "회의.png" not in targets:
        bugs.append("C-41: inbox image with a same-name user note is never ingested")
    if bugs:
        print("FAIL:", "; ".join(bugs))
        raise SystemExit(1)
    print("OK")
finally:
    shutil.rmtree(root, ignore_errors=True)
