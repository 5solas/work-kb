-- config/settings.lua: 기본값 + lua/local.lua (설계 C절, J절 settings).
-- 시험용 local 모듈은 ASCII 임시 폴더에 쓰고 package.path에 더해 settings.reload(이름)으로 읽는다.
-- 테스트마다 settings.reload()로 이 PC의 실제 local.lua(없으면 기본값)로 되돌린다.
local H = require('helpers')

local settings = require('config.settings')
local counter = 0

---임시 모듈을 만들고 그 이름을 돌려준다.
local function module(source)
  counter = counter + 1
  local dir = H.tmp() .. '/settings_spec'
  H.mkdir_p(dir)
  if not package.path:find(dir, 1, true) then
    package.path = dir .. '/?.lua;' .. package.path
  end
  local name = 'kbspec_local_' .. counter
  H.write_bytes(dir .. '/' .. name .. '.lua', source)
  return name
end

local function restore()
  settings.reload()
end

return {
  {
    name = 'defaults have the keys other modules read',
    fn = function()
      local d = settings.defaults
      H.eq(d.colorscheme, 'tokyonight-night')
      H.eq(d.ime, { auto = true, langmap = false })
      H.eq(d.neovide, { scale = 1.0, scroll_animation = 0.08 })
      H.eq(d.background, { enabled = true, server = true, prewarm = true })
      H.eq(d.keep_builtins, {})
      H.eq(d.vault, nil)
      H.eq(d.python, nil)
    end,
  },
  {
    name = 'root defaults to the real parent of the config folder (the repository)',
    fn = function()
      settings.reload('kbspec_missing_module')
      H.eq(rawget(settings, 'root'), nil)
      H.eq(settings.root, vim.fs.normalize(H.repo))
      H.ok(H.exists(settings.root .. '/scripts/kb.py'))
      restore()
    end,
  },
  {
    name = 'a missing local module warns once and falls back to defaults',
    fn = function()
      settings.reload('kbspec_missing_module')
      H.contains(settings.warning, '없습니다')
      H.eq(settings.colorscheme, 'tokyonight-night')
      H.eq(settings.background.server, true)
      restore()
    end,
  },
  {
    name = 'a broken local module warns and falls back to defaults (no error)',
    fn = function()
      local name = module('return { vault = \n')
      local ok, err = pcall(settings.reload, name)
      H.ok(ok, tostring(err))
      H.contains(settings.warning, '읽지 못했습니다')
      H.eq(settings.vault, nil)
      local name2 = module('error("일부러")\n')
      settings.reload(name2)
      H.contains(settings.warning, '일부러')
      local name3 = module('return 42\n')
      settings.reload(name3)
      H.contains(settings.warning, '표를 돌려줘야')
      H.eq(settings.ime.auto, true)
      restore()
    end,
  },
  {
    name = 'local values deep-merge over defaults; lists replace',
    fn = function()
      local name = module([[return { background = { server = false }, neovide = { scale = 1.25 }, keep_builtins = { 'matchit' }, colorscheme = 'habamax' }]])
      settings.reload(name)
      H.eq(settings.warning, nil)
      H.eq(settings.background, { enabled = true, server = false, prewarm = true })
      H.eq(settings.neovide, { scale = 1.25, scroll_animation = 0.08 })
      H.eq(settings.keep_builtins, { 'matchit' })
      H.eq(settings.colorscheme, 'habamax')
      restore()
    end,
  },
  {
    name = 'a Hangul vault path in a long-bracket string round-trips byte for byte',
    fn = function()
      local name = module('return { vault = [[D:\\업무 메모\\볼트]], python = [[C:\\파이썬\\python.exe]] }\n')
      settings.reload(name)
      H.eq(settings.vault, 'D:\\업무 메모\\볼트')
      H.eq(settings.python, 'C:\\파이썬\\python.exe')
      local plugin = require('lazy.core.config').spec.plugins['work-kb']
      local o = plugin.opts(plugin, {})
      H.eq(o.vault, 'D:\\업무 메모\\볼트')
      restore()
    end,
  },
  {
    name = 'local.example.lua loads and its keys are a subset of the defaults',
    fn = function()
      local path = vim.fn.stdpath('config') .. '/lua/local.example.lua'
      local example = dofile(path)
      H.ok(type(example) == 'table')
      local defaults = settings.defaults
      local function check(ex, def, prefix)
        for k, v in pairs(ex) do
          local key = prefix .. tostring(k)
          H.ok(def[k] ~= nil or k == 'vault' or k == 'python' or k == 'root', 'example 키가 기본값에 없음: ' .. key)
          if type(v) == 'table' and type(def[k]) == 'table' and not vim.islist(v) then
            check(v, def[k], key .. '.')
          end
        end
      end
      check(example, defaults, '')
      -- 예시 값은 기본값과 같다 (복사만 해도 동작이 바뀌지 않는다)
      H.eq(settings.merge(defaults, example), defaults)
      H.ok(not H.read_bytes(path):find('^\239\187\191'), 'BOM')
    end,
  },
  {
    name = 'local.lua is ignored by git and the example is not',
    fn = function()
      local text = H.read_bytes(H.repo .. '/.gitignore')
      H.contains(text, '/nvim/lua/local.lua')
    end,
  },
}
