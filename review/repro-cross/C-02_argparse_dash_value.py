"""C-02 repro: a value that starts with '-' (comment body, search query, ...) is rejected by argparse.

The Neovim plugin sends `["act","comment",KEY,"--root",V,"--body",BODY]` and `["search","--root",V,QUERY]`
through `kb.py --argv-stdin` exactly like below. A body such as "-긴급" or a query "-foo" ends with exit 4.
Run: .venv\\Scripts\\python.exe -X utf8 review/repro/C-02_argparse_dash_value.py   (exit 1 = bug reproduced)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-X", "utf8", "scripts/kb.py", "--argv-stdin"], cwd=REPO, env=env,
                       input=json.dumps(argv).encode("utf-8"), capture_output=True)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace").strip()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="c02-"))
    try:
        vault = tmp / "vault"
        for sub in ("daily", "jira", ".state"):
            (vault / sub).mkdir(parents=True)
        shutil.copy(REPO / "config.yaml", vault / "config.yaml")
        (vault / "config.local.yaml").write_text(
            "environment: home\njira:\n  base_url: http://127.0.0.1:9\n  project_keys: [KB]\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
        cases = [
            ("search query '-foo'", ["search", "--root", str(vault), "-foo"]),
            ("comment body '-긴급'", ["act", "comment", "KB-1", "--root", str(vault), "--body", "-긴급"]),
        ]
        failed = 0
        for label, argv in cases:
            code, out = run(argv, env)
            argparse_error = code == 4 and "expected one argument" in out or "unrecognized arguments" in out
            print(f"{label}: exit {code}: {out.splitlines()[-1] if out else ''}")
            failed += argparse_error
        if failed:
            print(f"FAIL: {failed} value(s) starting with '-' rejected by argparse")
            return 1
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
