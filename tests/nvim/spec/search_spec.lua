-- s (검색): ERT search-falls-back-to-kb-py + rg·fzf-lua 단계, 프롬프트, quickfix 목록.
local H = require("helpers")

local function search()
  return require("work-kb.search")
end

local function proc()
  return require("work-kb.proc")
end

local function cleanup()
  pcall(vim.fn.setqflist, {}, "r", { title = "", items = {} })
  pcall(vim.cmd, "cclose")
  pcall(vim.cmd, "silent! only!")
  pcall(vim.cmd, "silent! %bwipeout!")
end

local function case(name, fn)
  it(name, function()
    local ok, err = pcall(fn)
    cleanup()
    if not ok then
      error(err, 0)
    end
  end)
end

---quickfix 목록 (파일 이름은 슬래시로, 줄, 본문)과 제목.
local function qf()
  local info = vim.fn.getqflist({ items = 0, title = 0 })
  local out = {}
  for _, item in ipairs(info.items) do
    table.insert(out, { vim.fs.normalize(vim.api.nvim_buf_get_name(item.bufnr)), item.lnum, item.text })
  end
  return out, info.title
end

local function lower_paths(list)
  for _, item in ipairs(list) do
    item[1] = item[1]:lower()
  end
  return list
end

local function no_fzf_no_rg()
  H.patch(search(), "_fzf", function()
    return nil
  end)
  H.patch(search(), "_rg", function()
    return nil
  end)
end

