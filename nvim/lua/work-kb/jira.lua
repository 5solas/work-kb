-- j: Jira 작업 메뉴 (Emacs work-kb-jira transient, .el:1053-1437).
-- 쓰기는 모두 `kb.py act <하위 명령>`(outbox 경유)로 보낸다. 상태 전환·새 일감·승격은
-- 사용자가 고르고 확인한 뒤에만 `--yes`로 보낸다.
--
-- 메뉴: 일감 v c t a w, 만들기 n p, 조회·동기화 q S f d. 각 동작은 이 모듈의 함수이기도 하다
-- (view, comment, transition, assign, worklog, create, promote, query, sync, flush, check_done).
-- open.lua(thing_at_point, link_key, open_key, open_at_point)는 함수 안에서만 불러온다.
-- 입력을 취소하면(ui.input/select가 nil) "work-kb: 취소했습니다" 사용자 오류로 멈춘다.

local M = {}

M.JIRA_BUFFER = "*work-kb-jira*"
M.JQL_BUFFER = "*work-kb-jql*"
M.SYNC_BUFFER = "*work-kb-sync*"
M.CHECK_DONE_BUFFER = "*work-kb-check-done*"
M.STATUS_JSON_BUFFER = "*work-kb-status-json*"
M.DEFAULT_ISSUE_TYPES = { "Task", "Bug", "Story" }

local KEY_PATTERN = "[A-Z][A-Z0-9_]+%-[0-9]+"

local function proc()
  return require("work-kb.proc")
end

local function ui()
  return require("work-kb.ui")
end

local function config()
  return require("work-kb.config")
end

local function open()
  return require("work-kb.open")
end

---------------------------------------------------------------- 글 도우미

---Emacs string-trim (공백·탭·LF·CR).
local function trim(s)
  return ((s or ""):gsub("^[ \t\n\r]+", ""):gsub("[ \t\n\r]+$", ""))
end
M._trim = trim

---Emacs `%S`로 문자열을 보인 꼴: "따옴표", 안의 \와 "는 역슬래시로.
local function quote(s)
  return '"' .. tostring(s):gsub("\\", "\\\\"):gsub('"', '\\"') .. '"'
end
M._quote = quote

---KEY 전체가 Jira 키 꼴인가 (대소문자 구분).
local function is_key(s)
  return type(s) == "string" and s:match("^" .. KEY_PATTERN .. "$") ~= nil
end
M._is_key = is_key

local function fileio()
  return require("work-kb.fileio")
end

---LINE(기본 현재 줄)에서 첫 번째 홀로 선 Jira 키 (open.key_on_line; work-kb--key-on-line). 없으면 nil.
function M.key_on_line(line)
  return open().key_on_line(line)
end

---ui.input을 부르고, 취소(nil)면 사용자 오류로 멈춘다.
local function ask(prompt, opts)
  local answer = ui().input(prompt, opts)
  if answer == nil then
    ui().fail("work-kb: 취소했습니다")
  end
  return answer
end

local function choose(prompt, items, opts)
  local answer = ui().select(prompt, items, opts)
  if answer == nil then
    ui().fail("work-kb: 취소했습니다")
  end
  return answer
end

---Emacs format-prompt: "이름 (default X): " 또는 "이름: ".
local function format_prompt(name, default)
  if default ~= nil and default ~= "" then
    return string.format("%s (default %s): ", name, default)
  end
  return name .. ": "
end

---------------------------------------------------------------- 커서 위치의 키

---open.thing_at_point()의 돌려준 값을 (종류, 값)으로 맞춘다.
---{kind=, value=} / {type=, target=} / {"key", "KB-1"} / ("key", "KB-1") 꼴을 받는다.
function M._thing(a, b)
  if type(a) == "table" then
    local kind = a.kind or a.type or a[1]
    local value = a.value or a.target or a.key or a.name or a.text or a[2]
    return kind, value
  elseif type(a) == "string" then
    return a, b
  end
  return nil, nil
