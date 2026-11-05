-- config/neovide.lua 시험. config.settings는 이 파일이 만든 가짜 표를 package.loaded에 넣어 쓴다
-- (N4a의 진짜 settings.lua와 무관하게 돈다). Neovide가 아닌 헤드리스에서 vim.g.neovide를 흉내 낸다.
local H = require("helpers")

local repo = H.repo
package.path = repo .. "/nvim/lua/?.lua;" .. repo .. "/nvim/lua/?/init.lua;" .. package.path

local KEYS = { "<C-=>", "<C-->", "<C-0>", "<C-S-v>", "<F11>" }
local MODES = { "n", "v", "i", "c", "t" }

---vim.g의 neovide* 전부와 linespace, 이 모듈이 만드는 키 맵을 되돌릴 수 있게 저장한다.
---vim.g는 pairs로 돌 수 없으므로 g: 사전을 읽는다.
local function neovide_globals()
  local out = {}
  for k in pairs(vim.api.nvim_eval("g:")) do -- 값은 vim.g로 읽는다 (사전의 Float은 Lua 표로 바뀐다)
    if k:find("^neovide") then
      out[k] = vim.g[k]
    end
  end
  return out
end

local function snapshot()
  local globals = neovide_globals()
  return { globals = globals, linespace = vim.o.linespace, settings = package.loaded["config.settings"] }
end

local function restore(snap)
  for k in pairs(neovide_globals()) do
    vim.g[k] = nil
  end
  for k, v in pairs(snap.globals) do
    vim.g[k] = v
  end
  vim.o.linespace = snap.linespace
  package.loaded["config.settings"] = snap.settings
  package.loaded["config.neovide"] = nil
  for _, mode in ipairs(MODES) do
    for _, lhs in ipairs(KEYS) do
      pcall(vim.keymap.del, mode, lhs)
    end
  end
end

---SETTINGS(가짜 config.settings)로 FN(neovide 모듈)을 돌린다. 끝나면 모두 되돌린다.
local function sandbox(settings, fn)
  local snap = snapshot()
  for k in pairs(neovide_globals()) do
    vim.g[k] = nil
  end
  package.loaded["config.settings"] = settings
  package.loaded["config.neovide"] = nil
  local ok, err = pcall(function()
    fn(require("config.neovide"))
  end)
  restore(snap)
  if not ok then
    error(err, 0)
  end
end

local DEFAULTS = { neovide = { scale = 1.0, scroll_animation = 0.08 }, ime = { auto = true, langmap = false } }

local function collect()
  local out = neovide_globals()
  out.neovide = nil -- vim.g.neovide 자체(Neovide인가)는 제외
  return out
end

local EXPECTED = {
  neovide_scale_factor = 1.0,
  neovide_refresh_rate = 60,
  neovide_refresh_rate_idle = 5,
  neovide_cursor_animation_length = 0,
  neovide_cursor_short_animation_length = 0,
  neovide_cursor_trail_size = 0,
  neovide_cursor_vfx_mode = "",
  neovide_cursor_animate_in_insert_mode = false,
  neovide_cursor_animate_command_line = false,
  neovide_scroll_animation_length = 0.08,
  neovide_scroll_animation_far_lines = 0,
  neovide_position_animation_length = 0,
  neovide_padding_top = 6,
  neovide_padding_bottom = 6,
  neovide_padding_left = 8,
  neovide_padding_right = 8,
  neovide_remember_window_size = true,
  neovide_opacity = 1.0,
  neovide_normal_opacity = 1.0,
  neovide_floating_shadow = false,
  neovide_floating_blur_amount_x = 0,
  neovide_floating_blur_amount_y = 0,
  neovide_hide_mouse_when_typing = true,
  neovide_confirm_quit = true,
  neovide_theme = "auto",
  neovide_input_ime = false,
}

local function keymap(mode, lhs)
  return vim.fn.maparg(lhs, mode, false, true)
end

