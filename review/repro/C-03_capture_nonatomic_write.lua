-- C-03 repro: capture rewrites the daily file in place (open "w" = truncate, then write), so another reader
-- (kb.py link from a scheduled task, Obsidian, sync tools) can see an empty or partial daily file, and a crash or
-- full disk between truncate and write loses the whole day. Python side uses core.atomic_write (temp + replace).
-- Run (repo root):
--   $env:XDG_CONFIG_HOME=$env:XDG_DATA_HOME=$env:XDG_STATE_HOME=$env:XDG_CACHE_HOME="$env:TEMP\c03xdg"
--   nvim --headless --clean -l review/repro/C-03_capture_nonatomic_write.lua      (exit 1 = reproduced)
local repo = vim.fs.normalize(vim.fn.getcwd())
vim.opt.rtp:append(repo .. "/nvim")
local py = repo .. "/.venv/Scripts/python.exe"
local tmp = vim.fs.normalize(vim.fn.tempname()) .. "-c03"
vim.fn.mkdir(tmp, "p")
local file = tmp .. "/2026-11-05.md"
local lines = { "---", "date: 2026-11-05", "---", "## 기록" }
for i = 1, 20000 do
  lines[#lines + 1] = string.format("- 09:%02d 기록 %d KB-%d 긴 줄을 만들어 파일을 키운다", i % 60, i, i)
end
vim.fn.writefile(lines, file, "b")
local size = vim.uv.fs_stat(file).size

-- reader: polls the file for ~4 s and reports the smallest size it saw
local reader = vim.system({ py, "-c", table.concat({
  "import os,time,sys",
  "p=sys.argv[1]; m=None; end=time.time()+4",
  "while time.time()<end:",
  "  try: n=len(open(p,'rb').read())",
  "  except OSError: continue",
  "  m=n if m is None else min(m,n)",
  "print(m)",
}, "\n"), file }, { text = true })
vim.wait(300)
local fileio = require("work-kb.fileio")
local t0 = vim.uv.hrtime()
local n = 0
while (vim.uv.hrtime() - t0) / 1e6 < 3000 and n < 200 do
  n = n + 1
  fileio.append(file, "- 10:00 캡처 " .. n)
end
local res = reader:wait()
local min_seen = tonumber(vim.trim(res.stdout or "")) or -1
print(string.format("file size before=%d bytes; %d captures; smallest size a concurrent reader saw=%d", size, n, min_seen))
vim.fn.delete(tmp, "rf")
if min_seen >= 0 and min_seen < size then
  print("FAIL: a concurrent reader saw a truncated daily file during capture (non-atomic in-place write)")
  os.exit(1)
end
print("OK (no truncated read observed)")
os.exit(0)
