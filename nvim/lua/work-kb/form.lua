-- 새 일감·승격 양식. 위 창은 항목(읽기 전용, <CR>로 고르기), 아래 창은 설명(편집).
-- 항목과 필수 여부는 `kb.py meta create`(Jira의 생성 정보)를 따른다. 보내기는 확인 뒤 `kb.py act create|promote
-- … --yes --json`(outbox 경유)이고, 결과는 종료 코드가 아니라 출력의 state로 판단한다.
-- 쓴 내용(draft)은 양식을 닫아도 세션 동안 남는다. 콜백은 늘 자기 draft(d)에만 쓴다: 그사이 양식을 닫거나
-- 다른 양식을 열어도 섞이지 않는다.

local M = {}

M.BUFFER = "*work-kb-issue-form*"
M.DESC_BUFFER = "*work-kb-issue-description*"
M.META_BUFFER = "*work-kb-issue-meta*"
M.OUTPUT_BUFFER = "*work-kb-jira*"
M.FALLBACK_TYPES = { "Task", "Bug", "Story" }

M._live = nil -- 열린 양식: { d, buf, win, desc_buf, desc_win, rows }
M._draft = nil -- 닫아도 남는 내용
M._last = {} -- 마지막으로 만든 프로젝트·유형

local NONE = "—"
local DONE = "완료"
local COMMON = { priority = 1, assignee = 2, components = 3, labels = 4, duedate = 5 }

local function ui()
  return require("work-kb.ui")
end

local function proc()
  return require("work-kb.proc")
end

local function jira()
  return require("work-kb.jira")
end

---------------------------------------------------------------- 내용

local function new_draft(promote)
  return {
    project = M._last.project,
    issuetype = M._last.issuetype,
    summary = promote and promote.summary or "",
    description = "",
    values = {}, -- 항목 id → 글, { id, label }, 또는 { { id, label }, … }
    promote = promote, -- { ref, hash, buf, summary }
    expanded = false,
    sending = false,
    failed = nil, -- 실패한 outbox 기록 id (다시 보낼 때 먼저 치운다)
    warn = nil, -- 다시 보내면 중복될 수 있는 까닭
    error = nil,
    meta = nil,
    fresh = nil, -- 뒤에서 새로 받은 후보 (r로 반영)
    meta_seq = 0,
    keys = {},
  }
end

local function is_empty(d)
  local default = d.promote and d.promote.summary or ""
  return d.summary == default and vim.trim(d.description) == "" and next(d.values) == nil
end

local function live_for(d)
  return M._live ~= nil and M._live.d == d and M._live or nil
end

local function current_type(d)
  for _, t in ipairs(d.meta and d.meta.issuetypes or {}) do
    if t.name == d.issuetype then
      return t
    end
  end
  return nil
end

local function must(f)
  return f.required and not f.has_default
end

---보일 항목: 필수 → 흔한 것 → (펼치면) 나머지. 값이 있으면 늘 보인다. 둘째 값은 접힌 수.
local function visible_fields(d)
  local t = current_type(d)
  local required, common, rest, hidden = {}, {}, {}, 0
  for _, f in ipairs(t and t.fields or {}) do
    if must(f) then
      table.insert(required, f)
    elseif f.kind ~= "unsupported" and (COMMON[f.id] or f.kind == "epic") then
      table.insert(common, f)
    elseif d.expanded or d.values[f.id] ~= nil then
      table.insert(rest, f)
    else
      hidden = hidden + 1
    end
  end
  table.sort(common, function(a, b)
    return (COMMON[a.id] or 9) < (COMMON[b.id] or 9)
  end)
  return vim.list_extend(vim.list_extend(required, common), rest), hidden
end

local function is_choice(v)
  return type(v) == "table" and v.id ~= nil
end

local function display(value)
  if value == nil or value == "" then
    return NONE
  elseif type(value) == "string" then
    return value
  elseif is_choice(value) then
    return value.label
  end
  return table.concat(
    vim.tbl_map(function(v)
      return v.label
    end, value),
    ", "
  )
end

