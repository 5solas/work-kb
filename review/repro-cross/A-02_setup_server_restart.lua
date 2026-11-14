-- A-02 재현: `server.enable = true`인 채 `vault`/`root`/`idle_sec`를 바꿔 `setup()`을 다시 불러도
-- 기존 서버를 내리지(`server.stop()`) 않는다.
package.path = "nvim/lua/?.lua;nvim/lua/?/init.lua;" .. package.path

local wk = require("work-kb")
local srv = require("work-kb.server")

local stop_calls = 0
local orig_stop = srv.stop
srv.stop = function(wait)
  stop_calls = stop_calls + 1
  return orig_stop(wait)
end

local root = vim.fs.normalize(vim.fn.getcwd())
local tmp1 = vim.fn.tempname()
local tmp2 = vim.fn.tempname()
vim.fn.mkdir(tmp1, "p")
vim.fn.mkdir(tmp2, "p")

wk.setup({ root = root, vault = tmp1, server = { enable = true, idle_sec = 60 } })
stop_calls = 0
wk.setup({ root = root, vault = tmp2, server = { enable = true, idle_sec = 10 } })

print(string.format("stop_calls=%d after vault/idle_sec changed with server.enable=true", stop_calls))
vim.fn.delete(tmp1, "rf")
vim.fn.delete(tmp2, "rf")
assert(stop_calls >= 1, "Expected server.stop() to be called when setup() changes vault/idle_sec")
