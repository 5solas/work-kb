-- 상주 Python 서버 (`kb.py serve`) 클라이언트. 계약 A:
--   시작: <python> -X utf8 scripts/kb.py serve --idle-sec N (작업 폴더 = 코드 루트, 명령줄 ASCII)
--   준비: {"ready": true, "pid": <int>, "version": 1}
--   요청: {"id": n, "argv": [...]} → {"id": n, "code": c, "out": "..."} 또는 {"id": n, "refused": true, ...}
--   종료: {"op": "shutdown"}
-- 요청은 하나씩 차례로 보낸다 (앞 요청의 답이 와야 다음을 보낸다). 거부·시간 초과·서버 죽음은
-- 콜백에 nil로 알리고, proc가 한 번 실행으로 돌린다. 다시 띄우기는 1분에 한 번까지.
-- setup()은 이 모듈을 불러오지 않는다: VimEnter 뒤 vim.defer_fn에서, 또는 첫 요청 때 띄운다.

local M = {}

local PROTOCOL_VERSION = 1

M.request_timeout_ms = 30000
M.ready_timeout_ms = 15000
M.restart_interval_ms = 60000

---밀리초 시계 (테스트 이음매).
function M._now_ms()
  return vim.uv.now()
end

---Python 다음에 붙는 인수 (테스트 이음매: 가짜 서버 스크립트로 바꿀 수 있다).
function M._script_args(idle_sec)
  return { "-X", "utf8", "scripts/kb.py", "serve", "--idle-sec", tostring(idle_sec) }
end

local S = {
  state = "stopped", -- stopped | starting | ready | dead
  handle = nil,
  pid = nil,
  key = nil, -- M.key(): { root, python, idle_sec, env }
  last_start = nil,
  queue = {},
  inflight = nil,
  next_id = 0,
  linebuf = "",
  stderr_tail = {},
  last_error = nil,
  ready_timer = nil,
  starts = 0,
}

local function config()
  return require("work-kb.config")
end

local function proc()
  return require("work-kb.proc")
end

local function key_equal(a, b)
  if not a or not b then
    return false
  end
  if a.root ~= b.root or a.idle_sec ~= b.idle_sec or a.env ~= b.env then
    return false
  end
  if type(a.python) ~= "table" or type(b.python) ~= "table" or #a.python ~= #b.python then
    return false
  end
  for i = 1, #a.python do
    if a.python[i] ~= b.python[i] then
      return false
    end
  end
  return true
end

local function enabled()
  local server = config().get().server
  return type(server) == "table" and server.enable == true
end

local function idle_sec()
  local server = config().get().server
  return (type(server) == "table" and server.idle_sec) or 1800
end

---서버를 다시 써도 되는지 가르는 열쇠: 코드 루트, Python 명령, idle_sec, 그리고 지금 환경 전체.
---서버는 띄울 때의 환경으로 명령을 돌리고 한 번 실행은 지금 환경을 물려받으므로, Neovim에서
---vim.env를 바꾸면(KB_AGENT, PATH, 비밀 값 환경 변수 …) 열쇠가 달라져 다음 요청이 서버를 새로 띄운다.
---@param root string
---@param python string[]
function M.key(root, python)
  return {
    root = root,
    python = vim.list_extend({}, python),
    idle_sec = idle_sec(),
    env = table.concat(proc().environ(nil), "\n"),
  }
end

local function stop_timer(t)
  if t then
    t:stop()
    if not t:is_closing() then
      t:close()
    end
  end
end

local function call(cb, code, out)
  local ok, err = pcall(cb, code, out)
  if not ok then
    vim.notify("work-kb: 서버 응답 처리 오류: " .. tostring(err), vim.log.levels.WARN)
  end
end

---남은 요청을 모두 nil(대체 실행)로 돌려보낸다.
local function fail_pending()
  local pending = {}
  if S.inflight then
    stop_timer(S.inflight.timer)
    table.insert(pending, S.inflight)
    S.inflight = nil
  end
  for _, item in ipairs(S.queue) do
    table.insert(pending, item)
  end
  S.queue = {}
  for _, item in ipairs(pending) do
    if not item.cancelled then
      call(item.cb, nil)
    end
  end
end

local function kill_server(reason, new_state)
  if reason then
    S.last_error = reason
  end
  stop_timer(S.ready_timer)
  S.ready_timer = nil
  local handle = S.handle
  S.handle = nil
  S.state = new_state or "dead"
  if handle then
    pcall(handle.kill)
  end
  fail_pending()
end

local pump

