-- kb.py 실행: Python을 부르는 유일한 입구 (D10, D03).
-- 명령줄은 `<python> -X utf8 scripts/kb.py --argv-stdin` (ASCII만), 실제 인수는 stdin의 UTF-8 JSON 배열.
-- 작업 폴더는 코드 루트. stdout과 stderr는 한 파이프로 합쳐 도착 순서대로 버퍼에 넣고, CRLF는 LF로.
-- 상주 서버(work-kb.server)가 떠 있으면 빠른 읽기 명령은 서버로 보내고, 안 되면 한 번 실행으로 돌린다.

local M = {}

local uv = vim.uv

M.OUTPUT = "*work-kb*"
M.default_timeout_ms = 120000 -- work-kb-process-timeout 120초
M.SCRIPT = { "-X", "utf8", "scripts/kb.py", "--argv-stdin" }

local function config()
  return require("work-kb.config")
end

local function ui()
  return require("work-kb.ui")
end

---------------------------------------------------------------- 종료 코드

local EXIT_REASONS = {
  [1] = "오류",
  [2] = "인증",
  [3] = "네트워크",
  [4] = "설정",
  [5] = "공개 호스트 거부",
  [6] = "잠김",
  [7] = "정책 거부",
}

---"종료 N: 이유" (core.KbError 하위 클래스의 뜻).
function M.exit_reason(code)
  return string.format("종료 %s: %s", tostring(code), EXIT_REASONS[code] or "알 수 없음")
end

---------------------------------------------------------------- 인수

---kb.py 인수 목록: CMD --root <볼트> ...
function M.kb_args(cmd, ...)
  local args = { cmd, "--root", config().vault() }
  for _, a in ipairs({ ... }) do
    table.insert(args, a)
  end
  return args
end

---kb.py 인수 목록: CMD SUB --root <볼트> ... (kb.py가 argv[1]로 `act list` 등을 가른다).
function M.sub_args(cmd, sub, ...)
  local args = { cmd, sub, "--root", config().vault() }
  for _, a in ipairs({ ... }) do
    table.insert(args, a)
  end
  return args
end

local JSON_ESCAPES = {
  ['"'] = '\\"',
  ["\\"] = "\\\\",
  ["\b"] = "\\b",
  ["\f"] = "\\f",
  ["\n"] = "\\n",
  ["\r"] = "\\r",
  ["\t"] = "\\t",
}

---JSON 문자열. 한글 등 ASCII 밖 글자는 날 UTF-8 그대로 (\u 이스케이프 없음), `/`도 그대로.
function M.json_string(s)
  return '"'
    .. s:gsub('[%z\1-\31\127"\\]', function(c)
      return JSON_ESCAPES[c] or string.format("\\u%04x", c:byte())
    end)
    .. '"'
end

---문자열 배열의 JSON (공백 없음): ["a","b"].
function M.json_array(list)
  local parts = {}
  for i, s in ipairs(list) do
    parts[i] = M.json_string(s)
  end
  return "[" .. table.concat(parts, ",") .. "]"
end

---------------------------------------------------------------- Python 찾기 (D03)

local function exists(path)
  return uv.fs_stat(path) ~= nil
end

local function is_file(path)
  local st = uv.fs_stat(path)
  return st ~= nil and st.type ~= "directory"
end

local function join(dir, name)
  if dir:sub(-1) == "/" or dir:sub(-1) == "\\" then
    return dir .. name
  end
  return dir .. "/" .. name
end

---PATH의 폴더 목록 (이음매).
function M._path_dirs()
  local dirs = {}
  for dir in (vim.env.PATH or ""):gmatch("[^;]+") do
    dir = dir:gsub('^"', ""):gsub('"$', "")
    if dir ~= "" then
      table.insert(dirs, dir)
    end
  end
  return dirs
end

local PATH_SUFFIXES = { ".exe", ".com", ".bat", ".cmd" }

---PATH의 python (WindowsApps 별칭 폴더는 건너뜀). 찾으면 절대 경로(슬래시), 없으면 nil.
function M._find_python_on_path()
  for _, dir in ipairs(M._path_dirs()) do
    if not dir:lower():find("windowsapps", 1, true) then
      for _, suffix in ipairs(PATH_SUFFIXES) do
        local candidate = join(dir, "python" .. suffix)
        if is_file(candidate) then
          return vim.fs.normalize(candidate)
        end
      end
    end
  end
  return nil
