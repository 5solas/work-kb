-- 헤드리스 테스트 도우미: 임시 한글 볼트, 바이트 그대로 파일 읽기·쓰기, 고정 시각,
-- proc._spawn 가짜, ui 가짜, 단언. 외부 Lua 라이브러리 없이 Neovim API만 쓴다.
-- 파일 입출력은 vim.uv.fs_*만 쓴다 (한글 경로를 UTF-8 → 와이드 API로 넘긴다).

local H = {}

local uv = vim.uv

H.repo = vim.fs.normalize(vim.g.work_kb_test_repo or vim.fn.getcwd())

---플러그인 폴더 (저장소 기준 상대 경로와 절대 경로). minimal_init.lua의 PLUGIN_REL이 정한다.
H.PLUGIN_REL = vim.g.work_kb_test_plugin_rel or "nvim"
H.plugin = H.repo .. "/" .. H.PLUGIN_REL

---고정 시각: 2026-11-05 09:12:00 (로컬).
H.TIME = os.time({ year = 2026, month = 11, day = 5, hour = 9, min = 12, sec = 0 })

---------------------------------------------------------------- 단언

local function fmt(value)
  return vim.inspect(value)
end

function H.eq(actual, expected, msg)
  if not vim.deep_equal(actual, expected) then
    error(string.format("%s\n  기대: %s\n  실제: %s", msg or "같지 않음", fmt(expected), fmt(actual)), 2)
  end
end

function H.ok(cond, msg)
  if not cond then
    error(msg or "거짓", 2)
  end
end

---TEXT에 NEEDLE(글자 그대로)이 있는가.
function H.contains(text, needle, msg)
  if type(text) ~= "string" or not text:find(needle, 1, true) then
    error(string.format("%s\n  찾는 글: %s\n  대상: %s", msg or "포함하지 않음", fmt(needle), fmt(text)), 2)
  end
end

function H.not_contains(text, needle, msg)
  if type(text) == "string" and text:find(needle, 1, true) then
    error(string.format("%s\n  없어야 할 글: %s\n  대상: %s", msg or "포함함", fmt(needle), fmt(text)), 2)
  end
end

---FN이 오류를 내고 그 값이 EXPECTED(문자열 그대로)인가. 오류 값을 돌려준다.
function H.fails_with(fn, expected, msg)
  local ok, err = pcall(fn)
  if ok then
    error((msg or "오류가 나야 함") .. ": 오류 없음", 2)
  end
  if expected ~= nil and err ~= expected then
    error(string.format("%s\n  기대 오류: %s\n  실제 오류: %s", msg or "다른 오류", fmt(expected), fmt(err)), 2)
  end
  return err
end

function H.ascii(s)
  return type(s) == "string" and not s:find("[\128-\255]")
end

---이 테스트를 건너뛴다 (러너가 skipped로 센다).
function H.skip(reason)
  error({ work_kb_skip = reason }, 0)
end

---이 테스트는 돌리지 못했다 (러너가 NOT RUN 줄로 알리고 통과로 세지 않는다).
function H.not_run(reason)
  error({ work_kb_not_run = reason }, 0)
end

function H.wait(cond, timeout_ms)
  return vim.wait(timeout_ms or 10000, cond, 10)
end

---------------------------------------------------------------- 파일

function H.exists(path)
  return uv.fs_stat(path) ~= nil
end

function H.mkdir_p(path)
  vim.fn.mkdir(path, "p")
end

---DATA를 바이트 그대로 PATH에 쓴다 (EOL 변환 없음). 부모 폴더를 만든다.
function H.write_bytes(path, data)
  H.mkdir_p(vim.fs.dirname(path))
  local fd = assert(uv.fs_open(path, "w", 438))
  assert(uv.fs_write(fd, data, 0))
  assert(uv.fs_close(fd))
end

---PATH의 바이트 그대로. 없으면 nil.
function H.read_bytes(path)
  local fd = uv.fs_open(path, "r", 438)
  if not fd then
    return nil
  end
  local st = assert(uv.fs_fstat(fd))
  local data = st.size > 0 and assert(uv.fs_read(fd, st.size, 0)) or ""
  uv.fs_close(fd)
  return data
end

function H.copy_file(src, dst)
  H.mkdir_p(vim.fs.dirname(dst))
  assert(uv.fs_copyfile(src, dst))
end

