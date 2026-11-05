-- work-kb: 업무 메모 캡처와 위키 연결 (Neovim 0.12, Windows).
--
-- 설치 (lazy.nvim 예):
--   { dir = "D:/work/work-kb/nvim", opts = { root = "D:/work/work-kb", vault = "D:/notes/work-kb" } }
-- 또는: vim.opt.rtp:append("D:/work/work-kb/nvim"); require("work-kb").setup({ ... })
--
-- 키 (접두 키 다음, 기본 <leader>k):
--   c 캡처        C 클립보드 이미지   F 이미지 파일   i inbox 메모
--   t 오늘 daily  s 검색             o 커서 위치 열기  l link 실행
--   ? 상태        j Jira 작업        g 편찬(ingest → compile)  G 편찬 미리 보기(--dry-run)
--   a 주제 제안 승인
-- 같은 명령: :WorkKb <이름>. 직접 붙이기: vim.keymap.set("n", "<F8>c", function() require("work-kb").call("capture") end)
-- 또는 setup({ prefix = "<F8>" }). 접두 키를 바꾸려면 setup을 다시 부른다 (옛 키는 떼어 낸다).
--
-- setup()은 I/O를 하지 않고 config 말고는 아무 모듈도 불러오지 않는다. 기능 모듈은 키를
-- 처음 누를 때 불러오고, 상주 서버(kb.py serve)는 VimEnter 뒤에 띄운다.

local M = {}

---키, 명령 이름, 모듈, 함수, 인수(함수: 부를 때마다 새로 만든다).
M.keys = {
  { key = "c", name = "capture", module = "work-kb.capture", fn = "capture" },
  { key = "C", name = "capture-clipboard-image", module = "work-kb.capture", fn = "clipboard_image" },
  { key = "F", name = "capture-image-file", module = "work-kb.capture", fn = "image_file" },
  { key = "i", name = "capture-inbox", module = "work-kb.capture", fn = "inbox" },
  { key = "t", name = "today", module = "work-kb.daily", fn = "today" },
  { key = "s", name = "search", module = "work-kb.search", fn = "search" },
  { key = "o", name = "open-at-point", module = "work-kb.open", fn = "open_at_point" },
  { key = "l", name = "link", module = "work-kb.misc", fn = "link" },
  { key = "?", name = "status", module = "work-kb.misc", fn = "status" },
  { key = "j", name = "jira", module = "work-kb.jira", fn = "menu" },
  {
    key = "g",
    name = "ingest",
    module = "work-kb.compile",
    fn = "ingest_compile",
    arg = function()
      return { dry_run = false }
    end,
  },
  {
    key = "G",
    name = "ingest-dry-run",
    module = "work-kb.compile",
    fn = "ingest_compile",
    arg = function()
      return { dry_run = true }
    end,
  },
  { key = "a", name = "proposals", module = "work-kb.compile", fn = "proposals" },
}

local by_name = {}
for _, entry in ipairs(M.keys) do
  by_name[entry.name] = entry
end

local function echo_error(msg)
  vim.api.nvim_echo({ { msg, "ErrorMsg" } }, true, {})
end

---ENTRY의 기능을 부른다. 사용자 오류는 문구만, 그 밖의 오류는 traceback과 함께 보인다.
function M.call(entry)
  if type(entry) == "string" then
    local name = entry
    entry = by_name[name]
    if not entry then
      echo_error("work-kb: 알 수 없는 명령: " .. name)
      return false
    end
  end
  local ok, err = xpcall(function()
    local mod = require(entry.module)
    local fn = mod[entry.fn]
    if type(fn) ~= "function" then
      error(string.format("work-kb: %s.%s 함수가 없습니다", entry.module, entry.fn), 0)
    end
    if entry.arg then
      return fn(entry.arg())
    end
    return fn()
  end, function(e)
    local ui = package.loaded["work-kb.ui"]
    if ui and ui.is_user_error(e) then
      return e
    end
    return debug.traceback(tostring(e), 2)
  end)
  if not ok then
    echo_error(tostring(err))
  end
  return ok
end

M._bound = nil -- { prefix = "<leader>k", lhs = { ... } }

local DESC = "work-kb: "

