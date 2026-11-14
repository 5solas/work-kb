"""Repro: kb.py's KB_AGENT sub-command allowlist looks only at argv[1]. `act --root V list` (read-only, allowed)
is refused with 7 while `act list --root V` is allowed. jira_act.main itself accepts both orders.
"""
from repro_kb import make_vault, run

vault, keyring = make_vault()
a = run(vault, keyring, "act", "list", "--root", str(vault), extra={"KB_AGENT": "1"})
b = run(vault, keyring, "act", "--root", str(vault), "list", extra={"KB_AGENT": "1"})
c = run(vault, keyring, "act", "--root", str(vault), "list")
bug = a.returncode == 0 and b.returncode == 7 and c.returncode == 0
print("\nfalse refusal:", bug)
raise SystemExit(1 if bug else 0)
