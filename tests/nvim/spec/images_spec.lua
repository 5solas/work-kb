-- C 클립보드 이미지, F 이미지 파일 (ERT clipboard-no-image, clipboard-saved, clipboard-empty-name,
-- clipboard-error-cleans-temp, link-safe-asset-and-inbox, image-file-never-overwrites).
-- PowerShell은 proc._spawn 이음매로 바꿔 끼운다: 실제 클립보드는 건드리지 않는다.
local H = require("helpers")

local DAILY_LF = "---\ndate: 2026-11-05\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n"
  .. "## 기록\n- 08:00 첫 기록\n\n## 메모\n자유 텍스트\n"

local PS_ARGS = { "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA", "-File", "scripts/clip_image.ps1", "-Out" }

local function capture()
  return require("work-kb.capture")
end

---볼트 안 파일 목록 (점으로 시작하는 이름 제외).
local function files_in(dir)
  local out = {}
  local handle = vim.uv.fs_scandir(dir)
  while handle do
    local name = vim.uv.fs_scandir_next(handle)
    if not name then
      break
    end
    if name:sub(1, 1) ~= "." then
      table.insert(out, name)
    end
  end
  table.sort(out)
  return out
end

---코드 루트도 볼트로 (ERT: work-kb-root = vault). scripts/kb.py 자리 표시 파일을 둔다.
local function clip_vault()
  local vault = H.make_vault()
  H.write_bytes(vault .. "/scripts/kb.py", "# 자리 표시\n")
  H.configure({ root = vault, vault = vault })
  H.write_bytes(vault .. "/daily/2026-11-05.md", DAILY_LF)
  return vault
end

