# Runs a review/repro Lua file in headless Neovim with XDG folders in %TEMP% (never the real nvim config).
# Usage: pwsh -File review/repro/run_lua.ps1 review/repro/<ID>_*.lua [-Env @{NAME='value'}]
param([string]$Script, [hashtable]$Env = @{})
$x = Join-Path ([IO.Path]::GetTempPath()) 'work-kb-repro-xdg'
foreach ($n in 'config','data','state','cache') { New-Item -ItemType Directory -Force (Join-Path $x $n) | Out-Null }
$env:XDG_CONFIG_HOME = Join-Path $x 'config'; $env:XDG_DATA_HOME = Join-Path $x 'data'; $env:XDG_STATE_HOME = Join-Path $x 'state'; $env:XDG_CACHE_HOME = Join-Path $x 'cache'
foreach ($k in $Env.Keys) { Set-Item "env:$k" $Env[$k] }
& nvim --headless --clean -i NONE -n -l $Script 2>&1
exit $LASTEXITCODE
