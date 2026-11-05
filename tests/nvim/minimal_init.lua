-- 헤드리스 테스트용 최소 설정: 사용자 설정·플러그인 없이 플러그인 폴더(<저장소>/nvim/dev/work-kb)만 runtimepath에 둔다.
-- 실행: nvim --headless -u tests/nvim/minimal_init.lua -l tests/nvim/run.lua (작업 폴더 = 저장소)

local source = debug.getinfo(1, "S").source:sub(2):gsub("\\", "/")
local repo = source:match("^(.*)/tests/nvim/[^/]+$") or vim.fn.getcwd()

-- 기본 runtimepath에서 사용자 폴더(설정·데이터·site)만 빼고 저장소의 nvim/dev/work-kb/를 더한다.
-- 내장 파서(<설치>/lib/nvim/parser)는 남겨 둔다: 0.12의 markdown ftplugin이 treesitter를 켠다.
local function norm(p)
  return (p:gsub("\\", "/"):gsub("/+$", ""):lower())
end
local user_dirs = { norm(vim.fn.stdpath("config")), norm(vim.fn.stdpath("data")) }
local keep = {}
for _, dir in ipairs(vim.opt.runtimepath:get()) do
  local d = norm(dir)
  local user = false
  for _, u in ipairs(user_dirs) do
    if d == u or d:sub(1, #u + 1) == u .. "/" then
      user = true
    end
  end
  if not user then
    table.insert(keep, dir)
  end
end
table.insert(keep, repo .. "/nvim/dev/work-kb")
vim.opt.runtimepath = keep
vim.opt.packpath = {}
vim.g.loaded_netrw = 1
vim.g.loaded_netrwPlugin = 1
vim.opt.shadafile = "NONE"
vim.opt.swapfile = false
vim.opt.more = false
vim.g.work_kb_test_repo = repo
