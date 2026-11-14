# C-15 repro: with the plugin loaded through a junction and no setup({root}), jira.daily_relative() does not
# recognise the daily file that `t` opens, because jira.configured_dir uses the unresolved cfg.root while
# init.in_vault / daily.path use config.root() (real path). Effect: `p` (promote) refuses, `c` sends --body.
# Run: pwsh -NoProfile -File review/repro/C-15_junction_root.ps1     (exit 1 = reproduced)
# The junction is created in %TEMP% and removed with Directory.Delete(path, false) (the target is never touched).
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('c15-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $tmp | Out-Null
$j = Join-Path $tmp 'jroot'
New-Item -ItemType Junction -Path $j -Target $repo | Out-Null
$lua = Join-Path $tmp 'run.lua'
@"
vim.opt.rtp:append([[$($j.Replace('\','/'))/nvim]])
require("work-kb").setup({ prefix = false, server = { enable = false } })
local config = require("work-kb.config")
local daily_file = config.vault() .. "/daily/2026-11-05.md"
local got = require("work-kb.jira").daily_relative(daily_file)
print("config.vault() = " .. config.vault())
print("jira.daily_relative(daily file) -> " .. tostring(got) .. "   (expected daily/2026-11-05.md)")
os.exit(got == "daily/2026-11-05.md" and 0 or 1)
"@ | Set-Content -Encoding utf8NoBOM $lua
foreach ($n in 'CONFIG', 'DATA', 'STATE', 'CACHE') { Set-Item "env:XDG_${n}_HOME" (Join-Path $tmp "xdg-$n") }
try {
    & nvim --headless --clean -i NONE -n -l $lua 2>&1
    $code = $LASTEXITCODE
} finally {
    [IO.Directory]::Delete($j, $false)
    Remove-Item -Recurse -Force $tmp
}
if ($code -ne 0) { Write-Output 'FAIL: daily file not recognised when the plugin is loaded through a junction'; exit 1 }
Write-Output 'OK'; exit 0
