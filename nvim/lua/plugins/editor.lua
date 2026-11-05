-- 편집 도구: 키 안내, mini 묶음, 파일 탐색(oil).
return {
  {
    'folke/which-key.nvim',
    event = 'VeryLazy',
    opts = {
      preset = 'helix',
      delay = 300,
      spec = {
        { '<leader>f', group = 'find' },
        { '<leader>g', group = 'git' },
        { '<leader>c', group = 'code' },
        { '<leader>b', group = 'buffer' },
        { '<leader>u', group = 'ui' },
        { '<leader>k', group = 'work-kb' },
        { '<leader>q', group = 'quit' },
      },
    },
  },
  {
    'nvim-mini/mini.nvim',
    version = false,
    event = 'VeryLazy',
    init = function()
      -- nvim-web-devicons를 찾는 플러그인에는 mini.icons를 흉내 내 준다
      package.preload['nvim-web-devicons'] = function()
        require('mini.icons').mock_nvim_web_devicons()
        return package.loaded['nvim-web-devicons']
      end
    end,
    config = function()
      require('mini.icons').setup()
      require('mini.statusline').setup()
      require('mini.pairs').setup()
      require('mini.ai').setup()
      require('mini.bufremove').setup()
      require('mini.surround').setup({
        mappings = {
          add = 'gsa', delete = 'gsd', replace = 'gsr', find = 'gsf',
          find_left = 'gsF', highlight = 'gsh', update_n_lines = 'gsn',
        },
      })
    end,
  },
  {
    'stevearc/oil.nvim',
    cmd = 'Oil',
    keys = {
      { '-', '<cmd>Oil<cr>', desc = 'Parent dir' },
      { '<leader>e', '<cmd>Oil<cr>', desc = 'Explorer' },
    },
    init = function()
      -- `nvim <폴더>`로 열면 바로 oil을 불러온다 (netrw를 끈 대신)
      if vim.fn.argc(-1) == 1 then
        local st = vim.uv.fs_stat(vim.fn.argv(0) --[[@as string]])
        if st and st.type == 'directory' then
          require('lazy').load({ plugins = { 'oil.nvim' } })
        end
      end
    end,
    opts = {
      default_file_explorer = true,
      delete_to_trash = true,
      view_options = { show_hidden = true },
    },
  },
}
