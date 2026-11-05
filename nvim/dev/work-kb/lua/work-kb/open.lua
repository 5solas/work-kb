-- o (커서 위치 열기): Emacs work-kb-open-at-point와 그 도우미.
-- 커서 줄을 세 번 훑는다 (위키링크 → Jira 키 → @이름, 먼저 맞은 것). 위치는 바이트 열이고
-- Emacs 포인트처럼 일치 [s, e)에서 s <= 커서 <= e면 커서가 그 위에 있다고 본다 (끝 바로 뒤 포함).
-- jira.lua가 thing_at_point·link_key·open_key·key_on_line을 쓴다.

local M = {}

local uv = vim.uv

local function config()
  return require("work-kb.config")
end

local function proc()
  return require("work-kb.proc")
end

local function ui()
  return require("work-kb.ui")
end

---Jira 키 (대소문자 구분). Lua 패턴.
M.KEY_PATTERN = "[A-Z][A-Z0-9_]+%-[0-9]+"
local KEY_EXACT = "^" .. M.KEY_PATTERN .. "$"

---S 전체가 Jira 키인가.
function M.is_key(s)
  return type(s) == "string" and s:match(KEY_EXACT) ~= nil
end

local function trim(s)
  return (s:gsub("^[ \t\n\r]+", ""):gsub("[ \t\n\r]+$", ""))
end

local function ascii_lower(s)
  return (s:gsub("[A-Z]", function(c)
    return string.char(c:byte() + 32)
  end))
end

---------------------------------------------------------------- 커서 위치의 대상

-- 키 앞뒤에 붙으면 키가 아닌 글자 [A-Za-z0-9_-] (바이트 하나로 본다: 여러 바이트 글자의
-- 바이트는 모두 0x80 이상이라 걸리지 않는다).
local function key_char(byte)
  return byte ~= nil
    and ((byte >= 48 and byte <= 57) or (byte >= 65 and byte <= 90) or (byte >= 97 and byte <= 122) or byte == 95 or byte == 45)
end

---LINE의 1부터 센 바이트 S..E(포함)의 키 후보가 홀로 선 키인가 (work-kb--key-bounded-p).
local function key_bounded(line, s, e)
  return not key_char(s > 1 and line:byte(s - 1) or nil) and not key_char(line:byte(e + 1))
end

---`@` 앞에 오면 이름이 아닌 글자 [A-Za-z0-9._%+-] (메일 주소 제외).
local function mail_char(byte)
  return byte ~= nil
    and (
      (byte >= 48 and byte <= 57)
      or (byte >= 65 and byte <= 90)
      or (byte >= 97 and byte <= 122)
      or byte == 46
      or byte == 95
      or byte == 37
      or byte == 43
      or byte == 45
    )
end

---LINE의 바이트 I에서 UTF-8 글자 하나를 읽는다. 코드 포인트(올바르지 않으면 nil)와 길이.
local function char_at(line, i)
  local b = line:byte(i)
  if not b then
    return nil, 0
  end
  local len, cp
  if b < 0x80 then
    return b, 1
  elseif b >= 0xC2 and b <= 0xDF then
    len, cp = 2, b - 0xC0
  elseif b >= 0xE0 and b <= 0xEF then
    len, cp = 3, b - 0xE0
  elseif b >= 0xF0 and b <= 0xF4 then
    len, cp = 4, b - 0xF0
  else
    return nil, 1
  end
  for k = 1, len - 1 do
    local c = line:byte(i + k)
    if not c or c < 0x80 or c > 0xBF then
      return nil, 1
    end
    cp = cp * 64 + (c - 0x80)
  end
  return cp, len
end

---Emacs [[:alnum:]_]: ASCII 영문·숫자·`_`, Latin-1의 글자, 그 위는 Neovim 글자 부류가
---공백(0)·문장 부호(1)·이모지(3)가 아닌 것 (한글 음절·자모, 한자, 가나, 라틴 확장 …).
local function name_char(cp, s)
  if cp == nil then
    return false
  end
  if cp < 0x80 then
    return (cp >= 48 and cp <= 57) or (cp >= 65 and cp <= 90) or (cp >= 97 and cp <= 122) or cp == 95
  end
  if cp < 0x100 then
    return cp == 0xAA or cp == 0xB5 or cp == 0xBA or (cp >= 0xC0 and cp <= 0xFF and cp ~= 0xD7 and cp ~= 0xF7)
  end
  local class = vim.fn.charclass(s)
  return class ~= 0 and class ~= 1 and class ~= 3
end

