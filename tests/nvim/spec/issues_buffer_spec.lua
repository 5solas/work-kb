-- J: Jira 일감 목록의 버퍼·키·미리 보기 (issues.lua). fetch·jira·open·proc.run을 바꿔 끼운다.
local H = require("helpers")

local I
local dw = vim.fn.strdisplaywidth

local function issue(key, over)
  return vim.tbl_extend("force", {
    key = key,
    project = key:match("^[^-]+"),
    summary = key .. " 제목",
    status = "할 일",
    status_category = "new",
    issuetype = "Task",
    priority = "High",
    assignee = "김철수",
    updated = "2026-11-20T09:00:00+09:00",
    due = "2026-11-28",
  }, over or {})
end

---목록을 깨끗이 하고 fetch를 LIST로 바꾼다. 돌려주는 표: fetches(부른 뷰), list·fail(바꿀 수 있음),
---stub(ui), calls(jira 동작), opened(open_key), runs(proc.run).
local function setup(list, answers)
  H.with_vault({
    python = H.python(),
    server = { enable = false },
    now = function()
      return os.time({ year = 2026, month = 11, day = 25, hour = 12 })
    end,
  })
  I = require("work-kb.issues")
  vim.cmd("silent! only")
  vim.cmd("enew!")
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_get_name(b):find("work-kb-issues", 1, true) then
      pcall(vim.api.nvim_buf_delete, b, { force = true })
    end
  end
  H.patch(I, "state", { status = "open" })
  I._last_key = nil
  local t = {
    fetches = {},
    calls = {},
    opened = {},
    runs = {},
    list = list or { issue("KB-12"), issue("KB-31", { assignee = "홍길동" }), issue("OPS-40") },
  }
  H.patch(I, "fetch", function(view)
    table.insert(t.fetches, view or false)
    if t.fail then
      return nil, t.fail
    end
    return { synced_at = "2026-11-25T09:10:00+09:00", views = { "blocked", "today" }, issues = t.list }
  end)
  t.stub = H.ui_stub(answers)
  local fake = setmetatable({}, {
    __index = function(_, name)
      return function(opts)
        table.insert(t.calls, { name, opts or {} })
      end
    end,
  })
  H.patch(package.loaded, "work-kb.jira", fake)
  H.patch(require("work-kb.open"), "open_key", function(key)
    table.insert(t.opened, key)
  end)
  H.patch(require("work-kb.proc"), "run", function(args, opts)
    table.insert(t.runs, { args = args, opts = opts })
  end)
  return t
end

local function buf_lines()
  return vim.api.nvim_buf_get_lines(0, 0, -1, false)
end

local function goto_key(key, text)
  for lnum, line in ipairs(buf_lines()) do
    if line:match("^ " .. vim.pesc(key) .. " ") then
      vim.api.nvim_win_set_cursor(0, { lnum, (text and line:find(text, 1, true) or 1) - 1 })
      vim.api.nvim_exec_autocmds("CursorMoved", { buffer = 0 })
      return
    end
  end
  error("줄 없음: " .. key)
end

local function press(lhs)
  local m = vim.fn.maparg(lhs, "n", false, true)
  H.eq(m.buffer, 1, lhs .. " 버퍼 지역 매핑이 아님")
  m.callback()
end

---예약된 콜백(vim.schedule)을 돌린다.
local function flush()
  vim.wait(30, function()
    return false
  end)
end

