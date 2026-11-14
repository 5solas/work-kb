-- 볼트 안 Markdown 버퍼 설정 (nvim/after/ftplugin/markdown.lua).
-- minimal_init.lua가 lazy.nvim처럼 <플러그인>/after를 runtimepath에 넣는다.
local H = require("helpers")

local function cleanup()
  local kb = package.loaded["work-kb"]
  if kb then
    kb.rebind_prefix(false)
  end
  pcall(vim.api.nvim_del_augroup_by_name, "WorkKb")
  pcall(vim.api.nvim_del_augroup_by_name, "WorkKbVaultMarkdown")
end

---현재 버퍼에 걸린 다시 읽기 autocmd 수 (그룹이 아직 없으면 0).
local function reload_autocmds()
  local ok, list = pcall(vim.api.nvim_get_autocmds, { group = "WorkKbVaultMarkdown", buffer = 0 })
  return ok and #list or 0
end

local function setup(vault)
  require("work-kb").setup({ root = H.repo, vault = vault, prefix = false, server = { enable = false } })
end

local junction_n = 0

---H.tmp() 아래에 TARGET을 가리키는 정션을 만든다 (Windows). 경로 (슬래시).
local function make_junction(target)
  junction_n = junction_n + 1
  local link = string.format("%s/정션 %d", H.tmp(), junction_n)
  local ok, err = vim.uv.fs_symlink((target:gsub("/", "\\")), (link:gsub("/", "\\")), { junction = true })
  if not ok then
    H.not_run("정션을 만들지 못함: " .. tostring(err))
  end
  return link
end

---정션만 지운다 (대상 폴더 안으로 들어가지 않는다: RemoveDirectory는 재분석 지점 자체를 지운다).
local function remove_junction(link)
  vim.uv.fs_rmdir(link)
end

