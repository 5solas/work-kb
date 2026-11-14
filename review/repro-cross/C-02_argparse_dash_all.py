"""Repro: user text starting with '-' passed as a separate argv item (exactly how nvim/lua/work-kb/jira.lua
and search.lua build argv) is mis-parsed by argparse.

Expected: each command treats the value as data.
Actual: '-h…' positional -> help printed, exit 0 (nvim reports success); other '-x' values -> exit 4.
"""
from repro_kb import make_vault, run

vault, keyring = make_vault()  # base_url points at a closed port; nothing reaches any server
bad = []
cases = [
    ("act", "assign", "KB-12", "-hong"),                 # jira.lua M.assign: act_args("assign", key, user)
    ("search", "-hello"),                                  # search.lua: kb_args("search", query)
    ("search", "-foo"),
    ("act", "comment", "KB-12", "--body", "-foo"),        # jira.lua M.comment --body body
    ("act", "comment", "KB-12", "--body", "->완료"),
    ("act", "worklog", "KB-12", "1h", "--comment", "-리뷰"),
    ("act", "create", "KB", "--summary", "-긴급", "--type", "Task", "--yes"),
]
for args in cases:
    p = run(vault, keyring, *args)
    if p.returncode == 0 and "usage:" in p.stdout:
        bad.append((args, "help printed, exit 0 (false success)"))
    elif p.returncode == 4 and ("expected one argument" in p.stderr or "unrecognized" in p.stderr or "required" in p.stderr):
        bad.append((args, "argparse rejected user text, exit 4"))
print("\n--- the '=' form parses fine (exit 3 = queued, closed port):")
run(vault, keyring, "act", "comment", "KB-12", "--body=-foo")
print("\nFAILURES:")
for args, why in bad:
    print(" ", args, "->", why)
raise SystemExit(1 if bad else 0)