describe("issues 버퍼", function()
  it("issues open: 읽기 전용으로 그리고 첫 일감에 커서를 둔다", function()
    setup()
    I.open()
    H.eq(vim.api.nvim_get_current_buf(), require("work-kb.proc").get_buffer(I.BUFFER))
    H.eq(
      { vim.bo.modifiable, vim.bo.buftype, vim.bo.filetype, vim.bo.swapfile },
      { false, "nofile", "work-kb-issues", false }
    )
    H.eq({ vim.wo.wrap, vim.wo.cursorline }, { false, true })
    H.eq(I.key_at_cursor(), "KB-12")
    local ns = vim.api.nvim_create_namespace("work-kb-issues")
    H.ok(#vim.api.nvim_buf_get_extmarks(0, ns, 0, -1, {}) > 0)
  end)

  it("issues open: 다시 부르면 같은 버퍼와 창", function()
    setup()
    I.open()
    local win, buf = vim.api.nvim_get_current_win(), vim.api.nvim_get_current_buf()
    vim.cmd("new")
    I.open()
    H.eq({ vim.api.nvim_get_current_win(), vim.api.nvim_get_current_buf() }, { win, buf })
    vim.cmd("only")
  end)

  it("issues open: 읽기에 실패하면 알리고 이전 줄을 둔다", function()
    local t = setup()
    I.open()
    local before = buf_lines()
    t.fail = "잠김"
    I.open()
    H.eq(buf_lines(), before)
    H.eq(t.stub.messages, { "work-kb: 일감 목록을 읽지 못했습니다 (잠김)" })
  end)

  it("issues open: 처음부터 실패해도 오류 없이 빈 화면", function()
    local t = setup()
    t.fail = "Python 없음"
    I.open()
    H.contains(t.stub.messages[1], "Python 없음")
    H.contains(buf_lines()[1], "Jira 일감")
  end)

  it("issues <CR>·o: 커서 줄의 일감을 연다", function()
    local t = setup()
    I.open()
    goto_key("KB-31")
    press("<CR>")
    press("o")
    H.eq(t.opened, { "KB-31", "KB-31" })
  end)

  it("issues 동작: 머리줄·구분선·도움말 줄에서는 묻지 않고 오류", function()
    local t = setup()
    I.open()
    for _, lnum in ipairs({ 1, 2, vim.api.nvim_buf_line_count(0) }) do
      vim.api.nvim_win_set_cursor(0, { lnum, 0 })
      H.fails_with(I.key_at_cursor, "일감 줄이 아닙니다")
      for _, lhs in ipairs({ "c", "t", "a", "w", "<CR>", "y" }) do
        press(lhs)
      end
    end
    H.eq(t.calls, {})
    H.eq(t.opened, {})
    H.eq(t.stub.prompts, {})
  end)

  it("issues 동작: 제목의 다른 키 위에서도 그 줄의 키를 넘긴다", function()
    local t = setup({ issue("KB-12", { summary = "KB-99 후속" }) })
    I.open()
    goto_key("KB-12", "KB-99")
    for _, lhs in ipairs({ "c", "t", "a", "w", "+" }) do
      press(lhs)
    end
    local got = vim.tbl_map(function(c)
      return { c[1], c[2].key }
    end, t.calls)
    H.eq(got, {
      { "comment", "KB-12" },
      { "transition", "KB-12" },
      { "assign", "KB-12" },
      { "worklog", "KB-12" },
      { "create" },
    })
    H.eq(t.calls[1][2].initial, "")
  end)

  it("issues t: 성공하면 그 일감만 받아 다시 그리고, 실패면 아무것도 하지 않는다", function()
    local t = setup()
    I.open()
    goto_key("KB-31")
    press("t")
    local done = t.calls[1][2].on_done
    done(3)
    flush()
    H.eq(#t.runs, 0)
    done(0)
    flush()
    H.eq(t.runs[1].args[1], "sync")
    H.eq(vim.list_slice(t.runs[1].args, 4), { "--key", "KB-31" })
    H.eq(#t.fetches, 1)
    t.list = { issue("KB-31", { status = "새 상태" }) }
    t.runs[1].opts.on_exit(0)
    flush()
    H.eq(#t.fetches, 2)
    H.contains(buf_lines()[3], "새 상태")
    t.runs[1].opts.on_exit(1)
    flush()
    H.eq(#t.fetches, 2)
    H.contains(t.stub.messages[#t.stub.messages], "KB-31")
  end)

  it("issues 콜백: 버퍼를 지운 뒤에 끝나도 오류가 없다", function()
    local t = setup()
    I.open()
    press("t")
    press("S")
    local done, synced = t.calls[1][2].on_done, t.calls[2][2].on_done
    done(0)
    flush()
    local buf = vim.api.nvim_get_current_buf()
    vim.cmd("enew!")
    vim.api.nvim_buf_delete(buf, { force = true })
    vim.v.errmsg = ""
    t.runs[1].opts.on_exit(0)
    done(0)
    synced(0)
    flush()
    H.eq(vim.v.errmsg, "")
    H.eq(#t.fetches, 1)
    H.eq(t.stub.messages, {})
    I.refresh()
    H.eq(#t.fetches, 1)
  end)

  it("issues S·r: 다시 읽어 그린다", function()
    local t = setup()
    I.open()
    press("r")
    H.eq(#t.fetches, 2)
    press("S")
    H.eq(t.calls[1][1], "sync")
    t.calls[1][2].on_done(1)
    flush()
    H.eq(#t.fetches, 2)
    t.calls[1][2].on_done(0)
    flush()
    H.eq(#t.fetches, 3)
  end)

  it("issues s: 미완료 → 전체 → 완료 → 미완료", function()
    local t = setup({ issue("KB-1"), issue("KB-2", { status_category = "done" }) })
    I.open()
    for _, label in ipairs({ "[전체]", "[완료]", "[미완료]" }) do
      press("s")
      H.contains(buf_lines()[1], label)
    end
    H.eq(#t.fetches, 1)
  end)

  it("issues s: 뷰에서는 바꾸지 않고 알린다", function()
    local t = setup(nil, { { "pick", "today" } })
    I.open()
    press("V")
    press("s")
    H.eq(I.state.status, "open")
    H.contains(t.stub.messages[1], "뷰")
  end)

  it("issues A·P: 있는 값에서 고른다", function()
    local t = setup(nil, { { "pick", "홍길동" }, { "pick", "KB" }, { "pick", "전체" } })
    I.open()
    press("A")
    H.eq(t.stub.last_items, { "전체", "김철수", "홍길동" })
    H.eq(I.key_at_cursor(), "KB-31")
    press("P")
    H.eq(t.stub.last_items, { "전체", "KB", "OPS" })
    H.contains(buf_lines()[1], "프로젝트: KB")
    press("A")
    H.eq(I.state.assignee, nil)
    H.eq(#t.fetches, 1)
  end)

  it("issues P·A·V: 취소하면 그대로", function()
    local t = setup(nil, { { "pick" }, { "pick" }, { "pick" } })
    I.open()
    local before = buf_lines()
    press("P")
    press("A")
    press("V")
    H.eq(buf_lines(), before)
    H.eq(#t.fetches, 1)
  end)

  it("issues V·x: 뷰를 읽고, x는 거르기를 모두 지운다", function()
    local t = setup(nil, { { "pick", "today" }, { "pick", "—" }, { "pick", "blocked" }, { "pick", "홍길동" } })
    I.open()
    press("V")
    H.eq(t.stub.last_items, { "—", "blocked", "today" })
    H.contains(buf_lines()[1], "뷰: today")
    press("V")
    H.eq(t.fetches, { false, "today", false })
    press("V")
    press("A")
    press("x")
    H.eq(I.state.status, "open")
    H.eq(I.state.project, nil)
    H.eq(I.state.assignee, nil)
    H.eq(I.state.view, nil)
    H.eq(t.fetches[#t.fetches], false)
  end)

  it("issues V: 뷰를 읽지 못하면 전의 뷰로 남는다", function()
    local t = setup(nil, { { "pick", "today" } })
    I.open()
    t.fail = "없는 뷰"
    press("V")
    H.eq(I.state.view, nil)
    H.contains(buf_lines()[1], "뷰: —")
  end)

  it("issues refresh: 같은 키에 커서를 두고, 사라지면 첫 일감", function()
    local t = setup()
    I.open()
    goto_key("OPS-40")
    t.list = { issue("KB-1"), issue("OPS-40"), issue("KB-31") }
    I.refresh()
    H.eq(I.key_at_cursor(), "OPS-40")
    t.list = { issue("KB-1") }
    I.refresh()
    H.eq(I.key_at_cursor(), "KB-1")
  end)

  it("issues open: 버퍼를 지웠다 다시 열어도 커서의 키와 거르기를 기억한다", function()
    setup(nil, { { "pick", "김철수" } })
    I.open()
    press("A")
    goto_key("OPS-40")
    local buf = vim.api.nvim_get_current_buf()
    vim.cmd("enew!")
    vim.api.nvim_buf_delete(buf, { force = true })
    I.open()
    H.eq(I.key_at_cursor(), "OPS-40")
    H.contains(buf_lines()[1], "담당: 김철수")
  end)

  it("issues refresh: 거르기 값이 사라지면 머리줄에 남고 안내를 보인다", function()
    local t = setup(nil, { { "pick", "홍길동" } })
    I.open()
    press("A")
    t.list = { issue("KB-12") }
    press("r")
    H.contains(buf_lines()[1], "담당: 홍길동")
    H.contains(table.concat(buf_lines(), "\n"), "x로 거르기를 지우세요")
  end)

  it("issues 화면 없음: pick은 취소, 그리기는 된다", function()
    local ui = require("work-kb.ui")
    local real_pick = ui.pick
    setup()
    H.patch(ui, "pick", real_pick)
    H.patch(ui, "_can_prompt", function()
      return false
    end)
    H.patch(vim, "notify", function() end)
    local got = "안 부름"
    ui.pick("x", { "a" }, function(c)
      got = c
    end)
    H.eq(got, nil)
    I.open()
    press("A")
    H.eq(I.state.assignee, nil)
    H.eq(I.key_at_cursor(), "KB-12")
  end)

  it("issues pick: vim.ui.select에 넘긴다", function()
    H.patch(require("work-kb.ui"), "_can_prompt", function()
      return true
    end)
    local seen
    H.patch(vim.ui, "select", function(items, opts, cb)
      seen = { items, opts.prompt }
      cb(items[2], 2)
    end)
    local got
    require("work-kb.ui").pick("담당", { "a", "b" }, function(c)
      got = c
    end)
    H.eq(seen, { { "a", "b" }, "담당" })
    H.eq(got, "b")
  end)

  it("issues p: 아래에 보이고 초점은 목록, 커서를 따라가고, q는 함께 닫는다", function()
    setup()
    local vault = require("work-kb.config").vault()
    H.mkdir_p(vault .. "/jira")
    H.write_bytes(vault .. "/jira/KB-12.md", "---\r\nkey: KB-12\r\n---\r\n# 첫째\r\n")
    H.write_bytes(vault .. "/jira/KB-31.md", "# 둘째\n")
    vim.cmd("split")
    I.open()
    local list, wins = vim.api.nvim_get_current_win(), #vim.api.nvim_tabpage_list_wins(0)
    goto_key("KB-12")
    press("p")
    H.eq(vim.api.nvim_get_current_win(), list)
    H.eq(#vim.api.nvim_tabpage_list_wins(0), wins + 1)
    local function text()
      local buf = vim.api.nvim_win_get_buf(I._preview_win)
      return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
    end
    H.eq(text(), "---\nkey: KB-12\n---\n# 첫째")
    goto_key("KB-31")
    H.contains(text(), "둘째")
    goto_key("OPS-40")
    H.eq(text(), "")
    press("p")
    H.eq(#vim.api.nvim_tabpage_list_wins(0), wins)
    press("p")
    press("q")
    H.eq(#vim.api.nvim_tabpage_list_wins(0), wins - 1)
    H.not_contains(vim.api.nvim_buf_get_name(0), "work-kb-issues")
    vim.cmd("only")
  end)

  it("issues p: 목록이 다른 버퍼로 바뀌면 미리 보기도 닫힌다", function()
    setup()
    I.open()
    press("p")
    H.eq(#vim.api.nvim_tabpage_list_wins(0), 2)
    vim.cmd("enew!")
    flush()
    H.eq(#vim.api.nvim_tabpage_list_wins(0), 1)
  end)

  it("issues p: 미리 보기 창에 다른 버퍼가 들어오면 그 버퍼를 건드리지 않는다", function()
    setup()
    I.open()
    local list = vim.api.nvim_get_current_win()
    press("p")
    local win = I._preview_win
    H.eq(vim.wo[win].winfixbuf, true)
    -- winfixbuf를 풀고(또는 강제로) 다른 파일을 연 경우
    vim.wo[win].winfixbuf = false
    local other = vim.api.nvim_create_buf(true, false)
    vim.api.nvim_buf_set_lines(other, 0, -1, false, { "사람이 쓴 글" })
    vim.api.nvim_win_set_buf(win, other)
    vim.bo[other].modified = false
    vim.api.nvim_set_current_win(list)
    goto_key("KB-31")
    H.eq(vim.api.nvim_buf_get_lines(other, 0, -1, false), { "사람이 쓴 글" })
    H.eq({ vim.bo[other].modified, vim.bo[other].modifiable }, { false, true })
    vim.cmd("enew!")
    flush()
    H.ok(vim.api.nvim_win_is_valid(win), "사람이 쓰는 창을 닫음")
    vim.cmd("only")
    vim.api.nvim_buf_delete(other, { force = true })
  end)

  it("issues refresh: 커서의 일감이 사라지면 같은 줄 근처에 머문다", function()
    local list = {}
    for i = 1, 30 do
      list[i] = issue("KB-" .. i)
    end
    local t = setup(list)
    I.open()
    goto_key("KB-20")
    t.list = vim.list_extend(vim.list_slice(list, 1, 19), vim.list_slice(list, 21))
    I.refresh()
    H.eq(I.key_at_cursor(), "KB-21")
    goto_key("KB-30")
    t.list = vim.list_slice(list, 1, 10)
    I.refresh()
    H.eq(I.key_at_cursor(), "KB-10")
  end)

  it("issues refresh: 보던 뷰가 사라지면 머리줄에 남고 빈 목록과 안내를 보인다", function()
    local t = setup()
    I.open()
    I.state.view = "gone"
    local real = I.fetch
    H.patch(I, "fetch", function(view)
      if view == "gone" then
        return nil, "없는 뷰: gone"
      end
      return real(view)
    end)
    press("r")
    H.contains(buf_lines()[1], "뷰: gone")
    H.contains(table.concat(buf_lines(), "\n"), "x로 거르기를 지우세요")
    H.contains(t.stub.messages[1], "없는 뷰: gone")
    press("x")
    H.eq(I.key_at_cursor(), "KB-12")
  end)

  it("issues y: 키를 복사한다", function()
    local t = setup()
    I.open()
    local regs = {}
    H.patch(vim.fn, "setreg", function(name, value) -- 진짜 클립보드를 건드리지 않는다
      regs[name] = value
    end)
    press("y")
    H.eq(regs, { ['"'] = "KB-12", ["+"] = "KB-12" })
    H.eq(t.stub.messages, { "KB-12 복사" })
  end)

  it("issues n·N·/: 검색에 남긴다", function()
    local t = setup()
    I.open()
    for _, lhs in ipairs({ "n", "N", "/" }) do
      H.ok(vim.fn.maparg(lhs, "n", false, true).buffer ~= 1, lhs)
    end
    press("g?")
    H.contains(t.stub.messages[1], "미리 보기")
  end)

  it("issues 창 크기: 폭이 바뀌면 다시 그린다", function()
    local t = setup({ issue("KB-1", { summary = string.rep("가", 100) }) })
    I.open()
    local before = dw(buf_lines()[3])
    vim.cmd("vsplit")
    vim.api.nvim_exec_autocmds("WinResized", {})
    H.ok(dw(buf_lines()[3]) < before)
    H.eq(#t.fetches, 1)
    vim.cmd("only")
  end)

  it("issues 진입점: :WorkKb issues, 접두 키 J, Jira 메뉴 l", function()
    local real = require("work-kb.jira")
    setup()
    local n = 0
    H.patch(I, "open", function()
      n = n + 1
    end)
    H.patch(package.loaded, "work-kb.jira", real)
    local wk = require("work-kb")
    wk.command({ fargs = { "issues" } })
    H.eq(n, 1)
    for _, e in ipairs(wk.keys) do
      if e.key == "J" then
        wk.call(e)
      end
    end
    H.eq(n, 2)
    H.eq(real.action("l").fn, "list")
    real.run("list")
    H.eq(n, 3)
  end)
end)
