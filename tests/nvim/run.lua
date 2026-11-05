-- 헤드리스 테스트 러너 (외부 의존성 없음).
--   nvim --headless -u tests/nvim/minimal_init.lua -l tests/nvim/run.lua
-- tests/nvim/spec/*_spec.lua를 모은다. 각 파일은 { { name =, fn = }, ... }를 돌려주거나
-- 전역 describe/it을 쓴다. 실패마다 한 줄, 마지막 줄은 정확히
--   Ran <N> tests, <P> passed, <F> failed, <S> skipped
-- WORK_KB_TEST_FILTER(글자 그대로 포함)로 일부만 돌릴 수 있다. 실패가 있으면 종료 1.

local source = debug.getinfo(1, "S").source:sub(2):gsub("\\", "/")
local repo = vim.g.work_kb_test_repo or source:match("^(.*)/tests/nvim/[^/]+$") or vim.fn.getcwd()
vim.g.work_kb_test_repo = repo
package.path = repo .. "/tests/nvim/?.lua;" .. package.path

local function out(line)
  io.stdout:write(line, "\n")
  io.stdout:flush()
end

---활성 콘솔 코드 페이지 (cp65001, cp949 …). 콘솔이 없으면 사용자 로캘의 ANSI 코드 페이지.
local function console_locale()
  local ok, value = pcall(function()
    local ffi = require("ffi")
    pcall(ffi.cdef, [[
      unsigned int GetConsoleCP(void);
      int GetLocaleInfoEx(const wchar_t* name, unsigned int lctype, wchar_t* data, int cch);
    ]])
    local cp = ffi.C.GetConsoleCP()
    if cp ~= 0 then
      return "cp" .. tostring(cp)
    end
    local buf = ffi.new("wchar_t[16]")
    local n = ffi.C.GetLocaleInfoEx(nil, 0x1004, buf, 16) -- LOCALE_IDEFAULTANSICODEPAGE
    local chars = {}
    for i = 0, n - 2 do
      chars[#chars + 1] = string.char(buf[i])
    end
    return "cp" .. table.concat(chars)
  end)
  return ok and value or "unknown"
end

out("WORK-KB-TEST-LOCALE: " .. console_locale())

-- 시작 직후 상태 (plugin/work-kb.lua 시험용): 아무 테스트도 work-kb를 불러오기 전.
do
  local loaded = {}
  for name in pairs(package.loaded) do
    if name == "work-kb" or name:find("^work%-kb%.") then
      table.insert(loaded, name)
    end
  end
  table.sort(loaded)
  local def = vim.api.nvim_get_commands({})["WorkKb"] or {}
  _G.WORK_KB_STARTUP = {
    command = vim.fn.exists(":WorkKb") == 2,
    loaded = loaded,
    plugin = vim.g.loaded_work_kb,
    nargs = def.nargs,
    range = def.range,
  }
end

local H = require("helpers")

local tests = {}
local current_file
local name_stack = {}

_G.describe = function(name, fn)
  table.insert(name_stack, name)
  fn()
  table.remove(name_stack)
end

_G.it = function(name, fn)
  local full = table.concat(name_stack, " ")
  full = full ~= "" and (full .. " " .. name) or name
  table.insert(tests, { file = current_file, name = full, fn = fn })
end

local failures = {}
local load_errors = 0

-- WORK_KB_TEST_SPEC_DIR(저장소 기준 상대 경로)로 다른 스펙 폴더를 돌릴 수 있다. 기본은 플러그인 스펙.
local spec_dir = vim.env.WORK_KB_TEST_SPEC_DIR
if spec_dir == nil or spec_dir == "" then
  spec_dir = "tests/nvim/spec"
end
local files = vim.fn.glob(repo .. "/" .. spec_dir .. "/*_spec.lua", false, true)
table.sort(files)
for _, file in ipairs(files) do
  current_file = vim.fn.fnamemodify(file, ":t:r")
  local ok, ret = pcall(dofile, file)
  if not ok then
    load_errors = load_errors + 1
    table.insert(failures, string.format("FAIL %s: (불러오기) %s", current_file, tostring(ret)))
  elseif type(ret) == "table" then
    for _, t in ipairs(ret) do
      table.insert(tests, { file = current_file, name = t.name, fn = t.fn })
    end
  end
end

local filter = vim.env.WORK_KB_TEST_FILTER
local verbose = vim.env.WORK_KB_TEST_VERBOSE == "1"
local ran, passed, failed, skipped = load_errors, 0, load_errors, 0
local not_run = {}

for _, t in ipairs(tests) do
  local label = t.file .. ": " .. t.name
  if not filter or filter == "" or label:find(filter, 1, true) then
    local started = vim.uv.hrtime()
    local ok, err = xpcall(t.fn, function(e)
      if type(e) == "table" and (e.work_kb_skip or e.work_kb_not_run) then
        return e
      end
      return debug.traceback(tostring(e), 2)
    end)
    local reset_ok, reset_err = pcall(H.reset)
    if ok and not reset_ok then
      ok, err = false, "reset: " .. tostring(reset_err)
    end
    local ms = (vim.uv.hrtime() - started) / 1e6
    if not ok and type(err) == "table" and err.work_kb_not_run then
      table.insert(not_run, string.format("NOT RUN %s: %s", label, err.work_kb_not_run))
    else
      ran = ran + 1
      if ok then
        passed = passed + 1
        if verbose then
          out(string.format("ok   %s (%.0f ms)", label, ms))
        end
      elseif type(err) == "table" and err.work_kb_skip then
        skipped = skipped + 1
        out(string.format("SKIP %s: %s", label, err.work_kb_skip))
      else
        failed = failed + 1
        table.insert(failures, string.format("FAIL %s: %s", label, tostring(err)))
        out(failures[#failures])
      end
    end
  end
end

if load_errors > 0 then
  for i = 1, load_errors do
    out(failures[i])
  end
end
for _, line in ipairs(not_run) do
  out(line)
end
-- 테스트가 만든 임시 볼트를 지운다 (WORK_KB_TEST_KEEP=1이면 남긴다). 못 지우면 실패로 센다.
for _, buf in ipairs(vim.api.nvim_list_bufs()) do
  pcall(vim.api.nvim_buf_delete, buf, { force = true })
end
local left = H.cleanup_tmp()
if left then
  failed = failed + 1
  ran = ran + 1
  out("FAIL (정리) 임시 폴더가 남음: " .. left)
end
out(string.format("Ran %d tests, %d passed, %d failed, %d skipped", ran, passed, failed, skipped))
os.exit(failed > 0 and 1 or 0)
