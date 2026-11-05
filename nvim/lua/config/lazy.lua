-- lazy.nvim: 없으면 받아서(잠금 파일의 커밋으로) 켜고 lua/plugins/*.lua를 읽는다.
-- 시작은 네트워크를 쓰지 않는다 (install.missing = false). 플러그인 설치는
--   nvim --headless "+lua require('config.lazy').install_cli()"
-- (잠금 파일 커밋으로 받고 어긋난 것을 되돌린다; M.install 참고. `Lazy! install`은 쓰지 않는다).

local M = {}

local config_dir = vim.fn.stdpath('config') --[[@as string]]
local lockfile = config_dir .. '/lazy-lock.json'
local path = vim.fn.stdpath('data') .. '/lazy/lazy.nvim'

---잠금 파일에서 NAME의 커밋 (없으면 nil).
local function locked_commit(name)
  local fd = vim.uv.fs_open(lockfile, 'r', 438)
  if not fd then
    return nil
  end
  local st = vim.uv.fs_fstat(fd)
  local data = st and vim.uv.fs_read(fd, st.size, 0) or ''
  vim.uv.fs_close(fd)
  local ok, lock = pcall(vim.json.decode, data)
  return ok and type(lock) == 'table' and lock[name] and lock[name].commit or nil
end

if not vim.uv.fs_stat(path) then
  local out = vim.fn.system({ 'git', 'clone', '--filter=blob:none', '--branch=stable',
    'https://github.com/folke/lazy.nvim.git', path })
  if vim.v.shell_error ~= 0 then
    error('lazy.nvim clone failed: ' .. out)
  end
  local commit = locked_commit('lazy.nvim')
  if commit then
    vim.fn.system({ 'git', '-C', path, 'checkout', '--quiet', commit })
  end
end
vim.opt.rtp:prepend(path)

local disabled = {
  'gzip', 'tarPlugin', 'zipPlugin', 'netrwPlugin', 'tutor', 'rplugin', 'man',
  'spellfile', 'shada', 'osc52', 'matchit',
}
local keep = require('config.settings').keep_builtins or {}
disabled = vim.tbl_filter(function(name)
  return not vim.tbl_contains(keep, name)
end, disabled)

M.disabled_plugins = disabled

require('lazy').setup({
  spec = { { import = 'plugins' } },
  defaults = { lazy = true, version = false },
  lockfile = lockfile,
  install = { missing = false, colorscheme = { 'tokyonight-night', 'habamax' } },
  checker = { enabled = false },
  change_detection = { enabled = false, notify = false },
  rocks = { enabled = false },
  git = { timeout = 300, filter = true },
  ui = { border = 'rounded' },
  performance = {
    reset_packpath = true,
    rtp = { reset = true, disabled_plugins = disabled },
  },
})

---잠금 파일대로 설치한다: 없는 플러그인은 잠금 커밋으로 받고, 있는 것은 잠금 커밋으로 되돌린다.
---`:Lazy! install`은 잠금 파일을 보지 않고 기본 가지 끝을 받은 뒤 잠금 파일을 그 커밋으로 다시 쓰므로 쓰지 않는다.
---끝나면 잠금 파일과 실제 커밋이 다른 플러그인 이름 목록을 돌려준다 (비어 있으면 성공).
---@return string[] mismatched
function M.install()
  local manage = require('lazy.manage')
  manage.install({ wait = true, lockfile = true, show = false })
  manage.restore({ wait = true, show = false })
  return M.mismatches()
end

---설치기용: install() 뒤 `PLUGINS ok` 또는 `PLUGINS mismatch <이름,...>`을 찍고 끝낸다 (어긋나면 종료 코드 1).
---  nvim --headless "+lua require('config.lazy').install_cli()"
function M.install_cli()
  local ok, bad = pcall(M.install)
  if not ok then
    io.stdout:write('PLUGINS error ', tostring(bad), '\n')
    vim.cmd('cquit 1')
  elseif #bad > 0 then
    io.stdout:write('PLUGINS mismatch ', table.concat(bad, ','), '\n')
    vim.cmd('cquit 1')
  else
    io.stdout:write('PLUGINS ok\n')
    vim.cmd('qall!')
  end
end

---잠금 파일과 설치된 커밋이 다른(또는 설치되지 않은) 원격 플러그인 이름.
---@return string[]
function M.mismatches()
  local Git = require('lazy.manage.git')
  local fd = vim.uv.fs_open(lockfile, 'r', 438)
  local lock = {}
  if fd then
    local st = vim.uv.fs_fstat(fd)
    lock = vim.json.decode(vim.uv.fs_read(fd, st.size, 0) or '{}') or {}
    vim.uv.fs_close(fd)
  end
  local bad = {}
  for _, plugin in pairs(require('lazy.core.config').spec.plugins) do
    if not plugin._.is_local then
      local entry = lock[plugin.name]
      local info = vim.uv.fs_stat(plugin.dir) and Git.info(plugin.dir) or nil
      if not entry or not info or info.commit ~= entry.commit then
        table.insert(bad, plugin.name)
      end
    end
  end
  table.sort(bad)
  return bad
end

return M
