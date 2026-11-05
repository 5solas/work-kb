-- git: 줄 옆 표시와 hunk 키 (버퍼마다).
return {
  {
    'lewis6991/gitsigns.nvim',
    event = 'VeryLazy',
    opts = {
      on_attach = function(buf)
        local gs = require('gitsigns')
        local function map(mode, lhs, rhs, desc)
          vim.keymap.set(mode, lhs, rhs, { buffer = buf, desc = desc })
        end
        map('n', ']h', function()
          gs.nav_hunk('next')
        end, 'Next hunk')
        map('n', '[h', function()
          gs.nav_hunk('prev')
        end, 'Prev hunk')
        map('n', '<leader>gs', gs.stage_hunk, 'Stage hunk')
        map('n', '<leader>gr', gs.reset_hunk, 'Reset hunk')
        map('n', '<leader>gp', gs.preview_hunk, 'Preview hunk')
        map('n', '<leader>gb', function()
          gs.blame_line({ full = true })
        end, 'Blame line')
      end,
    },
  },
}
