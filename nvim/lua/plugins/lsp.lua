-- LSP: lua·python 파일에서만 불러온다 (markdown은 LSP를 부르지 않는다).
-- 서버(lua-language-server, pyright, ruff)는 mason이 nvim-data/mason/bin에 둔다 (tools.lock.json).
return {
  {
    -- BufReadPre로 먼저 불러온다 (ft만 쓰면 lazy의 FileType 재발사가 ftplugin을 되돌리고 다시 읽는다)
    'neovim/nvim-lspconfig',
    event = { 'BufReadPre *.lua', 'BufReadPre *.py', 'BufNewFile *.lua', 'BufNewFile *.py', 'FileType lua,python' },
    config = function()
      vim.lsp.config('lua_ls', {
        settings = { Lua = { workspace = { checkThirdParty = false }, telemetry = { enable = false } } },
      })
      vim.lsp.config('pyright', {
        before_init = function(_, c)
          local py = (c.root_dir or vim.uv.cwd()) .. '/.venv/Scripts/python.exe'
          if vim.uv.fs_stat(py) then
            c.settings = vim.tbl_deep_extend('force', c.settings or {}, { python = { pythonPath = py } })
          end
        end,
      })
      vim.lsp.config('ruff', {
        on_attach = function(client)
          client.server_capabilities.hoverProvider = false
        end,
      })
      vim.lsp.enable({ 'lua_ls', 'pyright', 'ruff' })
    end,
  },
  {
    'mason-org/mason.nvim',
    cmd = { 'Mason', 'MasonInstall', 'MasonUpdate', 'MasonUninstall', 'MasonLog' },
    opts = { PATH = 'skip' },
  },
  {
    'folke/lazydev.nvim',
    event = { 'BufReadPre *.lua', 'BufNewFile *.lua', 'FileType lua' },
    opts = {},
  },
}
