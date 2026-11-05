-- 헤드리스 테스트용 최소 설정: 사용자 설정·플러그인 없이 플러그인 폴더만 runtimepath에 둔다.
-- 실행: nvim --headless -u tests/nvim/minimal_init.lua -l tests/nvim/run.lua (작업 폴더 = 저장소)

-- 플러그인 폴더 (저장소 기준; plugin/·lua/work-kb/가 들어 있는 곳). tests/nvim/**에서 플러그인 자리를
-- 정하는 유일한 상수다 (helpers.lua는 vim.g.work_kb_test_plugin_rel로 받는다). 환경 변수
-- WORK_KB_TEST_PLUGIN_DIR(tests/test_nvim.py가 넘김)이 있으면 그것이 먼저다.
local PLUGIN_REL = "nvim"

local source = debug.getinfo(1, "S").source:sub(2):gsub("\\", "/")
local repo = (source:match("^(.*)/tests/nvim/[^/]+$") or vim.fn.getcwd()):gsub("\\", "/")

local plugin_rel = PLUGIN_REL
local from_env = vim.env.WORK_KB_TEST_PLUGIN_DIR
if from_env and from_env ~= "" then
  from_env = from_env:gsub("\\", "/"):gsub("/+$", "")
  local prefix = repo:lower() .. "/"
  if from_env:lower():sub(1, #prefix) == prefix then
    plugin_rel = from_env:sub(#prefix + 1)
  elseif not from_env:match("^%a:/") and not from_env:match("^/") then
    plugin_rel = from_env -- 저장소 기준 상대 경로
  else
    error("WORK_KB_TEST_PLUGIN_DIR가 저장소 밖입니다: " .. from_env)
  end
end

-- 기본 runtimepath에서 사용자 폴더(설정·데이터·site)만 빼고 저장소의 플러그인 폴더를 더한다.
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
table.insert(keep, repo .. "/" .. plugin_rel)
-- lazy.nvim처럼 플러그인의 after/도 더한다 (after/ftplugin/markdown.lua: 볼트 안 Markdown 버퍼 설정)
table.insert(keep, repo .. "/" .. plugin_rel .. "/after")
vim.opt.runtimepath = keep
vim.opt.packpath = {}
vim.g.loaded_netrw = 1
vim.g.loaded_netrwPlugin = 1
vim.opt.shadafile = "NONE"
vim.opt.swapfile = false
vim.opt.more = false
vim.g.work_kb_test_repo = repo
vim.g.work_kb_test_plugin_rel = plugin_rel
