-- 설정 공용 도우미. 불러올 때는 아무 I/O도 하지 않는다.
local M = {}

---scoop shim을 건너뛴 실제 실행 파일 경로. <shims>\<name>.shim의 `path = "..."`를 읽는다.
---shim이 아니거나 읽지 못하면 PATH에서 찾은 경로, 그것도 없으면 NAME 그대로.
---@param name string
---@return string
function M.unshim(name)
  local found = vim.fn.exepath(name)
  if found == '' then
    return name
  end
  local shim = found:gsub('%.[Ee][Xx][Ee]$', '') .. '.shim' -- exepath는 'rg.EXE'처럼 대문자 확장자를 줄 수 있다
  local fd = vim.uv.fs_open(shim, 'r', 438)
  if not fd then
    return found
  end
  local st = vim.uv.fs_fstat(fd)
  local data = st and st.size > 0 and vim.uv.fs_read(fd, st.size, 0) or ''
  vim.uv.fs_close(fd)
  local target = data:match('path%s*=%s*"([^"]+)"') or data:match('path%s*=%s*(%S+)')
  if target and vim.uv.fs_stat(target) then
    return target
  end
  return found
end

---저장소 루트: 설정 폴더(stdpath('config'), 정션이면 실제 경로)의 부모. 슬래시, 끝 슬래시 없음.
---@return string
function M.repo_root()
  local config = vim.fn.stdpath('config') --[[@as string]]
  local real = vim.uv.fs_realpath(config) or config
  return vim.fs.dirname(vim.fs.normalize(real))
end

---PATH가 볼트(설정의 vault, 없으면 저장소 루트) 아래인가. 대소문자 무시.
---@param path string
---@return boolean
function M.is_vault_path(path)
  if type(path) ~= 'string' or path == '' then
    return false
  end
  local s = require('config.settings')
  local base = s.vault or s.root
  if type(base) ~= 'string' or base == '' then
    return false
  end
  local function norm(p)
    return (vim.fs.normalize(vim.fn.fnamemodify(p, ':p')):gsub('/+$', ''):lower())
  end
  local file, dir = norm(path), norm(base)
  return file:sub(1, #dir + 1) == dir .. '/'
end

---실제 nvim.exe 옆의 win32yank.exe (없으면 nil).
---@return string?
function M.win32yank()
  local exe = vim.fs.dirname(vim.v.progpath) .. '/win32yank.exe'
  if vim.uv.fs_stat(exe) then
    return exe
  end
  return nil
end

return M
