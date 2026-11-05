-- 완성: blink.cmp 1.x 릴리스 태그 (미리 빌드한 Windows 바이너리를 받고, 없으면 Lua 구현).
-- markdown·text·gitcommit에서는 메뉴를 저절로 띄우지 않는다 (한글 조합과 다투지 않게; <C-Space>로 연다).
return {
  {
    'saghen/blink.cmp',
    version = '1.*',
    event = { 'InsertEnter', 'CmdlineEnter' },
    opts = {
      keymap = { preset = 'default' },
      completion = {
        menu = {
          auto_show = function()
            return not vim.tbl_contains({ 'markdown', 'text', 'gitcommit' }, vim.bo.filetype)
          end,
        },
        list = { selection = { preselect = false } },
      },
      sources = { default = { 'lsp', 'path', 'snippets', 'buffer' } },
      fuzzy = { implementation = 'prefer_rust' },
      signature = { enabled = true },
    },
  },
}
