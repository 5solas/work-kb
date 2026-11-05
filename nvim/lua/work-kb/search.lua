-- s (검색): Emacs work-kb-search. fzf-lua live grep → rg(quickfix) → `kb.py search`(quickfix).
-- rg는 질의를 한 덩어리 글자 그대로(--fixed-strings) 찾고, `kb.py search`는 공백을 AND로 본다.

local M = {}

M.BUFFER = "*work-kb-search*"
M.RG_ARGS = {
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
}

local function config()
  return require("work-kb.config")
end

local function proc()
  return require("work-kb.proc")
end

local function ui()
  return require("work-kb.ui")
end

local function trim(s)
  return (s:gsub("^[ \t\n\r]+", ""):gsub("[ \t\n\r]+$", ""))
end

---fzf-lua 모듈 (불러올 수 있으면). 이음매.
function M._fzf()
  local ok, mod = pcall(require, "fzf-lua")
  if ok and type(mod) == "table" and type(mod.live_grep) == "function" then
    return mod
  end
  return nil
end

---rg 실행 파일 경로 (없으면 nil). 이음매.
function M._rg()
  local path = vim.fn.exepath("rg")
  if path == nil or path == "" then
    return nil
  end
  return path
end

---볼트 아래 있는 검색 폴더 (daily inbox wiki jira 중). 없으면 { "." }.
function M.search_dirs(vault)
  local dirs = {}
  for _, d in ipairs({ "daily", "inbox", "wiki", "jira" }) do
    local st = vim.uv.fs_stat(vault .. "/" .. d)
    if st and st.type == "directory" then
      table.insert(dirs, d)
    end
  end
  if #dirs == 0 then
    dirs = { "." }
  end
  return dirs
end

---`경로:줄:본문`(rg) / `경로:줄: 본문`(kb.py search) 줄들을 quickfix 항목으로. 경로는 VAULT 기준.
---맞지 않는 줄(결과 없음, 건너뜀: …, … N개 더 있음)은 OTHERS에 모은다.
---@param text string
---@param vault string
---@param strip_space boolean `: ` 뒤의 공백 하나를 뗀다 (kb.py search)
---@return table[] items, string[] others
function M.parse(text, vault, strip_space)
  local items, others = {}, {}
  for line in ((text or "") .. "\n"):gmatch("([^\n]*)\n") do
    line = line:gsub("\r$", "")
    local path, lnum, body = line:match("^([^:]+):(%d+):(.*)$")
    if path then
      if strip_space then
        body = body:gsub("^ ", "", 1)
      end
      table.insert(items, {
        filename = vault .. "/" .. path,
        lnum = tonumber(lnum),
        text = body,
      })
    elseif line ~= "" then
      table.insert(others, line)
    end
  end
  return items, others
end

---quickfix 목록을 채우고 초점을 옮기지 않고 연다.
function M._show_quickfix(items, title)
  vim.fn.setqflist({}, " ", { title = title, items = items })
  local win = vim.api.nvim_get_current_win()
  vim.cmd("botright copen")
  if vim.api.nvim_win_is_valid(win) then
    vim.api.nvim_set_current_win(win)
  end
end

local function title_of(query)
  return "work-kb 검색: " .. query
end

local function output_buffer(text)
  local p = proc()
  local bufnr = p.get_buffer(M.BUFFER, true)
  local was = vim.bo[bufnr].modifiable
  vim.bo[bufnr].modifiable = true
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, vim.split(text, "\n", { plain = true }))
  vim.bo[bufnr].modifiable = was
  return bufnr
end

---rg로 찾아 quickfix에 넣는다 (work-kb--rg-search). 패턴은 stdin(`-f -`)으로.
---돌려주는 표: { done, code, items } (끝나면 채운다).
function M.rg_search(rg, query, vault)
  local p = proc()
  local cmd = { rg }
  vim.list_extend(cmd, M.RG_ARGS)
  vim.list_extend(cmd, M.search_dirs(vault))
  local handle = { done = false, via = "rg" }
  local out, err = {}, {}
  handle.process = p._spawn({
    cmd = cmd,
    cwd = vault,
    stdin = query .. "\n",
    merge = false,
    on_stdout = function(data)
      table.insert(out, data)
    end,
    on_stderr = function(data)
      table.insert(err, data)
    end,
    on_exit = function(code)
      local items = M.parse(table.concat(out), vault, false)
      handle.items = items
      if #items > 0 then
        M._show_quickfix(items, title_of(query))
      end
      if code == 1 then
        ui().notify("결과 없음")
      elseif code ~= 0 then
        local text = table.concat(err):gsub("\r\n", "\n")
        ui().show(output_buffer(text))
        ui().notify(string.format("work-kb: 검색 실패 (종료 %s)", tostring(code)))
      end
      handle.code = code
      handle.done = true
    end,
  })
  return handle
end

---`kb.py search --root V QUERY` 결과를 quickfix에 넣는다. proc.run 핸들.
function M.kb_search(query, vault)
  local p = proc()
  return p.run(p.kb_args("search", query), {
    buffer = M.BUFFER,
    on_exit = function(code, bufnr)
      if code ~= 0 then
        if bufnr and vim.api.nvim_buf_is_valid(bufnr) then
          ui().show(bufnr)
        end
        ui().notify(string.format("work-kb: 검색 실패 (종료 %s)", tostring(code)))
        return
      end
      local items, others = M.parse(p.buffer_text(bufnr) or "", vault, true)
      if #items > 0 then
        M._show_quickfix(items, title_of(query))
        if #others > 0 then
          ui().notify("work-kb: " .. others[#others])
        end
      else
        ui().notify(others[#others] or "결과 없음")
      end
    end,
  })
end

---볼트에서 QUERY를 찾는다. QUERY가 없으면 `검색: `으로 묻는다 (기본값 = 커서 아래 낱말).
---@param query? string
function M.search(query)
  if query == nil then
    query = ui().input("검색: ", { default = vim.fn.expand("<cword>") })
    if query == nil then
      return nil
    end
  end
  query = trim(query)
  if query == "" then
    ui().fail("검색어가 비었습니다")
  end
  local vault = config().vault()
  local fzf = M._fzf()
  if fzf then
    return fzf.live_grep({ cwd = vault, search = query })
  end
  local rg = M._rg()
  if rg then
    return M.rg_search(rg, query, vault)
  end
  return M.kb_search(query, vault)
end

return M
