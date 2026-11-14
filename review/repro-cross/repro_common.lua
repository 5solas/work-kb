REPO = vim.fs.normalize(vim.fs.dirname(vim.fs.dirname(vim.fs.dirname(debug.getinfo(1, "S").source:sub(2)))))
vim.opt.rtp:append(REPO .. "/nvim")
function W(path, data)
  vim.fn.mkdir(vim.fs.dirname(path), "p")
  local fd = assert(vim.uv.fs_open(path, "w", 438)); vim.uv.fs_write(fd, data, 0); vim.uv.fs_close(fd)
end
function TMPVAULT(label)
  local d = vim.fs.normalize(vim.fn.tempname()) .. " " .. (label or "한글 볼트")
  for _, sub in ipairs({ "daily", "inbox", "wiki/topics", "wiki/people", "jira", "raw/assets", ".state" }) do vim.fn.mkdir(d .. "/" .. sub, "p") end
  return d
end
function P(...) local t = {} for i = 1, select("#", ...) do local v = select(i, ...); t[#t+1] = type(v) == "string" and v or vim.inspect(v) end io.stdout:write(table.concat(t, " ") .. "\n") end

COUNTS = {}
BAD = false
-- exit 1 when BUG is true (bug reproduced), 0 otherwise
function VERDICT(bug, msg) if bug then P("FAIL: " .. msg); os.exit(1) end P("OK"); os.exit(0) end
