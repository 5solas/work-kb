-- j: Jira 메뉴 (ERT work-kb-test-jira-* 17개). open.lua는 다른 작업이 쓰는 중이라
-- package.loaded["work-kb.open"]에 가짜를 넣는다 (thing_at_point·link_key는 Emacs 규칙을 줄여 옮김).
local H = require("helpers")

local KEY = "[A-Z][A-Z0-9_]+%-[0-9]+"

local function jira()
  return require("work-kb.jira")
end

local function proc()
  return require("work-kb.proc")
end

local function trim(s)
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function key_char(b)
  return b ~= nil and string.char(b):match("[A-Za-z0-9_%-]") ~= nil
end

---가짜 open 모듈. thing_at_point는 현재 줄·커서(바이트 열)로 링크 → 키 → @이름을 찾는다.
local function stub_open()
  local s = { opened = {}, at_point = 0 }
  local function link_key(target)
    local name = trim(target):gsub("^[jJ][iI][rR][aA]/", ""):gsub("%.[mM][dD]$", "")
    return name:match("^" .. KEY .. "$") and name or nil
  end
  local function thing_at_point()
    local line = vim.api.nvim_get_current_line()
    local col = vim.api.nvim_win_get_cursor(0)[2]
    for s0, inner, e0 in line:gmatch("()%[%[([^%]]-)%]%]()") do
      local target = inner:match("^([^|#]+)")
      if target and s0 - 1 <= col and col <= e0 - 1 then
        target = trim(target)
        if target:match("^" .. KEY .. "$") then
          return { kind = "key", value = target }
        end
        return { kind = "link", value = target }
      end
    end
    local init = 1
    while true do
      local s1, e1 = line:find(KEY, init)
      if not s1 then
        break
      end
      if s1 - 1 <= col and col <= e1 and not key_char(line:byte(s1 - 1)) and not key_char(line:byte(e1 + 1)) then
        return { kind = "key", value = line:sub(s1, e1) }
      end
      init = e1 + 1
    end
    return nil
  end
  s.mod = {
    thing_at_point = thing_at_point,
    link_key = link_key,
    open_key = function(key)
      table.insert(s.opened, key)
      return "opened"
    end,
    open_at_point = function()
      s.at_point = s.at_point + 1
    end,
  }
  H.patch(package.loaded, "work-kb.open", s.mod)
  return s
end

---H.tmp() 아래 파일을 보던 버퍼를 지운다 (앞 테스트의 찌꺼기).
local function fresh()
  local tmp = H.tmp():lower()
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    local name = vim.fs.normalize(vim.api.nvim_buf_get_name(b)):lower()
    if name ~= "" and name:sub(1, #tmp) == tmp then
      pcall(vim.api.nvim_buf_delete, b, { force = true })
    end
  end
end

---proc.run 기록: { args, buffer }. 진짜 run(가짜 _spawn 위)을 부른다.
local function record_runs()
  local p = proc()
  local real = p.run
  local rec = { calls = {}, handles = {} }
  H.patch(p, "run", function(args, opts)
    table.insert(rec.calls, { args = vim.deepcopy(args), buffer = opts and opts.buffer })
    local h = real(args, opts)
    table.insert(rec.handles, h)
    return h
  end)
  function rec.settle()
    H.wait(function()
      for _, h in ipairs(rec.handles) do
        if not h.done then
          return false
        end
      end
      return true
    end, 5000)
  end
  function rec.args()
    local out = {}
    for i, c in ipairs(rec.calls) do
      out[i] = c.args
    end
    return out
  end
  return rec
end

local function act(vault, sub, ...)
  return vim.list_extend({ "act", sub, "--root", vault }, { ... })
end

---스크래치 버퍼에 LINES를 넣고 NEEDLE 뒤(BACK이면 NEEDLE의 마지막 글자)에 커서를 둔다.
local function scratch(lines, needle, back)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, type(lines) == "table" and lines or { lines })
  if needle then
    for lnum, line in ipairs(vim.api.nvim_buf_get_lines(buf, 0, -1, false)) do
      local s = line:find(needle, 1, true)
      if s then
        local col = s - 1 + #needle
        if back then
          col = col - 1
          while col > 0 and line:byte(col + 1) >= 0x80 and line:byte(col + 1) < 0xC0 do
            col = col - 1
          end
        end
        vim.api.nvim_win_set_cursor(0, { lnum, col })
        break
      end
    end
  end
  return buf
end

local JIRA_DAILY = table.concat({
  "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n",
  "- [ ] [[KB-12]] 캐시 동기화\n<!-- kb:today:end -->\n\n## 기록\n",
  "- 08:00 첫 기록\n", -- 10
  "- 09:12 회의 [[KB-12]] 배포 일정 확정\n", -- 11
  "- [ ] 09:30 경비 정산\n", -- 12
})