describe("search", function()
  case("search-falls-back-to-kb-py: fzf-lua·rg가 없으면 kb.py search --root V 질의", function()
    local vault = H.with_vault({ python = H.python() })
    H.ui_stub()
    no_fzf_no_rg()
    local fake = H.fake_spawn({ code = 0, output = "결과 없음\n" })
    local handle = search().search("한글 질의")
    proc().wait(handle, 5000)
    H.eq(#fake.calls, 1)
    H.eq(fake.calls[1].argv, { "search", "--root", vault, "한글 질의" })
    H.eq(proc().buffer_name(handle.bufnr), "*work-kb-search*")
  end)

  case("kb.py search 결과(경로:줄: 본문)를 quickfix로, 덧붙은 줄은 알림", function()
    local vault = H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    no_fzf_no_rg()
    H.fake_spawn({
      code = 0,
      output = "건너뜀: daily/2026-11-01.md: UTF-8이 아님\r\n"
        .. "daily/2026-11-05.md:12: - 09:12 캐시 KB-12 논의\r\n"
        .. "wiki/topics/설계 원칙.md:3: 캐시: 원칙 a:b\n"
        .. "… 2개 더 있음 (전체 4개, --limit 0이면 전부)\n",
    })
    local before = vim.api.nvim_get_current_win()
    proc().wait(search().search("  캐시  "), 5000)
    local items, title = qf()
    H.eq(lower_paths(items), lower_paths({
      { vault .. "/daily/2026-11-05.md", 12, "- 09:12 캐시 KB-12 논의" },
      { vault .. "/wiki/topics/설계 원칙.md", 3, "캐시: 원칙 a:b" },
    }))
    H.eq(title, "work-kb 검색: 캐시")
    H.eq(vim.api.nvim_get_current_win(), before, "초점은 그대로")
    H.ok(vim.fn.getqflist({ winid = 0 }).winid ~= 0, "quickfix 창이 열려야 함")
    H.eq(stub.messages, { "work-kb: … 2개 더 있음 (전체 4개, --limit 0이면 전부)" })
  end)

  case("kb.py search: 결과 없음 / 실패 (종료 N)", function()
    H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    no_fzf_no_rg()
    H.fake_spawn({ code = 0, output = "결과 없음\n" })
    proc().wait(search().search("없는 말"), 5000)
    H.eq(stub.messages, { "결과 없음" })
    H.fake_spawn({ code = 4, output = "오류: 설정\n" })
    local handle = search().search("x")
    proc().wait(handle, 5000)
    H.eq(stub.messages[2], "work-kb: 검색 실패 (종료 4)")
    H.eq(stub.shown, { handle.bufnr })
  end)

  case("rg: 인수·작업 폴더·stdin 패턴, 결과는 quickfix, 종료 1은 결과 없음", function()
    local vault = H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    H.patch(search(), "_fzf", function()
      return nil
    end)
    H.patch(search(), "_rg", function()
      return "C:/tools/rg.exe"
    end)
    local fake = H.fake_spawn({ code = 0, output = { "daily/2026-11-05.md:12:- 09:12 한글 질의\r\n", "inbox/메모.md:1:한글 질의: 끝\n" } })
    local handle = search().search("한글 질의")
    H.ok(H.wait(function()
      return handle.done
    end, 5000))
    H.eq(handle.code, 0)
    H.eq(#fake.calls, 1)
    local call = fake.calls[1]
    H.eq(call.cmd, {
      "C:/tools/rg.exe",
      "--line-number",
      "--no-heading",
      "--color=never",
      "--smart-case",
      "--fixed-strings",
      "--path-separator",
      "/",
      "-g",
      "*.md",
      "-f",
      "-",
      "daily",
      "inbox",
      "wiki",
      "jira",
    })
    H.eq(call.cwd, vault)
    H.eq(call.stdin, "한글 질의\n")
    H.eq(call.spec.merge, false)
    H.eq(lower_paths(qf()), lower_paths({
      { vault .. "/daily/2026-11-05.md", 12, "- 09:12 한글 질의" },
      { vault .. "/inbox/메모.md", 1, "한글 질의: 끝" },
    }))
    H.eq(#stub.messages, 0)
    -- 종료 1: 결과 없음
    H.fake_spawn({ code = 1 })
    local none = search().search("없는 말")
    H.ok(H.wait(function()
      return none.done
    end, 5000))
    H.eq(stub.messages, { "결과 없음" })
    -- 종료 2: 오류 출력을 버퍼에 보이고 알린다
    H.fake_spawn({ code = 2 })
    local bad = search().search("x")
    H.ok(H.wait(function()
      return bad.done
    end, 5000))
    H.eq(stub.messages[2], "work-kb: 검색 실패 (종료 2)")
    H.eq(#stub.shown, 1)
  end)

  case("rg: 검색 폴더가 하나도 없으면 .", function()
    local vault = H.make_vault("빈 볼트")
    for _, d in ipairs({ "daily", "inbox", "wiki/people", "wiki/projects", "wiki/topics", "wiki", "jira/_views", "jira" }) do
      vim.uv.fs_rmdir(vault .. "/" .. d)
    end
    H.eq(search().search_dirs(vault), { "." })
    local other = H.make_vault()
    vim.uv.fs_rmdir(other .. "/inbox")
    H.eq(search().search_dirs(other), { "daily", "wiki", "jira" })
  end)

  case("실제 rg: 한글 볼트에서 글자 그대로(-F) 찾아 quickfix로", function()
    local rg = vim.fn.exepath("rg")
    if rg == "" then
      H.not_run("rg가 PATH에 없음")
    end
    local vault = H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    H.patch(search(), "_fzf", function()
      return nil
    end)
    H.write_bytes(vault .. "/daily/2026-11-05.md", "## 기록\r\n- 09:12 캐시 (KB-12) 논의\r\n- 09:13 다른 줄\r\n")
    H.write_bytes(vault .. "/wiki/topics/설계 원칙.md", "# 설계\n캐시 (kb-12) 원칙\n")
    H.write_bytes(vault .. "/raw/밖.md", "캐시 (KB-12) 밖\n")
    H.write_bytes(vault .. "/daily/메모.txt", "캐시 (KB-12) txt\n")
    local handle = search().search("캐시 (KB-12)")
    H.ok(H.wait(function()
      return handle.done
    end, 30000), "rg가 끝나지 않음")
    H.eq(handle.code, 0)
    H.eq(lower_paths(qf()), lower_paths({
      { vault .. "/daily/2026-11-05.md", 2, "- 09:12 캐시 (KB-12) 논의" },
    }))
    H.eq(#stub.messages, 0)
    local none = search().search("없는 말 (")
    H.ok(H.wait(function()
      return none.done
    end, 30000))
    H.eq(none.code, 1)
    H.eq(stub.messages, { "결과 없음" })
  end)

  case("fzf-lua가 있으면 live_grep(cwd = 볼트, search = 질의)", function()
    local vault = H.with_vault({ python = H.python() })
    H.ui_stub()
    local got
    H.patch(search(), "_fzf", function()
      return {
        live_grep = function(opts)
          got = opts
          return "fzf"
        end,
      }
    end)
    local fake = H.fake_spawn(function()
      error("프로세스를 띄우지 않음")
    end)
    H.eq(search().search(" 질의 "), "fzf")
    H.eq(got, { cwd = vault, search = "질의" })
    H.eq(#fake.calls, 0)
  end)

  case("프롬프트: 검색: (기본값 = 커서 아래 낱말), 비면 사용자 오류, 취소면 아무것도 안 함", function()
    H.with_vault({ python = H.python() })
    H.ui_stub()
    no_fzf_no_rg()
    local fake = H.fake_spawn({ code = 0, output = "결과 없음\n" })
    local ui = require("work-kb.ui")
    local asked = {}
    local answers = { "", "   ", nil }
    local n = 0
    H.patch(ui, "input", function(prompt, opts)
      n = n + 1
      table.insert(asked, { prompt, opts and opts.default })
      local answer = answers[n]
      if answer == "" then
        return opts.default -- 비운 채 Enter → 기본값
      end
      return answer
    end)
    local buf = vim.api.nvim_create_buf(true, true)
    vim.api.nvim_win_set_buf(0, buf)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "회의 캐시동기화 논의" })
    vim.api.nvim_win_set_cursor(0, { 1, 7 })
    proc().wait(search().search(), 5000)
    H.eq(asked[1], { "검색: ", "캐시동기화" })
    H.eq(fake.calls[1].argv, { "search", "--root", require("work-kb.config").vault(), "캐시동기화" })
    H.fails_with(function()
      search().search()
    end, "검색어가 비었습니다")
    H.eq(search().search(), nil)
    H.eq(#fake.calls, 1)
  end)
end)
