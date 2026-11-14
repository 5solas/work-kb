import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repro_vault as vaulthelp  # noqa: E402,F401

import yaml  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

from lib import config  # noqa: E402

SET = settings(max_examples=200, deadline=None, suppress_health_check=list(HealthCheck))
opt_int = st.one_of(st.none(), st.integers(1, 999))


@SET
@given(opt_int, opt_int, opt_int, st.sampled_from(["KB_JIRA_POLL_MINUTES", "kb_jira_poll_minutes", "Kb_Jira_Poll_Minutes"]),
       opt_int, opt_int, opt_int)
def test_layer_order(cy, cl, ce, envname, ty, tl, te):
    root, _ = vaulthelp.make_vault()
    try:
        structure = {"jira": {"project_keys": ["KB"]}, "llm": {"tasks": {}, "profiles": {}}}
        if cy is not None:
            structure["jira"]["poll_minutes"] = cy
        if ty is not None:
            structure["llm"]["timeout_sec"] = ty
        (root / "config.yaml").write_text(yaml.safe_dump(structure), encoding="utf-8")
        local = {"environment": "home"}
        if cl is not None:
            local["jira"] = {"poll_minutes": cl}
        if tl is not None:
            local["llm"] = {"timeout_sec": tl}
        (root / "config.local.yaml").write_text(yaml.safe_dump(local), encoding="utf-8")
        env = {}
        if ce is not None:
            env[envname] = str(ce)
        if te is not None:
            env["KB_LLM_TIMEOUT_SEC"] = str(te)
        cfg = config.load(root, environ=env)
        want = next(v for v in (ce, cl, cy, 15) if v is not None)
        assert cfg.get("jira.poll_minutes") == want
        want_t = next(v for v in (te, tl, ty, 180) if v is not None)
        assert cfg.get("llm.timeout_sec") == want_t
        assert cfg.get("jira.max_results") == 500  # sibling default survives deep merge
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)
