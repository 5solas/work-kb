-- 새 일감·승격 양식 (form.lua). kb.py는 fake_spawn으로, 물음은 ui_stub으로 바꾼다.
local H = require("helpers")

local F

local function field(id, name, kind, over)
  return vim.tbl_extend("force", { id = id, name = name, kind = kind, required = false, has_default = false, allowed = {} }, over or {})
end

local TASK = {
  field("priority", "우선순위", "option", { required = true, has_default = true, allowed = { { id = "2", name = "High" }, { id = "3", name = "Low" } } }),
  field("assignee", "담당", "user"),
  field("components", "컴포넌트", "options", { allowed = { { id = "301", name = "API" }, { id = "302", name = "배치" } } }),
  field("labels", "레이블", "labels"),
  field("duedate", "마감", "date"),
  field("cf1", "에픽", "epic"),
  field("cf2", "점수", "number"),
  field("cf3", "스프린트", "unsupported"),
}

local function meta(over)
  return vim.tbl_extend("force", {
    project = "KB",
    stale = false,
    fallback = false,
    issuetypes = {
      { id = "1", name = "Task", description = { available = true, required = false }, fields = TASK },
      {
        id = "2",
        name = "Bug",
        description = { available = true, required = true },
        fields = { field("cf2", "점수", "number", { required = true }), TASK[4] },
      },
      {
        id = "3",
        name = "Sub",
        description = { available = true, required = false },
        fields = { field("parent", "상위", "unsupported", { required = true }) },
      },
    },
  }, over or {})
end

local function result(over)
  return vim.json.encode(vim.tbl_extend("force", { record = "R1", state = "done", key = "KB-16", message = "" }, over or {}))
end

---OUT: 명령("meta create", "act create" …) → 출력 글, { 코드, 출력 }, 또는 함수(call).
local function setup(answers, out)
  local vault = H.with_vault({ python = H.python(), server = { enable = false } })
  F = require("work-kb.form")
  F._reset()
  vim.cmd("silent! tabonly")
  vim.cmd("silent! only")
  vim.cmd("enew!")
  local t = { vault = vault, out = out or {} }
  t.out["status --root"] = t.out["status --root"] or '{"project_keys": ["KB", "OPS"]}\n'
  t.out["meta create"] = t.out["meta create"] or (vim.json.encode(meta()) .. "\n")
  t.out["act create"] = t.out["act create"] or ("보냄\n" .. result() .. "\n")
  t.stub = H.ui_stub(answers)
  H.patch(require("work-kb.ui"), "_can_prompt", function()
    return true
  end)
  t.refreshed = 0
  H.patch(require("work-kb.issues"), "refresh", function()
    t.refreshed = t.refreshed + 1
  end)
  t.spawn = H.fake_spawn(function(call)
    local v = t.out[call.argv[1] .. " " .. (call.argv[2] or "")]
    if type(v) == "function" then
      v = v(call)
    end
    if type(v) == "table" then
      return v[2], v[1]
    end
    return v or "", 0
  end)
  function t.settle()
    vim.wait(80, function()
      return false
    end)
  end
  function t.sent(sub)
    local list = {}
    for _, c in ipairs(t.spawn.calls) do
      if (c.argv[1] .. " " .. (c.argv[2] or "")) == sub then
        local args = vim.tbl_filter(function(a)
          return a ~= "--root" and a ~= vault
        end, c.argv)
        table.insert(list, vim.list_slice(args, 3))
      end
    end
    return list
  end
  function t.open(opts)
    F.open(opts)
    t.settle()
  end
  return t
end

local function text()
  return table.concat(vim.api.nvim_buf_get_lines(F._live.buf, 0, -1, false), "\n")
end

local function row(id)
  for lnum, r in pairs(F._live.rows) do
    if r.id == id then
      vim.api.nvim_set_current_win(F._live.win)
      vim.api.nvim_win_set_cursor(F._live.win, { lnum, 0 })
      return vim.api.nvim_buf_get_lines(F._live.buf, lnum - 1, lnum, false)[1]
    end
  end
  return nil
