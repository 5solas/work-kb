-- c 캡처, C 클립보드 이미지, F 이미지 파일, i inbox 메모 (Emacs work-kb-capture 등).
-- 캡처 줄은 오늘 daily의 `## 기록` 끝에 들어간다 (daily 쓰기 허용 목록 1).
-- 사용자 입출력은 work-kb.ui, 프로세스는 work-kb.proc, 시각은 work-kb.time을 거친다.

local M = {}

local function config()
  return require("work-kb.config")
end

local function proc()
  return require("work-kb.proc")
end

local function ui()
  return require("work-kb.ui")
end

local function time()
  return require("work-kb.time")
end

local function names()
  return require("work-kb.names")
end

local function fileio()
  return require("work-kb.fileio")
end

local function daily()
  return require("work-kb.daily")
end

---------------------------------------------------------------- 글 다듬기 (Emacs string-trim 기본: 공백·탭·LF·CR)

local function trim(s)
  return (s:gsub("^[ \t\n\r]+", ""):gsub("[ \t\n\r]+$", ""))
end

local function rtrim(s)
  return (s:gsub("[ \t\n\r]+$", ""))
end

local function ltrim(s)
  return (s:gsub("^[ \t\n\r]+", ""))
end

---\r?\n으로 나눈다 (split-string "\r?\n").
local function split_lines(s)
  local out = {}
  for piece in (s .. "\n"):gmatch("(.-)\r?\n") do
    table.insert(out, piece)
  end
  return out
end

M._trim = trim

---------------------------------------------------------------- 캡처 줄

---TEXT를 `- HH:MM 내용`으로. `[ ] `로 시작하면 `- [ ] HH:MM 내용` (D15).
---여러 줄이면 첫 줄에 시각, 나머지는 두 칸 들여쓰기. 빈 줄은 뺀다. 비면 사용자 오류.
---@param text string|nil
---@param t? integer
---@return string
function M.format(text, t)
  local hhmm = time().hhmm(t or time().now())
  local lines = {}
  for _, l in ipairs(split_lines(trim(text or ""))) do
    if trim(l) ~= "" then
      table.insert(lines, l)
    end
  end
  local first = rtrim(lines[1] or "")
  if trim(first) == "" then
    ui().fail("빈 내용은 기록하지 않습니다")
  end
  local head
  if first:sub(1, 4) == "[ ] " then
    head = string.format("- [ ] %s %s", hhmm, ltrim(first:sub(5)))
  else
    head = string.format("- %s %s", hhmm, first)
  end
  local out = { head }
  for i = 2, #lines do
    table.insert(out, "  " .. rtrim(lines[i]))
  end
  return table.concat(out, "\n")
end

---오늘 daily의 `## 기록` 끝에 ENTRY를 넣고 저장한다 (열린 버퍼 규칙은 work-kb.fileio). 파일 경로.
---@param entry string
---@param t? integer
---@return string
function M.append_entry(entry, t)
  local file = daily().ensure_today(false, t)
  fileio().append(file, entry)
  return file
end

---------------------------------------------------------------- 캡처·inbox 버퍼

M.CAPTURE_BUFFER = "*work-kb-capture*"
M.INBOX_BUFFER = "*work-kb-inbox*"
M.HINT = "저장: C-c C-c  취소: C-c C-k"

-- bufnr → { kind = "daily"|"inbox", title, prev_win, restore }
M._state = {}

local function call_entry(fn)
  return function()
    require("work-kb").call({ module = "work-kb.capture", fn = fn, name = "capture-" .. fn })
  end
end

local function window_of(bufnr)
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
    if vim.api.nvim_win_get_buf(win) == bufnr then
      return win
    end
  end
  return nil
end

local function buffer_empty(bufnr)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  return #lines == 0 or (#lines == 1 and lines[1] == "")
end

