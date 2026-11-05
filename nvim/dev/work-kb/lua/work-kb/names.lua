-- 파일명 규칙: core.safe_name / core.link_safe_name / core.unique_path와 같은 규칙 (D16).
-- Emacs work-kb--safe-name 등을 그대로 옮겼다. 길이는 바이트가 아니라 글자(코드 포인트) 수.
--
-- NFC: 한글만 정규화한다. 조합형 자모(L+V, L+V+T)를 Unicode 알고리즘으로 완성형 음절로
-- 합치고, 그 밖의 글(라틴 결합 문자 등)은 바꾸지 않고 그대로 둔다.
-- Lua 패턴의 %a·%l·%s 같은 문자 부류는 로캘을 타므로 쓰지 않고 바이트 범위를 직접 적는다.

local M = {}

---UTF-8 문자열을 글자 목록으로 나눈다. 각 항목 { s = 바이트 조각, cp = 코드 포인트|nil }.
---올바르지 않은 바이트는 한 바이트짜리 글자(cp = nil)로 둔다 (Emacs의 날 바이트처럼).
local function chars(s)
  local out = {}
  local i, n = 1, #s
  while i <= n do
    local b = s:byte(i)
    local len, cp
    if b < 0x80 then
      len, cp = 1, b
    elseif b >= 0xC2 and b <= 0xDF then
      len, cp = 2, b - 0xC0
    elseif b >= 0xE0 and b <= 0xEF then
      len, cp = 3, b - 0xE0
    elseif b >= 0xF0 and b <= 0xF4 then
      len, cp = 4, b - 0xF0
    else
      len, cp = 1, nil
    end
    if len > 1 then
      if i + len - 1 > n then
        len, cp = 1, nil
      else
        for k = 1, len - 1 do
          local c = s:byte(i + k)
          if c < 0x80 or c > 0xBF then
            len, cp = 1, nil
            break
          end
          cp = cp * 64 + (c - 0x80)
        end
        -- 너무 긴 표현·서로게이트·범위 밖은 날 바이트로 본다.
        if cp and ((len == 3 and (cp < 0x800 or (cp >= 0xD800 and cp <= 0xDFFF))) or (len == 4 and (cp < 0x10000 or cp > 0x10FFFF))) then
          len, cp = 1, nil
        end
      end
    end
    table.insert(out, { s = s:sub(i, i + len - 1), cp = cp })
    i = i + len
  end
  return out
end
M._chars = chars

local function encode(cp)
  if cp < 0x80 then
    return string.char(cp)
  elseif cp < 0x800 then
    return string.char(0xC0 + math.floor(cp / 64), 0x80 + cp % 64)
  elseif cp < 0x10000 then
    return string.char(0xE0 + math.floor(cp / 4096), 0x80 + math.floor(cp / 64) % 64, 0x80 + cp % 64)
  end
  return string.char(
    0xF0 + math.floor(cp / 262144),
    0x80 + math.floor(cp / 4096) % 64,
    0x80 + math.floor(cp / 64) % 64,
    0x80 + cp % 64
  )
end

---글자 수 (코드 포인트, 날 바이트는 하나씩).
function M.length(s)
  return #chars(s or "")
end