---VAULT의 daily/2026-11-05.md를 쓰고 열어 NEEDLE의 마지막 글자에 커서를 둔다.
local function visit_daily(vault, needle, text)
  local file = vault .. "/daily/2026-11-05.md"
  H.write_bytes(file, text or JIRA_DAILY)
  vim.cmd("edit " .. vim.fn.fnameescape(file))
  local buf = vim.api.nvim_get_current_buf()
  for lnum, line in ipairs(vim.api.nvim_buf_get_lines(buf, 0, -1, false)) do
    local s = line:find(needle, 1, true)
    if s then
      local col = s - 1 + #needle - 1
      while col > 0 and line:byte(col + 1) >= 0x80 and line:byte(col + 1) < 0xC0 do
        col = col - 1
      end
      vim.api.nvim_win_set_cursor(0, { lnum, col })
      break
    end
  end
  return buf, file
end

---다른 프로세스가 고친 것처럼 FILE을 TEXT로 다시 쓰고 수정 시각을 10초 미룬다.
local function rewrite_on_disk(file, text)
  H.write_bytes(file, text)
  local t = os.time() + 10
  assert(vim.uv.fs_utime(file, t, t))
end

local function prompt_list(stub)
  local out = {}
  for _, p in ipairs(stub.prompts) do
    table.insert(out, p.prompt)
  end
  return out
end

local TRANSITIONS_OUTPUT = table.concat({
  "jira_act: KB-9 전환 후보\n",
  '[{"id": "41", "name": "Done", "to": "Done", "to_category": "done"}, ',
  '{"id": "42", "name": "Done", "to": "Closed", "to_category": "done"}, ',
  '{"id": "43", "name": "In Progress", "to": "In Progress", "to_category": "indeterminate"}]\n',
})

local STATUS_JSON = '{\n "vault": "D:/한글 볼트",\n "project_keys": ["KB", "OPS"],\n "outbox": {"pending": 0, "failed": 0}\n}\n'

