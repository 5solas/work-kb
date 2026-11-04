"""link 성능 예산(perf): daily 1,000개 × 20줄, 두 번째(웜) 실행 ≤ 2.0초, 쓰기 0.

실행: .\\.venv\\Scripts\\python -m pytest -m perf -q -s tests/test_link_perf.py
콜드 시간은 출력만 하고 단언하지 않는다(보고용).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pytest

import link
from lib import config

pytestmark = pytest.mark.perf

DAYS = 1000
LINES = 20
WARM_BUDGET = 2.0


def _generate(vault: Path) -> None:
    start = dt.date(2024, 1, 1)
    for d in range(DAYS):
        day = start + dt.timedelta(days=d)
        lines = ["---", f"date: {day.isoformat()}", "---", "## 기록"]
        for i in range(LINES):
            n = (d * LINES + i) % 300 + 1
            hhmm = f"{8 + i // 4:02d}:{(i % 4) * 15:02d}"
            if i % 5 == 0:
                lines.append(f"- {hhmm} KB-{n} 맨 글자 메모 {d}-{i} @사람{n % 30}")
            elif i % 5 == 1:
                lines.append(f"- {hhmm} [[OPS-{n % 80 + 1}]] 회의 정리 {d}-{i}")
            elif i % 5 == 2:
                lines.append(f"- [ ] {hhmm} 개인 할 일 {d}-{i}")
            elif i % 5 == 3:
                lines.append(f"- {hhmm} 언급 없는 평범한 줄 {d}-{i} https://x.invalid/KB-{n}")
            else:
                lines.append(f"- {hhmm} @사람{i} 와 [[KB-{n}]] 검토 {d}-{i}")
        (vault / "daily" / f"{day.isoformat()}.md").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def _mtimes(vault: Path) -> dict[str, int]:
    out = {}
    for path in vault.rglob("*"):
        rel = path.relative_to(vault).as_posix()
        if path.is_file() and not rel.startswith(".state/logs/"):
            out[rel] = path.stat().st_mtime_ns
    return out


def test_warm_run_within_budget_and_writes_nothing(vault, fake_now):
    fake_now("2026-11-05T09:00:00")
    config.save_local(vault, {"environment": "home", "jira": {"project_keys": ["KB", "OPS"]}})
    _generate(vault)
    cfg = config.load(vault)

    t0 = time.perf_counter()
    cold = link.run(cfg)
    cold_s = time.perf_counter() - t0
    assert cold["files_scanned"] == DAYS
    assert cold["normalized_lines"] == DAYS * LINES // 5

    before = _mtimes(vault)
    t1 = time.perf_counter()
    warm = link.run(cfg)
    warm_s = time.perf_counter() - t1
    print(f"\nlink perf: cold {cold_s:.2f}s, warm {warm_s:.2f}s, pages {cold['pages_created']}, mentions {cold['mentions']}")

    assert warm["written"] == []
    assert warm["files_parsed"] == 0
    assert _mtimes(vault) == before
    assert warm_s <= WARM_BUDGET, f"웜 실행 {warm_s:.2f}s > {WARM_BUDGET}s (콜드 {cold_s:.2f}s)"
