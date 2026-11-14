"""C-35 repro: `python scripts/wheels.py` run directly is not refused under KB_AGENT=1 (kb.py wheels is, exit 7).
D57: scripts refuse by themselves too (jira_act, init, demo, schedule, push, compile do). Uses --dry-run (no network).
Run: .venv\\Scripts\\python.exe -X utf8 review/repro/C-35_wheels_agent.py   (exit 1 = reproduced)
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
env = {k: v for k, v in os.environ.items() if not k.startswith("KB_")}
env["KB_AGENT"] = "1"
via_kb = subprocess.run([sys.executable, "-X", "utf8", "scripts/kb.py", "wheels", "--dry-run"], cwd=REPO, env=env, capture_output=True)
direct = subprocess.run([sys.executable, "-X", "utf8", "scripts/wheels.py", "--dry-run"], cwd=REPO, env=env, capture_output=True)
print(f"KB_AGENT=1 kb.py wheels --dry-run -> exit {via_kb.returncode}; python scripts/wheels.py --dry-run -> exit {direct.returncode}")
if via_kb.returncode == 7 and direct.returncode != 7:
    print("FAIL: direct wheels.py is not refused under KB_AGENT=1")
    sys.exit(1)
print("OK")
