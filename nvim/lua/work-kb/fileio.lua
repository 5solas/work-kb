-- 바이트 단위 파일 도구와 열린 버퍼 규칙 (capture·daily·inbox가 쓴다).
-- daily 쓰기 허용 목록 1: capture가 `## 기록` 끝에 덧붙인다. 그 밖의 바이트(BOM, CRLF, EOL이 섞인
-- 줄, 마지막 줄의 줄바꿈 유무)는 그대로 둔다.
--
-- 열린 버퍼 규칙 (Emacs work-kb--append-entry, D26):
--   - 버퍼가 수정되지 않았으면 디스크 바이트를 고친 뒤 버퍼를 묻지 않고 다시 읽는다.
--   - 수정된 버퍼는 디스크가 바뀌지 않았을 때만 버퍼 글에 넣고 저장한다 (사람이 쓰던 글도 함께 저장).
--   - 둘 다 바뀌었으면 거부하고 디스크·버퍼를 그대로 둔다. "file changed" 질문은 띄우지 않는다.
-- 파일 입출력은 vim.uv.fs_*만 쓴다 (한글 경로). 불러올 때는 아무 일도 하지 않는다.

local M = {}

local uv = vim.uv

local function ui()
  return require("work-kb.ui")
end

---------------------------------------------------------------- 파일

---PATH의 바이트 그대로. 없거나 읽지 못하면 nil, 오류 문구.
---@param path string
---@return string|nil, string|nil
function M.read_bytes(path)
  local fd, err = uv.fs_open(path, "r", 438)
  if not fd then
    return nil, err
  end
  local st = uv.fs_fstat(fd)
  local size = st and st.size or 0
  local parts = {}
  local offset = 0
  while offset < size do
    local chunk, rerr = uv.fs_read(fd, size - offset, offset)
    if not chunk then
      uv.fs_close(fd)
      return nil, rerr
    end
    if chunk == "" then
      break
    end
    table.insert(parts, chunk)
    offset = offset + #chunk
  end
  uv.fs_close(fd)
  return table.concat(parts)
end

local function write_all(fd, data)
  local offset = 0
  while offset < #data do
    local n, err = uv.fs_write(fd, data:sub(offset + 1), offset)
    if not n then
      return nil, err
    end
    offset = offset + n
  end
  return true
end

---PATH에 DATA를 바이트 그대로 쓴다 (덮어씀, 제자리). 실패하면 오류.
function M.write_bytes(path, data)
  local fd, err = uv.fs_open(path, "w", 438)
  if not fd then
    error(string.format("work-kb: 파일을 쓰지 못했습니다: %s (%s)", path, tostring(err)), 0)
  end
  local ok, werr = write_all(fd, data)
  uv.fs_close(fd)
  if not ok then
    error(string.format("work-kb: 파일을 쓰지 못했습니다: %s (%s)", path, tostring(werr)), 0)
  end
end

function M.exists(path)
  return uv.fs_stat(path) ~= nil
end

---보통 파일인가 (폴더·없음은 거짓).
function M.is_file(path)
  local st = uv.fs_stat(path)
  return st ~= nil and st.type == "file"
end

function M.dirname(path)
  return (path:match("^(.*)[/\\][^/\\]*$")) or "."
end

function M.basename(path)
  return (path:match("([^/\\]*)$"))
end

---폴더를 부모까지 만든다 (mkdir -p).
function M.mkdir_p(dir)
  if dir == nil or dir == "" or M.exists(dir) then
    return
  end
  local parent = M.dirname(dir)
  if parent ~= dir and parent ~= "" and not parent:match("^%a:$") then
    M.mkdir_p(parent)
  end
  local ok, err, code = uv.fs_mkdir(dir, 493)
  if not ok and code ~= "EEXIST" then
    error(string.format("work-kb: 폴더를 만들지 못했습니다: %s (%s)", dir, tostring(err)), 0)
  end
end

