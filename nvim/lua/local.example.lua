-- PC별 설정 예. lua/local.lua로 복사해서 고친다 (local.lua는 git이 무시한다; install.ps1이 만든다).
-- 빠진 키는 lua/config/settings.lua의 기본값을 쓴다. 파일이 없거나 깨지면 경고 한 번 뒤 기본값.
-- 한글·역슬래시 경로는 Lua 긴 문자열로 쓴다: vault = [[D:\업무\vault]]
return {
  vault = nil, -- [[D:\업무\vault]]; nil = 저장소 루트
  python = nil, -- nil = <저장소>\.venv\Scripts\python.exe (없으면 PATH의 python, py -3)
  colorscheme = 'tokyonight-night',
  ime = { auto = true, langmap = false },
  background = { enabled = true, server = true, prewarm = true },
  neovide = { scale = 1.0, scroll_animation = 0.08 },
  keep_builtins = {}, -- e.g. { 'matchit' }
}
