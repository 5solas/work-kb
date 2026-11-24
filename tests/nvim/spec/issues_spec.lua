-- J: Jira 일감 목록 (issues.lua). 자료·그리기는 순수 함수로, 버퍼·키는 fetch를 바꿔 끼워 시험한다.
local H = require("helpers")

local function issues()
  return require("work-kb.issues")
end

local TODAY = "2026-11-25"

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
    sprint = "",
    labels = {},
    components = {},
    updated = "2026-11-20T09:00:00+09:00",
    due = "2026-11-28",
    url = "",
  }, over or {})
end

local function data(list)
  return { synced_at = TODAY .. "T09:10:00+09:00", views = { "blocked", "today" }, issues = list }
end

local function state(over)
  return vim.tbl_extend("force", { status = "open" }, over or {})
end

local function keys(list)
  return vim.tbl_map(function(i)
    return i.key
  end, list)
end

local dw = vim.fn.strdisplaywidth

---줄에서 TEXT가 시작하는 표시 칸.
local function column(line, text)
  local s = line:find(text, 1, true)
  H.ok(s, text .. " 없음: " .. line)
  return dw(line:sub(1, s - 1))
end

local function hls(r, lnum)
  local out = {}
  for _, m in ipairs(r.marks) do
    if m[1] == lnum then
      out[m[4]] = m
    end
  end
  return out
end

