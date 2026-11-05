-- 상주 서버 클라이언트 (계약 A). 가짜 서버 tests/nvim/fake_serve.py로 시험하고,
-- scripts/kb.py에 serve가 있으면 실제 서버와 한 번 실행의 결과가 같은지도 본다.
local H = require("helpers")

local function server()
  return require("work-kb.server")
end

local function proc()
  return require("work-kb.proc")
end

---가짜 서버를 쓰도록 설정한다. MODE: ok | bad-ready | no-ready | garbage.
local function use_fake(mode, opts)
  local vault = H.with_vault({ python = H.python(), server = { enable = true, idle_sec = 60 } })
  H.patch(server(), "_script_args", function(idle)
    return { "-X", "utf8", "tests/nvim/fake_serve.py", "--idle-sec", tostring(idle), "--mode", mode or "ok" }
  end)
  for k, v in pairs(opts or {}) do
    H.patch(server(), k, v)
  end
  return vault
end

local function start_ready()
  H.ok(server().start(), "start")
  H.ok(H.wait(function()
    return server().available()
  end, 20000), "서버가 준비되지 않음: " .. vim.inspect(server().status()))
end

describe("server", function()
  it("준비 줄을 읽으면 available, pid는 서버가 알려 준 값", function()
    use_fake("ok")
    H.eq(server().available(), false)
    start_ready()
    local st = server().status()
    H.eq(st.state, "ready")
    H.eq(type(st.pid), "number")
    H.ok(st.pid > 0)
    H.eq(server().start(), true, "이미 떠 있으면 그대로")
    H.eq(server().status().starts, 1)
  end)

  it("요청은 차례로 보내고 응답은 id로 맞춘다", function()
    use_fake("ok")
    start_ready()
    local results = {}
    for i = 1, 3 do
      server().request({ "search", "--root", "V", "질의 " .. i, "--fake-code", tostring(i) }, function(code, out)
        table.insert(results, { i = i, code = code, out = out })
      end)
    end
    H.ok(H.wait(function()
      return #results == 3
    end, 10000), "응답 3개가 오지 않음")
    for i = 1, 3 do
      H.eq(results[i].i, i)
      H.eq(results[i].code, i)
      H.contains(results[i].out, '"질의 ' .. i .. '"')
    end
  end)

  it("proc.run: 빠른 명령은 서버로, 결과는 버퍼·종료 코드로 (한 번 실행과 같은 꼴)", function()
    local vault = use_fake("ok")
    local fake = H.fake_spawn({ code = 9, output = "한 번 실행\n" })
    start_ready()
    local stub = H.ui_stub()
    local handle = proc().run(proc().kb_args("status"))
    H.eq(handle.via, "server")
    H.eq(proc().wait(handle, 10000), 0)
    local pid = server().status().pid
    H.eq(H.buffer_text(), string.format('served ["status", "--root", "%s"]\npid=%d\n', vault, pid))
    H.eq(#fake.calls, 0, "한 번 실행을 띄우지 않음")
    H.eq(stub.messages, { "work-kb: 완료 — pid=" .. pid })
    -- 하위 명령도
    handle = proc().run(proc().sub_args("act", "transitions", "KB-1"), { on_exit = function() end })
    H.eq(handle.via, "server")
    proc().wait(handle, 10000)
    handle = proc().run(proc().sub_args("proposals", "list"), { on_exit = function() end })
    H.eq(handle.via, "server")
    proc().wait(handle, 10000)
    -- 느리거나 쓰는 명령은 서버로 보내지 않는다
    handle = proc().run(proc().kb_args("compile"), { on_exit = function() end })
    H.eq(handle.via, "process")
    H.eq(proc().wait(handle, 10000), 9)
    handle = proc().run(proc().sub_args("proposals", "accept", "1"), { on_exit = function() end })
    H.eq(handle.via, "process")
    proc().wait(handle, 10000)
    H.eq(#fake.calls, 2)
  end)

  it("거부되면 한 번 실행으로 돌린다 (출력·코드는 한 번 실행의 것)", function()
    local vault = use_fake("ok")
    local fake = H.fake_spawn({ code = 2, output = "한 번 실행 출력\r\n" })
    start_ready()
    local code = proc().run_sync(proc().kb_args("status", "--fake-refuse"), 10000)
    H.eq(code, 2)
    H.eq(#fake.calls, 1)
    H.eq(fake.calls[1].argv, { "status", "--root", vault, "--fake-refuse" })
    H.eq(H.buffer_text(), "한 번 실행 출력\n")
    H.eq(server().available(), true, "거부는 서버를 죽이지 않는다")
  end)

  it("서버가 죽으면 한 번 실행으로 돌리고 서버는 쓰지 않는다", function()
    use_fake("ok")
    local fake = H.fake_spawn({ code = 0, output = "대체 실행\n" })
    start_ready()
    local code = proc().run_sync(proc().kb_args("link", "--fake-die"), 10000)
    H.eq(code, 0)
    H.eq(#fake.calls, 1)
    H.eq(H.buffer_text(), "대체 실행\n")
    H.ok(H.wait(function()
      return server().status().state == "dead"
    end, 5000))
    H.eq(server().available(), false)
    -- 1분이 안 지났으니 다음 요청도 한 번 실행으로 (다시 띄우지 않음)
    local handle = proc().run(proc().kb_args("status"), { on_exit = function() end })
    H.eq(handle.via, "process")
    proc().wait(handle, 5000)
    H.eq(server().status().starts, 1)
  end)

  it("다시 띄우기는 1분에 한 번까지", function()
    local clock = 1000000
    use_fake("ok", {
      _now_ms = function()
        return clock
      end,
    })
    H.fake_spawn({ code = 0 })
    start_ready()
    proc().run_sync(proc().kb_args("link", "--fake-die"), 10000)
    H.ok(H.wait(function()
      return server().status().state == "dead"
    end, 5000))
    clock = clock + 30000
    server().ensure()
    H.eq(server().status().starts, 1, "30초 뒤에는 띄우지 않음")
    H.eq(server().start(), false)
    clock = clock + 31000
    server().ensure()
    H.eq(server().status().starts, 2, "1분이 지나면 다시 띄움")
    H.ok(H.wait(function()
      return server().available()
    end, 20000), "다시 띄운 서버가 준비되지 않음")
    -- 다시 띄운 서버로 요청이 간다
    local handle = proc().run(proc().kb_args("status"), { on_exit = function() end })
    H.eq(handle.via, "server")
    H.eq(proc().wait(handle, 10000), 0)
  end)

  it("요청 시간 초과: 서버를 죽이고 한 번 실행으로", function()
    use_fake("ok", { request_timeout_ms = 300 })
    local fake = H.fake_spawn({ code = 0, output = "대체\n" })
    start_ready()
    local code = proc().run_sync(proc().kb_args("search", "x", "--fake-sleep"), 10000)
    H.eq(code, 0)
    H.eq(#fake.calls, 1)
    H.eq(H.buffer_text(), "대체\n")
    H.eq(server().status().state, "dead")
    H.eq(server().status().last_error, "요청 시간 초과")
  end)

  it("준비 줄이 틀리거나 JSON이 아니거나 오지 않으면 쓰지 않는다", function()
    for _, case in ipairs({ { "bad-ready", "준비 줄이 계약과 다릅니다" }, { "garbage", "서버 출력이 JSON이 아닙니다" }, { "no-ready", "준비 줄이 오지 않았습니다" } }) do
      server()._reset()
      use_fake(case[1], { ready_timeout_ms = 500 })
      local fake = H.fake_spawn({ code = 0, output = "대체\n" })
      H.ok(server().start())
      H.ok(H.wait(function()
        return server().status().state == "dead"
      end, 10000), case[1] .. ": " .. vim.inspect(server().status()))
      H.contains(server().status().last_error, case[2])
      local handle = proc().run(proc().kb_args("status"), { on_exit = function() end })
      H.eq(handle.via, "process", case[1])
      proc().wait(handle, 5000)
      H.eq(#fake.calls, 1)
    end
  end)

  it("설정(루트·Python)이 바뀌면 옛 서버를 쓰지 않는다", function()
    use_fake("ok")
    start_ready()
    local key = server().status().key
    H.eq(server().available(key), true)
    H.eq(server().available({ root = key.root, python = { "다른", "python" } }), false)
    H.eq(server().status().state, "stopped")
  end)

  it("서버를 끄면 서버 모듈을 거치지 않는다", function()
    H.with_vault({ python = H.python(), server = { enable = false } })
    local fake = H.fake_spawn({ code = 0 })
    local handle = proc().run(proc().kb_args("status"), { on_exit = function() end })
    H.eq(handle.via, "process")
    proc().wait(handle, 5000)
    H.eq(#fake.calls, 1)
    H.eq(server().start(), false)
  end)

  it("빠른 명령 표", function()
    local p = proc()
    for _, args in ipairs({ { "status" }, { "search", "x" }, { "today" }, { "link" }, { "proposals", "list" }, { "act", "list" }, { "act", "transitions", "KB-1" } }) do
      H.eq(p.served(args), true, table.concat(args, " "))
    end
    for _, args in ipairs({ { "compile" }, { "ingest" }, { "sync" }, { "proposals", "accept" }, { "act", "comment" }, { "--help" }, { "init" }, {} }) do
      H.eq(p.served(args), false, table.concat(args, " "))
    end
  end)

  it("실제 kb.py serve: 서버와 한 번 실행의 종료 코드·출력이 같다", function()
    local kb = H.read_bytes(H.repo .. "/scripts/kb.py") or ""
    if not (H.exists(H.repo .. "/scripts/serve.py") and kb:find('"serve"', 1, true)) then
      H.not_run("scripts/kb.py serve가 아직 없음 (scripts/serve.py 또는 COMMANDS의 serve)")
    end
    local auto = H.exists(H.repo .. "/.venv/Scripts/python.exe")
    local vault = H.with_vault({ python = (not auto) and H.python() or nil, server = { enable = true, idle_sec = 120 } })
    H.copy_config(vault)
    H.write_bytes(vault .. "/daily/2026-11-05.md", "---\ndate: 2026-11-05\n---\n## 기록\n- 09:00 한글 회의 [[KB-12]] @김철수\n")
    start_ready()
    local cases = {
      proc().kb_args("status"),
      proc().kb_args("search", "한글"),
      proc().kb_args("search", "없는말😀"),
      proc().sub_args("proposals", "list"),
      proc().sub_args("act", "list"),
    }
    local results = {}
    for _, args in ipairs(cases) do
      local handle = proc().run(args, { on_exit = function() end })
      H.eq(handle.via, "server", table.concat(args, " "))
      local code = proc().wait(handle, 60000)
      table.insert(results, { code = code, text = H.buffer_text() })
    end
    H.eq(results[1].code, 0, "status: " .. results[1].text)
    H.eq(results[2].code, 0, "search: " .. results[2].text)
    H.contains(results[2].text, "한글 회의")
    require("work-kb.config").get().server.enable = false
    for i, args in ipairs(cases) do
      local handle = proc().run(args, { on_exit = function() end })
      H.eq(handle.via, "process")
      local code = proc().wait(handle, 60000)
      H.eq(results[i].code, code, table.concat(args, " ") .. " 종료 코드")
      H.eq(results[i].text, H.buffer_text(), table.concat(args, " ") .. " 출력")
    end
    -- 서버가 받지 않는 명령은 refused (클라이언트는 보내지 않지만 서버도 거부해야 한다)
    require("work-kb.config").get().server.enable = true
    local got = "pending"
    server().request({ "compile", "--root", vault }, function(code)
      got = code
    end)
    H.ok(H.wait(function()
      return got ~= "pending"
    end, 10000))
    H.eq(got, nil, "compile은 거부")
  end)
end)
