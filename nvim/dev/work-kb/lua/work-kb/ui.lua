-- work-kb 사용자 입출력. 모두 동기식이고, 테스트는 이 표의 함수를 바꿔 끼운다
-- (호출하는 쪽은 늘 `require("work-kb.ui").input(...)`처럼 표를 거쳐 부른다).

local M = {}

local CANCEL = "\1work-kb-cancel\1"

-- 사용자 오류로 올린 문구 (최근 몇 개). 진입점이 traceback 없이 보여 줄지 고를 때 쓴다.
local recent_user_errors = {}
local recent_order = {}

---사용자 오류를 낸다 (Emacs user-error). 오류 값은 MSG 문자열 그대로다.
---@param msg string
function M.fail(msg)
  msg = tostring(msg)
  if not recent_user_errors[msg] then
    recent_user_errors[msg] = true
    table.insert(recent_order, msg)
    if #recent_order > 32 then
      recent_user_errors[table.remove(recent_order, 1)] = nil
    end
  end
  error(msg, 0)
end

---ERR가 `fail`로 낸 사용자 오류인가.
function M.is_user_error(err)
  return type(err) == "string" and recent_user_errors[err] == true
end

---알림 (Emacs message).
function M.notify(msg)
  vim.notify(tostring(msg), vim.log.levels.INFO)
end

---한 줄 입력. 취소(Esc)면 nil. 비운 채 Enter면 opts.default(있을 때) 또는 "".
---@param prompt string
---@param opts? {initial?: string, default?: string, completion?: string}
---@return string|nil
function M.input(prompt, opts)
  opts = opts or {}
  local ok, answer = pcall(vim.fn.input, {
    prompt = prompt,
    default = opts.initial or "",
    completion = opts.completion,
    cancelreturn = CANCEL,
  })
  if not ok then
    -- 입력 중 <C-c>는 Keyboard interrupt 오류로 온다: 취소로 본다.
    return nil
  end
  if answer == CANCEL then
    return nil
  end
  if answer == "" and opts.default ~= nil then
    return opts.default
  end
  return answer
end

M._items = {}

---`input`의 사용자 완성 (customlist): 지금 고르는 후보 중 ARGLEAD로 시작하는 것.
function M.complete(arglead)
  local out = {}
  for _, item in ipairs(M._items) do
    if arglead == nil or arglead == "" or item:sub(1, #arglead) == arglead then
      table.insert(out, item)
    end
  end
  return out
end

---후보에서 고른다. strict면 번호 목록에서 하나 (취소 nil), 아니면 목록에 없는 글도 받는다
---(Tab 완성, 비우면 opts.default).
---@param prompt string
---@param items string[]
---@param opts? {strict?: boolean, default?: string}
---@return string|nil
function M.select(prompt, items, opts)
  opts = opts or {}
  if opts.strict then
    local lines = { prompt }
    for i, item in ipairs(items) do
      table.insert(lines, string.format("%d. %s", i, item))
    end
    local ok, index = pcall(vim.fn.inputlist, lines)
    if not ok or type(index) ~= "number" or index < 1 or index > #items then
      return nil
    end
    return items[index]
  end
  M._items = items
  local answer = M.input(prompt, {
    default = opts.default,
    completion = "customlist,v:lua.require'work-kb.ui'.complete",
  })
  M._items = {}
  return answer
end

---예/아니요 확인 (Emacs y-or-n-p). 예면 true.
---@param msg string
---@return boolean
function M.confirm(msg)
  local prompt = tostring(msg):gsub("[ \t]+$", "") .. " (y or n) "
  local hint = ""
  while true do
    vim.api.nvim_echo({ { hint .. prompt, "Question" } }, false, {})
    local ok, ch = pcall(vim.fn.getcharstr)
    if not ok then
      return false -- <C-c>
    end
    if ch == "y" or ch == "Y" then
      vim.api.nvim_echo({ { "" } }, false, {})
      return true
    elseif ch == "n" or ch == "N" or ch == "\27" or ch == "\3" then
      vim.api.nvim_echo({ { "" } }, false, {})
      return false
    end
    hint = "y 또는 n을 누르세요. "
  end
end

---BUFNR을 초점을 옮기지 않고 보인다 (Emacs display-buffer). 이미 보이면 그 창을 돌려준다.
---@param bufnr integer
---@return integer|nil window
function M.show(bufnr)
  if not (bufnr and vim.api.nvim_buf_is_valid(bufnr)) then
    return nil
  end
  local tab = vim.api.nvim_get_current_tabpage()
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
    if vim.api.nvim_win_get_buf(win) == bufnr then
      return win
    end
  end
  local height = math.max(6, math.floor(vim.o.lines * 0.3))
  local ok, win = pcall(vim.api.nvim_open_win, bufnr, false, { split = "below", win = -1, height = height })
  if ok then
    return win
  end
  return nil
end

return M
