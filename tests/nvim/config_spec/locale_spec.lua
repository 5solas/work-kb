-- 한글·공백 경로가 vim.system(rg, fd, python -X utf8)을 거쳐 그대로 돌아온다 (설계 J절 locale).
-- tests/test_nvim_config.py가 콘솔 상속과 CREATE_NO_WINDOW(cp949) 두 모드로 돌린다.
local H = require('helpers')

local util = require('config.util')

local function fixture()
  local dir = H.make_vault('한글 경로') .. '/메모 폴더'
  local file = dir .. '/회의 메모.md'
  H.write_bytes(file, '# 제목\n찾을말 한글 본문\n')
  return dir, file
end

local function run(cmd)
  local res = vim.system(cmd, { text = false }):wait(30000)
  return res.code, (res.stdout or ''):gsub('\r\n', '\n'), res.stderr or ''
end

local function slash(s)
  return (s:gsub('\\', '/'))
end

return {
  {
    name = 'rg finds a Hangul word under a Hangul+space path',
    fn = function()
      local dir, file = fixture()
      local rg = util.unshim('rg')
      H.ok(vim.fn.executable(rg) == 1, 'rg 없음')
      local code, out, err = run({ rg, '--no-heading', '--line-number', '--color=never', '찾을말', dir })
      H.eq(code, 0, err)
      H.contains(slash(out), file .. ':2:찾을말 한글 본문')
    end,
  },
  {
    name = 'fd lists a Hangul file name under a Hangul+space path',
    fn = function()
      local dir, file = fixture()
      local fd = util.unshim('fd')
      H.ok(vim.fn.executable(fd) == 1, 'fd 없음')
      local code, out, err = run({ fd, '--type', 'f', '--color=never', '--absolute-path', '.', dir })
      H.eq(code, 0, err)
      H.contains(slash(out), file)
    end,
  },
  {
    name = 'python -X utf8 receives the path argument and prints Hangul back',
    fn = function()
      local _, file = fixture()
      local py = H.python()
      H.ok(H.exists(py) or vim.fn.executable(py) == 1, 'python 없음: ' .. py)
      local script = 'import sys, os; p = sys.argv[1]; print(os.path.isfile(p)); print(open(p, encoding="utf-8").read().splitlines()[1]); print(p)'
      local code, out, err = run({ py, '-X', 'utf8', '-c', script, file })
      H.eq(code, 0, err)
      local lines = vim.split(out, '\n', { trimempty = true })
      H.eq(lines[1], 'True')
      H.eq(lines[2], '찾을말 한글 본문')
      H.eq(slash(lines[3]), file)
    end,
  },
  {
    name = 'unshim resolves scoop shims to the real executable',
    fn = function()
      for _, name in ipairs({ 'rg', 'fd', 'fzf' }) do
        local found = vim.fn.exepath(name)
        local real = util.unshim(name)
        H.ok(H.exists(real), name .. ': ' .. real)
        local shim = found:gsub('%.[Ee][Xx][Ee]$', '') .. '.shim'
        if H.exists(shim) then
          H.not_contains((real:lower():gsub('/', '\\')), '\\shims\\', name .. ' shim을 건너뛰지 못함: ' .. real)
          H.ok(real:lower():find('%.exe$') ~= nil, real)
        end
      end
      H.eq(util.unshim('no-such-tool-xyz'), 'no-such-tool-xyz')
    end,
  },
}
