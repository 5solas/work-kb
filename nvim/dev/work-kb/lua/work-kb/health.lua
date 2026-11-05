-- :checkhealth work-kb

local M = {}

function M.check()
  local health = vim.health
  local config = require("work-kb.config")
  local proc = require("work-kb.proc")

  health.start("work-kb: 환경")
  if vim.fn.has("nvim-0.12") == 1 then
    health.ok("Neovim " .. tostring(vim.version()))
  else
    health.error("Neovim 0.12 이상이 필요합니다 (지금 " .. tostring(vim.version()) .. ")")
  end
  if vim.fn.has("win32") == 1 then
    health.ok("Windows")
  else
    health.error("work-kb Neovim 플러그인은 Windows 전용입니다")
  end

  health.start("work-kb: 설정")
  local ok_root, root = pcall(config.root)
  if ok_root then
    health.ok("코드 루트: " .. root)
    if vim.uv.fs_stat(root .. "/scripts/kb.py") then
      health.ok("scripts/kb.py 있음")
    else
      health.error("scripts/kb.py가 없습니다: " .. root)
    end
  else
    health.error(tostring(root))
  end
  local ok_vault, vault = pcall(config.vault)
  if ok_vault then
    health.ok("볼트: " .. vault)
  else
    health.error(tostring(vault))
  end
  local bound = package.loaded["work-kb"] and package.loaded["work-kb"]._bound
  if bound then
    health.ok("접두 키: " .. bound.prefix)
  else
    health.info("접두 키가 붙어 있지 않습니다 (setup의 prefix, 또는 require(\"work-kb\").call(\"<이름>\")을 부르는 매핑)")
  end

  health.start("work-kb: Python")
  if ok_root then
    local ok_py, python = pcall(proc.python_command)
    if ok_py then
      local cmd = vim.list_extend(vim.list_extend({}, python), { "-X", "utf8", "-c", "import sys; print(sys.version.split()[0])" })
      local ok_run, result = pcall(function()
        return vim.system(cmd, { cwd = root, text = true }):wait(15000)
      end)
      if ok_run and result and result.code == 0 then
        health.ok(string.format("%s (Python %s)", table.concat(python, " "), vim.trim(result.stdout or "")))
      else
        health.error(string.format("%s 실행 실패: %s", table.concat(python, " "), ok_run and result and (result.stderr or "") or tostring(result)))
      end
    else
      health.error(tostring(python))
    end
  end
  if vim.fn.executable("rg") == 1 then
    health.ok("rg 있음 (검색)")
  else
    health.info("rg 없음: 검색은 kb.py search로 합니다")
  end

  health.start("work-kb: 상주 서버 (kb.py serve)")
  local cfg = config.get().server or {}
  if not cfg.enable then
    health.info("꺼져 있음 (server.enable = false): 모든 명령을 한 번 실행으로 돌립니다")
    return
  end
  local server = package.loaded["work-kb.server"]
  if not server then
    health.info("아직 띄우지 않았습니다 (VimEnter 뒤 또는 첫 요청 때 띄웁니다)")
    return
  end
  local st = server.status()
  if st.state == "ready" then
    health.ok(string.format("준비됨 (pid %s)", tostring(st.pid)))
  elseif st.state == "starting" then
    health.info("띄우는 중")
  else
    health.warn(string.format("쓰지 못함 (%s): %s — 한 번 실행으로 돌립니다", st.state, tostring(st.last_error or "-")))
    if st.stderr ~= "" then
      health.info("서버 stderr:\n" .. st.stderr)
    end
  end
end

return M
