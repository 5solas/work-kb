-- 설정 검사 (work-kb--root / work-kb--vault와 같은 한국어 오류).
local H = require("helpers")

---저장소 꼴의 임시 트리 <base>/한글 저장소 (scripts/kb.py는 OPTS.kb일 때만, 실제 config.lua 사본)와
---<base>/AppData/Local/nvim → <저장소>/nvim 정션 (mklink /J; 설치된 PC의 %LOCALAPPDATA%\nvim과 같은 꼴).
---via = 정션을 거친 config.lua 경로. cleanup은 정션만 지운다 (대상은 그대로).
local function junction_tree(opts)
  local base = H.make_vault("정션 시험")
  local repo = base .. "/한글 저장소"
  -- 플러그인 자리: OPTS.layout ("nvim/dev/work-kb" 또는 "nvim"), 기본은 지금 저장소의 자리
  local layout = opts.layout or H.PLUGIN_REL
  local plugin = repo .. "/" .. layout .. "/lua/work-kb"
  H.mkdir_p(plugin)
  H.copy_file(H.plugin .. "/lua/work-kb/config.lua", plugin .. "/config.lua")
  if opts.kb then
    H.write_bytes(repo .. "/scripts/kb.py", "# 시험용\n")
  end
  local link = base .. "/AppData/Local/nvim"
  H.mkdir_p(vim.fs.dirname(link))
  local result = vim
    .system({ "cmd.exe", "/d", "/c", "mklink", "/J", (link:gsub("/", "\\")), ((repo .. "/nvim"):gsub("/", "\\")) }, { text = true })
    :wait(15000)
  H.eq(result.code, 0, "mklink /J 실패: " .. tostring(result.stdout) .. tostring(result.stderr))
  local lst = vim.uv.fs_lstat(link)
  H.ok(lst and lst.type == "link", "정션이 아님: " .. vim.inspect(lst))
  local below = layout:gsub("^nvim/?", "")
  local via = link .. (below ~= "" and ("/" .. below) or "") .. "/lua/work-kb/config.lua"
  H.ok(H.exists(via), "정션을 거쳐 config.lua가 보이지 않음")
  return {
    base = base,
    repo = repo,
    link = link,
    via = via,
    cleanup = function()
      vim.uv.fs_rmdir(link)
      H.ok(not H.exists(link), "정션이 남음")
      H.ok(H.exists(plugin .. "/config.lua"), "정션을 지우면서 대상까지 지움")
    end,
  }
end

-- 플러그인이 놓일 수 있는 두 자리 (저장소 기준). 지금 자리(H.PLUGIN_REL)는 그중 하나다.
local LAYOUTS = { "nvim/dev/work-kb", "nvim" }

