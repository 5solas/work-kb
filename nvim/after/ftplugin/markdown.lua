-- markdown 마지막 덮어쓰기 (모든 ftplugin 뒤).
local wo, bo = vim.wo, vim.bo
wo.wrap = true
wo.linebreak = true
wo.conceallevel = 2
wo.spell = false
bo.textwidth = 0
bo.expandtab = true
bo.shiftwidth = 2
bo.tabstop = 2
bo.softtabstop = 2
