-- config/ime.lua 시험: 가짜 vim.g.neovide로 상태 기계를 본다. Neovide가 아니면 아무것도 하지 않는다.
-- 삽입 모드·명령줄 안에 실제로 들어갈 수는 없으므로 autocmd를 직접 쏘고, 모드는 nvim_get_mode를 바꿔 끼워 흉내 낸다.
-- 실제 Neovide에서 한/영 상태가 유지되는지는 이 시험으로 알 수 없다 (수동 확인).
local H = require("helpers")

local repo = H.repo
package.path = repo .. "/nvim/lua/?.lua;" .. repo .. "/nvim/lua/?/init.lua;" .. package.path

local GROUP = "WorkKbIme"

local function group_autocmds()
  local ok, list = pcall(vim.api.nvim_get_autocmds, { group = GROUP })
  return ok and list or {}
end

local function sandbox(settings, fn)
  local saved_settings = package.loaded["config.settings"]
  local saved_neovide = vim.g.neovide
  local saved_ime = vim.g.neovide_input_ime
  local saved_langmap = vim.o.langmap
  package.loaded["config.settings"] = settings
  package.loaded["config.ime"] = nil
  local ok, err = pcall(function()
    fn(require("config.ime"))
  end)
  pcall(vim.api.nvim_del_augroup_by_name, GROUP)
  package.loaded["config.ime"] = nil
  package.loaded["config.settings"] = saved_settings
  vim.g.neovide = saved_neovide
  vim.g.neovide_input_ime = saved_ime
  vim.o.langmap = saved_langmap
  if not ok then
    error(err, 0)
  end
end

local function fire(event, pattern)
  local opts = { pattern = pattern, modeline = false }
  if #group_autocmds() > 0 then
    opts.group = GROUP -- 그룹이 없을 때(Neovide가 아닌 경우)는 그룹 없이 쏜다
  end
  vim.api.nvim_exec_autocmds(event, opts)
end

local AUTO = { ime = { auto = true, langmap = false } }