---PATH를 새로 만들어 DATA를 쓴다 (부모 폴더 생성). 이미 있으면 오류 — 덮어쓰지 않는다.
function M.create_exclusive(path, data)
  M.mkdir_p(M.dirname(path))
  local fd, err = uv.fs_open(path, "wx", 438)
  if not fd then
    error(string.format("work-kb: 파일을 만들지 못했습니다: %s (%s)", path, tostring(err)), 0)
  end
  local ok, werr = write_all(fd, data)
  uv.fs_close(fd)
  if not ok then
    error(string.format("work-kb: 파일을 쓰지 못했습니다: %s (%s)", path, tostring(werr)), 0)
  end
end

---SRC를 DST로 복사한다. DST가 있으면 오류 (덮어쓰지 않음). 수정 시각은 Windows CopyFile이 보존한다.
function M.copy_new(src, dst)
  M.mkdir_p(M.dirname(dst))
  local ok, err = uv.fs_copyfile(src, dst, { excl = true })
  if not ok then
    error(string.format("work-kb: 파일을 복사하지 못했습니다: %s → %s (%s)", src, dst, tostring(err)), 0)
  end
end

---SRC를 DST로 옮긴다. DST가 있으면 오류 (uv.fs_rename은 덮어쓰므로 쓰지 않는다: 배타 복사 뒤 원본 삭제).
function M.move_new(src, dst)
  M.copy_new(src, dst)
  local ok, err = uv.fs_unlink(src)
  if not ok then
    error(string.format("work-kb: 임시 파일을 지우지 못했습니다: %s (%s)", src, tostring(err)), 0)
  end
end

---------------------------------------------------------------- UTF-8

---S가 올바른 UTF-8인가 (너무 긴 표현, 서로게이트, U+10FFFF 밖은 거짓).
---@param s string
---@return boolean
function M.is_utf8(s)
  local i, n = 1, #s
  while i <= n do
    -- ASCII는 한꺼번에 건너뛴다.
    local j = s:find("[\128-\255]", i)
    if not j then
      return true
    end
    i = j
    local b = s:byte(i)
    local len, min
    if b >= 0xC2 and b <= 0xDF then
      len, min = 2, 0x80
    elseif b >= 0xE0 and b <= 0xEF then
      len, min = 3, 0x800
    elseif b >= 0xF0 and b <= 0xF4 then
      len, min = 4, 0x10000
    else
      return false
    end
    if i + len - 1 > n then
      return false
    end
    local cp = b % (2 ^ (7 - len))
    for k = 1, len - 1 do
      local c = s:byte(i + k)
      if c < 0x80 or c > 0xBF then
        return false
      end
      cp = cp * 64 + (c - 0x80)
    end
    if cp < min or cp > 0x10FFFF or (cp >= 0xD800 and cp <= 0xDFFF) then
      return false
    end
    i = i + len
  end
  return true
end

---------------------------------------------------------------- `## 기록` 찾기와 넣기 (D24, D25)

local BOM = "\239\187\191"

---제목 줄인가: `^﻿?## +기록[ \t\r]*$` (대소문자 구분).
local function is_record_heading(line)
  if line:sub(1, 3) == BOM then
    line = line:sub(4)
  end
  return line:find("^## +기록[ \t\r]*$") ~= nil
end

---다음 2단계 제목인가: `^## +[^ \t\r\n]`.
local function is_level2_heading(line)
  return line:find("^## +[^ \t\r\n]") ~= nil
end

local function is_blank(line)
  return line:find("^[ \t\r]*$") ~= nil
end

M._is_record_heading = is_record_heading

---줄 목록에서 `## 기록` 절의 마지막 내용 줄 번호 (내용이 없으면 제목 줄). 절이 없으면 nil.
---@param lines string[]
---@return integer|nil
function M.record_end_line(lines)
  for i, line in ipairs(lines) do
    if is_record_heading(line) then
      local last = i
      for k = i + 1, #lines do
        if is_level2_heading(lines[k]) then
          break
        end
        if not is_blank(lines[k]) then
          last = k
        end
      end
      return last
    end
  end
  return nil
end

