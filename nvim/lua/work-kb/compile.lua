-- g/G: ingest → compile, a: 주제 제안 승인 (Emacs work-kb-ingest / work-kb-proposals, .el:1439-1556).
-- 둘 다 proc.run(stdin JSON)만 거친다. LLM 연결·회사 가드는 모두 Python 쪽 일이다.

local M = {}

M.PROPOSALS_FILE = "wiki/_proposals.md"

---g가 도는 중이면 true (두 번 돌려 *work-kb* 버퍼를 섞지 않는다).
M._running = false

local function proc()
  return require("work-kb.proc")
end

local function ui()
  return require("work-kb.ui")
end

---단계 NAME이 CODE로 끝난 결과 문구.
local function step_result(name, code)
  if code == 0 then
    return name .. " 완료"
  end
  return string.format("%s 실패(%s)", name, proc().exit_reason(code))
end

---g가 끝났다: 버퍼를 보이고 두 단계 결과를 알린다. SKIPPED면 compile을 돌리지 않았다.
local function finish(label, ingest_code, compile_code, skipped)
  M._running = false
  local p = proc()
  local buf = p.get_buffer(p.OUTPUT, false)
  if buf then
    ui().show(buf)
  end
  if not skipped and ingest_code == 0 and compile_code == 0 then
    local last = p.last_line(buf)
    ui().notify(string.format("work-kb: %s 완료%s", label, last and (" — " .. last) or ""))
  elseif skipped then
    ui().notify(
      string.format(
        "work-kb: %s 중단 — %s. compile은 돌리지 않았습니다. %s 버퍼를 보세요",
        label,
        step_result("ingest", ingest_code),
        p.OUTPUT
      )
    )
  else
    ui().notify(
      string.format(
        "work-kb: %s 끝 — %s, %s. %s 버퍼를 보세요",
        label,
        step_result("ingest", ingest_code),
        step_result("compile", compile_code),
        p.OUTPUT
      )
    )
  end
end

---ingest가 INGEST_CODE로 끝난 뒤 compile을 같은 버퍼에 이어 돌린다.
---공개 호스트 거부(종료 5)면 compile도 같은 가드에 걸리므로 돌리지 않는다.
local function after_ingest(ingest_code, dry_run, label)
  if ingest_code == 5 then
    finish(label, ingest_code, nil, true)
    return
  end
  local p = proc()
  local args = dry_run and p.kb_args("compile", "--dry-run") or p.kb_args("compile")
  local ok, err = pcall(p.run, args, {
    buffer = p.OUTPUT,
    append = true,
    header = string.format("\n== compile%s ==\n", dry_run and " --dry-run" or ""),
    on_exit = function(code)
      finish(label, ingest_code, code, false)
    end,
  })
  if not ok then
    M._running = false
    ui().notify("work-kb: compile을 시작하지 못했습니다: " .. tostring(err))
  end
end

---g: 이미지 추출(`kb.py ingest`) 뒤 편찬(`kb.py compile`)을 차례로 돌린다. 두 출력은 *work-kb*
---한 버퍼에 이어 쓴다. OPTS.dry_run(G)이면 둘 다 `--dry-run`. ingest가 실패해도(종료 5 제외)
---compile은 돌린다. 이미 도는 중이면 다시 돌리지 않는다. proc.run 핸들 (도는 중이면 nil).
---@param opts? {dry_run?: boolean}
function M.ingest_compile(opts)
  local p = proc()
  if M._running then
    ui().notify(string.format("work-kb: 편찬이 이미 진행 중입니다 (%s 버퍼)", p.OUTPUT))
    return nil
  end
  local dry_run = type(opts) == "table" and opts.dry_run == true
  local label = dry_run and "편찬 미리 보기" or "편찬"
  M._running = true
  ui().notify(string.format("work-kb: %s 중… (ingest → compile)", label))
  local ok, result = pcall(function()
    local args = dry_run and p.kb_args("ingest", "--dry-run") or p.kb_args("ingest")
    return p.run(args, {
      buffer = p.OUTPUT,
      header = string.format("== ingest%s ==\n", dry_run and " --dry-run" or ""),
      on_exit = function(code)
        after_ingest(code, dry_run, label)
      end,
    })
  end)
  if not ok then
    M._running = false
    error(result, 0)
  end
  return result
end

---------------------------------------------------------------- 주제 제안

---기계 소유 FILE을 지금 창에 연다. 이미 연 버퍼가 수정 없이 디스크보다 낡았으면 묻지 않고
---다시 읽는다 (work-kb--visit-jira; open.visit). 그 버퍼 번호를 돌려준다.
function M._visit(file)
  return require("work-kb.open").visit(file)
end

---승인할 제안: 번호 글(앞의 #은 뗀다), "all", 비우면 nil. 그 밖은 사용자 오류.
function M.read_proposal_choice()
  local answer = ui().input("승인할 제안 번호 (all = 전부, 비우면 보기만): ")
  if answer == nil then
    ui().fail("work-kb: 취소했습니다")
  end
  local text = vim.fn.tolower((answer:gsub("^[ \t\n\r]+", ""):gsub("[ \t\n\r]+$", "")))
  if text == "" then
    return nil
  end
  if text == "all" or text == "전부" or text == "모두" then
    return "all"
  end
  local digits = text:match("^#?([0-9]+)$")
  if digits then
    return digits
  end
  ui().fail(string.format('제안 번호나 all이 아닙니다: "%s"', (text:gsub("\\", "\\\\"):gsub('"', '\\"'))))
end

---a: wiki/_proposals.md를 열고, 번호(또는 all)를 받아 확인하면 `kb.py proposals accept <n|all>`
---(--yes 없음)을 보낸다. 비우면 열기만. 끝나면 제안 버퍼를 다시 읽는다. 파일이 없으면 묻지 않는다.
---승인을 보냈으면 proc.run 핸들, 아니면 nil.
function M.proposals()
  local p = proc()
  local file = require("work-kb.config").vault() .. "/" .. M.PROPOSALS_FILE
  if not vim.uv.fs_stat(file) then
    ui().notify(string.format("work-kb: %s이 아직 없습니다. g(편찬)로 주제 제안을 만드세요", M.PROPOSALS_FILE))
    return nil
  end
  local bufnr = M._visit(file)
  local choice = M.read_proposal_choice()
  if choice == nil then
    ui().notify("work-kb: 승인하지 않았습니다 (보기만)")
    return nil
  end
  local what = choice == "all" and "모든 제안" or ("제안 " .. choice)
  if not ui().confirm(string.format("%s을(를) 승인할까요? (wiki/topics/에 주제 페이지를 만들거나 별칭을 더합니다) ", what)) then
    ui().notify("work-kb: 승인을 취소했습니다")
    return nil
  end
  return p.run(p.sub_args("proposals", "accept", choice), {
    buffer = p.OUTPUT,
    on_exit = function(code)
      if vim.api.nvim_buf_is_valid(bufnr) and vim.api.nvim_buf_is_loaded(bufnr) then
        require("work-kb.fileio").refresh(bufnr)
      end
      local buf = p.get_buffer(p.OUTPUT, false)
      if code == 0 then
        local last = p.last_line(buf)
        ui().notify(string.format("work-kb: %s 승인 완료%s", what, last and (" — " .. last) or ""))
      else
        if buf then
          ui().show(buf)
        end
        ui().notify(
          string.format("work-kb: %s 승인 실패 (%s). %s 버퍼를 보세요", what, p.exit_reason(code), p.OUTPUT)
        )
      end
    end,
  })
end

return M