---clip_image.ps1 대신: -Out 경로에 가짜 PNG를 만들고 OUTPUT을 낸 뒤 CODE로 끝난다.
local function fake_clip(code, output, make_file)
  return H.fake_spawn(function(call)
    if make_file ~= false then
      H.write_bytes(call.cwd .. "/" .. call.cmd[#call.cmd], "\137PNG\r\n\26\nfake")
    end
    return output, code
  end)
end

describe("images", function()
  it("clipboard-no-image: 종료 2 → 안내, 묻지 않음, 정확한 PowerShell 인수, PS* 환경 변수를 지움", function()
    local vault = clip_vault()
    local stub = H.ui_stub()
    local fake = fake_clip(2, nil, false)
    H.eq(capture().clipboard_image(), nil)
    H.eq(stub.messages, { "클립보드에 이미지가 없습니다" })
    H.eq(#stub.prompts, 0)
    H.eq(#fake.calls, 1)
    local call = fake.calls[1]
    H.ok(call.cmd[1]:find("/WindowsPowerShell/v1%.0/powershell%.exe$"), call.cmd[1])
    H.eq(call.cmd[1], (vim.env.SystemRoot:gsub("\\", "/")) .. "/System32/WindowsPowerShell/v1.0/powershell.exe")
    H.eq(vim.list_slice(call.cmd, 2, #call.cmd - 1), PS_ARGS)
    H.eq(call.cmd[#call.cmd], ".state/tmp/clip-20261105-091200.png")
    for _, arg in ipairs(call.cmd) do
      H.ok(H.ascii(arg), "명령줄에 ASCII 밖 글자: " .. arg)
    end
    H.eq(call.cwd, require("work-kb.config").root())
    H.eq(call.spec.merge, true, "stdout·stderr 합침")
    H.eq(call.stdin, nil)
    H.eq(call.spec.env, { PSModulePath = false, PSExecutionPolicyPreference = false }, "PS* 환경 변수를 지운다")
    H.eq(files_in(vault .. "/raw/assets"), {})
    H.eq(H.read_bytes(vault .. "/daily/2026-11-05.md"), DAILY_LF)
  end)

  it("PS* 환경 변수 제거는 실제 자식 프로세스에 닿는다 (같은 호출의 명령만 Python으로)", function()
    local vault = clip_vault()
    local saved = { vim.env.PSModulePath, vim.env.PSExecutionPolicyPreference }
    vim.env.PSModulePath = "C:/pwsh7/Modules"
    vim.env.psexecutionpolicypreference = "RemoteSigned" -- 대소문자가 달라도 지운다
    local ok, err = pcall(function()
      local script = "import os,sys;print(repr(os.environ.get('PSModulePath')), repr(os.environ.get('PSExecutionPolicyPreference')),"
        .. " repr(os.environ.get('SystemRoot') is not None), repr(sys.argv[1:]))"
      H.rewrite_spawn(function()
        return { H.python(), "-c", script, "-Out", "x" }
      end)
      local code, output = capture()._call_clip(".state/tmp/x.png", require("work-kb.config").root())
      H.eq(code, 0)
      H.eq(output, "None None True ['-Out', 'x']")
      H.eq(vim.env.PSModulePath, "C:/pwsh7/Modules", "Neovim 자신의 환경은 그대로")
    end)
    vim.env.PSModulePath = saved[1]
    vim.env.PSExecutionPolicyPreference = saved[2]
    if not ok then
      error(err, 0)
    end
    H.ok(vault)
  end)
  it("실제 powershell.exe 5.1 + clip_image.ps1 (-FromFile 이음매: 클립보드는 건드리지 않음)", function()
    local vault = clip_vault()
    H.copy_file(H.repo .. "/scripts/clip_image.ps1", vault .. "/scripts/clip_image.ps1")
    local png = vault .. "/inbox/원본.png"
    -- 저장소의 허구 픽스처 PNG를 쓴다(소스에 base64 문자열을 두면 비밀값 스캔에 걸린다)
    H.copy_file(H.repo .. "/tests/fixtures/images/mail.png", png)
    local from = "inbox/src.png"
    local rec = H.rewrite_spawn(function(spec)
      local cmd = vim.deepcopy(spec.cmd)
      vim.list_extend(cmd, { "-FromFile", from })
      return cmd
    end)
    -- -FromFile 인수는 ASCII로: 볼트 안 ASCII 이름으로 복사해 둔다
    H.copy_file(png, vault .. "/inbox/src.png")
    local stub = H.ui_stub()
    local asset = capture().clipboard_image("실제 PowerShell")
    H.eq(#rec.calls, 1)
    H.eq(stub.messages, { "work-kb: 이미지를 기록했습니다 — 2026-11-05-0912-실제 PowerShell.png" })
    H.eq(asset, vault .. "/raw/assets/2026-11-05-0912-실제 PowerShell.png")
    H.eq(H.read_bytes(asset):sub(1, 8), "\137PNG\r\n\26\n")
    H.eq(files_in(vault .. "/.state/tmp"), {})
    -- 없는 -FromFile: 종료 3과 스크립트의 오류 줄
    from = "inbox/none.png"
    stub.messages = {}
    H.eq(capture().clipboard_image("x"), nil)
    H.eq(#stub.messages, 1)
    H.contains(stub.messages[1], "클립보드 이미지를 저장하지 못했습니다 (종료 3). F(파일)로 캡처하세요. clip_image: -FromFile not found: ")
    H.eq(files_in(vault .. "/.state/tmp"), {})
  end)

  it("clipboard-saved: 종료 0 → raw/assets/YYYY-MM-DD-HHMM-<이름>.png로 옮기고 ![[…]] 줄", function()
    local vault = clip_vault()
    local stub = H.ui_stub()
    fake_clip(0)
    local asset = capture().clipboard_image("회의 화이트보드")
    H.eq(asset, vault .. "/raw/assets/2026-11-05-0912-회의 화이트보드.png")
    H.eq(H.read_bytes(asset), "\137PNG\r\n\26\nfake")
    H.eq(files_in(vault .. "/.state/tmp"), {}, "임시 PNG가 남음")
    H.contains(
      H.read_bytes(vault .. "/daily/2026-11-05.md"),
      "- 09:12 ![[raw/assets/2026-11-05-0912-회의 화이트보드.png]] 회의 화이트보드\n"
    )
    H.eq(stub.messages, { "work-kb: 이미지를 기록했습니다 — 2026-11-05-0912-회의 화이트보드.png" })
  end)

  it("clipboard: 이미지가 있을 때만 이름·설명을 묻는다, 취소하면 임시 파일을 지운다", function()
    local vault = clip_vault()
    local stub = H.ui_stub({ { "input", "회의 [초안] #2" }, { "input", nil } })
    fake_clip(0)
    local asset = capture().clipboard_image()
    H.eq(stub.prompts[1], { kind = "input", prompt = "이미지 이름·설명 (비우면 capture): " })
    H.eq(asset, vault .. "/raw/assets/2026-11-05-0912-회의 -초안- -2.png")
    H.contains(H.read_bytes(vault .. "/daily/2026-11-05.md"), "![[raw/assets/2026-11-05-0912-회의 -초안- -2.png]] 회의 [초안] #2\n")
    -- 두 번째: 이름을 묻다 취소
    local before = H.read_bytes(vault .. "/daily/2026-11-05.md")
    H.eq(capture().clipboard_image(), nil)
    H.eq(stub.messages[#stub.messages], "work-kb: 취소했습니다")
    H.eq(files_in(vault .. "/.state/tmp"), {})
    H.eq(files_in(vault .. "/raw/assets"), { "2026-11-05-0912-회의 -초안- -2.png" })
    H.eq(H.read_bytes(vault .. "/daily/2026-11-05.md"), before)
  end)

  it("clipboard-empty-name: 이름을 비우면 capture", function()
    local vault = clip_vault()
    H.ui_stub()
    fake_clip(0)
    local asset = capture().clipboard_image("")
    H.eq(asset, vault .. "/raw/assets/2026-11-05-0912-capture.png")
    H.ok(H.exists(asset))
    H.contains(H.read_bytes(vault .. "/daily/2026-11-05.md"), "- 09:12 ![[raw/assets/2026-11-05-0912-capture.png]]\n")
    -- 같은 분에 또: -2 (덮어쓰지 않음)
    fake_clip(0)
    H.eq(capture().clipboard_image("  "), vault .. "/raw/assets/2026-11-05-0912-capture-2.png")
  end)

  it("clipboard-error-cleans-temp: 종료 3 → 임시 PNG 삭제, F(파일) 안내, daily·assets 그대로", function()
    local vault = clip_vault()
    local stub = H.ui_stub()
    fake_clip(3, "clip_image: boom\r\n")
    H.eq(capture().clipboard_image(), nil)
    H.eq(#stub.prompts, 0)
    H.eq(stub.messages, { "클립보드 이미지를 저장하지 못했습니다 (종료 3). F(파일)로 캡처하세요. clip_image: boom" })
    H.eq(files_in(vault .. "/.state/tmp"), {})
    H.eq(files_in(vault .. "/raw/assets"), {})
    H.eq(H.read_bytes(vault .. "/daily/2026-11-05.md"), DAILY_LF)
    -- 종료 0인데 파일이 없음 → 같은 실패 안내 (출력 없음)
    stub.messages = {}
    fake_clip(0, nil, false)
    H.eq(capture().clipboard_image(), nil)
    H.eq(stub.messages, { "클립보드 이미지를 저장하지 못했습니다 (종료 0). F(파일)로 캡처하세요. " })
    -- cp949로 나온 출력은 한글로 바꿔 보인다 (이 PC의 코드 페이지 후보에 949가 있을 때)
    stub.messages = {}
    fake_clip(3, assert(vim.iconv("clip_image: 액세스가 거부되었습니다.\r\n", "utf-8", "cp949")))
    capture().clipboard_image()
    if vim.tbl_contains(capture()._codepages(), 949) then
      H.eq(stub.messages, { "클립보드 이미지를 저장하지 못했습니다 (종료 3). F(파일)로 캡처하세요. clip_image: 액세스가 거부되었습니다." })
    else
      H.contains(stub.messages[1], "클립보드 이미지를 저장하지 못했습니다 (종료 3). F(파일)로 캡처하세요. clip_image: ")
    end
  end)

  it("link-safe-asset-and-inbox: 링크 대상 이름은 [ ] # ^ |를 -로", function()
    local names = require("work-kb.names")
    H.eq(names.link_safe_name("회의 [초안] #2"), "회의 -초안- -2")
    H.eq(names.link_safe_name("a^b|c]"), "a-bc-")
    H.eq(names.link_safe_name("CON"), "_CON")
    H.eq(names.link_safe_name(" . "), "_")
    H.eq(names.safe_name("이슈 #12 [a]"), "이슈 #12 [a]")
    local vault = H.with_vault()
    H.write_bytes(vault .. "/daily/2026-11-05.md", DAILY_LF)
    local src = vault .. "/inbox/회의 [초안] #2.png"
    H.write_bytes(src, "png")
    H.ui_stub()
    local asset = capture().image_file(src, "설명")
    H.eq(vim.fs.basename(asset), "2026-11-05-0912-회의 -초안- -2.png")
    local text = H.read_bytes(vault .. "/daily/2026-11-05.md")
    -- notes.image_embeds와 같은 꼴: ![[ 다음 ] 또는 | 앞까지가 대상
    local target = text:match("!%[%[([^]|]+)%]%]")
    H.eq(target, "raw/assets/2026-11-05-0912-회의 -초안- -2.png")
    H.ok(not target:find("#", 1, true))
    H.eq(vault .. "/" .. target, asset)
    H.contains(text, "- 09:12 ![[raw/assets/2026-11-05-0912-회의 -초안- -2.png]] 설명\n")
    local note = capture().write_inbox("회의 [초안] #2", "본문")
    local stem = vim.fs.basename(note):gsub("%.md$", "")
    H.eq(vim.fs.basename(note), "20261105-0912-회의 -초안- -2.md")
    H.ok(not stem:find("[%[%]#%^|]"), stem)
    -- notes.wikilinks와 같은 꼴: # | ] 앞에서 끊긴다
    local link = "[[inbox/" .. stem .. "]]"
    H.eq(link:match("%[%[([^]|#]+)"), "inbox/" .. stem)
  end)

  it("image-file-never-overwrites: 복사(원본 유지), 겹치면 -2, 이미지가 아니면 사용자 오류", function()
    local vault = H.with_vault()
    local file = vault .. "/daily/2026-11-05.md"
    H.write_bytes(file, DAILY_LF)
    local src = vault .. "/inbox/화면.PNG"
    H.write_bytes(src, "png")
    local stub = H.ui_stub()
    local first = capture().image_file(src, "설명")
    local second = capture().image_file(src, "")
    H.eq(vim.fs.basename(first), "2026-11-05-0912-화면.png")
    H.eq(vim.fs.basename(second), "2026-11-05-0912-화면-2.png")
    H.ok(H.exists(src), "원본이 남아야 함")
    H.eq(H.read_bytes(second), "png")
    local bytes = H.read_bytes(file)
    H.contains(bytes, "- 09:12 ![[raw/assets/2026-11-05-0912-화면.png]] 설명\n")
    H.contains(bytes, "- 09:12 ![[raw/assets/2026-11-05-0912-화면-2.png]]\n")
    H.eq(stub.messages[2], "work-kb: 이미지를 기록했습니다 — 2026-11-05-0912-화면-2.png")
    local err = H.fails_with(function()
      capture().image_file(file)
    end, "이미지 파일이 아닙니다 (png, jpg, jpeg, gif, webp, bmp): " .. file)
    H.ok(require("work-kb.ui").is_user_error(err))
    H.fails_with(function()
      capture().image_file(vault .. "/inbox/없음.png")
    end, "파일이 없습니다: " .. vault .. "/inbox/없음.png")
    H.fails_with(function()
      capture().image_file(vault .. "/inbox")
    end, "파일이 없습니다: " .. vault .. "/inbox")
    H.eq(H.read_bytes(file), bytes, "실패하면 daily 그대로")
  end)

  it("F: 파일과 설명을 묻는다, oil 버퍼면 커서 아래 파일이 처음 값, 취소하면 아무것도 안 함", function()
    local vault = H.with_vault()
    H.write_bytes(vault .. "/daily/2026-11-05.md", DAILY_LF)
    local src = vault .. "/inbox/그림.jpg"
    H.write_bytes(src, "jpg")
    local stub = H.ui_stub({ { "input", src }, { "input", "  설명 " } })
    local asset = capture().image_file()
    H.eq(stub.prompts, { { kind = "input", prompt = "이미지 파일: " }, { kind = "input", prompt = "설명 (선택): " } })
    H.eq(vim.fs.basename(asset), "2026-11-05-0912-그림.jpg")
    H.contains(H.read_bytes(vault .. "/daily/2026-11-05.md"), "![[raw/assets/2026-11-05-0912-그림.jpg]] 설명\n")
    -- oil 버퍼 흉내: 커서 아래 항목이 처음 값 (":initial"은 처음 값을 그대로 받는다)
    local saved = package.loaded.oil
    package.loaded.oil = {
      get_current_dir = function()
        return vault .. "/inbox/"
      end,
      get_cursor_entry = function()
        return { name = "그림.jpg", type = "file" }
      end,
    }
    local buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_set_current_buf(buf)
    vim.bo[buf].filetype = "oil"
    local ok, err = pcall(function()
      H.eq(capture()._browser_file(), src)
      local seen_opts
      H.patch(require("work-kb.ui"), "input", function(prompt, o)
        if prompt == "이미지 파일: " then
          seen_opts = o
          return o.initial
        end
        return ""
      end)
      local second = capture().image_file()
      H.eq(seen_opts, { initial = src, completion = "file" })
      H.eq(vim.fs.basename(second), "2026-11-05-0912-그림-2.jpg")
      -- 취소
      H.patch(require("work-kb.ui"), "input", function()
        return nil
      end)
      H.eq(capture().image_file(), nil)
    end)
    package.loaded.oil = saved
    vim.api.nvim_buf_delete(buf, { force = true })
    if not ok then
      error(err, 0)
    end
    -- 탐색 버퍼가 아니면 처음 값 없음
    H.eq(capture()._browser_file(), nil)
  end)
end)