describe("jira", function()
  it("jira-transient-keys: 메뉴 세 무리와 키 → 함수, 메뉴가 키 하나를 읽어 부른다", function()
    fresh()
    local j = jira()
    local expected = {
      { "일감", { { "v", "view" }, { "c", "comment" }, { "t", "transition" }, { "a", "assign" }, { "w", "worklog" } } },
      { "만들기", { { "n", "create" }, { "p", "promote" } } },
      { "조회·동기화", { { "q", "query" }, { "S", "sync" }, { "f", "flush" }, { "d", "check_done" } } },
    }
    H.eq(#j.MENU, 3)
    for gi, group in ipairs(expected) do
      H.eq(j.MENU[gi].group, group[1])
      for ii, pair in ipairs(group[2]) do
        H.eq({ j.MENU[gi].items[ii].key, j.MENU[gi].items[ii].fn }, pair)
        H.eq(type(j[pair[2]]), "function", pair[2])
      end
      H.eq(#j.MENU[gi].items, #group[2])
    end
    -- j는 init.keys에서 work-kb.jira.menu
    local found
    for _, e in ipairs(require("work-kb").keys) do
      if e.key == "j" then
        found = e
      end
    end
    H.eq({ found.module, found.fn, found.name }, { "work-kb.jira", "menu", "jira" })
    -- 메뉴: 글을 보이고 키 하나로 동작을 부른다
    local stub = H.ui_stub()
    local echoed = {}
    H.patch(vim.api, "nvim_echo", function(chunks)
      local parts = {}
      for _, c in ipairs(chunks) do
        table.insert(parts, c[1])
      end
      table.insert(echoed, table.concat(parts))
    end)
    local called = {}
    for _, group in ipairs(expected) do
      for _, pair in ipairs(group[2]) do
        H.patch(j, pair[2], function()
          table.insert(called, pair[2])
          return pair[2]
        end)
        H.patch(j, "_read_key", function()
          return pair[1]
        end)
        H.eq(j.menu(), pair[2])
      end
    end
    H.eq(called, { "view", "comment", "transition", "assign", "worklog", "create", "promote", "query", "sync", "flush", "check_done" })
    H.contains(echoed[1], "일감")
    H.contains(echoed[1], "만들기")
    H.contains(echoed[1], "조회·동기화")
    H.contains(echoed[1], "v 보기")
    H.contains(echoed[1], "d 완료 전환 제안")
    -- Esc는 그만둠, 없는 키는 알림
    H.patch(j, "_read_key", function()
      return "\27"
    end)
    H.eq(j.menu(), nil)
    H.patch(j, "_read_key", function()
      return "x"
    end)
    H.eq(j.menu(), nil)
    H.eq(stub.messages, { "work-kb: Jira 메뉴에 없는 키입니다: x" })
    H.eq(#called, 11)
    -- 이름으로도 부른다
    H.eq(j.run("check-done"), "check_done")
    H.eq(j.run("view"), "view")
    H.fails_with(function()
      j.run("없음")
    end, "work-kb: 알 수 없는 Jira 작업: 없음")
  end)

  it("메뉴 키 읽기: 화면도 미리 넣은 키도 없으면 읽지 않고 사용자 오류 (헤드리스가 끝나지 않게)", function()
    fresh()
    local j = jira()
    H.eq(#vim.api.nvim_list_uis(), 0, "이 시험은 헤드리스 전용")
    local err = H.fails_with(function()
      j._read_key()
    end, "work-kb: 화면이 없어 Jira 메뉴 키를 읽을 수 없습니다")
    H.ok(require("work-kb.ui").is_user_error(err))
    vim.api.nvim_feedkeys("S", "t", false)
    H.eq(j._read_key(), "S")
  end)

  it("jira-key-at-point: 커서 위치 → 줄의 첫 키 → jira/KEY.md 이름, 키가 아닌 링크면 nil", function()
    fresh()
    stub_open()
    local j = jira()
    scratch({ "회의 [[KB-12]] 와 OPS-3 배포", "키 없는 줄 xKB-1" }, "OPS-")
    H.eq(j.key_at_point(), "OPS-3")
    scratch({ "회의 [[KB-12]] 와 OPS-3 배포", "키 없는 줄 xKB-1" }, "배포")
    H.eq(j.key_at_point(), "KB-12")
    vim.api.nvim_win_set_cursor(0, { 2, 0 })
    H.eq(j.key_at_point(), nil)
    local function at(line, needle)
      scratch(line, needle, true)
      return j.key_at_point()
    end
    -- 링크 대상은 jira/·.md·별칭·제목을 떼고 본다 (줄의 첫 키 OPS-3으로 새지 않는다)
    H.eq(at("- [[OPS-3]] 와 [[jira/KB-12]] 비교", "jira/KB"), "KB-12")
    H.eq(at("- [[OPS-3]] 와 [[jira/KB-12|별칭]] 비교", "별칭"), "KB-12")
    H.eq(at("- [[OPS-3]] 와 [[KB-12#h]] 비교", "#h"), "KB-12")
    H.eq(at("- [[OPS-3]] 와 [[jira/KB-12.md]] 비교", ".md"), "KB-12")
    -- 키가 아닌 링크 위면 nil
    H.eq(at("- [[KB-12-설계]] 회의 OPS-3", "설계"), nil)
    H.eq(at("- OPS-3 와 [[projects/KB]]", "projects"), nil)
    -- 링크 밖이면 줄의 첫 키. KB-12-설계는 키가 아니다
    H.eq(at("- [[KB-12-설계]] 회의 OPS-3", "회의"), "OPS-3")
    H.eq(at("- KB-12-설계 회의", "회의"), nil)
    H.eq(at("- KB-12-설계 회의", "KB-1"), nil)
    -- 키가 아닌 링크 위에서는 묻는다 (앞뒤 공백 제거, 대문자)
    scratch("- OPS-3 와 [[KB-12-설계]]", "설계")
    local stub = H.ui_stub({ { "input", " kb-7 " } })
    H.eq(j.jira_key(), "KB-7")
    H.eq(prompt_list(stub), { "Jira 키: " })
    -- jira/OPS-7.md를 보고 있으면 파일 이름
    local vault = H.with_vault({ python = H.python() })
    local page = vault .. "/jira/OPS-7.md"
    H.write_bytes(page, "---\nkey: OPS-7\n---\n# 제목\n")
    vim.cmd("edit " .. vim.fn.fnameescape(page))
    vim.api.nvim_win_set_cursor(0, { 4, 0 })
    H.eq(j.key_at_point(), "OPS-7")
    -- 줄의 키 찾기 (open.lua 없이)
    H.eq(j.key_on_line("xKB-1 KB-12-설계 회의 OPS-3 와 KB-9"), "OPS-3")
    H.eq(j.key_on_line("한글KB-1한글"), "KB-1")
    H.eq(j.key_on_line("kb-1 K-1 KB_-"), nil)
    fresh()
  end)

  it("jira-key-at-point를 실제 open.lua(thing_at_point·link_key)로 한 번 더", function()
    fresh()
    local ok_open, open = pcall(require, "work-kb.open")
    H.ok(ok_open, "work-kb.open을 불러오지 못함: " .. tostring(open))
    local j = jira()
    local function at(line, needle, back)
      scratch(line, needle, back)
      return j.key_at_point()
    end
    H.eq(at("회의 [[KB-12]] 와 OPS-3 배포", "OPS-"), "OPS-3")
    H.eq(at("회의 [[KB-12]] 와 OPS-3 배포", "배포"), "KB-12")
    H.eq(at("키 없는 줄 xKB-1", "줄"), nil)
    H.eq(at("- [[OPS-3]] 와 [[jira/KB-12]] 비교", "jira/KB", true), "KB-12")
    H.eq(at("- [[OPS-3]] 와 [[jira/KB-12|별칭]] 비교", "별칭", true), "KB-12")
    H.eq(at("- [[OPS-3]] 와 [[KB-12#h]] 비교", "#h", true), "KB-12")
    H.eq(at("- [[OPS-3]] 와 [[jira/KB-12.md]] 비교", ".md", true), "KB-12")
    H.eq(at("- [[KB-12-설계]] 회의 OPS-3", "설계", true), nil)
    H.eq(at("- OPS-3 와 [[projects/KB]]", "projects", true), nil)
    H.eq(at("- [[KB-12-설계]] 회의 OPS-3", "회의", true), "OPS-3")
    H.eq(at("- KB-12-설계 회의", "회의", true), nil)
    H.eq(at("- KB-12-설계 회의", "KB-1", true), nil)
    -- @이름 위에서는 줄의 키로 넘어간다 (키·링크만 막는다)
    H.eq(at("@김철수 KB-3", "김철", true), "KB-3")
  end)

  it("thing_at_point 돌려준 값의 여러 꼴을 받는다 (open.lua와의 계약)", function()
    local j = jira()
    H.eq({ j._thing({ kind = "key", value = "KB-1" }) }, { "key", "KB-1" })
    H.eq({ j._thing({ type = "link", target = "설계" }) }, { "link", "설계" })
    H.eq({ j._thing({ "person", "김철수" }) }, { "person", "김철수" })
    H.eq({ j._thing("key", "KB-2") }, { "key", "KB-2" })
    H.eq({ j._thing(nil) }, {})
  end)

  it("jira-view: 키가 있으면 묻지 않고 open_key, 없으면 묻고 대문자로", function()
    fresh()
    H.with_vault({ python = H.python() })
    local open = stub_open()
    local j = jira()
    scratch("회의 KB-12", "KB-12")
    H.ui_stub()
    H.eq(j.view(), "opened")
    scratch("키 없음", "없음")
    H.ui_stub({ { "input", " kb-7 " } })
    j.view()
    local stub = H.ui_stub({ { "input", "아님" } })
    local err = H.fails_with(function()
      j.view()
    end, 'Jira 키가 아닙니다: "아님" (예: KB-12)')
    H.ok(require("work-kb.ui").is_user_error(err))
    H.eq(open.opened, { "KB-12", "KB-7" })
    H.eq(prompt_list(stub), { "Jira 키: " })
    -- 취소(nil)면 멈춘다
    H.ui_stub({ { "input", nil } })
    H.fails_with(function()
      j.view()
    end, "work-kb: 취소했습니다")
    H.eq(#open.opened, 2)
  end)

  it("jira-comment-from-daily-line: daily에서 본문 그대로면 --from-line daily/…:11, 먼저 저장", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn({ code = 0 })
    local buf, file = visit_daily(vault, "배포")
    -- 위 줄을 고쳐 두면(저장 안 함) 디스크와 줄 번호를 맞추려고 저장한다
    vim.api.nvim_buf_set_lines(buf, 9, 10, false, { "- 08:00 첫 기록 추가" })
    H.ok(vim.bo[buf].modified)
    local stub = H.ui_stub({ { "input", ":initial" } })
    local handle = jira().comment()
    rec.settle()
    H.ok(not vim.bo[buf].modified, "저장했어야 함")
    H.contains(H.read_bytes(file), "첫 기록 추가")
    H.eq(H.read_bytes(file), (JIRA_DAILY:gsub("첫 기록\n", "첫 기록 추가\n")), "다른 바이트는 그대로")
    H.eq(prompt_list(stub), { "KB-12 댓글: " })
    H.eq(rec.calls, { { args = act(vault, "comment", "KB-12", "--from-line", "daily/2026-11-05.md:11"), buffer = "*work-kb-jira*" } })
    H.eq(proc().buffer_name(handle.bufnr), "*work-kb-jira*")
    H.eq(stub.messages, { "work-kb: KB-12 댓글 완료" })
    fresh()
  end)

  it("daily 줄 참조: 버퍼와 파일이 모두 바뀌었으면 사용자 오류, 아무것도 보내지 않음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn({ code = 0 })
    local buf, file = visit_daily(vault, "배포")
    vim.api.nvim_buf_set_lines(buf, 9, 10, false, { "- 08:00 첫 기록 버퍼" })
    rewrite_on_disk(file, JIRA_DAILY:gsub("첫 기록\n", "첫 기록 디스크\n"))
    H.ui_stub({ { "input", ":initial" } })
    H.fails_with(function()
      jira().comment()
    end, "2026-11-05.md: 버퍼와 파일이 모두 바뀌었습니다. 먼저 정리하세요")
    H.eq(#rec.calls, 0)
    H.contains(H.read_bytes(file), "첫 기록 디스크")
    H.ok(vim.bo[buf].modified, "버퍼 수정은 그대로")
    fresh()
  end)

  it("jira-comment-body: 고쳤거나 daily가 아니면 --body, 빈 본문은 보내지 않음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn({ code = 0 })
    visit_daily(vault, "배포")
    H.ui_stub({ { "input", "고친 댓글 😀" } })
    jira().comment()
    H.ui_stub({ { "input", "  " } })
    H.fails_with(function()
      jira().comment()
    end, "빈 댓글은 보내지 않습니다")
    scratch("OPS-3 배포 완료", "완료")
    H.ui_stub({ { "input", ":initial" } })
    jira().comment()
    rec.settle()
    H.eq(rec.args(), {
      act(vault, "comment", "KB-12", "--body", "고친 댓글 😀"),
      act(vault, "comment", "OPS-3", "--body", "OPS-3 배포 완료"),
    })
    fresh()
  end)

  it("jira-transition-choices: JSON → 표시·값, 앞의 로그 줄, 깨진 출력, 같은 이름", function()
    local parse = jira().parse_transitions
    local function pairs_of(text)
      local out = {}
      for i, c in ipairs(parse(text)) do
        out[i] = { c.label, c.value }
      end
      return out
    end
    H.eq(pairs_of('[{"id":"21","name":"In Review"},{"id":"31","name":"완료"}]'), { { "In Review (21)", "21" }, { "완료 (31)", "31" } })
    H.eq(
      pairs_of('jira_act: 경고 [무시]\n{"key":"KB-1","transitions":[{"id":"41","name":"Done","to":{"name":"Done"}},{"id":42,"name":"Done","to":"Closed"}]}\n'),
      { { "Done → Done (41)", "41" }, { "Done → Closed (42)", "42" } }
    )
    H.eq(pairs_of('["Start", "Stop"]'), { { "Start", "Start" }, { "Stop", "Stop" } })
    H.eq(pairs_of('[{"name":"Go","to":""}]'), { { "Go", "Go" } })
    H.eq(pairs_of("[]"), {})
    H.eq(pairs_of("오류: 연결 실패\n"), {})
    H.eq(pairs_of('[{"id": 1, "name": null}, {"name": ""}]'), {})
    -- 줄 머리의 [로 시작하는 로그 줄은 건너뛰고 다음 줄의 JSON을 읽는다
    H.eq(pairs_of('[INFO] 연결\n  [{"id": 7, "name": "닫기"}]\n'), { { "닫기 (7)", "7" } })
    -- 여러 줄 JSON, 같은 표시는 처음 것만
    H.eq(pairs_of('{\n "transitions": [\n  {"id": "1", "name": "A"},\n  {"id": "1", "name": "A"}\n ]\n}\n'), { { "A (1)", "1" } })
  end)

  it("jira-transition: 후보를 고르고 확인하면 act transition KEY <id> --yes", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn(function(call)
      if call.argv[2] == "transitions" then
        return TRANSITIONS_OUTPUT, 0
      end
      return "jira_act: 전환함\n", 0
    end)
    scratch("회의 KB-9", "KB-9")
    local stub = H.ui_stub({ { "select", "Done → Closed (42)" }, { "confirm", true } })
    jira().transition()
    rec.settle()
    H.eq(stub.last_items, { "Done → Done (41)", "Done → Closed (42)", "In Progress → In Progress (43)" })
    H.eq(prompt_list(stub), { "KB-9 전환: ", "KB-9을(를) 'Done → Closed (42)'(으)로 전환할까요? " })
    H.eq(rec.calls, {
      { args = act(vault, "transitions", "KB-9", "--json"), buffer = "*work-kb-jira*" },
      { args = act(vault, "transition", "KB-9", "42", "--yes"), buffer = "*work-kb-jira*" },
    })
    H.eq(stub.messages, { "work-kb: KB-9 전환(Done → Closed (42)) 완료 — jira_act: 전환함" })
    fresh()
  end)

  it("jira-transition-declined-or-failed: n이면 보내지 않음, 조회 실패·후보 없음이면 묻지 않음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn({ code = 0, output = TRANSITIONS_OUTPUT })
    scratch("KB-12", "KB-12")
    local stub = H.ui_stub({ { "select", "In Progress → In Progress (43)" }, { "confirm", false } })
    H.eq(jira().transition(), nil)
    H.eq(rec.args(), { act(vault, "transitions", "KB-12", "--json") })
    H.eq(stub.messages, { "work-kb: 전환을 취소했습니다" })
    -- 목록 조회 실패: 버퍼를 보이고 사용자 오류, 묻지 않음
    rec = record_runs()
    H.fake_spawn({ code = 3, output = "jira_act: 연결 실패\n" })
    stub = H.ui_stub()
    local err = H.fails_with(function()
      jira().transition()
    end, "work-kb: KB-12 전환 목록을 받지 못했습니다 (종료 3: 네트워크)")
    H.ok(require("work-kb.ui").is_user_error(err))
    H.eq(#rec.calls, 1)
    H.eq(#stub.prompts, 0)
    H.eq(stub.shown, { proc().get_buffer("*work-kb-jira*") })
    -- 후보 없음
    rec = record_runs()
    H.fake_spawn({ code = 0, output = "[]\n" })
    H.ui_stub()
    H.fails_with(function()
      jira().transition()
    end, "work-kb: KB-12에서 할 수 있는 전환이 없습니다")
    H.eq(#rec.calls, 1)
    -- 고르지 않음 (strict 선택 취소)
    rec = record_runs()
    H.fake_spawn({ code = 0, output = TRANSITIONS_OUTPUT })
    H.ui_stub({ { "select", nil } })
    H.fails_with(function()
      jira().transition()
    end, "전환을 고르지 않았습니다")
    H.eq(#rec.calls, 1)
    fresh()
  end)

  it("jira-assign-and-worklog: 인수, 잘못된 시간·빈 담당자는 보내지 않음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn({ code = 0 })
    scratch("KB-12 작업", "작업")
    local stub = H.ui_stub({
      { "input", "kim.cs" },
      { "input", "1h 30M" },
      { "input", "배포 준비 😀" },
      { "input", "45m" },
      { "input", "" },
      { "input", "2H" },
      { "input", "" },
    })
    jira().assign()
    jira().worklog()
    jira().worklog()
    jira().worklog()
    rec.settle()
    H.eq(#stub.answers, 0)
    H.eq(stub.prompts[1].prompt, "KB-12 담당자 (Jira 사용자명): ")
    H.eq(stub.prompts[2].prompt, "KB-12 작업 시간 (예: 1h30m): ")
    H.eq(stub.prompts[3].prompt, "KB-12 워크로그 설명 (선택): ")
    local bad_messages = {
      ["한 시간"] = '시간 형식이 아닙니다: "한시간" (예: 1h30m, 2h, 45m)',
      ["1d"] = '시간 형식이 아닙니다: "1d" (예: 1h30m, 2h, 45m)',
      ["2w"] = '시간 형식이 아닙니다: "2w" (예: 1h30m, 2h, 45m)',
      ["30m1h"] = '시간 형식이 아닙니다: "30m1h" (예: 1h30m, 2h, 45m)',
      ["1h1h"] = '시간 형식이 아닙니다: "1h1h" (예: 1h30m, 2h, 45m)',
      [""] = '시간 형식이 아닙니다: "" (예: 1h30m, 2h, 45m)',
      ["0h0m"] = '작업 시간은 1분 이상이어야 합니다: "0h0m"',
    }
    for _, bad in ipairs({ "한 시간", "1d", "2w", "30m1h", "1h1h", "", "0h0m" }) do
      H.ui_stub({ { "input", bad } })
      H.fails_with(function()
        jira().worklog()
      end, bad_messages[bad])
    end
    H.ui_stub({ { "input", " " } })
    H.fails_with(function()
      jira().assign()
    end, "담당자가 비었습니다")
    rec.settle()
    H.eq(rec.args(), {
      act(vault, "assign", "KB-12", "kim.cs"),
      act(vault, "worklog", "KB-12", "1h30m", "--comment", "배포 준비 😀"),
      act(vault, "worklog", "KB-12", "45m"),
      act(vault, "worklog", "KB-12", "2h"),
    })
    H.eq(stub.messages, {
      "work-kb: KB-12 담당 kim.cs 완료",
      "work-kb: KB-12 워크로그 1h30m 완료",
      "work-kb: KB-12 워크로그 45m 완료",
      "work-kb: KB-12 워크로그 2h 완료",
    })
    fresh()
  end)

  it("jira-project-keys: status JSON 어디에 있든 project_keys", function()
    local pk = jira().project_keys
    H.eq(pk({ project_keys = { "KB", "OPS" } }), { "KB", "OPS" })
    H.eq(pk({ vault = "x", jira = { base_url = "u", project_keys = { "KB" } } }), { "KB" })
    H.eq(pk({ vault = "x", outbox = { pending = 0 } }), nil)
    H.eq(pk(nil), nil)
    H.eq(pk(STATUS_JSON), { "KB", "OPS" })
    H.eq(pk('log\n{"a": {"b": {"c": {"d": {"project_keys": ["DEEP"]}}}}}'), nil, "깊이 4 이상은 보지 않는다")
    H.eq(pk('{"a": {"b": {"c": {"project_keys": ["OK"]}}}}'), { "OK" }, "깊이 3까지는 본다")
    H.eq(pk('{"project_keys": []}'), nil)
    H.eq(pk('{"project_keys": ["KB", 3]}'), nil)
  end)

  it("jira-create: 프로젝트(status --json 후보)·요약·유형·설명 → 확인 → act create … --yes", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn(function(call)
      if call.argv[1] == "status" then
        return STATUS_JSON, 0
      end
      return "", 0
    end)
    local stub = H.ui_stub({
      { "select", "ops" },
      { "input", "새 대시보드" },
      { "select", "Bug" },
      { "input", "" },
      { "confirm", true },
    })
    jira().create()
    H.eq(prompt_list(stub), { "프로젝트 (default KB): ", "OPS 새 일감 요약: ", "유형 (default Task): ", "설명 (선택): ", "OPS에 Bug 일감 '새 대시보드'을(를) 만들까요? " })
    H.ui_stub({ { "select", "KB" }, { "input", "요약" }, { "select", "Task" }, { "input", "설명 한 줄" }, { "confirm", true } })
    jira().create()
    rec.settle()
    stub = H.ui_stub({ { "select", "KB" }, { "input", "취소할 일감" }, { "select", "Task" }, { "input", "" }, { "confirm", false } })
    H.eq(jira().create(), nil)
    H.eq(stub.messages, { "work-kb: 새 일감을 취소했습니다" })
    rec.settle()
    local status = { args = { "status", "--root", vault, "--json" }, buffer = "*work-kb-status-json*" }
    H.eq(rec.calls, {
      status,
      { args = act(vault, "create", "OPS", "--summary", "새 대시보드", "--type", "Bug", "--yes"), buffer = "*work-kb-jira*" },
      status,
      { args = act(vault, "create", "KB", "--summary", "요약", "--type", "Task", "--description", "설명 한 줄", "--yes"), buffer = "*work-kb-jira*" },
      status,
    })
    -- 빈 요약·빈 유형
    H.ui_stub({ { "select", "KB" }, { "input", "  " } })
    H.fails_with(function()
      jira().create()
    end, "비어 있어 취소했습니다")
    H.ui_stub({ { "select", "KB" }, { "input", "요약" }, { "select", " " }, { "input", "" } })
    H.fails_with(function()
      jira().create()
    end, "유형이 비었습니다")
    fresh()
  end)

  it("jira-create-without-project-list: project_keys가 없으면 직접 입력, 키 꼴이 아니면 거부", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local rec = record_runs()
    H.fake_spawn(function(call)
      if call.argv[1] == "status" then
        return '{"vault": "x"}\n', 0
      end
      return "", 0
    end)
    local stub = H.ui_stub({ { "select", "한글" } })
    H.fails_with(function()
      jira().create()
    end, '프로젝트 키가 아닙니다: "한글" (예: KB)')
    H.eq(prompt_list(stub), { "프로젝트: " })
    H.eq(stub.last_items, {})
    H.ui_stub({ { "select", "web" }, { "input", "요약" }, { "select", "Task" }, { "input", "" }, { "confirm", true } })
    jira().create()
    rec.settle()
    H.eq(rec.calls[#rec.calls].args, act(vault, "create", "WEB", "--summary", "요약", "--type", "Task", "--yes"))
    -- status가 실패해도 직접 입력
    H.fake_spawn(function(call)
      if call.argv[1] == "status" then
        return STATUS_JSON, 4
      end
      return "", 0
    end)
    stub = H.ui_stub({ { "select", "ops" }, { "input", "요약" }, { "select", "Story" }, { "input", "" }, { "confirm", false } })
    H.eq(jira().create(), nil)
    H.eq(prompt_list(stub)[1], "프로젝트: ")
    H.eq(stub.prompts[5].prompt, "OPS에 Story 일감 '요약'을(를) 만들까요? ")
    fresh()
  end)

  it("jira-promote: 현재 daily 줄 → 확인 → act promote --from daily/…:12 KB --yes, 성공하면 버퍼를 다시 읽음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local file = vault .. "/daily/2026-11-05.md"
    local rec = record_runs()
    H.fake_spawn(function(call)
      if call.argv[1] == "status" then
        return STATUS_JSON, 0
      end
      rewrite_on_disk(file, (JIRA_DAILY:gsub("경비 정산\n", "경비 정산 [[KB-100]]\n")))
      return "jira_act: KB-100 만듦\n", 0
    end)
    local buf = visit_daily(vault, "경비")
    local stub = H.ui_stub({ { "select", "KB" }, { "confirm", true } })
    local handle = jira().promote()
    rec.settle()
    H.ok(handle and handle.done)
    H.contains(table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n"), "경비 정산 [[KB-100]]")
    H.ok(not vim.bo[buf].modified)
    H.eq(rec.args(), {
      { "status", "--root", vault, "--json" },
      act(vault, "promote", "--from", "daily/2026-11-05.md:12", "KB", "--yes"),
    })
    H.eq(prompt_list(stub), { "프로젝트 (default KB): ", "이 줄을 KB 일감으로 만들까요? - [ ] 09:30 경비 정산 " })
    H.eq(stub.messages, { "work-kb: KB 승격 완료 — jira_act: KB-100 만듦" })
    fresh()
  end)

  it("jira-promote-declined-or-outside-daily: n이면 보내지 않음, 빈 줄·daily 밖이면 아무것도 실행하지 않음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    stub_open()
    local file = vault .. "/daily/2026-11-05.md"
    local rec = record_runs()
    H.fake_spawn(function(call)
      if call.argv[1] == "status" then
        return STATUS_JSON, 0
      end
      return "", 0
    end)
    visit_daily(vault, "경비")
    local stub = H.ui_stub({ { "select", "KB" }, { "confirm", false } })
    H.eq(jira().promote(), nil)
    H.eq(stub.messages, { "work-kb: 승격을 취소했습니다" })
    -- 빈 줄 (8번 줄)
    vim.api.nvim_win_set_cursor(0, { 8, 0 })
    H.ui_stub()
    H.fails_with(function()
      jira().promote()
    end, "빈 줄은 승격할 수 없습니다")
    -- daily 밖
    scratch("- [ ] 09:30 경비 정산", "경비")
    H.ui_stub()
    H.fails_with(function()
      jira().promote()
    end, "승격은 daily 파일의 줄에서만 됩니다")
    H.eq(rec.args(), { { "status", "--root", vault, "--json" } })
    H.eq(H.read_bytes(file), JIRA_DAILY)
    fresh()
  end)

  it("jira-query-sync-flush-check-done: 인수·출력 버퍼, q·d는 읽기 전용 결과 버퍼(<CR> → 열기)", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    local open = stub_open()
    local jql = 'project = KB AND summary ~ "한글"'
    local rec = record_runs()
    H.fake_spawn(function(call)
      local a = call.argv
      if a[1] == "sync" and vim.tbl_contains(a, "--jql") then
        return "KB-12 캐시 동기화\n", 0
      elseif a[1] == "today" then
        return "KB-12 제안\n", 0
      end
      return "", 0
    end)
    local stub = H.ui_stub({ { "input", jql } })
    local j = jira()
    j.query()
    j.sync()
    j.flush()
    j.check_done()
    rec.settle()
    H.fails_with(function()
      j.query("  ")
    end, "JQL이 비었습니다")
    H.eq(prompt_list(stub), { "JQL: " })
    H.eq(rec.calls, {
      { args = { "sync", "--root", vault, "--jql", jql }, buffer = "*work-kb-jql*" },
      { args = { "sync", "--root", vault }, buffer = "*work-kb-sync*" },
      { args = act(vault, "flush"), buffer = "*work-kb-jira*" },
      { args = { "today", "--root", vault, "--check-done" }, buffer = "*work-kb-check-done*" },
    })
    H.eq(stub.messages, {
      "work-kb: 동기화 중…",
      "work-kb: 라이브 JQL 완료",
      "work-kb: 동기화 완료",
      "work-kb: outbox 다시 보내기 완료",
      "work-kb: 완료 전환 제안 완료",
    })
    H.eq(H.buffer_text("*work-kb-jql*"), "KB-12 캐시 동기화\n")
    H.eq(H.buffer_text("*work-kb-check-done*"), "KB-12 제안\n")
    for _, name in ipairs({ "*work-kb-jql*", "*work-kb-check-done*" }) do
      local buf = proc().get_buffer(name)
      H.ok(vim.tbl_contains(stub.shown, buf), name .. " 보임")
      H.eq(vim.bo[buf].modifiable, false, name)
      local map = vim.api.nvim_buf_call(buf, function()
        return vim.fn.maparg("<CR>", "n", false, true)
      end)
      H.eq(map.buffer, 1, name .. " 버퍼 지역 <CR>")
      map.callback()
    end
    H.eq(open.at_point, 2, "<CR>은 open_at_point")
    -- 결과 버퍼가 읽기 전용이어도 다시 실행하면 비우고 채운다
    H.ui_stub()
    j.query("project = OPS")
    rec.settle()
    H.eq(H.buffer_text("*work-kb-jql*"), "KB-12 캐시 동기화\n")
    -- 실패 문구
    H.fake_spawn({ code = 3, output = "오류\n" })
    stub = H.ui_stub()
    j.query("x")
    j.sync()
    j.check_done()
    rec.settle()
    H.eq(stub.messages, {
      "work-kb: 동기화 중…",
      "work-kb: 라이브 JQL 실패 (종료 3: 네트워크)",
      "work-kb: 동기화 실패 (종료 3: 네트워크). *work-kb-sync* 버퍼를 보세요",
      "work-kb: 완료 전환 제안 실패 (종료 3: 네트워크)",
    })
    H.ok(vim.tbl_contains(stub.shown, proc().get_buffer("*work-kb-sync*")))
  end)

  it("jira-act-exit-messages: 0 완료, 3 outbox에 남김, 2 인증, 그 밖은 이유와 버퍼", function()
    H.with_vault({ python = H.python() })
    local cases = {
      { 0, "work-kb: outbox 다시 보내기 완료 — jira_act: 끝", false },
      { 3, "work-kb: outbox 다시 보내기 — 네트워크 오류로 outbox에 남겼습니다. 나중에 f(flush)로 보냅니다", true },
      { 2, "work-kb: outbox 다시 보내기 — 인증 실패 (outbox에 남김). 토큰을 확인하세요: kb.py init", true },
      { 4, "work-kb: outbox 다시 보내기 실패 (종료 4: 설정). *work-kb-jira* 버퍼를 보세요", true },
    }
    for _, case in ipairs(cases) do
      H.fake_spawn({ code = case[1], output = "jira_act: 끝\n" })
      local stub = H.ui_stub()
      local handle = jira().flush()
      proc().wait(handle, 5000)
      H.eq(stub.messages, { case[2] })
      H.eq(#stub.shown, case[3] and 1 or 0, "종료 " .. case[1])
    end
  end)

  it("jira-run-output-real-process: run_output이 실제 프로세스를 기다려 (코드, 출력) → 전환 후보", function()
    H.with_vault({ python = H.python() })
    local code = table.concat({
      "import sys, json",
      "a = json.loads(sys.stdin.buffer.read().decode('utf-8'))",
      "sys.stdout.buffer.write(('log\\n' + json.dumps([{'name': a[-1]}], ensure_ascii=False) + '\\n').encode('utf-8'))",
      "sys.exit(0)",
    }, "\n")
    H.rewrite_spawn(function(spec)
      return { spec.cmd[1], "-c", code }
    end)
    local p = proc()
    local exit, text = p.run_output(p.sub_args("act", "transitions", "KB-1", "완료"), { buffer = " *work-kb-test-jira-out*", timeout_ms = 60000 })
    H.eq(exit, 0)
    local choices = jira().parse_transitions(text)
    H.eq(#choices, 1)
    H.eq({ choices[1].label, choices[1].value }, { "완료", "완료" })
  end)
end)