describe("issues 자료", function()
  it("issues fetch: --json과 --view를 보내고 앞의 로그 줄을 건너뛴다", function()
    H.with_vault({ python = H.python(), server = { enable = false } })
    local rec = H.fake_spawn({
      output = "jira/KB-9.md: 깨짐\n"
        .. vim.json.encode({ synced_at = vim.NIL, views = { "today" }, issues = { issue("KB-1") } })
        .. "\n",
    })
    local got = issues().fetch("today")
    H.eq(keys(got.issues), { "KB-1" })
    H.eq(got.synced_at, nil)
    H.eq(rec.calls[1].argv[1], "issues")
    H.ok(vim.tbl_contains(rec.calls[1].argv, "--json"))
    H.ok(vim.tbl_contains(rec.calls[1].argv, "--view=today"))
    issues().fetch()
    H.not_contains(table.concat(rec.calls[2].argv, " "), "--view")
  end)

  it("issues fetch: 실패·JSON 아님·오류면 nil과 이유", function()
    H.with_vault({ python = H.python(), server = { enable = false } })
    for _, case in ipairs({ { 1, "오류: 없는 뷰: x\n" }, { 0, "이상한 글\n" }, { 0, "{}\n" }, { 0, "" } }) do
      H.fake_spawn({ code = case[1], output = case[2] })
      local got, why = issues().fetch()
      H.eq(got, nil)
      H.ok(type(why) == "string" and why ~= "", vim.inspect(why))
    end
    H.patch(require("work-kb.proc"), "run_output", function()
      require("work-kb.ui").fail("work-kb: Python이 없습니다")
    end)
    local got, why = issues().fetch()
    H.eq(got, nil)
    H.contains(why, "Python이 없습니다")
  end)

  it("issues filter: 상태·프로젝트·담당, 뷰는 상태를 무시", function()
    local f = issues().filter
    local list = {
      issue("KB-1"),
      issue("KB-2", { status_category = "done" }),
      issue("OPS-3", { assignee = "홍길동" }),
      issue("KB-4", { status_category = "", cached = false }),
    }
    H.eq(keys(f(list, state())), { "KB-1", "OPS-3", "KB-4" })
    H.eq(keys(f(list, state({ status = "done" }))), { "KB-2" })
    H.eq(#f(list, state({ status = "all" })), 4)
    H.eq(#f(list, state({ view = "today" })), 4)
    H.eq(keys(f(list, state({ view = "today", project = "OPS" }))), { "OPS-3" })
    H.eq(keys(f(list, state({ assignee = "홍길동" }))), { "OPS-3" })
    H.eq(f(list, state({ assignee = "홍길" })), {})
  end)

  it("issues sort: 진행 → 할 일 → 모름 → 완료, 갱신 내림차순, 키 번호 순; 뷰는 그대로", function()
    local list = {
      issue("KB-1", { status_category = "done" }),
      issue("KB-2", { status_category = "" }),
      issue("KB-10"),
      issue("KB-9"),
      issue("KB-3", { updated = "2026-11-24T09:00:00+09:00" }),
      issue("KB-5", { status_category = "indeterminate" }),
    }
    H.eq(keys(issues().sort(list, false)), { "KB-5", "KB-3", "KB-9", "KB-10", "KB-2", "KB-1" })
    H.eq(keys(issues().sort(list, true)), { "KB-1", "KB-2", "KB-10", "KB-9", "KB-3", "KB-5" })
    H.eq(list[1].key, "KB-1") -- 받은 목록은 바꾸지 않는다
  end)

  it("issues values: 서로 다른 값, 빈 값 제외, 정렬", function()
    local list = {
      issue("KB-1", { assignee = "나" }),
      issue("KB-2", { assignee = "" }),
      issue("KB-3", { assignee = "가" }),
      issue("KB-4", { assignee = "나" }),
    }
    H.eq(issues().values(list, "assignee"), { "가", "나" })
  end)
end)

describe("issues 그리기", function()
  it("issues render: 머리줄에 거르기·건수·동기화 시각", function()
    local list = { issue("KB-1"), issue("KB-2", { assignee = "홍길동" }), issue("KB-3", { status_category = "done" }) }
    local r = issues().render(data(list), state({ assignee = "김철수" }), 120, TODAY)
    for _, part in ipairs({ "[미완료]", "프로젝트: 전체", "담당: 김철수", "뷰: —", "1/3건", "동기화 09:10" }) do
      H.contains(r.lines[1], part)
    end
    H.contains(issues().render(data(list), state({ view = "today" }), 120, TODAY).lines[1], "[전체]")
    local old = data(list)
    old.synced_at = "2026-11-24T09:10:00+09:00"
    H.contains(issues().render(old, state(), 120, TODAY).lines[1], "동기화 11-24 09:10")
    old.synced_at = nil
    H.contains(issues().render(old, state(), 120, TODAY).lines[1], "동기화 없음")
    H.contains(r.lines[#r.lines], "g? 도움말")
  end)

  it("issues render: 일감 줄은 맨 키로 시작하고 rows가 줄을 가리킨다", function()
    local r = issues().render(data({ issue("KB-1"), issue("OPS-22") }), state(), 100, TODAY)
    local n = 0
    for lnum, row in pairs(r.rows) do
      n = n + 1
      H.eq(r.lines[lnum]:match("^ ([A-Z][A-Z0-9_]+%-%d+) "), row.key)
      H.contains(r.lines[lnum], "11-28")
    end
    H.eq(n, 2)
    H.eq(r.rows[1], nil)
    H.eq(r.rows[3].key, "KB-1")
  end)

  it("issues render: 한글 폭에서도 열이 맞는다", function()
    local list = {
      issue("KB-1", { status = "진행 중", summary = "제목A" }),
      issue("OPS-2222", { status = "Open", priority = "", summary = "제목B 한글", assignee = "가나다라마바사아자차카타" }),
      issue("KB-3", { status = "아주 긴 상태 이름입니다 정말로", summary = "제목C", assignee = "" }),
    }
    local r = issues().render(data(list), state(), 100, TODAY)
    local cols, due = {}, {}
    for lnum = 3, 5 do
      cols[column(r.lines[lnum], "제목")] = true
      due[column(r.lines[lnum], "11-28")] = true
      H.ok(dw(r.lines[lnum]) <= 100, r.lines[lnum])
    end
    H.eq(vim.tbl_count(cols), 1)
    H.eq(vim.tbl_count(due), 1)
  end)

  it("issues render: 좁으면 우선순위, 담당, 마감 순으로 숨긴다", function()
    local list = { issue("KB-1", { priority = "PRIO", assignee = "ASGN", status = "ST", summary = string.rep("x", 200) }) }
    local function shown(width)
      local line = issues().render(data(list), state(), width, TODAY).lines[3]
      H.ok(dw(line) <= math.max(width, 21), width .. ": " .. line)
      return { line:find("PRIO") ~= nil, line:find("ASGN") ~= nil, line:find("11-28", 1, true) ~= nil }
    end
    -- 바닥: 1 + 4(KB-1) + 2 + 2(ST) + 2 + 10 = 21. 마감 +7, 담당 +6, 우선순위 +6.
    H.eq(shown(80), { true, true, true })
    H.eq(shown(40), { true, true, true })
    H.eq(shown(39), { false, true, true })
    H.eq(shown(33), { false, false, true })
    H.eq(shown(27), { false, false, false })
    for _, width in ipairs({ 10, 1, 0 }) do
      H.eq(shown(width), { false, false, false })
    end
  end)

  it("issues render: 이모지·탭·줄바꿈·결합 글자를 표시 폭으로 자른다", function()
    local list = {
      issue("KB-1", { summary = string.rep("😀", 80) }),
      issue("KB-2", { summary = "가\t나\n다\r라" }),
      issue("KB-3", { summary = string.rep("가", 80) }),
      issue("KB-4", { summary = string.rep("e" .. vim.fn.nr2char(0x301), 200) }),
    }
    for width = 30, 70 do
      local r = issues().render(data(list), state(), width, TODAY)
      for _, line in ipairs(r.lines) do
        H.ok(dw(line) <= width, width .. ": " .. line)
        H.eq(line:find("%c"), nil)
      end
    end
    local r = issues().render(data(list), state(), 60, TODAY)
    H.contains(r.lines[3], "…")
    H.contains(table.concat(r.lines, "\n"), "가 나 다 라")
    local cols = {}
    for lnum = 3, 6 do
      cols[column(r.lines[lnum], "11-28")] = true
    end
    H.eq(vim.tbl_count(cols), 1)
  end)

  it("issues render: 지난 마감은 완료가 아닐 때만 경고색", function()
    local list = {
      issue("KB-1", { due = "2026-11-24" }),
      issue("KB-2", { due = TODAY }),
      issue("KB-3", { due = "" }),
      issue("KB-4", { due = "2026-11-24", status_category = "done" }),
    }
    local r = issues().render(data(list), state({ status = "all" }), 100, TODAY)
    for lnum, row in pairs(r.rows) do
      local mark = hls(r, lnum).WorkKbIssueOverdue
      H.eq(mark ~= nil, row.key == "KB-1", row.key)
      if mark then
        H.eq(r.lines[lnum]:sub(mark[2] + 1, mark[3]), "11-24")
      end
      if row.key == "KB-3" then
        H.contains(r.lines[lnum], "—")
      end
    end
  end)

  it("issues render: 상태 범주 색과 캐시에 없는 줄", function()
    local list = {
      issue("KB-1", { status_category = "indeterminate" }),
      issue("KB-2"),
      issue("KB-3", { cached = false, status_category = "" }),
    }
    local r = issues().render(data(list), state({ view = "today" }), 100, TODAY)
    H.ok(hls(r, 3).WorkKbIssueProgress)
    H.ok(hls(r, 4).WorkKbIssueTodo)
    local m = hls(r, 5).WorkKbIssueUncached
    H.eq({ m[2], m[3] }, { 0, #r.lines[5] })
    H.ok(hls(r, 1).WorkKbIssueHeader)
  end)

  it("issues render: 빈 캐시와 거르기로 빈 목록의 안내가 다르다", function()
    local text = table.concat(issues().render(data({}), state(), 100, TODAY).lines, "\n")
    H.contains(text, "캐시에 일감이 없습니다. S로 동기화하세요")
    local r = issues().render(data({ issue("KB-1") }), state({ assignee = "없는 사람" }), 100, TODAY)
    H.contains(r.lines[1], "담당: 없는 사람")
    H.contains(table.concat(r.lines, "\n"), "조건에 맞는 일감이 없습니다. x로 거르기를 지우세요")
    H.contains(
      table.concat(issues().render(data({}), state({ view = "today" }), 100, TODAY).lines, "\n"),
      "x로 거르기를 지우세요"
    )
  end)

  it("issues render: 5000건을 0.5초 안에", function()
    local list = {}
    for i = 1, 5000 do
      list[i] = issue("KB-" .. i, { summary = string.rep("긴 제목 ", 20) })
    end
    local t = vim.uv.hrtime()
    local r = issues().render(data(list), state(), 100, TODAY)
    local ms = (vim.uv.hrtime() - t) / 1e6
    H.eq(vim.tbl_count(r.rows), 5000)
    H.ok(ms < 500, ms .. " ms")
  end)
end)