end

---설정의 볼트(없으면 코드 루트) 폴더. 검사하지 않는다 (Emacs `(or work-kb-vault work-kb-root)`).
local function configured_dir()
  local cfg = config().get()
  local dir = cfg.vault
  if type(dir) ~= "string" or dir == "" then
    dir = cfg.root
  end
  if type(dir) ~= "string" or dir == "" then
    return nil
  end
  return config().normalize(dir)
end

local function buffer_file(bufnr)
  local name = vim.api.nvim_buf_get_name(bufnr or 0)
  if name == "" or vim.bo[bufnr or 0].buftype ~= "" then
    return nil
  end
  return vim.fs.normalize(name)
end

---작업할 Jira 키 (묻지 않음). 없으면 nil.
---커서 위치의 키·키 링크 → 현재 줄의 첫 키 → 지금 연 jira/KEY.md의 키.
---커서가 키가 아닌 링크 위면 줄의 다른 키로 넘어가지 않고 nil.
function M.key_at_point()
  local kind, value = M._thing(open().thing_at_point())
  if kind == "key" then
    return value
  elseif kind == "link" then
    return open().link_key(value)
  end
  local key = M.key_on_line()
  if key then
    return key
  end
  local file = buffer_file(0)
  local dir = configured_dir()
  if file and dir then
    local prefix = (dir .. "/jira/"):lower()
    if file:lower():sub(1, #prefix) == prefix then
      local base = vim.fn.fnamemodify(file, ":t:r")
      if is_key(base) then
        return base
      end
    end
  end
  return nil
end

---커서 위치의 Jira 키. 없으면 "Jira 키: "로 묻는다 (앞뒤 공백을 떼고 대문자로).
function M.jira_key()
  local key = M.key_at_point()
  if key then
    return key
  end
  key = vim.fn.toupper(trim(ask("Jira 키: ")))
  if not is_key(key) then
    ui().fail(string.format("Jira 키가 아닙니다: %s (예: KB-12)", quote(key)))
  end
  return key
end

---------------------------------------------------------------- daily 줄 참조

---FILE이 볼트 daily/ 아래 .md면 "daily/<이름>.md", 아니면 nil (대소문자 무시).
function M.daily_relative(file)
  local dir = configured_dir()
  if type(file) ~= "string" or file == "" or not dir or not file:lower():match("%.md$") then
    return nil
  end
  local full = vim.fs.normalize(file)
  local daily = dir .. "/daily/"
  if full:lower():sub(1, #daily) == daily:lower() then
    return "daily/" .. full:sub(#daily + 1)
  end
  return nil
end

---BUFNR이 수정 없이 디스크보다 낡았으면 묻지 않고 다시 읽는다 (work-kb--revert-if-stale;
---fileio.refresh와 같다).
function M.revert_if_stale(bufnr)
  if bufnr and vim.api.nvim_buf_is_valid(bufnr) and vim.api.nvim_buf_is_loaded(bufnr) then
    fileio().refresh(bufnr)
  end
end

---현재 버퍼가 볼트의 daily 파일이면 "daily/<이름>.md:<줄 번호>", 아니면 nil.
---Python은 디스크의 줄을 읽으므로 저장 안 된 수정이 있으면 먼저 훅 없이 저장한다 (capture와 같은
---fileio 규칙). 버퍼와 파일이 모두 바뀌었으면 사용자 오류 (한 번 거부한 버퍼는 다시 읽거나 저장할
---때까지 계속 거부한다: fileio.both_changed).
function M.daily_line_ref()
  local bufnr = vim.api.nvim_get_current_buf()
  local rel = M.daily_relative(buffer_file(bufnr))
  if not rel then
    return nil
  end
  if vim.bo[bufnr].modified then
    local fio = fileio()
    if vim.uv.fs_stat(vim.api.nvim_buf_get_name(bufnr)) and fio.both_changed(bufnr) then
      ui().fail(string.format("%s: 버퍼와 파일이 모두 바뀌었습니다. 먼저 정리하세요", fio.buffer_label(bufnr)))
    end
    fio.write_buffer(bufnr, true)
  end
  return string.format("%s:%d", rel, vim.api.nvim_win_get_cursor(0)[1])
end

---현재 줄 (앞뒤 공백 제거).
local function current_line_text()
  return trim(vim.api.nvim_get_current_line())
end

---------------------------------------------------------------- JSON 출력 읽기

---TEXT의 START(`[` 또는 `{`)에서 시작하는 JSON 값이 끝나는 위치. 짝이 없으면 nil.
local function json_extent(text, start)
  local depth, in_string, escaped = 0, false, false
  for i = start, #text do
    local c = text:sub(i, i)
    if in_string then
      if escaped then
        escaped = false
      elseif c == "\\" then
        escaped = true
      elseif c == '"' then
        in_string = false
      end
    elseif c == '"' then
      in_string = true
    elseif c == "[" or c == "{" then
      depth = depth + 1
    elseif c == "]" or c == "}" then
      depth = depth - 1
      if depth == 0 then
        return i
      end
    end
  end
  return nil
end

---kb.py 출력 TEXT에서 JSON 값(배열·객체) 하나를 읽는다. 첫 글자가 `[`·`{`인 줄부터 차례로
---시도하고, 못 읽으면 다음 줄로 (앞의 로그 줄은 건너뛴다). 못 읽으면 nil.
function M.parse_json_output(text)
  text = text or ""
  local pos = 1
  while pos <= #text do
    local line_end = text:find("\n", pos, true) or (#text + 1)
    local line = text:sub(pos, line_end - 1)
    local lead = line:match("^[ \t]*()[%[{]")
    if lead then
      local start = pos + lead - 1
      local stop = json_extent(text, start)
      if stop then
        local ok, value = pcall(vim.json.decode, text:sub(start, stop))
        if ok then
          return value
        end
      end
    end
    pos = line_end + 1
  end
  return nil
end

---JSON 객체(비어 있지 않은 사전)인가.
local function is_object(v)
  return type(v) == "table" and next(v) ~= nil and not vim.islist(v)
end

---JSON 값을 비어 있지 않은 글로 (정수는 숫자 글로, 앞뒤 공백 제거). 아니면 nil.
local function json_text(v)
  local text
  if type(v) == "string" then
    text = v
  elseif type(v) == "number" and v == math.floor(v) and v == v and v ~= math.huge and v ~= -math.huge then
    text = string.format("%d", v)
  end
  if text then
    text = trim(text)
    if text ~= "" then
      return text
    end
  end
  return nil
end

---`act transitions KEY --json` 결과 DATA(풀어 둔 JSON)의 전환 후보: { {label=, value=}, ... }.
---표시는 "이름 → 도착 (id)", 보낼 값은 id(없으면 이름). 표시가 겹치면 처음 것만.
function M.transition_choices(data)
  local items = data
  if is_object(data) then
    items = data.transitions
  end
  local choices, seen = {}, {}
  if type(items) ~= "table" or not vim.islist(items) then
    return choices
  end
  for _, item in ipairs(items) do
    local choice
    local text = json_text(item)
    if text then
      choice = { label = text, value = text }
    elseif is_object(item) then
      local name = json_text(item.name)
      local id = json_text(item.id)
      local to_raw = item.to
      local to = json_text(is_object(to_raw) and to_raw.name or to_raw)
      if name then
        choice = {
          label = name .. (to and (" → " .. to) or "") .. (id and (" (" .. id .. ")") or ""),
          value = id or name,
        }
      end
    end
    if choice and not seen[choice.label] then
      seen[choice.label] = true
      table.insert(choices, choice)
    end
  end
  return choices
end

---kb.py 출력 TEXT의 전환 후보 (parse_json_output + transition_choices).
function M.parse_transitions(text)
  return M.transition_choices(M.parse_json_output(text))
end

local function find_project_keys(data, depth)
  if not is_object(data) or depth >= 4 then
    return nil
  end
  local direct = data.project_keys
  if type(direct) == "table" and #direct > 0 and vim.islist(direct) then
    local all_strings = true
    for _, v in ipairs(direct) do
      if type(v) ~= "string" then
        all_strings = false
        break
      end
    end
    if all_strings then
      return direct
    end
  end
  -- JSON 객체의 순서는 Lua 표에 남지 않으므로 키 이름 순으로 본다.
  local names = {}
  for k in pairs(data) do
    if type(k) == "string" then
      table.insert(names, k)
    end
  end
  table.sort(names)
  for _, k in ipairs(names) do
    local found = find_project_keys(data[k], depth + 1)
    if found then
      return found
    end
  end
  return nil
end

---status JSON(글 또는 풀어 둔 값) 어디든 있는 project_keys 목록 (문자열만, 깊이 4 미만). 없으면 nil.
function M.project_keys(text_or_data)
  local data = text_or_data
  if type(data) == "string" then
    data = M.parse_json_output(data)
  end
  return find_project_keys(data, 0)
end

---------------------------------------------------------------- 실행과 알림

---kb.py 인수: act SUB --root <볼트> ...
local function act_args(sub, ...)
  return proc().sub_args("act", sub, ...)
end

---act WHAT이 CODE로 끝난 뒤 알린다. 실패면 출력 버퍼를 보인다.
function M._act_message(what, code)
  local p = proc()
  local buf = p.get_buffer(M.JIRA_BUFFER, false)
  local last = p.last_line(buf)
  if code ~= 0 and buf then
    ui().show(buf)
  end
  if code == 0 then
    ui().notify(string.format("work-kb: %s 완료%s", what, last and (" — " .. last) or ""))
  elseif code == 3 then
    ui().notify(string.format("work-kb: %s — 네트워크 오류로 outbox에 남겼습니다. 나중에 f(flush)로 보냅니다", what))
  elseif code == 2 then
    ui().notify(string.format("work-kb: %s — 인증 실패 (outbox에 남김). 토큰을 확인하세요: kb.py init", what))
  else
    ui().notify(string.format("work-kb: %s 실패 (%s). %s 버퍼를 보세요", what, p.exit_reason(code), M.JIRA_BUFFER))
  end
end

---ARGS(`kb.py act …`)를 비동기로 보내고 끝나면 WHAT으로 알린다. AFTER가 있으면 AFTER(code).
local function act_run(what, args, after)
  return proc().run(args, {
    buffer = M.JIRA_BUFFER,
    on_exit = function(code)
      M._act_message(what, code)
      if after then
        after(code)
      end
    end,
  })
end

---결과 버퍼 NAME을 결과 모드(읽기 전용, <CR> → 커서 위치 열기)로 보이고 WHAT의 끝을 알린다
---(work-kb--show-results; results.lua).
local function show_results(name, what, code)
  return require("work-kb.results").show(name, what, code)
end

---프로젝트 키를 고른다. 후보는 `kb.py status --json`의 project_keys (없으면 직접 입력).
function M.read_project()
  local p = proc()
  local code, text = p.run_output(p.kb_args("status", "--json"), { buffer = M.STATUS_JSON_BUFFER })
  local keys = (code == 0) and M.project_keys(text) or nil
  local default = keys and keys[1] or nil
  local answer = choose(format_prompt("프로젝트", default), keys or {}, { default = default })
  local project = vim.fn.toupper(trim(answer))
  if not project:match("^[A-Z][A-Z0-9_]+$") then
    ui().fail(string.format("프로젝트 키가 아닙니다: %s (예: KB)", quote(project)))
  end
  return project
end

local function read_nonempty(prompt, opts)
  local text = trim(ask(prompt, opts))
  if text == "" then
    ui().fail("비어 있어 취소했습니다")
  end
  return text
end

local function issue_types()
  local types = config().get().jira_issue_types
  if type(types) == "table" and #types > 0 then
    return types
  end
  return M.DEFAULT_ISSUE_TYPES
end

---------------------------------------------------------------- 동작

---v: 커서 위치(또는 입력한) 키의 jira/KEY.md를 연다 (o와 같은 길).
function M.view()
  return open().open_key(M.jira_key())
end

---c: 댓글. 기본 본문은 현재 줄. daily에서 본문을 그대로 두면 `--from-line daily/…:N`,
---고쳤거나 daily가 아니면 `--body`.
function M.comment()
  local key = M.jira_key()
  local line = current_line_text()
  local daily = M.daily_relative(buffer_file(0))
  local body = trim(ask(string.format("%s 댓글: ", key), { initial = line }))
  if body == "" then
    ui().fail("빈 댓글은 보내지 않습니다")
  end
  local args
  if daily and body == line then
    args = act_args("comment", key, "--from-line", M.daily_line_ref())
  else
    args = act_args("comment", key, "--body", body)
  end
  return act_run(key .. " 댓글", args)
end

---t: 전환. `act transitions KEY --json`으로 후보를 받아 고르고, 확인하면 `--yes`로 보낸다.
function M.transition()
  local key = M.jira_key()
  local p = proc()
  local code, text = p.run_output(act_args("transitions", key, "--json"), { buffer = M.JIRA_BUFFER })
  if code ~= 0 then
    local buf = p.get_buffer(M.JIRA_BUFFER, false)
    if buf then
      ui().show(buf)
    end
    ui().fail(string.format("work-kb: %s 전환 목록을 받지 못했습니다 (%s)", key, p.exit_reason(code)))
  end
  local choices = M.parse_transitions(text)
  if #choices == 0 then
    ui().fail(string.format("work-kb: %s에서 할 수 있는 전환이 없습니다", key))
  end
  local labels = {}
  for i, c in ipairs(choices) do
    labels[i] = c.label
  end
  local label = ui().select(string.format("%s 전환: ", key), labels, { strict = true })
  local choice
  for _, c in ipairs(choices) do
    if c.label == label then
      choice = c
      break
    end
  end
  if not choice then
    ui().fail("전환을 고르지 않았습니다")
  end
  if ui().confirm(string.format("%s을(를) '%s'(으)로 전환할까요? ", key, label)) then
    return act_run(string.format("%s 전환(%s)", key, label), act_args("transition", key, choice.value, "--yes"))
  end
  ui().notify("work-kb: 전환을 취소했습니다")
  return nil
end

---a: 담당자 지정 (Jira 사용자명).
function M.assign()
  local key = M.jira_key()
  local user = trim(ask(string.format("%s 담당자 (Jira 사용자명): ", key)))
  if user == "" then
    ui().fail("담당자가 비었습니다")
  end
  return act_run(string.format("%s 담당 %s", key, user), act_args("assign", key, user))
end

---워크로그 시간 꼴: XhYm · Xh · Ym (jira_act.parse_duration과 같다; 빈 글은 아님).
function M.valid_duration(s)
  return s:match("^[0-9]+h[0-9]+m$") ~= nil or s:match("^[0-9]+h$") ~= nil or s:match("^[0-9]+m$") ~= nil
end

---w: 워크로그. 시간(예: 1h30m)과 설명(선택).
function M.worklog()
  local key = M.jira_key()
  local raw = ask(string.format("%s 작업 시간 (예: 1h30m): ", key))
  local spent = vim.fn.tolower((raw:gsub("[ \t]+", "")))
  if spent == "" or not M.valid_duration(spent) then
    ui().fail(string.format("시간 형식이 아닙니다: %s (예: 1h30m, 2h, 45m)", quote(spent)))
  end
  if not spent:find("[1-9]") then
    ui().fail(string.format("작업 시간은 1분 이상이어야 합니다: %s", quote(spent)))
  end
  local comment = trim(ask(string.format("%s 워크로그 설명 (선택): ", key)))
  local args = act_args("worklog", key, spent)
  if comment ~= "" then
    table.insert(args, "--comment")
    table.insert(args, comment)
  end
  return act_run(string.format("%s 워크로그 %s", key, spent), args)
end

---n: 새 일감. 프로젝트·요약·유형·설명을 받고 확인하면 `--yes`로 보낸다.
function M.create()
  local project = M.read_project()
  local summary = read_nonempty(string.format("%s 새 일감 요약: ", project))
  local types = issue_types()
  local issue_type = trim(choose(format_prompt("유형", types[1]), types, { default = types[1] }))
  local description = trim(ask("설명 (선택): "))
  if issue_type == "" then
    ui().fail("유형이 비었습니다")
  end
  if ui().confirm(string.format("%s에 %s 일감 '%s'을(를) 만들까요? ", project, issue_type, summary)) then
    local args = act_args("create", project, "--summary", summary, "--type", issue_type)
    if description ~= "" then
      table.insert(args, "--description")
      table.insert(args, description)
    end
    table.insert(args, "--yes")
    return act_run(project .. " 새 일감", args)
  end
  ui().notify("work-kb: 새 일감을 취소했습니다")
  return nil
end

---p: 현재 daily 줄을 새 일감으로 승격 (`act promote --from daily/…:N PROJECT --yes`).
---성공하면 kb.py가 그 줄 끝에 ` [[NEW-1]]`을 붙이므로 낡은 daily 버퍼를 다시 읽는다.
function M.promote()
  if not M.daily_relative(buffer_file(0)) then
    ui().fail("승격은 daily 파일의 줄에서만 됩니다")
  end
  local line = current_line_text()
  local bufnr = vim.api.nvim_get_current_buf()
  if line == "" then
    ui().fail("빈 줄은 승격할 수 없습니다")
  end
  local project = M.read_project()
  if ui().confirm(string.format("이 줄을 %s 일감으로 만들까요? %s ", project, line)) then
    return act_run(
      project .. " 승격",
      act_args("promote", "--from", M.daily_line_ref(), project, "--yes"),
      function(code)
        if code == 0 then
          M.revert_if_stale(bufnr)
        end
      end
    )
  end
  ui().notify("work-kb: 승격을 취소했습니다")
  return nil
end

---q: 라이브 JQL (`kb.py sync --jql`, 캐시에 쓰지 않음). JQL이 없으면 묻는다.
function M.query(jql)
  if jql == nil then
    jql = ask("JQL: ")
  end
  jql = trim(jql)
  if jql == "" then
    ui().fail("JQL이 비었습니다")
  end
  local p = proc()
  return p.run(p.kb_args("sync", "--jql", jql), {
    buffer = M.JQL_BUFFER,
    on_exit = function(code)
      show_results(M.JQL_BUFFER, "라이브 JQL", code)
    end,
  })
end

---S: 동기화 (`kb.py sync`).
function M.sync()
  local p = proc()
  ui().notify("work-kb: 동기화 중…")
  return p.run(p.kb_args("sync"), {
    buffer = M.SYNC_BUFFER,
    on_exit = function(code)
      local buf = p.get_buffer(M.SYNC_BUFFER, false)
      if code == 0 then
        local last = p.last_line(buf)
        ui().notify("work-kb: 동기화 완료" .. (last and (" — " .. last) or ""))
      else
        if buf then
          ui().show(buf)
        end
        ui().notify(string.format("work-kb: 동기화 실패 (%s). *work-kb-sync* 버퍼를 보세요", p.exit_reason(code)))
      end
    end,
  })
end

---f: outbox의 대기 항목을 다시 보낸다 (`kb.py act flush`).
function M.flush()
  return act_run("outbox 다시 보내기", act_args("flush"))
end

---d: 완료 전환 제안 (`kb.py today --check-done`). 제안만 보이고 아무것도 바꾸지 않는다.
function M.check_done()
  local p = proc()
  return p.run(p.kb_args("today", "--check-done"), {
    buffer = M.CHECK_DONE_BUFFER,
    on_exit = function(code)
      show_results(M.CHECK_DONE_BUFFER, "완료 전환 제안", code)
    end,
  })
end

---------------------------------------------------------------- 메뉴

---메뉴 항목: 무리, 키, 이름(함수), 설명.
M.MENU = {
  {
    group = "일감",
    items = {
      { key = "v", fn = "view", label = "보기" },
      { key = "c", fn = "comment", label = "댓글" },
      { key = "t", fn = "transition", label = "전환" },
      { key = "a", fn = "assign", label = "담당" },
      { key = "w", fn = "worklog", label = "워크로그" },
    },
  },
  {
    group = "만들기",
    items = {
      { key = "n", fn = "create", label = "새 일감" },
      { key = "p", fn = "promote", label = "현재 줄 승격" },
    },
  },
  {
    group = "조회·동기화",
    items = {
      { key = "q", fn = "query", label = "라이브 JQL" },
      { key = "S", fn = "sync", label = "동기화" },
      { key = "f", fn = "flush", label = "outbox 다시 보내기" },
      { key = "d", fn = "check_done", label = "완료 전환 제안" },
    },
  },
}

---동작 이름(view, check_done …) 또는 키(v, d …)의 메뉴 항목.
function M.action(name_or_key)
  for _, group in ipairs(M.MENU) do
    for _, item in ipairs(group.items) do
      if item.fn == name_or_key or item.key == name_or_key then
        return item
      end
    end
  end
  return nil
end

---메뉴 글 (nvim_echo 조각).
function M.menu_chunks()
  local chunks = { { "work-kb Jira", "Title" }, { "\n" } }
  for gi, group in ipairs(M.MENU) do
    table.insert(chunks, { group.group, "Title" })
    for _, item in ipairs(group.items) do
      table.insert(chunks, { "  " })
      table.insert(chunks, { item.key, "Special" })
      table.insert(chunks, { " " .. item.label })
    end
    if gi < #M.MENU then
      table.insert(chunks, { "\n" })
    end
  end
  return chunks
end

---키 하나를 읽는다 (이음매). 화면(UI)이 없으면 읽지 않는다 (ui._can_prompt):
---헤드리스에서 getcharstr()은 입력 끝(EOF)을 만나면 Neovim을 끝내 버린다.
function M._read_key()
  if not ui()._can_prompt() then
    ui().fail("work-kb: 화면이 없어 Jira 메뉴 키를 읽을 수 없습니다")
  end
  local ok, ch = pcall(vim.fn.getcharstr)
  if not ok then
    return nil
  end
  return ch
end

---j: 메뉴를 보이고 키 하나를 읽어 그 동작을 부른다. Esc·C-c·C-g는 그만둔다.
function M.menu()
  vim.api.nvim_echo(M.menu_chunks(), false, {})
  local ch = M._read_key()
  vim.api.nvim_echo({ { "" } }, false, {})
  if ch == nil or ch == "\27" or ch == "\3" or ch == "\7" then
    return nil
  end
  local item = M.action(ch)
  if not item then
    ui().notify(string.format("work-kb: Jira 메뉴에 없는 키입니다: %s", vim.fn.keytrans(ch)))
    return nil
  end
  return M[item.fn]()
end

---이름으로 동작을 부른다 (예: run("check_done") 또는 run("check-done")).
function M.run(name)
  local item = M.action((tostring(name or "")):gsub("%-", "_"))
  if not item then
    ui().fail("work-kb: 알 수 없는 Jira 작업: " .. tostring(name))
  end
  return M[item.fn]()
end

return M
