-- work-kb 설정: setup()이 넘긴 값과 기본값, 코드 루트·볼트 경로 검사.
-- 이 모듈은 불러올 때 아무 I/O도 하지 않고 다른 모듈을 불러오지 않는다 (setup 비용).

local M = {}

---이 파일의 경로 (슬래시). 불러온 경로 그대로라 정션·심볼릭 링크를 거쳤으면 그 경로다.
local function source_path()
  local source = debug.getinfo(1, "S").source or ""
  if source:sub(1, 1) ~= "@" then
    return nil
  end
  return (source:sub(2):gsub("\\", "/"))
end

-- 플러그인이 놓일 수 있는 자리 (저장소 기준): <루트>/nvim/dev/work-kb/lua/work-kb/<파일> 또는
-- <루트>/nvim/lua/work-kb/<파일>. 앞의 것이 더 구체적이라 먼저 본다.
local LAYOUTS = {
  "^(.*)/nvim/dev/work%-kb/lua/work%-kb/[^/]+$",
  "^(.*)/nvim/lua/work%-kb/[^/]+$",
}

---모듈 파일 경로에서 <루트>를 꺼낸다 (문자열만, 두 배치 모두).
local function root_from_path(path)
  if not path then
    return nil
  end
  path = path:gsub("\\", "/")
  for _, pattern in ipairs(LAYOUTS) do
    local root = path:match(pattern)
    if root then
      return root
    end
  end
  return nil
end
M._root_from_path = root_from_path

-- 실제 경로에서 루트를 찾을 때 모듈 폴더에서 몇 단계까지 올라가 보는가 (dev 배치는 5단계).
local WALK_UP = 6

---모듈 파일 PATH의 폴더에서 위로 WALK_UP 단계까지 올라가며 scripts/kb.py가 있는 첫 폴더. 없으면 nil.
local function root_by_walking(path)
  local dir = vim.fs.dirname((path:gsub("\\", "/")))
  for _ = 1, WALK_UP do
    if not dir or dir == "" then
      return nil
    end
    if vim.uv.fs_stat(dir .. "/scripts/kb.py") then
      return dir
    end
    local parent = vim.fs.dirname(dir)
    if parent == dir then
      return nil
    end
    dir = parent
  end
  return nil
end
M._root_by_walking = root_by_walking

---코드 루트 기본값: 이 파일의 자리(두 배치 중 하나)에서 앞부분이 루트다.
---문자열만 다루고 파일 시스템은 보지 않는다. %LOCALAPPDATA%\nvim이 <저장소>\nvim을 가리키는 정션이면
---이 값은 저장소가 아니라 ...\AppData\Local이 된다: 실제 루트는 root()가 처음 불릴 때 실제 경로로 정한다.
local function default_root()
  return root_from_path(source_path())
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
-- setup에 root를 주지 않았는가 (그러면 root()가 플러그인 파일의 실제 경로에서 루트를 정한다).
local root_is_default = true
-- 실제 경로로 정한 기본 루트 (root()가 처음 불릴 때 채운다; false = 정하지 못함).
local real_default_root = nil

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
---I/O는 하지 않는다 (기본 루트의 실제 경로는 root()가 처음 불릴 때 정한다).
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
  root_is_default = opts.root == nil
  real_default_root = nil
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

---플러그인 파일의 실제 경로(정션·심볼릭 링크를 푼 것)에서 정한 코드 루트: 모듈 폴더에서 위로
---올라가며 scripts/kb.py가 있는 첫 폴더 (배치와 상관없이), 없으면 배치 꼴로 꺼낸 값. 정하지 못하면 nil.
---@param path? string 모듈 파일 (기본: 이 파일; 시험 이음매)
function M.real_default_root(path)
  path = path or source_path()
  if not path then
    return nil
  end
  local real = vim.uv.fs_realpath(path)
  real = real and real:gsub("\\", "/") or path
  return root_by_walking(real) or root_from_path(real)
end

---코드 루트. 없거나 폴더가 아니면 사용자 오류 (work-kb--root와 같은 문구, 옵션 이름은 setup()의 root).
---root를 설정하지 않았으면 플러그인 파일의 실제 경로에서 정하고, 그 아래 scripts/kb.py가 있어야 한다.
function M.root()
  local root = M.get().root
  if root_is_default and type(root) == "string" then
    if real_default_root == nil then
      real_default_root = M.real_default_root() or false
    end
    if real_default_root then
      root = real_default_root
    end
    if is_dir(root) and not vim.uv.fs_stat(root .. "/scripts/kb.py") then
      fail(string.format("코드 루트에 scripts/kb.py가 없습니다: %s. setup()의 root를 설정하세요", M.normalize(root)))
    end
  end
  if type(root) ~= "string" or root == "" or not is_dir(root) then
    fail(string.format("setup()의 root를 설정하세요 (scripts/kb.py가 있는 폴더): %s", lisp_repr(root)))
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

---볼트 판정에 쓰는 폴더: 설정의 vault(폴더 검사 없음), 없으면 root()(설정하지 않았으면 실제 경로).
---정하지 못하면 nil (오류를 내지 않는다).
---@return string|nil
function M.vault_dir()
  local ok, dir = pcall(function()
    local vault = M.get().vault
    if type(vault) == "string" and vault ~= "" then
      return M.normalize(vault)
    end
    return M.root()
  end)
  if ok and type(dir) == "string" and dir ~= "" then
    return dir
  end
  return nil
end

---PATH의 실제 경로 (정션·심볼릭 링크를 풀고 디스크의 대소문자로, 슬래시). 아직 없는 파일은 가장 가까운
---있는 부모의 실제 경로 뒤에 나머지를 붙인다. 못 정하면 nil.
local function real_path(path)
  local tail = {}
  local cur = path
  for _ = 1, 64 do
    local real = vim.uv.fs_realpath(cur)
    if real then
      local out = vim.fs.normalize(real)
      for i = #tail, 1, -1 do
        out = out .. "/" .. tail[i]
      end
      return out
    end
    local parent = vim.fs.dirname(cur)
    if not parent or parent == cur then
      return nil
    end
    table.insert(tail, vim.fs.basename(cur))
    cur = parent
  end
  return nil
end

---PATH가 DIR 아래(DIR 자신 제외)면 DIR 기준 상대 경로(슬래시), 아니면 nil.
---정규화한 경로를 대소문자 없이(ASCII) 견주고, 맞지 않으면 양쪽 실제 경로로 다시 견준다
---(정션·심볼릭 링크를 거친 경로, 비ASCII 글자의 대소문자만 다른 경로: NTFS는 같은 파일).
---@param path string
---@param dir string
---@return string|nil
function M.in_dir(path, dir)
  if type(path) ~= "string" or path == "" or type(dir) ~= "string" or dir == "" then
    return nil
  end
  local function under(p, d)
    d = d:gsub("/+$", "")
    local prefix = d:lower() .. "/"
    if #p > #prefix and p:lower():sub(1, #prefix) == prefix then
      return p:sub(#prefix + 1)
    end
    return nil
  end
  local rel = under(vim.fs.normalize(M.normalize(path)), vim.fs.normalize(M.normalize(dir)))
  if rel then
    return rel
  end
  local real_file, real_dir = real_path(M.normalize(path)), real_path(M.normalize(dir))
  if real_file and real_dir then
    return under(real_file, real_dir)
  end
  return nil
end

return M
