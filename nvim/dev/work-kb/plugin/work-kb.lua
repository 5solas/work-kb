-- work-kb: 시작 때는 :WorkKb 명령만 정의한다. 모듈은 처음 쓸 때 불러온다.
if vim.g.loaded_work_kb == 1 then
  return
end
vim.g.loaded_work_kb = 1

vim.api.nvim_create_user_command("WorkKb", function(o)
  require("work-kb").command(o)
end, {
  nargs = "?",
  complete = function(arglead)
    return require("work-kb").complete(arglead)
  end,
  desc = "work-kb 명령",
})
