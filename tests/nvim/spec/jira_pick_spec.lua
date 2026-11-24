-- Jira 동작의 고르기: 키·담당·워크로그·JQL·메뉴 창 (화면이 있는 것처럼 _can_prompt를 바꾼다).
local H = require("helpers")

local function setup(answers, outputs)
  local vault = H.with_vault({ python = H.python(), server = { enable = false } })
  local t = { vault = vault, jira = require("work-kb.jira") }
  t.stub = H.ui_stub(answers)
  H.patch(require("work-kb.ui"), "_can_prompt", function()
    return true
  end)
  H.patch(t.jira, "_recent_jql", {})
  H.patch(t.jira, "_cached_issues", function()
    return { { key = "KB-12", summary = "배포" }, { key = "OPS-3", summary = "인증서" } }
  end)
  H.patch(require("work-kb.issues"), "fetch", function()
    return { issues = {}, views = {}, view_jql = { today = "assignee = me", blocked = "status = Blocked" } }
  end)
  t.spawn = H.fake_spawn(function(call)
    local key = call.argv[1] .. " " .. (call.argv[2] or "")
    local out = (outputs or {})[key]
    if type(out) == "table" then
      return out[2], out[1]
    end
    return out or "", 0
  end)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "키 없는 줄" })
  function t.settle()
    vim.wait(100, function()
      return false
    end)
  end
  function t.sent()
    return vim.tbl_map(function(c)
      local out = {}
      for _, a in ipairs(c.argv) do
        if a ~= "--root" and a ~= vault then
          table.insert(out, a)
        end
      end
      return out
    end, t.spawn.calls)
  end
  return t
end

local USERS = vim.json.encode({
  me = { name = "hong", display = "홍길동" },
  users = { { name = "kimcs", display = "김철수" }, { name = "hong", display = "홍길동" } },
})

