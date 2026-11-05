-- PC별 설정: 기본값 위에 lua/local.lua(.gitignore 대상, 예: lua/local.example.lua)를 깊게 합친다.
-- require('config.settings')는 합친 표 자체다 (예: require('config.settings').neovide.scale).
-- local.lua가 없거나 깨졌으면 경고 한 번(init.lua가 알림) 뒤 기본값을 쓴다. 오류는 내지 않는다.
-- root는 local.lua에 없으면 처음 읽을 때 정한다 (설정 폴더의 실제 경로의 부모 = 저장소).
-- 표에 없는 이름 중 defaults·warning·merge·reload는 메타테이블이 돌려준다 (값이 아니라 도구).

local DEFAULTS = {
  root = nil, -- 코드 루트 (scripts/kb.py가 있는 폴더). nil = 이 설정 폴더(정션이면 실제 경로)의 부모
  vault = nil, -- 볼트 루트. nil = 코드 루트
  python = nil, -- nil = work-kb가 <root>/.venv/Scripts/python.exe → PATH의 python → py -3 순서로 찾음
  colorscheme = 'tokyonight-night',
  ime = { auto = true, langmap = false },
  background = { enabled = true, server = true, prewarm = true },
  neovide = { scale = 1.0, scroll_animation = 0.08 },
  keep_builtins = {}, -- 끄지 않을 내장 플러그인 이름 (예: { 'matchit' })
}

local function copy(v)
  if type(v) ~= 'table' then
    return v
  end
  local out = {}
  for k, x in pairs(v) do
    out[k] = copy(x)
  end
  return out
end

---BASE 위에 USER를 깊게 얹는다. 둘 다 사전인 표만 합치고, 목록과 값은 USER 것으로 바꾼다.
local function merge(base, user)
  local out = copy(base)
  for k, v in pairs(user) do
    local dict_v = type(v) == 'table' and (next(v) == nil or not vim.islist(v))
    local dict_base = type(out[k]) == 'table' and next(out[k]) ~= nil and not vim.islist(out[k])
    if dict_v and dict_base then
      out[k] = merge(out[k], v)
    else
      out[k] = copy(v)
    end
  end
  return out
end

---모듈 MODNAME(기본 'local')을 읽어 합친 표와 경고(문제가 없으면 nil)를 돌려준다.
---@param modname? string
---@return table settings, string? warning
local function load(modname)
  modname = modname or 'local'
  package.loaded[modname] = nil
  local ok, user
  -- 없는 모듈을 require하면 runtimepath 전체를 훑어 3ms 가까이 든다: 'local'은 설정 폴더의 파일부터 본다
  -- (다른 이름은 테스트용: package.path 어디에 있어도 된다)
  local lua_dir = vim.fn.stdpath('config') .. '/lua/'
  if modname ~= 'local' or vim.uv.fs_stat(lua_dir .. 'local.lua') or vim.uv.fs_stat(lua_dir .. 'local/init.lua') then
    ok, user = pcall(require, modname)
  else
    ok, user = false, "module '" .. modname .. "' not found"
  end
  local warning
  if not ok then
    local msg = tostring(user)
    if msg:find("module '" .. modname .. "' not found", 1, true) then
      warning = string.format('lua/%s.lua가 없습니다: 기본값을 씁니다 (lua/local.example.lua를 복사하세요)', modname)
    else
      warning = string.format('lua/%s.lua를 읽지 못했습니다: 기본값을 씁니다\n%s', modname, msg)
    end
    user = {}
  elseif type(user) ~= 'table' then
    warning = string.format('lua/%s.lua는 표를 돌려줘야 합니다 (받은 값: %s): 기본값을 씁니다', modname, type(user))
    user = {}
  end
  return merge(DEFAULTS, user), warning
end

local settings, warning = load()

setmetatable(settings, {
  __index = function(t, key)
    if key == 'root' then
      local root = require('config.util').repo_root()
      rawset(t, 'root', root)
      return root
    elseif key == 'warning' then
      return warning
    elseif key == 'defaults' then
      return copy(DEFAULTS)
    elseif key == 'merge' then
      return merge
    elseif key == 'reload' then
      ---MODNAME(기본 'local')을 다시 읽어 이 표를 제자리에서 바꾼다 (테스트용). 표를 돌려준다.
      return function(modname)
        local fresh, w = load(modname)
        for k in pairs(t) do
          rawset(t, k, nil)
        end
        for k, v in pairs(fresh) do
          rawset(t, k, v)
        end
        warning = w
        return t
      end
    end
    return nil
  end,
})

return settings
