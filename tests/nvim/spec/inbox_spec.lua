-- i inbox 메모 (ERT inbox-filename, inbox-write) + 제목 프롬프트·본문 버퍼·미리 채우기.
local H = require("helpers")

local function capture()
  return require("work-kb.capture")
end

local function wipe_capture_buffers()
  for _, name in ipairs({ "*work-kb-inbox*", "*work-kb-capture*" }) do
    local b = require("work-kb.proc").get_buffer(name)
    if b then
      pcall(vim.api.nvim_buf_delete, b, { force = true })
    end
  end
end

describe("inbox", function()
  it("inbox-filename: 파일 이름과 제목 규칙", function()
    H.configure()
    local c = capture()
    H.eq(c.inbox_file_name("회의: 결과?"), "20261105-0912-회의 결과.md")
    H.eq(c.inbox_title("", "\n# 첫 줄 제목\n본문"), "첫 줄 제목")
    H.eq(c.inbox_title(nil, ""), "메모")
    H.eq(c.inbox_title("  ", string.rep("가", 50)), string.rep("가", 40))
    H.eq(c.inbox_title("직접 제목", "본문"), "직접 제목")
    -- 목록 표시·제목 표시는 한 번만 벗긴다, 40글자로 자른 뒤 끝 공백 제거
    H.eq(c.inbox_title("", "  - 할 일 목록\n"), "할 일 목록")
    H.eq(c.inbox_title("", "* 별표"), "별표")
    H.eq(c.inbox_title("", "## - 둘 다"), "- 둘 다")
    H.eq(c.inbox_title("", "#붙은 해시"), "#붙은 해시")
    H.eq(c.inbox_title("", "  \r\n\t\n#  \n둘째"), "메모")
    H.eq(c.inbox_title("", string.rep("가", 39) .. " 나"), string.rep("가", 39))
    H.eq(c.yaml_quote('a "b" \\c\r\n\td'), '"a \\"b\\" \\\\c d"')
  end)

  it("inbox-write: UTF-8·LF, 따옴표 이스케이프, 겹치면 -2", function()
    local vault = H.with_vault()
    local c = capture()
    local first = c.write_inbox("", '아이디어: "따옴표" 메모\n둘째 줄')
    local second = c.write_inbox("", '아이디어: "따옴표" 메모')
    H.eq(first, vault .. "/inbox/20261105-0912-아이디어 따옴표 메모.md")
    H.eq(second, vault .. "/inbox/20261105-0912-아이디어 따옴표 메모-2.md")
    local offset = require("work-kb.time").offset(H.TIME)
    H.eq(
      H.read_bytes(first),
      "---\ncreated: 2026-11-05T09:12:00"
        .. offset
        .. '\ntitle: "아이디어: \\"따옴표\\" 메모"\n---\n아이디어: "따옴표" 메모\n둘째 줄\n'
    )
    H.not_contains(H.read_bytes(first), "\r")
    H.ok(offset:match("^[+-]%d%d:%d%d$"), offset)
    -- 본문이 비면 머리말만
    local third = c.write_inbox("제목만", "  \n ")
    H.eq(H.read_bytes(third), "---\ncreated: 2026-11-05T09:12:00" .. offset .. '\ntitle: "제목만"\n---\n')
  end)

  it("i: 제목을 묻고 본문 버퍼를 열어 C-c C-c로 저장한다", function()
    local vault = H.with_vault()
    local ok, err = pcall(function()
      local stub = H.ui_stub({ { "input", "  주간 회의 " } })
      local win = vim.api.nvim_get_current_win()
      local buf = capture().inbox()
      H.eq(stub.prompts, { { kind = "input", prompt = "제목 (비우면 본문 첫 줄): " } })
      H.eq(vim.api.nvim_get_current_buf(), buf)
      H.eq(require("work-kb.proc").buffer_name(buf), "*work-kb-inbox*")
      H.eq(vim.wo.winbar, "저장: C-c C-c  취소: C-c C-k")
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "", "본문 첫 줄", "둘째 줄", "" })
      vim.fn.maparg("<C-c><C-c>", "n", false, true).callback()
      H.ok(not vim.api.nvim_buf_is_valid(buf))
      H.eq(vim.api.nvim_get_current_win(), win)
      local path = vault .. "/inbox/20261105-0912-주간 회의.md"
      H.contains(H.read_bytes(path), '\ntitle: "주간 회의"\n---\n본문 첫 줄\n둘째 줄\n')
      H.eq(stub.messages, { "work-kb: inbox에 저장했습니다 — 20261105-0912-주간 회의.md" })
    end)
    wipe_capture_buffers()
    if not ok then
      error(err, 0)
    end
  end)

  it("i: 미리 채우기(비주얼 선택), 빈 본문은 오류, 제목을 비우면 본문 첫 줄, 제목 취소", function()
    local vault = H.with_vault()
    local ok, err = pcall(function()
      local stub = H.ui_stub({ { "input", "" }, { "input", nil } })
      local buf = capture().inbox("# 선택한 제목\n선택한 본문")
      H.eq(vim.api.nvim_buf_get_lines(buf, 0, -1, false), { "# 선택한 제목", "선택한 본문" })
      capture().finish()
      local path = vault .. "/inbox/20261105-0912-선택한 제목.md"
      H.contains(H.read_bytes(path), '\ntitle: "선택한 제목"\n---\n# 선택한 제목\n선택한 본문\n')
      -- 제목을 묻다 취소: 버퍼를 열지 않는다
      H.eq(capture().inbox(), nil)
      H.eq(require("work-kb.proc").get_buffer("*work-kb-inbox*"), nil)
      H.eq(stub.messages[#stub.messages], "work-kb: 취소했습니다")
      -- 제목이 있어도 본문이 비면 쓰지 않는다 (Emacs: 빈 내용 오류)
      stub = H.ui_stub({ { "input", "제목" } })
      buf = capture().inbox()
      H.fails_with(function()
        capture().finish()
      end, "빈 내용입니다 (취소: C-c C-k)")
      capture().cancel()
      local names = {}
      for name in vim.fs.dir(vault .. "/inbox") do
        table.insert(names, name)
      end
      H.eq(names, { "20261105-0912-선택한 제목.md" })
    end)
    wipe_capture_buffers()
    if not ok then
      error(err, 0)
    end
  end)
end)
