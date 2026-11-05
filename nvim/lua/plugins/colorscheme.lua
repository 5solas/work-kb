-- 색 테마: 첫 화면 전에 불러오는 유일한 플러그인 (lazy.nvim 말고).
-- `noautocmd`: lazy.nvim의 ColorSchemePre 처리기가 getcompletion('', 'color')로 모든 colors/를
-- 훑어 :colorscheme 한 번에 10ms 남짓 든다 (측정). 테마는 이미 불러왔으므로 그 처리기는 필요 없다.
return {
  {
    'folke/tokyonight.nvim',
    lazy = false,
    priority = 1000,
    opts = { style = 'night', cache = true },
    config = function(_, opts)
      require('tokyonight').setup(opts)
      local name = require('config.settings').colorscheme or 'tokyonight-night'
      if not pcall(vim.cmd, 'noautocmd colorscheme ' .. name) then
        vim.cmd('noautocmd colorscheme habamax')
      end
    end,
  },
}