end

M.PY_PROBE = { "py", "-3", "-c", "pass" }
M.py_probe_timeout_ms = 3000 -- 사용자 명령에서 동기로 기다리는 최대 시간 (보통 0.1초 안팎)

-- `py -3` 시험 결과 (nil = 아직, true/false). setup()이 다시 불리면 지운다.
M._py_cache = nil

---`py -3 -c pass`가 0으로 끝나는가 (동기, 최대 py_probe_timeout_ms; 이음매).
function M._py_launcher_works()
  if vim.fn.executable("py") ~= 1 then
    return false
  end
  local ok, result = pcall(function()
    return vim.system(M.PY_PROBE, { text = true }):wait(M.py_probe_timeout_ms)
  end)
  return ok and result ~= nil and result.code == 0
end

---`py -3 -c pass`를 비동기로 시험하고 CB(ok)를 메인 루프에서 부른다 (이음매). 기다리지 않는다.
function M._py_probe_async(cb)
  if vim.fn.executable("py") ~= 1 then
    vim.schedule(function()
      cb(false)
    end)
    return
  end
  local ok = pcall(vim.system, M.PY_PROBE, { text = true, timeout = 15000 }, function(result)
    vim.schedule(function()
      cb(result ~= nil and result.code == 0)
    end)
  end)
  if not ok then
    vim.schedule(function()
      cb(false)
    end)
  end
end

---`py -3` 결과를 비동기로 정해 캐시에 넣고 CB(ok)를 부른다 (이미 정했으면 바로).
function M.probe_py_launcher(cb)
  if M._py_cache ~= nil then
    local cached = M._py_cache
    vim.schedule(function()
      cb(cached)
    end)
    return
  end
  M._py_probe_async(function(ok)
    M._py_cache = ok == true
    cb(M._py_cache)
  end)
end

---Python 명령 목록. 직접 지정 → <루트>/.venv/Scripts/python.exe(상대 경로) → PATH의 python → py -3.
---OPTS.probe == false(배경 준비 길: setup 뒤 서버 띄우기)면 `py -3`을 동기로 시험하지 않는다: 이미 시험한
---결과만 쓰고, 정하지 못하면 사용자 오류 대신 nil (부른 쪽이 probe_py_launcher로 비동기로 정한다).
---@param opts? {probe?: boolean}
---@return string[]|nil
function M.python_command(opts)
  local probe = not (opts and opts.probe == false)
  local python = config().get().python
  if type(python) == "string" and python ~= "" then
    return { python }
  elseif type(python) == "table" and #python > 0 then
    return vim.list_extend({}, python)
  end
  local root = config().root()
  if exists(join(root, ".venv/Scripts/python.exe")) then
    return { ".venv/Scripts/python.exe" }
  end
  local found = M._find_python_on_path()
  if found then
    return { found }
  end
  if M._py_cache ~= true and probe then
    -- 사용자 명령: 짧게(최대 py_probe_timeout_ms) 동기로 시험한다. 실패는 캐시하지 않는다 (나중에 설치할 수 있다).
    if M._py_launcher_works() then
      M._py_cache = true
    end
  end
  if M._py_cache == true then
    return { "py", "-3" }
  end
  if not probe then
    return nil
  end
  local dir = root:sub(-1) == "/" and root or (root .. "/")
  ui().fail(string.format("Python을 찾지 못했습니다. %s.venv를 만들거나 setup()의 python을 설정하세요", dir))
end

---------------------------------------------------------------- 출력 버퍼

M._buffers = {} -- 이름 → bufnr
local buffer_names = {} -- bufnr → 이름

