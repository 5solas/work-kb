# C-07 repro: uninstall.ps1 run from clone A removes WORK_KB_ROOT that clone B's install.ps1 set.
# Uses only the test seams (-LocalAppData, -UserEnvFile); never touches HKCU or the real profile.
# Run: pwsh -NoProfile -File review/repro/C-07_uninstall_other_clone.ps1   (exit 1 = bug reproduced)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('c07-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$a = Join-Path $tmp 'cloneA'; $b = Join-Path $tmp 'cloneB'; $lad = Join-Path $tmp 'localappdata'
$envFile = Join-Path $tmp 'user-env.json'
foreach ($d in $a, $b, $lad) { New-Item -ItemType Directory -Force $d | Out-Null }
foreach ($d in $a, $b) { Copy-Item (Join-Path $repo 'install.ps1'), (Join-Path $repo 'uninstall.ps1') $d }
$common = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File')
& pwsh @common (Join-Path $a 'install.ps1') -Only env -Yes -LocalAppData $lad -UserEnvFile $envFile | Out-Null
& pwsh @common (Join-Path $b 'install.ps1') -Only env -Yes -LocalAppData $lad -UserEnvFile $envFile | Out-Null
$before = (Get-Content $envFile -Raw | ConvertFrom-Json).WORK_KB_ROOT
& pwsh @common (Join-Path $a 'uninstall.ps1') -Only env -LocalAppData $lad -UserEnvFile $envFile | Select-String 'STEP' | ForEach-Object { '  A uninstall: ' + $_.Line }
$after = (Get-Content $envFile -Raw | ConvertFrom-Json).PSObject.Properties['WORK_KB_ROOT']
Write-Output ('WORK_KB_ROOT before A uninstall: ' + $before)
Write-Output ('WORK_KB_ROOT after  A uninstall: ' + $(if ($after) { $after.Value } else { '(removed)' }))
Remove-Item -Recurse -Force $tmp
if ($before -eq $b -and -not $after) { Write-Output 'FAIL: uninstall from clone A removed the variable that points at clone B'; exit 1 }
Write-Output 'OK'; exit 0
