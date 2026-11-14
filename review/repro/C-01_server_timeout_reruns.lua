-- C-01 repro: a served request that waits longer than server.request_timeout_ms kills the resident server,
-- the same command is run again as a one-shot, and the server stays down for restart_interval_ms (60 s).
-- Real trigger: `link`/`today` waiting on kb_lock (up to 60 s) held by a scheduled `sync`, or
-- `act transitions` waiting on a slow Jira (jira.timeout 30 s = request_timeout_ms 30 s).
-- Here the lock is held for 6 s and request_timeout_ms is lowered to 2 s to keep the run short.
-- Run (repo root):
--   $env:XDG_CONFIG_HOME=$env:XDG_DATA_HOME=$env:XDG_STATE_HOME=$env:XDG_CACHE_HOME="$env:TEMP\c01xdg"
--   nvim --headless --clean -l review/repro/C-01_server_timeout_reruns.lua     (exit 1 = reproduced)
local repo = vim.fs.normalize(vim.fn.getcwd())
vim.opt.rtp:append(repo .. "/nvim")
local py = repo .. "/.venv/Scripts/python.exe"
local tmp = vim.fs.normalize(vim.fn.tempname()) .. "-c01"
local vault = tmp .. "/vault"
for _, d in ipairs({ "daily", "jira", "wiki", ".state" }) do
  vim.fn.mkdir(vault .. "/" .. d, "p")
end
vim.fn.writefile(vim.fn.readfile(repo .. "/config.yaml", "b"), vault .. "/config.yaml", "b")
vim.fn.writefile({ "environment: home", "jira:", "  project_keys: [KB]" }, vault .. "/config.local.yaml")
vim.fn.writefile({ "---", "date: 2026-11-05", "---", "## 기록", "- 09:00 KB-1 메모" }, vault .. "/daily/2026-11-05.md")

require("work-kb").setup({ root = repo, vault = vault, python = py, prefix = false, server = { enable = true, delay_ms = 0 } })
local server = require("work-kb.server")
local proc = require("work-kb.proc")
server.request_timeout_ms = 2000
server.start()
vim.wait(20000, function() return server.status().state == "ready" end, 50)
assert(server.status().state == "ready", "server did not start: " .. vim.inspect(server.status()))

-- another process holds .state/kb.lock for 6 s (like a scheduled `kb.py sync`)
local holder = vim.system({ py, "-X", "utf8", "-c",
  "import sys,time; sys.path.insert(0, r'" .. repo .. "/scripts'); from lib import core\n"
  .. "with core.kb_lock(r'" .. vault .. "', 'sync'):\n print('held', flush=True); time.sleep(6)" }, { text = true })
vim.wait(5000, function() return vim.uv.fs_stat(vault .. "/.state/kb.lock") ~= nil end, 50)
vim.wait(500)

local t0 = vim.uv.hrtime()
local h = proc.run(proc.kb_args("link"), { on_exit = function() end })
local code = proc.wait(h, 120000)
local secs = (vim.uv.hrtime() - t0) / 1e9
local st = server.status()
local h2 = proc.run(proc.kb_args("status"), { on_exit = function() end })
proc.wait(h2, 30000)
holder:wait()
print(string.format("link exit=%s via=%s after %.1fs; server state=%s last_error=%s; next status via=%s",
  tostring(code), tostring(h.via), secs, st.state, tostring(st.last_error), tostring(h2.via)))
server.stop(true)
vim.fn.delete(tmp, "rf")
if st.state == "dead" and h2.via == "process" then
  print("FAIL: server killed by request timeout while the command was only waiting for kb_lock; the command was"
    .. " re-run as one-shot and the server stays down (restart_interval_ms) — later fast requests pay process start")
  os.exit(1)
end
print("OK")
os.exit(0)
