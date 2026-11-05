-- markdown 보기: 내장 파서(markdown, markdown_inline)를 쓴다. kb 관리 구역 표시
-- (<!-- kb:*:start --> / end)는 숨기지 않는다.
--
-- 불러오는 때: 첫 markdown 버퍼의 FileType에서 vim.schedule로 미룬다 (첫 화면 뒤, 한 번 다시 그림).
-- 시작 때 바로 불러오면 markdown 파일 시작이 7~8ms 늘어 시작 예산(--clean + 20ms)이 부하에 따라
-- 넘쳤다 (측정; 설계 K절의 대안). 불러온 뒤에는 열려 있는 markdown 버퍼를 모두 붙이고, 그 뒤
-- 버퍼는 플러그인의 FileType 자동 명령이 붙인다.
-- `ft =`나 `event = 'FileType markdown'`은 쓰지 않는다: lazy가 플러그인을 불러온 뒤 FileType을
-- 다시 쏘면 b:undo_ftplugin의 treesitter.stop()이 regex syntax/markdown.vim(약 15ms)을 다시 읽는다.
return {
  {
    'MeanderingProgrammer/render-markdown.nvim',
    cmd = 'RenderMarkdown',
    init = function()
      vim.api.nvim_create_autocmd('FileType', {
        group = vim.api.nvim_create_augroup('config_render_markdown', { clear = true }),
        pattern = 'markdown',
        once = true,
        callback = function()
          vim.schedule(function()
            if not package.loaded['render-markdown'] then
              require('lazy').load({ plugins = { 'render-markdown.nvim' } })
            end
          end)
        end,
      })
    end,
    opts = {
      file_types = { 'markdown' },
      latex = { enabled = false },
      heading = { sign = false },
      code = { sign = false },
      html = { comment = { conceal = false } },
    },
    config = function(_, opts)
      require('render-markdown').setup(opts)
      local manager = require('render-markdown.core.manager')
      for _, buf in ipairs(vim.api.nvim_list_bufs()) do
        if vim.api.nvim_buf_is_loaded(buf) and vim.bo[buf].filetype == 'markdown' then
          pcall(manager.attach, buf)
        end
      end
    end,
  },
}
