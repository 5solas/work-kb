-- Neovide 전용 설정 (vim.g.neovide가 있을 때만). 글꼴은 neovide/config.toml에만 둔다 (guifont를 Lua에서
-- 정하면 시작 직후 한 번 더 다시 그린다).
--
-- M.apply()는 여러 번 불러도 같은 결과를 낸다: init.lua가 vim.g.neovide가 있을 때 한 번, autocmds.lua가
-- UIEnter에서 한 번 더 부른다 (미리 띄운 서버에 Neovide가 붙을 때는 UIEnter에서야 vim.g.neovide가 생긴다).
-- require 시점에는 아무것도 하지 않는다.
--
-- 이름 확인 (Neovide 0.16.2, 설치된 실행 파일에 붙여 보고 로그의 등록된 설정 목록과 대조함):
--   neovide_no_idle 은 0.16.2에 등록되어 있지 않다. 그래서 설정하지 않는다 (idle은 config.toml의 `idle`).

local M = {}

local SCALE_STEP = 1.1
local SCALE_MIN, SCALE_MAX = 0.4, 4.0

---합친 설정에서 neovide 표를 읽는다. 없거나 잘못되면 기본값.
local function read_settings()
  local ok, s = pcall(require, "config.settings")
  local n = (ok and type(s) == "table" and type(s.neovide) == "table") and s.neovide or {}
  local scale = tonumber(n.scale)
  if not scale or scale <= 0 then
    scale = 1.0
  end
  local scroll = tonumber(n.scroll_animation)
  if not scroll or scroll < 0 then
    scroll = 0.08
  end
  local ime_auto = true
  if ok and type(s) == "table" and type(s.ime) == "table" and s.ime.auto == false then
    ime_auto = false
  end
  return { scale = scale, scroll_animation = scroll, ime_auto = ime_auto }
end

local function clamp(x)
  return math.max(SCALE_MIN, math.min(SCALE_MAX, x))
end

local function set_scale(x)
  vim.g.neovide_scale_factor = clamp(x)
end

local function map_keys(default_scale)
  local set = vim.keymap.set
  set({ "n", "v", "i", "c", "t" }, "<C-=>", function()
    set_scale((vim.g.neovide_scale_factor or 1.0) * SCALE_STEP)
  end, { desc = "Neovide: 확대" })
  set({ "n", "v", "i", "c", "t" }, "<C-->", function()
    set_scale((vim.g.neovide_scale_factor or 1.0) / SCALE_STEP)
  end, { desc = "Neovide: 축소" })
  set({ "n", "v", "i", "c", "t" }, "<C-0>", function()
    set_scale(default_scale)
  end, { desc = "Neovide: 배율 처음으로" })
  -- Ctrl+Shift+V 붙여넣기 (Neovide는 <C-S-v>를 그대로 보낸다)
  set("i", "<C-S-v>", "<C-r>+", { desc = "붙여넣기" })
  set("c", "<C-S-v>", "<C-r>+", { desc = "붙여넣기" })
  set("t", "<C-S-v>", [[<C-\><C-n>"+pi]], { desc = "붙여넣기" })
  set({ "n", "v", "i", "c", "t" }, "<F11>", function()
    vim.g.neovide_fullscreen = not vim.g.neovide_fullscreen
  end, { desc = "Neovide: 전체 화면" })
end

---Neovide 설정을 적용한다. Neovide가 아니면 아무것도 하지 않고 false를 돌려준다.
function M.apply()
  if not vim.g.neovide then
    return false
  end
  local s = read_settings()
  local g = vim.g

  vim.o.linespace = 2
  g.neovide_scale_factor = s.scale

  g.neovide_refresh_rate = 60
  g.neovide_refresh_rate_idle = 5

  -- 움직임은 최소로: 커서는 끄고 스크롤만 아주 짧게.
  g.neovide_cursor_animation_length = 0
  g.neovide_cursor_short_animation_length = 0
  g.neovide_cursor_trail_size = 0
  g.neovide_cursor_vfx_mode = ""
  g.neovide_cursor_animate_in_insert_mode = false
  g.neovide_cursor_animate_command_line = false
  g.neovide_scroll_animation_length = s.scroll_animation
  g.neovide_scroll_animation_far_lines = 0
  g.neovide_position_animation_length = 0

  g.neovide_padding_top = 6
  g.neovide_padding_bottom = 6
  g.neovide_padding_left = 8
  g.neovide_padding_right = 8

  g.neovide_remember_window_size = true
  g.neovide_opacity = 1.0
  g.neovide_normal_opacity = 1.0
  g.neovide_floating_shadow = false
  g.neovide_floating_blur_amount_x = 0
  g.neovide_floating_blur_amount_y = 0
  g.neovide_hide_mouse_when_typing = true
  g.neovide_confirm_quit = true
  g.neovide_theme = "auto"

  -- 입력기: ime 모듈이 이미 상태를 정했다면 덮어쓰지 않는다 (UIEnter에서 다시 불릴 때 삽입 모드 중일 수 있다).
  -- 자동 전환을 끈 사용자(ime.auto = false)에게는 아무 값도 강요하지 않는다.
  if s.ime_auto and g.neovide_input_ime == nil then
    g.neovide_input_ime = false
  end

  map_keys(s.scale)
  return true
end

return M
