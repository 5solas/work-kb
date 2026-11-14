"""A-22 재현: `config.local.yaml`에 `jira:`가 null이고 `KB_JIRA_*` 환경변수가 있으면 `ConfigError` 대신 `AttributeError`가 난다."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import config, core  # noqa: E402

with tempfile.TemporaryDirectory(prefix="repro_a22_") as tmp:
    vault = Path(tmp) / "vault"
    shutil.copytree(ROOT / "examples" / "sample-vault", vault)
    (vault / "config.local.yaml").write_text("environment: home\njira:\n", encoding="utf-8")

    try:
        config.load(vault, environ={"KB_JIRA_MAX_RESULTS": "50"})
    except core.ConfigError as exc:
        print("Proper ConfigError:", exc)
    except AttributeError as exc:
        print(f"Unhandled AttributeError instead of ConfigError: {exc}")
        raise AssertionError(f"Expected ConfigError, got AttributeError: {exc}") from exc
