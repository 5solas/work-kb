import sys, datetime as dt
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'scripts'))
import lint
v = lint._Vault(cfg=None, now=dt.datetime.now())
v.add_file("wiki/topics/Node.js.md")
v.add_file("inbox/20261105-0912-v1.2 배포 회의.md")
print("lint resolve [[Node.js]] ->", v.resolve("daily/2026-11-05.md", "Node.js"))
print("lint resolve [[20261105-0912-v1.2 배포 회의]] ->", v.resolve("daily/2026-11-05.md", "20261105-0912-v1.2 배포 회의"))

# (C-09 Lua side: C-09_dotted_link.lua) Python lint resolves both names; exit 0 here is expected.

