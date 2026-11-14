"""C-12 / C-13 repro: Lua names.safe_name and open.lua `@이름` reading differ from Python core.safe_name / notes.find_people.

Runs the Lua side in headless Neovim (XDG folders in %TEMP%) and compares with Python on the same inputs.
Run: .venv\\Scripts\\python.exe -X utf8 review/repro/C-12_C-13_name_parity.py   (exit 1 = reproduced)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from lib import core, notes  # noqa: E402

SAFE_CASES = ["회의 메모", "회의　메모", "　앞 공백", "끝 공백 ", "a b", "conın$", "a\u0085b"]
PERSON_CASES = ["@Nguyễn 회의", "@Иван 회의", "@김철수µ x", "@김×철수", "@김철수 x"]

LUA = r"""
vim.opt.rtp:append(REPO .. "/nvim")
local data = vim.json.decode(table.concat(vim.fn.readfile(IN), "\n"))
local names, open = require("work-kb.names"), require("work-kb.open")
local out = { safe = {}, person = {} }
for i, c in ipairs(data.safe) do out.safe[i] = names.safe_name(c) end
for i, c in ipairs(data.person) do local t = open.thing_at_point(c, 1); out.person[i] = t and t.value or vim.NIL end
vim.fn.writefile({ vim.json.encode(out) }, OUT)
vim.cmd("qa!")
"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="c12-"))
    inp, out, script = tmp / "in.json", tmp / "out.json", tmp / "run.lua"
    inp.write_text(json.dumps({"safe": SAFE_CASES, "person": PERSON_CASES}), encoding="utf-8")
    head = f"REPO = [[{REPO.as_posix()}]]\nIN = [[{inp.as_posix()}]]\nOUT = [[{out.as_posix()}]]\n"
    script.write_text(head + LUA, encoding="utf-8")
    env = dict(os.environ)
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        env[f"XDG_{name}_HOME"] = str(tmp / "xdg" / name.lower())
    subprocess.run(["nvim", "--headless", "--clean", "-i", "NONE", "-n", "-l", str(script)], env=env, check=True, timeout=60)
    lua = json.loads(out.read_text(encoding="utf-8"))
    diffs = 0
    for c, l in zip(SAFE_CASES, lua["safe"], strict=True):
        p = core.safe_name(c)
        diffs += p != l
        print(("SAME " if p == l else "DIFF ") + f"safe_name in={c!r:24} python={p!r:20} lua={l!r}")
    for c, l in zip(PERSON_CASES, lua["person"], strict=True):
        found = notes.find_people(c)
        p = found[0] if found else None
        diffs += p != l
        print(("SAME " if p == l else "DIFF ") + f"@이름    in={c!r:24} python link page={p!r:14} lua o looks for={l!r}")
    if diffs:
        print(f"FAIL: {diffs} Lua/Python differences")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
