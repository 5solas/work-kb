-- nvim-treesitter (main 가지): 파서 설치기로만 쓴다 (:TSInstall python 등). 시작 때 불러오지 않는다.
-- 설치에는 tree-sitter-cli 0.26.1 이상과 C 컴파일러가 필요하다. 설치된 파서는 nvim-data/site/parser에
-- 들어가고, autocmds.lua의 FileType 자동 명령이 파서가 있으면 treesitter를 켠다.
return {
  {
    'nvim-treesitter/nvim-treesitter',
    branch = 'main',
    build = false,
    cmd = { 'TSInstall', 'TSUpdate', 'TSUninstall' },
  },
}
