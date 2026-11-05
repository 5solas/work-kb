-- 오늘 daily: 경로, 뼈대, 준비(`kb.py today --date`), t 명령 (Emacs work-kb--ensure-today, work-kb-today).

local M = {}

local function config()
  return require("work-kb.config")
end

local function proc()
  return require("work-kb.proc")
end

local function ui()
  return require("work-kb.ui")
end

local function time()
  return require("work-kb.time")
end

local function fileio()
  return require("work-kb.fileio")
end

---오늘(또는 T) daily 경로: <볼트>/daily/YYYY-MM-DD.md (슬래시).
---@param t? integer
---@return string
function M.path(t)
  return config().vault() .. "/daily/" .. time().day(t) .. ".md"
end

---notes.daily_skeleton과 같은 바이트.
---@param day string YYYY-MM-DD
---@return string
function M.skeleton(day)
  return table.concat({
    "---\n",
    "date: ",
    day,
    "\n",
    "---\n",
    "## 오늘\n",
    "<!-- kb:today:start -->\n",
    "<!-- kb:today:end -->\n",
    "\n",
    "## 이월\n",
    "<!-- kb:carryover:start -->\n",
    "<!-- kb:carryover:end -->\n",
    "\n",
    "## 기록\n",
  })
end

---오늘 daily 경로. 없거나 FORCE면 `kb.py today --date DAY`를 기다려 실행하고(실패해도 계속),
---그래도 없으면 뼈대를 UTF-8·LF로 새로 만든다 (덮어쓰지 않음, D15).
---@param force? boolean
---@param t? integer
---@return string
function M.ensure_today(force, t)
  t = t or time().now()
  local day = time().day(t)
  local file = M.path(t)
  local fio = fileio()
  if force or not fio.exists(file) then
    local p = proc()
    local ok, code = pcall(function()
      return p.run_sync(p.kb_args("today", "--date", day))
    end)
    if not ok then
      ui().notify("work-kb: today 실행 실패: " .. tostring(code))
      code = nil
    end
    if code ~= 0 then
      ui().notify(
        string.format("work-kb: daily_prepare 실패 (종료 %s). %s 버퍼를 보세요", code == nil and "nil" or tostring(code), p.OUTPUT)
      )
    end
  end
  if not fio.exists(file) then
    fio.create_exclusive(file, M.skeleton(day))
  end
  return file
end

---t: 오늘 daily를 준비(`kb.py today`)하고 연다. 준비가 실패해도 뼈대를 만들어 연다.
---오늘 파일을 연 버퍼에 저장하지 않은 수정이 있으면 먼저 저장한다. 버퍼와 파일이 모두 바뀌었으면
---준비를 건너뛰고 버퍼만 보인다. 준비 뒤 수정 없는 버퍼는 묻지 않고 다시 읽는다.
function M.today()
  local fio = fileio()
  local file = M.path()
  local live = fio.find_buffer(file)
  local skip = false
  if live and vim.bo[live].modified then
    if fio.exists(file) and fio.both_changed(live) then
      skip = true
    else
      fio.write_buffer(live, false)
    end
  end
  if skip then
    ui().notify(string.format("work-kb: %s 버퍼와 파일이 모두 바뀌어 today 준비를 건너뜁니다. 먼저 정리하세요", fio.buffer_label(live)))
  else
    file = M.ensure_today(true)
  end
  if live and vim.api.nvim_buf_is_valid(live) then
    fio.refresh(live)
    vim.api.nvim_set_current_buf(live)
  else
    vim.cmd("edit " .. vim.fn.fnameescape(file))
  end
  return file
end

return M
