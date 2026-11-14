dofile(vim.fs.dirname(debug.getinfo(1, "S").source:sub(2)) .. "/repro_common.lua")
local v = TMPVAULT()
W(v .. "/jira/KB-12.md", "---\nkey: KB-12\nstub: true\n---\n# KB-12\n")
require("work-kb").setup({ root = REPO, vault = v, python = "python", prefix = false, server = { enable = false } })
local spawned = {}
require("work-kb.proc")._spawn = function(spec) spawned[#spawned + 1] = spec.stdin; return { kill = function() end, alive = function() return true end, write = function() end, close_stdin = function() end } end
require("work-kb.ui").notify = function() end
local open = require("work-kb.open")
for _, text in ipairs({ "[[KB-12]]", "[[jira/KB-12]]", "[[KB-12.md]]" }) do
  spawned = {}; open._syncing = {}
  vim.cmd("enew!"); vim.bo.buftype = "nofile"
  vim.api.nvim_buf_set_lines(0, 0, -1, false, { text }); vim.api.nvim_win_set_cursor(0, { 1, 3 })
  local ok, r = pcall(open.open_at_point)
  if text ~= "[[KB-12]]" and not spawned[1] then BAD = true end
  P(text, "-> ok", ok, "spawned kb.py:", spawned[1] or "none", " now showing:", (vim.api.nvim_buf_get_name(0):match("jira/.*") or "(scratch)"))
end
VERDICT(BAD, "[[jira/KB-12]] / [[KB-12.md]] open the stub without sync --key (only [[KB-12]] syncs)")