local function link_at(line, col)
  local init = 1
  while true do
    local i = line:find("[[", init, true)
    if not i then
      return nil
    end
    local p = i + 2
    local target = line:match("^[^%]|#\n]+", p)
    local matched = false
    if target then
      local q = p + #target
      local heading = line:match("^#[^%]|\n]*", q)
      if heading then
        q = q + #heading
      end
      local alias = line:match("^|[^%]\n]*", q)
      if alias then
        q = q + #alias
      end
      if line:sub(q, q + 1) == "]]" then
        matched = true
        local s0, e0 = i - 1, q + 1 -- [s0, e0) 0부터
        if s0 <= col and col <= e0 then
          return trim(target)
        end
        init = q + 2
      end
    end
    if not matched then
      init = i + 1
    end
  end
end

local function key_at(line, col)
  local init = 1
  while true do
    local s, e = line:find(M.KEY_PATTERN, init)
    if not s then
      return nil
    end
    if s - 1 <= col and col <= e and key_bounded(line, s, e) then
      return line:sub(s, e)
    end
    init = e + 1
  end
end

local function person_at(line, col)
  local init = 1
  while true do
    local i = line:find("@", init, true)
    if not i then
      return nil
    end
    local j = i + 1
    while true do
      local cp, len = char_at(line, j)
      if len == 0 or not name_char(cp, line:sub(j, j + len - 1)) then
        break
      end
      j = j + len
    end
    if j > i + 1 then
      if i - 1 <= col and col <= j - 1 and not mail_char(i > 1 and line:byte(i - 1) or nil) then
        return line:sub(i + 1, j - 1)
      end
      init = j
    else
      init = i + 1
    end
  end
end

---커서 위치의 대상 (work-kb--thing-at-point). 없으면 nil.
---돌려주는 표: { kind = "link"|"key"|"person", value = 대상, [1] = kind, [2] = value }.
---LINE·COL(0부터 센 바이트 열)을 주지 않으면 현재 창의 커서 줄·열.
---@param line? string
---@param col? integer
---@return {kind: string, value: string}|nil
function M.thing_at_point(line, col)
  if line == nil then
    line = vim.api.nvim_get_current_line()
  end
  if col == nil then
    col = vim.api.nvim_win_get_cursor(0)[2]
  end
  local kind, value
  local target = link_at(line, col)
  if target then
    kind, value = "link", target
  else
    local key = key_at(line, col)
    if key then
      kind, value = "key", key
    else
      local name = person_at(line, col)
      if name then
        kind, value = "person", name
      end
    end
  end
  if not kind then
    return nil
  end
  -- 대상이 정확히 키인 링크는 키다 ([[OPS-3|배포]]).
  if kind == "link" and M.is_key(value) then
    kind = "key"
  end
  return { kind = kind, value = value, kind, value }
end

---LINE(기본 현재 줄)의 첫 홀로 선 Jira 키 (work-kb--key-on-line). 없으면 nil.
function M.key_on_line(line)
  if line == nil then
    line = vim.api.nvim_get_current_line()
  end
  local init = 1
  while true do
    local s, e = line:find(M.KEY_PATTERN, init)
    if not s then
      return nil
    end
    if key_bounded(line, s, e) then
      return line:sub(s, e)
    end
    init = e + 1
  end
end

---위키링크 대상이 Jira 키를 가리키면 그 키 (앞의 `jira/`·뒤의 `.md`는 대소문자 없이 뗀다).
---[[jira/KB-12]]·[[KB-12.md]] → KB-12, [[KB-12-설계]] → nil.
function M.link_key(target)
  local name = trim(target or "")
  if ascii_lower(name:sub(1, 5)) == "jira/" then
    name = name:sub(6)
  end
  if #name >= 3 and ascii_lower(name:sub(-3)) == ".md" then
    name = name:sub(1, -4)
  end
  if M.is_key(name) then
    return name
  end
  return nil
end

---------------------------------------------------------------- 파일

local function stat(path)
  return uv.fs_stat(path)
end

local function exists(path)
  return stat(path) ~= nil
end

local function is_regular(path)
  local st = stat(path)
  return st ~= nil and st.type == "file"
end

local function is_dir(path)
  local st = stat(path)
  return st ~= nil and st.type == "directory"
end

local function read_head(path, size)
  local fd = uv.fs_open(path, "r", 438)
  if not fd then
    return nil
  end
  local data = uv.fs_read(fd, size, 0)
  uv.fs_close(fd)
  return data
end

