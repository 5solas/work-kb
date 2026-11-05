-- 백그라운드 작업 줄 (User VeryLazy 뒤 start()). 이벤트 루프 한 바퀴에 작업 하나,
-- 작업 사이 20ms. 노멀 모드이고 입력 대기가 없고 마지막 키에서 200ms가 지났을 때만 돌고,
-- 아니면 100ms 뒤 다시 본다. 작업마다 xpcall로 이름·시간·오류를 M.log에 남긴다. VimLeavePre에 멈춘다.
-- 설치기는 warm_all({ sync = true })로 모든 작업을 한 번에 돌려 vim.loader 캐시를 채운다.
-- shada 읽기를 미루는 실험(설계 F절 7·d)은 넣지 않았다: --startuptime의 "reading ShaDa"가 1.2~1.3ms로
-- 채택 기준(3ms 이상)에 못 미쳤다.

local M = {}

M.log = {} ---@type {name: string, ok: boolean, ms: number, err?: string}[]
M.done = false
M.started = false
M.cancelled = false

M.first_delay_ms = 300
M.gap_ms = 20
M.retry_ms = 100
M.idle_ms = 200

local queue = {}
local last_key = 0
local on_key_ns
local leave_au

local function now_ms()
  return vim.uv.hrtime() / 1e6
end

local function settings()
  local ok, s = pcall(require, 'config.settings')
  return ok and s or {}
end

---실제 실행 파일 VERSION 확인을 비동기로 (OS·Defender 캐시 데우기). 결과는 기다리지 않는다.
local function touch_exe(name)
  local exe = require('config.util').unshim(name)
  if vim.fn.executable(exe) == 1 then
    pcall(vim.system, { exe, '--version' }, { text = true })
  end
end

local function load_plugin(name)
  local ok, lazy_config = pcall(require, 'lazy.core.config')
  if not ok then
    return
  end
  local plugin = lazy_config.plugins[name]
  if plugin and not plugin._.loaded then
    require('lazy').load({ plugins = { name } })
  end
end

---기본 작업 목록 (순서대로). 설정(background.*)이 끈 작업은 빠진다.
---@return {name: string, fn: fun()}[]
function M.default_tasks()
  local bg = settings().background or {}
  local tasks = {}
  local function add(name, fn)
    table.insert(tasks, { name = name, fn = fn })
  end
  if bg.server ~= false then
    -- work-kb 플러그인 setup은 바로 끝나고 kb.py serve를 비동기로 띄운다 (VimLeavePre에 끝냄)
    add('work-kb', function()
      load_plugin('work-kb')
    end)
  end
  if bg.prewarm ~= false then
    for _, name in ipairs({ 'blink.cmp', 'fzf-lua', 'oil.nvim', 'conform.nvim', 'render-markdown.nvim' }) do
      add(name, function()
        load_plugin(name)
      end)
    end
    add('vim.lsp', function()
      require('vim.lsp')
    end)
    add('ts-queries', function()
      for _, lang in ipairs({ 'markdown', 'markdown_inline', 'lua' }) do
        pcall(vim.treesitter.query.get, lang, 'highlights')
      end
    end)
    add('exe-cache', function()
      for _, name in ipairs({ 'fzf', 'rg', 'fd' }) do
        touch_exe(name)
      end
    end)
  end
  return tasks
end

local function idle()
  local mode = vim.api.nvim_get_mode()
  if mode.blocking or mode.mode ~= 'n' then
    return false
  end
  if vim.fn.getchar(1) ~= 0 then -- 입력 대기 중인 키가 있다
    return false
  end
  return now_ms() - last_key >= M.idle_ms
end

local function finish()
  M.done = true
  if on_key_ns then
    vim.on_key(nil, on_key_ns)
  end
  if leave_au then
    pcall(vim.api.nvim_del_autocmd, leave_au)
    leave_au = nil
  end
end

local function run_task(task)
  local started = now_ms()
  local ok, err = xpcall(task.fn, debug.traceback)
  table.insert(M.log, { name = task.name, ok = ok, ms = now_ms() - started, err = (not ok) and tostring(err) or nil })
end

local step
step = function()
  if M.cancelled or M.done then
    return
  end
  if #queue == 0 then
    finish()
    return
  end
  if not idle() then
    vim.defer_fn(step, M.retry_ms)
    return
  end
  run_task(table.remove(queue, 1))
  if #queue == 0 then
    finish()
  else
    vim.defer_fn(step, M.gap_ms)
  end
end

---작업 줄을 시작한다 (한 번만). OPTS.tasks로 작업 목록을 바꿀 수 있다 (테스트).
---@param opts? {tasks?: {name: string, fn: fun()}[], first_delay_ms?: integer}
function M.start(opts)
  opts = opts or {}
  if M.started then
    return
  end
  if settings().background and settings().background.enabled == false and not opts.tasks then
    M.started, M.done = true, true
    return
  end
  M.started, M.done, M.cancelled = true, false, false
  queue = vim.list_extend({}, opts.tasks or M.default_tasks())
  last_key = 0
  on_key_ns = vim.on_key(function()
    last_key = now_ms()
  end, on_key_ns)
  leave_au = vim.api.nvim_create_autocmd('VimLeavePre', {
    group = vim.api.nvim_create_augroup('config_background', { clear = true }),
    once = true,
    callback = function()
      M.cancel()
    end,
  })
  vim.defer_fn(step, opts.first_delay_ms or M.first_delay_ms)
end

---남은 작업을 버린다.
function M.cancel()
  M.cancelled = true
  queue = {}
  finish()
end

---모든 작업을 바로(유휴 조건 없이) 돌린다. 설치기의 데우기 단계:
---  nvim --headless "+lua require('config.background').warm_all({sync=true})" +qa
---@param opts? {sync?: boolean, tasks?: {name: string, fn: fun()}[]}
---@return boolean all_ok
function M.warm_all(opts)
  opts = opts or {}
  local tasks = opts.tasks or M.default_tasks()
  if not opts.sync then
    M.start({ tasks = tasks, first_delay_ms = 0 })
    return true
  end
  local all_ok = true
  for _, task in ipairs(tasks) do
    run_task(task)
    all_ok = all_ok and M.log[#M.log].ok
  end
  return all_ok
end

---테스트용: 처음 상태로.
function M._reset()
  M.cancel()
  M.log, M.done, M.started, M.cancelled = {}, false, false, false
  on_key_ns = nil
end

---:KbStartup — lazy.stats()와 작업 기록.
function M.report()
  local lines = {}
  local ok, lazy = pcall(require, 'lazy')
  if ok then
    local s = lazy.stats()
    table.insert(lines, string.format('lazy: %d/%d 플러그인, 시작 %.1f ms', s.loaded, s.count, s.startuptime))
  end
  table.insert(lines, string.format('백그라운드: %s', M.done and '끝' or (M.started and '진행 중' or '시작 전')))
  for _, entry in ipairs(M.log) do
    table.insert(lines, string.format('  %-22s %s %6.1f ms%s', entry.name, entry.ok and 'ok  ' or 'FAIL',
      entry.ms, entry.err and ('  ' .. entry.err:match('[^\n]*')) or ''))
  end
  vim.api.nvim_echo({ { table.concat(lines, '\n') } }, true, {})
  return lines
end

return M