return {
  {
    name = "require만으로는 autocmd도 전역도 만들지 않는다",
    fn = function()
      sandbox(AUTO, function()
        vim.g.neovide = true
        H.eq(#group_autocmds(), 0)
        H.eq(vim.g.neovide_input_ime, nil)
      end)
    end,
  },
  {
    name = "Neovide가 아니면 setup은 false이고 아무것도 하지 않는다",
    fn = function()
      sandbox({ ime = { auto = true, langmap = true } }, function(ime)
        vim.g.neovide = nil
        local langmap = vim.o.langmap
        H.eq(ime.setup(), false)
        H.eq(#group_autocmds(), 0)
        H.eq(vim.g.neovide_input_ime, nil)
        H.eq(vim.o.langmap, langmap, "Neovide가 아니면 langmap도 건드리지 않는다")
        fire("InsertEnter")
        H.eq(vim.g.neovide_input_ime, nil)
      end)
    end,
  },
  {
    name = "처음 상태는 꺼짐",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        vim.g.neovide_input_ime = true
        H.eq(ime.setup(), true)
        H.eq(vim.g.neovide_input_ime, false)
      end)
    end,
  },
  {
    name = "InsertEnter 켬, InsertLeave 끔",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        ime.setup()
        fire("InsertEnter")
        H.eq(vim.g.neovide_input_ime, true)
        fire("InsertLeave")
        H.eq(vim.g.neovide_input_ime, false)
      end)
    end,
  },
  {
    name = "CmdlineEnter는 / ? @ 에서만 켠다 (: 는 라틴 그대로)",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        ime.setup()
        for _, t in ipairs({ "/", "?", "@" }) do
          fire("CmdlineEnter", t)
          H.eq(vim.g.neovide_input_ime, true, "CmdlineEnter " .. t)
          fire("CmdlineLeave", t)
          H.eq(vim.g.neovide_input_ime, false, "CmdlineLeave " .. t)
        end
        fire("CmdlineEnter", ":")
        H.eq(vim.g.neovide_input_ime, false, "':'에서는 켜지 않는다")
        fire("CmdlineEnter", "=")
        H.eq(vim.g.neovide_input_ime, false, "'='에서는 켜지 않는다")
      end)
    end,
  },
  {
    name = "TermEnter 켬, TermLeave 끔",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        ime.setup()
        fire("TermEnter")
        H.eq(vim.g.neovide_input_ime, true)
        fire("TermLeave")
        H.eq(vim.g.neovide_input_ime, false)
      end)
    end,
  },
  {
    name = "setup을 여러 번 불러도 autocmd는 한 벌",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        ime.setup()
        local n = #group_autocmds()
        H.ok(n >= 7, "autocmd 수: " .. n)
        ime.setup()
        ime.setup()
        H.eq(#group_autocmds(), n)
      end)
    end,
  },
  {
    name = "ime.auto = false이면 상태를 정하지 않는다",
    fn = function()
      sandbox({ ime = { auto = false } }, function(ime)
        vim.g.neovide = true
        vim.g.neovide_input_ime = nil
        ime.setup()
        H.eq(vim.g.neovide_input_ime, nil)
        fire("InsertEnter")
        H.eq(vim.g.neovide_input_ime, nil)
      end)
    end,
  },
  {
    name = "toggle: 끄면 이벤트가 무시되고, 켜면 지금 모드에 맞춘다",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        ime.setup()
        H.eq(ime.toggle(), false)
        fire("InsertEnter")
        H.eq(vim.g.neovide_input_ime, false, "자동 전환이 꺼진 동안은 그대로")
        H.eq(ime.toggle(), true)
        H.eq(vim.g.neovide_input_ime, false, "일반 모드이므로 꺼짐")
        fire("InsertEnter")
        H.eq(vim.g.neovide_input_ime, true)
        H.eq(ime.toggle(false), false)
        H.eq(ime.toggle(true), true)
      end)
    end,
  },
  {
    name = "UIEnter: 지금 모드에 맞춘다 (삽입 모드·검색 입력·일반 모드)",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        ime.setup()
        H.patch(vim.api, "nvim_get_mode", function()
          return { mode = "i", blocking = false }
        end)
        fire("UIEnter")
        H.eq(vim.g.neovide_input_ime, true)
        H.patch(vim.api, "nvim_get_mode", function()
          return { mode = "c", blocking = false }
        end)
        H.patch(vim.fn, "getcmdtype", function()
          return ":"
        end)
        fire("UIEnter")
        H.eq(vim.g.neovide_input_ime, false, "':' 명령줄")
        H.patch(vim.fn, "getcmdtype", function()
          return "/"
        end)
        fire("UIEnter")
        H.eq(vim.g.neovide_input_ime, true, "'/' 검색")
        H.patch(vim.api, "nvim_get_mode", function()
          return { mode = "n", blocking = false }
        end)
        fire("UIEnter")
        H.eq(vim.g.neovide_input_ime, false)
      end)
    end,
  },
  {
    name = "langmap은 기본으로 꺼져 있고, 켜면 두벌식 자모를 QWERTY로 옮긴다",
    fn = function()
      sandbox(AUTO, function(ime)
        vim.g.neovide = true
        local before = vim.o.langmap
        ime.setup()
        H.eq(vim.o.langmap, before)
      end)
      sandbox({ ime = { auto = true, langmap = true } }, function(ime)
        vim.g.neovide = true
        ime.setup()
        local map = vim.o.langmap
        H.contains(map, "ㅂq")
        H.contains(map, "ㅣl")
        H.contains(map, "ㅃQ")
        -- 실제로 일반 모드 키로 번역되는가: ㅣ → l (오른쪽으로 한 칸)
        vim.cmd("enew!")
        vim.api.nvim_buf_set_lines(0, 0, -1, false, { "abc" })
        vim.api.nvim_win_set_cursor(0, { 1, 0 })
        vim.api.nvim_feedkeys("ㅣ", "xt", false)
        local col = vim.api.nvim_win_get_cursor(0)[2]
        vim.cmd("bwipeout!")
        H.eq(col, 1, "langmap으로 ㅣ가 l로 번역되어야 함")
      end)
    end,
  },
  {
    name = "langmap 문자열: 짝마다 자모 하나와 키 하나, 중복 없음",
    fn = function()
      sandbox(AUTO, function(ime)
        local seen = {}
        local count = 0
        for pair in ime.langmap_string():gmatch("[^,]+") do
          local jamo = vim.fn.strcharpart(pair, 0, 1)
          local key = vim.fn.strcharpart(pair, 1)
          H.eq(vim.fn.strchars(pair), 2, pair)
          H.ok(#key == 1, "키는 ASCII 한 글자: " .. pair)
          H.ok(not seen[jamo], "자모 중복: " .. jamo)
          seen[jamo] = true
          count = count + 1
        end
        H.eq(count, 33) -- 26 + 7
      end)
    end,
  },
  {
    name = "config.settings가 없어도 setup은 오류 없이 기본값(자동 전환 켬)",
    fn = function()
      sandbox(nil, function(ime)
        package.loaded["config.settings"] = nil
        local saved = package.preload["config.settings"]
        package.preload["config.settings"] = function()
          error("설정 없음")
        end
        vim.g.neovide = true
        local ok, err = pcall(ime.setup)
        package.preload["config.settings"] = saved
        H.ok(ok, tostring(err))
        fire("InsertEnter")
        H.eq(vim.g.neovide_input_ime, true)
      end)
    end,
  },
}
