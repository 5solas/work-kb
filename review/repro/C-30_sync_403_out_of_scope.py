"""Repro: an issue that is in the local cache but that the user can no longer see makes EVERY full sync fail
with exit 2 ("인증 실패"), if Jira answers the per-key re-fetch with 403 instead of 404.

jira_sync.fetch_out_of_scope (scripts/jira_sync.py:739-743) only catches NotFoundError(404); a 403 from
client.issue() goes through net.request(auth_raises=True) -> core.AuthError and aborts the whole sync
(nothing written, last_sync/last_full not moved). Next full sync re-tries the same key -> permanent failure,
and the message tells the user their token is wrong.
Whether Jira DC answers 403 or 404 for an issue hidden by permission/security level is 추정 (DC commonly answers
403 "You do not have the permission to see the specified issue." for authenticated users).
"""
from repro_kb import make_vault, run  # noqa: I001 (sets sys.path)

import mock_jira
from lib import frontmatter

mock = mock_jira.start()
try:
    vault, keyring = make_vault(mock.base_url)
    run(vault, keyring, "sync", "--full")
    victim = None
    for path in sorted((vault / "jira").glob("KB-*.md")):
        meta = frontmatter.parse(path.read_bytes())
        if meta.get("status_category") != "done":
            victim = path.stem
            break
    print("victim:", victim)
    mock.remove(victim)  # disappears from scope search; `key in (...)` -> 400 like DC for unknown/invisible keys

    def on_read(req):
        if req.path == f"/rest/api/2/issue/{victim}":
            return 403, {"errorMessages": ["You do not have the permission to see the specified issue."]}
        return None

    mock.modes["on_read"] = on_read
    p = run(vault, keyring, "sync", "--full")
    run(vault, keyring, "sync")  # within 24h: incremental still works
    import datetime as dt
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=25)).isoformat(timespec="seconds")
    # after full_every_hours (24h) every plain `sync` becomes full -> fails; last_full never moves, so forever
    p2 = run(vault, keyring, "sync", extra={"KB_FAKE_NOW": later})
    p3 = run(vault, keyring, "sync", extra={"KB_FAKE_NOW": later})
    bug = p.returncode == 2 and p2.returncode == 2 and p3.returncode == 2
    print("\nfull (and, after 24h, every plain) sync fails with exit 2:", bug)
    raise SystemExit(1 if bug else 0)
finally:
    mock.stop()