describe("ftplugin", function()
  it("applies through a junction", function()
    local vault = H.make_vault()
    local link = make_junction(vault)
    local upper_base = H.tmp() .. "/Jörg Notes"
    vim.fn.mkdir(upper_base .. "/daily", "p")
    local ok, err = pcall(function()
      H.write_bytes(vault .. "/daily/2026-11-05.md", "## 기록\n")
      H.write_bytes(upper_base .. "/daily/2026-11-05.md", "## 기록\n")
      local function check(file, label)
        vim.cmd.edit(vim.fn.fnameescape(file))
        H.eq(vim.b.work_kb_vault, true, label .. ": 볼트 ftplugin")
        H.eq(vim.bo.shiftwidth, 2, label)
        H.eq(vim.api.nvim_get_option_value("autoread", { scope = "local" }), true, label .. ": BufReadPost autoread")
        vim.cmd("bwipeout!")
      end
      -- 설정은 실제 폴더, 연 경로는 정션
      setup(vault)
      check(link .. "/daily/2026-11-05.md", "정션 경로로 연 파일")
      -- 설정은 정션, 연 경로는 실제 폴더
      setup(link)
      check(vault .. "/daily/2026-11-05.md", "볼트 설정이 정션")
      -- 비ASCII 글자의 대소문자만 다른 경로 (NTFS는 같은 파일)
      setup(upper_base)
      check(H.tmp() .. "/JÖRG NOTES/daily/2026-11-05.md", "비ASCII 대소문자")
    end)
    cleanup()
    remove_junction(link)
    H.ok(H.exists(vault .. "/daily/2026-11-05.md"), "정션 대상은 그대로")
    H.ok(ok, tostring(err))
  end)

  it("볼트 안 .md만 2칸 들여쓰기·자동 줄바꿈 없음, 파일 형식을 바꾸면 되돌린다", function()
    local vault = H.make_vault()
    local ok, err = pcall(function()
      setup(vault)
      H.write_bytes(vault .. "/daily/2026-11-04.md", "## 기록\n- 09:00 한 줄\n")
      vim.cmd.edit(vim.fn.fnameescape(vault .. "/daily/2026-11-04.md"))
      H.eq(vim.bo.filetype, "markdown")
      H.eq(vim.b.work_kb_vault, true)
      H.eq(vim.bo.shiftwidth, 2)
      H.eq(vim.bo.tabstop, 2)
      H.eq(vim.bo.textwidth, 0)
      H.eq(vim.bo.expandtab, true)
      H.eq(vim.wo.wrap, true)
      H.eq(vim.wo.linebreak, true)
      H.eq(vim.wo.spell, false)
      H.ok(reload_autocmds() > 0, "다시 읽기 autocmd")
      vim.cmd("setlocal filetype=text")
      H.eq(vim.b.work_kb_vault, nil, "undo_ftplugin이 표시를 지운다")
      H.eq(reload_autocmds(), 0)
      vim.cmd("bwipeout!")
    end)
    cleanup()
    H.ok(ok, tostring(err))
  end)

  it("볼트 밖 .md와 setup 전의 .md는 건드리지 않는다", function()
    local vault = H.make_vault()
    local outside = H.make_vault("밖")
    local ok, err = pcall(function()
      setup(vault)
      H.write_bytes(outside .. "/inbox/밖 메모.md", "# 밖\n")
      vim.cmd.edit(vim.fn.fnameescape(outside .. "/inbox/밖 메모.md"))
      H.eq(vim.bo.filetype, "markdown")
      H.eq(vim.b.work_kb_vault, nil)
      H.eq(reload_autocmds(), 0)
      vim.cmd("bwipeout!")
    end)
    cleanup()
    H.ok(ok, tostring(err))
  end)

  it("setup보다 먼저 열린 볼트 .md에도 setup 뒤에 적용한다 (플러그인을 늦게 불러온 경우)", function()
    local vault = H.make_vault()
    local ok, err = pcall(function()
      -- 다른 볼트로 setup한 상태 = 이 파일은 아직 볼트 밖
      setup(H.make_vault("다른"))
      H.write_bytes(vault .. "/daily/2026-11-05.md", "## 기록\n- 10:00 먼저 연 파일\n")
      vim.cmd.edit(vim.fn.fnameescape(vault .. "/daily/2026-11-05.md"))
      local buf = vim.api.nvim_get_current_buf()
      H.eq(vim.b[buf].work_kb_vault, nil)
      setup(vault)
      vim.wait(1000, function()
        return vim.b[buf].work_kb_vault == true
      end, 10)
      H.eq(vim.b[buf].work_kb_vault, true)
      H.eq(vim.bo[buf].shiftwidth, 2)
      H.eq(vim.bo[buf].textwidth, 0)
      H.ok(reload_autocmds() > 0, "다시 읽기 autocmd")
      vim.cmd("bwipeout!")
    end)
    cleanup()
    H.ok(ok, tostring(err))
  end)

  it("디스크에서 바뀐 수정 없는 버퍼는 BufEnter에서 묻지 않고 다시 읽고, 수정한 버퍼는 그대로 둔다", function()
    local vault = H.make_vault()
    local ok, err = pcall(function()
      setup(vault)
      local file = vault .. "/wiki/index.md"
      H.write_bytes(file, "# 색인\n")
      vim.cmd.edit(vim.fn.fnameescape(file))
      H.eq(vim.api.nvim_buf_get_lines(0, 0, -1, false), { "# 색인" })
      H.write_bytes(file, "# 색인\n- [[KB-12]] 기계가 다시 썼다\n")
      vim.cmd("doautocmd BufEnter")
      H.eq(vim.api.nvim_buf_get_lines(0, 0, -1, false), { "# 색인", "- [[KB-12]] 기계가 다시 썼다" })
      -- 수정한 버퍼: 디스크가 또 바뀌어도 버퍼 내용을 지킨다
      vim.api.nvim_buf_set_lines(0, 0, 1, false, { "# 내가 고친 제목" })
      H.write_bytes(file, "# 색인\n- 세 번째 내용으로 길이가 다르게\n- 한 줄 더\n")
      vim.cmd("doautocmd BufEnter")
      H.eq(vim.api.nvim_buf_get_lines(0, 0, 1, false), { "# 내가 고친 제목" })
      vim.cmd("bwipeout!")
    end)
    cleanup()
    H.ok(ok, tostring(err))
  end)
end)