---바이트의 EOL: 줄바꿈이 있고 모든 LF 앞에 CR이 있으면 "dos", 아니면 "unix" (줄바꿈이 없는
---파일도 "unix": Emacs가 읽은 뒤 EOL을 unix로 두는 것과 같다, D25).
function M.eol_mode(bytes)
  if not bytes:find("\n", 1, true) then
    return "unix"
  end
  if bytes:find("^\n") or bytes:find("[^\r]\n") then
    return "unix"
  end
  return "dos"
end

---바이트를 줄로 나눈다. 각 항목 { text = 줄 내용(dos면 끝 CR 제외), stop = 줄 내용 끝 바이트 위치 }.
local function split_lines(bytes, dos)
  local lines = {}
  local start = 1
  local n = #bytes
  while start <= n do
    local nl = bytes:find("\n", start, true)
    local stop = nl and (nl - 1) or n
    if dos and nl and stop >= start and bytes:sub(stop, stop) == "\r" then
      stop = stop - 1
    end
    table.insert(lines, { text = bytes:sub(start, stop), stop = stop })
    if not nl then
      break
    end
    start = nl + 1
  end
  return lines
end

---`## 기록` 절의 마지막 내용 줄 끝 (그 바이트 수, 즉 넣을 자리 앞의 바이트 수). 절이 없으면 nil.
---dos 파일이면 줄 끝의 CR 앞이다.
function M.record_end(bytes)
  local dos = M.eol_mode(bytes) == "dos"
  local lines = split_lines(bytes, dos)
  local texts = {}
  for i, l in ipairs(lines) do
    texts[i] = l.text
  end
  local idx = M.record_end_line(texts)
  return idx and lines[idx].stop or nil
end

---BYTES의 `## 기록` 끝에 ENTRY(줄은 \n으로 이음)를 넣은 새 바이트.
---모두 CRLF인 파일은 CRLF로, 섞인 파일은 바로 앞 줄이 CR로 끝나면 그 줄처럼 CRLF로 넣는다.
---절이 없으면 파일 끝에 "## 기록"을 만든다 (끝 줄바꿈 보충, 비어 있지 않으면 빈 줄 하나).
---@param bytes string
---@param entry string
---@return string
function M.insert_entry(bytes, entry)
  local dos = M.eol_mode(bytes) == "dos"
  local crlf_entry = entry:gsub("\n", "\r\n")
  local pos = M.record_end(bytes)
  if pos then
    local before, after = bytes:sub(1, pos), bytes:sub(pos + 1)
    if dos then
      return before .. "\r\n" .. crlf_entry .. after
    elseif before:sub(-1) == "\r" then
      return before .. "\n" .. crlf_entry .. "\r" .. after
    end
    return before .. "\n" .. entry .. after
  end
  local nl = dos and "\r\n" or "\n"
  local body = dos and crlf_entry or entry
  local out = bytes
  if out ~= "" and out:sub(-1) ~= "\n" then
    out = out .. nl
  end
  if out ~= "" then
    out = out .. nl
  end
  return out .. "## 기록" .. nl .. body .. nl
end

---버퍼 줄 목록(LINES)에 ENTRY를 넣을 자리와 줄. 돌려주는 값: 넣을 위치(0 기반, 그 줄 앞에 넣음),
---넣을 줄 목록, 제목을 새로 만들었는가.
---'fileformat'=unix 버퍼에서 앞 줄이 CR로 끝나면(EOL이 섞인 파일) 새 줄도 CR로 끝낸다.
function M.plan_lines(lines, entry)
  local entry_lines = vim.split(entry, "\n", { plain = true })
  local idx = M.record_end_line(lines)
  if idx then
    if lines[idx]:sub(-1) == "\r" then
      for i, l in ipairs(entry_lines) do
        entry_lines[i] = l .. "\r"
      end
    end
    return idx, entry_lines, false
  end
  local add = {}
  local empty = #lines == 0 or (#lines == 1 and lines[1] == "")
  if not empty then
    table.insert(add, "")
  end
  table.insert(add, "## 기록")
  vim.list_extend(add, entry_lines)
  if empty then
    return 0, add, true, true
  end
  return #lines, add, true