local function on_line(line)
  line = line:gsub("\r$", "")
  if line == "" then
    return
  end
  local ok, msg = pcall(vim.json.decode, line)
  if not ok or type(msg) ~= "table" then
    kill_server("서버 출력이 JSON이 아닙니다: " .. line:sub(1, 200))
    return
  end
  if S.state == "starting" then
    if msg.ready == true and msg.version == PROTOCOL_VERSION then
      stop_timer(S.ready_timer)
      S.ready_timer = nil
      S.state = "ready"
      S.pid = msg.pid
      pump()
    else
      kill_server("준비 줄이 계약과 다릅니다: " .. line:sub(1, 200))
    end
    return
  end
  local item = S.inflight
  -- serve.py는 요청 줄을 읽지 못하면(UTF-8·JSON 오류) id 없이 {"id": null, "refused": true}로 답한다.
  -- 요청은 한 번에 하나만 보내므로 그 거부는 지금 요청의 것이다: 서버를 죽이지 않고 한 번 실행으로 돌린다.
  local refused_unnumbered = item ~= nil and msg.refused == true and (msg.id == nil or msg.id == vim.NIL)
  if not item or (msg.id ~= item.id and not refused_unnumbered) then
    kill_server("요청 번호가 맞지 않는 응답: " .. line:sub(1, 200))
    return
  end
  stop_timer(item.timer)
  S.inflight = nil
  -- 다음 요청을 먼저 보낸다: 콜백 안에서 proc.wait로 큐의 다음 요청을 기다려도 멈추지 않는다.
  pump()
  if item.cancelled then
    return
  end
  if msg.refused then
    call(item.cb, nil)
  elseif type(msg.code) == "number" then
    call(item.cb, msg.code, type(msg.out) == "string" and msg.out or "")
  else
    call(item.cb, nil)
  end
end

local function on_stdout(data)
  S.linebuf = S.linebuf .. data
  while true do
    local nl = S.linebuf:find("\n", 1, true)
    if not nl then
      break
    end
    local line = S.linebuf:sub(1, nl - 1)
    S.linebuf = S.linebuf:sub(nl + 1)
    on_line(line)
    if S.state ~= "starting" and S.state ~= "ready" then
      S.linebuf = ""
      break
    end
  end
end

local function on_stderr(data)
  table.insert(S.stderr_tail, data)
  while #S.stderr_tail > 20 do
    table.remove(S.stderr_tail, 1)
  end
end

pump = function()
  if S.inflight or S.state ~= "ready" then
    return
  end
  local item
  repeat
    item = table.remove(S.queue, 1)
  until item == nil or not item.cancelled
  if not item then
    return
  end
  S.inflight = item
  local line = string.format('{"id":%d,"argv":%s}\n', item.id, proc().json_array(item.argv))
  local ok, err = pcall(S.handle.write, line)
  if not ok then
    kill_server("요청을 보내지 못했습니다: " .. tostring(err))
    return
  end
  item.timer = vim.uv.new_timer()
  item.timer:start(M.request_timeout_ms, 0, function()
    vim.schedule(function()
      if S.inflight == item then
        -- 서버가 멈췄다: 죽이고 이 요청은 한 번 실행으로 돌린다.
        kill_server("요청 시간 초과")
      end
    end)
  end)
end

-- 배경 준비(warmup)가 `py -3` 비동기 시험을 기다리는 중인가 (두 번 시험하지 않는다).
local probing = false

---서버를 띄운다 (비동기: 준비 줄이 오면 available()이 참). 이미 떠 있거나 뜨는 중이면 그대로.
---한 번 띄운 뒤에는 1분 안에 다시 띄우지 않는다. 띄우기를 시작했으면 true.
---OPTS.warmup(setup 뒤 VimEnter 예약)이면 메인 루프를 막지 않는다: Python을 찾으려고 `py -3`을 동기로
---시험하지 않고, 필요하면 비동기로 시험한 뒤 다시 부른다 (화면 없는 미리 띄운 Neovim의 RPC 제한 시간).
---@param opts? {warmup?: boolean}
function M.start(opts)
  if not enabled() then
    return false
  end
  if S.state == "starting" or S.state == "ready" then
    return true
  end
  local now = M._now_ms()
  if S.last_start and now - S.last_start < M.restart_interval_ms then
    return false
  end
  local warmup = opts and opts.warmup == true
  local python
  if warmup then
    local ok_py, found = pcall(proc().python_command, { probe = false })
    if not ok_py then
      S.state = "dead"
      S.last_error = tostring(found)
      return false
    end
    if not found then
      if proc()._py_cache == nil and not probing then
        probing = true
        proc().probe_py_launcher(function(works)
          probing = false
          if works and S.state ~= "starting" and S.state ~= "ready" then
            M.start({ warmup = true })
          end
        end)
      end
      return false
    end
    python = found
  end
  S.last_start = now
  local ok, err = pcall(function()
    local root = config().root()
    python = python or proc().python_command()
    local key = M.key(root, python)
    local cmd = vim.list_extend(vim.list_extend({}, python), M._script_args(key.idle_sec))
    S.key = key
    S.linebuf = ""
    S.stderr_tail = {}
    S.last_error = nil
    S.state = "starting"
    S.starts = S.starts + 1
    local handle
    handle = proc()._spawn({
      cmd = cmd,
      cwd = root,
      keep_stdin = true,
      merge = false,
      server = true,
      on_stdout = function(data)
        if S.handle == handle then
          on_stdout(data)
        end
      end,
      on_stderr = function(data)
        if S.handle == handle then
          on_stderr(data)
        end
      end,
      on_exit = function(code)
        if S.handle == handle then
          S.handle = nil
          S.last_exit = code
          if code == 0 and S.state == "ready" and not S.inflight and #S.queue == 0 then
            -- 정상 종료 (--idle-sec 동안 요청 없음, 또는 잠금을 남긴 요청 뒤 스스로 끝남):
            -- 오류가 아니다. 다음 요청이 1분 제한 없이 다시 띄운다.
            S.state = "stopped"
            S.last_start = nil
          elseif S.state ~= "stopped" then
            kill_server(S.last_error or string.format("서버가 끝났습니다 (종료 %s)", tostring(code)))
          end
        end
      end,
    })
    S.handle = handle
    S.ready_timer = vim.uv.new_timer()
    S.ready_timer:start(M.ready_timeout_ms, 0, function()
      vim.schedule(function()
        if S.state == "starting" and S.handle == handle then
          kill_server("준비 줄이 오지 않았습니다")
        end
      end)
    end)
  end)
  if not ok then
    S.state = "dead"
    S.handle = nil
    S.last_error = tostring(err)
    return false
  end
  return true
