-- 키 (ERT work-kb-test-every-key-bound, work-kb-test-prefix-rebind) + setup이 가벼운지.
local H = require("helpers")

local EXPECTED = {
  { "c", "work-kb.capture", "capture" },
  { "C", "work-kb.capture", "clipboard_image" },
  { "F", "work-kb.capture", "image_file" },
  { "i", "work-kb.capture", "inbox" },
  { "t", "work-kb.daily", "today" },
  { "s", "work-kb.search", "search" },
  { "o", "work-kb.open", "open_at_point" },
  { "l", "work-kb.misc", "link" },
  { "?", "work-kb.misc", "status" },
  { "j", "work-kb.jira", "menu" },
  { "g", "work-kb.compile", "ingest_compile", { dry_run = false } },
  { "G", "work-kb.compile", "ingest_compile", { dry_run = true } },
  { "a", "work-kb.compile", "proposals" },
}

---가짜 기능 모듈을 package.loaded에 넣고 부른 기록을 돌려준다. 끝나면 되돌린다.
local function fake_modules()
  local calls = {}
  local saved = {}
  for _, e in ipairs(EXPECTED) do
    local mod = e[2]
    if saved[mod] == nil then
      saved[mod] = package.loaded[mod] or false
      package.loaded[mod] = {}
    end
    package.loaded[mod][e[3]] = function(arg)
      table.insert(calls, { mod, e[3], arg })
    end
  end
  local function restore()
    for mod, value in pairs(saved) do
      package.loaded[mod] = value or nil
    end
  end
  return calls, restore
end

local function mapping(lhs)
  local m = vim.fn.maparg(lhs, "n", false, true)
  if type(m) == "table" and m.callback then
    return m
  end
  return nil
end

local function cleanup()
  local kb = package.loaded["work-kb"]
  if kb then
    kb.rebind_prefix(false)
  end
  pcall(vim.api.nvim_del_augroup_by_name, "WorkKb")
end

