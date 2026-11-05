-- 찾기: fzf-lua (설치된 fzf, shim을 건너뛴 실제 exe).
return {
  {
    'ibhagwan/fzf-lua',
    cmd = 'FzfLua',
    keys = {
      { '<leader><space>', '<cmd>FzfLua files<cr>', desc = 'Files' },
      { '<leader>fg', '<cmd>FzfLua live_grep<cr>', desc = 'Grep' },
      { '<leader>fb', '<cmd>FzfLua buffers<cr>', desc = 'Buffers' },
      { '<leader>fr', '<cmd>FzfLua oldfiles<cr>', desc = 'Recent' },
      { '<leader>fh', '<cmd>FzfLua helptags<cr>', desc = 'Help' },
      { '<leader>f.', '<cmd>FzfLua resume<cr>', desc = 'Resume' },
    },
    init = function()
      -- 처음 vim.ui.select를 부를 때 fzf-lua를 불러와 바꿔 끼운다
      vim.ui.select = function(...)
        require('fzf-lua').register_ui_select()
        return vim.ui.select(...)
      end
    end,
    opts = function()
      local util = require('config.util')
      return {
        fzf_bin = util.unshim('fzf'),
        defaults = { file_icons = 'mini' },
        winopts = { height = 0.85, width = 0.85 },
        files = {
          fd_opts = '--color=never --type f --hidden --exclude .git --exclude .venv'
            .. ' --exclude .venv313 --exclude _trash --exclude .state --exclude wheels',
        },
      }
    end,
  },
}