end

---------------------------------------------------------------- 열린 버퍼

local function norm(path)
  return vim.fs.normalize(path):lower()
end

local function realpath(path)
  local real = uv.fs_realpath(path)
  return real and norm(real) or nil
end

---PATH를 연(읽어 들인) 보통 버퍼. 없으면 nil. 경로는 대소문자를 가리지 않고, 정션·8.3 이름도 실제 경로로 맞춘다.
---@param path string
---@return integer|nil
function M.find_buffer(path)
  local target = norm(path)
  local candidates = {}
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(b) and vim.bo[b].buftype == "" then
      local name = vim.api.nvim_buf_get_name(b)
      if name ~= "" then
        if norm(name) == target then
          return b
        end
        table.insert(candidates, { b = b, name = name })
      end
    end
  end
  local real = realpath(path)
  if not real then
    return nil
  end
  local base = M.basename(target)
  for _, c in ipairs(candidates) do
    if M.basename(norm(c.name)) == base and realpath(c.name) == real then
      return c.b
    end
  end
  return nil
end

---버퍼 이름 (메시지용, Emacs buffer-name처럼 파일 이름만).
function M.buffer_label(bufnr)
  return M.basename(vim.api.nvim_buf_get_name(bufnr))
end

-- 충돌을 찾은 버퍼: 사람이 다시 읽거나 저장할 때까지 계속 충돌로 본다
-- (:checktime은 버퍼가 기억하는 디스크 시각을 새 값으로 바꾸므로 두 번째 :checktime은 같은 변경을 다시
-- 알리지 않는다). Neovim 0.12에서 확인: 이 :checktime 뒤에도 사람의 :w는 "file has been changed since
-- reading it" 경고를 그대로 낸다 (쓰기 검사는 읽을 때의 시각을 따로 기억한다). jira의 daily 줄 참조도
-- 이 함수를 쓴다.
local conflicts = {}

local function mark_conflict(bufnr)
  if conflicts[bufnr] then
    return
  end
  conflicts[bufnr] = true
  local group = vim.api.nvim_create_augroup("WorkKbConflict", { clear = false })
  vim.api.nvim_create_autocmd({ "BufReadPost", "BufWritePost", "BufWipeout", "BufUnload" }, {
    group = group,
    buffer = bufnr,
    once = true,
    callback = function()
      conflicts[bufnr] = nil
    end,
  })
end

---:checktime BUFNR을 돌려 디스크 변경을 본다 ("file changed" 질문 없이).
---수정 없는 버퍼가 바뀌었으면 그 자리에서 다시 읽는다 (Neovim 기본 reload: 인코딩·EOL 유지).
---@return string|nil reason FileChangedShell의 v:fcs_reason ("conflict", "changed", …), 변화 없으면 nil
local function checktime(bufnr, choice_for_unmodified)
  local reason
  local group = vim.api.nvim_create_augroup("WorkKbChecktime", { clear = true })
  vim.api.nvim_create_autocmd("FileChangedShell", {
    group = group,
    buffer = bufnr,
    callback = function()
      reason = vim.v.fcs_reason
      if reason == "conflict" or reason == "deleted" then
        vim.v.fcs_choice = ""
      else
        vim.v.fcs_choice = choice_for_unmodified or ""
      end
    end,
  })
  local ok, err = pcall(vim.cmd, "silent checktime " .. bufnr)
  pcall(vim.api.nvim_del_augroup_by_id, group)
  if not ok then
    error(err, 0)
  end
  return reason
end

---수정된 버퍼 BUFNR의 파일이 디스크에서 바뀌었는가 (버퍼와 파일이 모두 바뀜).
---@param bufnr integer
---@return boolean
function M.both_changed(bufnr)
  if not vim.bo[bufnr].modified then
    return false
  end
  if conflicts[bufnr] then
    return true
  end
  local reason = checktime(bufnr, "")
  if reason == "conflict" then
    mark_conflict(bufnr)
    return true
  end
  return false
