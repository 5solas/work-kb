-- 상주 서버(kb.py serve)와 한 번 실행의 출력이 같은가: stdout과 stderr를 둘 다 쓰는 명령.
-- 서버는 두 스트림을 쓴 순서대로 담는다. 한 번 실행은 PYTHONUNBUFFERED=1로 같은 순서를 얻는다
-- (버퍼를 쓰는 Python이면 stderr가 먼저 온다: 아래 대조 시험).
local H = require("helpers")

local function proc()
  return require("work-kb.proc")
end

local function server()
  return require("work-kb.server")
end

---UTF-8이 아닌 daily(건너뜀 경고가 stderr로)와 UTF-8 daily가 있는 볼트, 서버 켬.
local function setup_vault()
  local kb = H.read_bytes(H.repo .. "/scripts/kb.py") or ""
  if not (H.exists(H.repo .. "/scripts/serve.py") and kb:find('"serve"', 1, true)) then
    H.not_run("scripts/kb.py serve가 아직 없음")
  end
  local auto = H.exists(H.repo .. "/.venv/Scripts/python.exe")
  local vault = H.with_vault({ python = (not auto) and H.python() or nil, server = { enable = true, idle_sec = 120 } })
  H.copy_config(vault)
  H.write_bytes(vault .. "/config.local.yaml", "jira:\n  project_keys: [KB]\n")
  -- cp949 바이트 ("## 기록\n- 09:00 한글 KB-12\n"): UTF-8로 읽히지 않는다.
  H.write_bytes(vault .. "/daily/2026-11-04.md", "## \177\226\183\207\n- 09:00 \199\209\177\219 KB-12\n")
  H.write_bytes(vault .. "/daily/2026-11-05.md", "---\ndate: 2026-11-05\n---\n## 기록\n- 09:00 한글 회의 [[KB-12]] @김철수\n")
  H.ok(server().start(), "start")
  H.ok(H.wait(function()
    return server().available()
  end, 30000), "서버가 준비되지 않음: " .. vim.inspect(server().status()))
  return vault
end

local function run(args, via)
  local cfg = require("work-kb.config").get()
  cfg.server.enable = via == "server"
  local handle = proc().run(args, { on_exit = function() end })
  H.eq(handle.via, via, table.concat(args, " "))
  local code = proc().wait(handle, 60000)
  return code, H.buffer_text()
end

---link 요약 줄의 걸린 시간("· 0.02초")만 고정 글로 바꾼다. 정확히 한 번 있어야 한다.
local function mask_elapsed(text)
  local masked, n = text:gsub("· %d+%.%d%d초\n", "· <초>초\n")
  H.eq(n, 1, "걸린 시간 표기가 한 번이 아님: " .. text)
  return masked
end

describe("parity", function()
  it("link (stdout 요약 뒤 stderr 건너뜀): 서버와 한 번 실행의 종료 코드·글이 같다", function()
    local vault = setup_vault()
    local args = proc().kb_args("link", "--dry-run", "--rebuild")
    local s_code, s_text = run(args, "server")
    local p_code, p_text = run(args, "process")
    H.eq(s_code, 0, s_text)
    H.contains(s_text, "건너뜀: daily/2026-11-04.md")
    H.contains(s_text, "link(시험 실행, 쓰지 않음): 원천")
    -- 서버 쪽 순서: 요약(stdout)이 건너뜀(stderr)보다 먼저
    H.ok(s_text:find("link(", 1, true) < s_text:find("건너뜀:", 1, true), "서버 출력 순서: " .. s_text)
    H.eq(p_code, s_code, "종료 코드")
    H.eq(mask_elapsed(p_text), mask_elapsed(s_text), "출력 글")
    H.ok(vault)
  end)

  it("대조: 한 번 실행에서 PYTHONUNBUFFERED를 빼면 순서가 달라진다 (위 시험이 순서를 가려낸다)", function()
    setup_vault()
    local args = proc().kb_args("link", "--dry-run", "--rebuild")
    local _, s_text = run(args, "server")
    H.patch(proc(), "ONESHOT_ENV", { PYTHONUNBUFFERED = false })
    local p_code, p_text = run(args, "process")
    H.eq(p_code, 0)
    H.ok(p_text:find("건너뜀:", 1, true) < p_text:find("link(", 1, true), "버퍼를 쓰면 stderr가 먼저: " .. p_text)
    H.ok(mask_elapsed(p_text) ~= mask_elapsed(s_text), "버퍼를 써도 같으면 이 대조는 아무것도 가려내지 못한다")
  end)

  it("search (stderr 건너뜀 뒤 결과)와 결과 없음: 서버와 한 번 실행이 바이트까지 같다", function()
    setup_vault()
    for _, args in ipairs({ proc().kb_args("search", "한글"), proc().kb_args("search", "없는말😀") }) do
      local s_code, s_text = run(args, "server")
      local p_code, p_text = run(args, "process")
      H.eq(s_code, 0, s_text)
      H.contains(s_text, "건너뜀: daily/2026-11-04.md")
      H.eq(p_code, s_code, table.concat(args, " ") .. " 종료 코드")
      H.eq(p_text, s_text, table.concat(args, " ") .. " 출력")
    end
  end)

  it("오류로 끝나는 명령 (종료 4, stderr만): 서버와 한 번 실행이 같다", function()
    setup_vault()
    local args = proc().kb_args("search", "--limit", "-1", "x")
    local s_code, s_text = run(args, "server")
    local p_code, p_text = run(args, "process")
    H.eq(s_code, 4, s_text)
    H.eq(p_code, s_code)
    H.eq(p_text, s_text)
  end)
end)
