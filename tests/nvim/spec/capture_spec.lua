-- c 캡처 (ERT 캡처 10개: capture-format, -appends-to-record-end, -crlf-file, -no-record-heading,
-- -live-buffer, -stale-live-buffer, -refuses-non-utf-8, -bom-daily, -missing-today, -buffer-finish)
-- + EOL이 섞인 파일, 수정된 열린 버퍼, 캡처 버퍼 취소·미리 채우기·실제 키.
local H = require("helpers")

local DAILY_LF = "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
  .. "## 기록\n- 08:00 첫 기록\n\n## 메모\n자유 텍스트\n"

local SKELETON = "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
  .. "## 이월\n<!-- kb:carryover:start -->\n<!-- kb:carryover:end -->\n\n## 기록\n"

local function capture()
  return require("work-kb.capture")
end

local function daily_file(vault)
  return vault .. "/daily/2026-11-05.md"
end

local bump = 0
---다른 프로세스(link·daily_prepare)가 고친 것처럼 다시 쓰고 수정 시각을 미룬다.
local function rewrite_on_disk(file, text)
  H.write_bytes(file, text)
  -- Windows의 Neovim은 나노초가 같으면 1초 차이를 바뀐 것으로 보지 않는다: 몇 초씩 벌린다.
  bump = bump + 5
  local t = os.time() + 10 + bump
  assert(vim.uv.fs_utime(file, t, t))
end

---숨은 채로 읽어 들인 버퍼 (Emacs find-file-noselect). ENC를 주면 ++enc로 읽는다.
local function visit(file, enc)
  if enc then
    local before = vim.api.nvim_get_current_buf()
    vim.cmd("edit ++enc=" .. enc .. " " .. vim.fn.fnameescape(file))
    local buf = vim.api.nvim_get_current_buf()
    if before ~= buf and vim.api.nvim_buf_is_valid(before) then
      vim.api.nvim_set_current_buf(before)
    end
    return buf
  end
  local buf = vim.fn.bufadd(file)
  vim.fn.bufload(buf)
  return buf
end

local function lines_text(buf)
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end

local function find_buffer(file)
  return require("work-kb.fileio").find_buffer(file)
end

