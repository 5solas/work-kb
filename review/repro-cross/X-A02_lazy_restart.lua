-- X-A02 반증: setup()이 서버를 바로 내리지 않아도, 다음 요청 때 server.available(key)가 열쇠(root·python·
-- idle_sec·환경) 차이를 보고 서버를 멈추고 새 설정으로 다시 띄운다. vault는 요청마다 --root로 넘기므로
-- 서버 상태가 아니다. 그래서 A-02가 말하는 "옛 설정으로 계속 실행"은 일어나지 않는다.
-- 실행: nvim --headless -u tests/nvim/minimal_init.lua -l review/repro-cross/X-A02_lazy_restart.lua
-- 종료 0 = 반증 성립(새 설정으로 다시 뜸), 1 = A-02가 맞음.
local repo = vim.fs.normalize(vim.fn.getcwd())
local py = repo .. "/.venv/Scripts/python.exe"
local function vault()
  local d = vim.fs.normalize(vim.fn.tempname())
  for _, s in ipairs({ "daily", ".state" }) do vim.fn.mkdir(d .. "/" .. s, "p") end
  vim.fn.writefile(vim.fn.readfile(repo .. "/config.yaml", "b"), d .. "/config.yaml", "b")
  vim.fn.writefile({ "environment: home", "jira:", "  project_keys: [KB]" }, d .. "/config.local.yaml")
  return d
end
local v1, v2 = vault(), vault()
local wk, server, proc = require("work-kb"), require("work-kb.server"), require("work-kb.proc")
local function run_status()
  local h = proc.run(proc.kb_args("status"), { on_exit = function() end })
  proc.wait(h, 60000)
  return h
end
wk.setup({ root = repo, vault = v1, python = py, prefix = false, server = { enable = true, idle_sec = 60, delay_ms = 0 } })
server.start()
vim.wait(20000, function() return server.status().state == "ready" end, 50)
local h0 = run_status()
local pid1, starts1 = server.status().pid, server.status().starts
print(string.format("before: via=%s pid=%s idle_sec=%s", h0.via, tostring(pid1), tostring(server.status().key.idle_sec)))

wk.setup({ root = repo, vault = v2, python = py, prefix = false, server = { enable = true, idle_sec = 10, delay_ms = 0 } })
local h1 = run_status() -- 열쇠가 달라 서버를 멈추고 한 번 실행, 동시에 새 서버를 띄운다
vim.wait(20000, function() return server.status().state == "ready" end, 50)
local h2 = run_status()
local st = server.status()
local out = proc.buffer_text(h2.bufnr) or ""
print(string.format("after:  first via=%s, second via=%s pid=%s idle_sec=%s starts=%d", h1.via, h2.via, tostring(st.pid), tostring(st.key.idle_sec), st.starts))
print("second status shows new vault: " .. tostring(out:find(v2:gsub("/", "\\"), 1, true) ~= nil or out:find(v2, 1, true) ~= nil))
server.stop(true)
vim.fn.delete(v1, "rf"); vim.fn.delete(v2, "rf")
local ok = h2.via == "server" and st.key.idle_sec == 10 and st.pid ~= pid1 and st.starts == starts1 + 1
print(ok and "OK: config change restarts the server lazily (A-02 refuted)" or "FAIL: server kept old config")
os.exit(ok and 0 or 1)
