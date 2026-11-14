"""A-21 재현: `fts.Cache` 열린 뒤 읽기·검색 시 SQLite 잠금이 `FileBusy`(종료 6) 대신 미처리 `OperationalError`로 누수된다."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import core, fts  # noqa: E402

with tempfile.TemporaryDirectory(prefix="repro_a21_") as tmp:
    db_path = Path(tmp) / "cache.sqlite"
    with fts.Cache(db_path) as cache:
        cache.upsert([{"key": "KB-12", "summary": "API cache", "status": "Open", "assignee": "kim", "updated": "2026-11-15", "body": "hello"}])

    fts.BUSY_TIMEOUT = 0.05
    with fts.Cache(db_path) as reader:
        locker = sqlite3.connect(str(db_path), isolation_level=None)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            try:
                hits = reader.search(["API"])
                print("unexpected hits:", hits)
            except core.FileBusy as exc:
                print("Proper FileBusy:", exc)
            except sqlite3.OperationalError as exc:
                print(f"Leaked raw OperationalError instead of FileBusy: {exc}")
                raise AssertionError(f"Expected core.FileBusy, got sqlite3.OperationalError: {exc}") from exc
        finally:
            locker.rollback()
            locker.close()
