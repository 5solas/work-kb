-- work-kb: 볼트 안 Markdown 버퍼의 버퍼 설정. 볼트 밖 Markdown과 다른 파일 형식은 건드리지 않는다.
-- 이 저장소가 예전에 따로 갖고 있던 일반 Neovim 설정(nvim/ftplugin, nvim/after/ftplugin, config/autocmds)에서
-- 지식 베이스에 필요한 것만 옮겨 왔다. 일반 편집기 취향(색, 글꼴, 들여쓰기 기본값 등)은 nvim-config의 몫이다.
--
-- after/ 아래에 두는 이유: 런타임 ftplugin/markdown.vim이 들여쓰기 4칸(markdown_recommended_style)을 정한 뒤에
-- 2칸으로 덮어야 한다. lazy.nvim은 플러그인을 불러올 때 <플러그인>/after를 runtimepath에 넣는다.
--
-- 볼트: setup()의 vault, 없으면 코드 루트 (init.lua의 in_vault와 같은 규칙). setup() 전에는 아무것도 하지 않는다
-- (work-kb.config가 아직 불러와지지 않았으면 볼트를 모른다). 화면이 없어도 돌고 묻지 않는다 (nvim-config 데몬 규칙).
--
-- 설정 (모두 버퍼·창 지역):
--   wrap, linebreak, breakindent  긴 기록 줄을 화면에서만 접는다 (파일에는 줄바꿈을 넣지 않는다)
--   textwidth=0                   자동 줄바꿈 없음: 기록 한 줄 = 파일 한 줄 (kb.py가 줄 단위로 읽는다)
--   expandtab, shiftwidth=2 등    목록 들여쓰기 2칸 (kb.py가 쓰는 하위 목록과 같다)
--   nospell                       한글 본문에 맞춤법 밑줄을 긋지 않는다
--   BufEnter·FocusGained          kb.py(예약 작업 포함)가 다시 쓴 파일을 묻지 않고 다시 읽는다. 수정한 버퍼는
--                                 그대로 둔다 (work-kb.fileio.refresh: :checktime, FileChangedShell로 질문 막음)

local api = vim.api
local buf = api.nvim_get_current_buf()

local config = package.loaded["work-kb.config"]
if not config then
  return
end

local function vault_dir()
  local ok, dir = pcall(function()
    local cfg = config.get()
    if type(cfg.vault) == "string" and cfg.vault ~= "" then
      return config.normalize(cfg.vault)
    end
    return config.root()
  end)
  if ok and type(dir) == "string" and dir ~= "" then
    return dir
  end
  return nil
end

local name = api.nvim_buf_get_name(buf)
local dir = name ~= "" and vault_dir() or nil
if not dir then
  return
end
local prefix = vim.fs.normalize(dir):lower()
if prefix:sub(-1) ~= "/" then
  prefix = prefix .. "/"
end
if vim.fs.normalize(name):lower():sub(1, #prefix) ~= prefix then
  return
end

vim.b[buf].work_kb_vault = true

local wo = vim.opt_local
wo.wrap = true
wo.linebreak = true
wo.breakindent = true
wo.spell = false
local bo = vim.bo[buf]
bo.textwidth = 0
bo.expandtab = true
bo.shiftwidth = 2
bo.tabstop = 2
bo.softtabstop = 2

local GROUP = "WorkKbVaultMarkdown"
local group = api.nvim_create_augroup(GROUP, { clear = false })
api.nvim_clear_autocmds({ group = group, buffer = buf })
api.nvim_create_autocmd({ "BufEnter", "FocusGained" }, {
  group = group,
  buffer = buf,
  desc = "work-kb: 디스크에서 바뀐 볼트 파일 다시 읽기 (수정 없는 버퍼만, 묻지 않음)",
  callback = function(ev)
    if api.nvim_get_mode().mode:sub(1, 1) == "c" then
      return
    end
    local ok, fileio = pcall(require, "work-kb.fileio")
    if ok and type(fileio.refresh) == "function" then
      pcall(fileio.refresh, ev.buf)
    end
  end,
})

-- :setlocal filetype=... 로 바뀌면 되돌린다. 런타임 ftplugin처럼 줄바꿈으로 잇는다. :lua와 :au는 쓰지 않는다
-- (둘 다 뒤따르는 줄까지 자기 인수로 삼켜서, 그 뒤의 되돌리기가 깨진다). :call은 줄 끝에서 끝난다.
local undo = "setl wrap< lbr< bri< spell< tw< et< ts< sts< sw<\n"
  .. "sil! call nvim_clear_autocmds({'group': '" .. GROUP .. "', 'buffer': 0})\n"
  .. "unlet! b:work_kb_vault"
local prev = vim.b[buf].undo_ftplugin
vim.b[buf].undo_ftplugin = (type(prev) == "string" and prev ~= "") and (prev .. "\n" .. undo) or undo
