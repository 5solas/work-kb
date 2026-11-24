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

return M
