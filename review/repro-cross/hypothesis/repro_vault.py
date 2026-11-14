import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
for k in list(os.environ):
    if k.startswith("KB_"):
        del os.environ[k]

from lib import config  # noqa: E402

SCRATCH = Path(tempfile.gettempdir()) / "work-kb-repro"


def make_vault(keys=("KB", "OPS")):
    SCRATCH.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="v-", dir=SCRATCH)) / "한글 경로"
    for sub in ("daily", "inbox", "jira/_views", "wiki/projects", "wiki/people", ".state"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "config.yaml", root / "config.yaml")
    config.save_local(root, {"environment": "home", "jira": {"project_keys": list(keys)}})
    return root, config.load(root, environ={})
