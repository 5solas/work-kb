-- config/background.lua: 유휴 작업 줄 (설계 F절).
-- 전체 설정(-u nvim/init.lua)으로 돈다. 헤드리스에서는 User VeryLazy가 저절로 오지 않으므로
-- 작업 줄은 이 파일이 start()를 부를 때만 돈다. 이 파일은 config_spec 중 맨 먼저 돈다 (이름 순).
local H = require('helpers')

local bg = require('config.background')

local function wait_done(ms)
  return vim.wait(ms or 5000, function()
    return bg.done
  end, 10)
end

local function names(log)
  local out = {}
  for _, entry in ipairs(log) do
    table.insert(out, entry.name)
  end
  return out
end

---lazy.load를 가로채 work-kb는 기록만 한다 (실제 kb.py serve를 띄우지 않는다). 나머지는 그대로 불러온다.
local function intercept_work_kb()
  local lazy = require('lazy')
  local real = lazy.load
  local seen = { count = 0 }
  H.patch(lazy, 'load', function(opts)
    local list = opts.plugins or {}
    if list[1] == 'work-kb' or (type(list[1]) == 'table' and list[1].name == 'work-kb') then
      seen.count = seen.count + 1
      return
    end
    return real(opts)
  end)
  return seen
end

local function cleanup()
  bg._reset()
end

return {
  {
    name = 'log is empty and the queue idle before VeryLazy',
    fn = function()
      H.eq(vim.g.did_very_lazy, nil, 'VeryLazy가 이미 옴')
      H.eq(bg.started, false)
      H.eq(#bg.log, 0)
    end,
  },
  {
    name = 'start runs every task once, in order, and finishes',
    fn = function()
      local ran = {}
      local tasks = {}
      for _, n in ipairs({ 'a', 'b', 'c' }) do
        table.insert(tasks, { name = n, fn = function() table.insert(ran, n) end })
      end
      bg.start({ tasks = tasks, first_delay_ms = 0 })
      bg.start({ tasks = tasks, first_delay_ms = 0 }) -- 두 번째 start는 아무것도 하지 않는다
      H.ok(wait_done(5000), '끝나지 않음')
      H.eq(ran, { 'a', 'b', 'c' })
      H.eq(names(bg.log), { 'a', 'b', 'c' })
      for _, entry in ipairs(bg.log) do
        H.ok(entry.ok and type(entry.ms) == 'number', vim.inspect(entry))
      end
      cleanup()
    end,
  },
  {
    name = 'a failing task is logged and does not stop the queue',
    fn = function()
      local ran = {}
      bg.start({
        first_delay_ms = 0,
        tasks = {
          { name = 'boom', fn = function() error('일부러') end },
          { name = 'after', fn = function() table.insert(ran, 'after') end },
        },
      })
      H.ok(wait_done(5000), '끝나지 않음')
      H.eq(ran, { 'after' })
      H.eq(bg.log[1].ok, false)
      H.contains(bg.log[1].err, '일부러')
      H.eq(bg.log[2].ok, true)
      cleanup()
    end,
  },
  {
    name = 'key activity postpones the next task until 200 ms of quiet',
    fn = function()
      local ran_at
      bg.start({ first_delay_ms = 0, tasks = { { name = 't', fn = function() ran_at = vim.uv.hrtime() / 1e6 end } } })
      local stop_at = vim.uv.hrtime() / 1e6 + 500
      -- 500ms 동안 50ms마다 키를 넣는다 (vim.on_key가 본다)
      while vim.uv.hrtime() / 1e6 < stop_at do
        vim.api.nvim_feedkeys(vim.keycode('<Ignore>'), 'x', false)
        vim.wait(50)
        H.eq(ran_at, nil, '키를 누르는 동안 작업이 돌았음')
      end
      local last_key = vim.uv.hrtime() / 1e6
      H.ok(wait_done(5000), '끝나지 않음')
      H.ok(ran_at - last_key >= 150, string.format('마지막 키 %.0fms 뒤에 돔', ran_at - last_key))
      cleanup()
    end,
  },
  {
    name = 'tasks wait while not in normal mode',
    fn = function()
      local ran = false
      local mode = 'i'
      H.patch(vim.api, 'nvim_get_mode', function()
        return { mode = mode, blocking = false }
      end)
      bg.start({ first_delay_ms = 0, tasks = { { name = 't', fn = function() ran = true end } } })
      vim.wait(400)
      H.eq(ran, false, '삽입 모드에서 돌았음')
      mode = 'n'
      H.ok(wait_done(3000), '끝나지 않음')
      H.eq(ran, true)
      cleanup()
    end,
  },
  {
    name = 'VimLeavePre cancels the remaining tasks',
    fn = function()
      local ran = false
      bg.start({ first_delay_ms = 200, tasks = { { name = 't', fn = function() ran = true end } } })
      vim.api.nvim_exec_autocmds('VimLeavePre', { group = 'config_background' })
      vim.wait(500)
      H.eq(ran, false)
      H.eq(bg.cancelled, true)
      H.eq(bg.done, true)
      cleanup()
    end,
  },
  {
    name = 'default tasks: order and the settings switches',
    fn = function()
      local s = require('config.settings')
      H.patch(s, 'background', { enabled = true, server = true, prewarm = true })
      H.eq(names(bg.default_tasks()), {
        'work-kb', 'blink.cmp', 'fzf-lua', 'oil.nvim', 'conform.nvim', 'render-markdown.nvim',
        'vim.lsp', 'ts-queries', 'exe-cache',
      })
      s.background = { enabled = true, server = false, prewarm = false }
      H.eq(names(bg.default_tasks()), {})
      s.background = { enabled = false }
      bg.start()
      H.eq(bg.done, true, 'enabled = false면 바로 끝')
      H.eq(#bg.log, 0)
      cleanup()
    end,
  },
  {
    name = 'warm_all runs the real default tasks; work-kb is asked for exactly once',
    fn = function()
      local s = require('config.settings')
      H.patch(s, 'background', { enabled = true, server = true, prewarm = true })
      local seen = intercept_work_kb()
      local all_ok = bg.warm_all({ sync = true })
      local failed = {}
      for _, entry in ipairs(bg.log) do
        if not entry.ok then
          table.insert(failed, entry.name .. ': ' .. tostring(entry.err))
        end
      end
      H.eq(failed, {})
      H.ok(all_ok)
      H.eq(seen.count, 1)
      local plugins = require('lazy.core.config').plugins
      for _, name in ipairs({ 'blink.cmp', 'fzf-lua', 'oil.nvim', 'conform.nvim', 'render-markdown.nvim' }) do
        H.ok(plugins[name]._.loaded, name .. ' 안 불러옴')
      end
      H.ok(package.loaded['vim.lsp'] ~= nil)
      H.eq(plugins['work-kb']._.loaded, nil, 'work-kb가 실제로 불러와짐')
      cleanup()
    end,
  },
  {
    name = ':KbStartup reports lazy stats and the task log',
    fn = function()
      bg.log = { { name = 'x', ok = true, ms = 1.5 } }
      local lines = bg.report()
      H.contains(lines[1], 'lazy: ')
      H.contains(table.concat(lines, '\n'), 'x')
      H.eq(vim.fn.exists(':KbStartup'), 2)
      cleanup()
    end,
  },
}
