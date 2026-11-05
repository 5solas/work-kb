-- 서식: 손으로 부를 때만 (<leader>cf). 저장할 때 고치지 않고, markdown은 고치지 않는다.
return {
  {
    'stevearc/conform.nvim',
    cmd = 'ConformInfo',
    keys = {
      {
        '<leader>cf',
        function()
          if vim.bo.filetype == 'markdown' then
            vim.notify('markdown은 서식을 고치지 않습니다', vim.log.levels.INFO)
            return
          end
          require('conform').format({ async = true, lsp_format = 'fallback' })
        end,
        mode = { 'n', 'x' },
        desc = 'Format',
      },
    },
    opts = {
      formatters_by_ft = {
        lua = { 'stylua' },
        python = { 'ruff_organize_imports', 'ruff_format' },
      },
    },
  },
}
