-- 일반 키 (User VeryLazy 뒤에 불러온다). 플러그인 키는 lua/plugins/*.lua의 keys에 있다.
-- <leader>k*는 work-kb 플러그인이 정한다 (이 파일은 정하지 않는다).
local map = vim.keymap.set

map('n', '<Esc>', '<cmd>nohlsearch<cr><Esc>', { desc = '검색 강조 끄기' })

-- 창
map('n', '<C-h>', '<C-w>h', { desc = '왼쪽 창' })
map('n', '<C-j>', '<C-w>j', { desc = '아래 창' })
map('n', '<C-k>', '<C-w>k', { desc = '위 창' })
map('n', '<C-l>', '<C-w>l', { desc = '오른쪽 창' })

-- 버퍼
map('n', '<leader>bd', function()
  local ok, bufremove = pcall(require, 'mini.bufremove')
  if ok then
    bufremove.delete(0, false)
  else
    vim.cmd.bdelete()
  end
end, { desc = '버퍼 닫기 (창은 유지)' })
map('n', '<leader>bn', '<cmd>bnext<cr>', { desc = '다음 버퍼' })
map('n', '<leader>bp', '<cmd>bprevious<cr>', { desc = '이전 버퍼' })

-- 코드
map('n', '<leader>cd', vim.diagnostic.open_float, { desc = '진단 보기' })

-- 화면 토글
map('n', '<leader>uw', function()
  vim.wo.wrap = not vim.wo.wrap
end, { desc = '줄 바꿈 토글' })
map('n', '<leader>us', function()
  vim.wo.spell = not vim.wo.spell
end, { desc = '맞춤법 토글' })
map('n', '<leader>un', function()
  vim.wo.number = not vim.wo.number
end, { desc = '줄 번호 토글' })
map('n', '<leader>uS', '<cmd>KbStartup<cr>', { desc = '시작 통계' })
map('n', '<leader>ui', function()
  local ok, ime = pcall(require, 'config.ime')
  if ok and type(ime) == 'table' and ime.toggle then
    ime.toggle()
  end
end, { desc = '한글 입력기 자동 전환 토글 (Neovide)' })

-- 저장·종료
map('n', '<leader>w', '<cmd>write<cr>', { desc = '저장' })
map('n', '<leader>qq', '<cmd>confirm qall<cr>', { desc = '모두 종료' })