---앞에서 N글자.
function M.prefix(s, n)
  local list = chars(s or "")
  local parts = {}
  for i = 1, math.min(n, #list) do
    parts[i] = list[i].s
  end
  return table.concat(parts)
end

local S_BASE, L_BASE, V_BASE, T_BASE = 0xAC00, 0x1100, 0x1161, 0x11A7
local L_COUNT, V_COUNT, T_COUNT = 19, 21, 28
local N_COUNT = V_COUNT * T_COUNT
local S_COUNT = L_COUNT * N_COUNT

---한글 NFC. 조합형 자모만 완성형으로 합치고 나머지는 그대로.
function M.nfc(s)
  s = s or ""
  -- 빠른 길: 조합형 자모(U+1100..U+11FF, UTF-8 E1 84..E1 87)가 없으면 그대로.
  if not s:find("\225[\132-\135]") then
    return s
  end
  local list = chars(s)
  local out = {}
  local last -- out의 마지막 항목
  for _, ch in ipairs(list) do
    local cp = ch.cp
    local merged = false
    if cp and last and last.cp then
      local l_index = last.cp - L_BASE
      local v_index = cp - V_BASE
      if l_index >= 0 and l_index < L_COUNT and v_index >= 0 and v_index < V_COUNT then
        local s_cp = S_BASE + (l_index * V_COUNT + v_index) * T_COUNT
        last.cp, last.s = s_cp, encode(s_cp)
        merged = true
      else
        local s_index = last.cp - S_BASE
        local t_index = cp - T_BASE
        if s_index >= 0 and s_index < S_COUNT and s_index % T_COUNT == 0 and t_index > 0 and t_index < T_COUNT then
          local s_cp = last.cp + t_index
          last.cp, last.s = s_cp, encode(s_cp)
          merged = true
        end
      end
    end
    if not merged then
      last = { s = ch.s, cp = ch.cp }
      table.insert(out, last)
    end
  end
  local parts = {}
  for i, ch in ipairs(out) do
    parts[i] = ch.s
  end
  return table.concat(parts)
end

local function ascii_upper(s)
  return (s:gsub("[a-z]", function(c)
    return string.char(c:byte() - 32)
  end))
end

-- Windows 예약 이름 (work-kb--reserved-names).
local RESERVED = {}
for _, name in ipairs({ "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$" }) do
  RESERVED[name] = true
end
for _, base in ipairs({ "COM", "LPT" }) do
  for _, suffix in ipairs({ "1", "2", "3", "4", "5", "6", "7", "8", "9", "\194\185", "\194\178", "\194\179" }) do
    RESERVED[base .. suffix] = true
  end
end
M.RESERVED = RESERVED

---NAME을 Windows 파일명 조각으로 다듬는다. 비면 "_", 예약 이름이면 앞에 "_".
---@param name string|nil
---@param max_len? integer 글자 수 (기본 80)
function M.safe_name(name, max_len)
  max_len = max_len or 80
  local text = M.nfc(name or "")
  text = text:gsub('[<>:"/\\|?*%z\1-\31]', "")
  text = text:gsub("[ \t\n\r\f\v]+", " ")
  text = text:gsub("^[ %.]+", ""):gsub("[ %.]+$", "")
  if M.length(text) > max_len then
    text = M.prefix(text, max_len):gsub("[ %.]+$", "")
  end
  if text == "" then
    return "_"
  end
  local first = text:match("^[^%.]*"):gsub(" +$", "")
  if RESERVED[ascii_upper(first)] then
    return "_" .. text
  end
  return text
end

---safe_name 뒤 위키링크 안에서 뜻이 있는 `[ ] # ^ |`를 `-`로.
function M.link_safe_name(name, max_len)
  return (M.safe_name(name, max_len):gsub("[%[%]#%^|]", "-"))
end

local function exists(path)
  return vim.uv.fs_stat(path) ~= nil
end

---PATH가 있으면 이름-2.ext, 이름-3.ext … 절대 덮어쓰지 않는다.
---@param path string
---@return string
function M.unique_path(path)
  if not exists(path) then
    return path
  end
  local dir, file = path:match("^(.*[/\\])([^/\\]*)$")
  if not dir then
    dir, file = "", path
  end
  local stem, ext = file:match("^(.+)(%.[^%.]*)$")
  if not stem then
    stem, ext = file, ""
  end
  local i = 2
  while true do
    local candidate = string.format("%s%s-%d%s", dir, stem, i, ext)
    if not exists(candidate) then
      return candidate
    end
    i = i + 1
  end
end

return M
