-- 한글 입력: Neovide에서 입력기(IME)를 삽입·검색 입력 때만 켠다.
-- 일반 모드에서는 입력기가 꺼져 있으므로 한/영 상태와 상관없이 키가 라틴 글자로 들어온다.
--
-- M.setup()은 autocmd를 등록한다 (여러 번 불러도 하나뿐). Neovide가 아니면 아무것도 하지 않는다.
-- require 시점에는 아무것도 하지 않는다.
--
-- 켜는 때: InsertEnter, CmdlineEnter(패턴 / ? @ — @는 input()), TermEnter
-- 끄는 때: 짝이 되는 Leave
-- 알려진 빈틈: f, t, r 뒤에 한글 글자를 주는 경우 (일반 모드라 입력기가 꺼져 있다).
-- 터미널 Neovim은 입력기를 바꿀 수 없다 (docs/NVIM.md). langmap은 신뢰할 수 없는 선택 사항.

local M = {}

local GROUP = "WorkKbIme"

---자동 전환 중인가 (setup에서 설정값으로 정하고 M.toggle()이 뒤집는다).
M.auto = true

local CMDLINE_TYPES = { "/", "?", "@" }

local function set_ime(on)
  if vim.g.neovide then
    vim.g.neovide_input_ime = on and true or false
  end
end

---지금 모드에서 입력기가 켜져 있어야 하는가.
local function wanted_now()
  local mode = vim.api.nvim_get_mode().mode
  local c = mode:sub(1, 1)
  if c == "i" or c == "t" then
    return true
  end
  if c == "c" then
    local ok, t = pcall(vim.fn.getcmdtype)
    return ok and vim.tbl_contains(CMDLINE_TYPES, t)
  end
  return false
end

---두벌식 자모 → QWERTY 키 (langmap). 'langmap'은 일반 모드에서만 번역하고 삽입 모드의 글자는 그대로 둔다.
local JAMO = {
  { "ㅂ", "q" }, { "ㅈ", "w" }, { "ㄷ", "e" }, { "ㄱ", "r" }, { "ㅅ", "t" }, { "ㅛ", "y" }, { "ㅕ", "u" },
  { "ㅑ", "i" }, { "ㅐ", "o" }, { "ㅔ", "p" }, { "ㅁ", "a" }, { "ㄴ", "s" }, { "ㅇ", "d" }, { "ㄹ", "f" },
  { "ㅎ", "g" }, { "ㅗ", "h" }, { "ㅓ", "j" }, { "ㅏ", "k" }, { "ㅣ", "l" }, { "ㅋ", "z" }, { "ㅌ", "x" },
  { "ㅊ", "c" }, { "ㅍ", "v" }, { "ㅠ", "b" }, { "ㅜ", "n" }, { "ㅡ", "m" },
  -- Shift를 누른 자모 (쌍자음, ㅒ ㅖ)
  { "ㅃ", "Q" }, { "ㅉ", "W" }, { "ㄸ", "E" }, { "ㄲ", "R" }, { "ㅆ", "T" }, { "ㅒ", "O" }, { "ㅖ", "P" },
}

---'langmap'에 넣을 문자열 ("ㅂq,ㅈw,...").
function M.langmap_string()
  local parts = {}
  for _, pair in ipairs(JAMO) do
    parts[#parts + 1] = pair[1] .. pair[2]
  end
  return table.concat(parts, ",")
end

---두벌식 langmap을 켠다. 터미널 Neovim 사용자가 직접 부를 수 있다 (setup은 Neovide에서만 부른다).
function M.apply_langmap()
  vim.o.langmap = M.langmap_string()
end

---자동 전환을 켜고 끈다 (<leader>ui). 켜면 지금 모드에 맞춘다. 끄면 현재 상태를 그대로 둔다.
---@param value boolean|nil nil이면 뒤집는다
---@return boolean 바뀐 뒤의 값
function M.toggle(value)
  if value == nil then
    value = not M.auto
  end
  M.auto = value and true or false
  if M.auto then
    set_ime(wanted_now())
  end
  return M.auto
end

function M.setup()
  if not vim.g.neovide then
    return false
  end
  local ok, s = pcall(require, "config.settings")
  local cfg = (ok and type(s) == "table" and type(s.ime) == "table") and s.ime or {}
  M.auto = cfg.auto ~= false

  local group = vim.api.nvim_create_augroup(GROUP, { clear = true })
  -- 명령줄 종류는 pattern이 아니라 ev.match로 가린다: autocmd 패턴에서 ? 는 "아무 글자 하나"라서
  -- pattern = "?" 로 두면 : 와 = 까지 걸린다.
  local function on(events, ime_on, cmdline_only)
    vim.api.nvim_create_autocmd(events, {
      group = group,
      desc = "work-kb: 입력기 " .. (ime_on and "켜기" or "끄기"),
      callback = function(ev)
        if cmdline_only and not vim.tbl_contains(CMDLINE_TYPES, ev.match) then
          return
        end
        if M.auto then
          set_ime(ime_on)
        end
      end,
    })
  end

  on("InsertEnter", true)
  on("InsertLeave", false)
  on("CmdlineEnter", true, true)
  on("CmdlineLeave", false, true)
  on("TermEnter", true)
  on("TermLeave", false)
  -- 미리 띄운 서버에 Neovide가 붙을 때 (UIEnter) 지금 모드에 맞춘다.
  vim.api.nvim_create_autocmd("UIEnter", {
    group = group,
    desc = "work-kb: 입력기 처음 상태",
    callback = function()
      if M.auto then
        set_ime(wanted_now())
      end
    end,
  })

  if M.auto then
    set_ime(false) -- 처음 상태: 일반 모드
  end
  if cfg.langmap then
    M.apply_langmap()
  end
  return true
end

return M
