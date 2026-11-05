-- o (커서 위치 열기)와 결과 버퍼. ERT "열기·검색" 묶음:
--   thing-at-point, jira-stub-p, open-key-with-cache, open-key-missing-syncs-then-opens,
--   open-key-stub-syncs-and-reverts, open-key-sync-fails-makes-stub, open-key-sync-fails-keeps-stub,
--   open-key-sync-ok-but-missing, open-key-sync-in-flight, open-key-real-async-sentinel,
--   open-person-and-link. (search-falls-back-to-kb-py는 search_spec.lua.)
local H = require("helpers")

local uv = vim.uv

local JIRA_FULL =
  '---\nkey: %s\nproject: KB\nsummary: "캐시 동기화"\nstatus: 진행 중\nstatus_category: indeterminate\n---\n# %s 캐시 동기화\n\n## 노트에서 언급\n<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n'
local JIRA_STUB =
  '---\nkey: %s\nproject: KB\nstub: true\nsummary: ""\nstatus: ""\n---\n# %s\n\n> 아직 동기화되지 않은 일감입니다.\n\n## 노트에서 언급\n<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n'

local function page(template, key)
  return string.format(template, key, key)
end

local function open()
  return require("work-kb.open")
end

local function proc()
  return require("work-kb.proc")
end

---디스크의 파일을 바꾸고 수정 시각을 미래로 (같은 초 안의 쓰기도 낡음으로 보이게).
local function rewrite_on_disk(path, data)
  H.write_bytes(path, data)
  local t = os.time() + 10
  assert(uv.fs_utime(path, t, t))
end

local saved_autoread = nil

---창·버퍼·quickfix를 비우고 바꾼 전역 옵션을 되돌린다 (다음 테스트에 남지 않게).
local function cleanup()
  if saved_autoread ~= nil then
    vim.o.autoread = saved_autoread
    saved_autoread = nil
  end
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

---TEXT 한 줄짜리 새 버퍼를 지금 창에 두고 바이트 열 COL에 커서를 둔다.
local function scratch(text, col)
  local buf = vim.api.nvim_create_buf(true, true)
  vim.api.nvim_win_set_buf(0, buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { text })
  vim.api.nvim_win_set_cursor(0, { 1, col or 0 })
  return buf
end

---Emacs (search-forward NEEDLE): 커서를 NEEDLE 바로 뒤에.
local function col_after(text, needle)
  local _, e = text:find(needle, 1, true)
  assert(e, "없음: " .. needle)
  return e
end

---Emacs (search-forward NEEDLE) (backward-char 1): NEEDLE의 마지막 글자 위.
local function col_last_char(text, needle)
  local col = col_after(text, needle) - 1
  while col > 0 do
    local b = text:byte(col + 1)
    if b < 0x80 or b > 0xBF then
      break
    end
    col = col - 1
  end
  return col
end

---TEXT의 NEEDLE 바로 뒤에 커서를 두고 o. (돌려준 값, 지금 창의 버퍼).
local function open_key_at(text, needle)
  scratch(text, col_after(text, needle))
  local result = open().open_at_point()
  return result, vim.api.nvim_get_current_buf()
end

local function buf_file(buf)
  return vim.fs.normalize(vim.api.nvim_buf_get_name(buf))
end

local function same_file(a, b)
  return vim.fs.normalize(a):lower() == vim.fs.normalize(b):lower()
end

local function buf_text(buf)
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end

local function some(list, pred)
  for _, v in ipairs(list) do
    if pred(v) then
      return true
    end
  end
  return false
end

local function argvs(fake)
  local out = {}
  for _, call in ipairs(fake.calls) do
    table.insert(out, call.argv)
  end
  return out
end

