# A-07 재현: install.ps1 -Offline 경로에서도 uv venv에 --offline / --no-python-downloads가 없다 (읽기 전용 AST 점검).
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$installPath = Join-Path $repoRoot 'install.ps1'

$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "Parse error in install.ps1" }

$lines = Get-Content -LiteralPath $installPath -Encoding UTF8
$venvLines = @()
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "venv.*--seed") {
        $venvLines += ("install.ps1:{0}: {1}" -f ($i + 1), $lines[$i].Trim())
    }
}
$venvLines | ForEach-Object { Write-Output $_ }
$hasOfflineVenv = ($lines -join "`n") -match "--offline|--no-python-downloads"
Write-Output ("has_offline_or_no_python_downloads_in_install_ps1={0}" -f $hasOfflineVenv)
if (-not $hasOfflineVenv) {
    Write-Error "A-07 REPRODUCED: install.ps1 -Offline lacks --offline / --no-python-downloads on uv venv"
    exit 1
}
