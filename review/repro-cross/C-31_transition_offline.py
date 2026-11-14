"""Repro: with Jira unreachable, `act transition KEY <name|id> --yes` exits 3 and queues NOTHING
(name->id lookup happens before outbox). Brief §6.3: every write goes pending -> send -> keep on network error -> flush.
nvim always sends the numeric id it got from `act transitions`, so the id is already known, yet it is still not queued.
"""
from pathlib import Path

from repro_kb import make_vault, run

vault, keyring = make_vault()  # closed port
p = run(vault, keyring, "act", "transition", "KB-12", "31", "--yes")
outbox = vault / "jira" / "outbox.jsonl"
queued = outbox.exists() and outbox.read_text(encoding="utf-8").strip() != ""
c = run(vault, keyring, "act", "comment", "KB-12", "--body", "비교: 댓글은 큐에 남음")
print("\ntransition exit:", p.returncode, "queued:", queued, "| comment exit:", c.returncode)
raise SystemExit(1 if (p.returncode == 3 and not queued) else 0)
