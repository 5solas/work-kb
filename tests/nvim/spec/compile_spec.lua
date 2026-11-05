-- g/G (ingest → compile)과 a (주제 제안 승인) — ERT 편찬·주제 제안 7개.
local H = require("helpers")

local function compile()
  return require("work-kb.compile")
end

local function proc()
  return require("work-kb.proc")
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
  -- 진행 표시를 새로 (테스트가 끝나면 reset이 되돌린다)
  H.patch(compile(), "_running", false)
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
      return not compile()._running
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

local function rewrite_on_disk(file, text)
  H.write_bytes(file, text)
  local t = os.time() + 10
  assert(vim.uv.fs_utime(file, t, t))
end

local PROPOSALS = table.concat({
  "# 주제 제안\n\n<!-- kb:proposals:start -->\n",
  "1. 배포 자동화 — 대기\n2. 캐시 설계 — 대기\n3. 장애 대응 — 대기\n",
  "<!-- kb:proposals:end -->\n",
})

local function same_file(a, b)
  return vim.fs.normalize(a):lower() == vim.fs.normalize(b):lower()
end

describe("compile", function()
  it("ingest-then-compile: ingest --root V 뒤 compile --root V를 같은 *work-kb* 버퍼로, 묻지 않음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    local rec = record_runs()
    H.fake_spawn(function(call)
      return call.argv[1] .. ": 끝\n", 0
    end)
    local stub = H.ui_stub()
    local handle = compile().ingest_compile({ dry_run = false })
    H.ok(handle and handle.args, "ingest 핸들")
    H.ok(compile()._running)
    rec.settle()
    H.eq(rec.calls, {
      { args = { "ingest", "--root", vault }, buffer = "*work-kb*" },
      { args = { "compile", "--root", vault }, buffer = "*work-kb*" },
    })
    H.eq(stub.messages, { "work-kb: 편찬 중… (ingest → compile)", "work-kb: 편찬 완료 — compile: 끝" })
    H.eq(H.buffer_text(), "== ingest ==\ningest: 끝\n\n== compile ==\ncompile: 끝\n")
    H.eq(stub.shown, { proc().get_buffer("*work-kb*") })
    H.eq(#stub.prompts, 0)
    H.eq(compile()._running, false)
  end)

  it("ingest-dry-run: G는 두 단계 모두 --dry-run", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    local rec = record_runs()
    H.fake_spawn({ code = 0 })
    local stub = H.ui_stub()
    compile().ingest_compile({ dry_run = true })
    rec.settle()
    H.eq(rec.args(), { { "ingest", "--root", vault, "--dry-run" }, { "compile", "--root", vault, "--dry-run" } })
    -- 출력이 없으면 마지막 비어 있지 않은 줄은 머리말이다 (Emacs도 같다; ERT는 가짜 run이라 머리말이 없었다)
    H.eq(stub.messages, { "work-kb: 편찬 미리 보기 중… (ingest → compile)", "work-kb: 편찬 미리 보기 완료 — == compile --dry-run ==" })
    H.eq(H.buffer_text(), "== ingest --dry-run ==\n\n== compile --dry-run ==\n")
  end)

  it("ingest-failures: ingest 실패여도 compile, 종료 5면 compile 건너뜀, 진행 표시는 풀림", function()
    local cases = {
      { 1, 0, { "ingest", "compile" }, "work-kb: 편찬 끝 — ingest 실패(종료 1: 오류), compile 완료. *work-kb* 버퍼를 보세요" },
      {
        5,
        nil,
        { "ingest" },
        "work-kb: 편찬 중단 — ingest 실패(종료 5: 공개 호스트 거부). compile은 돌리지 않았습니다. *work-kb* 버퍼를 보세요",
      },
      { 0, 3, { "ingest", "compile" }, "work-kb: 편찬 끝 — ingest 완료, compile 실패(종료 3: 네트워크). *work-kb* 버퍼를 보세요" },
    }
    for _, case in ipairs(cases) do
      fresh()
      H.with_vault({ python = H.python() })
      local rec = record_runs()
      H.fake_spawn(function(call)
        if call.argv[1] == "ingest" then
          return "", case[1]
        end
        return "", case[2]
      end)
      local stub = H.ui_stub()
      compile().ingest_compile()
      rec.settle()
      local names = {}
      for i, a in ipairs(rec.args()) do
        names[i] = a[1]
      end
      H.eq(names, case[3])
      H.eq(stub.messages[#stub.messages], case[4])
      H.eq(#stub.shown, 1)
      H.eq(compile()._running, false)
    end
  end)

  it("ingest-in-flight: 도는 중에 다시 부르면 돌리지 않음, 시작이 실패하면 진행 표시를 푼다", function()
    fresh()
    H.with_vault({ python = H.python() })
    local p = proc()
    local calls = {}
    local fake_handle = { fake = true }
    H.patch(p, "run", function(args)
      table.insert(calls, args)
      return fake_handle
    end)
    local stub = H.ui_stub()
    H.eq(compile().ingest_compile(), fake_handle)
    H.eq(compile().ingest_compile(), nil)
    H.eq(compile().ingest_compile({ dry_run = true }), nil)
    H.eq(stub.messages[#stub.messages], "work-kb: 편찬이 이미 진행 중입니다 (*work-kb* 버퍼)")
    H.eq(#calls, 1)
    H.eq(calls[1][1], "ingest")
    H.ok(compile()._running)
    -- 시작 실패
    compile()._running = false
    H.patch(p, "run", function()
      require("work-kb.ui").fail("Python을 찾지 못했습니다")
    end)
    local err = H.fails_with(function()
      compile().ingest_compile()
    end, "Python을 찾지 못했습니다")
    H.ok(require("work-kb.ui").is_user_error(err))
    H.eq(compile()._running, false)
    -- compile 시작이 실패하면 알리고 진행 표시를 푼다
    local n = 0
    H.patch(p, "run", function(_, opts)
      n = n + 1
      if n == 1 then
        vim.schedule(function()
          opts.on_exit(0, nil)
        end)
        return fake_handle
      end
      error("compile 시작 실패", 0)
    end)
    stub = H.ui_stub()
    compile().ingest_compile()
    H.wait(function()
      return not compile()._running
    end, 5000)
    H.eq(compile()._running, false)
    H.eq(stub.messages[#stub.messages], "work-kb: compile을 시작하지 못했습니다: compile 시작 실패")
  end)

  it("ingest-real-async: 실제 프로세스 둘, ingest가 끝나면 compile, 두 출력이 한 버퍼에 차례로", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    local code = table.concat({
      "import sys, json",
      "a = json.loads(sys.stdin.buffer.read().decode('utf-8'))",
      "sys.stdout.buffer.write(('\\ub2e8\\uacc4 ' + a[0] + ' ' + a[a.index('--root') + 1] + '\\n').encode('utf-8'))",
    }, "\n")
    local buf = proc().get_buffer("*work-kb*", true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "이전 출력", "" })
    local rec = H.rewrite_spawn(function(spec)
      return { spec.cmd[1], "-c", code }
    end)
    H.ui_stub()
    local handle = compile().ingest_compile()
    H.ok(handle and handle.via == "process", "실제 한 번 실행")
    H.ok(compile()._running)
    H.wait(function()
      return not compile()._running
    end, 60000)
    H.eq(compile()._running, false)
    H.eq(#rec.calls, 2)
    for _, call in ipairs(rec.calls) do
      H.eq(vim.list_slice(call.cmd, #call.cmd - 3), { "-X", "utf8", "scripts/kb.py", "--argv-stdin" })
    end
    H.eq(H.buffer_text(), "== ingest ==\n단계 ingest " .. vault .. "\n\n== compile ==\n단계 compile " .. vault .. "\n")
  end)

  it("proposals-accept: _proposals.md를 열고 번호 → 확인 → proposals accept --root V <n> (--yes 없음), 버퍼를 다시 읽음", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/wiki/_proposals.md"
    H.write_bytes(file, PROPOSALS)
    local rec = record_runs()
    H.fake_spawn(function(call)
      rewrite_on_disk(file, (PROPOSALS:gsub("3%. 장애 대응 — 대기", "3. 장애 대응 — 승인")))
      return string.format("proposals: %s 승인\n", call.argv[#call.argv]), 0
    end)
    local stub = H.ui_stub({ { "input", " 3 " }, { "confirm", true } })
    local handle = compile().proposals()
    rec.settle()
    H.ok(handle and handle.done)
    local buf = vim.api.nvim_get_current_buf()
    H.ok(same_file(vim.api.nvim_buf_get_name(buf), file), "현재 창이 _proposals.md")
    H.ok(not vim.bo[buf].modified)
    H.contains(table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n"), "장애 대응 — 승인")
    H.eq(stub.prompts[1].prompt, "승인할 제안 번호 (all = 전부, 비우면 보기만): ")
    H.eq(stub.prompts[2].prompt, "제안 3을(를) 승인할까요? (wiki/topics/에 주제 페이지를 만들거나 별칭을 더합니다) ")
    H.eq(stub.messages, { "work-kb: 제안 3 승인 완료 — proposals: 3 승인" })
    stub = H.ui_stub({ { "input", "ALL" }, { "confirm", true } })
    compile().proposals()
    rec.settle()
    H.eq(stub.prompts[2].prompt, "모든 제안을(를) 승인할까요? (wiki/topics/에 주제 페이지를 만들거나 별칭을 더합니다) ")
    H.eq(stub.messages, { "work-kb: 모든 제안 승인 완료 — proposals: all 승인" })
    H.ui_stub({ { "input", "#2" }, { "confirm", true } })
    compile().proposals()
    rec.settle()
    H.eq(rec.calls, {
      { args = { "proposals", "accept", "--root", vault, "3" }, buffer = "*work-kb*" },
      { args = { "proposals", "accept", "--root", vault, "all" }, buffer = "*work-kb*" },
      { args = { "proposals", "accept", "--root", vault, "2" }, buffer = "*work-kb*" },
    })
    -- 전부·모두도 all
    H.ui_stub({ { "input", "전부" }, { "confirm", false } })
    H.eq(compile().proposals(), nil)
    H.ui_stub({ { "input", " 모두 " }, { "confirm", false } })
    H.eq(compile().proposals(), nil)
    fresh()
  end)

  it("proposals-declined-empty-invalid: 비우면 열기만, n이면 보내지 않음, 번호가 아니면 오류, 실패는 이유와 함께", function()
    fresh()
    local vault = H.with_vault({ python = H.python() })
    local file = vault .. "/wiki/_proposals.md"
    local rec = record_runs()
    H.fake_spawn(function()
      error("보내면 안 됨")
    end)
    -- 파일이 없으면 묻지도 열지도 않는다
    local stub = H.ui_stub()
    H.eq(compile().proposals(), nil)
    H.eq(stub.messages, { "work-kb: wiki/_proposals.md이 아직 없습니다. g(편찬)로 주제 제안을 만드세요" })
    for _, b in ipairs(vim.api.nvim_list_bufs()) do
      H.ok(not same_file(vim.api.nvim_buf_get_name(b), file), "제안 파일을 열면 안 됨")
    end
    H.write_bytes(file, PROPOSALS)
    stub = H.ui_stub({ { "input", "  " } })
    H.eq(compile().proposals(), nil)
    H.eq(stub.messages, { "work-kb: 승인하지 않았습니다 (보기만)" })
    H.ok(same_file(vim.api.nvim_buf_get_name(0), file), "보기만이어도 연다")
    stub = H.ui_stub({ { "input", "3" }, { "confirm", false } })
    H.eq(compile().proposals(), nil)
    H.eq(stub.messages, { "work-kb: 승인을 취소했습니다" })
    for _, bad in ipairs({ "abc", "3,4", "-1" }) do
      H.ui_stub({ { "input", bad } })
      local err = H.fails_with(function()
        compile().proposals()
      end, string.format('제안 번호나 all이 아닙니다: "%s"', bad))
      H.ok(require("work-kb.ui").is_user_error(err))
    end
    H.ui_stub({ { "input", nil } })
    H.fails_with(function()
      compile().proposals()
    end, "work-kb: 취소했습니다")
    H.eq(#rec.calls, 0)
    -- 실패: 종료 7 → 이유와 버퍼
    H.fake_spawn({ code = 7, output = "kb: KB_AGENT=1 세션에서는 거부\n" })
    stub = H.ui_stub({ { "input", "1" }, { "confirm", true } })
    compile().proposals()
    rec.settle()
    H.eq(stub.messages, { "work-kb: 제안 1 승인 실패 (종료 7: 정책 거부). *work-kb* 버퍼를 보세요" })
    H.eq(stub.shown, { proc().get_buffer("*work-kb*") })
    H.eq(#rec.calls, 1)
    H.eq(H.read_bytes(file), PROPOSALS)
    fresh()
  end)
end)
