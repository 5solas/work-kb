-- markdown: 런타임 ftplugin보다 먼저 돈다 (설정 폴더가 runtimepath 맨 앞).
-- treesitter(내장 markdown 파서)로 칠하고 regex syntax/markdown.vim은 읽지 않는다.
-- b:did_ftplugin을 세워 런타임 ftplugin/markdown.vim(과 그것이 부르는 html ftplugin)을 건너뛰고,
-- 거기서 쓸모 있는 설정만 아래에 옮겨 둔다. 런타임 ftplugin/markdown.lua(gO, ]], [[)는 그대로 돈다.
if vim.b.did_ftplugin then
  return
end
vim.b.did_ftplugin = 1

pcall(vim.treesitter.start)

local bo = vim.bo
bo.comments = 'fb:*,fb:-,fb:+,n:>'
bo.commentstring = '<!-- %s -->'
vim.opt_local.formatoptions:remove({ 'r', 'o' })
vim.opt_local.formatoptions:append('tcqln')
bo.formatlistpat = [=[^\s*\d\+\.\s\+\|^\s*[-*+]\s\+\|^\[^\ze[^\]]\+\]:\&^.\{4\}]=]

vim.b.undo_ftplugin = (vim.b.undo_ftplugin or '') .. '\n setl com< cms< fo< flp<'