---캡처 버퍼를 연다 (Emacs work-kb--open-capture-buffer): 아래 창에 보이고 초점을 옮긴다.
---INITIAL은 버퍼가 비어 있을 때만 넣는다.
function M._open_buffer(name, kind, title, initial)
  local prev_win = vim.api.nvim_get_current_win()
  local restore = vim.fn.winrestcmd()
  local bufnr = proc().get_buffer(name, true)
  local win = window_of(bufnr)
  local old = M._state[bufnr]
  if win and old and old.prev_win and vim.api.nvim_win_is_valid(old.prev_win) then
    -- 이미 보이는 캡처 버퍼로 돌아가는 것: 처음 연 때의 창 배치를 그대로 기억한다.
    prev_win, restore = old.prev_win, old.restore
  end
  if win then
    vim.api.nvim_set_current_win(win)
  else
    local height = math.max(8, math.floor(vim.o.lines * 0.3))
    win = vim.api.nvim_open_win(bufnr, true, { split = "below", win = -1, height = height })
  end
  vim.bo[bufnr].modifiable = true
  if vim.bo[bufnr].filetype ~= "markdown" then
    vim.bo[bufnr].filetype = "markdown"
  end
  vim.wo[win].winbar = M.HINT
  for _, mode in ipairs({ "n", "i" }) do
    vim.keymap.set(mode, "<C-c><C-c>", call_entry("finish"), { buffer = bufnr, desc = "work-kb: 캡처 저장" })
    vim.keymap.set(mode, "<C-c><C-k>", call_entry("cancel"), { buffer = bufnr, desc = "work-kb: 캡처 취소" })
  end
  M._state[bufnr] = { kind = kind, title = title, prev_win = prev_win, restore = restore }
  if initial and initial ~= "" and buffer_empty(bufnr) then
    vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, vim.split(initial, "\n", { plain = true }))
  end
  return bufnr
end

---캡처 버퍼를 닫고 연 때의 창 배치로 돌아간다.
local function close_buffer(bufnr, st)
  M._state[bufnr] = nil
  pcall(vim.cmd.stopinsert)
  if vim.api.nvim_buf_is_valid(bufnr) then
    vim.api.nvim_buf_delete(bufnr, { force = true })
  end
  if st and st.prev_win and vim.api.nvim_win_is_valid(st.prev_win) then
    vim.api.nvim_set_current_win(st.prev_win)
    if st.restore then
      pcall(vim.cmd, st.restore)
    end
  end
end

local function current_capture()
  local bufnr = vim.api.nvim_get_current_buf()
  local st = M._state[bufnr]
  if not st then
    ui().fail("work-kb: 캡처 버퍼가 아닙니다")
  end
  return bufnr, st
end

---캡처 버퍼(C-c C-c): 내용을 저장하고 닫는다. 비었으면 사용자 오류 (버퍼는 그대로).
function M.finish()
  local bufnr, st = current_capture()
  local text = table.concat(vim.api.nvim_buf_get_lines(bufnr, 0, -1, false), "\n")
  if trim(text) == "" then
    ui().fail("빈 내용입니다 (취소: C-c C-k)")
  end
  if st.kind == "inbox" then
    local path = M.write_inbox(st.title, text)
    ui().notify("work-kb: inbox에 저장했습니다 — " .. fileio().basename(path))
  else
    M.capture(text)
  end
  close_buffer(bufnr, st)
end

---캡처 버퍼(C-c C-k): 저장하지 않고 닫는다.
function M.cancel()
  local bufnr, st = current_capture()
  close_buffer(bufnr, st)
  ui().notify("work-kb: 취소했습니다")
end

---캡처 버퍼를 연다. PREFILL(비주얼 선택 등)은 빈 버퍼에만 넣는다. 버퍼 번호.
---@param prefill? string
---@return integer
function M.open(prefill)
  return M._open_buffer(M.CAPTURE_BUFFER, "daily", nil, prefill)
end

---c: TEXT가 있으면 오늘 daily의 `## 기록` 끝에 바로 넣고 넣은 줄을 돌려준다.
---없으면 캡처 버퍼를 연다 (C-c C-c 저장, C-c C-k 취소) — 버퍼 번호를 돌려준다.
---@param text? string
function M.capture(text)
  if text == nil then
    return M.open(nil)
  end
  local t = time().now()
  local entry = M.format(text, t)
  M.append_entry(entry, t)
  ui().notify("work-kb: 기록했습니다 — " .. vim.split(entry, "\n", { plain = true })[1])
  return entry
