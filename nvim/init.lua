-- work-kb Neovim 설정 (Neovim 0.12, Windows). 구조는 docs/NVIM.md.
-- 첫 화면 전에는 lazy.nvim과 색 테마만 불러온다. 키와 백그라운드 작업은 User VeryLazy 뒤.
vim.loader.enable()
vim.g.mapleader, vim.g.maplocalleader = ' ', ','

require('config.options')
local lazy_ok, lazy_err = pcall(require, 'config.lazy') -- 플러그인이 없어도 설정은 돈다
require('config.autocmds')

vim.api.nvim_create_autocmd('User', {
  pattern = 'VeryLazy',
  once = true,
  callback = function()
    vim.opt.clipboard = 'unnamedplus'
    require('config.keymaps')
    require('config.background').start()
  end,
})

-- 알림은 첫 화면 뒤에 (시작 중 Press ENTER를 피한다)
local warnings = {}
if not lazy_ok then
  table.insert(warnings, 'lazy.nvim을 쓰지 못했습니다 (플러그인 없이 시작): ' .. tostring(lazy_err))
end
local settings_warning = require('config.settings').warning
if settings_warning then
  table.insert(warnings, settings_warning)
end
if #warnings > 0 then
  vim.schedule(function()
    for _, msg in ipairs(warnings) do
      vim.notify(msg, vim.log.levels.WARN)
    end
  end)
end

-- Neovide: 여기서 한 번, UIEnter에서 다시 (서버에 붙는 경우; autocmds.lua). IME 전환은 UIEnter에서 켠다.
if vim.g.neovide then
  local ok, neovide = pcall(require, 'config.neovide')
  if ok and type(neovide) == 'table' and neovide.apply then
    pcall(neovide.apply)
  end
end
