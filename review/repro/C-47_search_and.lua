dofile(vim.fs.dirname(debug.getinfo(1, "S").source:sub(2)) .. "/repro_common.lua")
local v = TMPVAULT()
vim.uv.fs_copyfile(REPO .. "/config.yaml", v .. "/config.yaml")
W(v .. "/config.local.yaml", "environment: home\n")
W(v .. "/daily/2026-11-05.md", "## 기록\n- 09:12 캐시 front matter 확정\n")
require("work-kb").setup({ root = REPO, vault = v, python = REPO .. "/.venv/Scripts/python.exe", prefix = false, server = { enable = false } })
local s = require("work-kb.search")
require("work-kb.ui").notify = function(m) P("  notify:", m) end
s._fzf = function() return nil end
for _, via in ipairs({ "rg", "kb" }) do
  s._rg = function() if via == "rg" then local x = vim.fn.exepath("rg"); return x ~= "" and x or nil end end
  local h = s.search("front 캐시")
  vim.wait(30000, function() return h.done end, 20)
  COUNTS[via] = #vim.fn.getqflist()
  P(via, "query 'front 캐시' -> code", h.code, "quickfix items", #vim.fn.getqflist())
  vim.fn.setqflist({}, "r")
end
VERDICT(COUNTS.rg ~= COUNTS.kb, "same query gives different results by backend; USER-GUIDE says spaces are AND")
