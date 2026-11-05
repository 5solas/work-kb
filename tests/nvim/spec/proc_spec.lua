-- kb.py 실행 (ERT work-kb-test-run-ascii-argv, -json-stdin-roundtrip, -real-kb-py,
-- -hangul-root-relative-python, -python-resolution, 그리고 보고 문구).
local H = require("helpers")

local function proc()
  return require("work-kb.proc")
end

---Python 찾기 이음매: PATH 폴더와 py -3 결과를 정한다. py -3을 실제로 돌리면 안 된다.
local function seams(dirs, launcher)
  local p = proc()
  H.patch(p, "_path_dirs", function()
    return dirs
  end)
  H.patch(p, "_py_launcher_works", function()
    if launcher == "error" then
      error("py -3를 시험하면 안 됨")
    end
    return launcher
  end)
end

describe("proc", function()
  it("Python 찾기: 직접 지정이 먼저 (py -3을 시험하지 않음)", function()
    local base = H.make_vault("python")
    seams({}, "error")
    H.configure({ root = base, python = { "py", "-3" } })
    H.eq(proc().python_command(), { "py", "-3" })
    H.configure({ root = base, python = "C:/Python/python.exe" })
    H.eq(proc().python_command(), { "C:/Python/python.exe" })
  end)

  it("Python 찾기: .venv가 있으면 상대 경로 (PATH python·py -3보다 먼저)", function()
    local base = H.make_vault("python")
    local store, bin = base .. "/WindowsApps", base .. "/bin"
    H.write_bytes(store .. "/python.exe", "")
    H.write_bytes(bin .. "/python.exe", "")
    H.write_bytes(base .. "/.venv/Scripts/python.exe", "")
    H.configure({ root = base })
    seams({ store, bin }, "error")
    H.eq(proc().python_command(), { ".venv/Scripts/python.exe" })
    seams({}, "error")
    H.eq(proc().python_command(), { ".venv/Scripts/python.exe" })
  end)

  it("Python 찾기: .venv가 없으면 PATH의 python, WindowsApps 별칭은 건너뜀", function()
    local base = H.make_vault("python")
    local store, bin = base .. "/WindowsApps", base .. "/bin"
    H.write_bytes(store .. "/python.exe", "")
    H.write_bytes(bin .. "/python.exe", "")
    H.configure({ root = base })
    seams({ store, bin }, "error")
    H.eq(proc().python_command(), { bin .. "/python.exe" })
    seams({ (store:gsub("WindowsApps", "windowsapps")), (bin:gsub("/", "\\")) }, "error")
    H.eq(proc().python_command(), { bin .. "/python.exe" }, "대소문자 무시, 역슬래시 PATH")
  end)

  it("Python 찾기: PATH에 별칭뿐이면 py -3, 그것도 안 되면 사용자 오류", function()
    local base = H.make_vault("python")
    local store = base .. "/WindowsApps"
    H.write_bytes(store .. "/python.exe", "")
    H.configure({ root = base })
    seams({ store }, true)
    H.eq(proc().python_command(), { "py", "-3" })
    seams({}, true)
    H.eq(proc().python_command(), { "py", "-3" })
    seams({}, false)
    local err = H.fails_with(function()
      proc().python_command()
    end, string.format("Python을 찾지 못했습니다. %s/.venv를 만들거나 setup()의 python을 설정하세요", base))
    H.ok(require("work-kb.ui").is_user_error(err))
  end)

  it("kb_args·sub_args: --root <볼트>의 자리", function()
    local vault = H.with_vault()
    H.eq(proc().kb_args("search", "질의", "--limit", "5"), { "search", "--root", vault, "질의", "--limit", "5" })
    H.eq(proc().kb_args("link"), { "link", "--root", vault })
    H.eq(proc().sub_args("act", "transitions", "KB-1"), { "act", "transitions", "--root", vault, "KB-1" })
    H.eq(proc().sub_args("proposals", "list"), { "proposals", "list", "--root", vault })
  end)

  it("명령줄은 ASCII만, 한글·이모지 인수는 stdin JSON으로 (D10)", function()
    local vault = H.with_vault({ python = H.python() })
    local fake = H.fake_spawn({ code = 0 })
    H.eq(proc().run_sync(proc().kb_args("search", "한글 😀 질의", 'a"b\\c\n')), 0)
    H.eq(#fake.calls, 1)
    local call = fake.calls[1]
    H.eq(vim.list_slice(call.cmd, #call.cmd - 3), { "-X", "utf8", "scripts/kb.py", "--argv-stdin" })
    for _, arg in ipairs(call.cmd) do
      H.ok(H.ascii(arg), "명령줄에 ASCII 밖 글자: " .. arg)
    end
    H.eq(call.cwd, H.repo)
    H.eq(call.stdin, '["search","--root","' .. vault .. '","한글 😀 질의","a\\"b\\\\c\\n"]')
    H.eq(call.argv, { "search", "--root", vault, "한글 😀 질의", 'a"b\\c\n' })
    H.eq(call.spec.merge, true, "stdout·stderr를 한 파이프로")
    H.eq(call.spec.env, { PYTHONUNBUFFERED = "1" }, "한 번 실행은 버퍼 없이 (서버와 같은 출력 순서)")
  end)

  it("환경: 지금 환경을 물려받고 얹은 값은 대소문자 무시로 바꾸고 false는 지운다", function()
    local p = proc()
    H.patch(vim.uv, "os_environ", function()
      return { Path = "C:\\bin", PythonUnbuffered = "0", PSModulePath = "x", HOME = "C:\\사용자" }
    end)
    H.eq(p.environ({ PYTHONUNBUFFERED = "1", PSMODULEPATH = false, NEW = "새 값" }), {
      "HOME=C:\\사용자",
      "NEW=새 값",
      "PYTHONUNBUFFERED=1",
      "Path=C:\\bin",
    })
    H.eq(p.environ(nil), { "HOME=C:\\사용자", "PSModulePath=x", "Path=C:\\bin", "PythonUnbuffered=0" })
  end)

  it("실제 _spawn의 env: 물려받은 환경 + 얹은 값, 지운 값은 자식에 없다", function()
    vim.env.WORK_KB_TEST_INHERITED = "물려받음 😀"
    vim.env.WORK_KB_TEST_DROPPED = "지울 값"
    local done, chunks = false, {}
    local ok, err = pcall(function()
      proc()._spawn({
        cmd = {
          H.python(),
          "-X",
          "utf8",
          "-c",
          "import os, json, sys; sys.stdout.buffer.write(json.dumps({k: os.environ.get(k) for k in ('WORK_KB_TEST_INHERITED', 'WORK_KB_TEST_DROPPED', 'PYTHONUNBUFFERED', 'SYSTEMROOT')}, ensure_ascii=False).encode('utf-8'))",
        },
        cwd = H.repo,
        env = { PYTHONUNBUFFERED = "1", WORK_KB_TEST_DROPPED = false },
        on_stdout = function(data)
          table.insert(chunks, data)
        end,
        on_exit = function()
          done = true
        end,
      })
      H.ok(H.wait(function()
        return done
      end, 30000), "자식이 끝나지 않음")
    end)
    vim.env.WORK_KB_TEST_INHERITED = nil
    vim.env.WORK_KB_TEST_DROPPED = nil
    H.ok(ok, tostring(err))
    local got = vim.json.decode(table.concat(chunks))
    H.eq(got.WORK_KB_TEST_INHERITED, "물려받음 😀")
    H.eq(got.WORK_KB_TEST_DROPPED, vim.NIL)
    H.eq(got.PYTHONUNBUFFERED, "1")
    H.ok(type(got.SYSTEMROOT) == "string" and got.SYSTEMROOT ~= "", "SYSTEMROOT를 물려받음")
  end)

  it("한 번 실행: flush 없는 print·stderr도 쓴 순서대로 한 버퍼에 (PYTHONUNBUFFERED)", function()
    H.configure({ python = H.python() })
    local code = table.concat({
      "import sys",
      "print('요약 1')",
      "print('건너뜀: a', file=sys.stderr)",
      "print('요약 2')",
      "print('건너뜀: b', file=sys.stderr)",
      "sys.exit(3)",
    }, "\n")
    H.rewrite_spawn(function(spec)
      return { spec.cmd[1], "-X", "utf8", "-c", code }
    end)
    local handle = proc().run({ "link" }, { buffer = " *work-kb-test-order*", on_exit = function() end })
    H.eq(handle.via, "process")
    H.eq(proc().wait(handle, 30000), 3)
    H.eq(H.buffer_text(" *work-kb-test-order*"), "요약 1\n건너뜀: a\n요약 2\n건너뜀: b\n")
    -- 대조: 버퍼를 쓰는 Python이면 stderr(줄 단위)가 먼저, stdout(블록 단위)은 끝날 때 온다.
    H.patch(proc(), "ONESHOT_ENV", { PYTHONUNBUFFERED = false })
    handle = proc().run({ "link" }, { buffer = " *work-kb-test-order*", on_exit = function() end })
    H.eq(proc().wait(handle, 30000), 3)
    H.eq(H.buffer_text(" *work-kb-test-order*"), "건너뜀: a\n건너뜀: b\n요약 1\n요약 2\n")
  end)

  it("실제 Python 왕복: 한글 😀이 그대로, CR 없음, 상태 문구 없음, stdout·stderr 도착 순서", function()
    H.configure({ python = H.python() })
    local code = table.concat({
      "import sys, json",
      "d = json.loads(sys.stdin.buffer.read().decode('utf-8'))",
      "sys.stdout.buffer.write(b'out1\\r\\n'); sys.stdout.buffer.flush()",
      "sys.stderr.buffer.write('오류 줄\\r\\n'.encode('utf-8')); sys.stderr.buffer.flush()",
      "sys.stdout.buffer.write(json.dumps(d, ensure_ascii=False).encode('utf-8') + b'\\r\\n')",
    }, "\n")
    H.rewrite_spawn(function(spec)
      return { spec.cmd[1], "-c", code }
    end)
    local handle = proc().run({ "echo", "한글 😀", "줄\n바꿈" }, { buffer = " *work-kb-test-echo*", on_exit = function() end })
    H.eq(proc().wait(handle, 30000), 0)
    local text = H.buffer_text(" *work-kb-test-echo*")
    H.not_contains(text, "\r")
    H.not_contains(text, "finished")
    H.not_contains(text, "Process")
    local lines = vim.split(text, "\n", { plain = true })
    H.eq(lines[1], "out1")
    H.eq(lines[2], "오류 줄")
    H.eq(vim.json.decode(lines[3]), { "echo", "한글 😀", "줄\n바꿈" })
    H.eq(lines[4], "")
  end)

  it("실제 scripts/kb.py --help (한 번 실행 경로)에 사용법", function()
    local auto = H.exists(H.repo .. "/.venv/Scripts/python.exe")
    H.configure({ python = (not auto) and H.python() or nil })
    local handle = proc().run({ "--help" }, { on_exit = function() end })
    H.eq(handle.via, "process")
    H.eq(proc().wait(handle, 60000), 0)
    H.contains(H.buffer_text(), "사용법")
    H.not_contains(H.buffer_text(), "\r")
  end)

  it("한글 루트에서 상대 경로 인터프리터와 scripts/kb.py가 돈다 (D03)", function()
    local python = H.python()
    local venv = vim.fs.dirname(vim.fs.dirname(python))
    if not (H.exists(python) and H.exists(venv .. "/pyvenv.cfg")) then
      H.skip("venv 인터프리터가 없음: " .. python)
    end
    local root = H.make_vault("한글 루트")
    H.copy_file(python, root .. "/.venv/Scripts/python.exe")
    H.copy_file(venv .. "/pyvenv.cfg", root .. "/.venv/pyvenv.cfg")
    H.write_bytes(
      root .. "/scripts/kb.py",
      table.concat({
        "import json, os, sys",
        "data = json.loads(sys.stdin.buffer.read().decode('utf-8'))",
        "out = {'argv': sys.argv[1:], 'stdin': data, 'cwd': os.getcwd(), 'utf8': sys.flags.utf8_mode}",
        "sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode('utf-8') + b'\\r\\n')",
        "",
      }, "\n")
    )
    H.configure({ root = root, vault = nil })
    H.eq(proc().python_command(), { ".venv/Scripts/python.exe" })
    local handle = proc().run(proc().kb_args("status"), { buffer = " *work-kb-test-root*", on_exit = function() end })
    H.eq(proc().wait(handle, 30000), 0)
    local result = vim.json.decode(vim.trim(H.buffer_text(" *work-kb-test-root*")))
    H.eq(result.argv, { "--argv-stdin" })
    H.eq(result.stdin, { "status", "--root", root })
    H.eq(vim.fs.normalize(result.cwd):lower(), root:lower())
    H.eq(result.utf8, 1)
  end)

  it("출력 버퍼: 실행 전에 비우고, append·header, 조각 경계의 CRLF", function()
    H.with_vault({ python = H.python() })
    H.fake_spawn({ code = 0, output = { "첫 줄\r", "\n둘째", " 줄\r\n" } })
    H.eq(proc().run_sync({ "link" }), 0)
    H.eq(H.buffer_text(), "첫 줄\n둘째 줄\n")
    -- 다시 실행하면 비운다
    H.eq(proc().run_sync({ "link" }), 0)
    H.eq(H.buffer_text(), "첫 줄\n둘째 줄\n")
    -- append + header: 줄 중간이면 줄을 바꾼 뒤 머리말
    local handle = proc().run({ "compile" }, { append = true, header = "\n== compile ==\n", on_exit = function() end })
    proc().wait(handle, 5000)
    H.eq(H.buffer_text(), "첫 줄\n둘째 줄\n\n== compile ==\n첫 줄\n둘째 줄\n")
    -- 홀로 있는 CR은 그대로 (Emacs utf-8-dos와 같음), 끝의 CR도 남는다
    H.fake_spawn({ code = 0, output = { "a\rb\r" } })
    H.eq(proc().run_sync({ "status" }), 0)
    H.eq(H.buffer_text(), "a\rb\r")
  end)

  it("last_line·exit_reason", function()
    local p = proc()
    H.eq(p.exit_reason(1), "종료 1: 오류")
    H.eq(p.exit_reason(2), "종료 2: 인증")
    H.eq(p.exit_reason(3), "종료 3: 네트워크")
    H.eq(p.exit_reason(4), "종료 4: 설정")
    H.eq(p.exit_reason(5), "종료 5: 공개 호스트 거부")
    H.eq(p.exit_reason(6), "종료 6: 잠김")
    H.eq(p.exit_reason(7), "종료 7: 정책 거부")
    H.eq(p.exit_reason(9), "종료 9: 알 수 없음")
    H.eq(p.exit_reason(nil), "종료 nil: 알 수 없음")
    local buf = p.get_buffer(" *work-kb-test-last*", true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "첫 줄", "  마지막 줄 \r", "   ", "" })
    H.eq(p.last_line(buf), "마지막 줄")
    H.eq(p.last_line(" *work-kb-test-last*"), "마지막 줄")
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "", " \t" })
    H.eq(p.last_line(buf), nil)
    H.eq(p.last_line(" *없는 버퍼*"), nil)
  end)

  it("기본 보고: 완료 — 마지막 줄 / 실패 (종료 N). *work-kb* 버퍼를 보세요", function()
    H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    H.fake_spawn({ code = 0, output = "진행\n요약: 3건\n" })
    local handle = proc().run({ "link" })
    proc().wait(handle, 5000)
    H.eq(stub.messages, { "work-kb: 완료 — 요약: 3건" })
    H.eq(#stub.shown, 0)
    H.fake_spawn({ code = 0, output = "" })
    handle = proc().run({ "link" })
    proc().wait(handle, 5000)
    H.eq(stub.messages[2], "work-kb: 완료")
    H.fake_spawn({ code = 4, output = "오류: 설정\n" })
    handle = proc().run({ "link" })
    proc().wait(handle, 5000)
    H.eq(stub.messages[3], "work-kb: 실패 (종료 4). *work-kb* 버퍼를 보세요")
    H.eq(stub.shown, { handle.bufnr })
    -- on_exit가 있으면 기본 보고는 없고, on_exit의 오류는 알림으로
    H.fake_spawn({ code = 0 })
    handle = proc().run({ "link" }, {
      on_exit = function()
        error("후처리 실패", 0)
      end,
    })
    proc().wait(handle, 5000)
    H.eq(stub.messages[4], "work-kb: 후처리 오류: 후처리 실패")
    H.eq(#stub.messages, 4)
  end)

  it("동기 실행 시간 초과: run_sync는 nil, run_output은 사용자 오류, 프로세스는 죽인다", function()
    H.with_vault({ python = H.python() })
    local fake = H.fake_spawn({ code = "hang" })
    H.eq(proc().run_sync({ "compile" }, 50), nil)
    H.eq(fake.killed, 1)
    H.fails_with(function()
      proc().run_output({ "compile" }, { buffer = " *work-kb-test-out*", timeout_ms = 1000 })
    end, "work-kb: compile 응답이 없습니다 (1초)")
    H.eq(fake.killed, 2)
    H.eq(proc().default_timeout_ms, 120000)
  end)

  it("run_output: 실제 비동기 프로세스가 끝날 때까지 기다려 코드와 출력", function()
    local vault = H.with_vault({ python = H.python() })
    local code = table.concat({
      "import sys, json",
      "a = json.loads(sys.stdin.buffer.read().decode('utf-8'))",
      "sys.stdout.buffer.write(('log\\n' + json.dumps([{'name': a[-1]}], ensure_ascii=False) + '\\n').encode('utf-8'))",
      "sys.exit(3)",
    }, "\n")
    H.rewrite_spawn(function(spec)
      return { spec.cmd[1], "-c", code }
    end)
    local exit, text = proc().run_output(proc().sub_args("act", "transitions", "KB-1", "완료"), { buffer = " *work-kb-test-out*", timeout_ms = 30000 })
    H.eq(exit, 3)
    H.eq(text, 'log\n[{"name": "완료"}]\n')
    H.ok(vault)
  end)

  it("ARGS가 문자열 목록이 아니면 오류", function()
    H.with_vault({ python = H.python() })
    H.fake_spawn({ code = 0 })
    H.fails_with(function()
      proc().run({ "link", 3 })
    end)
    H.fails_with(function()
      proc().run("link")
    end)
  end)
end)