local tmp_root
local counter = 0

---이번 실행의 ASCII 임시 폴더: WORK_KB_TEST_TMP(없으면 Neovim 임시 폴더) 아래 새 폴더 run-<pid>-<시각>.
---러너가 끝날 때 H.cleanup_tmp()가 통째로 지운다 (WORK_KB_TEST_KEEP=1이면 남긴다).
function H.tmp()
  if not tmp_root then
    local env = vim.env.WORK_KB_TEST_TMP
    local base
    if env and env ~= "" then
      base = vim.fs.normalize(env)
    else
      base = vim.fs.normalize(vim.fn.tempname())
    end
    tmp_root = string.format("%s/run-%d-%d", base, vim.fn.getpid(), vim.uv.hrtime() % 1000000000)
    H.mkdir_p(tmp_root)
  end
  return tmp_root
end

---이번 실행의 임시 폴더를 지운다. 지웠거나 지울 것이 없으면 nil, 남았으면 그 경로.
function H.cleanup_tmp()
  if not tmp_root or vim.env.WORK_KB_TEST_KEEP == "1" then
    return nil
  end
  local dir = tmp_root
  for _ = 1, 3 do
    vim.fn.delete(dir, "rf")
    if not H.exists(dir) then
      tmp_root = nil
      return nil
    end
    vim.wait(500) -- 방금 끝난 프로세스가 파일을 아직 잡고 있을 수 있다
  end
  return dir
end

---한글·공백 경로의 빈 볼트 (슬래시, 끝 슬래시 없음).
function H.make_vault(label)
  counter = counter + 1
  local dir = string.format("%s/%s %d", H.tmp(), label or "한글 볼트", counter)
  for _, sub in ipairs({ "daily", "inbox", "raw/assets", "jira/_views", "wiki/people", "wiki/projects", "wiki/topics", ".state" }) do
    H.mkdir_p(dir .. "/" .. sub)
  end
  return dir
end

---저장소의 config.yaml을 볼트에 복사한다 (실제 kb.py를 돌릴 때; conftest의 vault 픽스처와 같음).
function H.copy_config(vault)
  local src = H.repo .. "/config.yaml"
  if H.exists(src) then
    H.copy_file(src, vault .. "/config.yaml")
  end
end

---테스트에 쓸 실제 Python: WORK_KB_TEST_PYTHON, 없으면 저장소의 .venv.
function H.python()
  local env = vim.env.WORK_KB_TEST_PYTHON
  if env and env ~= "" then
    return vim.fs.normalize(env)
  end
  return H.repo .. "/.venv/Scripts/python.exe"
end

---------------------------------------------------------------- 바꿔 끼우기 (테스트마다 되돌림)

local saved = {}

---TBL[KEY]를 VALUE로 바꾼다. reset()이 원래 값으로 되돌린다.
function H.patch(tbl, key, value)
  table.insert(saved, { tbl = tbl, key = key, value = rawget(tbl, key) })
  tbl[key] = value
end

---설정: 서버 끔, 시각 고정, 접두 키 없음이 기본. OPTS가 덮는다.
function H.configure(opts)
  local merged = {
    root = H.repo,
    prefix = false,
    server = { enable = false },
    now = function()
      return H.TIME
    end,
  }
  for k, v in pairs(opts or {}) do
    merged[k] = v
  end
  return require("work-kb.config").setup(merged)
end

---새 볼트를 만들고 그 볼트로 설정한다 (루트는 저장소). 볼트 경로를 돌려준다.
function H.with_vault(opts)
  local vault = H.make_vault()
  local o = { vault = vault }
  for k, v in pairs(opts or {}) do
    o[k] = v
  end
  H.configure(o)
  return vault
end