return {
  {
    name = "require만으로는 아무것도 바꾸지 않는다",
    fn = function()
      sandbox(DEFAULTS, function()
        vim.g.neovide = true
        H.eq(collect(), {})
        H.eq(vim.tbl_isempty(keymap("n", "<C-=>")), true)
      end)
    end,
  },
  {
    name = "Neovide가 아니면 apply는 false이고 아무것도 건드리지 않는다",
    fn = function()
      sandbox(DEFAULTS, function(m)
        local before = vim.o.linespace
        H.eq(m.apply(), false)
        H.eq(collect(), {})
        H.eq(vim.o.linespace, before)
        H.eq(vim.tbl_isempty(keymap("n", "<C-=>")), true)
      end)
    end,
  },
  {
    name = "Neovide에서 apply가 정확한 전역 변수를 정한다",
    fn = function()
      sandbox(DEFAULTS, function(m)
        vim.g.neovide = true
        H.eq(m.apply(), true)
        H.eq(collect(), EXPECTED)
        H.eq(vim.o.linespace, 2)
      end)
    end,
  },
  {
    name = "neovide_no_idle는 설정하지 않는다 (0.16.2에 등록되어 있지 않음)",
    fn = function()
      sandbox(DEFAULTS, function(m)
        vim.g.neovide = true
        m.apply()
        H.eq(vim.g.neovide_no_idle, nil)
      end)
    end,
  },
  {
    name = "apply는 여러 번 불러도 같은 결과 (전역·키 맵)",
    fn = function()
      sandbox(DEFAULTS, function(m)
        vim.g.neovide = true
        m.apply()
        local first = collect()
        local map1 = keymap("n", "<C-=>")
        m.apply()
        m.apply()
        H.eq(collect(), first)
        H.eq(keymap("n", "<C-=>").lhs, map1.lhs)
        H.eq(vim.o.linespace, 2)
      end)
    end,
  },
  {
    name = "배율과 스크롤 애니메이션은 설정에서 온다",
    fn = function()
      sandbox({ neovide = { scale = 1.25, scroll_animation = 0.2 }, ime = { auto = true } }, function(m)
        vim.g.neovide = true
        m.apply()
        H.eq(vim.g.neovide_scale_factor, 1.25)
        H.eq(vim.g.neovide_scroll_animation_length, 0.2)
      end)
    end,
  },
  {
    name = "설정이 없거나 값이 잘못되면 기본값",
    fn = function()
      for _, bad in ipairs({
        {},
        { neovide = "x" },
        { neovide = { scale = "abc", scroll_animation = -1 } },
        { neovide = { scale = 0 } },
      }) do
        sandbox(bad, function(m)
          vim.g.neovide = true
          m.apply()
          H.eq(vim.g.neovide_scale_factor, 1.0, vim.inspect(bad))
          H.eq(vim.g.neovide_scroll_animation_length, 0.08, vim.inspect(bad))
        end)
      end
    end,
  },
  {
    name = "config.settings를 불러올 수 없어도 오류 없이 기본값",
    fn = function()
      sandbox(nil, function(m)
        local saved = package.preload["config.settings"]
        package.loaded["config.settings"] = nil
        package.preload["config.settings"] = function()
          error("설정 없음")
        end
        vim.g.neovide = true
        local ok, err = pcall(m.apply)
        package.preload["config.settings"] = saved
        H.ok(ok, tostring(err))
        H.eq(vim.g.neovide_scale_factor, 1.0)
      end)
    end,
  },
  {
    name = "UIEnter 경로: 처음엔 Neovide가 아니었다가 나중에 vim.g.neovide가 생겨도 적용된다",
    fn = function()
      sandbox(DEFAULTS, function(m)
        H.eq(m.apply(), false) -- init 시점: 아직 붙지 않음
        H.eq(collect(), {})
        vim.g.neovide = true -- 서버에 붙은 뒤
        vim.api.nvim_exec_autocmds("UIEnter", { modeline = false })
        H.eq(m.apply(), true) -- autocmds.lua가 UIEnter에서 부르는 호출
        H.eq(collect(), EXPECTED)
      end)
    end,
  },
  {
    name = "다시 적용해도 입력기 상태(neovide_input_ime)를 덮어쓰지 않는다",
    fn = function()
      sandbox(DEFAULTS, function(m)
        vim.g.neovide = true
        m.apply()
        H.eq(vim.g.neovide_input_ime, false)
        vim.g.neovide_input_ime = true -- ime 모듈이 삽입 모드에서 켠 상태
        m.apply()
        H.eq(vim.g.neovide_input_ime, true)
      end)
    end,
  },
  {
    name = "ime.auto = false이면 neovide_input_ime를 정하지 않는다",
    fn = function()
      sandbox({ neovide = DEFAULTS.neovide, ime = { auto = false } }, function(m)
        vim.g.neovide = true
        m.apply()
        H.eq(vim.g.neovide_input_ime, nil)
      end)
    end,
  },
  {
    name = "키 맵: <C-=> <C--> <C-0> 배율",
    fn = function()
      sandbox({ neovide = { scale = 1.5, scroll_animation = 0.08 }, ime = {} }, function(m)
        vim.g.neovide = true
        m.apply()
        for _, mode in ipairs(MODES) do
          for _, lhs in ipairs({ "<C-=>", "<C-->", "<C-0>", "<F11>" }) do
            H.ok(keymap(mode, lhs).callback ~= nil, mode .. " " .. lhs .. " 없음")
          end
        end
        keymap("n", "<C-=>").callback()
        H.ok(math.abs(vim.g.neovide_scale_factor - 1.65) < 1e-9, tostring(vim.g.neovide_scale_factor))
        keymap("n", "<C-->").callback()
        H.ok(math.abs(vim.g.neovide_scale_factor - 1.5) < 1e-9, tostring(vim.g.neovide_scale_factor))
        keymap("n", "<C-=>").callback()
        keymap("n", "<C-0>").callback()
        H.eq(vim.g.neovide_scale_factor, 1.5, "<C-0>은 설정의 배율로 돌아간다")
      end)
    end,
  },
  {
    name = "키 맵: 배율은 범위 안에 머문다",
    fn = function()
      sandbox(DEFAULTS, function(m)
        vim.g.neovide = true
        m.apply()
        for _ = 1, 60 do
          keymap("n", "<C-=>").callback()
        end
        H.eq(vim.g.neovide_scale_factor, 4.0)
        for _ = 1, 120 do
          keymap("n", "<C-->").callback()
        end
        H.eq(vim.g.neovide_scale_factor, 0.4)
      end)
    end,
  },
  {
    name = "키 맵: <F11> 전체 화면 전환과 <C-S-v> 붙여넣기",
    fn = function()
      sandbox(DEFAULTS, function(m)
        vim.g.neovide = true
        m.apply()
        H.eq(vim.g.neovide_fullscreen, nil)
        keymap("n", "<F11>").callback()
        H.eq(vim.g.neovide_fullscreen, true)
        keymap("n", "<F11>").callback()
        H.eq(vim.g.neovide_fullscreen, false)
        H.eq(keymap("i", "<C-S-v>").rhs, "<C-r>+")
        H.eq(keymap("c", "<C-S-v>").rhs, "<C-r>+")
        H.eq(keymap("t", "<C-S-v>").rhs, [[<C-\><C-n>"+pi]])
      end)
    end,
  },
  {
    name = "config.toml.in: 키와 글꼴, 치환 표지",
    fn = function()
      local text = H.read_bytes(repo .. "/nvim/neovide/config.toml.in")
      H.ok(text, "config.toml.in 없음")
      H.ok(not text:find("\r", 1, true), "LF만 써야 함")
      H.ok(text:sub(1, 3) ~= "\239\187\191", "BOM 없음이어야 함")
      H.contains(text, "neovim-bin = '@NVIM_EXE@'")
      for _, line in ipairs({
        "fork = true",
        'frame = "full"',
        "idle = true",
        "maximized = false",
        "no-multigrid = false",
        "srgb = true",
        "tabs = false",
        "title-hidden = false",
        "vsync = true",
        "wsl = false",
        "[font]",
        'normal = ["JetBrainsMono NF", "Sarasa Fixed K"]',
        "size = 12.0",
        'hinting = "full"',
        'edging = "antialias"',
      }) do
        H.ok(text:find("\n" .. line .. "\n", 1, true), "줄 없음: " .. line)
      end
      -- 치환 표지는 한 번만, 다른 @...@는 없다.
      local n = 0
      for _ in text:gmatch("@[%w_]+@") do
        n = n + 1
      end
      H.eq(n, 1)
    end,
  },
}
