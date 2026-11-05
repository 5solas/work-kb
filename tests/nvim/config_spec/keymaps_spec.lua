-- 키: config/keymaps.lua(VeryLazy 뒤)와 lazy 플러그인 keys, work-kb 접두 키 (설계 J절).
local H = require('helpers')

local function leader(lhs)
  return (lhs:gsub('<[Ll]eader>', ' '))
end

---config/keymaps.lua를 다시 돌려 vim.keymap.set 호출을 모은다 (실제 키는 만들지 않는다).
local function keymaps_calls()
  local calls = {}
  local real = vim.keymap.set
  vim.keymap.set = function(mode, lhs, _, opts)
    for _, m in ipairs(type(mode) == 'table' and mode or { mode }) do
      table.insert(calls, { mode = m, lhs = lhs, desc = opts and opts.desc })
    end
  end
  local ok, err = pcall(dofile, vim.fn.stdpath('config') .. '/lua/config/keymaps.lua')
  vim.keymap.set = real
  assert(ok, err)
  return calls
end

---lazy 플러그인 spec의 keys (모드별).
local function lazy_keys()
  local Keys = require('lazy.core.handler.keys')
  local out = {}
  for name, plugin in pairs(require('lazy.core.config').spec.plugins) do
    if plugin.keys then
      local keys = type(plugin.keys) == 'function' and plugin.keys(plugin, {}) or plugin.keys
      for _, k in pairs(Keys.resolve(keys)) do
        table.insert(out, { mode = k.mode, lhs = k.lhs, desc = k.desc, plugin = name })
      end
    end
  end
  return out
end

local function fire_very_lazy()
  -- 백그라운드 작업 줄은 끈다 (work-kb와 kb.py serve를 띄우지 않게)
  H.patch(require('config.settings'), 'background', { enabled = false })
  if not vim.g.did_very_lazy then
    vim.g.did_very_lazy = true
    vim.api.nvim_exec_autocmds('User', { pattern = 'VeryLazy', modeline = false })
  end
end

---work-kb 플러그인을 서버 없이 불러오도록 opts를 바꾼다.
local function work_kb_without_server()
  local plugin = require('lazy.core.config').plugins['work-kb']
  local real = plugin.opts
  H.patch(plugin, 'opts', function(...)
    local o = real(...)
    o.server = { enable = false }
    return o
  end)
end

-- 새 nvim(전체 설정)에서 비주얼 모드로 접두 키를 누르는 스크립트. 결과를 한 줄 JSON으로 찍는다.
local VISUAL_CHILD = [=[
local plugin = require('lazy.core.config').plugins['work-kb']
local real_opts = plugin.opts
plugin.opts = function(...) local o = real_opts(...); o.server = { enable = false }; return o end
local got = {}
package.loaded['work-kb.capture'] = {
  open = function(text) table.insert(got, { 'open', text }) end,
  inbox = function(text) table.insert(got, { 'inbox', text }) end,
  capture = function() table.insert(got, { 'capture' }) end,
}
local before = plugin._.loaded ~= nil
vim.api.nvim_buf_set_lines(0, 0, -1, false, { '첫 줄 한글', '둘째 줄' })
vim.api.nvim_win_set_cursor(0, { 1, 0 })
vim.api.nvim_feedkeys(vim.keycode('vj$<Space>kc'), 'mx', false)
local after = plugin._.loaded ~= nil
vim.api.nvim_feedkeys(vim.keycode('ggVj<Space>ki'), 'mx', false)
io.stdout:write('CHILD ' .. vim.json.encode({ before = before, after = after, got = got, mode = vim.api.nvim_get_mode().mode }) .. '\n')
io.stdout:flush()
os.exit(0)
]=]