---볼트 아래 파일을 연 버퍼를 모두 지운다 (Emacs work-kb-test--kill-buffers-under).
local function wipe_under(dir)
  local prefix = vim.fs.normalize(dir):lower() .. "/"
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    local name = vim.fs.normalize(vim.api.nvim_buf_get_name(b)):lower()
    if name:sub(1, #prefix) == prefix then
      pcall(vim.api.nvim_buf_delete, b, { force = true })
    end
  end
end

---VAULT를 만들고 BODY(vault)를 돌린 뒤 버퍼를 지운다.
local function with_vault(opts, body)
  local vault = H.with_vault(opts)
  local ok, err = pcall(body, vault)
  wipe_under(vault)
  if not ok then
    error(err, 0)
  end
end

---kb.py를 부르면 안 되는 자리: 불리면 기록만 남긴다 (ensure-today는 오류를 삼키므로 개수로 본다).
local function forbid_spawn()
  return H.fake_spawn(function()
    error("호출되면 안 됨")
  end)
end

describe("capture", function()
  it("capture-format: 시각·체크박스·여러 줄·빈 줄", function()
    H.configure()
    H.ui_stub()
    local fmt = capture().format
    H.eq(fmt("회의 [[KB-12]] @김철수"), "- 09:12 회의 [[KB-12]] @김철수")
    H.eq(fmt("  앞뒤 공백  \n"), "- 09:12 앞뒤 공백")
    H.eq(fmt("[ ] 경비 정산"), "- [ ] 09:12 경비 정산")
    H.eq(fmt("첫 줄\n둘째 줄\r\n\n셋째"), "- 09:12 첫 줄\n  둘째 줄\n  셋째")
    H.eq(fmt("[ ] 할 일\n세부"), "- [ ] 09:12 할 일\n  세부")
    H.eq(fmt("[ ]    띄움\n    들여 쓴 줄   "), "- [ ] 09:12 띄움\n      들여 쓴 줄")
    local err = H.fails_with(function()
      fmt("  \n ")
    end, "빈 내용은 기록하지 않습니다")
    H.ok(require("work-kb.ui").is_user_error(err))
  end)

  it("capture-appends-to-record-end: 마지막 내용 줄 다음, 나머지 바이트 그대로, 버퍼 없음", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, DAILY_LF)
      local stub = H.ui_stub()
      local fake = forbid_spawn()
      H.eq(capture().capture("회의 KB-12 한글 😀"), "- 09:12 회의 KB-12 한글 😀")
      capture().capture("[ ] 회의실 예약")
      H.eq(#fake.calls, 0, "오늘 파일이 있으면 kb.py today를 부르지 않는다")
      H.eq(
        H.read_bytes(file),
        "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
          .. "## 기록\n- 08:00 첫 기록\n- 09:12 회의 KB-12 한글 😀\n- [ ] 09:12 회의실 예약\n\n"
          .. "## 메모\n자유 텍스트\n"
      )
      H.eq(find_buffer(file), nil, "파일을 버퍼로 열지 않는다")
      H.eq(stub.messages, {
        "work-kb: 기록했습니다 — - 09:12 회의 KB-12 한글 😀",
        "work-kb: 기록했습니다 — - [ ] 09:12 회의실 예약",
      })
    end)
  end)

  it("capture-crlf-file: 모두 CRLF인 파일은 CRLF로", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, (DAILY_LF:gsub("\n", "\r\n")))
      H.ui_stub()
      capture().capture("여러 줄\n둘째")
      local expected = "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
        .. "## 기록\n- 08:00 첫 기록\n- 09:12 여러 줄\n  둘째\n\n## 메모\n자유 텍스트\n"
      H.eq(H.read_bytes(file), (expected:gsub("\n", "\r\n")))
    end)
  end)

  it("EOL이 섞인 파일: 앞 줄의 EOL을 따른다, 끝 줄바꿈 없는 파일은 그대로 없음", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.ui_stub()
      H.write_bytes(file, "## 기록\r\n- a\r\n\n## 메모\nx\n")
      capture().capture("b\n둘째")
      H.eq(H.read_bytes(file), "## 기록\r\n- a\r\n- 09:12 b\r\n  둘째\r\n\n## 메모\nx\n")
      H.write_bytes(file, "## 기록\n- a\r\n- c\n")
      capture().capture("d")
      H.eq(H.read_bytes(file), "## 기록\n- a\r\n- c\n- 09:12 d\n")
      H.write_bytes(file, "## 기록\n- a")
      capture().capture("b")
      H.eq(H.read_bytes(file), "## 기록\n- a\n- 09:12 b")
      H.write_bytes(file, "## 기록\r\n- a")
      capture().capture("b")
      H.eq(H.read_bytes(file), "## 기록\r\n- a\r\n- 09:12 b")
      -- 내용 없는 절, 다음 절은 `## ` 2단계만 (### 는 절 안)
      H.write_bytes(file, "## 기록   \n\n\n### 소제목\n본문\n\n## 다음\n")
      capture().capture("e")
      H.eq(H.read_bytes(file), "## 기록   \n\n\n### 소제목\n본문\n- 09:12 e\n\n## 다음\n")
      H.write_bytes(file, "## 기록\n\n\n## 다음\n")
      capture().capture("f")
      H.eq(H.read_bytes(file), "## 기록\n- 09:12 f\n\n\n## 다음\n")
      -- `## 기록 메모`나 `##기록`은 제목이 아니다
      H.write_bytes(file, "## 기록 메모\n##기록\n")
      capture().capture("g")
      H.eq(H.read_bytes(file), "## 기록 메모\n##기록\n\n## 기록\n- 09:12 g\n")
    end)
  end)

  it("capture-no-record-heading: 파일 끝에 `## 기록`을 만든다", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, "사람이 쓴 글")
      H.ui_stub()
      capture().capture("메모")
      H.eq(H.read_bytes(file), "사람이 쓴 글\n\n## 기록\n- 09:12 메모\n")
      H.write_bytes(file, "")
      capture().capture("빈 파일")
      H.eq(H.read_bytes(file), "## 기록\n- 09:12 빈 파일\n")
      H.write_bytes(file, "a\r\nb\r\n")
      capture().capture("crlf")
      H.eq(H.read_bytes(file), "a\r\nb\r\n\r\n## 기록\r\n- 09:12 crlf\r\n")
    end)
  end)

  it("capture-live-buffer: 열린(수정 없는) 버퍼는 묻지 않고 다시 읽는다", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, DAILY_LF)
      local buf = visit(file)
      H.ui_stub()
      capture().capture("열린 버퍼")
      H.ok(vim.api.nvim_buf_is_valid(buf))
      H.eq(vim.bo[buf].modified, false)
      H.contains(lines_text(buf), "- 08:00 첫 기록\n- 09:12 열린 버퍼\n")
      H.contains(H.read_bytes(file), "- 09:12 열린 버퍼\n\n## 메모")
    end)
  end)

  it("capture-stale-live-buffer: 낡은 버퍼는 다시 읽고 기록, 버퍼도 수정됐으면 거부", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      local disk = DAILY_LF:gsub("%- 08:00 첫 기록\n", "- 08:00 첫 기록 [[KB-12]]\n")
      H.write_bytes(file, DAILY_LF)
      local buf = visit(file)
      rewrite_on_disk(file, disk)
      H.ui_stub()
      capture().capture("새 캡처")
      H.eq(H.read_bytes(file), (disk:gsub("%[%[KB%-12%]%]\n", "[[KB-12]]\n- 09:12 새 캡처\n")))
      H.eq(vim.bo[buf].modified, false)
      H.contains(lines_text(buf), "[[KB-12]]\n- 09:12 새 캡처")
      -- 버퍼도 수정됨 + 디스크 변경: 거부, 디스크 바이트 그대로
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "사람이 쓰는 중" })
      local disk2 = H.read_bytes(file) .. "외부 변경\n"
      rewrite_on_disk(file, disk2)
      local err = H.fails_with(function()
        capture().capture("거부될 캡처")
      end, "2026-11-05.md: 버퍼와 파일이 모두 바뀌었습니다. 먼저 정리하세요")
      H.ok(require("work-kb.ui").is_user_error(err))
      H.eq(H.read_bytes(file), disk2)
      H.eq(vim.bo[buf].modified, true)
      H.contains(lines_text(buf), "사람이 쓰는 중")
      H.not_contains(lines_text(buf), "거부될 캡처")
      -- 두 번째 시도도 거부 (:checktime이 시각을 기억한 뒤에도)
      H.fails_with(function()
        capture().capture("또 거부")
      end, "2026-11-05.md: 버퍼와 파일이 모두 바뀌었습니다. 먼저 정리하세요")
      H.eq(H.read_bytes(file), disk2)
      -- 사람이 다시 읽으면 (버퍼 수정 버림) 다시 기록된다
      vim.api.nvim_buf_call(buf, function()
        vim.cmd("silent edit!")
      end)
      capture().capture("정리 뒤")
      H.contains(H.read_bytes(file), "- 09:12 정리 뒤\n")
      H.eq(vim.bo[buf].modified, false)
    end)
  end)

  it("수정된 열린 버퍼 (디스크는 그대로): 버퍼에 넣고 사람이 쓰던 글과 함께 저장", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, DAILY_LF)
      local buf = visit(file)
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "사람이 쓰는 중" })
      H.ui_stub()
      capture().capture("버퍼로\n둘째")
      H.eq(vim.bo[buf].modified, false)
      H.eq(
        H.read_bytes(file),
        "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
          .. "## 기록\n- 08:00 첫 기록\n- 09:12 버퍼로\n  둘째\n\n## 메모\n자유 텍스트\n사람이 쓰는 중\n"
      )
      -- EOL이 섞인 파일 (Neovim은 unix로 읽고 CR을 줄에 남긴다) + 끝 줄바꿈 없음
      wipe_under(vault)
      H.write_bytes(file, "## 기록\r\n- a\r\n\n## 메모\nx")
      buf = visit(file)
      H.eq(vim.bo[buf].fileformat, "unix")
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "y" })
      capture().capture("b")
      H.eq(H.read_bytes(file), "## 기록\r\n- a\r\n- 09:12 b\r\n\n## 메모\nx\ny")
      -- 절이 없는 수정된 버퍼: 끝에 만들고 끝 줄바꿈을 붙인다
      wipe_under(vault)
      H.write_bytes(file, "사람이 쓴 글")
      buf = visit(file)
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "사람이 쓴 글!" })
      capture().capture("메모")
      H.eq(H.read_bytes(file), "사람이 쓴 글!\n\n## 기록\n- 09:12 메모\n")
    end)
  end)

  it("capture-refuses-non-utf-8: cp949 daily에는 쓰지 않고 파일 이름을 담은 사용자 오류", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      local cp949 = assert(vim.iconv("## 기록\n- 09:00 한글 cp949\n", "utf-8", "cp949"))
      H.ok(not require("work-kb.fileio").is_utf8(cp949))
      H.write_bytes(file, cp949)
      H.ui_stub()
      local message = string.format("UTF-8이 아닙니다: %s (UTF-8로 바꿔 저장한 뒤 다시 캡처하세요)", file)
      -- 열린 버퍼 없음
      local err = H.fails_with(function()
        capture().capture("새 줄")
      end, message)
      H.ok(require("work-kb.ui").is_user_error(err))
      H.contains(err, "2026-11-05.md")
      H.eq(H.read_bytes(file), cp949)
      H.eq(find_buffer(file), nil)
      -- cp949로 열린 버퍼
      local buf = visit(file, "cp949")
      H.eq(vim.bo[buf].fileencoding, "cp949")
      H.fails_with(function()
        capture().capture("라이브")
      end, message)
      H.eq(H.read_bytes(file), cp949)
      H.eq(vim.bo[buf].modified, false)
      H.eq(vim.bo[buf].fileencoding, "cp949")
      H.contains(lines_text(buf), "한글 cp949")
      H.not_contains(lines_text(buf), "라이브")
      -- 수정된 cp949 버퍼도 거부
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "고치는 중" })
      H.fails_with(function()
        capture().capture("라이브 수정")
      end, message)
      H.eq(H.read_bytes(file), cp949)
      H.eq(vim.bo[buf].modified, true)
      vim.api.nvim_buf_delete(buf, { force = true })
      -- UTF-8로 열려 날 바이트가 깨져 보이는 버퍼
      buf = visit(file, "utf-8")
      H.fails_with(function()
        capture().capture("라이브 2")
      end, message)
      H.eq(H.read_bytes(file), cp949)
      H.eq(vim.bo[buf].modified, false)
    end)
  end)

  it("capture-bom-daily: BOM 뒤 `## 기록`도 제목, BOM은 그대로", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, "\239\187\191## 기록\n- 09:00 첫 줄\n")
      H.ui_stub()
      capture().capture("BOM 캡처")
      H.eq(H.read_bytes(file), "\239\187\191## 기록\n- 09:00 첫 줄\n- 09:12 BOM 캡처\n")
      -- 열린 버퍼(BOM은 'bomb'으로)도 같은 결과
      local buf = visit(file)
      H.eq(vim.bo[buf].bomb, true)
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "수정" })
      capture().capture("두 번째")
      H.eq(H.read_bytes(file), "\239\187\191## 기록\n- 09:00 첫 줄\n- 09:12 BOM 캡처\n수정\n- 09:12 두 번째\n")
    end)
  end)

  it("capture-missing-today: kb.py today --date를 부르고 실패하면 뼈대를 직접 만든다", function()
    with_vault({ python = H.python() }, function(vault)
      local file = daily_file(vault)
      local stub = H.ui_stub()
      local fake = H.fake_spawn({ code = 1, output = "오류\n" })
      capture().capture("첫 캡처")
      H.eq(#fake.calls, 1)
      H.eq(fake.calls[1].argv, { "today", "--root", vault, "--date", "2026-11-05" })
      H.eq(H.read_bytes(file), SKELETON .. "- 09:12 첫 캡처\n")
      H.eq(stub.messages, {
        "work-kb: daily_prepare 실패 (종료 1). *work-kb* 버퍼를 보세요",
        "work-kb: 기록했습니다 — - 09:12 첫 캡처",
      })
    end)
  end)

  it("capture-buffer-finish: 캡처 버퍼에 쓰고 C-c C-c로 저장, 버퍼는 닫히고 창은 돌아온다", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, DAILY_LF)
      local stub = H.ui_stub()
      local win = vim.api.nvim_get_current_win()
      local wins = #vim.api.nvim_tabpage_list_wins(0)
      local buf = capture().capture()
      H.eq(vim.api.nvim_get_current_buf(), buf)
      H.eq(require("work-kb.proc").buffer_name(buf), "*work-kb-capture*")
      H.eq(vim.wo.winbar, "저장: C-c C-c  취소: C-c C-k")
      for _, mode in ipairs({ "n", "i" }) do
        local m = vim.fn.maparg("<C-c><C-c>", mode, false, true)
        H.ok(m.callback and m.buffer == 1, "C-c C-c 맵 " .. mode)
        local k = vim.fn.maparg("<C-c><C-k>", mode, false, true)
        H.ok(k.callback and k.buffer == 1, "C-c C-k 맵 " .. mode)
      end
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "버퍼에서 쓴 글" })
      vim.fn.maparg("<C-c><C-c>", "n", false, true).callback()
      H.ok(not vim.api.nvim_buf_is_valid(buf), "캡처 버퍼가 남음")
      H.eq(require("work-kb.proc").get_buffer("*work-kb-capture*"), nil)
      H.eq(vim.api.nvim_get_current_win(), win)
      H.eq(#vim.api.nvim_tabpage_list_wins(0), wins)
      H.contains(H.read_bytes(file), "- 09:12 버퍼에서 쓴 글\n")
      H.eq(stub.messages, { "work-kb: 기록했습니다 — - 09:12 버퍼에서 쓴 글" })
    end)
  end)

  it("캡처 버퍼: 빈 내용은 오류(버퍼 유지), 취소, 미리 채우기(빈 버퍼에만), 실제 키 입력", function()
    with_vault({}, function(vault)
      local file = daily_file(vault)
      H.write_bytes(file, DAILY_LF)
      local stub = H.ui_stub()
      local echoed = {}
      H.patch(vim.api, "nvim_echo", function(chunks)
        table.insert(echoed, chunks[1][1])
      end)
      local win = vim.api.nvim_get_current_win()
      local buf = capture().capture()
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "  ", "" })
      -- 맵은 사용자 오류를 traceback 없이 보인다
      vim.fn.maparg("<C-c><C-c>", "n", false, true).callback()
      H.eq(echoed, { "빈 내용입니다 (취소: C-c C-k)" })
      H.ok(vim.api.nvim_buf_is_valid(buf))
      H.fails_with(function()
        capture().finish()
      end, "빈 내용입니다 (취소: C-c C-k)")
      capture().cancel()
      H.ok(not vim.api.nvim_buf_is_valid(buf))
      H.eq(vim.api.nvim_get_current_win(), win)
      H.eq(stub.messages, { "work-kb: 취소했습니다" })
      H.eq(H.read_bytes(file), DAILY_LF)
      -- 미리 채우기 (비주얼 선택): 빈 버퍼에만
      buf = capture().open("선택한 글\n둘째 줄")
      H.eq(vim.api.nvim_buf_get_lines(buf, 0, -1, false), { "선택한 글", "둘째 줄" })
      H.eq(capture().open("다른 글"), buf, "같은 버퍼")
      H.eq(vim.api.nvim_buf_get_lines(buf, 0, -1, false), { "선택한 글", "둘째 줄" })
      -- 실제 키 입력 (일반 모드 C-c C-c)
      vim.api.nvim_feedkeys(vim.keycode("<C-c><C-c>"), "x", false)
      H.ok(not vim.api.nvim_buf_is_valid(buf), "C-c C-c 키로 닫히지 않음")
      H.contains(H.read_bytes(file), "- 09:12 선택한 글\n  둘째 줄\n\n## 메모")
      -- 삽입 모드 C-c C-c
      buf = capture().open()
      vim.api.nvim_feedkeys(vim.keycode("i삽입 모드 글<C-c><C-c>"), "x", false)
      H.ok(not vim.api.nvim_buf_is_valid(buf), "삽입 모드 C-c C-c 키로 닫히지 않음")
      H.contains(H.read_bytes(file), "- 09:12 삽입 모드 글\n")
      H.eq(vim.fn.mode(), "n")
    end)
  end)
end)
