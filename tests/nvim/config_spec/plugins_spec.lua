-- lua/plugins/*.lua와 lazy-lock.json (설계 J절 plugins, D·E절).
local H = require('helpers')

-- 시작 때 불러와도 되는 것 (나머지는 모두 지연)
local EAGER = { ['lazy.nvim'] = true, ['tokyonight.nvim'] = true }

local function spec_plugins()
  return require('lazy.core.config').spec.plugins
end

local function read_lock()
  local text = H.read_bytes(vim.fn.stdpath('config') .. '/lazy-lock.json')
  return vim.json.decode(text), text
end

local function nonempty(v)
  if type(v) == 'table' then
    return next(v) ~= nil
  end
  return v ~= nil
end

return {
  {
    name = 'the plugin set is the designed one',
    fn = function()
      local names = vim.tbl_keys(spec_plugins())
      table.sort(names)
      H.eq(names, {
        'blink.cmp', 'conform.nvim', 'fzf-lua', 'gitsigns.nvim', 'lazy.nvim', 'lazydev.nvim', 'mason.nvim',
        'mini.nvim', 'nvim-lspconfig', 'nvim-treesitter', 'oil.nvim', 'render-markdown.nvim',
        'tokyonight.nvim', 'which-key.nvim', 'work-kb',
      })
    end,
  },
  {
    name = 'every plugin is lazy with a trigger (except lazy.nvim and the colorscheme)',
    fn = function()
      local bad = {}
      for name, p in pairs(spec_plugins()) do
        if not EAGER[name] then
          local trigger = nonempty(p.event) or nonempty(p.cmd) or nonempty(p.keys) or nonempty(p.ft)
          if p.lazy == false or not trigger then
            table.insert(bad, name)
          end
          H.ok(p.build == nil or p.build == false, name .. ': build 단계가 있음')
          H.ok(p.enabled == nil, name .. ': enabled를 쓰지 않는다 (잠금 파일이 PC마다 달라짐)')
        end
      end
      table.sort(bad)
      H.eq(bad, {})
      H.eq(spec_plugins()['tokyonight.nvim'].lazy, false)
    end,
  },
  {
    name = 'every remote plugin has a 40-hex lockfile entry and the local one has none',
    fn = function()
      local lock, text = read_lock()
      local remote = {}
      for name, p in pairs(spec_plugins()) do
        if p.dir and p._ and p._.is_local then
          H.eq(lock[name], nil, name .. ': 로컬 플러그인이 잠금 파일에 있음')
        else
          remote[name] = true
          local entry = lock[name]
          H.ok(type(entry) == 'table', name .. ': 잠금 항목 없음')
          H.ok(type(entry.commit) == 'string' and entry.commit:match('^%x+$') and #entry.commit == 40, name .. ': ' .. vim.inspect(entry))
          H.ok(type(entry.branch) == 'string' and entry.branch ~= '', name)
        end
      end
      for name in pairs(lock) do
        H.ok(remote[name], '잠금 파일에만 있는 항목: ' .. name)
      end
      H.ok(not text:find('\r', 1, true), 'CRLF')
      H.ok(not text:find('^\239\187\191'), 'BOM')
    end,
  },
  {
    name = 'installed plugins sit at their lockfile commits',
    fn = function()
      H.eq(require('config.lazy').mismatches(), {})
    end,
  },
  {
    name = 'tools.lock.json pins the mason tools',
    fn = function()
      local text = H.read_bytes(vim.fn.stdpath('config') .. '/tools.lock.json')
      local tools = vim.json.decode(text)
      local names = vim.tbl_keys(tools.tools)
      table.sort(names)
      H.eq(names, { 'lua-language-server', 'pyright', 'ruff', 'stylua' })
      for name, version in pairs(tools.tools) do
        H.ok(type(version) == 'string' and version:match('^v?%d+%.%d+') ~= nil, name .. ' ' .. tostring(version))
      end
    end,
  },
  {
    name = 'each plugin loads without an error',
    fn = function()
      -- work-kb는 서버 없이 (실제 kb.py serve를 띄우지 않는다)
      local wk = require('lazy.core.config').plugins['work-kb']
      local real_opts = wk.opts
      H.patch(wk, 'opts', function(...)
        local o = real_opts(...)
        o.server = { enable = false }
        return o
      end)
      local errors = {}
      local real_notify = vim.notify
      H.patch(vim, 'notify', function(msg, level, opts)
        if level and level >= vim.log.levels.ERROR then
          table.insert(errors, tostring(msg))
        end
        return real_notify(msg, level, opts)
      end)
      local names = vim.tbl_keys(spec_plugins())
      table.sort(names)
      for _, name in ipairs(names) do
        local ok, err = pcall(require('lazy').load, { plugins = { name } })
        H.ok(ok, name .. ': ' .. tostring(err))
        H.ok(require('lazy.core.config').plugins[name]._.loaded ~= nil, name .. ' 안 불러옴')
      end
      H.eq(errors, {})
      -- 대표 모듈
      for _, mod in ipairs({ 'which-key', 'mini.icons', 'mini.statusline', 'oil', 'fzf-lua', 'gitsigns', 'mason',
        'lazydev', 'blink.cmp', 'conform', 'render-markdown', 'work-kb' }) do
        H.ok(package.loaded[mod] ~= nil, mod .. ' 모듈 없음')
      end
    end,
  },
  {
    name = 'blink.cmp uses its prebuilt Rust fuzzy library',
    fn = function()
      local dir = require('lazy.core.config').plugins['blink.cmp'].dir
      local dll = dir .. '/target/release/blink_cmp_fuzzy.dll'
      local lib = dir .. '/target/release/libblink_cmp_fuzzy.dll'
      if not (H.exists(dll) or H.exists(lib)) then
        H.not_run('blink.cmp 바이너리가 아직 없음 (처음 불러올 때 받는다): ' .. dll)
      end
      require('lazy').load({ plugins = { 'blink.cmp' } })
      local fuzzy = require('blink.cmp.fuzzy')
      H.ok(vim.wait(5000, function()
        return fuzzy.implementation_type == 'rust'
      end, 20), 'blink.cmp가 Lua 구현을 씀: ' .. tostring(fuzzy.implementation_type))
    end,
  },
  {
    name = 'markdown never loads the LSP stack or the formatter config',
    fn = function()
      local spec = spec_plugins()
      for _, name in ipairs({ 'nvim-lspconfig', 'lazydev.nvim' }) do
        local events = table.concat(spec[name].event or {}, ' ')
        H.not_contains(events, 'markdown', name)
        H.not_contains(events, '*.md', name)
        H.ok(not spec[name].ft, name .. ': ft를 쓰지 않는다 (lazy의 FileType 재발사)')
      end
      H.ok(not spec['render-markdown.nvim'].ft, 'render-markdown: ft를 쓰지 않는다')
    end,
  },
}