describe("jira 고르기", function()
  it("pick 키: 캐시된 일감에서 고른다", function()
    local t = setup({ { "pick", "OPS-3 인증서" } })
    local got
    t.jira.resolve_key(nil, function(k)
      got = k
    end)
    H.eq(t.stub.last_items, { "KB-12 배포", "OPS-3 인증서", t.jira.DIRECT })
    H.eq(got, "OPS-3")
  end)

  it("pick 키: 직접 입력과 취소, 빈 캐시", function()
    local t = setup({ { "pick", "직접 입력…" }, { "input", " kb-7 " }, { "pick" }, { "input", "KB-8" } })
    local got = {}
    local function cb(k)
      table.insert(got, k)
    end
    t.jira.resolve_key(nil, cb)
    H.fails_with(function()
      t.jira.resolve_key(nil, cb)
    end, "work-kb: 취소했습니다")
    H.patch(t.jira, "_cached_issues", function()
      return {}
    end)
    t.jira.resolve_key(nil, cb)
    H.eq(got, { "KB-7", "KB-8" })
  end)

  it("pick 담당: 이름으로 찾아 고르고 사용자명을 보낸다", function()
    local t = setup({ { "input", "철수" }, { "pick", "김철수 (kimcs)" } }, { ["meta users"] = "경고\n" .. USERS .. "\n" })
    t.jira.assign({ key = "KB-12" })
    t.settle()
    H.eq(t.stub.last_items, { "나 — 홍길동 (hong)", "김철수 (kimcs)", t.jira.DIRECT })
    H.eq(t.sent(), {
      { "meta", "users", "--json", "--query=철수", "--issue=KB-12" },
      { "act", "assign", "--", "KB-12", "kimcs" },
    })
    H.eq(H.buffer_text(t.jira.USERS_BUFFER), "")
  end)

  it("pick 담당: 받지 못하면 직접 친다", function()
    local t = setup({ { "input", "kim" }, { "input", ":initial" } }, { ["meta users"] = { 3, "연결 실패\n" } })
    t.jira.assign({ key = "KB-12" })
    t.settle()
    H.contains(t.stub.messages[1], "담당 후보를 받지 못했습니다")
    H.eq(t.sent()[2], { "act", "assign", "--", "KB-12", "kim" })
  end)

  it("pick 담당: 고르지 않으면 보내지 않는다", function()
    local t = setup({ { "input", "" }, { "pick" } }, { ["meta users"] = USERS })
    t.jira.assign({ key = "KB-12" })
    t.settle()
    H.eq(#t.sent(), 1)
    H.eq(t.stub.messages, { "work-kb: 취소했습니다" })
  end)

  it("pick 워크로그: 흔한 시간에서 고르거나 직접", function()
    local t = setup({ { "pick", "30m" }, { "input", "" }, { "pick", "직접 입력…" }, { "input", "1h 15m" }, { "input", "" } })
    t.jira.worklog({ key = "KB-12" })
    t.jira.worklog({ key = "KB-12" })
    t.settle()
    H.eq(t.sent(), { { "act", "worklog", "KB-12", "30m" }, { "act", "worklog", "KB-12", "1h15m" } })
  end)

  it("pick JQL: 저장된 뷰와 최근 질의", function()
    local t = setup({ { "pick", "[today] assignee = me" }, { "pick", "직접 입력…" }, { "input", "a = b" }, { "pick", "a = b" } })
    t.jira.query()
    H.eq(t.stub.last_items, { "[blocked] status = Blocked", "[today] assignee = me", t.jira.DIRECT })
    t.jira.query()
    t.jira.query()
    H.eq(vim.list_slice(t.stub.last_items, 1, 2), { "a = b", "assignee = me" })
    t.settle()
    H.eq(t.sent()[1], { "sync", "--jql=assignee = me" })
    H.eq(t.sent()[3], { "sync", "--jql=a = b" })
  end)

  it("pick JQL: 최근 질의는 10개까지", function()
    local t = setup()
    for i = 1, 12 do
      t.jira.query("q" .. i)
    end
    t.settle()
    H.eq(#t.jira._recent_jql, 10)
    H.eq(t.jira._recent_jql[1], "q12")
  end)

  it("pick 댓글: 고르는 동안 커서가 움직여도 처음 daily 줄을 쓴다", function()
    local t = setup({ { "input", ":initial" } })
    local file = t.vault .. "/daily/2026-11-05.md"
    H.mkdir_p(t.vault .. "/daily")
    H.write_bytes(file, "## 기록\n- 첫 줄\n- 둘째 줄\n")
    vim.cmd.edit(vim.fn.fnameescape(file))
    vim.api.nvim_win_set_cursor(0, { 2, 0 })
    local cb
    H.patch(require("work-kb.ui"), "pick", function(_, _, f)
      cb = f
    end)
    t.jira.comment()
    vim.api.nvim_win_set_cursor(0, { 3, 0 })
    cb("KB-12 배포")
    t.settle()
    H.eq(t.sent(), { { "act", "comment", "KB-12", "--from-line", "daily/2026-11-05.md:2" } })
    vim.cmd("enew!")
  end)

  it("pick 메뉴: 뜨는 창에 키와 설명을 보이고 닫는다", function()
    local t = setup()
    local wins, seen = #vim.api.nvim_list_wins(), nil
    H.patch(t.jira, "_read_key", function()
      for _, w in ipairs(vim.api.nvim_list_wins()) do
        if vim.api.nvim_win_get_config(w).relative == "editor" then
          seen = table.concat(vim.api.nvim_buf_get_lines(vim.api.nvim_win_get_buf(w), 0, -1, false), "\n")
        end
      end
      return "\27"
    end)
    t.jira.menu()
    H.contains(seen, "t  전환")
    H.contains(seen, "만들기")
    H.eq(#vim.api.nvim_list_wins(), wins)
    H.patch(t.jira, "_read_key", function()
      require("work-kb.ui").fail("화면 없음")
    end)
    H.fails_with(t.jira.menu, "화면 없음")
    H.eq(#vim.api.nvim_list_wins(), wins)
  end)

  it("ui.pick protect: 콜백의 사용자 오류는 문구만 보인다", function()
    local ui = require("work-kb.ui")
    H.patch(ui, "_can_prompt", function()
      return true
    end)
    H.patch(vim.ui, "select", function(items, _, cb)
      cb(items[1])
    end)
    local echoed = {}
    H.patch(vim.api, "nvim_echo", function(chunks)
      table.insert(echoed, chunks[1][1])
    end)
    ui.pick("x", { "a" }, function()
      ui.fail("안 됩니다")
    end)
    ui.pick("x", { "a" }, function()
      error("버그")
    end)
    H.eq(echoed[1], "안 됩니다")
    H.contains(echoed[2], "traceback")
  end)
end)
