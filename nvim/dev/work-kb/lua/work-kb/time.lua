-- 시각: 현재 시각 이음매와 캡처·inbox·자산 이름에 쓰는 형식.
-- 시각 값은 epoch 초(정수)이고 형식은 모두 로컬 시간이다 (Emacs format-time-string 기본).

local M = {}

---현재 시각 (초). setup({ now = fn })이 있으면 그 함수 (테스트 이음매).
---@return integer
function M.now()
  local fn = require("work-kb.config").get().now
  if fn then
    return fn()
  end
  return os.time()
end

---T의 로컬 UTC 오프셋 "+09:00" 꼴 (%:z).
function M.offset(t)
  t = t or M.now()
  local localt = os.date("*t", t)
  local utc = os.date("!*t", t)
  utc.isdst = localt.isdst
  local diff = os.difftime(t, os.time(utc))
  local sign = diff < 0 and "-" or "+"
  diff = math.abs(diff)
  local minutes = math.floor(diff / 60 + 0.5)
  return string.format("%s%02d:%02d", sign, math.floor(minutes / 60), minutes % 60)
end

---os.date 형식에 %:z(오프셋 +09:00)를 더한 것. strftime에 넘기기 전에 %:z를 바꾼다
---(MSVC strftime은 모르는 지정자에서 프로세스를 죽일 수 있다).
---@param fmt string
---@param t? integer
function M.format(fmt, t)
  t = t or M.now()
  local offset = M.offset(t)
  local parts = {}
  local i = 1
  while i <= #fmt do
    local c = fmt:sub(i, i)
    if c == "%" then
      local nxt = fmt:sub(i + 1, i + 1)
      if nxt == ":" and fmt:sub(i + 2, i + 2) == "z" then
        table.insert(parts, offset)
        i = i + 3
      elseif nxt == "%" then
        table.insert(parts, "%")
        i = i + 2
      elseif nxt ~= "" and ("YmdHMSjaAbBpy"):find(nxt, 1, true) then
        table.insert(parts, os.date("%" .. nxt, t))
        i = i + 2
      else
        error("work-kb: 지원하지 않는 시각 형식: %" .. nxt)
      end
    else
      table.insert(parts, c)
      i = i + 1
    end
  end
  return table.concat(parts)
end

---YYYY-MM-DD (daily 파일 이름, `today --date`).
function M.day(t)
  return M.format("%Y-%m-%d", t)
end

---HH:MM (캡처 줄).
function M.hhmm(t)
  return M.format("%H:%M", t)
end

---YYYY-MM-DD-HHMM (raw/assets 이름 앞부분).
function M.asset_stamp(t)
  return M.format("%Y-%m-%d-%H%M", t)
end

---YYYYMMDD-HHMM (inbox 파일 이름 앞부분).
function M.inbox_stamp(t)
  return M.format("%Y%m%d-%H%M", t)
end

---YYYYMMDD-HHMMSS (클립보드 임시 파일 .state/tmp/clip-<이것>.png).
function M.clip_stamp(t)
  return M.format("%Y%m%d-%H%M%S", t)
end

---YYYY-MM-DDTHH:MM:SS+09:00 (inbox front matter created).
function M.iso(t)
  return M.format("%Y-%m-%dT%H:%M:%S%:z", t)
end

return M
