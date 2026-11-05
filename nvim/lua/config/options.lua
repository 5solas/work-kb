-- 시작 때 바로 읽는 옵션. 값만 정하고 플러그인은 부르지 않는다.
local opt, g = vim.opt, vim.g

-- 쓰지 않는 공급자 (확인 비용을 없앤다)
g.loaded_python3_provider = 0
g.loaded_node_provider = 0
g.loaded_perl_provider = 0
g.loaded_ruby_provider = 0

-- netrw는 끈다 (oil.nvim이 대신한다, gx는 vim.ui.open)
g.loaded_netrw = 1
g.loaded_netrwPlugin = 1

-- markdown ftplugin의 들여쓰기 4칸 강제를 끈다
g.markdown_recommended_style = 0

-- 클립보드: nvim.exe 옆의 win32yank.exe를 직접 쓴다 (탐지 비용 없음).
-- 'clipboard=unnamedplus'는 VeryLazy에서 켠다 (init.lua).
local yank = require('config.util').win32yank()
if yank then
  g.clipboard = {
    name = 'win32yank',
    copy = { ['+'] = { yank, '-i', '--crlf' }, ['*'] = { yank, '-i', '--crlf' } },
    paste = { ['+'] = { yank, '-o', '--lf' }, ['*'] = { yank, '-o', '--lf' } },
    cache_enabled = 0,
  }
end

-- mason이 설치한 도구를 PATH 앞에 (mason을 불러오지 않는다)
local data = vim.fn.stdpath('data') --[[@as string]]
local mason_bin = data:gsub('/', '\\') .. '\\mason\\bin'
if not (vim.env.PATH or ''):find(mason_bin, 1, true) then
  vim.env.PATH = mason_bin .. ';' .. (vim.env.PATH or '')
end

-- 파일: kb.py가 쓰는 UTF-8·LF가 기본, 옛 CRLF·cp949 파일도 읽는다
opt.fileformats = 'unix,dos'
opt.fileencodings = 'ucs-bom,utf-8,cp949,latin1'
opt.swapfile = false
opt.autoread = true -- kb.py가 파일을 다시 쓴다; checktime은 autocmds.lua
opt.undofile = true
opt.spelllang = 'en,cjk'
opt.timeoutlen = 300
opt.updatetime = 300

-- shada: 기록 100개, 레지스터 50줄, 10KiB; TEMP 아래 파일은 기억하지 않는다
local shada = "'100,<50,s10,h"
local temp = vim.env.TEMP
if temp and temp ~= '' and not temp:find(',', 1, true) then
  shada = shada .. ',r' .. temp
end
opt.shada = shada

-- 화면
opt.number = true
opt.signcolumn = 'yes'
opt.cursorline = true
opt.scrolloff = 4
opt.splitright = true
opt.splitbelow = true
opt.wrap = true
opt.linebreak = true
opt.breakindent = true
opt.showmode = false
opt.mouse = 'a'
opt.ignorecase = true
opt.smartcase = true
opt.expandtab = true
opt.shiftwidth = 2
opt.tabstop = 2
opt.softtabstop = 2
opt.confirm = true
opt.winborder = 'rounded'
opt.completeopt = 'menu,menuone,noselect,popup'
-- 첫 화면이 mini.statusline이 뜰 때 흔들리지 않게 고정 상태 줄을 둔다
opt.laststatus = 3
opt.statusline = ' %f %m%r%=%y  %l:%c '
-- shell은 기본값 cmd.exe를 그대로 둔다 (kb.py·rg 인수 따옴표 규칙이 cmd 기준)
