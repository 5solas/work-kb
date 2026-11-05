-- t 오늘 daily (ERT today-forces-prepare, today-live-buffer) + ensure-today·뼈대.
local H = require("helpers")

local DAILY_LF = "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
  .. "## 기록\n- 08:00 첫 기록\n\n## 메모\n자유 텍스트\n"

local SKELETON = "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
  .. "## 이월\n<!-- kb:carryover:start -->\n<!-- kb:carryover:end -->\n\n## 기록\n"

local function daily()
  return require("work-kb.daily")
end

local bump = 0
local function rewrite_on_disk(file, text)
  H.write_bytes(file, text)
  -- Windows의 Neovim은 나노초가 같으면 1초 차이를 바뀐 것으로 보지 않는다: 몇 초씩 벌린다.
  bump = bump + 5
  local t = os.time() + 10 + bump
  assert(vim.uv.fs_utime(file, t, t))
end

local function wipe_under(dir)
  local prefix = vim.fs.normalize(dir):lower() .. "/"
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    local name = vim.fs.normalize(vim.api.nvim_buf_get_name(b)):lower()
    if name:sub(1, #prefix) == prefix then
      pcall(vim.api.nvim_buf_delete, b, { force = true })
    end
  end
end

local function with_vault(opts, body)
  local vault = H.with_vault(opts)
  local ok, err = pcall(body, vault)
  wipe_under(vault)
  if not ok then
    error(err, 0)
  end
end

local function lines_text(buf)
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end

local function same_file(a, b)
  return vim.fs.normalize(a):lower() == vim.fs.normalize(b):lower()
end

local function count(text, needle)
  local n, start = 0, 1
  while true do
    local s, e = text:find(needle, start, true)
    if not s then
      return n
    end
    n, start = n + 1, e + 1
  end
end

describe("daily", function()
  it("경로와 뼈대 바이트 (notes.daily_skeleton)", function()
    with_vault({}, function(vault)
      H.eq(daily().path(), vault .. "/daily/2026-11-05.md")
      H.eq(daily().skeleton("2026-11-05"), SKELETON)
    end)
  end)

  it("ensure-today: 있으면 부르지 않음, FORCE면 부름, 실행 오류·실패에도 뼈대 (덮어쓰지 않음)", function()
    with_vault({ python = H.python() }, function(vault)
      local file = vault .. "/daily/2026-11-05.md"
      local stub = H.ui_stub()
      -- 실행이 성공했지만 파일을 만들지 않았다: 메시지 없이 뼈대
      local fake = H.fake_spawn({ code = 0 })
      H.eq(daily().ensure_today(), file)
      H.eq(#fake.calls, 1)
      H.eq(H.read_bytes(file), SKELETON)
      H.eq(stub.messages, {})
      -- 이미 있으면 부르지 않는다
      daily().ensure_today()
      H.eq(#fake.calls, 1)
      -- FORCE: 있어도 부르고, 실패하면 알리되 파일은 그대로
      H.write_bytes(file, DAILY_LF)
      fake = H.fake_spawn({ code = 6, output = "잠김\n" })
      daily().ensure_today(true)
      H.eq(fake.calls[1].argv, { "today", "--root", vault, "--date", "2026-11-05" })
      H.eq(H.read_bytes(file), DAILY_LF)
      H.eq(stub.messages, { "work-kb: daily_prepare 실패 (종료 6). *work-kb* 버퍼를 보세요" })
      H.eq(H.buffer_text(), "잠김\n")
      -- 실행 자체가 오류 (Python 없음 등): 두 줄 알리고 뼈대
      vim.uv.fs_unlink(file)
      H.fake_spawn(function()
        error("work-kb: 실행하지 못했습니다 (python): ENOENT", 0)
      end)
      stub.messages = {}
      daily().ensure_today()
      H.eq(stub.messages, {
        "work-kb: today 실행 실패: work-kb: 실행하지 못했습니다 (python): ENOENT",
        "work-kb: daily_prepare 실패 (종료 nil). *work-kb* 버퍼를 보세요",
      })
      H.eq(H.read_bytes(file), SKELETON)
    end)
  end)

  it("today-forces-prepare: 파일이 있어도 kb.py today --date를 부르고 그 파일을 연다", function()
    with_vault({ python = H.python() }, function(vault)
      local file = vault .. "/daily/2026-11-05.md"
      H.write_bytes(file, DAILY_LF)
      local stub = H.ui_stub()
      local fake = H.fake_spawn({ code = 0 })
      daily().today()
      H.ok(same_file(vim.api.nvim_buf_get_name(0), file), "연 파일: " .. vim.api.nvim_buf_get_name(0))
      H.eq(#fake.calls, 1)
      H.eq(fake.calls[1].argv, { "today", "--root", vault, "--date", "2026-11-05" })
      H.eq(stub.messages, {})
      H.eq(vim.bo.fileformat, "unix")
      H.eq(lines_text(0), (DAILY_LF:gsub("\n$", "")))
    end)
  end)

  it("today: 파일이 없고 준비가 실패해도 뼈대를 만들어 연다", function()
    with_vault({ python = H.python() }, function(vault)
      local file = vault .. "/daily/2026-11-05.md"
      local stub = H.ui_stub()
      H.fake_spawn({ code = 1 })
      daily().today()
      H.ok(same_file(vim.api.nvim_buf_get_name(0), file))
      H.eq(H.read_bytes(file), SKELETON)
      H.eq(stub.messages, { "work-kb: daily_prepare 실패 (종료 1). *work-kb* 버퍼를 보세요" })
    end)
  end)

  it("today-live-buffer: 수정 없으면 다시 읽고, 수정 있으면 먼저 저장, 둘 다 바뀌면 건너뜀", function()
    with_vault({ python = H.python() }, function(vault)
      local file = vault .. "/daily/2026-11-05.md"
      local seen = {}
      H.write_bytes(file, DAILY_LF)
      local stub = H.ui_stub()
      -- daily_prepare 흉내: 디스크 내용을 읽어 오늘 구역에 한 줄을 넣고 다시 쓴다.
      H.fake_spawn(function()
        local text = H.read_bytes(file)
        table.insert(seen, text)
        rewrite_on_disk(file, (text:gsub("<!%-%- kb:today:start %-%->\n", "<!-- kb:today:start -->\n- [ ] KB-1 준비됨\n", 1)))
        return "", 0
      end)
      local buf = vim.fn.bufadd(file)
      vim.fn.bufload(buf)
      -- 1) 수정 없음: 묻지 않고 다시 읽는다
      daily().today()
      H.eq(vim.api.nvim_get_current_buf(), buf)
      H.eq(#seen, 1)
      H.eq(vim.bo[buf].modified, false)
      H.contains(lines_text(buf), "KB-1 준비됨")
      -- 2) 저장 안 한 수정: 준비 전에 저장되어 daily_prepare가 보고, 버퍼에도 남는다
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "저장 안 한 글" })
      daily().today()
      H.eq(#seen, 2)
      H.contains(seen[2], "저장 안 한 글")
      H.contains(H.read_bytes(file), "저장 안 한 글\n")
      H.eq(vim.bo[buf].modified, false)
      H.contains(lines_text(buf), "저장 안 한 글")
      H.eq(count(lines_text(buf), "KB-1 준비됨"), 2)
      -- 3) 버퍼도 수정, 디스크도 바뀜: 준비를 건너뛰고 둘 다 그대로
      vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "둘 다 바뀜" })
      local disk = H.read_bytes(file) .. "외부 변경\n"
      rewrite_on_disk(file, disk)
      daily().today()
      H.eq(vim.api.nvim_get_current_buf(), buf)
      H.eq(stub.messages, {
        "work-kb: 2026-11-05.md 버퍼와 파일이 모두 바뀌어 today 준비를 건너뜁니다. 먼저 정리하세요",
      })
      H.eq(#seen, 2)
      H.eq(H.read_bytes(file), disk)
      H.eq(vim.bo[buf].modified, true)
      H.contains(lines_text(buf), "둘 다 바뀜")
      H.not_contains(lines_text(buf), "외부 변경")
    end)
  end)
end)