return {
  {
    name = 'visual mode: prefix stub loads work-kb and the x map gets the selection (fresh nvim)',
    fn = function()
      local script = H.tmp() .. '/keymaps_visual_child.lua'
      H.write_bytes(script, VISUAL_CHILD)
      local init = vim.fn.stdpath('config') .. '/init.lua'
      local res = vim.system({ vim.v.progpath, '--headless', '-u', init, '-l', script }, { text = true }):wait(60000)
      local line = (res.stdout or ''):match('CHILD (%b{})')
      H.ok(line, 'child 출력 없음: ' .. tostring(res.stdout) .. tostring(res.stderr))
      local r = vim.json.decode(line)
      H.eq(r.before, false, '처음부터 불러와져 있었음')
      H.eq(r.after, true, '비주얼 모드 <leader>k가 work-kb를 불러오지 않음')
      H.eq(r.got, { { 'open', '첫 줄 한글\n둘째 줄' }, { 'inbox', '첫 줄 한글\n둘째 줄' } })
      H.eq(r.mode, 'n')
    end,
  },
  {
    name = 'prefix stub loads work-kb and the plugin map receives the re-fed key',
    fn = function()
      local plugins = require('lazy.core.config').plugins
      H.eq(plugins['work-kb']._.loaded, nil, 'work-kb가 이미 불러와짐 (앞 스펙이 불렀음)')
      local stub = vim.fn.maparg(' k', 'n', false, true)
      H.eq(stub.desc, '+work-kb', '접두 자리 표시가 없음')
      H.eq(vim.fn.maparg(' k', 'x', false, true).desc, '+work-kb', 'x 모드 자리 표시가 없음')
      H.eq(vim.fn.maparg(' kc', 'n'), '', '불러오기 전에 <leader>kc가 있음')
      work_kb_without_server()
      local hits = {}
      H.patch(package.loaded, 'work-kb.capture', {
        capture = function() table.insert(hits, 'capture') end,
        inbox = function() table.insert(hits, 'inbox') end,
      })
      H.patch(package.loaded, 'work-kb.misc', {
        status = function() table.insert(hits, 'status') end,
        link = function() table.insert(hits, 'link') end,
      })
      vim.api.nvim_feedkeys(vim.keycode('<Space>kc'), 'mx', false)
      H.ok(plugins['work-kb']._.loaded ~= nil, '<leader>k가 work-kb를 불러오지 않음')
      H.eq(hits, { 'capture' }, '다시 넣은 키가 플러그인 키에 닿지 않음')
      H.eq(vim.fn.maparg(' kc', 'n', false, true).desc, 'work-kb: capture')
      vim.api.nvim_feedkeys(vim.keycode('<Space>k?'), 'mx', false)
      H.eq(hits, { 'capture', 'status' })
      H.eq(vim.fn.exists(':WorkKb'), 2)
    end,
  },
  {
    name = 'work-kb opts come from config.settings',
    fn = function()
      local plugin = require('lazy.core.config').spec.plugins['work-kb']
      local s = require('config.settings')
      local o = plugin.opts(plugin, {})
      H.eq(o.prefix, '<leader>k')
      H.eq(o.root, s.root)
      H.eq(o.vault, s.vault)
      H.eq(o.python, s.python)
      H.eq(o.server, { enable = true })
      H.eq(plugin.main, 'work-kb')
      H.eq(vim.fs.normalize(plugin.dir), vim.fs.normalize(vim.fn.stdpath('config') .. '/dev/work-kb'))
      H.ok(H.exists(s.root .. '/scripts/kb.py'), 'root에 scripts/kb.py가 없음: ' .. s.root)
    end,
  },
  {
    name = 'leader maps exist after VeryLazy',
    fn = function()
      fire_very_lazy()
      for _, lhs in ipairs({ ' bd', ' bn', ' bp', ' cd', ' uw', ' us', ' un', ' uS', ' ui', ' w', ' qq' }) do
        H.ok(vim.fn.maparg(lhs, 'n') ~= '', 'n ' .. lhs .. ' 없음')
      end
      -- lazy keys 자리 표시 (플러그인을 불러오기 전) 또는 플러그인이 만든 실제 키
      for _, lhs in ipairs({ '  ', ' fg', ' fb', ' fr', ' fh', ' f.', ' e', ' cf', '-' }) do
        H.ok(vim.fn.maparg(lhs, 'n') ~= '' or vim.fn.maparg(lhs, 'n', false, true).callback ~= nil, 'n ' .. lhs .. ' 없음')
      end
      H.eq(vim.o.clipboard, 'unnamedplus')
    end,
  },
  {
    name = 'the config defines no <leader>k* map besides the lazy stub',
    fn = function()
      for _, call in ipairs(keymaps_calls()) do
        H.ok(not leader(call.lhs):find('^ k'), 'keymaps.lua가 정함: ' .. call.lhs)
      end
      for _, k in ipairs(lazy_keys()) do
        if leader(k.lhs):find('^ k') then
          H.eq({ k.plugin, k.lhs }, { 'work-kb', '<leader>k' }, 'lazy keys')
        end
      end
      -- 실제 키: <leader>k 아래는 자리 표시나 work-kb 플러그인 것뿐
      for _, mode in ipairs({ 'n', 'x' }) do
        for _, map in ipairs(vim.api.nvim_get_keymap(mode)) do
          if map.lhs:find('^ k') or map.lhs:find('^<Space>k') then
            local desc = map.desc or ''
            H.ok(desc == '+work-kb' or desc:find('^work%-kb: '), mode .. ' ' .. map.lhs .. ' ' .. desc)
          end
        end
      end
    end,
  },
  {
    name = 'no duplicate lhs between keymaps.lua and plugin keys',
    fn = function()
      local seen, dups = {}, {}
      local all = keymaps_calls()
      vim.list_extend(all, lazy_keys())
      for _, k in ipairs(all) do
        local id = k.mode .. ':' .. vim.keycode(k.lhs)
        if seen[id] then
          table.insert(dups, string.format('%s %s (%s, %s)', k.mode, k.lhs, seen[id], k.plugin or 'keymaps.lua'))
        end
        seen[id] = k.plugin or 'keymaps.lua'
      end
      H.eq(dups, {})
    end,
  },
}
