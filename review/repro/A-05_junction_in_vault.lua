-- A-05 재현: Windows 정션(또는 심볼릭 링크) 경로 아래 볼트 .md 파일을 열면 `in_vault()`가 false를 반환한다.
package.path = "nvim/lua/?.lua;nvim/lua/?/init.lua;" .. package.path

local uv = vim.uv
local wk = require("work-kb")
local root = vim.fs.normalize(vim.fn.getcwd())

local tmp = vim.fn.tempname()
local real_vault = tmp .. "/real_vault"
local link_vault = tmp .. "/link_vault"
vim.fn.mkdir(real_vault .. "/daily", "p")
local f = assert(io.open(real_vault .. "/daily/2026-11-15.md", "wb"))
f:write("# Test\n")
f:close()

local ok_link = false
local link_err = ""
if vim.fn.has("win32") == 1 then
  local res = vim.system({
    "powershell.exe", "-NoProfile", "-Command",
    "New-Item", "-ItemType", "Junction", "-Path", link_vault, "-Target", real_vault,
  }, { text = true }):wait()
  ok_link = (res.code == 0)
  link_err = (res.stdout or "") .. (res.stderr or "")
else
  ok_link = (uv.fs_symlink(real_vault, link_vault) == true)
end
assert(ok_link, "Failed to create junction/symlink for repro: " .. link_err)

vim.opt.rtp:prepend(root .. "/nvim")
vim.cmd("filetype plugin on")
wk.setup({ root = root, vault = real_vault, server = { enable = false } })
vim.cmd("edit " .. vim.fn.fnameescape(link_vault .. "/daily/2026-11-15.md"))
local bufnr = vim.api.nvim_get_current_buf()
local buf_path = vim.api.nvim_buf_get_name(bufnr)
local detected = (vim.b[bufnr].work_kb_vault == true)
print(string.format("vault=%s real_vault=%s buf_path=%s b:work_kb_vault=%s",
  tostring(require("work-kb.config").vault()), tostring(uv.fs_realpath(real_vault)), buf_path, tostring(vim.b[bufnr].work_kb_vault)))
vim.cmd("bwipeout!")
vim.fn.delete(link_vault)
vim.fn.delete(tmp, "rf")
assert(detected == true, "Expected buffer opened through junction path to set b:work_kb_vault = true")