describe("keys", function()
  it("contract C의 모든 키가 접두 키 아래 제 기능에 묶여 있다", function()
    local calls, restore = fake_modules()
    local ok, err = pcall(function()
      require("work-kb").setup({ root = H.repo, prefix = "<leader>k", server = { enable = false } })
      for _, e in ipairs(EXPECTED) do
        local m = mapping("<leader>k" .. e[1])
        H.ok(m, "키가 없음: " .. e[1])
        H.ok(m.desc:find("^work%-kb: "), "설명: " .. tostring(m.desc))
        m.callback()
      end
      H.eq(#calls, #EXPECTED)
      for i, e in ipairs(EXPECTED) do
        H.eq({ calls[i][1], calls[i][2] }, { e[2], e[3] }, "키 " .. e[1])
        H.eq(calls[i][3], e[4], "키 " .. e[1] .. " 인수")
      end
      -- 실제 키 입력으로도 (기본 mapleader는 \)
      vim.api.nvim_feedkeys(vim.keycode("<leader>kl"), "x", false)
      H.eq(calls[#calls][2], "link")
      -- :WorkKb 이름도 같은 기능
      vim.cmd("WorkKb ingest-dry-run")
      H.eq(calls[#calls][3], { dry_run = true })
      H.eq(require("work-kb").complete("cap"), { "capture", "capture-clipboard-image", "capture-image-file", "capture-inbox" })
    end)
    restore()
    cleanup()
    H.ok(ok, tostring(err))
  end)

  it("setup을 다른 접두 키로 다시 부르면 옮겨 붙이고 옛 키는 뗀다", function()
    local ok, err = pcall(function()
      local kb = require("work-kb")
      kb.setup({ root = H.repo, prefix = "<leader>k", server = { enable = false } })
      H.ok(mapping("<leader>kc"), "처음 접두")
      kb.setup({ root = H.repo, prefix = "<F8>", server = { enable = false } })
      for _, e in ipairs(EXPECTED) do
        H.ok(mapping("<F8>" .. e[1]), "새 접두: " .. e[1])
        H.ok(not mapping("<leader>k" .. e[1]), "옛 접두가 남음: " .. e[1])
      end
      -- 사용자가 덮어쓴 키는 떼지 않는다 (Emacs: 아직 이 키맵을 가리킬 때만 뗀다).
      vim.keymap.set("n", "<F8>c", function() end, { desc = "사용자 매핑" })
      kb.setup({ root = H.repo, prefix = "<leader>k", server = { enable = false } })
      local user = vim.fn.maparg("<F8>c", "n", false, true)
      H.eq(user.desc, "사용자 매핑")
      H.ok(not mapping("<F8>o"))
      H.ok(mapping("<leader>ko"))
      pcall(vim.keymap.del, "n", "<F8>c")
      -- 접두 키 없음
      kb.setup({ root = H.repo, prefix = false, server = { enable = false } })
      H.ok(not mapping("<leader>kc"))
      H.ok(not mapping("<leader>ko"))
    end)
    cleanup()
    H.ok(ok, tostring(err))
  end)

  it("setup은 config 말고 아무 모듈도 불러오지 않고 서버도 바로 띄우지 않는다", function()
    local saved = {}
    for name, value in pairs(package.loaded) do
      if name == "work-kb" or name:find("^work%-kb%.") then
        saved[name] = value
        package.loaded[name] = nil
      end
    end
    local loaded_after = {}
    local ok, err = pcall(function()
      local started = vim.uv.hrtime()
      require("work-kb").setup({ root = H.repo, server = { enable = true, delay_ms = 50 } })
      local ms = (vim.uv.hrtime() - started) / 1e6
      for name in pairs(package.loaded) do
        if name == "work-kb" or name:find("^work%-kb%.") then
          table.insert(loaded_after, name)
        end
      end
      table.sort(loaded_after)
      H.eq(loaded_after, { "work-kb", "work-kb.config" })
      H.ok(ms < 50, string.format("setup이 %.1f ms 걸림", ms))
      -- 서버 띄우기는 예약만 된다: 이 순간에는 서버 모듈조차 없다.
      H.eq(package.loaded["work-kb.server"], nil)
      -- 예약된 시작이 돌기 전에 서버를 끈다 (이 테스트는 실제 서버를 띄우지 않는다).
      require("work-kb.config").get().server.enable = false
      vim.wait(120, function()
        return false
      end)
      H.eq(package.loaded["work-kb.server"], nil, "꺼진 뒤에는 예약된 시작이 아무것도 하지 않는다")
      -- 키를 눌러야 기능 모듈을 불러온다: 없는 모듈은 그때 오류로 보인다.
      local echoed = {}
      H.patch(vim.api, "nvim_echo", function(chunks)
        table.insert(echoed, chunks[1][1])
      end)
      require("work-kb").call("jira")
      H.ok(echoed[1] and echoed[1]:find("work%-kb%.jira"), "없는 모듈 오류: " .. tostring(echoed[1]))
    end)
    cleanup()
    for name in pairs(package.loaded) do
      if name == "work-kb" or name:find("^work%-kb%.") then
        package.loaded[name] = nil
      end
    end
    for name, value in pairs(saved) do
      package.loaded[name] = value
    end
    H.ok(ok, tostring(err))
  end)

  it("볼트의 새 .md는 UTF-8·LF, 기존 파일은 EOL 그대로, 볼트 밖은 건드리지 않음 (D11)", function()
    local vault = H.make_vault()
    local outside = H.make_vault("밖")
    local ok, err = pcall(function()
      require("work-kb").setup({ root = H.repo, vault = vault, prefix = false, server = { enable = false } })
      vim.cmd.edit(vim.fn.fnameescape(vault .. "/inbox/새 메모.md"))
      H.eq(vim.bo.fileformat, "unix")
      H.eq(vim.bo.fileencoding, "utf-8")
      H.eq(vim.bo.autoread, true)
      vim.cmd("bwipeout!")
      H.write_bytes(vault .. "/daily/2026-11-04.md", "## 기록\r\n- 09:00 한글 😀\r\n")
      vim.cmd.edit(vim.fn.fnameescape(vault .. "/daily/2026-11-04.md"))
      H.eq(vim.bo.fileformat, "dos")
      H.eq(vim.bo.fileencoding, "utf-8")
      H.eq(vim.api.nvim_buf_get_lines(0, 1, 2, false)[1], "- 09:00 한글 😀")
      vim.cmd("bwipeout!")
      local before = vim.o.fileformats
      vim.cmd.edit(vim.fn.fnameescape(outside .. "/inbox/밖 메모.md"))
      H.eq(vim.bo.fileformat, vim.split(before, ",")[1], "볼트 밖은 Neovim 기본")
      vim.cmd("bwipeout!")
    end)
    cleanup()
    H.ok(ok, tostring(err))
  end)

  it("plugin/work-kb.lua는 시작 때 :WorkKb만 정의하고 모듈을 불러오지 않는다", function()
    local startup = _G.WORK_KB_STARTUP
    H.ok(startup, "러너가 시작 상태를 기록하지 않음")
    H.eq(startup.plugin, 1)
    H.eq(startup.command, true)
    H.eq(startup.loaded, {})
  end)

  it("사용자 오류는 traceback 없이 문구만 보인다", function()
    local saved = package.loaded["work-kb.search"]
    package.loaded["work-kb.search"] = {
      search = function()
        require("work-kb.ui").fail("검색어가 비었습니다")
      end,
    }
    local echoed = {}
    H.patch(vim.api, "nvim_echo", function(chunks)
      table.insert(echoed, chunks[1])
    end)
    local ok = require("work-kb").call("search")
    package.loaded["work-kb.search"] = saved
    H.eq(ok, false)
    H.eq(echoed[1], { "검색어가 비었습니다", "ErrorMsg" })
  end)
end)