end

---수정 없는 버퍼가 디스크보다 낡았으면 묻지 않고 다시 읽는다 ('autoread' 여부와 무관).
function M.refresh(bufnr)
  if vim.api.nvim_buf_is_valid(bufnr) and not vim.bo[bufnr].modified then
    checktime(bufnr, "reload")
  end
end

---버퍼를 디스크에서 다시 읽는다 (:edit!, 질문 없음). UTF8이면 ++enc=utf-8 (EOL·BOM은 다시 찾는다).
function M.reload(bufnr, utf8)
  vim.api.nvim_buf_call(bufnr, function()
    vim.cmd("silent keepalt keepjumps edit!" .. (utf8 and " ++enc=utf-8" or ""))
  end)
end

---버퍼를 저장한다. RAW면 Emacs 캡처 저장처럼 저장 훅 없이(noautocmd), 끝 줄바꿈을 보태지 않고.
function M.write_buffer(bufnr, raw)
  vim.api.nvim_buf_call(bufnr, function()
    if raw then
      local fixeol = vim.bo.fixendofline
      vim.bo.fixendofline = false
      local ok, err = pcall(vim.cmd, "silent noautocmd keepalt write")
      vim.bo.fixendofline = fixeol
      if not ok then
        error(err, 0)
      end
    else
      vim.cmd("silent keepalt write")
    end
  end)
end

---------------------------------------------------------------- 덧붙이기

local function not_utf8(path)
  ui().fail(string.format("UTF-8이 아닙니다: %s (UTF-8로 바꿔 저장한 뒤 다시 캡처하세요)", path))
end

local UTF8_ENCODINGS = { ["utf-8"] = true, ["utf8"] = true, [""] = true }

---수정된 열린 버퍼에 ENTRY를 넣고 저장한다 (사람이 쓰던 글도 함께 저장된다).
local function append_to_buffer(bufnr, path, entry, disk)
  local fenc = vim.bo[bufnr].fileencoding
  if not UTF8_ENCODINGS[fenc:lower()] then
    ui().fail(string.format("%s: UTF-8이 아닌 인코딩(%s)으로 열려 있습니다. 저장 후 다시 여세요", M.buffer_label(bufnr), fenc))
  end
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  if not M.is_utf8(table.concat(lines, "\n")) then
    not_utf8(path)
  end
  local at, add, created, replace_empty = M.plan_lines(lines, entry)
  if replace_empty then
    vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, add)
  else
    vim.api.nvim_buf_set_lines(bufnr, at, at, false, add)
  end
  if created then
    vim.bo[bufnr].endofline = true
  end
  if disk and not disk:find("\n", 1, true) then
    -- 줄바꿈이 하나도 없던 파일은 EOL을 알 수 없다: 'fileformats' 첫 값(dos) 대신 LF (D25).
    vim.bo[bufnr].fileformat = "unix"
  end
  M.write_buffer(bufnr, true)
end

---PATH(있는 파일)의 `## 기록` 끝에 ENTRY를 넣는다. 열린 버퍼 규칙은 이 파일 머리말.
---@param path string
---@param entry string
function M.append(path, entry)
  local live = M.find_buffer(path)
  if live and vim.bo[live].modified then
    if M.both_changed(live) then
      ui().fail(string.format("%s: 버퍼와 파일이 모두 바뀌었습니다. 먼저 정리하세요", M.buffer_label(live)))
    end
    local disk = M.read_bytes(path)
    if disk and not M.is_utf8(disk) then
      not_utf8(path)
    end
    append_to_buffer(live, path, entry, disk)
    return path
  end
  local bytes, err = M.read_bytes(path)
  if not bytes then
    error(string.format("work-kb: 파일을 읽지 못했습니다: %s (%s)", path, tostring(err)), 0)
  end
  if not M.is_utf8(bytes) then
    not_utf8(path)
  end
  M.write_bytes(path, M.insert_entry(bytes, entry))
  if live then
    M.reload(live, true)
  end
  return path
end

return M