describe("config", function()
  it("코드 루트가 없으면 사용자 오류 (Emacs %S 꼴)", function()
    local config = require("work-kb.config")
    local ui = require("work-kb.ui")
    H.configure({ root = false })
    local err = H.fails_with(function()
      config.root()
    end, "setup()의 root를 설정하세요 (scripts/kb.py가 있는 폴더): nil")
    H.ok(ui.is_user_error(err), "사용자 오류로 표시")
    local missing = H.tmp() .. "/없는 폴더"
    H.configure({ root = missing })
    H.fails_with(function()
      config.root()
    end, string.format('setup()의 root를 설정하세요 (scripts/kb.py가 있는 폴더): "%s"', missing))
    H.configure({ root = "C:\\없는\\루트" })
    H.fails_with(function()
      config.root()
    end, 'setup()의 root를 설정하세요 (scripts/kb.py가 있는 폴더): "C:\\\\없는\\\\루트"')
  end)

  it("볼트가 없으면 사용자 오류, 없으면 코드 루트", function()
    local config = require("work-kb.config")
    local missing = H.tmp() .. "/없는 볼트"
    H.configure({ vault = missing })
    H.fails_with(function()
      config.vault()
    end, "볼트 폴더가 없습니다: " .. missing)
    H.configure({ vault = nil })
    H.eq(config.vault(), config.root())
    H.eq(config.root(), H.repo)
  end)

  it("경로는 절대, 슬래시, 끝 슬래시 없음", function()
    local config = require("work-kb.config")
    local vault = H.make_vault()
    local backslashed = vault:gsub("/", "\\") .. "\\"
    H.configure({ vault = backslashed })
    H.eq(config.vault(), vault)
    H.ok(not config.vault():find("\\", 1, true))
    H.ok(config.vault():sub(-1) ~= "/")
    -- 상대 경로는 Neovim 작업 폴더 기준으로 절대가 된다.
    local cwd = vim.fn.getcwd()
    vim.cmd.cd(vim.fn.fnameescape(vim.fs.dirname(vault)))
    local ok, err = pcall(function()
      H.configure({ vault = vim.fs.basename(vault) })
      H.eq(config.vault(), vault)
    end)
    vim.cmd.cd(vim.fn.fnameescape(cwd))
    H.ok(ok, tostring(err))
  end)

  for _, layout in ipairs(LAYOUTS) do
  it("정션으로 닿은 플러그인: 기본 루트는 첫 root() 때 실제 저장소 경로로 (setup은 I/O 없음) — " .. layout, function()
    local t = junction_tree({ kb = true, layout = layout })
    local ok, err = pcall(function()
      local cfg = dofile(t.via)
      -- 문자열만으로 정한 값은 정션의 부모 (...\AppData\Local꼴): 이것을 쓰면 안 된다.
      H.eq(cfg.defaults.root, t.base .. "/AppData/Local")
      local realpath_calls = 0
      local real_fn = vim.uv.fs_realpath
      H.patch(vim.uv, "fs_realpath", function(...)
        realpath_calls = realpath_calls + 1
        return real_fn(...)
      end)
      local stat_calls = 0
      local stat_fn = vim.uv.fs_stat
      H.patch(vim.uv, "fs_stat", function(...)
        stat_calls = stat_calls + 1
        return stat_fn(...)
      end)
      cfg.setup({ prefix = false })
      H.eq(realpath_calls, 0, "setup은 실제 경로를 보지 않는다")
      H.eq(stat_calls, 0, "setup은 파일 시스템을 보지 않는다")
      H.eq(cfg.root(), t.repo)
      H.eq(cfg.vault(), t.repo, "볼트를 주지 않으면 코드 루트")
      H.eq(realpath_calls, 1)
      cfg.root()
      H.eq(realpath_calls, 1, "한 번 정한 실제 경로는 다시 보지 않는다")
      -- 다시 setup하면 다시 정한다 (설정이 바뀌었을 수 있음)
      cfg.setup({ prefix = false })
      H.eq(cfg.root(), t.repo)
      H.eq(realpath_calls, 2)
      -- root를 직접 주면 그 값 그대로 (정션 경로여도 풀지 않는다)
      cfg.setup({ root = t.link })
      H.eq(cfg.root(), t.link)
      H.eq(realpath_calls, 2)
    end)
    t.cleanup()
    H.ok(ok, tostring(err))
  end)

  it("기본 루트 아래 scripts/kb.py가 없으면 setup()의 root를 말하는 사용자 오류 — " .. layout, function()
    local t = junction_tree({ kb = false, layout = layout })
    local ok, err = pcall(function()
      local cfg = dofile(t.via)
      cfg.setup({})
      local e = H.fails_with(function()
        cfg.root()
      end, string.format("코드 루트에 scripts/kb.py가 없습니다: %s. setup()의 root를 설정하세요", t.repo))
      H.ok(require("work-kb.ui").is_user_error(e), "사용자 오류로 표시")
      H.fails_with(function()
        cfg.vault()
      end, e)
      -- 직접 준 root는 Emacs처럼 폴더인지만 본다 (kb.py 검사 없음)
      cfg.setup({ root = t.repo })
      H.eq(cfg.root(), t.repo)
    end)
    t.cleanup()
    H.ok(ok, tostring(err))
  end)
  end

  it("두 플러그인 자리에서 루트를 꺼낸다: 문자열 꼴과 위로 올라가며 scripts/kb.py 찾기", function()
    local config = require("work-kb.config")
    H.eq(config._root_from_path("D:\\저장소 A\\nvim\\dev\\work-kb\\lua\\work-kb\\config.lua"), "D:/저장소 A")
    H.eq(config._root_from_path("D:/저장소 A/nvim/lua/work-kb/config.lua"), "D:/저장소 A")
    H.eq(config._root_from_path("D:/저장소 A/lua/work-kb/config.lua"), nil)
    for _, layout in ipairs(LAYOUTS) do
      local base = H.make_vault("자리 시험")
      local repo = base .. "/한글 저장소"
      local file = repo .. "/" .. layout .. "/lua/work-kb/config.lua"
      H.write_bytes(file, "-- 자리\n")
      H.eq(config.real_default_root(file), repo, layout .. ": scripts/kb.py가 없으면 자리 꼴")
      H.write_bytes(repo .. "/scripts/kb.py", "# 시험용\n")
      H.eq(config.real_default_root(file), repo, layout)
      H.eq(config._root_by_walking(file), repo, layout)
    end
    -- 플러그인을 옮겨도(어떤 자리든) 위로 올라가 저장소를 찾는다
    local base = H.make_vault("다른 자리")
    H.write_bytes(base .. "/repo/scripts/kb.py", "# 시험용\n")
    H.write_bytes(base .. "/repo/editor/lua/work-kb/config.lua", "-- 자리\n")
    H.eq(config.real_default_root(base .. "/repo/editor/lua/work-kb/config.lua"), base .. "/repo")
    -- 지금 저장소에서
    H.eq(vim.fs.normalize(config.real_default_root()), H.repo)
  end)

  it("기본 코드 루트는 플러그인 위치에서 계산 (I/O 없음)", function()
    local config = require("work-kb.config")
    H.eq(vim.fs.normalize(config.defaults.root), H.repo)
    config.setup({})
    H.eq(config.get().prefix, "<leader>k")
    H.eq(config.get().server.enable, true)
    H.eq(config.get().server.idle_sec, 1800)
    config.setup({ server = { idle_sec = 60 } })
    H.eq(config.get().server.enable, true, "server 표는 키별로 합친다")
    H.eq(config.get().server.idle_sec, 60)
  end)
end)