---`--field`로 보낼 값(JSON 글). 글은 글, 고른 것은 id, 여럿은 id 목록.
local function field_json(value)
  local p = proc()
  if type(value) == "string" then
    return p.json_string(value)
  elseif is_choice(value) then
    return p.json_string(value.id)
  end
  return p.json_array(vim.tbl_map(function(v)
    return v.id
  end, value))
end

---F에서 아직 쓸 수 있는 값만 남긴다.
local function still_valid(f, value)
  if f.kind ~= "option" and f.kind ~= "options" then
    return f.kind ~= "unsupported" and value or nil
  end
  local ids = {}
  for _, a in ipairs(f.allowed) do
    ids[a.id] = true
  end
  if f.kind == "option" then
    return is_choice(value) and ids[value.id] and value or nil
  end
  local kept = type(value) == "table" and not is_choice(value) and vim.tbl_filter(function(v)
    return ids[v.id]
  end, value) or {}
  return #kept > 0 and kept or nil
end

---지금 유형에 없는 값을 지우고 알린다. 후보를 받지 못한 동안에는 지우지 않는다.
local function prune(d, names)
  local t = current_type(d)
  if not t or d.meta.fallback then
    return
  end
  local by_id, dropped = {}, {}
  for _, f in ipairs(t.fields) do
    by_id[f.id] = f
  end
  for id, value in pairs(d.values) do
    local f = by_id[id]
    local kept = f and still_valid(f, value) or nil
    if kept == nil or (type(kept) == "table" and #kept ~= #value) then
      table.insert(dropped, (f and f.name) or (names and names[id]) or id)
    end
    d.values[id] = kept
  end
  if #dropped > 0 then
    table.sort(dropped)
    ui().notify("work-kb: 쓸 수 없는 값을 지웠습니다: " .. table.concat(dropped, ", "))
  end
end

local function field_names(d)
  local names = {}
  local t = current_type(d)
  for _, f in ipairs(t and t.fields or {}) do
    names[f.id] = f.name
  end
  return names
end

---------------------------------------------------------------- 그리기

local NS = vim.api.nvim_create_namespace("work-kb-issue-form")

local function build(d)
  local items = {
    { id = "project", label = "프로젝트", value = d.project, required = true },
    { id = "issuetype", label = "유형", value = d.issuetype, required = true },
    { id = "summary", label = "요약", value = d.summary, required = true },
  }
  local fields, hidden = visible_fields(d)
  for _, f in ipairs(fields) do
    local value = f.kind == "unsupported" and "(Jira 웹에서만)" or display(d.values[f.id])
    table.insert(items, { id = f.id, label = f.name, value = value, required = must(f), field = f })
  end
  local width = 0
  for _, item in ipairs(items) do
    width = math.max(width, vim.fn.strdisplaywidth(item.label))
  end
  local lines = { " 새 일감   C-c C-c 만들기  C-c C-k 닫기  <CR> 고르기  x 지움  d 설명  r 후보 새로  g? 도움말" }
  local rows, marks = {}, { { 0, "Title" } }
  local function note(text, hl)
    table.insert(lines, " " .. text:gsub("%c+", " "))
    table.insert(marks, { #lines - 1, hl })
  end
  if d.promote then
    note("승격: " .. d.promote.ref, "Comment")
  end
  if d.sending then
    note("보내는 중…", "DiagnosticInfo")
  end
  if d.error then
    note("오류: " .. d.error, "DiagnosticError")
  end
  if d.warn then
    note("주의: " .. d.warn, "DiagnosticWarn")
  end
  if not d.meta then
    note("후보를 받는 중…", "Comment")
  elseif d.meta.error then
    note("후보를 받지 못함: " .. d.meta.error, "DiagnosticWarn")
  end
  for _, item in ipairs(items) do
    local gap = string.rep(" ", width - vim.fn.strdisplaywidth(item.label))
    local text = (display(item.value):gsub("%c+", " "))
    table.insert(lines, string.format(" %s %s%s  %s", item.required and "*" or " ", item.label, gap, text))
    rows[#lines] = item
  end
  if hidden > 0 then
    note(string.format("g. 나머지 항목 %d개 펼치기", hidden), "Comment")
  end
  return lines, rows, marks
end

local function draw(d)
  local live = live_for(d)
  if not live or not vim.api.nvim_buf_is_loaded(live.buf) then
    return
  end
  local valid = vim.api.nvim_win_is_valid(live.win)
  local old = valid and live.rows[vim.api.nvim_win_get_cursor(live.win)[1]]
  local lines, rows, marks = build(d)
  vim.bo[live.buf].modifiable = true
  vim.api.nvim_buf_set_lines(live.buf, 0, -1, false, lines)
  vim.bo[live.buf].modifiable = false
  vim.api.nvim_buf_clear_namespace(live.buf, NS, 0, -1)
  for _, m in ipairs(marks) do
    vim.api.nvim_buf_set_extmark(live.buf, NS, m[1], 0, { end_col = #lines[m[1] + 1], hl_group = m[2] })
  end
  live.rows = rows
  if not valid then
    return
  end
  local want = old and old.id or "summary"
  for lnum, row in pairs(rows) do
    if row.id == want then
      vim.api.nvim_win_set_cursor(live.win, { lnum, 0 })
    end
  end
end

---------------------------------------------------------------- 후보 (meta)

local function apply_meta(d, data)
  local names = field_names(d)
  d.meta, d.fresh = data, nil
  if not current_type(d) then
    local first = data.issuetypes[1]
    local pick = first and first.name
    for _, t in ipairs(data.issuetypes) do
      if t.name == M._last.issuetype then
        pick = t.name
      end
    end
    d.issuetype = pick
  end
  prune(d, names)
  draw(d)
end

local function fallback(project, why)
  local types = vim.tbl_map(function(name)
    return { id = "", name = name, description = { available = true, required = false }, fields = {} }
  end, M.FALLBACK_TYPES)
  return { project = project, stale = true, fallback = true, error = why, issuetypes = types }
end

---후보를 받는다 (비동기). DONE_CB(data)는 프로젝트가 그사이 바뀌지 않았을 때만.
local function fetch_meta(d, refresh, done_cb)
  d.meta_seq = d.meta_seq + 1
  local seq, project, p = d.meta_seq, d.project, proc()
  local args = p.sub_args("meta", "create", project, "--json")
  if refresh then
    table.insert(args, "--refresh")
  end
  p.run(args, {
    buffer = M.META_BUFFER .. d.meta_seq % 2,
    on_exit = function(code, bufnr)
      if seq ~= d.meta_seq then
        return
      end
      local data
      for line in p.buffer_text(bufnr):gmatch("[^\r\n]+") do
        if line:sub(1, 1) == "{" then
          local ok, value = pcall(vim.json.decode, line, { luanil = { object = true, array = true } })
          data = ok and type(value) == "table" and type(value.issuetypes) == "table" and value or data
        end
      end
      if code ~= 0 or not data then
        data = fallback(project, p.last_line(bufnr) or p.exit_reason(code))
      end
      done_cb(data)
    end,
  })
end

local function load_meta(d)
  d.meta, d.fresh = nil, nil
  draw(d)
  fetch_meta(d, false, function(data)
    apply_meta(d, data)
    if data.stale and not data.fallback then
      fetch_meta(d, true, function(new)
        if new.fallback or new.stale or vim.deep_equal(new.issuetypes, d.meta.issuetypes) then
          return
        end
        d.fresh = new
        if live_for(d) then
          ui().notify("work-kb: 새 후보를 받았습니다. r로 반영합니다")
        end
      end)
    end
  end)
end

---------------------------------------------------------------- 창

---아래 창의 설명을 d에 옮긴다.
local function sync_description(live)
  if vim.api.nvim_buf_is_loaded(live.desc_buf) then
    live.d.description = table.concat(vim.api.nvim_buf_get_lines(live.desc_buf, 0, -1, false), "\n")
  end
end

---양식을 닫는다. 내용은 비어 있지 않으면 기억한다 (FORGET이면 버린다).
function M.close(forget)
  local live = M._live
  if not live then
    return
  end
  sync_description(live)
  M._live = nil
  if forget then
    M._draft = M._draft ~= live.d and M._draft or nil
  else
    M._draft = (live.d.sending or not is_empty(live.d)) and live.d or nil
  end
  for _, win in ipairs({ live.desc_win, live.win }) do
    if vim.api.nvim_win_is_valid(win) and not pcall(vim.api.nvim_win_close, win, true) then
      vim.wo[win].winfixbuf = false -- 마지막 창: 빈 버퍼로 바꾼다
      vim.api.nvim_win_call(win, function()
        vim.cmd("enew!")
      end)
    end
  end
  for _, buf in ipairs({ live.buf, live.desc_buf }) do
    if vim.api.nvim_buf_is_valid(buf) then
      pcall(vim.api.nvim_buf_delete, buf, { force = true })
    end
  end
end

local function map(buf, modes, lhs, fn, desc)
  vim.keymap.set(modes, lhs, function()
    require("work-kb").call({ name = "form " .. fn, module = "work-kb.form", fn = fn })
  end, { buffer = buf, silent = true, nowait = true, desc = "work-kb: " .. desc })
end

M.KEYS = {
  { "<CR>", "edit", "고르기·고치기" },
  { "x", "clear_value", "값 지움" },
  { "g.", "toggle_rest", "나머지 항목 펼치기·접기" },
  { "d", "goto_description", "설명으로" },
  { "r", "reload", "후보 새로 받기" },
  { "g?", "help", "도움말" },
  { "q", "close", "닫기 (내용은 기억)" },
}

local function scratch(name, filetype)
  local buf = vim.api.nvim_create_buf(false, true)
  pcall(vim.api.nvim_buf_set_name, buf, name)
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = filetype
  return buf
end

local function show(d)
  M._draft = d
  local buf, desc_buf = scratch(M.BUFFER, "work-kb-issue-form"), scratch(M.DESC_BUFFER, "work-kb-issue-description")
  vim.api.nvim_buf_set_lines(desc_buf, 0, -1, false, vim.split(d.description, "\n", { plain = true }))
  vim.cmd("tab split")
  local win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(win, buf)
  local height = math.max(3, math.floor(vim.api.nvim_win_get_height(win) * 0.4))
  local desc_win = vim.api.nvim_open_win(desc_buf, false, { split = "below", win = win, height = height })
  vim.wo[win][0].wrap = false
  vim.wo[win][0].cursorline = true
  vim.wo[desc_win][0].wrap = true
  vim.wo[desc_win][0].winbar = " 설명   C-c C-c 만들기  C-c C-k 닫기"
  vim.wo[win][0].winfixbuf = true
  vim.wo[desc_win][0].winfixbuf = true
  local live = { d = d, buf = buf, win = win, desc_buf = desc_buf, desc_win = desc_win, rows = {} }
  M._live = live
  for _, k in ipairs(M.KEYS) do
    map(buf, "n", k[1], k[2], k[3])
  end
  for _, b in ipairs({ buf, desc_buf }) do
    map(b, b == buf and "n" or { "n", "i" }, "<C-c><C-c>", "submit", "만들기")
    map(b, b == buf and "n" or { "n", "i" }, "<C-c><C-k>", "close", "닫기 (내용은 기억)")
  end
  local group = vim.api.nvim_create_augroup("WorkKbIssueForm", { clear = true })
  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "TextChangedP", "BufLeave" }, {
    group = group,
    buffer = desc_buf,
    callback = function()
      sync_description(live)
    end,
  })
  vim.api.nvim_create_autocmd("WinClosed", {
    group = group,
    pattern = { tostring(win), tostring(desc_win) },
    callback = function()
      vim.schedule(function()
        if M._live == live then
          M.close()
        end
      end)
    end,
  })
  draw(d)
end

---설정된 프로젝트 키 (status --json). 없으면 {}.
local function project_keys()
  local p = proc()
  local ok, code, text = pcall(p.run_output, p.kb_args("status", "--json"), { buffer = jira().STATUS_JSON_BUFFER })
  return ok and code == 0 and jira().project_keys(text) or {}
end

local function ask_project()
  local answer = ui().input("프로젝트 키: ")
  if answer == nil then
    ui().fail("work-kb: 취소했습니다")
  end
  local key = vim.fn.toupper(vim.trim(answer))
  if not key:match("^[A-Z][A-Z0-9_]+$") then
    ui().fail(string.format('프로젝트 키가 아닙니다: "%s" (예: KB)', key))
  end
  return key
end

---양식을 연다. opts.promote = { ref, hash, buf, summary }면 그 daily 줄을 승격한다.
function M.open(opts)
  local promote = opts and opts.promote
  if M._live then
    vim.api.nvim_set_current_win(M._live.win)
    if promote then
      ui().notify("work-kb: 이미 열린 양식이 있습니다. 먼저 끝내거나 닫으세요")
    end
    return
  end
  local d
  local old = M._draft
  if old then
    local title = vim.trim(old.summary) ~= "" and old.summary or "(요약 없음)"
    local same = promote and old.promote and old.promote.ref == promote.ref and old.promote.hash == promote.hash
    if promote and not same then
      if not ui().confirm(string.format("쓰던 양식 '%s'을(를) 버리고 이 줄을 승격할까요? ", title)) then
        ui().notify("work-kb: 승격을 취소했습니다. 쓰던 양식은 <leader>kj n으로 엽니다")
        return
      end
    elseif ui().confirm(string.format("쓰던 새 일감 '%s'을(를) 이어 쓸까요? (n이면 버리고 새로) ", title)) then
      d = old
    end
  end
  d = d or new_draft(promote)
  d.keys = project_keys()
  if not d.project or (#d.keys > 0 and not vim.tbl_contains(d.keys, d.project)) then
    d.project = d.keys[1] or ask_project()
  end
  show(d)
  load_meta(d)
end

---------------------------------------------------------------- 항목 고치기

local function row_at_cursor()
  local live = M._live
  local row = live and live.rows[vim.api.nvim_win_get_cursor(live.win)[1]]
  if not row then
    ui().fail("항목 줄이 아닙니다")
  end
  return live.d, row
end

local function set_value(d, id, value)
  d.values[id] = value
  draw(d)
end

---글을 묻는다. 취소면 아무것도 하지 않는다.
local function ask_text(prompt, initial, cb)
  local answer = ui().input(prompt, { initial = initial })
  if answer ~= nil then
    cb(vim.trim(answer))
  end
end

---{ 라벨 목록, 라벨 → 고른 것 } (이름이 겹치면 id를 붙인다).
local function choices(f)
  local labels, map_, count = {}, {}, {}
  for _, a in ipairs(f.allowed) do
    count[a.name] = (count[a.name] or 0) + 1
  end
  for _, a in ipairs(f.allowed) do
    local label = count[a.name] > 1 and string.format("%s (%s)", a.name, a.id) or a.name
    table.insert(labels, label)
    map_[label] = { id = a.id, label = label }
  end
  return labels, map_
end

local EDIT = {}

function EDIT.option(d, f)
  local labels, by_label = choices(f)
  ui().pick(f.name, labels, function(choice)
    if choice and by_label[choice] then
      set_value(d, f.id, by_label[choice])
    end
  end)
end

function EDIT.options(d, f)
  local labels, by_label = choices(f)
  local function loop()
    local on = {}
    for _, v in ipairs(d.values[f.id] or {}) do
      on[v.id] = true
    end
    local items, by_item = { DONE }, {}
    for _, label in ipairs(labels) do
      local item = (on[by_label[label].id] and "✓ " or "  ") .. label
      table.insert(items, item)
      by_item[item] = by_label[label]
    end
    ui().pick(f.name .. " (켜고 끄기)", items, function(choice)
      local chosen = choice and by_item[choice]
      if not chosen then
        return
      end
      local kept = vim.tbl_filter(function(v)
        return v.id ~= chosen.id
      end, d.values[f.id] or {})
      if not on[chosen.id] then
        table.insert(kept, chosen)
      end
      set_value(d, f.id, #kept > 0 and kept or nil)
      if live_for(d) then
        vim.schedule(function()
          ui().protect(loop)
        end)
      end
    end)
  end
  loop()
end

local function as_choice(user)
  local label = user.display == user.name and user.name or string.format("%s (%s)", user.display, user.name)
  return { id = user.name, label = label }
end

function EDIT.user(d, f)
  jira().pick_user({ project = d.project }, f.name, function(user)
    set_value(d, f.id, as_choice(user))
  end)
end

function EDIT.users(d, f)
  jira().pick_user({ project = d.project }, f.name .. " 추가", function(user)
    local list = vim.tbl_filter(function(v)
      return v.id ~= user.name
    end, d.values[f.id] or {})
    table.insert(list, as_choice(user))
    set_value(d, f.id, list)
  end)
end

function EDIT.labels(d, f)
  local current = {}
  for _, v in ipairs(d.values[f.id] or {}) do
    table.insert(current, v.label)
  end
  ask_text(f.name .. " (공백·쉼표로 나눔): ", table.concat(current, " "), function(text)
    local list, seen = {}, {}
    for word in text:gmatch("[^%s,]+") do
      if not seen[word] then
        seen[word] = true
        table.insert(list, { id = word, label = word })
      end
    end
    set_value(d, f.id, #list > 0 and list or nil)
  end)
end

function EDIT.epic(d, f)
  jira().pick_epic(f.name, function(key)
    set_value(d, f.id, key)
  end)
end

function EDIT.unsupported(_, f)
  ui().notify(string.format("work-kb: %s은(는) Jira 웹에서만 채울 수 있습니다", f.name))
end

local HINT = { date = " (예: 2026-11-30)", datetime = " (예: 2026-11-30T09:00)", number = " (숫자)" }

local function edit_text(d, f)
  ask_text(f.name .. (HINT[f.kind] or "") .. ": ", d.values[f.id] or "", function(text)
    set_value(d, f.id, text ~= "" and text or nil)
  end)
end

function M.edit()
  local d, row = row_at_cursor()
  if row.id == "project" then
    local function change(key)
      if key ~= d.project then
        d.project = key
        load_meta(d)
      end
    end
    if #d.keys == 0 then
      return change(ask_project())
    end
    return ui().pick("프로젝트", d.keys, function(choice)
      if choice then
        change(choice)
      end
    end)
  elseif row.id == "issuetype" then
    local names = vim.tbl_map(function(t)
      return t.name
    end, d.meta and d.meta.issuetypes or {})
    if #names == 0 then
      ui().fail("후보를 받는 중입니다")
    end
    local fallback_mode = d.meta.fallback
    if fallback_mode then
      table.insert(names, jira().DIRECT)
    end
    return ui().pick("유형", names, function(choice)
      local function set(name)
        local old = field_names(d)
        if fallback_mode and name ~= "" then
          local known = vim.tbl_contains(names, name)
          if not known then
            table.insert(d.meta.issuetypes, { id = "", name = name, description = { available = true }, fields = {} })
          end
        end
        d.issuetype = name
        prune(d, old)
        draw(d)
      end
      if choice == jira().DIRECT then
        ask_text("유형: ", "", function(text)
          if text ~= "" then
            set(text)
          end
        end)
      elseif choice then
        set(choice)
      end
    end)
  elseif row.id == "summary" then
    return ask_text("요약: ", d.summary, function(text)
      d.summary = text
      draw(d)
    end)
  end
  return (EDIT[row.field.kind] or edit_text)(d, row.field)
end

function M.clear_value()
  local d, row = row_at_cursor()
  if row.id == "summary" then
    d.summary = ""
  elseif row.field then
    d.values[row.id] = nil
  else
    ui().notify("work-kb: 프로젝트와 유형은 지울 수 없습니다")
  end
  draw(d)
end

function M.toggle_rest()
  local d = M._live.d
  d.expanded = not d.expanded
  draw(d)
end

function M.goto_description()
  vim.api.nvim_set_current_win(M._live.desc_win)
end

function M.reload()
  local d = M._live.d
  if d.fresh then
    return apply_meta(d, d.fresh)
  end
  fetch_meta(d, true, function(data)
    if data.fallback and d.meta and not d.meta.fallback then
      ui().notify("work-kb: 후보를 새로 받지 못했습니다: " .. tostring(data.error))
      return
    end
    apply_meta(d, data)
  end)
end

function M.help()
  local lines = {}
  for _, k in ipairs(M.KEYS) do
    table.insert(lines, string.format("%-9s%s", k[1], k[3]))
  end
  table.insert(lines, "C-c C-c  만들기 (확인을 묻는다)")
  table.insert(lines, "C-c C-k  닫기 (내용은 기억)")
  ui().notify(table.concat(lines, "\n"))
end

---------------------------------------------------------------- 보내기

local function goto_row(id)
  local live = M._live
  vim.api.nvim_set_current_win(live.win)
  for lnum, row in pairs(live.rows) do
    if row.id == id then
      vim.api.nvim_win_set_cursor(live.win, { lnum, 0 })
    end
  end
end

---보내기 전 검사. 비어 있으면 그 줄로 가고 사용자 오류.
local function check(d)
  if not d.meta then
    ui().fail("후보를 받는 중입니다. 잠시 뒤 다시 하세요")
  end
  local function missing(id, name)
    goto_row(id)
    ui().fail("필수 항목이 비었습니다: " .. name)
  end
  local t = current_type(d)
  for _, f in ipairs(t and t.fields or {}) do
    if f.kind == "unsupported" and must(f) then
      ui().fail("이 유형은 Jira 웹에서 만들어야 합니다: " .. f.name)
    end
  end
  if not t then
    missing("issuetype", "유형")
  end
  if vim.trim(d.summary) == "" then
    missing("summary", "요약")
  end
  for _, f in ipairs(t.fields) do
    if must(f) and d.values[f.id] == nil then
      missing(f.id, f.name)
    end
  end
  if t.description and t.description.required and vim.trim(d.description) == "" then
    M.goto_description()
    ui().fail("필수 항목이 비었습니다: 설명")
  end
  return t
end

local function build_args(d, t)
  local args
  if d.promote then
    args = proc().sub_args("act", "promote", "--from", d.promote.ref, d.project)
    if vim.trim(d.summary) ~= d.promote.summary then
      table.insert(args, "--summary=" .. d.summary)
    end
  else
    args = proc().sub_args("act", "create", d.project, "--summary=" .. d.summary)
  end
  table.insert(args, "--type=" .. t.name)
  if d.promote then
    table.insert(args, "--expect-line=" .. d.promote.hash)
  end
  if vim.trim(d.description) ~= "" then
    table.insert(args, "--description=" .. d.description)
  end
  for _, f in ipairs(t.fields) do
    local value = d.values[f.id]
    if value ~= nil and f.kind ~= "unsupported" then
      table.insert(args, "--field=" .. f.id .. "=" .. field_json(value))
    end
  end
  return vim.list_extend(args, { "--yes", "--json" })
end

---act의 출력에서 결과 줄을 읽는다. 읽지 못하면 불확실로 본다 (보냈을 수 있다).
local function parse_result(text)
  local found
  for line in text:gmatch("[^\r\n]+") do
    if line:sub(1, 1) == "{" then
      local ok, value = pcall(vim.json.decode, line, { luanil = { object = true, array = true } })
      if ok and type(value) == "table" and type(value.state) == "string" then
        found = value
      end
    end
  end
  return found or { state = "uncertain", message = "결과를 읽지 못했습니다" }
end

local function after_promote(d, res)
  jira().revert_if_stale(d.promote.buf)
  local buf = d.promote.buf
  if vim.api.nvim_buf_is_valid(buf) and vim.bo[buf].modified then
    ui().notify(
      string.format(
        "work-kb: %s: 버퍼가 수정돼 있어 다시 읽지 못했습니다. 저장하기 전에 디스크의 승격 링크를 확인하세요",
        require("work-kb.fileio").buffer_label(buf)
      )
    )
  end
  if res.linked == false then
    ui().notify(string.format("work-kb: %s: daily 줄에 [[%s]]를 붙이지 못했습니다", d.promote.ref, res.key or "?"))
  end
end

local function finish(d, res)
  d.sending = false
  local live = live_for(d)
  if live then
    sync_description(live)
  end
  local message = res.message and res.message ~= "" and (" — " .. res.message) or ""
  if res.state == "done" then
    M._last = { project = d.project, issuetype = d.issuetype }
    if live then
      M.close(true)
    end
    if M._draft == d then
      M._draft = nil
    end
    ui().notify(string.format("work-kb: %s을(를) 만들었습니다", res.key or "새 일감"))
    if d.promote then
      after_promote(d, res)
    end
    require("work-kb.issues").refresh()
    return
  end
  if res.state == "pending" or res.state == "uncertain" or res.state == "sending" then
    local pending = res.state == "pending"
    d.warn = pending and "앞의 전송이 outbox에 대기 중입니다. 다시 보내면 중복됩니다"
      or "앞의 전송 결과를 모릅니다. Jira에서 확인하세요. 다시 보내면 중복될 수 있습니다"
    if live then
      M.close()
    end
    if pending then
      ui().notify("work-kb: 아직 보내지 못해 outbox에 남겼습니다. 나중에 f(flush)로 보냅니다" .. message)
    else
      ui().notify("work-kb: 보냈는지 알 수 없습니다. Jira에서 확인하세요" .. message)
    end
    return
  end
  -- failed·none: 양식을 그대로 두고 고쳐 다시 보낼 수 있게 한다
  d.error = res.message and res.message ~= "" and res.message or "보내지 못했습니다"
  d.failed = res.state == "failed" and res.record or nil
  draw(d)
  ui().notify("work-kb: 만들지 못했습니다" .. message)
end

local function run_act(d, args, on_exit)
  local p = proc()
  local ok, err = pcall(p.run, args, {
    buffer = M.OUTPUT_BUFFER,
    on_exit = function(code, bufnr)
      on_exit(code, p.buffer_text(bufnr) or "", bufnr)
    end,
  })
  if not ok then -- 실행하지 못함(Python 없음 등): 보내지 않았다
    finish(d, { state = "none", message = tostring(err) })
  end
end

function M.submit()
  local live = M._live
  local d = live.d
  if d.sending then
    ui().notify("work-kb: 보내는 중입니다")
    return
  end
  sync_description(live)
  local t = check(d)
  local question = string.format("%s에 %s 일감 '%s'을(를) 만들까요? ", d.project, t.name, d.summary)
  local known, lost = {}, 0
  for _, f in ipairs(t.fields) do
    known[f.id] = true
  end
  for id in pairs(d.values) do
    lost = lost + (known[id] and 0 or 1)
  end
  if lost > 0 then
    question = string.format("(후보를 받지 못해 항목 %d개는 빠집니다) ", lost) .. question
  end
  if d.warn then
    question = "(" .. d.warn .. ") " .. question
  end
  -- 확인한 내용 그대로 보낸다 (확인하는 동안 바뀌지 않게 먼저 만든다)
  local args = build_args(d, t)
  d.sending = true
  if not ui().confirm(question) then
    d.sending = false
    return
  end
  d.error = nil
  draw(d)
  local function send()
    run_act(d, args, function(_, text)
      finish(d, parse_result(text))
    end)
  end
  if not d.failed then
    return send()
  end
  run_act(d, proc().sub_args("act", "discard", d.failed), function(code)
    if code == 0 then
      d.failed = nil
      return send()
    end
    d.sending = false
    if code == 4 then -- 그사이 다시 보냈거나 치웠다
      d.failed = nil
      d.warn = "앞의 실패 기록의 상태가 바뀌었습니다. 다시 보내면 중복될 수 있습니다"
    end
    draw(d)
    ui().notify("work-kb: 앞의 실패 기록을 치우지 못해 보내지 않았습니다. kb.py act list로 확인하세요")
  end)
end

---시험용: 상태를 처음으로.
function M._reset()
  if M._live then
    M.close(true)
  end
  M._draft, M._last = nil, {}
end

return M
