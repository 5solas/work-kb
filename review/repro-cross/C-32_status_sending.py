"""Repro: a record whose sender died mid-send (status sending, dead pid, old claim) is shown by
`act list` as `uncertain` (needs --retry-uncertain), but `kb.py status` reports it as "전송 중 1개"
forever and never mentions it needs a human decision.
"""
import json
import subprocess
import sys

from repro_kb import make_vault, run

vault, keyring = make_vault()
dead = subprocess.Popen([sys.executable, "-c", "pass"])
dead.wait()
record = {
    "id": "01J0000000AAAAAAAAAAAAAAAA", "ts": "2026-11-05T16:05:00+09:00", "action": "comment", "key": "KB-12",
    "payload": {"body": "보내다 끊긴 댓글"}, "source": None, "status": "sending", "attempts": 1,
    "last_error": None, "done_at": None, "claimed_at": "2026-11-05T07:05:00+00:00", "pid": dead.pid,
}
(vault / "jira").mkdir(exist_ok=True)
(vault / "jira" / "outbox.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
lst = run(vault, keyring, "act", "list")
st = run(vault, keyring, "status")
stj = run(vault, keyring, "status", "--json")
ok = "uncertain" in lst.stdout and "전송 중 1개" in st.stdout
print("\nMISMATCH (list=uncertain, status=전송 중):", ok)
raise SystemExit(1 if ok else 0)
