-- kb.py 결과 버퍼 (Emacs work-kb-results-mode, work-kb--show-results): 읽기 전용,
-- 버퍼 안 <CR> → 커서 위치 열기 (o), q → 창 닫기. *work-kb-jql*, *work-kb-check-done*에 쓴다.

local M = {}

local function ui()
  return require("work-kb.ui")
end

local function proc()
  return require("work-kb.proc")
end

---<CR>: o와 같다 (사용자 오류는 문구만 보인다).
function M.open()
  return require("work-kb").call("open-at-point")
end

---q: 이 창을 닫는다. 마지막 창이면 다른 버퍼로 바꾼다 (Emacs quit-window).
function M.quit()
  local win = vim.api.nvim_get_current_win()
  if #vim.api.nvim_tabpage_list_wins(0) > 1 then
    pcall(vim.api.nvim_win_close, win, false)
  else
    pcall(vim.cmd, "bprevious")
  end
end

---BUFNR을 결과 버퍼로 만든다: nomodifiable, 버퍼 지역 <CR>·q. 다시 불러도 된다.
---@param bufnr integer
function M.attach(bufnr)
  if not (bufnr and vim.api.nvim_buf_is_valid(bufnr)) then
    return
  end
  vim.bo[bufnr].modifiable = false
  vim.bo[bufnr].modified = false
  vim.keymap.set("n", "<CR>", function()
    M.open()
  end, { buffer = bufnr, silent = true, nowait = true, desc = "work-kb: 커서 위치 열기" })
  vim.keymap.set("n", "q", function()
    M.quit()
  end, { buffer = bufnr, silent = true, nowait = true, desc = "work-kb: 결과 창 닫기" })
  vim.b[bufnr].work_kb_results = true
end

---결과 버퍼 BUFFER(이름 또는 번호)를 결과 모드로 맨 위에서 초점 없이 보이고 끝을 알린다
---(work-kb--show-results): 0 → `work-kb: WHAT 완료`, 아니면 `work-kb: WHAT 실패 (종료 N: 이유)`.
---@param buffer string|integer
---@param what string
---@param code integer|nil
---@return integer|nil bufnr
function M.show(buffer, what, code)
  local bufnr = proc().get_buffer(buffer, false)
  if bufnr then
    M.attach(bufnr)
    for _, win in ipairs(vim.fn.win_findbuf(bufnr)) do
      pcall(vim.api.nvim_win_set_cursor, win, { 1, 0 })
    end
    local win = ui().show(bufnr)
    if type(win) == "number" and vim.api.nvim_win_is_valid(win) then
      pcall(vim.api.nvim_win_set_cursor, win, { 1, 0 })
    end
  end
  if code == 0 then
    ui().notify(string.format("work-kb: %s 완료", what))
  else
    ui().notify(string.format("work-kb: %s 실패 (%s)", what, proc().exit_reason(code)))
  end
  return bufnr
end

return M
