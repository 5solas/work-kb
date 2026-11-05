-- work-kb: 업무 메모 캡처와 위키 연결 (Neovim 0.12, Windows).
--
-- 설치 (lazy.nvim 예):
--   { dir = "D:/work/work-kb/nvim", opts = { root = "D:/work/work-kb", vault = "D:/notes/work-kb" } }
--   (nvim-config 저장소의 lua/plugins/work-kb.lua가 환경 변수 WORK_KB_ROOT로 이 일을 한다)
-- 또는: vim.opt.rtp:append("D:/work/work-kb/nvim"); require("work-kb").setup({ ... })
-- root를 주지 않으면 이 파일의 실제 경로(정션·심볼릭 링크를 푼 것)에서 정한다 (첫 사용 때).
--
-- 키 (접두 키 다음, 기본 <leader>k):
--   c 캡처        C 클립보드 이미지   F 이미지 파일   i inbox 메모
--   t 오늘 daily  s 검색             o 커서 위치 열기  l link 실행
--   ? 상태        j Jira 작업        g 편찬(ingest → compile)  G 편찬 미리 보기(--dry-run)
--   a 주제 제안 승인
-- 비주얼 모드: c·i는 선택한 글을 캡처·inbox 버퍼에 미리 넣는다.
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

---비주얼 모드 키 (x): 선택한 글을 캡처·inbox 버퍼에 미리 넣는다 (Emacs: region이 있으면 그 글).
---fn은 선택 글 하나를 받는다: capture.open(prefill), capture.inbox(prefill).
M.visual_keys = {
  { key = "c", name = "capture", module = "work-kb.capture", fn = "open" },
  { key = "i", name = "capture-inbox", module = "work-kb.capture", fn = "inbox" },
}

---비주얼 모드에서 ENTRY를 부른다: 비주얼 모드를 빠져나와 선택 글을 읽고 그 글로 부른다.
function M.call_visual(entry)
  local ok, text = pcall(function()
    return require("work-kb.ui").selection()
  end)
  if not ok then
    echo_error(tostring(text))
    return false
  end
  return M.call({
    name = entry.name,
    module = entry.module,
    fn = entry.fn,
    arg = function()
      return text
    end,
  })
end

M._bound = nil -- { prefix = "<leader>k", lhs = { ... }, xlhs = { ... } }

local DESC = "work-kb: "