describe("open", function()
  case("thing-at-point: 링크·키·@이름, 경계·메일 제외 (test:750 줄)", function()
    local line = "회의 KB-12 와 [[OPS-3|배포]] 그리고 @김철수, [[설계 원칙#요약]] mail a@b.com xKB-1"
    local function at(needle)
      local t = open().thing_at_point(line, col_last_char(line, needle))
      return t and { t.kind, t.value } or nil
    end
    H.eq(at("KB-12"), { "key", "KB-12" })
    H.eq(at("OPS-"), { "key", "OPS-3" })
    H.eq(at("김철"), { "person", "김철수" })
    H.eq(at("설계"), { "link", "설계 원칙" })
    H.eq(at("회의"), nil)
    H.eq(at("a@b"), nil)
    H.eq(at("xKB-1"), nil)
    -- 커서 위치 기본값 (현재 창): 키 바로 뒤도 그 키 (Emacs 포인트 규칙)
    scratch(line, col_after(line, "KB-12"))
    local t = open().thing_at_point()
    H.eq({ t.kind, t.value, t[1], t[2] }, { "key", "KB-12", "key", "KB-12" })
    -- 키 경계: 문서 이름 KB-12-설계는 키가 아니다, 한글이 붙은 키는 키다
    H.eq(open().thing_at_point("KB-12-설계 참고", 1), nil)
    H.eq(open().thing_at_point("회의KB-12에서", 7).value, "KB-12")
    H.eq(open().thing_at_point("[[KB-12-설계]]", 4).kind, "link")
    H.eq(open().thing_at_point("[[jira/KB-12]]", 4).value, "jira/KB-12")
    H.eq(open().thing_at_point("@이름_2 끝", 1).value, "이름_2")
    -- link_key / key_on_line (jira.lua가 쓴다)
    H.eq(open().link_key("jira/KB-12"), "KB-12")
    H.eq(open().link_key(" JIRA/KB-12.MD "), "KB-12")
    H.eq(open().link_key("KB-12.md"), "KB-12")
    H.eq(open().link_key("KB-12-설계"), nil)
    H.eq(open().link_key("projects/KB"), nil)
    H.eq(open().link_key("kb-12"), nil)
    H.eq(open().key_on_line("xKB-1 그리고 OPS-3, KB-12"), "OPS-3")
    H.eq(open().key_on_line("키 없음"), nil)
  end)

  case("jira-stub-p: front matter의 stub: true만 (BOM·CRLF·대소문자, 본문 무시)", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/KB-1.md"
    local cases = {
      { page(JIRA_STUB, "KB-1"), true },
      { (page(JIRA_STUB, "KB-1"):gsub("\n", "\r\n")), true },
      { "\239\187\191" .. page(JIRA_STUB, "KB-1"), true },
      { "---\nkey: KB-1\nstub: True  # link\n---\n", true },
      { "---\nstub: 'true'\n---\n", true },
      { page(JIRA_FULL, "KB-1"), false },
      { "---\nkey: KB-1\nstub: false\n---\n", false },
      { "---\nkey: KB-1\nstubborn: true\n---\n", false },
      { "---\nkey: KB-1\n---\nstub: true\n", false },
      { "# 제목만\nstub: true\n", false },
      { "", false },
    }
    for i, c in ipairs(cases) do
      H.write_bytes(file, c[1])
      H.eq(open().is_stub(file), c[2], "경우 " .. i .. ": " .. vim.inspect(c[1]))
    end
  end)

  case("open-key-with-cache: 동기화된 페이지가 있으면 sync 없이 바로 연다", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/KB-12.md"
    H.write_bytes(file, page(JIRA_FULL, "KB-12"))
    H.ui_stub()
    local fake = H.fake_spawn(function()
      error("캐시가 있으면 호출 안 함")
    end)
    local result, buf = open_key_at("회의 KB-12 논의", "KB-1")
    H.ok(same_file(buf_file(buf), file), buf_file(buf))
    H.eq(result, buf)
    H.eq(#fake.calls, 0)
  end)

  case("open-key-missing-syncs-then-opens: sync --root V --key KEY를 자기 버퍼로, 성공하면 연다", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/KB-99.md"
    local stub = H.ui_stub()
    H.patch(open(), "_syncing", {})
    local seen_buffer
    local fake = H.fake_spawn(function()
      seen_buffer = proc().get_buffer("*work-kb-sync KB-99*", false) ~= nil
      H.write_bytes(file, page(JIRA_FULL, "KB-99"))
      return "", 0
    end)
    local handle = open_key_at("링크 [[KB-99]]", "KB-9")
    H.eq(type(handle), "table")
    H.eq(proc().wait(handle, 5000), 0)
    local buf = vim.api.nvim_get_current_buf()
    H.ok(same_file(buf_file(buf), file), buf_file(buf))
    H.contains(buf_text(buf), "캐시 동기화")
    H.eq(stub.messages, { "work-kb: KB-99 동기화 중… (끝나면 엽니다)", "work-kb: KB-99 동기화했습니다" })
    H.eq(argvs(fake), { { "sync", "--root", vault, "--key", "KB-99" } })
    H.ok(seen_buffer, "*work-kb-sync KB-99* 버퍼에서 돌아야 함")
    H.eq(proc().get_buffer("*work-kb-sync KB-99*", false), nil, "성공하면 sync 버퍼를 지운다")
    H.eq(open()._syncing, {})
  end)

  case("open-key-stub-syncs-and-reverts: 열린 stub 버퍼를 묻지 않고 다시 읽어 보인다", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/KB-12.md"
    H.write_bytes(file, page(JIRA_STUB, "KB-12"))
    -- 'autoread'가 꺼져 있어도 묻지 않고 다시 읽어야 한다 (켜져 있으면 버퍼에 들어갈 때 Neovim이 읽는다).
    saved_autoread = vim.o.autoread
    vim.o.autoread = false
    local stub_buf = vim.fn.bufadd(file)
    vim.fn.bufload(stub_buf)
    H.contains(buf_text(stub_buf), "stub: true")
    H.ui_stub() -- 묻는 것이 있으면 오류
    H.patch(open(), "_syncing", {})
    local fake = H.fake_spawn(function()
      rewrite_on_disk(file, page(JIRA_FULL, "KB-12"))
      return "", 0
    end)
    local handle = open_key_at("회의 KB-12", "KB-1")
    H.eq(proc().wait(handle, 5000), 0)
    H.eq(vim.api.nvim_get_current_buf(), stub_buf)
    H.eq(vim.bo[stub_buf].modified, false)
    H.contains(buf_text(stub_buf), "status_category: indeterminate")
    H.not_contains(buf_text(stub_buf), "stub: true")
    H.eq(argvs(fake), { { "sync", "--root", vault, "--key", "KB-12" } })
    H.eq(vim.bo[stub_buf].autoread, nil, "버퍼의 autoread 지역 값은 그대로 (없음)")
    H.eq(vim.go.autoread, false, "전역 autoread도 그대로")
  end)

  case("open-key-sync-fails-makes-stub: 네트워크 3·캐시 없음 → link --stub KEY로 만들어 연다", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/KB-99.md"
    local stub = H.ui_stub()
    H.patch(open(), "_syncing", {})
    local fake = H.fake_spawn(function(call)
      if call.argv[1] == "sync" then
        return "jira_sync: 연결 실패 (주소.invalid)\n", 3
      elseif call.argv[1] == "link" then
        H.write_bytes(file, page(JIRA_STUB, "KB-99"))
        return "", 0
      end
      error("다른 명령: " .. vim.inspect(call.argv))
    end)
    open_key_at("KB-99 확인", "KB-9")
    H.ok(H.wait(function()
      return #stub.messages >= 2
    end, 5000), "끝 알림이 없음")
    local buf = vim.api.nvim_get_current_buf()
    H.ok(same_file(buf_file(buf), file), buf_file(buf))
    H.eq(
      stub.messages[2],
      "work-kb: KB-99 동기화 실패 (종료 3: 네트워크 — jira_sync: 연결 실패 (주소.invalid)). stub을 만들어 엽니다. *work-kb-sync KB-99* 버퍼를 보세요"
    )
    H.eq(argvs(fake), {
      { "sync", "--root", vault, "--key", "KB-99" },
      { "link", "--root", vault, "--stub", "KB-99" },
    })
    H.ok(proc().get_buffer("*work-kb-sync KB-99*", false), "실패하면 sync 버퍼를 남긴다")
    H.eq(open()._syncing, {})
  end)

  case("open-key-sync-fails-keeps-stub: stub이 있고 sync가 실패(설정 4)하면 link 없이 stub을 연다", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/OPS-3.md"
    local bytes = page(JIRA_STUB, "OPS-3")
    H.write_bytes(file, bytes)
    local stub = H.ui_stub()
    H.patch(open(), "_syncing", {})
    local fake = H.fake_spawn({ code = 4 })
    local handle = open_key_at("배포 [[OPS-3]]", "OPS")
    H.eq(proc().wait(handle, 5000), 4)
    local buf = vim.api.nvim_get_current_buf()
    H.ok(same_file(buf_file(buf), file), buf_file(buf))
    H.ok(some(stub.messages, function(m)
      return m:find("OPS-3 동기화 실패", 1, true) and m:find("종료 4: 설정", 1, true)
    end), vim.inspect(stub.messages))
    H.eq(
      stub.messages[2],
      "work-kb: OPS-3 동기화 실패 (종료 4: 설정). 저장된 페이지를 엽니다. *work-kb-sync OPS-3* 버퍼를 보세요"
    )
    H.eq(#fake.calls, 1)
    H.eq(fake.calls[1].argv[1], "sync")
    H.eq(H.read_bytes(file), bytes)
  end)

  case("open-key-sync-ok-but-missing: 0으로 끝났는데 페이지가 없으면 stub으로", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/KB-404.md"
    local stub = H.ui_stub()
    H.patch(open(), "_syncing", {})
    local fake = H.fake_spawn(function(call)
      if call.argv[1] == "link" then
        H.write_bytes(file, page(JIRA_STUB, "KB-404"))
      end
      return "", 0
    end)
    open_key_at("KB-404", "KB-4")
    H.ok(H.wait(function()
      return #stub.messages >= 2
    end, 5000), "끝 알림이 없음")
    H.ok(same_file(buf_file(vim.api.nvim_get_current_buf()), file))
    H.eq(
      stub.messages[2],
      "work-kb: KB-404 동기화 실패 (Jira에서 받아 오지 못함). stub을 만들어 엽니다. *work-kb-sync KB-404* 버퍼를 보세요"
    )
    H.eq({ fake.calls[1].argv[1], fake.calls[2].argv[1] }, { "sync", "link" })
    H.eq(#fake.calls, 2)
  end)

  case("sync 실패에 stub 생성도 실패: 두 버퍼를 보라고 알린다", function()
    local vault = H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    H.patch(open(), "_syncing", {})
    H.fake_spawn(function(call)
      if call.argv[1] == "sync" then
        return "마지막 줄\n", 2
      end
      return "", 6
    end)
    local _, before = open_key_at("KB-5 확인", "KB-")
    H.ok(H.wait(function()
      return #stub.messages >= 2
    end, 5000), "끝 알림이 없음")
    H.eq(
      stub.messages[2],
      "work-kb: KB-5 동기화 실패 (종료 2: 인증 — 마지막 줄), stub 생성도 실패 (종료 6: 잠김). *work-kb-sync KB-5*, *work-kb* 버퍼를 보세요"
    )
    H.eq(vim.api.nvim_get_current_buf(), before, "아무것도 열지 않는다")
    H.ok(not H.exists(vault .. "/jira/KB-5.md"))
  end)

  case("open-key-sync-in-flight: 같은 키가 도는 중이면 다시 돌리지 않는다", function()
    H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    H.patch(open(), "_syncing", {})
    local fake = H.fake_spawn({ code = "hang" })
    local first = open_key_at("KB-7", "KB-")
    H.eq(type(first), "table")
    local second = open_key_at("KB-7", "KB-")
    H.eq(second, nil)
    H.ok(some(stub.messages, function(m)
      return m == "work-kb: KB-7 동기화가 이미 진행 중입니다 (끝나면 열립니다)"
    end), vim.inspect(stub.messages))
    H.eq(#fake.calls, 1)
    H.eq(open()._syncing, { ["KB-7"] = true })
    proc().cancel(first)
  end)

  case("실행이 바로 실패하면 진행 중 표시를 지우고 오류를 그대로 올린다", function()
    H.with_vault({ python = H.python() })
    H.ui_stub()
    H.patch(open(), "_syncing", {})
    H.patch(proc(), "_spawn", function()
      error("work-kb: 실행하지 못했습니다 (x): ENOENT", 0)
    end)
    scratch("KB-8", 0)
    H.fails_with(function()
      open().open_at_point()
    end, "work-kb: 실행하지 못했습니다 (x): ENOENT")
    H.eq(open()._syncing, {})
  end)

  case("open-key-real-async-sentinel: 실제 비동기 프로세스가 끝난 뒤 연다", function()
    local vault = H.with_vault({ python = H.python() })
    if not H.exists(H.python()) then
      H.not_run("Python이 없음: " .. H.python())
    end
    local file = vault .. "/jira/KB-12.md"
    local stub = H.ui_stub()
    H.patch(open(), "_syncing", {})
    local code = [==[import sys, json, pathlib; a = json.loads(sys.stdin.buffer.read().decode('utf-8')); assert a[0] == 'sync'; root = a[a.index('--root') + 1]; key = a[a.index('--key') + 1]; pathlib.Path(root, 'jira', key + '.md').write_bytes(('---\nkey: ' + key + '\nsummary: "비동기 동기화"\nstatus_category: new\n---\n# ' + key + '\n').encode('utf-8'))]==]
    local captured
    H.rewrite_spawn(function(spec)
      captured = spec.cmd
      return { spec.cmd[1], "-c", code }
    end)
    local handle = open_key_at("회의 KB-12", "KB-1")
    H.eq(type(handle), "table")
    H.eq(handle.via, "process")
    H.ok(not H.exists(file), "아직 없어야 함")
    H.eq(proc().wait(handle, 60000), 0, "출력: " .. tostring(proc().buffer_text("*work-kb-sync KB-12*")))
    local buf = vim.api.nvim_get_current_buf()
    H.ok(same_file(buf_file(buf), file), buf_file(buf))
    H.contains(buf_text(buf), "비동기 동기화")
    H.eq(vim.list_slice(captured, #captured - 3), { "-X", "utf8", "scripts/kb.py", "--argv-stdin" })
    H.eq(proc().get_buffer("*work-kb-sync KB-12*", false), nil)
    H.eq(open()._syncing, {})
    H.eq(stub.messages[#stub.messages], "work-kb: KB-12 동기화했습니다")
  end)

  case("open-person-and-link: @이름·[[링크]]를 열고, 없으면 사용자 오류", function()
    local vault = H.with_vault({ python = H.python() })
    local person = vault .. "/wiki/people/김철수.md"
    local topic = vault .. "/wiki/topics/설계 원칙.md"
    local daily = vault .. "/daily/2026-11-04.md"
    local deep = vault .. "/raw/notes/깊은 문서.md"
    local jira = vault .. "/jira/KB-12.md"
    H.write_bytes(person, "# 김철수\n")
    H.write_bytes(topic, "# 설계 원칙\n")
    H.write_bytes(daily, "## 기록\n")
    H.write_bytes(deep, "# 깊은 문서\n")
    H.write_bytes(jira, page(JIRA_STUB, "KB-12"))
    H.ui_stub()
    local fake = H.fake_spawn(function()
      error("링크는 sync하지 않음")
    end)
    for _, c in ipairs({
      { "@김철수", 4, person }, -- (goto-char (+ (point-min) 2)) = 셋째 글자
      { "[[설계 원칙]]", 2, topic },
      { "[[2026-11-04]]", 2, daily },
      { "[[깊은 문서]]", 2, deep }, -- 정해진 폴더에 없으면 raw 등을 재귀로
      { "[[jira/KB-12]]", 2, jira }, -- o에서는 링크 그대로 (sync 없음)
      { "[[KB-12.md]]", 2, jira },
    }) do
      scratch(c[1], c[2])
      open().open_at_point()
      local name = buf_file(vim.api.nvim_get_current_buf())
      H.ok(same_file(name, c[3]), c[1] .. " → " .. name)
    end
    H.eq(#fake.calls, 0)
    scratch("@이영희", 0)
    H.fails_with(function()
      open().open_at_point()
    end, "@이영희 페이지가 아직 없습니다. l(link)을 실행하세요")
    scratch("[[없는 페이지|별칭]]", 3)
    H.fails_with(function()
      open().open_at_point()
    end, "페이지가 없습니다: 없는 페이지")
    scratch("아무것도 없는 줄", 0)
    H.fails_with(function()
      open().open_at_point()
    end, "커서 위치에 Jira 키·[[링크]]·@이름이 없습니다")
  end)

  case("resolve_link: 확장자 규칙과 폴더 순서", function()
    local vault = H.with_vault({ python = H.python() })
    H.write_bytes(vault .. "/wiki/같은 이름.md", "wiki\n")
    H.write_bytes(vault .. "/wiki/topics/같은 이름.md", "topics\n")
    H.write_bytes(vault .. "/raw/assets/그림.png", "png")
    H.write_bytes(vault .. "/raw/b/sub/중첩.md", "sub\n")
    H.write_bytes(vault .. "/raw/a/중첩.md", "a\n")
    H.eq(open().resolve_link("같은 이름"), vault .. "/wiki/같은 이름.md")
    H.ok(same_file(open().resolve_link("그림.png"), vault .. "/raw/assets/그림.png"))
    H.ok(same_file(open().resolve_link("중첩"), vault .. "/raw/a/중첩.md"))
    H.eq(open().resolve_link("없음"), nil)
  end)
end)

describe("results", function()
  case("결과 버퍼: 읽기 전용, 맨 위, 끝 알림 (work-kb--show-results)", function()
    H.with_vault({ python = H.python() })
    local stub = H.ui_stub()
    local results = require("work-kb.results")
    local p = proc()
    H.fake_spawn({ code = 0, output = "KB-12 완료로 전환 제안\nKB-13 확인\n" })
    p.wait(p.run(p.kb_args("today", "--check-done"), { buffer = "*work-kb-check-done*", on_exit = function() end }), 5000)
    local bufnr = results.show("*work-kb-check-done*", "완료 전환 제안", 0)
    H.ok(bufnr)
    H.eq(vim.bo[bufnr].modifiable, false)
    H.eq(stub.shown, { bufnr })
    H.eq(stub.messages, { "work-kb: 완료 전환 제안 완료" })
    local map = vim.fn.maparg("<CR>", "n", false, true)
    H.eq(type(map.callback), "nil", "버퍼 밖에는 없음")
    vim.api.nvim_buf_call(bufnr, function()
      local local_map = vim.fn.maparg("<CR>", "n", false, true)
      H.eq(local_map.buffer, 1)
      H.eq(type(local_map.callback), "function")
      H.eq(vim.fn.maparg("q", "n", false, true).buffer, 1)
    end)
    -- 다음 실행은 읽기 전용 버퍼도 비우고 다시 채운다
    H.fake_spawn({ code = 7, output = "거부\n" })
    p.wait(p.run(p.kb_args("sync", "--jql", "x"), { buffer = "*work-kb-check-done*", on_exit = function() end }), 5000)
    H.eq(H.buffer_text("*work-kb-check-done*"), "거부\n")
    results.show("*work-kb-check-done*", "라이브 JQL", 7)
    H.eq(stub.messages[2], "work-kb: 라이브 JQL 실패 (종료 7: 정책 거부)")
  end)

  case("결과 버퍼의 <CR>은 커서 위치의 키를 연다", function()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/jira/KB-12.md"
    H.write_bytes(file, page(JIRA_FULL, "KB-12"))
    H.ui_stub()
    local fake = H.fake_spawn({ code = 0, output = "제안: KB-12 → 완료\n" })
    local p = proc()
    p.wait(p.run(p.kb_args("today", "--check-done"), { buffer = "*work-kb-check-done*", on_exit = function() end }), 5000)
    local bufnr = require("work-kb.results").show("*work-kb-check-done*", "완료 전환 제안", 0)
    vim.api.nvim_win_set_buf(0, bufnr)
    vim.api.nvim_win_set_cursor(0, { 1, 9 })
    vim.fn.maparg("<CR>", "n", false, true).callback()
    H.ok(same_file(buf_file(vim.api.nvim_get_current_buf()), file))
    H.eq(#fake.calls, 1)
  end)
end)