end

---------------------------------------------------------------- 이미지 (D12, D16)

M.IMAGE_EXTENSIONS = { "png", "jpg", "jpeg", "gif", "webp", "bmp" }

local function vault()
  return config().vault()
end

---<볼트>/raw/assets/YYYY-MM-DD-HHMM-<NAME>.<EXT> (겹치면 -2, -3 …). NAME이 비면 capture.
function M.asset_path(name, ext, t)
  local base = trim(name or "") == "" and "capture" or name
  local path = string.format(
    "%s/raw/assets/%s-%s.%s",
    vault(),
    time().asset_stamp(t),
    names().link_safe_name(base),
    ext:lower()
  )
  return names().unique_path(path)
end

---ASSET의 볼트 기준 상대 경로 (슬래시).
local function vault_relative(path)
  local v = vault()
  local p = vim.fs.normalize(path)
  if p:sub(1, #v + 1):lower() == (v .. "/"):lower() then
    return p:sub(#v + 2)
  end
  return p
end

---`- HH:MM ![[raw/assets/…]] 설명` 캡처 줄.
function M.embed_entry(asset, text, t)
  local desc = trim(text or "")
  return M.format("![[" .. vault_relative(asset) .. "]]" .. (desc == "" and "" or (" " .. desc)), t)
end

local function finish_image(asset, text, t)
  M.append_entry(M.embed_entry(asset, text, t), t)
  ui().notify("work-kb: 이미지를 기록했습니다 — " .. fileio().basename(asset))
  return asset
end

---Windows PowerShell 5.1 (pwsh 7 아님): %SystemRoot%/System32/WindowsPowerShell/v1.0/powershell.exe.
function M.powershell()
  local root = (vim.env.SystemRoot or "C:/Windows"):gsub("\\", "/"):gsub("/+$", "")
  return root .. "/System32/WindowsPowerShell/v1.0/powershell.exe"
end

-- 부모 pwsh 7의 모듈 경로·실행 정책이 5.1로 새지 않게 지운다 (D12 보완). proc._spawn의 env: false는 지움.
M.PS_ENV = { PSModulePath = false, PSExecutionPolicyPreference = false }

---출력 해독에 시험할 코드 페이지: 활성 OEM, 활성 ANSI, 사용자 로캘의 기본 OEM·ANSI (65001과 중복 제외).
---시스템 코드 페이지가 UTF-8(베타 옵션)이어도 로캘 기본값(cp949)을 시험한다.
function M._codepages()
  local ok, list = pcall(function()
    local ffi = require("ffi")
    pcall(
      ffi.cdef,
      [[
      unsigned int GetOEMCP(void);
      unsigned int GetACP(void);
      int GetLocaleInfoEx(const wchar_t* name, unsigned int lctype, wchar_t* data, int cch);
    ]]
    )
    local out = { tonumber(ffi.C.GetOEMCP()), tonumber(ffi.C.GetACP()) }
    for _, lctype in ipairs({ 0x000B, 0x1004 }) do -- LOCALE_IDEFAULTCODEPAGE, LOCALE_IDEFAULTANSICODEPAGE
      local buf = ffi.new("wchar_t[16]")
      local n = ffi.C.GetLocaleInfoEx(nil, lctype, buf, 16)
      local digits = {}
      for i = 0, n - 2 do
        digits[#digits + 1] = string.char(buf[i])
      end
      table.insert(out, tonumber(table.concat(digits)))
    end
    return out
  end)
  local result, seen = {}, {}
  for _, cp in ipairs(ok and list or {}) do
    if cp and cp ~= 65001 and cp ~= 0 and not seen[cp] then
      seen[cp] = true
      table.insert(result, cp)
    end
  end
  return result
end

---PowerShell 출력: UTF-8이 아니면 콘솔 코드 페이지(cp949 등)로 보고 바꾼다. CRLF → LF, 앞뒤 공백 제거.
local function decode_output(raw)
  local text = raw
  if not fileio().is_utf8(text) then
    for _, cp in ipairs(M._codepages()) do
      local converted = vim.iconv(text, "cp" .. cp, "utf-8")
      if converted and converted ~= "" and fileio().is_utf8(converted) then
        text = converted
        break
      end
    end
  end
  return trim((text:gsub("\r\n", "\n")))
end

---clip_image.ps1을 OUT_REL(루트 기준 ASCII 상대 경로)로 실행하고 기다린다. 종료 코드(시간 초과 nil), 출력.
function M._call_clip(out_rel, root)
  local p = proc()
  local cmd = {
    M.powershell(),
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-STA",
    "-File",
    "scripts/clip_image.ps1",
    "-Out",
    out_rel,
  }
  local chunks = {}
  local done, code = false, nil
  local handle = p._spawn({
    cmd = cmd,
    cwd = root,
    merge = true,
    env = vim.deepcopy(M.PS_ENV),
    on_stdout = function(data)
      table.insert(chunks, data)
    end,
    on_exit = function(c)
      code = c
      done = true
    end,
  })
  vim.wait(p.default_timeout_ms, function()
    return done
  end, 10)
  if not done then
    pcall(handle.kill)
    code = nil
  end
  return code, decode_output(table.concat(chunks))
end

---C: 클립보드 이미지를 raw/assets/에 저장하고 `![[…]] 입력` 줄을 캡처한다. 자산 경로 (없으면 nil).
---INPUT(이름·설명)이 없으면 이미지가 있을 때만 묻는다.
---@param input? string
---@return string|nil
function M.clipboard_image(input)
  local t = time().now()
  local root = config().root()
  local out_rel = string.format(".state/tmp/clip-%s.png", time().clip_stamp(t))
  local out_abs = root .. "/" .. out_rel
  local fio = fileio()
  fio.mkdir_p(fio.dirname(out_abs))
  local code, output = M._call_clip(out_rel, root)
  if code == 2 then
    ui().notify("클립보드에 이미지가 없습니다")
    return nil
  end
  if code == 0 and fio.exists(out_abs) then
    if input == nil then
      input = ui().input("이미지 이름·설명 (비우면 capture): ")
      if input == nil then
        vim.uv.fs_unlink(out_abs)
        ui().notify("work-kb: 취소했습니다")
        return nil
      end
    end
    local asset = M.asset_path(input, "png", t)
    fio.move_new(out_abs, asset)
    return finish_image(asset, input, t)
  end
  if fio.exists(out_abs) then
    vim.uv.fs_unlink(out_abs)
  end
  ui().notify(
    string.format(
      "클립보드 이미지를 저장하지 못했습니다 (종료 %s). F(파일)로 캡처하세요. %s",
      code == nil and "nil" or tostring(code),
      output or ""
    )
  )
  return nil
end

---파일 탐색 버퍼(oil, netrw)에서 커서 아래 파일 (보통 파일일 때만). 없으면 nil.
function M._browser_file()
  local ft = vim.bo.filetype
  local candidate
  if ft == "oil" then
    local ok, oil = pcall(require, "oil")
    if ok and type(oil) == "table" then
      local okd, dir = pcall(oil.get_current_dir)
      local oke, entry = pcall(oil.get_cursor_entry)
      if okd and oke and type(dir) == "string" and type(entry) == "table" and entry.name then
        candidate = dir .. entry.name
      end
    end
  elseif ft == "netrw" then
    local dir = vim.b.netrw_curdir
    local ok, word = pcall(vim.fn["netrw#Call"], "NetrwGetWord")
    if not ok or type(word) ~= "string" or word == "" then
      word = vim.fn.expand("<cfile>")
    end
    if type(dir) == "string" and dir ~= "" and word ~= "" then
      candidate = dir .. "/" .. word
    end
  end
  if candidate then
    candidate = vim.fs.normalize(candidate)
    if fileio().is_file(candidate) then
      return candidate
    end
  end
  return nil
end

local function is_image_ext(ext)
  for _, e in ipairs(M.IMAGE_EXTENSIONS) do
    if e == ext then
      return true
    end
  end
  return false
end

---F: 이미지 FILE을 raw/assets/로 복사(덮어쓰지 않음)하고 `![[…]] TEXT` 줄을 캡처한다. 자산 경로.
---FILE이 없으면 `이미지 파일: `(oil/netrw 커서 아래 파일이 처음 값)과 `설명 (선택): `을 묻는다.
---@param file? string
---@param text? string
---@return string|nil
function M.image_file(file, text)
  if file == nil then
    file = ui().input("이미지 파일: ", { initial = M._browser_file() or "", completion = "file" })
    if file == nil then
      return nil
    end
    text = ui().input("설명 (선택): ")
    if text == nil then
      return nil
    end
  end
  local t = time().now()
  local full = vim.fs.normalize(vim.fn.fnamemodify(file, ":p"))
  local fio = fileio()
  if not fio.is_file(full) then
    ui().fail("파일이 없습니다: " .. full)
  end
  local filename = fio.basename(full)
  local stem, ext = filename:match("^(.+)%.([^.]*)$")
  if not stem then
    stem, ext = filename, ""
  end
  ext = ext:lower()
  if not is_image_ext(ext) then
    ui().fail(string.format("이미지 파일이 아닙니다 (%s): %s", table.concat(M.IMAGE_EXTENSIONS, ", "), full))
  end
  local asset = M.asset_path(stem, ext, t)
  fio.copy_new(full, asset)
  return finish_image(asset, text, t)
end

---------------------------------------------------------------- inbox (§5.2, D16)

---TITLE이 비면 BODY의 첫 내용 줄(앞의 `#`·`-`·`*` 표시 제거) 40글자, 그것도 비면 "메모".
function M.inbox_title(title, body)
  title = trim(title or "")
  if title ~= "" then
    return title
  end
  local first
  for _, l in ipairs(split_lines(body or "")) do
    if trim(l) ~= "" then
      first = l
      break
    end
  end
  local line = first or ""
  local stripped, n = line:gsub("^[ \t]*#+[ \t]+", "", 1)
  if n == 0 then
    stripped = line:gsub("^[ \t]*[-*][ \t]+", "", 1)
  end
  line = trim(stripped)
  if line == "" then
    return "메모"
  end
  local nm = names()
  if nm.length(line) > 40 then
    return rtrim(nm.prefix(line, 40))
  end
  return line
end

---inbox 파일 이름: YYYYMMDD-HHMM-<안전한 제목>.md
function M.inbox_file_name(title, t)
  return string.format("%s-%s.md", time().inbox_stamp(t or time().now()), names().link_safe_name(title))
end

---YAML 큰따옴표 값: [\r\n\t]+ → 공백 하나, `"`·`\` 이스케이프.
function M.yaml_quote(text)
  local s = text:gsub("[\r\n\t]+", " ")
  s = s:gsub('["\\]', function(c)
    return "\\" .. c
  end)
  return '"' .. s .. '"'
end

---inbox/에 새 메모를 UTF-8·LF로 쓰고 경로를 돌려준다. 있는 파일은 덮지 않는다 (-2, -3 …).
---@param title string|nil
---@param body string|nil
---@param t? integer
---@return string
function M.write_inbox(title, body, t)
  t = t or time().now()
  body = trim(body or "")
  title = M.inbox_title(title, body)
  local path = names().unique_path(vault() .. "/inbox/" .. M.inbox_file_name(title, t))
  local content = table.concat({
    "---\n",
    "created: ",
    time().iso(t),
    "\n",
    "title: ",
    M.yaml_quote(title),
    "\n",
    "---\n",
    body == "" and "" or (body .. "\n"),
  })
  fileio().create_exclusive(path, content)
  return path
end

---i: 제목을 묻고 본문 버퍼 *work-kb-inbox*를 연다 (C-c C-c 저장). PREFILL은 빈 버퍼에만 넣는다.
---제목을 묻다 취소하면 아무것도 열지 않는다.
---@param prefill? string
---@return integer|nil
function M.inbox(prefill)
  local title = ui().input("제목 (비우면 본문 첫 줄): ")
  if title == nil then
    ui().notify("work-kb: 취소했습니다")
    return nil
  end
  return M._open_buffer(M.INBOX_BUFFER, "inbox", title, prefill)
end

return M
