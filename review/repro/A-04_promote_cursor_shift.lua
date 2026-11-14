-- A-04 재현: promote()가 확인 문구에 line을 캡처한 뒤, 확인 후 daily_line_ref()로
-- 현재 커서 줄을 다시 읽어 확인한 줄과 다른 줄 번호를 승격 인수로 넘긴다.
package.path = "nvim/lua/?.lua;nvim/lua/?/init.lua;" .. package.path

local wk = require("work-kb")
local jira = require("work-kb.jira")
local proc = require("work-kb.proc")
local ui = require("work-kb.ui")

local tmp = vim.fn.tempname()
vim.fn.mkdir(tmp .. "/daily", "p")
local daily = tmp .. "/daily/2026-11-15.md"
local f = assert(io.open(daily, "wb"))
f:write("## 기록\n- 09:00 첫째 줄\n- 09:05 둘째 줄\n")
f:close()

wk.setup({ root = vim.fs.normalize(vim.fn.getcwd()), vault = tmp, server = { enable = false } })
vim.cmd("edit " .. vim.fn.fnameescape(daily))
vim.api.nvim_win_set_cursor(0, { 2, 0 }) -- '- 09:00 첫째 줄'

jira.read_project = function()
  -- 사용자가 project 입력/대기 중 커서가 3번째 줄로 이동한 상황 시뮬레이션
  vim.api.nvim_win_set_cursor(0, { 3, 0 })
  return "KB"
end

local confirm_msg, sent_args
ui.confirm = function(msg)
  confirm_msg = msg
  return true
end
proc.run = function(args, _)
  sent_args = args
  return {}
end

jira.promote()
vim.cmd("bwipeout!")
vim.fn.delete(tmp, "rf")

print("confirm_msg=" .. tostring(confirm_msg))
print("sent_args=" .. table.concat(sent_args or {}, " "))
assert(table.concat(sent_args or {}, " "):find("2026-11-15.md:2", 1, true),
  "Expected line 2 (matching confirm_msg) to be promoted, got: " .. table.concat(sent_args or {}, " "))
