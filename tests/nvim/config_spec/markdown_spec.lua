-- markdown 버퍼: treesitter로 칠하고 regex 문법을 읽지 않는다, EOL·BOM·cp949, render-markdown 지연 불러오기.
local H = require('helpers')

local function open(path)
  vim.cmd('silent! %bwipeout!')
  vim.v.errmsg = ''
  vim.cmd.edit(vim.fn.fnameescape(path))
  return vim.api.nvim_get_current_buf()
end

local function close()
  vim.cmd('silent! %bwipeout!')
end

return {
  {
    name = 'treesitter highlights markdown and no regex syntax is loaded',
    fn = function()
      local path = H.make_vault('마크다운') .. '/메모.md'
      H.write_bytes(path, '# 제목\n\n- 항목 [[링크]]\n<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n')
      local buf = open(path)
      H.eq(vim.bo[buf].filetype, 'markdown')
      H.ok(vim.treesitter.highlighter.active[buf] ~= nil, 'treesitter 강조가 없음')
      H.eq(vim.b[buf].current_syntax, nil, 'regex 문법을 읽음')
      H.eq(vim.bo[buf].syntax, '')
      H.eq(vim.v.errmsg, '')
      -- ftplugin/markdown.lua가 옮겨 둔 설정, after/ftplugin의 덮어쓰기
      H.eq(vim.bo[buf].commentstring, '<!-- %s -->')
      H.eq(vim.bo[buf].shiftwidth, 2)
      H.ok(not vim.bo[buf].formatoptions:find('[ro]'), vim.bo[buf].formatoptions)
      H.ok(vim.bo[buf].omnifunc ~= 'htmlcomplete#CompleteTags', 'html ftplugin을 읽음 (런타임 markdown.vim이 돌았음)')
      close()
    end,
  },
  {
    name = 'render-markdown loads after the first paint and attaches to open markdown buffers',
    fn = function()
      local path = H.make_vault('마크다운') .. '/보기.md'
      H.write_bytes(path, '# 제목\n\n- [ ] 할 일\n')
      local buf = open(path)
      H.ok(vim.wait(3000, function()
        return package.loaded['render-markdown'] ~= nil
      end, 10), 'render-markdown을 불러오지 않음')
      local manager = require('render-markdown.core.manager')
      H.ok(manager.attached(buf), '열린 버퍼에 붙지 않음')
      -- 그 뒤에 여는 버퍼는 플러그인의 FileType 자동 명령이 붙인다
      local path2 = vim.fs.dirname(path) .. '/두번째.md'
      H.write_bytes(path2, '# 둘\n')
      vim.cmd.edit(vim.fn.fnameescape(path2))
      H.ok(manager.attached(vim.api.nvim_get_current_buf()), '나중 버퍼에 붙지 않음')
      H.eq(vim.b.current_syntax, nil)
      close()
    end,
  },
  {
    name = 'LF and no BOM are preserved on write; CRLF stays CRLF',
    fn = function()
      local dir = H.make_vault('마크다운')
      local lf = dir .. '/lf.md'
      H.write_bytes(lf, '첫 줄\n둘째 줄\n')
      open(lf)
      vim.api.nvim_buf_set_lines(0, -1, -1, false, { '셋째 줄' })
      vim.cmd('silent write')
      H.eq(H.read_bytes(lf), '첫 줄\n둘째 줄\n셋째 줄\n')
      local crlf = dir .. '/crlf.md'
      H.write_bytes(crlf, '첫 줄\r\n둘째 줄\r\n')
      open(crlf)
      H.eq(vim.bo.fileformat, 'dos')
      vim.api.nvim_buf_set_lines(0, -1, -1, false, { '셋째 줄' })
      vim.cmd('silent write')
      H.eq(H.read_bytes(crlf), '첫 줄\r\n둘째 줄\r\n셋째 줄\r\n')
      local new = dir .. '/새 파일.md'
      open(new)
      vim.api.nvim_buf_set_lines(0, 0, -1, false, { '새 글' })
      vim.cmd('silent write')
      H.eq(H.read_bytes(new), '새 글\n')
      close()
    end,
  },
  {
    name = 'a cp949 file is read through the fileencodings fallback',
    fn = function()
      local path = H.make_vault('마크다운') .. '/옛 파일.md'
      -- "한글 메모" in cp949
      H.write_bytes(path, '\199\209\177\219 \184\222\184\240\n')
      open(path)
      H.eq(vim.bo.fileencoding, 'cp949')
      H.eq(vim.api.nvim_buf_get_lines(0, 0, -1, false), { '한글 메모' })
      close()
    end,
  },
  {
    name = 'every markdown file in tests/fixtures/notes opens cleanly',
    fn = function()
      local files = vim.fs.find(function(name)
        return name:match('%.md$') ~= nil
      end, { path = H.repo .. '/tests/fixtures/notes', type = 'file', limit = math.huge })
      H.ok(#files > 0, 'fixture 없음')
      for _, file in ipairs(files) do
        local buf = open(file)
        H.eq(vim.v.errmsg, '', file)
        H.eq(vim.bo[buf].filetype, 'markdown', file)
        H.ok(vim.treesitter.highlighter.active[buf] ~= nil, 'treesitter 없음: ' .. file)
        H.eq(vim.bo[buf].modified, false, file)
      end
      close()
    end,
  },
}
