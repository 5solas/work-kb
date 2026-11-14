dofile(vim.fs.dirname(debug.getinfo(1, "S").source:sub(2)) .. "/repro_common.lua")
require("work-kb").setup({ root = REPO, prefix = "<leader>k", server = { enable = true } })
require("work-kb.server") -- loaded (e.g. by search/status) but idle: initial state
P("server.status().state =", require("work-kb.server").status().state)
vim.cmd("checkhealth work-kb")
local lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)
for _, l in ipairs(lines) do if l:find("상주") or l:find("WARNING") or l:find("쓰지 못함") or l:find("ERROR") then P(l) end end
VERDICT(table.concat(lines, "\n"):find("WARNING", 1, true) ~= nil, "checkhealth warns for an idle (stopped) server")