---접두 키에 키를 붙인다. 전에 붙인 접두와 다르면 옛 키는 아직 work-kb 것일 때만 뗀다.
---PREFIX가 false·nil·""이면 붙이지 않는다.
function M.rebind_prefix(prefix)
  local old = M._bound
  if old and old.prefix ~= prefix then
    for _, lhs in ipairs(old.lhs) do
      local current = vim.fn.maparg(lhs, "n", false, true)
      if type(current) == "table" and type(current.desc) == "string" and current.desc:sub(1, #DESC) == DESC then
        pcall(vim.keymap.del, "n", lhs)
      end
    end
    M._bound = nil
  end
  if type(prefix) ~= "string" or prefix == "" then
    M._bound = nil
    return nil
  end
  local lhs_list = {}
  for _, entry in ipairs(M.keys) do
    local lhs = prefix .. entry.key
    vim.keymap.set("n", lhs, function()
      M.call(entry)
    end, { desc = DESC .. entry.name, silent = true })
    table.insert(lhs_list, lhs)
  end
  M._bound = { prefix = prefix, lhs = lhs_list }
  return prefix
end

---:WorkKb <이름> 처리.
function M.command(opts)
  local name = opts.fargs and opts.fargs[1]
  if not name or name == "" then
    local names = {}
    for _, entry in ipairs(M.keys) do
      table.insert(names, entry.name)
    end
    vim.api.nvim_echo({ { "work-kb: " .. table.concat(names, " ") } }, false, {})
    return
  end
  local entry = by_name[name]
  if not entry then
    echo_error("work-kb: 알 수 없는 명령: " .. name)
    return
  end
  M.call(entry)
end

---:WorkKb 완성.
function M.complete(arglead)
  local out = {}
  for _, entry in ipairs(M.keys) do
    if entry.name:sub(1, #(arglead or "")) == (arglead or "") then
      table.insert(out, entry.name)
    end
  end
  return out
end

local function create_command()
  vim.api.nvim_create_user_command("WorkKb", function(o)
    M.command(o)
  end, {
    nargs = "?",
    complete = function(arglead)
      return M.complete(arglead)
    end,
    desc = "work-kb 명령",
  })
end

---경로 PATH가 볼트(또는 코드 루트) 아래인가. 대소문자 무시 (Windows). 실패하면 거짓.
local function in_vault(path)
  local config = require("work-kb.config")
  local ok, dirs = pcall(function()
    local cfg = config.get()
    local out = {}
    for _, dir in ipairs({ cfg.vault, cfg.root }) do
      if type(dir) == "string" and dir ~= "" then
        table.insert(out, config.normalize(dir):lower())
      end
    end
    return out
  end)
  if not ok then
    return false
  end
  local file = vim.fs.normalize(path):lower()
  for _, dir in ipairs(dirs) do
    local prefix = dir:sub(-1) == "/" and dir or (dir .. "/")
    if file:sub(1, #prefix) == prefix then
      return true
    end
  end
  return false
end

local function server_cfg()
  local server = require("work-kb.config").get().server
  return type(server) == "table" and server or {}
end

local function schedule_server()
  local cfg = server_cfg()
  if not cfg.enable then
    return
  end
  vim.defer_fn(function()
    if server_cfg().enable then
      pcall(function()
        require("work-kb.server").start()
      end)
    end
  end, cfg.delay_ms or 300)
end

local function define_autocmds()
  local group = vim.api.nvim_create_augroup("WorkKb", { clear = true })
  -- 볼트 .md (D11): 새 파일은 UTF-8·LF, 기존 파일은 EOL을 그대로 두고 자동 되읽기.
  vim.api.nvim_create_autocmd("BufNewFile", {
    group = group,
    pattern = "*.md",
    callback = function(ev)
      if in_vault(ev.match) then
        vim.bo[ev.buf].fileformat = "unix"
        vim.bo[ev.buf].fileencoding = "utf-8"
        vim.bo[ev.buf].autoread = true
      end
    end,
  })
  vim.api.nvim_create_autocmd("BufReadPost", {
    group = group,
    pattern = "*.md",
    callback = function(ev)
      if in_vault(ev.match) then
        vim.bo[ev.buf].autoread = true
      end
    end,
  })
  if vim.v.vim_did_enter == 1 then
    schedule_server()
  else
    vim.api.nvim_create_autocmd("VimEnter", { group = group, once = true, callback = schedule_server })
  end
  vim.api.nvim_create_autocmd("VimLeavePre", {
    group = group,
    callback = function()
      local server = package.loaded["work-kb.server"]
      if server then
        pcall(server.stop, true)
      end
    end,
  })
end

---설정하고 키·명령·자동 명령을 정의한다. 다시 불러도 된다 (접두 키가 바뀌면 옮겨 붙인다).
---@param opts? {root?: string, vault?: string, python?: string|string[], prefix?: string|false, server?: {enable?: boolean, idle_sec?: integer, delay_ms?: integer}, now?: fun(): integer}
function M.setup(opts)
  local config = require("work-kb.config")
  config.setup(opts)
  local cfg = config.get()
  M.rebind_prefix(cfg.prefix)
  create_command()
  define_autocmds()
  -- 설정이 바뀌었으면 떠 있는 서버를 내린다 (다음 요청이 새 설정으로 다시 띄운다).
  local server = package.loaded["work-kb.server"]
  if server and not (cfg.server and cfg.server.enable) then
    pcall(server.stop)
  end
end

return M
