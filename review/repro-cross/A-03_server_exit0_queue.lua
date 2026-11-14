-- A-03 재현: 유휴 종료(exit 0) 직전 요청이 S.inflight 또는 S.queue에 들어가면
-- server.lua가 정상 종료를 실패("dead")로 판정해 60초 쿨다운에 들어간다.
package.path = "nvim/lua/?.lua;nvim/lua/?/init.lua;" .. package.path

local wk = require("work-kb")
local srv = require("work-kb.server")
local proc = require("work-kb.proc")

local spawned_opts
local orig_spawn = proc._spawn
proc._spawn = function(opts)
  spawned_opts = opts
  return {
    alive = function() return true end,
    write = function() end,
    kill = function() end,
  }
end

local root = vim.fs.normalize(vim.fn.getcwd())
wk.setup({ root = root, vault = root .. "/examples/sample-vault", server = { enable = true, idle_sec = 60 } })

assert(srv.start(), "start failed")
spawned_opts.on_stdout('{"ready":true,"pid":99999,"version":1}\n')
vim.wait(1000, function() return srv.available() end, 10)
assert(srv.status().state == "ready")

local cb_code
assert(srv.request({ "status" }, function(code) cb_code = code end))
-- 서버가 idle 타임아웃 등으로 exit 0으로 종료한 찰나에 요청이 들어간 상황
spawned_opts.on_exit(0)
vim.wait(500, function() return cb_code ~= nil end, 10)
local st = srv.status()
local can_restart = srv.start()
proc._spawn = orig_spawn

print(string.format("state=%s cb_code=%s can_restart=%s last_error=%s",
  tostring(st.state), tostring(cb_code), tostring(can_restart), tostring(st.last_error)))
assert(st.state ~= "dead" and can_restart == true, "Expected code==0 idle exit not to enter 60s dead cooldown")