local function find_buffer(name)
  local bufnr = M._buffers[name]
  if bufnr and vim.api.nvim_buf_is_valid(bufnr) then
    return bufnr
  end
  M._buffers[name] = nil
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    local full = vim.api.nvim_buf_get_name(b)
    if full == name or full:sub(-(#name + 1)) == "\\" .. name or full:sub(-(#name + 1)) == "/" .. name then
      M._buffers[name] = b
      buffer_names[b] = name
      return b
    end
  end
  return nil
end

---이름(기본 *work-kb*) 또는 번호로 출력 버퍼를 찾는다. CREATE면 없을 때 만든다 (nofile, 목록 밖).
---@param name_or_nr string|integer|nil
---@param create? boolean
---@return integer|nil
function M.get_buffer(name_or_nr, create)
  if type(name_or_nr) == "number" then
    if vim.api.nvim_buf_is_valid(name_or_nr) then
      return name_or_nr
    end
    return nil
  end
  local name = name_or_nr or M.OUTPUT
  local bufnr = find_buffer(name)
  if bufnr or not create then
    return bufnr
  end
  bufnr = vim.api.nvim_create_buf(false, true)
  vim.bo[bufnr].bufhidden = "hide"
  vim.bo[bufnr].swapfile = false
  pcall(vim.api.nvim_buf_set_name, bufnr, name)
  M._buffers[name] = bufnr
  buffer_names[bufnr] = name
  return bufnr
end

---버퍼 이름 (메시지용): get_buffer로 만든 버퍼면 그 이름, 아니면 nvim 버퍼 이름.
function M.buffer_name(bufnr)
  if not (bufnr and vim.api.nvim_buf_is_valid(bufnr)) then
    return ""
  end
  return buffer_names[bufnr] or vim.api.nvim_buf_get_name(bufnr)
end

---버퍼 전체 글 (줄을 \n으로 이음; Emacs buffer-string).
function M.buffer_text(buf)
  local bufnr = M.get_buffer(buf, false)
  if not bufnr then
    return nil
  end
  return table.concat(vim.api.nvim_buf_get_lines(bufnr, 0, -1, false), "\n")
end

local function with_modifiable(bufnr, fn)
  local was = vim.bo[bufnr].modifiable
  if not was then
    vim.bo[bufnr].modifiable = true
  end
  local ok, err = pcall(fn)
  if not was then
    vim.bo[bufnr].modifiable = false
  end
  if not ok then
    error(err, 0)
  end
end

---버퍼 끝에 TEXT를 잇는다. 끝을 보고 있던 창은 따라간다.
function M._append(bufnr, text)
  if text == nil or text == "" or not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  local count = vim.api.nvim_buf_line_count(bufnr)
  local last = vim.api.nvim_buf_get_lines(bufnr, count - 1, count, false)[1] or ""
  local follow = {}
  for _, win in ipairs(vim.fn.win_findbuf(bufnr)) do
    if vim.api.nvim_win_get_cursor(win)[1] == count then
      table.insert(follow, win)
    end
  end
  with_modifiable(bufnr, function()
    vim.api.nvim_buf_set_text(bufnr, count - 1, #last, count - 1, #last, vim.split(text, "\n", { plain = true }))
  end)
  local new_count = vim.api.nvim_buf_line_count(bufnr)
  for _, win in ipairs(follow) do
    pcall(vim.api.nvim_win_set_cursor, win, { new_count, 0 })
  end
end

local function erase(bufnr)
  with_modifiable(bufnr, function()
    vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {})
  end)
end

---출력 조각을 버퍼에 넣는 쓰기 도구. CRLF는 LF로 (조각 끝의 CR은 다음 조각을 보고 정한다).
local function writer(bufnr)
  local pending_cr = false
  local w = {}
  function w.write(data)
    if data == nil or data == "" then
      return
    end
    if pending_cr then
      data = "\r" .. data
      pending_cr = false
    end
    if data:sub(-1) == "\r" then
      pending_cr = true
      data = data:sub(1, -2)
    end
    M._append(bufnr, (data:gsub("\r\n", "\n")))
  end
  function w.flush()
    if pending_cr then
      pending_cr = false
      M._append(bufnr, "\r")
    end
  end
  return w
end

---버퍼의 마지막 비어 있지 않은 줄 (앞뒤 공백·탭·CR 제거). 없으면 nil.
function M.last_line(buf)
  local text = M.buffer_text(buf)
  if not text then
    return nil
  end
  local last
  for piece in (text .. "\n"):gmatch("([^\n]*)\n") do
    local trimmed = piece:gsub("^[ \t\r]+", ""):gsub("[ \t\r]+$", "")
    if trimmed ~= "" then
      last = trimmed
    end
  end
  return last
end

---기본 끝 보고 (work-kb--report).
function M.report(bufnr, code)
  if code == 0 then
    local last = M.last_line(bufnr)
    ui().notify("work-kb: 완료" .. (last and (" — " .. last) or ""))
  else
    if bufnr and vim.api.nvim_buf_is_valid(bufnr) then
      ui().show(bufnr)
    end
    ui().notify(string.format("work-kb: 실패 (종료 %s). %s 버퍼를 보세요", tostring(code), M.buffer_name(bufnr)))
  end
end

---------------------------------------------------------------- 프로세스 (이음매)

---한 번 실행의 환경에 얹는 값. 출력을 버퍼에 담지 않아야 stdout·stderr가 쓴 순서대로 한 파이프에 도착한다
---(상주 서버의 출력 순서와 같아진다).
M.ONESHOT_ENV = { PYTHONUNBUFFERED = "1" }

---지금 프로세스의 환경을 물려받고 OVERRIDES(이름 → 값, false면 지움)를 얹은 "이름=값" 목록.
---Windows 환경 변수 이름은 대소문자를 가리지 않으므로 같은 이름(대소문자 무시)은 바꿔 쓴다.
---@param overrides table<string, string|false>
---@return string[]
function M.environ(overrides)
  local env, names = {}, {}
  for k, v in pairs(uv.os_environ()) do
    env[k] = v
    names[k:upper()] = k
  end
  for k, v in pairs(overrides or {}) do
    local existing = names[k:upper()]
    if existing then
      env[existing] = nil
      names[k:upper()] = nil
    end
    if v ~= false and v ~= nil then
      env[k] = tostring(v)
      names[k:upper()] = k
    end
  end
  local list = {}
  for k, v in pairs(env) do
    table.insert(list, k .. "=" .. v)
  end
  table.sort(list)
  return list
end

---하위 프로세스를 띄운다. 테스트는 이 함수를 바꿔 끼운다.
---spec = { cmd = {...}, cwd = 폴더, stdin = 문자열|nil, keep_stdin = bool, merge = bool(기본 true),
---         env = { 이름 = 값|false }|nil (지금 환경에 얹을 값; false는 지움, nil이면 그대로 물려받음),
---         on_stdout = fn(data), on_stderr = fn(data) (merge가 거짓일 때), on_exit = fn(code, signal) }
---콜백은 모두 vim.schedule로 메인 루프에서, 출력이 모두 온 뒤 on_exit 순서로 부른다.
---돌려주는 표: { pid, write = fn(s), close_stdin = fn(), kill = fn(), alive = fn() -> bool }
function M._spawn(spec)
  local merge = spec.merge ~= false
  local stdin = uv.new_pipe(false)
  local stdout, stderr, write_fd
  local stdio
  if merge then
    -- 한 파이프의 쓰기 끝을 자식의 stdout·stderr 둘 다로 넘긴다: 도착 순서 그대로 합쳐진다.
    local fds, perr = uv.pipe({ nonblock = true }, { nonblock = false })
    if not fds then
      stdin:close()
      error("work-kb: 파이프를 만들지 못했습니다: " .. tostring(perr), 0)
    end
    stdout = uv.new_pipe(false)
    stdout:open(fds.read)
    write_fd = fds.write
    stdio = { stdin, write_fd, write_fd }
  else
    stdout = uv.new_pipe(false)
    stderr = uv.new_pipe(false)
    stdio = { stdin, stdout, stderr }
  end

  local exited, exit_code, exit_signal = false, nil, nil
  local open_streams = merge and 1 or 2
  local finished = false
  local grace

  local function close(h)
    if h and not h:is_closing() then
      h:close()
    end
  end

  local function finish()
    if finished then
      return
    end
    finished = true
    if grace then
      grace:stop()
      close(grace)
    end
    close(stdout)
    close(stderr)
    close(stdin)
    vim.schedule(function()
      if spec.on_exit then
        spec.on_exit(exit_code, exit_signal)
      end
    end)
  end

  local cmd = spec.cmd
  local args = {}
  for i = 2, #cmd do
    args[i - 1] = cmd[i]
  end
  local handle
  local pid_or_err
  handle, pid_or_err = uv.spawn(cmd[1], {
    args = args,
    cwd = spec.cwd,
    stdio = stdio,
    env = spec.env and M.environ(spec.env) or nil,
    hide = true,
  }, function(code, signal)
    exited, exit_code, exit_signal = true, code, signal
    close(handle)
    if open_streams <= 0 then
      finish()
    else
      -- 손자 프로세스가 파이프를 물고 있어도 오래 기다리지 않는다.
      grace = uv.new_timer()
      grace:start(2000, 0, function()
        finish()
      end)
    end
  end)
  if write_fd then
    uv.fs_close(write_fd)
  end
  if not handle then
    close(stdin)
    close(stdout)
    close(stderr)
    error(string.format("work-kb: 실행하지 못했습니다 (%s): %s", tostring(cmd[1]), tostring(pid_or_err)), 0)
  end

  local function reader(cb)
    return function(err, data)
      if data then
        if cb then
          vim.schedule(function()
            cb(data)
          end)
        end
      else
        open_streams = open_streams - 1
        if open_streams <= 0 and exited then
          finish()
        end
      end
    end
  end
  stdout:read_start(reader(spec.on_stdout))
  if stderr then
    stderr:read_start(reader(spec.on_stderr))
  end

  local h = { pid = pid_or_err }
  function h.write(s)
    if not stdin:is_closing() then
      stdin:write(s)
    end
  end
  function h.close_stdin()
    if not stdin:is_closing() then
      stdin:shutdown(function()
        close(stdin)
      end)
    end
  end
  function h.kill()
    if not handle:is_closing() and not exited then
      pcall(handle.kill, handle, "sigterm")
    end
  end
  function h.alive()
    return not exited
  end
  if spec.stdin then
    h.write(spec.stdin)
  end
  if not spec.keep_stdin then
    h.close_stdin()
  end
  return h
end

---------------------------------------------------------------- 실행

local SERVED = { status = true, search = true, today = true, link = true }
local SERVED_SUB = { proposals = { list = true }, act = { list = true, transitions = true } }

---상주 서버로 보낼 수 있는 명령인가 (빠른 로컬 명령: 읽기 status, search, proposals list, act list,
---act transitions; 볼트를 고치는 today, link). today·link는 kb_lock 안에서 원자적으로 쓰고 다시 돌려도
---결과가 같으므로, 서버가 도중에 죽거나 시간 초과로 죽여도 한 번 실행으로 다시 돌린다 (잠금은
---운영체제가 프로세스와 함께 풀고, kb_lock은 60초까지 기다린다).
function M.served(args)
  local cmd = args[1]
  if SERVED[cmd] then
    return true
  end
  local subs = SERVED_SUB[cmd]
  return subs ~= nil and subs[args[2]] == true
end

local function server_enabled()
  local server = config().get().server
  return type(server) == "table" and server.enable == true
end

---kb.py를 비동기로 실행한다. 출력은 BUFFER(기본 *work-kb*; 실행 전에 비움, APPEND면 끝에 이음)에.
---HEADER가 있으면 출력 앞에 넣는다. 끝나면 ON_EXIT(code, bufnr), 없으면 기본 보고.
---돌려주는 표: { args, bufnr, done, code, via = "process"|"server" } (proc.wait로 기다린다).
---@param args string[]
---@param opts? {buffer?: string|integer, append?: boolean, header?: string, on_exit?: fun(code: integer|nil, bufnr: integer)}
function M.run(args, opts)
  opts = opts or {}
  if type(args) ~= "table" then
    error("work-kb: ARGS는 문자열 목록이어야 합니다: " .. vim.inspect(args))
  end
  for i = 1, #args do
    if type(args[i]) ~= "string" then
      error("work-kb: ARGS는 문자열 목록이어야 합니다: " .. vim.inspect(args))
    end
  end
  local root = config().root()
  local python = M.python_command()
  local bufnr = M.get_buffer(opts.buffer or M.OUTPUT, true)
  if not bufnr then
    error("work-kb: 버퍼가 없습니다: " .. tostring(opts.buffer))
  end
  if not opts.append then
    erase(bufnr)
  end
  if opts.header then
    local count = vim.api.nvim_buf_line_count(bufnr)
    local last = vim.api.nvim_buf_get_lines(bufnr, count - 1, count, false)[1] or ""
    if last ~= "" then
      M._append(bufnr, "\n")
    end
    M._append(bufnr, opts.header)
  end

  local handle = { args = args, bufnr = bufnr, done = false }
  local out = writer(bufnr)

  local function finish(code)
    if handle.done then
      return
    end
    out.flush()
    handle.done = true
    handle.code = code
    local ok, err = pcall(function()
      if opts.on_exit then
        opts.on_exit(code, bufnr)
      else
        M.report(bufnr, code)
      end
    end)
    if not ok then
      ui().notify("work-kb: 후처리 오류: " .. tostring(err))
    end
  end

  local function oneshot()
    handle.via = "process"
    local cmd = vim.list_extend(vim.list_extend({}, python), M.SCRIPT)
    handle.process = M._spawn({
      cmd = cmd,
      cwd = root,
      stdin = M.json_array(args),
      merge = true,
      env = vim.deepcopy(M.ONESHOT_ENV),
      on_stdout = function(data)
        if not handle.cancelled then
          out.write(data)
        end
      end,
      on_exit = function(code)
        if not handle.cancelled then
          finish(code)
        end
      end,
    })
  end

  if server_enabled() and M.served(args) then
    local server = require("work-kb.server")
    local key = server.key(root, python)
    if server.available(key) then
      handle.via = "server"
      handle.server_item = server.request(args, function(code, text)
        if handle.cancelled or handle.done then
          return
        end
        if code == nil then
          -- 거부·시간 초과·서버 죽음: 한 번 실행으로 (결과는 같다).
          local ok, err = pcall(oneshot)
          if not ok then
            ui().notify(tostring(err))
            finish(nil)
          end
        else
          out.write(text or "")
          finish(code)
        end
      end)
      return handle
    end
    server.ensure(key)
  end
  oneshot()
  return handle
end

---run이 돌려준 HANDLE을 멈춘다 (on_exit는 부르지 않는다).
function M.cancel(handle)
  if handle.done then
    return
  end
  handle.cancelled = true
  handle.done = true
  handle.code = nil
  if handle.server_item then
    -- 서버 큐에서 아직 보내지 않은 요청은 빼낸다 (멈춘 요청이 나중에 서버에서 실행되지 않게).
    local server = package.loaded["work-kb.server"]
    if server then
      server.cancel(handle.server_item)
    end
  end
  if handle.process then
    handle.process.kill()
  end
end

---HANDLE이 끝날 때까지 기다린다. 종료 코드, 시간 초과면 멈추고 nil.
function M.wait(handle, timeout_ms)
  timeout_ms = timeout_ms or M.default_timeout_ms
  vim.wait(timeout_ms, function()
    return handle.done
  end, 10)
  if handle.done and not handle.cancelled then
    return handle.code
  end
  M.cancel(handle)
  return nil
end

---ARGS로 kb.py를 실행하고 끝날 때까지 기다린다 (보고 없음). 종료 코드, 시간 초과면 nil.
---@param args string[]
---@param timeout_ms? integer 기본 120000
---@return integer|nil
function M.run_sync(args, timeout_ms)
  local handle = M.run(args, { on_exit = function() end })
  return M.wait(handle, timeout_ms)
end

---ARGS로 kb.py를 실행하고 기다려 종료 코드와 버퍼 글을 돌려준다. 시간 초과면 사용자 오류.
---@param args string[]
---@param opts? {buffer?: string|integer, timeout_ms?: integer}
---@return integer|nil code, string text
function M.run_output(args, opts)
  opts = opts or {}
  local timeout_ms = opts.timeout_ms or M.default_timeout_ms
  local handle = M.run(args, { buffer = opts.buffer, on_exit = function() end })
  local code = M.wait(handle, timeout_ms)
  if code == nil and handle.cancelled then
    local seconds = timeout_ms / 1000
    if seconds == math.floor(seconds) then
      seconds = string.format("%d", seconds)
    end
    ui().fail(string.format("work-kb: %s 응답이 없습니다 (%s초)", tostring(args[1]), tostring(seconds)))
  end
  return code, M.buffer_text(handle.bufnr) or ""
end

return M
