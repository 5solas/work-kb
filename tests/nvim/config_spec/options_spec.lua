-- config/options.lua (설계 J절 options, F절 5·6·7·9).
local H = require('helpers')

return {
  {
    name = 'file formats, encodings and file options',
    fn = function()
      H.eq(vim.o.fileformats, 'unix,dos')
      H.eq(vim.o.fileencodings, 'ucs-bom,utf-8,cp949,latin1')
      H.eq(vim.o.swapfile, false)
      H.eq(vim.o.autoread, true)
      H.eq(vim.o.undofile, true)
      H.eq(vim.o.spelllang, 'en,cjk')
      H.eq(vim.o.timeoutlen, 300)
      H.eq(vim.g.mapleader, ' ')
      H.eq(vim.g.maplocalleader, ',')
    end,
  },
  {
    name = 'providers are disabled',
    fn = function()
      for _, p in ipairs({ 'python3', 'node', 'perl', 'ruby' }) do
        H.eq(vim.g['loaded_' .. p .. '_provider'], 0, p)
      end
      H.eq(vim.g.loaded_netrwPlugin, 1)
    end,
  },
  {
    name = 'clipboard uses the win32yank.exe next to nvim.exe',
    fn = function()
      local cb = vim.g.clipboard
      H.ok(type(cb) == 'table', 'vim.g.clipboard 없음')
      H.eq(cb.name, 'win32yank')
      local exe = cb.copy['+'][1]
      H.ok(H.exists(exe), 'win32yank 없음: ' .. tostring(exe))
      H.eq(vim.fs.normalize(vim.fs.dirname(exe)):lower(), vim.fs.normalize(vim.fs.dirname(vim.v.progpath)):lower())
      H.eq(cb.paste['+'], { exe, '-o', '--lf' })
    end,
  },
  {
    name = 'shell stays cmd.exe',
    fn = function()
      H.ok(vim.o.shell:lower():find('cmd%.exe$') ~= nil, vim.o.shell)
    end,
  },
  {
    name = 'mason/bin is on PATH without loading mason',
    fn = function()
      local bin = (vim.fn.stdpath('data') .. '\\mason\\bin'):gsub('/', '\\')
      H.contains(vim.env.PATH, bin .. ';')
      H.eq(vim.env.PATH:sub(1, #bin), bin)
    end,
  },
  {
    name = 'shada and the static statusline',
    fn = function()
      H.ok(vim.o.shada:find("^'100,<50,s10,h") ~= nil, vim.o.shada)
      if vim.env.TEMP and not vim.env.TEMP:find(',', 1, true) then
        H.contains(vim.o.shada, ',r' .. vim.env.TEMP)
      end
      H.eq(vim.o.laststatus, 3)
    end,
  },
  {
    name = 'disabled builtin plugins are not loaded, kept ones are',
    fn = function()
      local disabled = require('config.lazy').disabled_plugins
      local keep = require('config.settings').keep_builtins or {}
      H.eq(disabled, vim.tbl_filter(function(n)
        return not vim.tbl_contains(keep, n)
      end, { 'gzip', 'tarPlugin', 'zipPlugin', 'netrwPlugin', 'tutor', 'rplugin', 'man',
        'spellfile', 'shada', 'osc52', 'matchit' }))
      local scripts = vim.fn.execute('scriptnames'):gsub('\\', '/')
      for _, name in ipairs(disabled) do
        H.not_contains(scripts, '/plugin/' .. name .. '.', name)
      end
      for _, name in ipairs({ 'matchparen', 'editorconfig', 'net' }) do
        H.contains(scripts, '/runtime/plugin/' .. name .. '.', name)
      end
    end,
  },
}
