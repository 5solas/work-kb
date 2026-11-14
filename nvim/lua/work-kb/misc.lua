-- l (link)과 ? (status): Emacs work-kb-link / work-kb-status.

local M = {}

local function proc()
  return require("work-kb.proc")
end

local function ui()
  return require("work-kb.ui")
end

---`kb.py link`를 실행하고 요약(마지막 줄)을 알린다. 서버 시간 초과(code nil)는 따로 알린다.
function M.link()
  local p = proc()
  return p.run(p.kb_args("link"), {
    on_exit = function(code, bufnr, reason)
      if bufnr and vim.api.nvim_buf_is_valid(bufnr) then
        ui().show(bufnr)
      end
      if reason == "timeout" then
        ui().notify(p.timeout_message("link", bufnr))
      elseif code == 0 then
        ui().notify("work-kb link: " .. (p.last_line(bufnr) or "완료"))
      else
        ui().notify(string.format("work-kb link 실패 (종료 %s)", tostring(code)))
      end
    end,
  })
end

---`kb.py status` 결과를 *work-kb* 버퍼에 보인다.
function M.status()
  local p = proc()
  return p.run(p.kb_args("status"), {
    on_exit = function(code, bufnr)
      if bufnr and vim.api.nvim_buf_is_valid(bufnr) then
        ui().show(bufnr)
      end
      if code ~= 0 then
        ui().notify(string.format("work-kb status 실패 (종료 %s)", tostring(code)))
      end
    end,
  })
end

return M
