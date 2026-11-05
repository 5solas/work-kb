-- work-kb 플러그인 (이 저장소의 nvim/dev/work-kb). 접두 키(<leader>k)나 :WorkKb를 처음 쓸 때 불러오고,
-- 백그라운드 작업(config/background.lua)이 첫 화면 300ms 뒤에 미리 불러온다.
-- 접두 키 자리 표시는 lazy가 지운 뒤 플러그인을 불러오고 키를 다시 넣는다; 그 사이 setup()이
-- <leader>k* 키를 정한다. 이 설정은 <leader>k* 키를 직접 정하지 않는다.
-- dir = stdpath('config')는 쓰지 않는다 (설정 폴더와 after/가 runtimepath에 두 번 들어간다).
return {
  {
    dir = vim.fn.stdpath('config') .. '/dev/work-kb',
    name = 'work-kb',
    main = 'work-kb',
    cmd = { 'WorkKb' },
    keys = { { '<leader>k', mode = { 'n', 'x' }, desc = '+work-kb' } },
    opts = function()
      local s = require('config.settings')
      return {
        prefix = '<leader>k',
        root = s.root,
        vault = s.vault,
        python = s.python,
        server = { enable = true },
      }
    end,
  },
}
