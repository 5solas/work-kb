dofile(vim.fs.dirname(debug.getinfo(1, "S").source:sub(2)) .. "/repro_common.lua")
vim.opt.rtp:append(REPO .. "/nvim/after")
local base = vim.fs.normalize(vim.fn.tempname())
local v = base .. "/Jörg Notes"
vim.fn.mkdir(v .. "/daily", "p")
W(v .. "/daily/2026-11-05.md", "## 기록\n")
require("work-kb").setup({ root = REPO, vault = v, prefix = false, server = { enable = false } })
local upper = base .. "/JÖRG NOTES/daily/2026-11-05.md"   -- same file on NTFS (case-insensitive incl. non-ASCII)
local mixed = base .. "/JöRG NOTES/daily/2026-11-05.md"   -- only ASCII letters changed case
P("same file on disk:", vim.uv.fs_stat(upper) ~= nil)
P("jira.daily_relative(JÖRG NOTES/...) ->", require("work-kb.jira").daily_relative(upper))
P("jira.daily_relative(JöRG NOTES/...) ->", require("work-kb.jira").daily_relative(mixed))
local target = vim.env.WHICH == "upper" and upper or mixed
vim.cmd("edit " .. vim.fn.fnameescape(target))
vim.cmd("doautocmd FileType markdown")
P("edit", vim.env.WHICH, "-> vault ftplugin applied:", vim.b.work_kb_vault == true, " shiftwidth:", vim.bo.shiftwidth, " local autoread:", vim.api.nvim_get_option_value("autoread", { scope = "local" }))
VERDICT(require("work-kb.jira").daily_relative(upper) == nil, "a vault path that differs only in non-ASCII letter case is not recognised")