local function stub_value(rest)
  rest = rest:gsub("^['\"]", "", 1)
  for _, word in ipairs({ "true", "yes", "on" }) do
    if rest:sub(1, #word) == word then
      local tail = rest:sub(#word + 1):gsub("^['\"]", "", 1):gsub("^[ \t]*", "")
      if tail == "" or tail == "\r" or tail:sub(1, 1) == "#" then
        return true
      end
    end
  end
  return false
end

---PATH(jira/KEY.md)의 front matter에 `stub: true`가 있는가 (work-kb--jira-stub-p).
---앞 8192바이트만 본다. BOM·CRLF·대소문자 허용, front matter 밖의 `stub:`은 보지 않는다.
function M.is_stub(path)
  local data = read_head(path, 8192)
  if not data or data == "" then
    return false
  end
  if data:sub(1, 3) == "\239\187\191" then
    data = data:sub(4)
  end
  local first = true
  for line in (data .. "\n"):gmatch("([^\n]*)\n") do
    if first then
      if not line:match("^%-%-%-[ \t]*\r?$") then
        return false
      end
      first = false
    elseif line:match("^%-%-%-[ \t]*\r?$") or line:match("^%.%.%.[ \t]*\r?$") then
      return false
    else
      local rest = ascii_lower(line):match("^stub[ \t]*:[ \t]*(.*)$")
      if rest and stub_value(rest) then
        return true
      end
    end
  end
  return false
end

local function same_path(a, b)
  return vim.fs.normalize(a):lower() == vim.fs.normalize(b):lower()
end

---FILE을 연 버퍼 (없으면 nil).
local function file_buffer(file)
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    local name = vim.api.nvim_buf_get_name(b)
    if name ~= "" and vim.bo[b].buftype == "" and same_path(name, file) then
      return b
    end
  end
  return nil
end

---BUF가 수정 없이 디스크보다 낡았으면 묻지 않고 다시 읽는다 (Emacs revert-buffer t t t).
function M.reload_if_stale(buf)
  if not (buf and vim.api.nvim_buf_is_valid(buf) and vim.api.nvim_buf_is_loaded(buf)) then
    return
  end
  if vim.bo[buf].modified then
    return
  end
  -- 'autoread'를 잠시 켜고 :checktime: 수정 없는 버퍼가 낡았으면 묻지 않고 다시 읽는다.
  -- 'autoread'는 전역-지역 옵션이라 지역 값이 없으면 vim.bo가 nil을 준다 (그때는 지역 값을 다시 지운다).
  local had = vim.bo[buf].autoread
  if had ~= true then
    vim.bo[buf].autoread = true
  end
  pcall(vim.cmd, "silent checktime " .. buf)
  if had ~= true and vim.api.nvim_buf_is_valid(buf) then
    if had == nil then
      pcall(vim.api.nvim_buf_call, buf, function()
        vim.cmd("set autoread<") -- 전역-지역 옵션: 지역 값을 지운다 (:setlocal x<는 전역 값을 복사)
      end)
    else
      vim.bo[buf].autoread = had
    end
  end
end

---FILE을 지금 창에 연다. 이미 연 버퍼가 수정 없이 낡았으면 묻지 않고 다시 읽는다. 그 버퍼 번호.
function M.visit(file)
  local live = file_buffer(file)
  if live then
    M.reload_if_stale(live)
    vim.api.nvim_win_set_buf(0, live)
    return live
  end
  vim.cmd("edit " .. vim.fn.fnameescape(file))
  return vim.api.nvim_get_current_buf()
end

---위키링크 대상의 파일 경로 (work-kb--resolve-link). 못 찾으면 nil.
function M.resolve_link(target)
  local vault = config().vault()
  local base_of_target = target:match("[^/\\]*$") or target
  local dot = base_of_target:match("^.*()%.")
  local has_ext = dot ~= nil and dot > 1
  local name = has_ext and target or (target .. ".md")
  for _, dir in ipairs({ "", "daily/", "wiki/", "wiki/projects/", "wiki/people/", "wiki/topics/", "jira/", "inbox/" }) do
    local path = vault .. "/" .. dir .. name
    if is_regular(path) then
      return path
    end
  end
  local base = ascii_lower(name:match("[^/\\]*$") or name)
  -- directory-files-recursively: 하위 폴더의 결과가 먼저, 그다음 이 폴더의 파일 (이름순).
  local function search(dir)
    local handle = uv.fs_scandir(dir)
    if not handle then
      return nil
    end
    local entries = {}
    while true do
      local entry, kind = uv.fs_scandir_next(handle)
      if not entry then
        break
      end
      if kind == nil then
        local st = uv.fs_lstat(dir .. "/" .. entry)
        kind = st and st.type or "file"
      end
      if kind == "link" or kind == "junction" then
        -- 링크된 폴더는 따라가지 않고 (Emacs file-symlink-p), 링크된 파일은 파일로 본다.
        local st = uv.fs_stat(dir .. "/" .. entry)
        kind = (st and st.type == "directory") and "linkdir" or "file"
      end
      table.insert(entries, { name = entry, kind = kind, key = kind == "directory" and (entry .. "/") or entry })
    end
    table.sort(entries, function(a, b)
      return a.key < b.key
    end)
    for _, e in ipairs(entries) do
      if e.kind == "directory" then
        local found = search(dir .. "/" .. e.name)
        if found then
          return found
        end
      end
    end
    for _, e in ipairs(entries) do
      if e.kind ~= "directory" and e.kind ~= "linkdir" and ascii_lower(e.name) == base then
        return dir .. "/" .. e.name
      end
    end
    return nil
  end
  for _, dir in ipairs({ "daily", "inbox", "wiki", "jira", "raw" }) do
    local full = vault .. "/" .. dir
    if is_dir(full) then
      local found = search(full)
      if found then
        return found
      end
    end
  end
  return nil
end

---------------------------------------------------------------- Jira 키 열기

---지금 `kb.py sync --key`가 도는 키 (같은 키를 두 번 돌리지 않는다).
M._syncing = {}

function M.jira_file(key)
  return config().vault() .. "/jira/" .. key .. ".md"
end

function M.sync_buffer_name(key)
  return string.format("*work-kb-sync %s*", key)
end

---동기화 실패 뒤: FILE이 있으면 열고, 없으면 `kb.py link --stub KEY`로 만든 뒤 연다.
local function key_fallback(key, file, why)
  local sync_name = M.sync_buffer_name(key)
  if exists(file) then
    M.visit(file)
    ui().notify(string.format("work-kb: %s 동기화 실패 (%s). 저장된 페이지를 엽니다. %s 버퍼를 보세요", key, why, sync_name))
    return
  end
  local p = proc()
  p.run(p.kb_args("link", "--stub", key), {
    on_exit = function(code)
      if exists(file) then
        M.visit(file)
        ui().notify(string.format("work-kb: %s 동기화 실패 (%s). stub을 만들어 엽니다. %s 버퍼를 보세요", key, why, sync_name))
      else
        ui().notify(
          string.format(
            "work-kb: %s 동기화 실패 (%s), stub 생성도 실패 (%s). %s, %s 버퍼를 보세요",
            key,
            why,
            p.exit_reason(code),
            sync_name,
            p.OUTPUT
          )
        )
      end
    end,
  })
end

local function after_key_sync(key, file, code)
  M._syncing[key] = nil
  local p = proc()
  local sync_buf = p.get_buffer(M.sync_buffer_name(key), false)
  if code == 0 and exists(file) and not M.is_stub(file) then
    if sync_buf then
      pcall(vim.api.nvim_buf_delete, sync_buf, { force = true })
    end
    M.visit(file)
    ui().notify(string.format("work-kb: %s 동기화했습니다", key))
    return
  end
  local last = sync_buf and p.last_line(sync_buf) or nil
  local why = (code == 0 and "Jira에서 받아 오지 못함" or p.exit_reason(code)) .. (last and (" — " .. last) or "")
  key_fallback(key, file, why)
end

---jira/KEY.md를 연다 (work-kb--open-key). 없거나 stub이면 `kb.py sync --key KEY`를 비동기로
---돌리고 끝나면 연다. 실패하면 stub(없으면 `kb.py link --stub KEY`로 만든다)을 열고 이유를 알린다.
---바로 열었으면 버퍼 번호, 동기화를 시작했으면 proc.run 핸들, 이미 도는 중이면 nil.
function M.open_key(key)
  local file = M.jira_file(key)
  if exists(file) and not M.is_stub(file) then
    return M.visit(file)
  end
  if M._syncing[key] then
    ui().notify(string.format("work-kb: %s 동기화가 이미 진행 중입니다 (끝나면 열립니다)", key))
    return nil
  end
  M._syncing[key] = true
  local p = proc()
  local ok, handle = pcall(p.run, p.kb_args("sync", "--key", key), {
    buffer = M.sync_buffer_name(key),
    on_exit = function(code)
      after_key_sync(key, file, code)
    end,
  })
  if not ok then
    M._syncing[key] = nil
    error(handle, 0)
  end
  ui().notify(string.format("work-kb: %s 동기화 중… (끝나면 엽니다)", key))
  return handle
end

---------------------------------------------------------------- o

---커서 위치의 KEY-123 / [[…]] / @이름 페이지를 연다 (work-kb-open-at-point).
function M.open_at_point()
  local thing = M.thing_at_point()
  if not thing then
    ui().fail("커서 위치에 Jira 키·[[링크]]·@이름이 없습니다")
  end
  if thing.kind == "key" then
    return M.open_key(thing.value)
  elseif thing.kind == "person" then
    local file = config().vault() .. "/wiki/people/" .. require("work-kb.names").safe_name(thing.value) .. ".md"
    if not exists(file) then
      ui().fail(string.format("@%s 페이지가 아직 없습니다. l(link)을 실행하세요", thing.value))
    end
    return M.visit(file)
  end
  local file = M.resolve_link(thing.value)
  if not file then
    ui().fail(string.format("페이지가 없습니다: %s", thing.value))
  end
  return M.visit(file)
end

return M