local function del_if_ours(mode, lhs)
  local current = vim.fn.maparg(lhs, mode, false, true)
  if type(current) == "table" and type(current.desc) == "string" and current.desc:sub(1, #DESC) == DESC then
    pcall(vim.keymap.del, mode, lhs)
  end
end

---접두 키에 키를 붙인다 (보통 모드 n 전부, 비주얼 모드 x의 c·i). 전에 붙인 접두와 다르면 옛 키는
---아직 work-kb 것일 때만 뗀다. PREFIX가 false·nil·""이면 붙이지 않는다.
function M.rebind_prefix(prefix)
  local old = M._bound
  if old and old.prefix ~= prefix then
    for _, lhs in ipairs(old.lhs) do
      del_if_ours("n", lhs)
    end
    for _, lhs in ipairs(old.xlhs or {}) do
      del_if_ours("x", lhs)
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
  local xlhs_list = {}
  for _, entry in ipairs(M.visual_keys) do
    local lhs = prefix .. entry.key
    vim.keymap.set("x", lhs, function()
      M.call_visual(entry)
    end, { desc = DESC .. entry.name .. " (선택 글)", silent = true })
    table.insert(xlhs_list, lhs)
  end
  M._bound = { prefix = prefix, lhs = lhs_list, xlhs = xlhs_list }
  return prefix
end

local visual_by_name = {}
for _, entry in ipairs(M.visual_keys) do
  visual_by_name[entry.name] = entry
end

---:WorkKb에 준 범위의 글: '<,'>(마지막 비주얼 선택과 같은 줄)면 그 선택(글자·줄·블록 단위 그대로),
---아니면 LINE1..LINE2 줄 전체 (\n으로 잇고 끝 줄바꿈 없음).
local function range_text(opts)
  local start, finish = vim.fn.getpos("'<"), vim.fn.getpos("'>")
  if vim.fn.visualmode() ~= "" and start[2] == opts.line1 and finish[2] == opts.line2 then
    local text = require("work-kb.ui").selection()
    if text then
      return text
    end
  end
  return table.concat(vim.api.nvim_buf_get_lines(0, opts.line1 - 1, opts.line2, false), "\n")
end

---Jira 작업 이름 (:WorkKb jira <이름>): 메뉴의 함수 이름, `_`는 `-`로 (check-done).
function M.jira_actions()
  local names = {}
  local ok, jira = pcall(require, "work-kb.jira")
  if ok and type(jira.MENU) == "table" then
    for _, group in ipairs(jira.MENU) do
      for _, item in ipairs(group.items) do
        table.insert(names, (item.fn:gsub("_", "-")))
      end
    end
  end
  return names
end

---:WorkKb <이름> [인수] 처리. 범위(:'<,'>WorkKb capture)를 주면 capture·capture-inbox는 그 글로
---부른다 (비주얼 키와 같다). `:WorkKb jira <작업>`은 Jira 메뉴 없이 그 작업을 바로 부른다.
function M.command(opts)
  local fargs = opts.fargs or {}
  local name = fargs[1]
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
  if name == "jira" and fargs[2] then
    if fargs[3] then
      echo_error("work-kb: 인수가 너무 많습니다: " .. table.concat(fargs, " "))
      return
    end
    local action = fargs[2]
    return M.call({
      name = "jira " .. action,
      module = "work-kb.jira",
      fn = "run",
      arg = function()
        return action
      end,
    })
  end
  if fargs[2] then
    echo_error(string.format("work-kb: %s는 인수를 받지 않습니다: %s", name, table.concat(fargs, " ", 2)))
    return
  end
  local visual = visual_by_name[name]
  if visual and (opts.range or 0) > 0 then
    local ok, text = pcall(range_text, opts)
    if not ok then
      echo_error(tostring(text))
      return
    end
    return M.call({
      name = visual.name,
      module = visual.module,
      fn = visual.fn,
      arg = function()
        return text
      end,
    })
  end
  return M.call(entry)
end

---:WorkKb 완성: 첫 인수는 명령 이름, `jira` 다음은 Jira 작업 이름.
function M.complete(arglead, cmdline)
  arglead = arglead or ""
  local candidates = {}
  local before = (cmdline or ""):gsub("^.-WorkKb!?%s*", "", 1)
  local words = vim.split(before, "%s+", { trimempty = true })
  local complete_words = #words - ((arglead ~= "" and #words > 0) and 1 or 0)
  if complete_words == 0 then
    for _, entry in ipairs(M.keys) do
      table.insert(candidates, entry.name)
    end
  elseif complete_words == 1 and words[1] == "jira" then
    candidates = M.jira_actions()
  end
  local out = {}
  for _, c in ipairs(candidates) do
    if c:sub(1, #arglead) == arglead then
      table.insert(out, c)
    end
  end
  return out
end

---:WorkKb 명령의 정의 (plugin/work-kb.lua와 같다).
M.COMMAND_OPTS = {
  nargs = "*",
  range = true,
  desc = "work-kb 명령",
}

local function create_command()
  vim.api.nvim_create_user_command(
    "WorkKb",
    function(o)
      M.command(o)
    end,
    vim.tbl_extend("force", M.COMMAND_OPTS, {
      complete = function(arglead, cmdline)
        return M.complete(arglead, cmdline)
      end,
    })
  )
end

---경로 PATH가 볼트 아래인가 (Emacs work-kb--vault-file-p: 볼트를 설정했으면 볼트, 아니면 코드 루트).
---코드 루트는 config.root()로 정한다 (정션 아래 설치에서도 실제 저장소). 대소문자 무시 (Windows).
---실패하면 거짓.
local function in_vault(path)
  local config = require("work-kb.config")
  local ok, dir = pcall(function()
    local cfg = config.get()
    if type(cfg.vault) == "string" and cfg.vault ~= "" then
      return config.normalize(cfg.vault)
    end
    return config.root()
  end)
  if not ok or type(dir) ~= "string" then
    return false
  end
  dir = dir:lower()
  local file = vim.fs.normalize(path):lower()
  local prefix = dir:sub(-1) == "/" and dir or (dir .. "/")
  return file:sub(1, #prefix) == prefix
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
        -- warmup: 메인 루프를 막지 않는다 (`py -3` 시험은 비동기, 화면 없는 미리 띄운 Neovim에서도 안전).
        require("work-kb.server").start({ warmup = true })
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

---이미 열려 있는 볼트 Markdown 버퍼에 after/ftplugin/markdown.lua를 적용한다.
---플러그인이 늦게 불러와지면(데몬 밖의 nvim에서 첫 <leader>k 키) 그 전에 연 버퍼는 FileType이 이미 지나갔다.
---이 플러그인의 파일만 직접 읽는다 (다른 플러그인의 after/ftplugin은 다시 돌리지 않는다).
local function apply_ftplugin_to_open_buffers()
  local here = debug.getinfo(1, "S").source:sub(2):gsub("\\", "/")
  local plugin_dir = here:match("^(.*)/lua/work%-kb/[^/]+$")
  local file = plugin_dir and (plugin_dir .. "/after/ftplugin/markdown.lua")
  if not file or not vim.uv.fs_stat(file) then
    return
  end
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.bo[buf].filetype == "markdown" and not vim.b[buf].work_kb_vault then
      local name = vim.api.nvim_buf_get_name(buf)
      if name ~= "" and in_vault(name) then
        pcall(vim.api.nvim_buf_call, buf, function()
          vim.cmd.source(vim.fn.fnameescape(file))
        end)
      end
    end
  end
end

---설정하고 키·명령·자동 명령을 정의한다. 다시 불러도 된다 (접두 키가 바뀌면 옮겨 붙인다).
---@param opts? {root?: string, vault?: string, python?: string|string[], prefix?: string|false, server?: {enable?: boolean, idle_sec?: integer, delay_ms?: integer}, now?: fun(): integer}
function M.setup(opts)
  local config = require("work-kb.config")
  config.setup(opts)
  local cfg = config.get()
  local proc = package.loaded["work-kb.proc"]
  if proc then
    proc._py_cache = nil -- 설정(python·root)이 바뀌었을 수 있다
  end
  M.rebind_prefix(cfg.prefix)
  create_command()
  define_autocmds()
  vim.schedule(function()
    pcall(apply_ftplugin_to_open_buffers)
  end)
  -- 설정이 바뀌었으면 떠 있는 서버를 내린다 (다음 요청이 새 설정으로 다시 띄운다).
  local server = package.loaded["work-kb.server"]
  if server and not (cfg.server and cfg.server.enable) then
    pcall(server.stop)
  end
end

return M
