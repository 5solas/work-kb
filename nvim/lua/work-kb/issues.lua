-- J: Jira 일감 목록. `kb.py issues --json`이 읽은 캐시(jira/*.md)를 거르고 정렬해 버퍼에 그린다.
-- 네트워크를 쓰지 않고 jira/ 파일을 고치지 않는다. front matter는 Python만 해석한다.
-- 줄의 동작은 jira.lua의 함수에 그 줄의 키를 넘긴다 (커서 밑의 글을 다시 읽지 않는다).

local M = {}

M.BUFFER = "*work-kb-issues*"
M.JSON_BUFFER = "*work-kb-issues-json*"

---거르기와 마지막으로 읽은 자료 (세션 동안 유지).
---status: "open"(미완료) | "all" | "done". project·assignee·view는 값 또는 nil.
M.state = { status = "open", project = nil, assignee = nil, view = nil, data = nil }

local STATUS_LABEL = { open = "미완료", all = "전체", done = "완료" }
local NONE = "—"
local MAX_COLUMN = 12 -- 상태·담당·우선순위 열의 최대 표시 폭
local MIN_SUMMARY = 10
local HELP = " <CR> 열기  p 미리 보기  s 상태  P 프로젝트  A 담당  V 뷰  x 거르기 지움  g? 도움말"

local function proc()
  return require("work-kb.proc")
end

---------------------------------------------------------------- 자료

---캐시 목록을 읽는다 (VIEW가 있으면 그 뷰). 실패하면 nil과 이유.
---@param view? string
---@return table|nil data, string|nil reason
function M.fetch(view)
  local p = proc()
  local ok, code, text = pcall(function()
    local args = p.kb_args("issues", "--json")
    if view then
      table.insert(args, "--view=" .. view)
    end
    return p.run_output(args, { buffer = M.JSON_BUFFER })
  end)
  if not ok then
    return nil, tostring(code)
  end
  if code ~= 0 then
    local last = p.last_line(p.get_buffer(M.JSON_BUFFER, false))
    return nil, last or p.exit_reason(code)
  end
  -- 출력은 한 줄 JSON이고 stderr의 안내 줄이 섞일 수 있다.
  for line in text:gmatch("[^\r\n]+") do
    if line:sub(1, 1) == "{" then
      local decoded, value = pcall(vim.json.decode, line, { luanil = { object = true, array = true } })
      if decoded and type(value) == "table" and type(value.issues) == "table" and type(value.views) == "table" then
        return value
      end
    end
  end
  return nil, "출력이 JSON이 아닙니다"
end

---STATE의 거르기를 적용한다. 뷰가 있으면 상태 거르기는 무시한다 (뷰가 이미 거르기다).
function M.filter(issues, state)
  local out = {}
  for _, issue in ipairs(issues) do
    local done = issue.status_category == "done"
    local keep = state.view ~= nil or state.status == "all" or done == (state.status == "done")
    if
      keep
      and (state.project == nil or issue.project == state.project)
      and (state.assignee == nil or issue.assignee == state.assignee)
    then
      table.insert(out, issue)
    end
  end
  return out
end

local CATEGORY_ORDER = { indeterminate = 1, new = 2, done = 4 }

---정렬한 새 목록. BY_VIEW면 순서를 그대로 둔다.
function M.sort(issues, by_view)
  local out = vim.list_slice(issues)
  if by_view then
    return out
  end
  table.sort(out, function(a, b)
    local ca, cb = CATEGORY_ORDER[a.status_category] or 3, CATEGORY_ORDER[b.status_category] or 3
    if ca ~= cb then
      return ca < cb
    end
    if a.updated ~= b.updated then
      return a.updated > b.updated
    end
    local pa, na = a.key:match("^(.*)%-(%d+)$")
    local pb, nb = b.key:match("^(.*)%-(%d+)$")
    if pa ~= pb then
      return (pa or a.key) < (pb or b.key)
    end
    return (tonumber(na) or 0) < (tonumber(nb) or 0)
  end)
  return out
end

---FIELD의 서로 다른 비어 있지 않은 값 (정렬).
function M.values(issues, field)
  local seen, out = {}, {}
  for _, issue in ipairs(issues) do
    local value = issue[field]
    if type(value) == "string" and value ~= "" and not seen[value] then
      seen[value] = true
      table.insert(out, value)
    end
  end
  table.sort(out)
  return out
end

---------------------------------------------------------------- 그리기

local width_of = vim.fn.strdisplaywidth

---제어 글자(탭·줄바꿈)를 공백으로, 빈 글은 "—".
local function cell(text)
  text = vim.trim((text or ""):gsub("%c+", " "))
  return text == "" and NONE or text
end

---TEXT를 표시 폭 WIDTH 안으로 자른다 (자르면 끝이 …). 둘째 값은 결과의 폭.
local function cut(text, width)
  local full = width_of(text)
  if full <= width then
    return text, full
  end
  local low, high = 0, vim.fn.strchars(text, 1)
  while low < high do
    local mid = math.ceil((low + high) / 2)
    if width_of(vim.fn.strcharpart(text, 0, mid, 1)) <= width - 1 then
      low = mid
    else
      high = mid - 1
    end
  end
  local out = vim.fn.strcharpart(text, 0, low, 1) .. "…"
  return out, width_of(out)
end

local function pad(text, width)
  local out, w = cut(text, width)
  return out .. string.rep(" ", width - w)
end

local CATEGORY_HL = { indeterminate = "WorkKbIssueProgress", new = "WorkKbIssueTodo", done = "WorkKbIssueDone" }

M.HIGHLIGHTS = {
  WorkKbIssueProgress = "DiagnosticInfo",
  WorkKbIssueTodo = "Normal",
  WorkKbIssueDone = "Comment",
  WorkKbIssueOverdue = "DiagnosticError",
  WorkKbIssueUncached = "NonText",
  WorkKbIssueHeader = "Title",
}

local function header(data, state, shown, today)
  local synced = "없음"
  if data.synced_at then
    local day, time = data.synced_at:sub(1, 10), data.synced_at:sub(12, 16)
    synced = day == today and time or (day:sub(6) .. " " .. time)
  end
  return string.format(
    " Jira 일감  [%s]  프로젝트: %s  담당: %s  뷰: %s   %d/%d건   동기화 %s",
    state.view and STATUS_LABEL.all or STATUS_LABEL[state.status],
    state.project or "전체",
    state.assignee or "전체",
    state.view or NONE,
    shown,
    #data.issues,
    synced
  )
end

---보일 열과 폭을 정한다. 모자라면 우선순위 → 담당 → 마감 순으로 숨긴다.
local function layout(rows, width)
  local w = { key = 0, status = 1, priority = 1, assignee = 1, due = 5 }
  for _, row in ipairs(rows) do
    for _, name in ipairs({ "key", "status", "priority", "assignee" }) do
      w[name] = math.max(w[name], width_of(row[name]))
    end
  end
  for _, name in ipairs({ "status", "priority", "assignee" }) do
    w[name] = math.min(w[name], MAX_COLUMN)
  end
  local floor = 1 + w.key + 2 + w.status + 2 + MIN_SUMMARY
  local total = floor
  for _, name in ipairs({ "priority", "assignee", "due" }) do
    total = total + w[name] + 2
  end
  for _, name in ipairs({ "priority", "assignee", "due" }) do
    if total > width then
      total = total - w[name] - 2
      w[name] = nil
    end
  end
  w.summary = MIN_SUMMARY + math.max(0, width - total)
  return w, math.max(width, floor)
end

---화면 글. rows[줄 번호(1부터)] = 일감, marks = { 줄 번호, 시작·끝 바이트(0부터), 강조 그룹 }.
function M.render(data, state, width, today)
  local shown = M.sort(M.filter(data.issues, state), state.view ~= nil)
  local cells = {}
  for i, issue in ipairs(shown) do
    cells[i] = {
      key = issue.key,
      status = cell(issue.status),
      priority = cell(issue.priority),
      summary = cell(issue.summary),
      assignee = cell(issue.assignee),
      due = issue.due:match("^%d+%-(%d%d%-%d%d)") or NONE,
    }
  end
  local w, line_width = layout(cells, width)
  local rule = string.rep("─", math.floor(line_width / width_of("─")))
  local lines = { (cut(header(data, state, #shown, today):gsub("%c+", " "), line_width)), rule }
  local rows, marks = {}, { { 1, 0, #lines[1], "WorkKbIssueHeader" } }

  for i, issue in ipairs(shown) do
    local lnum = #lines + 1
    local parts, offset = {}, 0
    local span = {}
    local function add(name, lead)
      if not w[name] then
        return
      end
      local text = pad(cells[i][name], w[name])
      span[name] = { offset + #lead, offset + #lead + #text }
      table.insert(parts, lead .. text)
      offset = offset + #lead + #text
    end
    add("key", " ")
    for _, name in ipairs({ "status", "priority", "summary", "assignee", "due" }) do
      add(name, "  ")
    end
    local line = table.concat(parts):gsub(" +$", "")
    lines[lnum] = line
    rows[lnum] = issue
    if issue.cached == false then
      table.insert(marks, { lnum, 0, #line, "WorkKbIssueUncached" })
    else
      local hl = CATEGORY_HL[issue.status_category]
      if hl then
        local from, to = span.status[1], span.status[2]
        if hl == "WorkKbIssueDone" then
          from, to = 0, #line
        end
        table.insert(marks, { lnum, from, to, hl })
      end
    end
    if span.due and cells[i].due ~= NONE and issue.due:sub(1, 10) < today and issue.status_category ~= "done" then
      table.insert(marks, { lnum, span.due[1], span.due[1] + #cells[i].due, "WorkKbIssueOverdue" })
    end
  end

  if #shown == 0 then
    local empty = #data.issues == 0 and not state.view
    table.insert(
      lines,
      empty and " 캐시에 일감이 없습니다. S로 동기화하세요" or " 조건에 맞는 일감이 없습니다. x로 거르기를 지우세요"
    )
  end
  table.insert(lines, rule)
  table.insert(lines, (cut(HELP, line_width)))
  return { lines = lines, rows = rows, marks = marks }
end

---------------------------------------------------------------- 버퍼

local NS = vim.api.nvim_create_namespace("work-kb-issues")

M._last_key = nil -- 마지막으로 커서가 있던 일감 (버퍼를 지워도 세션 동안 기억)
M._rows = {}
M._width = nil
M._preview_win = nil
M._preview_buf = nil

local function ui()
  return require("work-kb.ui")
end

local function jira()
  return require("work-kb.jira")
end

---목록 버퍼 (불러온 것만). 없으면 nil.
local function list_buffer()
  local buf = proc().get_buffer(M.BUFFER, false)
  if buf and vim.api.nvim_buf_is_loaded(buf) then
    return buf
  end
  return nil
end

local function text_width(win)
  local info = win and vim.fn.getwininfo(win)[1]
  return info and (info.width - info.textoff) or vim.o.columns
end

---지금 자료로 다시 그린다. 커서는 같은 키의 줄로, 그 일감이 없으면 있던 줄에서 가장 가까운 일감 줄로.
local function draw(buf)
  local wins = vim.fn.win_findbuf(buf)
  local keys, old_line = {}, {}
  for _, win in ipairs(wins) do
    old_line[win] = vim.api.nvim_win_get_cursor(win)[1]
    local row = M._rows[old_line[win]]
    keys[win] = row and row.key or M._last_key
  end
  M._width = text_width(wins[1])
  local data = M.state.data or { issues = {}, views = {} }
  local out = M.render(data, M.state, M._width, require("work-kb.time").day())
  M._rows = out.rows
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, out.lines)
  vim.bo[buf].modifiable = false
  vim.bo[buf].modified = false
  vim.api.nvim_buf_clear_namespace(buf, NS, 0, -1)
  for _, m in ipairs(out.marks) do
    vim.api.nvim_buf_set_extmark(buf, NS, m[1] - 1, m[2], { end_col = m[3], hl_group = m[4] })
  end
  local line_of, first, last = {}, 3, 3
  for lnum, row in pairs(out.rows) do
    line_of[row.key] = lnum
    last = math.max(last, lnum)
  end
  for _, win in ipairs(wins) do
    local lnum = line_of[keys[win] or ""] or math.max(first, math.min(old_line[win], last))
    pcall(vim.api.nvim_win_set_cursor, win, { lnum, 0 })
  end
  M._moved()
end

---목록을 다시 읽어 그린다 (opts.keep_data면 읽지 않고 그리기만). 버퍼가 없으면 아무것도 하지 않는다.
---읽지 못하면 알리고 이전 화면을 둔다. 그렸으면 true.
function M.refresh(opts)
  local buf = list_buffer()
  if not buf then
    return false
  end
  if not (opts and opts.keep_data) then
    local view = M.state.view
    local data, why = M.fetch(view)
    if not data and view then
      -- 보던 뷰가 사라졌으면 빈 목록으로 (머리줄에 뷰 이름이 남고 x로 지운다)
      local all = M.fetch()
      if all and not vim.tbl_contains(all.views, view) then
        ui().notify(string.format("work-kb: 일감 목록을 읽지 못했습니다 (%s)", why))
        data = { synced_at = all.synced_at, views = all.views, issues = {} }
      end
    end
    buf = list_buffer() -- 기다리는 동안 지웠을 수 있다
    if not data then
      ui().notify(string.format("work-kb: 일감 목록을 읽지 못했습니다 (%s)", why))
      if buf and vim.api.nvim_buf_line_count(buf) <= 1 then
        draw(buf) -- 아직 그린 적이 없는 버퍼
      end
      return false
    end
    M.state.data = data
    if not buf then
      return false
    end
  end
  draw(buf)
  return true
end

---커서 줄의 일감 키. 일감 줄이 아니면 사용자 오류.
function M.key_at_cursor()
  local row = M._rows[vim.api.nvim_win_get_cursor(0)[1]]
  if not row then
    ui().fail("일감 줄이 아닙니다")
  end
  return row.key
end

---------------------------------------------------------------- 미리 보기

---미리 보기 창이 아직 우리 버퍼를 보이는가 (사용자가 그 창에 다른 파일을 열었으면 아니다).
local function preview_open()
  local win = M._preview_win
  return win ~= nil and vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == M._preview_buf
end

local function close_preview()
  if preview_open() then
    pcall(vim.api.nvim_win_close, M._preview_win, true)
  end
  M._preview_win, M._preview_buf = nil, nil
end

---미리 보기 창에 KEY의 jira/KEY.md 글을 보인다 (파일이 없으면 빈 화면).
local function show_preview(key)
  local lines = {}
  if key then
    local file = require("work-kb.open").jira_file(key)
    local ok, read = pcall(vim.fn.readfile, vim.uv.fs_stat(file) and file or "")
    for i, line in ipairs(ok and read or {}) do
      lines[i] = line:gsub("\r$", ""):gsub("\n", " ")
    end
  end
  local buf = M._preview_buf
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  pcall(vim.api.nvim_win_set_cursor, M._preview_win, { 1, 0 })
end

---커서가 움직인 뒤: 키를 기억하고 미리 보기를 맞춘다.
function M._moved()
  if vim.api.nvim_get_current_buf() ~= list_buffer() then
    return
  end
  local row = M._rows[vim.api.nvim_win_get_cursor(0)[1]]
  local key = row and row.key
  M._last_key = key or M._last_key
  if preview_open() then
    show_preview(key)
  end
end

function M.preview()
  if preview_open() then
    close_preview()
    return
  end
  local key = M.key_at_cursor()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].filetype = "markdown"
  local height = math.max(5, math.floor(vim.api.nvim_win_get_height(0) * 0.5))
  local win = vim.api.nvim_open_win(buf, false, { split = "below", win = 0, height = height })
  vim.wo[win][0].wrap = true
  vim.wo[win][0].cursorline = false
  vim.wo[win][0].winfixbuf = true
  M._preview_win, M._preview_buf = win, buf
  show_preview(key)
end

---------------------------------------------------------------- 동작

function M.open_issue()
  return require("work-kb.open").open_key(M.key_at_cursor())
end

function M.comment()
  jira().comment({ key = M.key_at_cursor(), initial = "" })
end

function M.worklog()
  jira().worklog({ key = M.key_at_cursor() })
end

---KEY 하나만 Jira에서 받아 다시 그린다.
local function sync_key(key)
  if not list_buffer() then
    return
  end
  proc().run(proc().kb_args("sync", "--key", key), {
    buffer = require("work-kb.open").sync_buffer_name(key),
    on_exit = function(code)
      vim.schedule(function()
        if not list_buffer() then
          return
        end
        if code == 0 then
          M.refresh()
        else
          ui().notify(string.format("work-kb: %s을(를) 다시 받지 못했습니다 (%s)", key, proc().exit_reason(code)))
        end
      end)
    end,
  })
end

---성공하면 그 일감을 다시 받는 on_done.
local function then_sync(key)
  return function(code)
    if code == 0 then
      vim.schedule(function()
        sync_key(key)
      end)
    end
  end
end

function M.transition()
  local key = M.key_at_cursor()
  jira().transition({ key = key, on_done = then_sync(key) })
end

function M.assign()
  local key = M.key_at_cursor()
  jira().assign({ key = key, on_done = then_sync(key) })
end

function M.create()
  jira().create()
end

function M.sync()
  jira().sync({
    on_done = function(code)
      if code == 0 then
        vim.schedule(M.refresh)
      end
    end,
  })
end

function M.yank()
  local key = M.key_at_cursor()
  vim.fn.setreg('"', key)
  pcall(vim.fn.setreg, "+", key)
  ui().notify(key .. " 복사")
end

local NEXT_STATUS = { open = "all", all = "done", done = "open" }

function M.cycle_status()
  if M.state.view then
    ui().notify("work-kb: 뷰에서는 상태 거르기를 쓰지 않습니다. V에서 —를 고르면 뷰가 풀립니다")
    return
  end
  M.state.status = NEXT_STATUS[M.state.status]
  M.refresh({ keep_data = true })
end

---FIELD의 값을 지금 목록에서 고른다.
local function pick_filter(field, label)
  local all = "전체"
  local items = vim.list_extend({ all }, M.values(M.state.data and M.state.data.issues or {}, field))
  ui().pick(label, items, function(choice)
    if choice == nil then
      return
    end
    M.state[field] = choice ~= all and choice or nil
    M.refresh({ keep_data = true })
  end)
end

function M.pick_project()
  pick_filter("project", "프로젝트")
end

function M.pick_assignee()
  pick_filter("assignee", "담당")
end

function M.pick_view()
  local items = vim.list_extend({ NONE }, M.state.data and M.state.data.views or {})
  ui().pick("뷰", items, function(choice)
    if choice == nil then
      return
    end
    local old = M.state.view
    M.state.view = choice ~= NONE and choice or nil
    if not M.refresh() then
      M.state.view = old
    end
  end)
end

function M.clear()
  M.state.status, M.state.project, M.state.assignee, M.state.view = "open", nil, nil, nil
  M.refresh()
end

function M.quit()
  close_preview()
  require("work-kb.results").quit()
end

---버퍼 키: 키, 함수 이름, 설명. n·N·/는 Vim 검색에 남긴다.
M.KEYS = {
  { "<CR>", "open_issue", "열기" },
  { "o", "open_issue", "열기" },
  { "p", "preview", "미리 보기" },
  { "s", "cycle_status", "상태 거르기 (미완료 → 전체 → 완료)" },
  { "P", "pick_project", "프로젝트 거르기" },
  { "A", "pick_assignee", "담당 거르기" },
  { "V", "pick_view", "저장된 뷰" },
  { "x", "clear", "거르기 지움" },
  { "c", "comment", "댓글" },
  { "t", "transition", "전환" },
  { "a", "assign", "담당 지정" },
  { "w", "worklog", "워크로그" },
  { "+", "create", "새 일감" },
  { "S", "sync", "동기화" },
  { "r", "refresh", "다시 읽기" },
  { "y", "yank", "키 복사" },
  { "g?", "help", "도움말" },
  { "q", "quit", "닫기" },
}

function M.help()
  local lines = {}
  for _, k in ipairs(M.KEYS) do
    table.insert(lines, string.format("%-5s%s", k[1], k[3]))
  end
  ui().notify(table.concat(lines, "\n"))
end

local function attach(buf)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modifiable = false
  vim.bo[buf].filetype = "work-kb-issues"
  for _, k in ipairs(M.KEYS) do
    vim.keymap.set("n", k[1], function()
      require("work-kb").call({ name = "issues " .. k[2], module = "work-kb.issues", fn = k[2] })
    end, { buffer = buf, silent = true, nowait = true, desc = "work-kb: " .. k[3] })
  end
  for name, link in pairs(M.HIGHLIGHTS) do
    vim.api.nvim_set_hl(0, name, { link = link, default = true })
  end
  local group = vim.api.nvim_create_augroup("WorkKbIssues", { clear = true })
  vim.api.nvim_create_autocmd("CursorMoved", {
    group = group,
    buffer = buf,
    callback = function()
      M._moved()
    end,
  })
  vim.api.nvim_create_autocmd("BufWinLeave", {
    group = group,
    buffer = buf,
    callback = function()
      vim.schedule(function()
        if #vim.fn.win_findbuf(buf) == 0 then
          close_preview()
        end
      end)
    end,
  })
  vim.api.nvim_create_autocmd({ "WinResized", "VimResized" }, {
    group = group,
    callback = function()
      local win = list_buffer() and vim.fn.win_findbuf(buf)[1]
      if win and text_width(win) ~= M._width then
        draw(buf)
      end
    end,
  })
end

---J: 목록을 연다. 이미 보이면 그 창으로 간다.
function M.open()
  local buf = proc().get_buffer(M.BUFFER, true)
  if not vim.api.nvim_buf_is_loaded(buf) or vim.api.nvim_buf_line_count(buf) <= 1 then
    M._rows = {} -- 새 버퍼: 옛 줄 번호를 쓰지 않는다
  end
  local win = vim.fn.bufwinid(buf)
  if win ~= -1 then
    vim.api.nvim_set_current_win(win)
  else
    vim.api.nvim_win_set_buf(0, buf)
  end
  attach(buf)
  vim.wo[0][0].wrap = false
  vim.wo[0][0].cursorline = true
  M.refresh()
end

return M
