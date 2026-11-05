-- 시작 때 등록하는 자동 명령 (등록만 하고 일은 이벤트 때 한다).
local api = vim.api
local group = api.nvim_create_augroup('config', { clear = true })

-- kb.py가 파일을 다시 쓰므로 돌아올 때 디스크를 확인한다
api.nvim_create_autocmd({ 'FocusGained', 'BufEnter', 'TermLeave' }, {
  group = group,
  callback = function()
    if vim.bo.buftype == '' and api.nvim_get_mode().mode ~= 'c' then
      pcall(vim.cmd.checktime)
    end
  end,
})

-- 파서가 있는 파일 형식은 treesitter로 칠한다. 이 자동 명령은 init 때 등록되므로 `syntax on`이
-- 만드는 syntaxset 자동 명령보다 먼저 돌고, 여기서 켜면 b:ts_highlight가 서서 regex 문법 파일
-- (syntax/markdown.vim 등, markdown은 html·css·yaml까지 불러 15ms 안팎)을 읽지 않는다.
-- 런타임 ftplugin(markdown, lua, help, query)이 다시 start()를 불러도 아무 일도 하지 않는다.
-- 내장 파서(c, lua, markdown, markdown_inline, query, vim, vimdoc)와 nvim-data/site/parser의 것만 쓴다.
api.nvim_create_autocmd('FileType', {
  group = group,
  callback = function(ev)
    local ft = ev.match
    if vim.treesitter.highlighter.active[ev.buf] then
      return
    end
    local lang = vim.treesitter.language.get_lang(ft) or ft
    local ok, added = pcall(vim.treesitter.language.add, lang)
    if ok and added then
      pcall(vim.treesitter.start, ev.buf, lang)
    end
  end,
})

api.nvim_create_autocmd('TextYankPost', {
  group = group,
  callback = function()
    pcall(vim.hl.on_yank, { timeout = 150 })
  end,
})

-- Neovide: 서버에 붙을 때(neovide --server)는 init 때 vim.g.neovide가 없으므로 UIEnter에서 다시 적용한다.
-- apply()는 붙을 때마다, IME 자동 명령(ime.setup)은 한 번만.
local ime_ready = false
api.nvim_create_autocmd('UIEnter', {
  group = group,
  callback = function()
    if not vim.g.neovide then
      return
    end
    local ok, neovide = pcall(require, 'config.neovide')
    if ok and type(neovide) == 'table' and neovide.apply then
      pcall(neovide.apply)
    end
    if not ime_ready then
      local ok_ime, ime = pcall(require, 'config.ime')
      if ok_ime and type(ime) == 'table' and ime.setup then
        ime_ready = pcall(ime.setup)
      end
    end
  end,
})

-- :KbStartup — lazy.nvim 통계와 백그라운드 작업 기록
api.nvim_create_user_command('KbStartup', function()
  require('config.background').report()
end, { desc = '시작 통계와 백그라운드 작업 기록' })