---proc._spawn 가짜. BEHAVIOR는 { code = 0, output = "..." } 또는 fn(call) -> output, code.
---서버 실행(spec.server)은 기본으로 진짜 _spawn에 넘긴다 (passthrough_server = false면 가짜).
---돌려주는 표의 calls[i] = { cmd, cwd, stdin, argv(stdin JSON을 푼 것), spec }.
function H.fake_spawn(behavior, opts)
  opts = opts or {}
  local proc = require("work-kb.proc")
  local real = proc._spawn
  local recorder = { calls = {} }
  H.patch(proc, "_spawn", function(spec)
    if spec.server and opts.passthrough_server ~= false then
      return real(spec)
    end
    local call = { cmd = spec.cmd, cwd = spec.cwd, stdin = spec.stdin, spec = spec }
    if spec.stdin then
      local ok, decoded = pcall(vim.json.decode, spec.stdin)
      call.argv = ok and decoded or nil
    end
    table.insert(recorder.calls, call)
    local output, code
    if type(behavior) == "function" then
      output, code = behavior(call)
    else
      output, code = (behavior or {}).output, (behavior or {}).code
    end
    local alive = true
    vim.schedule(function()
      local chunks = type(output) == "table" and output or { output }
      for _, chunk in ipairs(chunks) do
        if chunk and chunk ~= "" and spec.on_stdout then
          spec.on_stdout(chunk)
        end
      end
      if code == "hang" then -- 끝나지 않는 프로세스 (시간 초과 시험)
        return
      end
      vim.schedule(function()
        alive = false
        if spec.on_exit then
          spec.on_exit(code or 0, 0)
        end
      end)
    end)
    return {
      pid = 0,
      write = function() end,
      close_stdin = function() end,
      kill = function()
        alive = false
        recorder.killed = (recorder.killed or 0) + 1
      end,
      alive = function()
        return alive
      end,
    }
  end)
  return recorder
end

---진짜 _spawn을 쓰되 명령을 MAP(spec)으로 바꾼다 (Emacs 테스트의 make-process 바꿔치기).
function H.rewrite_spawn(map)
  local proc = require("work-kb.proc")
  local real = proc._spawn
  local recorder = { calls = {} }
  H.patch(proc, "_spawn", function(spec)
    table.insert(recorder.calls, { cmd = spec.cmd, cwd = spec.cwd, stdin = spec.stdin })
    local copy = vim.deepcopy(spec)
    copy.on_stdout, copy.on_stderr, copy.on_exit = spec.on_stdout, spec.on_stderr, spec.on_exit
    copy.cmd = map(spec) or spec.cmd
    return real(copy)
  end)
  return recorder
end

---ui 가짜. ANSWERS는 차례로 쓰는 답: { "input", "값" } / { "select", "값" } / { "confirm", true }.
---답이 없는데 물으면(또는 종류가 다르면) 오류. 알림·보인 버퍼·프롬프트를 기록한다.
function H.ui_stub(answers)
  local ui = require("work-kb.ui")
  local stub = { answers = vim.deepcopy(answers or {}), prompts = {}, messages = {}, shown = {} }
  local function next_answer(kind, prompt)
    table.insert(stub.prompts, { kind = kind, prompt = prompt })
    local answer = table.remove(stub.answers, 1)
    if not answer then
      error("물으면 안 됨: " .. kind .. " " .. tostring(prompt), 0)
    end
    if answer[1] ~= kind then
      error(string.format("다른 질문: %s를 기대했지만 %s %s", answer[1], kind, tostring(prompt)), 0)
    end
    return answer[2]
  end
  H.patch(ui, "input", function(prompt, o)
    local value = next_answer("input", prompt)
    if value == ":initial" then
      return (o or {}).initial or ""
    end
    return value
  end)
  H.patch(ui, "select", function(prompt, items)
    stub.last_items = items
    return next_answer("select", prompt)
  end)
  H.patch(ui, "confirm", function(msg)
    return next_answer("confirm", msg) == true
  end)
  H.patch(ui, "notify", function(msg)
    table.insert(stub.messages, msg)
  end)
  H.patch(ui, "show", function(bufnr)
    table.insert(stub.shown, bufnr)
  end)
  return stub
end

---테스트 하나가 끝난 뒤: 바꿔 끼운 것을 되돌리고 서버를 멈추고 설정을 지운다.
function H.reset()
  local server = package.loaded["work-kb.server"]
  if server and server._reset then
    pcall(server._reset)
  end
  for i = #saved, 1, -1 do
    local entry = saved[i]
    entry.tbl[entry.key] = entry.value
  end
  saved = {}
  local proc = package.loaded["work-kb.proc"]
  if proc then
    proc._py_cache = nil
  end
  local config = package.loaded["work-kb.config"]
  if config then
    config.setup({ server = { enable = false }, prefix = false })
  end
end

---출력 버퍼(이름 또는 번호)의 글.
function H.buffer_text(name)
  return require("work-kb.proc").buffer_text(name or "*work-kb*")
end

return H
