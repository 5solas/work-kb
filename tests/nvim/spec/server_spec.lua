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
    handle = proc().run(proc().sub_args("proposals", "list"), { on_exit = function() end })
    H.eq(handle.via, "server")
    proc().wait(handle, 10000)
    -- 느리거나 쓰는 명령은 서버로 보내지 않는다 (act transitions는 Jira를 부르므로 한 번 실행, P-01)
    handle = proc().run(proc().kb_args("compile"), { on_exit = function() end })
    H.eq(handle.via, "process")
    H.eq(proc().wait(handle, 10000), 9)
    handle = proc().run(proc().sub_args("proposals", "accept", "1"), { on_exit = function() end })
    H.eq(handle.via, "process")
    proc().wait(handle, 10000)
    handle = proc().run(proc().sub_args("act", "transitions", "KB-1"), { on_exit = function() end })
    H.eq(handle.via, "process")
    proc().wait(handle, 10000)
    H.eq(#fake.calls, 3)
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
    server().ensure()
    H.eq(server().status().starts, 2, "시간 초과로 죽인 서버는 1분 제한 없이 다시 띄운다 (P-01)")
  end)

  it("write command waiting on kb_lock is not rerun after timeout", function()
    -- C-01: 예약 작업(sync)이 kb_lock을 쥔 동안 서버의 link가 잠금을 기다리다 요청 시간 초과에 걸린다.
    -- 서버는 죽이지만 link(파일을 쓰는 명령)는 한 번 실행으로 다시 돌리지 않고 시간 초과로 끝낸다.
    local kb = H.read_bytes(H.repo .. "/scripts/kb.py") or ""
    if not (H.exists(H.repo .. "/scripts/serve.py") and kb:find('"serve"', 1, true)) then
      H.not_run("scripts/kb.py serve가 아직 없음")
    end
    local py = H.python()
    local vault = H.with_vault({ python = py, server = { enable = true, idle_sec = 120 } })
    H.copy_config(vault)
    H.write_bytes(vault .. "/config.local.yaml", "environment: home\njira:\n  project_keys: [KB]\n")
    H.write_bytes(vault .. "/daily/2026-11-05.md", "---\ndate: 2026-11-05\n---\n## 기록\n- 09:00 KB-1 메모\n")
    H.patch(server(), "request_timeout_ms", 1500)
    local fake = H.fake_spawn({ code = 0, output = "다시 실행됨\n" })
    start_ready()
    local held = false
    local script = "import sys, time\nsys.path.insert(0, r'"
      .. H.repo
      .. "/scripts')\nfrom lib import core\nwith core.kb_lock(r'"
      .. vault
      .. "', 'sync'):\n    print('held', flush=True)\n    time.sleep(5)\n"
    local holder = vim.system({ py, "-X", "utf8", "-c", script }, {
      text = true,
      stdout = function(_, data)
        if data and data:find("held", 1, true) then
          held = true
        end
      end,
    })
    H.ok(H.wait(function()
      return held
    end, 15000), "잠금을 쥐지 못함")
    local got
    local handle = proc().run(proc().kb_args("link"), {
      on_exit = function(code, _, reason)
        got = { code = code, reason = reason }
      end,
    })
    H.eq(handle.via, "server")
    H.ok(H.wait(function()
      return handle.done
    end, 15000), "link가 끝나지 않음")
    holder:wait(15000)
    H.eq(handle.code, nil, "시간 초과는 실패로 끝난다")
    H.eq(got, { reason = "timeout" })
    H.eq(#fake.calls, 0, "쓰기 명령은 한 번 실행으로 다시 돌리지 않는다")
    H.contains(H.buffer_text(), "시간 초과")
    H.eq(server().status().state, "dead")
    H.eq(server().status().last_error, "요청 시간 초과")
    server().ensure()
    H.eq(server().status().starts, 2, "1분 제한 없이 다시 띄운다")
  end)

  it("write timeout is reported to the caller", function()
    use_fake("ok", { request_timeout_ms = 300 })
    H.patch(server(), "_script_args", function(idle)
      return { "-X", "utf8", "tests/nvim/fake_serve.py", "--idle-sec", tostring(idle), "--sleep-on", "today,link" }
    end)
    local fake = H.fake_spawn({ code = 0, output = "다시 실행됨\n" })
    start_ready()
    local stub = H.ui_stub()
    -- l: misc.link
    local handle = require("work-kb.misc").link()
    H.eq(handle.via, "server")
    H.ok(H.wait(function()
      return handle.done
    end, 10000), "link가 끝나지 않음")
    H.eq(handle.code, nil)
    H.eq(#fake.calls, 0, "link를 다시 실행하지 않음")
    H.contains(H.buffer_text(), "시간 초과")
    H.eq(#stub.messages, 1, vim.inspect(stub.messages))
    H.contains(stub.messages[1], "시간 초과")
    -- daily.ensure_today: today가 시간 초과면 알리고, 뼈대를 만들어 계속한다
    H.ok(H.wait(function()
      server().ensure()
      return server().available()
    end, 20000), "다시 띄운 서버가 준비되지 않음")
    stub.messages = {}
    local daily = require("work-kb.daily")
    local file = daily.ensure_today(true)
    H.eq(#fake.calls, 0, "today를 다시 실행하지 않음")
    H.eq(#stub.messages, 1, vim.inspect(stub.messages))
    H.contains(stub.messages[1], "시간 초과")
    H.eq(H.read_bytes(file), daily.skeleton("2026-11-05"))
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

  it("응답 콜백 안에서 큐의 다음 요청을 proc.wait로 기다려도 멈추지 않는다 (agy 1)", function()
    use_fake("ok")
    start_ready()
    local p = proc()
    local inner, inner_ms
    local handle_b
    local handle_a = p.run(p.kb_args("search", "가"), {
      buffer = "*work-kb-a*",
      on_exit = function()
        local started = vim.uv.hrtime()
        inner = p.wait(handle_b, 3000)
        inner_ms = (vim.uv.hrtime() - started) / 1e6
      end,
    })
    handle_b = p.run(p.kb_args("status", "--fake-code", "4"), { buffer = "*work-kb-b*", on_exit = function() end })
    H.eq(handle_a.via, "server")
    H.eq(handle_b.via, "server")
    H.eq(server().status().queued, 2, "A는 보냈고 B는 큐에")
    H.ok(H.wait(function()
      return handle_a.done
    end, 10000), "A가 끝나지 않음")
    H.eq(inner, 4, "A의 콜백 안에서 B의 답을 받는다")
    H.ok(inner_ms < 2500, string.format("B를 기다리느라 %.0f ms 멈춤", inner_ms))
  end)

  it("UTF-8이 아닌 요청: id 없는 거부도 그 요청의 거부로 보고 서버는 살려 둔다 (agy 4)", function()
    local vault = use_fake("ok")
    local fake = H.fake_spawn({ code = 6, output = "한 번 실행\n" })
    start_ready()
    local pid = server().status().pid
    local code = proc().run_sync(proc().kb_args("search", "\255깨진"), 10000)
    H.eq(code, 6, "한 번 실행으로 돌림")
    H.eq(#fake.calls, 1)
    H.eq(fake.calls[1].stdin, '["search","--root",' .. proc().json_string(vault) .. ',"\255깨진"]')
    H.eq(server().status().state, "ready", vim.inspect(server().status()))
    H.eq(server().status().pid, pid)
    H.eq(server().status().last_error, nil)
    -- 다음 요청은 같은 서버로
    local handle = proc().run(proc().kb_args("status"), { on_exit = function() end })
    H.eq(handle.via, "server")
    H.eq(proc().wait(handle, 10000), 0)
  end)

  it("proc.cancel: 큐에서 기다리던 요청은 서버로 보내지 않는다 (agy 6)", function()
    use_fake("ok")
    start_ready()
    local p = proc()
    local a = p.run(p.kb_args("search", "a", "--fake-delay-ms", "400", "--fake-seq"), { buffer = "*work-kb-a*", on_exit = function() end })
    local b_called = false
    local b = p.run(p.kb_args("link", "--fake-seq"), {
      buffer = "*work-kb-b*",
      on_exit = function()
        b_called = true
      end,
    })
    H.eq(b.via, "server")
    p.cancel(b)
    H.eq(server().status().queued, 1, "B를 큐에서 뺐다")
    H.eq(p.wait(a, 10000), 0)
    H.contains(H.buffer_text("*work-kb-a*"), "seq=1")
    local c = p.run(p.kb_args("status", "--fake-seq"), { buffer = "*work-kb-c*", on_exit = function() end })
    H.eq(p.wait(c, 10000), 0)
    H.contains(H.buffer_text("*work-kb-c*"), "seq=2", "B는 서버에 가지 않았다")
    H.eq(b_called, false)
    -- 시간 초과로 기다리기를 그만둔 요청도 (run_sync → wait → cancel)
    local d = p.run(p.kb_args("search", "d", "--fake-delay-ms", "600", "--fake-seq"), { buffer = "*work-kb-d*", on_exit = function() end })
    H.eq(p.run_sync(p.kb_args("link", "--fake-seq"), 100), nil, "기다리기 시간 초과")
    H.eq(p.wait(d, 10000), 0)
    local e = p.run(p.kb_args("status", "--fake-seq"), { buffer = "*work-kb-e*", on_exit = function() end })
    H.eq(p.wait(e, 10000), 0)
    H.contains(H.buffer_text("*work-kb-e*"), "seq=4", "시간 초과로 거둔 link는 서버에 가지 않았다")
  end)

  it("환경(vim.env)이나 idle_sec이 바뀌면 옛 서버를 쓰지 않고 새로 띄운다 (agy 8·9)", function()
    use_fake("ok")
    start_ready()
    local p = proc()
    local root = require("work-kb.config").root()
    local python = p.python_command()
    H.eq(server().available(server().key(root, python)), true)
    local name = "WORK_KB_SPEC_ENV_" .. tostring(vim.uv.hrtime())
    vim.env[name] = "1"
    local ok, err = pcall(function()
      local handle = p.run(p.kb_args("status"), { on_exit = function() end })
      H.eq(handle.via, "process", "환경이 바뀐 뒤 첫 요청은 한 번 실행")
      p.wait(handle, 10000)
      H.eq(server().status().starts, 2, "1분 제한 없이 새로 띄움")
      H.ok(H.wait(function()
        return server().available(server().key(root, python))
      end, 20000), "새 서버가 준비되지 않음")
      H.contains(server().status().key.env, name .. "=1")
      handle = p.run(p.kb_args("status"), { on_exit = function() end })
      H.eq(handle.via, "server")
      p.wait(handle, 10000)
      -- idle_sec
      require("work-kb.config").get().server.idle_sec = 61
      H.eq(server().available(server().key(root, python)), false)
      H.eq(server().status().state, "stopped")
    end)
    vim.env[name] = nil
    H.ok(ok, tostring(err))
  end)

  it("유휴 종료(종료 0)는 오류가 아니고 다음 요청 때 바로 다시 띄운다 (agy 9)", function()
    use_fake("ok")
    require("work-kb.config").get().server.idle_sec = 1
    start_ready()
    H.ok(H.wait(function()
      return server().status().state ~= "ready"
    end, 10000), "1초 유휴 뒤 끝나지 않음")
    local st = server().status()
    H.eq(st.state, "stopped", vim.inspect(st))
    H.eq(st.last_error, nil)
    H.eq(st.last_exit, 0)
    server().ensure()
    H.eq(server().status().starts, 2, "1분 제한에 걸리지 않음")
    H.ok(H.wait(function()
      return server().available()
    end, 20000))
  end)

  it("exit 0 with queued request falls back and allows restart", function()
    -- A-03: 유휴 종료(종료 0) 찰나에 요청이 서버로 갔다. 그 요청은 한 번 실행으로 돌리고, 서버는
    -- 정상 종료(stopped)로 보아 1분 제한 없이 다시 띄운다.
    H.with_vault({ python = H.python(), server = { enable = true, idle_sec = 60 } })
    local p = proc()
    local fake = H.fake_spawn({ code = 0, output = "대체 실행\n" })
    local oneshot_spawn = p._spawn
    local servers = {}
    H.patch(p, "_spawn", function(spec)
      if not spec.server then
        return oneshot_spawn(spec)
      end
      table.insert(servers, spec)
      local function noop() end
      return {
        pid = 1,
        write = noop,
        close_stdin = noop,
        kill = noop,
        alive = function()
          return true
        end,
      }
    end)
    H.ok(server().start())
    servers[1].on_stdout('{"ready":true,"pid":99999,"version":1}\n')
    H.eq(server().status().state, "ready")
    local handle = p.run(p.kb_args("status"), { on_exit = function() end })
    H.eq(handle.via, "server")
    H.eq(server().status().queued, 1)
    servers[1].on_exit(0)
    H.eq(p.wait(handle, 5000), 0)
    H.eq(#fake.calls, 1, "한 번 실행으로 돌림")
    H.eq(H.buffer_text(), "대체 실행\n")
    local st = server().status()
    H.eq(st.state, "stopped", vim.inspect(st))
    H.eq(st.last_error, nil)
    H.eq(st.last_exit, 0)
    H.eq(st.queued, 0)
    H.eq(server().start(), true, "1분 제한 없이 다시 띄움")
    H.eq(server().status().starts, 2)
  end)

  it("handles a 5 MB response line", function()
    -- P-07 기능 시험: 큰 응답 한 줄이 여러 조각(글자 중간에서 잘림)으로 오고, 마지막 조각에 다음 응답까지
    -- 붙어 와도 그대로 맞춘다. 성능 항목이라 수정 전에도 통과할 수 있다(걸린 시간은 기록만).
    H.with_vault({ python = H.python(), server = { enable = true, idle_sec = 60 } })
    local p = proc()
    local servers = {}
    H.patch(p, "_spawn", function(spec)
      table.insert(servers, spec)
      local function noop() end
      return {
        pid = 1,
        write = noop,
        close_stdin = noop,
        kill = noop,
        alive = function()
          return true
        end,
      }
    end)
    H.ok(server().start())
    servers[1].on_stdout('{"ready":true,"pid":1,"version":1}\n')
    H.eq(server().status().state, "ready")
    local got = {}
    for _, argv in ipairs({ { "search", "큰" }, { "status" } }) do
      server().request(argv, function(code, out)
        table.insert(got, { code = code, out = out })
      end)
    end
    local unit = "한글 줄 😀\n"
    local big = string.rep(unit, math.ceil(5 * 1024 * 1024 / #unit))
    local data = vim.json.encode({ id = 1, code = 0, out = big }) .. '\n{"id":2,"code":3,"out":"둘째"}\n'
    H.ok(#data > 5 * 1024 * 1024)
    local size = 65536 + 7
    local started = vim.uv.hrtime()
    for i = 1, #data, size do
      servers[1].on_stdout(data:sub(i, i + size - 1))
    end
    print(string.format("server_spec: %d바이트 응답을 %d바이트 조각으로 %.0f ms", #data, size, (vim.uv.hrtime() - started) / 1e6))
    H.eq(#got, 2)
    H.eq(got[1].code, 0)
    H.ok(got[1].out == big, "큰 응답의 out이 다름 (" .. #tostring(got[1].out) .. "바이트)")
    H.eq(got[2], { code = 3, out = "둘째" })
    H.eq(server().status().state, "ready")
  end)

  it("죽인 서버의 실제 Python(준비 줄의 pid)이 남지 않는다: 시간 초과·stop(true) (agy 2)", function()
    -- WORK_KB_TEST_PYTHON은 pytest의 sys.executable (.venv/Scripts/python.exe: 실제 인터프리터를
    -- 자식으로 띄우는 venv 런처)이다. 런처의 pid와 준비 줄의 pid가 달라야 이 시험이 뜻이 있다.
    local function gone(pid)
      return H.wait(function()
        return vim.uv.kill(pid, 0) == nil
      end, 8000)
    end
    use_fake("ok", { request_timeout_ms = 300 })
    local launchers = {}
    local p = proc()
    local inner = p._spawn
    H.patch(p, "_spawn", function(spec)
      local h = inner(spec)
      if spec.server then
        table.insert(launchers, h.pid)
      end
      return h
    end)
    H.fake_spawn({ code = 0, output = "대체\n" })
    -- 1. 요청 시간 초과로 죽임 (요청을 처리하는 중이라 stdin을 읽지 않는 서버)
    start_ready()
    local real = server().status().pid
    H.eq(vim.uv.kill(real, 0), 0, "실제 서버가 떠 있음")
    print(string.format("server_spec: 런처 pid %s, 준비 줄 pid %d (%s)", tostring(launchers[1]), real, table.concat(p.python_command(), " ")))
    H.eq(p.run_sync(p.kb_args("search", "x", "--fake-hang"), 10000), 0)
    H.eq(server().status().last_error, "요청 시간 초과")
    H.ok(gone(real), string.format("시간 초과로 죽인 서버 pid %d가 남음 (런처 %s)", real, vim.inspect(launchers)))
    H.ok(gone(launchers[1]), "런처가 남음")
    -- 2. 요청 처리 중 stop(true) (VimLeavePre와 같은 길)
    server()._reset()
    start_ready()
    real = server().status().pid
    server().request({ "search", "--fake-hang" }, function() end)
    vim.wait(200)
    server().stop(true)
    H.ok(gone(real), string.format("stop(true) 뒤 서버 pid %d가 남음", real))
    H.ok(gone(launchers[2]), "런처가 남음")
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
    for _, args in ipairs({ { "status" }, { "search", "x" }, { "today" }, { "link" }, { "proposals", "list" }, { "act", "list" } }) do
      H.eq(p.served(args), true, table.concat(args, " "))
    end
    for _, args in ipairs({ { "compile" }, { "ingest" }, { "sync" }, { "proposals", "accept" }, { "act", "comment" }, { "act", "transitions", "KB-1" }, { "--help" }, { "init" }, {} }) do
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
    -- UTF-8이 아닌 인수: serve.py는 줄을 읽지 못해 id 없이 거부한다. 클라이언트는 서버를 살려 둔다.
    local pid = server().status().pid
    got = "pending"
    server().request({ "search", "--root", vault, "\255" }, function(code)
      got = code
    end)
    H.ok(H.wait(function()
      return got ~= "pending"
    end, 10000))
    H.eq(got, nil, "깨진 UTF-8은 거부")
    H.eq(server().status().state, "ready", vim.inspect(server().status()))
    H.eq(server().status().pid, pid)
  end)
end)
