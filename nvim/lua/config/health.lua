-- :checkhealth config — 설정 점검. install.ps1의 doctor 단계는 같은 점검을 줄 단위로 찍는다:
--   nvim --headless "+lua require('config.health').cli()"
-- 줄 모양: `OK   <내용>` / `WARN <내용>` / `FAIL <내용>`. FAIL이 하나라도 있으면 종료 코드 1.

local M = {}

local function exists(path)
  return path ~= nil and vim.uv.fs_stat(path) ~= nil
end

local function read(path)
  local fd = vim.uv.fs_open(path, 'r', 438)
  if not fd then
    return nil
  end
  local st = vim.uv.fs_fstat(fd)
  local data = st and st.size > 0 and vim.uv.fs_read(fd, st.size, 0) or ''
  vim.uv.fs_close(fd)
  return data
end

local function config_dir()
  return vim.fn.stdpath('config') --[[@as string]]
end

---도구 이름 → mason/bin의 실행 파일 이름.
M.tool_bins = {
  ['lua-language-server'] = 'lua-language-server',
  pyright = 'pyright-langserver',
  ruff = 'ruff',
  stylua = 'stylua',
}

---모든 점검 결과 { level = 'ok'|'warn'|'fail', msg = ... } 목록.
---@return {level: string, msg: string}[]
function M.results()
  local out = {}
  local function add(level, msg)
    table.insert(out, { level = level, msg = msg })
  end

  -- local.lua
  local settings = require('config.settings')
  if settings.warning then
    add('warn', settings.warning:match('[^\n]*'))
  else
    add('ok', 'lua/local.lua')
  end

  -- 코드 루트·볼트·Python
  local root = settings.root
  if exists(root .. '/scripts/kb.py') then
    add('ok', 'root ' .. root)
  else
    add('fail', 'root에 scripts/kb.py가 없음: ' .. tostring(root))
  end
  if settings.vault and not exists(settings.vault) then
    add('fail', 'vault 폴더가 없음: ' .. settings.vault)
  end
  local python = settings.python or (root .. '/.venv/Scripts/python.exe')
  if type(python) == 'string' and (exists(python) or vim.fn.executable(python) == 1) then
    add('ok', 'python ' .. python)
  else
    add('fail', 'python 없음: ' .. vim.inspect(python))
  end

  -- 플러그인 잠금
  local ok_lazy, lazy_cfg = pcall(require, 'config.lazy')
  if ok_lazy and package.loaded['lazy'] then
    local bad = lazy_cfg.mismatches()
    if #bad == 0 then
      add('ok', 'lazy-lock.json: 모든 플러그인이 잠금 커밋')
    else
      add('fail', 'lazy-lock.json과 다르거나 없는 플러그인: ' .. table.concat(bad, ', '))
    end
  else
    add('fail', 'lazy.nvim을 쓰지 못함')
  end

  -- 실행 파일
  local util = require('config.util')
  for _, name in ipairs({ 'git', 'rg', 'fd', 'fzf' }) do
    if vim.fn.executable(name) == 1 then
      add('ok', name .. ' ' .. util.unshim(name))
    else
      add('fail', name .. ' 없음 (scoop install ' .. (name == 'rg' and 'ripgrep' or name) .. ')')
    end
  end
  if util.win32yank() then
    add('ok', 'win32yank ' .. util.win32yank())
  else
    add('warn', 'nvim.exe 옆에 win32yank.exe가 없음 (클립보드는 자동 탐지)')
  end
  if vim.fn.executable('neovide') == 1 then
    add('ok', 'neovide')
  else
    add('warn', 'neovide 없음 (scoop install extras/neovide)')
  end

  -- mason 도구 (tools.lock.json)
  local tools_text = read(config_dir() .. '/tools.lock.json')
  local ok_tools, tools = pcall(vim.json.decode, tools_text or '')
  if ok_tools and type(tools) == 'table' and type(tools.tools) == 'table' then
    local bin = vim.fn.stdpath('data') .. '/mason/bin/'
    local tool_names = vim.tbl_keys(tools.tools)
    table.sort(tool_names)
    for _, name in ipairs(tool_names) do
      local version = tools.tools[name]
      local exe = M.tool_bins[name] or name
      local found = false
      for _, ext in ipairs({ '.cmd', '.exe', '' }) do
        found = found or exists(bin .. exe .. ext)
      end
      if found then
        add('ok', string.format('mason %s (잠금 %s)', name, version))
      else
        add('warn', string.format('mason %s@%s 없음 (install.ps1 -Only tools)', name, version))
      end
    end
  else
    add('fail', 'tools.lock.json을 읽지 못함')
  end

  -- 글꼴
  local fonts = ''
  for _, key in ipairs({
    [[HKCU\Software\Microsoft\Windows NT\CurrentVersion\Fonts]],
    [[HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts]],
  }) do
    local ok, res = pcall(function()
      return vim.system({ 'reg', 'query', key }, { text = false }):wait(10000)
    end)
    if ok and res and res.stdout then
      fonts = fonts .. res.stdout
    end
  end
  for _, font in ipairs({ { 'JetBrainsMono', 'scoop install nerd-fonts/JetBrainsMono-NF' }, { 'Sarasa', 'scoop install nerd-fonts/SarasaGothic-K' } }) do
    if fonts:find(font[1], 1, true) then
      add('ok', '글꼴 ' .. font[1])
    else
      add('warn', '글꼴 ' .. font[1] .. ' 없음 (' .. font[2] .. ')')
    end
  end

  -- Neovide config.toml (install.ps1이 nvim/neovide/config.toml.in으로 만든다)
  local template = read(config_dir() .. '/neovide/config.toml.in')
  local appdata = vim.env.APPDATA
  local target = appdata and (appdata .. '/neovide/config.toml') or nil
  local current = target and read(target)
  if not template then
    add('warn', 'nvim/neovide/config.toml.in 없음')
  elseif not current then
    add('warn', 'Neovide config.toml 없음 (install.ps1 -Only neovide)')
  else
    local function body(text)
      local lines = {}
      for line in (text:gsub('\r\n', '\n')):gmatch('[^\n]*') do
        if not line:match('^%s*neovim%-bin%s*=') then
          table.insert(lines, line)
        end
      end
      return table.concat(lines, '\n')
    end
    local bin = current:match("neovim%-bin%s*=%s*'([^']*)'") or current:match('neovim%-bin%s*=%s*"([^"]*)"')
    if body(template) ~= body(current) then
      add('warn', 'Neovide config.toml이 템플릿과 다름: ' .. target)
    elseif bin and not exists(bin) then
      add('fail', 'Neovide config.toml의 neovim-bin이 없음: ' .. bin)
    else
      add('ok', 'Neovide config.toml')
    end
  end
  return out
end

function M.check()
  vim.health.start('config')
  for _, r in ipairs(M.results()) do
    if r.level == 'ok' then
      vim.health.ok(r.msg)
    elseif r.level == 'warn' then
      vim.health.warn(r.msg)
    else
      vim.health.error(r.msg)
    end
  end
end

---줄 단위로 찍고 끝낸다 (FAIL이 있으면 종료 코드 1).
function M.cli()
  local failed = false
  for _, r in ipairs(M.results()) do
    local tag = r.level == 'ok' and 'OK  ' or (r.level == 'warn' and 'WARN' or 'FAIL')
    failed = failed or r.level == 'fail'
    io.stdout:write(tag, ' ', r.msg, '\n')
  end
  io.stdout:flush()
  vim.cmd((failed and 'cquit 1' or 'qall!'))
end

return M