end

local function press(lhs)
  local m = vim.fn.maparg(lhs, "n", false, true)
  H.eq(m.buffer, 1, lhs)
  m.callback()
end

local function edit(t, id)
  H.ok(row(id), id .. " 줄 없음")
  press("<CR>")
  t.settle()
end

local function describe_text(s)
  vim.api.nvim_buf_set_lines(F._live.desc_buf, 0, -1, false, vim.split(s, "\n"))
end

describe("form 양식", function()
  it("form open: 항목을 필수·흔한 것부터 보이고 나머지는 접는다", function()
    local t = setup()
    t.open()
    H.contains(row("project"), "KB")
    H.contains(row("issuetype"), "Task")
    for _, id in ipairs({ "summary", "priority", "assignee", "components", "labels", "duedate", "cf1" }) do
      H.ok(row(id), id)
    end
    H.eq(row("cf2"), nil)
    H.eq(row("cf3"), nil)
    press("g.")
    H.ok(row("cf2"))
    H.contains(row("cf3"), "Jira 웹에서만")
    H.eq(vim.bo[F._live.buf].modifiable, false)
    H.eq(vim.bo[F._live.desc_buf].modifiable, true)
    H.eq(t.sent("meta create"), { { "KB", "--json" } })
  end)

  it("form 만들기: 고른 값을 한 번에 보내고 닫고 목록을 다시 그린다", function()
    local users = vim.json.encode({ me = { name = "hong", display = "홍길동" }, users = {} })
    local t = setup({
      { "input", "새 일감" },
      { "pick", "High" },
      { "input", "" },
      { "pick", "나 — 홍길동 (hong)" },
      { "pick", "  API" },
      { "pick", "  배치" },
      { "pick", "✓ API" },
      { "pick", "완료" },
      { "input", "a, b  c" },
      { "input", "2026-11-30" },
      { "confirm", true },
    }, { ["meta users"] = users })
    t.open()
    local tabs = #vim.api.nvim_list_tabpages()
    for _, id in ipairs({ "summary", "priority", "assignee", "components" }) do
      edit(t, id)
    end
    t.settle()
    edit(t, "labels")
    edit(t, "duedate")
    H.contains(row("assignee"), "홍길동 (hong)")
    H.contains(row("components"), "배치")
    describe_text("설명\n둘째 줄")
    press("<C-C><C-C>")
    t.settle()
    H.eq(t.stub.prompts[#t.stub.prompts].prompt, "KB에 Task 일감 '새 일감'을(를) 만들까요? ")
    H.eq(t.sent("act create"), {
      {
        "KB",
        "--summary=새 일감",
        "--type=Task",
        "--description=설명\n둘째 줄",
        '--field=priority="2"',
        '--field=assignee="hong"',
        '--field=components=["302"]',
        '--field=labels=["a","b","c"]',
        '--field=duedate="2026-11-30"',
        "--yes",
        "--json",
      },
    })
    H.eq(F._live, nil)
    H.eq(F._draft, nil)
    H.eq(#vim.api.nvim_list_tabpages(), tabs - 1)
    H.contains(t.stub.messages[#t.stub.messages], "KB-16")
    H.eq(t.refreshed, 1)
  end)

  it("form 필수: 비면 보내지 않고 그 줄로 간다", function()
    local t = setup({ { "pick", "Bug" }, { "input", "요약" }, { "input", "3" } })
    t.open()
    local function fails(msg)
      H.fails_with(F.submit, msg)
    end
    fails("필수 항목이 비었습니다: 요약")
    edit(t, "issuetype")
    edit(t, "summary")
    fails("필수 항목이 비었습니다: 점수")
    H.eq(F._live.rows[vim.api.nvim_win_get_cursor(F._live.win)[1]].id, "cf2")
    edit(t, "cf2")
    fails("필수 항목이 비었습니다: 설명")
    H.eq(vim.api.nvim_get_current_win(), F._live.desc_win)
    H.eq(t.sent("act create"), {})
  end)

  it("form 막힌 유형: 웹에서만 되는 필수 항목이 있으면 만들지 않는다", function()
    local t = setup({ { "pick", "Sub" }, { "input", "요약" } })
    t.open()
    edit(t, "issuetype")
    edit(t, "summary")
    H.fails_with(F.submit, "이 유형은 Jira 웹에서 만들어야 합니다: 상위")
  end)

  it("form 유형 바꾸기: 새 유형에 있는 값만 남기고 알린다", function()
    local t = setup({ { "input", "a" }, { "pick", "High" }, { "pick", "Bug" } })
    t.open()
    edit(t, "labels")
    edit(t, "priority")
    edit(t, "issuetype")
    H.contains(row("labels"), "a")
    H.eq(F._live.d.values.priority, nil)
    H.contains(t.stub.messages[#t.stub.messages], "우선순위")
  end)

  it("form 확인 거절: 보내지 않고 양식을 둔다", function()
    local t = setup({ { "input", "요약" }, { "confirm", false } })
    t.open()
    edit(t, "summary")
    press("<C-C><C-C>")
    t.settle()
    H.eq(t.sent("act create"), {})
    H.ok(F._live)
    H.eq(F._live.d.sending, false)
  end)

  it("form 연타: 보내는 동안 다시 보내지 않는다", function()
    local t = setup({ { "input", "요약" }, { "confirm", true } })
    t.open()
    edit(t, "summary")
    press("<C-C><C-C>")
    press("<C-C><C-C>")
    t.settle()
    H.eq(#t.sent("act create"), 1)
    H.contains(t.stub.messages[1], "보내는 중")
  end)

  it("form 실패: 양식을 두고, 다시 보낼 때 앞의 기록을 먼저 치운다", function()
    local t = setup({ { "input", "요약" }, { "confirm", true }, { "confirm", true } })
    t.out["act create"] = { 1, result({ state = "failed", key = vim.NIL, message = "필드 오류" }) .. "\n" }
    t.open()
    edit(t, "summary")
    describe_text("긴 글")
    press("<C-C><C-C>")
    t.settle()
    H.contains(text(), "필드 오류")
    H.eq(vim.api.nvim_buf_get_lines(F._live.desc_buf, 0, -1, false), { "긴 글" })
    t.out["act create"] = result() .. "\n"
    press("<C-C><C-C>")
    t.settle()
    H.eq(t.sent("act discard"), { { "R1" } })
    H.eq(#t.sent("act create"), 2)
    H.eq(F._live, nil)
  end)

  it("form 실패 기록의 상태가 바뀌었으면 다시 보내지 않고 중복을 알린다", function()
    local t = setup({ { "input", "요약" }, { "confirm", true }, { "confirm", true }, { "confirm", false } })
    t.out["act create"] = { 1, result({ state = "failed", message = "오류" }) .. "\n" }
    t.out["act discard"] = { 4, "실패한 기록이 아님\n" }
    t.open()
    edit(t, "summary")
    press("<C-C><C-C>")
    t.settle()
    press("<C-C><C-C>")
    t.settle()
    H.eq(#t.sent("act create"), 1)
    H.contains(t.stub.messages[#t.stub.messages], "act list")
    press("<C-C><C-C>")
    H.contains(t.stub.prompts[#t.stub.prompts].prompt, "중복")
  end)

  it("form none: 기록이 남지 않으면 양식을 둔다 (토큰 없음)", function()
    local t = setup({ { "input", "요약" }, { "confirm", true } })
    t.out["act create"] = { 2, result({ record = vim.NIL, state = "none", key = vim.NIL, message = "토큰 없음" }) .. "\n" }
    t.open()
    edit(t, "summary")
    press("<C-C><C-C>")
    t.settle()
    H.contains(text(), "토큰 없음")
    H.eq(F._live.d.sending, false)
  end)

  it("form 불확실·대기: 닫고 기억하며, 다시 보낼 때 중복을 알린다", function()
    for _, case in ipairs({
      { { 3, result({ state = "uncertain", key = vim.NIL }) }, "확인" },
      { { 3, result({ state = "pending", key = vim.NIL }) }, "outbox" },
      { { 0, "잘린 출력 {\"record" }, "확인" },
      { { 1, "" }, "확인" },
    }) do
      local t = setup({ { "input", "요약" }, { "confirm", true }, { "confirm", true }, { "confirm", false } })
      t.out["act create"] = case[1]
      t.open()
      edit(t, "summary")
      press("<C-C><C-C>")
      t.settle()
      H.eq(F._live, nil)
      H.contains(t.stub.messages[#t.stub.messages], case[2])
      t.open()
      H.contains(row("summary"), "요약")
      press("<C-C><C-C>")
      H.contains(t.stub.prompts[#t.stub.prompts].prompt, "중복")
      F.close()
      t.settle()
    end
  end)

  it("form 보내는 동안 닫아도 결과를 알리고, 실패면 내용을 기억한다", function()
    local t = setup({ { "input", "요약" }, { "confirm", true }, { "confirm", true } })
    t.out["act create"] = { 1, result({ state = "failed", message = "오류" }) .. "\n" }
    t.open()
    edit(t, "summary")
    describe_text("본문")
    press("<C-C><C-C>")
    vim.cmd("tabclose")
    t.settle()
    H.eq(F._live, nil)
    H.contains(t.stub.messages[#t.stub.messages], "오류")
    t.open()
    H.contains(row("summary"), "요약")
    H.eq(vim.api.nvim_buf_get_lines(F._live.desc_buf, 0, -1, false), { "본문" })
  end)

  it("form 닫기: 한 창을 닫으면 둘 다 닫히고, 이어 쓰거나 버린다", function()
    local t = setup({ { "input", "요약" }, { "confirm", true }, { "confirm", false } })
    t.open()
    local tabs = #vim.api.nvim_list_tabpages()
    edit(t, "summary")
    describe_text("본문")
    vim.api.nvim_set_current_win(F._live.desc_win)
    vim.cmd("quit")
    t.settle()
    H.eq(#vim.api.nvim_list_tabpages(), tabs - 1)
    H.eq(F._live, nil)
    t.open()
    H.contains(row("summary"), "요약")
    press("<C-C><C-K>")
    t.settle()
    H.eq(F._live, nil)
    t.open()
    H.contains(row("summary"), "—")
    H.eq(vim.api.nvim_buf_get_lines(F._live.desc_buf, 0, -1, false), { "" })
  end)

  it("form 설명 버퍼를 지워도 마지막 글을 기억한다", function()
    local t = setup({ { "confirm", true } })
    t.open()
    describe_text("지워질 글")
    vim.api.nvim_exec_autocmds("TextChanged", { buffer = F._live.desc_buf })
    vim.cmd("bwipeout! " .. F._live.desc_buf)
    t.settle()
    t.open()
    H.eq(vim.api.nvim_buf_get_lines(F._live.desc_buf, 0, -1, false), { "지워질 글" })
  end)

  it("form 빈 양식은 닫을 때 기억하지 않고, 이미 열려 있으면 그 창으로 간다", function()
    local t = setup()
    t.open()
    local win = F._live.win
    vim.cmd("tabfirst")
    t.open()
    H.eq(vim.api.nvim_get_current_win(), win)
    F.close()
    t.settle()
    H.eq(F._draft, nil)
  end)

  it("form 후보를 받지 못하면 유형·요약·설명만으로 만든다", function()
    local t = setup({ { "input", "요약" }, { "confirm", true } })
    t.out["meta create"] = { 2, "토큰이 없습니다\n" }
    t.open()
    H.contains(text(), "토큰이 없습니다")
    H.contains(row("issuetype"), "Task")
    edit(t, "summary")
    press("<C-C><C-C>")
    t.settle()
    H.eq(t.sent("act create"), { { "KB", "--summary=요약", "--type=Task", "--yes", "--json" } })
  end)

  it("form 프로젝트를 바꾸면 새로 받고, 늦게 온 앞의 답은 버린다", function()
    local t = setup({ { "pick", "OPS" } })
    t.out["meta create"] = function(call)
      local project = vim.tbl_contains(call.argv, "OPS") and "OPS" or "KB"
      return vim.json.encode(meta({ project = project, issuetypes = { { id = "9", name = project .. "유형", description = {}, fields = {} } } }))
    end
    F.open()
    row("project")
    press("<CR>")
    t.settle()
    H.contains(row("project"), "OPS")
    H.contains(row("issuetype"), "OPS유형")
    H.eq(#t.sent("meta create"), 2)
  end)

  it("form 오래된 후보: 뒤에서 받고 r로 반영하며 없어진 값을 알린다", function()
    local t = setup({ { "pick", "High" } })
    local fresh = meta()
    fresh.issuetypes = vim.deepcopy(fresh.issuetypes)
    fresh.issuetypes[1].fields[1].allowed = { { id = "3", name = "Low" } }
    t.out["meta create"] = function(call)
      if vim.tbl_contains(call.argv, "--refresh") then
        return vim.json.encode(fresh)
      end
      return vim.json.encode(meta({ stale = true }))
    end
    t.open()
    H.contains(t.stub.messages[1], "r로 반영")
    edit(t, "priority")
    H.contains(row("priority"), "High")
    press("r")
    t.settle()
    H.contains(row("priority"), "—")
    H.contains(t.stub.messages[#t.stub.messages], "우선순위")
  end)

  it("form x: 값을 지우고, 프로젝트·유형은 지우지 않는다", function()
    local t = setup({ { "input", "a" } })
    t.open()
    edit(t, "labels")
    row("labels")
    press("x")
    H.contains(row("labels"), "—")
    row("project")
    press("x")
    H.contains(row("project"), "KB")
  end)

  it("form 고르기 취소와 닫은 뒤의 답은 값을 바꾸지 않는다", function()
    local t = setup({ { "pick" }, { "input" } })
    t.open()
    edit(t, "priority")
    edit(t, "labels")
    H.eq(F._live.d.values, {})
    local cb
    H.patch(require("work-kb.ui"), "pick", function(_, _, f)
      cb = f
    end)
    edit(t, "priority")
    F.close()
    t.settle()
    cb("High")
    t.settle()
    H.eq(F._live, nil)
  end)

  it("form 에픽: 캐시된 일감에서 고른다", function()
    local t = setup({ { "pick", "KB-5 큰 일" } })
    H.patch(require("work-kb.jira"), "_cached_issues", function()
      return { { key = "KB-5", summary = "큰 일", issuetype = "Epic" }, { key = "KB-6", summary = "작은 일", issuetype = "Task" } }
    end)
    t.open()
    edit(t, "cf1")
    H.eq(t.stub.last_items, { "KB-5 큰 일", "직접 입력…" })
    H.eq(F._live.d.values.cf1, "KB-5")
  end)
end)

local DAILY = "---\ndate: 2026-11-05\n---\n## 기록\n- 08:00 첫\n- [ ] 09:30 경비 정산\n"

local function visit(vault)
  local file = vault .. "/daily/2026-11-05.md"
  H.mkdir_p(vault .. "/daily")
  H.write_bytes(file, DAILY)
  vim.cmd.edit(vim.fn.fnameescape(file))
  vim.api.nvim_win_set_cursor(0, { 6, 0 })
  return vim.api.nvim_get_current_buf(), file
end

describe("form 승격", function()
  it("form 승격: 처음 줄과 해시를 보내고 daily 버퍼를 다시 읽는다", function()
    local t = setup({ { "confirm", true } })
    local buf, file = visit(t.vault)
    t.out["act promote"] = function()
      H.write_bytes(file, (DAILY:gsub("경비 정산\n", "경비 정산 [[KB-16]]\n")))
      vim.uv.fs_utime(file, os.time() + 5, os.time() + 5)
      return result({ linked = true }) .. "\n"
    end
    require("work-kb.jira").promote()
    vim.api.nvim_buf_call(buf, function()
      vim.api.nvim_win_set_cursor(0, { 5, 0 })
    end)
    t.settle()
    H.contains(row("summary"), "경비 정산")
    H.contains(text(), "daily/2026-11-05.md:6")
    press("<C-C><C-C>")
    t.settle()
    H.eq(t.sent("act promote"), {
      {
        "--from",
        "daily/2026-11-05.md:6",
        "KB",
        "--type=Task",
        "--expect-line=" .. vim.fn.sha256("- [ ] 09:30 경비 정산"),
        "--yes",
        "--json",
      },
    })
    H.contains(table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n"), "[[KB-16]]")
    vim.cmd("enew!")
  end)

  it("form 승격: 요약을 고치면 보내고, 줄이 바뀌어 거부되면 글을 둔다", function()
    local t = setup({ { "input", "고친 제목" }, { "confirm", true } })
    visit(t.vault)
    t.out["act promote"] = { 4, result({ record = vim.NIL, state = "none", message = "그사이 줄이 바뀌었습니다" }) }
    require("work-kb.jira").promote()
    t.settle()
    edit(t, "summary")
    press("<C-C><C-C>")
    t.settle()
    H.ok(vim.tbl_contains(t.sent("act promote")[1], "--summary=고친 제목"))
    H.contains(text(), "그사이 줄이 바뀌었습니다")
    H.contains(row("summary"), "고친 제목")
    F.close()
    t.settle()
    vim.cmd("enew!")
  end)

  it("form 승격: 버퍼가 수정돼 다시 읽지 못하면, 링크를 붙이지 못했으면 알린다", function()
    local t = setup({ { "confirm", true } })
    local buf = visit(t.vault)
    t.out["act promote"] = function()
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "- 10:00 쓰는 중" })
      return result({ linked = false }) .. "\n"
    end
    require("work-kb.jira").promote()
    t.settle()
    press("<C-C><C-C>")
    t.settle()
    local all = table.concat(t.stub.messages, "\n")
    H.contains(all, "다시 읽지 못했습니다")
    H.contains(all, "붙이지 못했습니다")
    H.ok(vim.bo[buf].modified)
    vim.cmd("enew!")
    vim.api.nvim_buf_delete(buf, { force = true })
  end)

  it("form 승격: 빈 줄·daily 밖은 열지 않는다", function()
    local t = setup()
    H.fails_with(function()
      require("work-kb.jira").promote()
    end, "승격은 daily 파일의 줄에서만 됩니다")
    local buf = visit(t.vault)
    vim.api.nvim_buf_set_lines(buf, 5, 6, false, { "" })
    H.fails_with(function()
      require("work-kb.jira").promote()
    end, "빈 줄은 승격할 수 없습니다")
    H.eq(F._live, nil)
    vim.cmd("enew!")
    vim.api.nvim_buf_delete(buf, { force = true })
  end)

  it("form 승격: 쓰던 양식이 있으면 버릴지 묻고, 아니면 그대로 둔다", function()
    local t = setup({ { "input", "쓰던 것" }, { "confirm", false } })
    t.open()
    edit(t, "summary")
    F.close()
    t.settle()
    visit(t.vault)
    require("work-kb.jira").promote()
    t.settle()
    H.eq(F._live, nil)
    H.eq(F._draft.summary, "쓰던 것")
    vim.cmd("enew!")
  end)
end)
