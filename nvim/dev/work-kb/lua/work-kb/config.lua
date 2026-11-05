-- work-kb 설정: setup()이 넘긴 값과 기본값, 코드 루트·볼트 경로 검사.
-- 이 모듈은 불러올 때 아무 I/O도 하지 않고 다른 모듈을 불러오지 않는다 (setup 비용).

local M = {}

---코드 루트 기본값: 이 파일은 <루트>/nvim/dev/work-kb/lua/work-kb/config.lua에 있으므로 그 앞부분이 루트다.
---문자열만 다루고 파일 시스템은 보지 않는다.
local function default_root()
  local source = debug.getinfo(1, "S").source or ""
  if source:sub(1, 1) ~= "@" then
    return nil
  end
  local path = source:sub(2):gsub("\\", "/")
  local root = path:match("^(.*)/nvim/dev/work%-kb/lua/work%-kb/[^/]+$")
  return root
end

M.defaults = {
  root = default_root(), -- 코드 루트 (scripts/kb.py가 있는 폴더)
  vault = nil, -- 볼트 루트. nil이면 코드 루트
  python = nil, -- 문자열 하나 또는 목록 (예: { "py", "-3" }). nil이면 자동
  prefix = "<leader>k", -- 접두 키. false 또는 ""면 붙이지 않는다
  server = {
    enable = true, -- kb.py serve 상주 서버 사용
    idle_sec = 1800, -- 요청 없이 이 초가 지나면 서버가 스스로 끝난다
    delay_ms = 300, -- VimEnter 뒤 서버를 띄우기까지 기다리는 시간
  },
  now = nil, -- 현재 시각(초)을 돌려주는 함수 (테스트 이음매)
}

local state = nil

local function copy(value)
  if type(value) ~= "table" then
    return value
  end
  local out = {}
  for k, v in pairs(value) do
    out[k] = copy(v)
  end
  return out
end

---opts를 기본값 위에 얹어 저장한다. 없는 키는 기본값. `root = false`는 '설정 안 함'.
---@param opts table|nil
function M.setup(opts)
  opts = opts or {}
  local cfg = copy(M.defaults)
  for k, v in pairs(opts) do
    if k == "server" and type(v) == "table" then
      for sk, sv in pairs(v) do
        cfg.server[sk] = sv
      end
    else
      cfg[k] = copy(v)
    end
  end
  if cfg.root == false then
    cfg.root = nil
  end
  if cfg.vault == false then
    cfg.vault = nil
  end
  state = cfg
  return state
end

---현재 설정 (setup 전이면 기본값).
function M.get()
  if not state then
    M.setup({})
  end
  return state
end

---Emacs `%S`처럼 값을 보인다: nil → nil, 문자열 → "따옴표" (역슬래시·따옴표는 이스케이프).
local function lisp_repr(value)
  if value == nil or value == false then
    return "nil"
  end
  if type(value) == "string" then
    return '"' .. value:gsub("\\", "\\\\"):gsub('"', '\\"') .. '"'
  end
  return tostring(value)
end
M._lisp_repr = lisp_repr

local function is_dir(path)
  local st = vim.uv.fs_stat(path)
  return st ~= nil and st.type == "directory"
end

---절대 경로, 슬래시(/), 끝 슬래시 없음 (드라이브 루트 "D:/"는 그대로).
function M.normalize(path)
  local abs = vim.fn.fnamemodify(path, ":p")
  return vim.fs.normalize(abs)
end

local function fail(msg)
  require("work-kb.ui").fail(msg)
end

---코드 루트. 없거나 폴더가 아니면 사용자 오류 (work-kb--root와 같은 문구).
function M.root()
  local root = M.get().root
  if type(root) ~= "string" or root == "" or not is_dir(root) then
    fail(string.format("work-kb-root를 설정하세요 (scripts/kb.py가 있는 폴더): %s", lisp_repr(root)))
  end
  return M.normalize(root)
end

---볼트 루트. 설정이 없으면 코드 루트. 폴더가 없으면 사용자 오류 (work-kb--vault와 같은 문구).
function M.vault()
  local vault = M.get().vault
  if vault == nil or vault == "" then
    return M.root()
  end
  if type(vault) ~= "string" or not is_dir(vault) then
    fail(string.format("볼트 폴더가 없습니다: %s", tostring(vault)))
  end
  return M.normalize(vault)
end

return M