end

---서버가 준비되어 요청을 받을 수 있는가. KEY(M.key: 루트, Python, idle_sec, 환경)가 다르면 서버를
---멈추고 거짓 (설정이나 환경이 바뀐 것이므로 다음 ensure가 1분 제한 없이 새로 띄운다).
function M.available(key)
  if not enabled() then
    return false
  end
  if S.state ~= "ready" or not S.handle or not S.handle.alive() then
    return false
  end
  if key and not key_equal(key, S.key) then
    M.stop()
    S.last_start = nil
    return false
  end
  return true
end

---서버가 없으면 띄우기를 시도한다 (비동기, 1분 제한). 지금 요청은 기다리지 않는다.
function M.ensure(key)
  if not enabled() then
    return
  end
  if key and S.key and not key_equal(key, S.key) and (S.state == "starting" or S.state == "ready") then
    M.stop()
    S.last_start = nil
  end
  if S.state == "stopped" or S.state == "dead" then
    M.start()
  end
end

---ARGV를 서버로 보낸다. 답이 오면 CB(code, out), 거부·실패면 CB(nil).
---준비되지 않았으면 바로 CB(nil)하고 nil. 아니면 M.cancel에 넘길 요청 표.
function M.request(argv, cb)
  if S.state ~= "ready" or not S.handle then
    call(cb, nil)
    return nil
  end
  S.next_id = S.next_id + 1
  local item = { id = S.next_id, argv = argv, cb = cb }
  table.insert(S.queue, item)
  pump()
  return item
end

---M.request가 돌려준 요청을 거둔다: 아직 보내지 않았으면 큐에서 빼고(서버에서 실행되지 않는다),
---이미 보냈으면 답을 버린다. 어느 쪽이든 CB는 불리지 않는다.
function M.cancel(item)
  if type(item) ~= "table" or item.cancelled then
    return
  end
  item.cancelled = true
  for i, queued in ipairs(S.queue) do
    if queued == item then
      table.remove(S.queue, i)
      return
    end
  end
end

---서버를 멈춘다: shutdown을 보내고 stdin을 닫는다. NOW면 바로 죽이고, 아니면 1초 뒤에도
---살아 있을 때 죽인다. 남은 요청은 nil로 돌려보낸다.
function M.stop(now)
  local handle = S.handle
  stop_timer(S.ready_timer)
  S.ready_timer = nil
  S.handle = nil
  S.state = "stopped"
  if handle then
    pcall(handle.write, '{"op":"shutdown"}\n')
    pcall(handle.close_stdin)
    if now then
      pcall(handle.kill)
    else
      vim.defer_fn(function()
        if handle.alive() then
          pcall(handle.kill)
        end
      end, 1000)
    end
  end
  fail_pending()
end

---상태 (checkhealth·테스트용).
function M.status()
  return {
    state = S.state,
    pid = S.pid,
    key = S.key,
    last_error = S.last_error,
    last_exit = S.last_exit,
    stderr = table.concat(S.stderr_tail),
    starts = S.starts,
    queued = #S.queue + (S.inflight and 1 or 0),
  }
end

---테스트용: 멈추고 기록을 지운다.
function M._reset()
  M.stop(true)
  probing = false
  S.last_start = nil
  S.last_error = nil
  S.last_exit = nil
  S.pid = nil
  S.key = nil
  S.starts = 0
  S.next_id = 0
end

return M
