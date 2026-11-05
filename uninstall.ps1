<#
.SYNOPSIS
  Undo what install.ps1 did, using its manifest (PowerShell 5.1 and 7).

.DESCRIPTION
  ASCII-only on purpose (Windows PowerShell 5.1 reads BOM-less scripts as cp949); Korean text
  is kept as \uXXXX escapes.

  Reads %LOCALAPPDATA%\work-kb\install-manifest.json (written by install.ps1). Only what the
  manifest records as created by install.ps1 is undone.

  Steps (select with -Only, comma separated):
    env    the user environment variable WORK_KB_ROOT: if it still has the value install.ps1
           set, the previous value is restored (or the variable removed if there was none).
           If someone changed it since, it is left alone.
    venv   only with -RemoveVenv: delete this repository's .venv if install.ps1 created it.
           Without -RemoveVenv the venv is kept.
  When nothing is left in the manifest it is deleted (and the work-kb folder, if empty).

  Never touched: the repository, the vault, nvim-config, %LOCALAPPDATA%\nvim*, %APPDATA%\neovide.
  The nvd daemon keeps the environment it started with: restart it (nvd --restart) afterwards.
  -WhatIf prints every action and changes nothing.

  Seams (same as install.ps1): -LocalAppData, -UserEnvFile (or env WORK_KB_INSTALL_USER_ENV_FILE).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 -WhatIf
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 -RemoveVenv
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string[]]$Only = @(),
    [switch]$RemoveVenv,
    [string]$LocalAppData = '',
    [string]$UserEnvFile = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$script:Cmdlet = $PSCmdlet
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding $false
try { [Console]::OutputEncoding = $script:Utf8NoBom } catch { }

$AllSteps = @('env', 'venv')
$EnvName = 'WORK_KB_ROOT'
$Msg = @{
    Done = '\uD658\uACBD \uBCC0\uC218\uB97C \uBC14\uAFE8\uB2E4\uBA74 \uC0C8 \uD130\uBBF8\uB110\uC744 \uC5F4\uACE0 nvd --restart \uB85C \uB370\uBAAC\uC744 \uB2E4\uC2DC \uC2DC\uC791\uD558\uC138\uC694 (nvim-config\uB294 WORK_KB_ROOT\uAC00 \uC5C6\uC73C\uBA74 work-kb\uB97C \uBD88\uB7EC\uC624\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4).'
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $LocalAppData) { $LocalAppData = $env:LOCALAPPDATA }
if (-not $UserEnvFile) { $UserEnvFile = $env:WORK_KB_INSTALL_USER_ENV_FILE }
$VenvDir = Join-Path $RepoRoot '.venv'
$ManifestDir = Join-Path $LocalAppData 'work-kb'
$ManifestPath = Join-Path $ManifestDir 'install-manifest.json'

$script:Results = New-Object System.Collections.ArrayList
function Out-Line([string]$Text) { [Console]::Out.WriteLine($Text) }
function Report([string]$Step, [string]$Status, [string]$Detail) {
    [void]$script:Results.Add([pscustomobject]@{ Step = $Step; Status = $Status; Detail = $Detail })
    Out-Line ('STEP {0} {1} {2}' -f $Step, $Status, $Detail)
}
function Test-WhatIf { return [bool]$WhatIfPreference }
function Invoke-Action([string]$Step, [string]$Description, [scriptblock]$Action) {
    if ($script:Cmdlet.ShouldProcess($Description, $Step)) {
        Out-Line ('  do: ' + $Description)
        & $Action | Out-Null
        return $true
    }
    if (Test-WhatIf) { Out-Line ('WHATIF {0} {1}' -f $Step, $Description) }
    else { Out-Line ('  declined: ' + $Description) }
    return $false
}
function Read-Utf8([string]$Path) { return [IO.File]::ReadAllText($Path, $script:Utf8NoBom) }
function Write-Utf8([string]$Path, [string]$Text) { [IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom) }
function Get-NormPath([string]$Path) { return ([IO.Path]::GetFullPath($Path)).TrimEnd('\') }
function Test-SamePath([string]$A, [string]$B) {
    if (-not $A -or -not $B) { return $false }
    try { return ((Get-NormPath $A) -ieq (Get-NormPath $B)) } catch { return ($A -ieq $B) }
}

# Deletes a folder tree without ever following a junction or symlink inside it.
function Remove-TreeSafe([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        if ($item.PSIsContainer) { [IO.Directory]::Delete($Path, $false) } else { [IO.File]::Delete($Path) }
        return
    }
    if (-not $item.PSIsContainer) {
        $item.Attributes = [IO.FileAttributes]::Normal
        [IO.File]::Delete($Path)
        return
    }
    foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force)) { Remove-TreeSafe $child.FullName }
    [IO.Directory]::Delete($Path, $false)
}

# ---------------------------------------------------------------- user environment store (as install.ps1)
function Read-EnvFile {
    $h = @{}
    if ((Test-Path -LiteralPath $UserEnvFile -PathType Leaf)) {
        $text = Read-Utf8 $UserEnvFile
        if ($text.Trim()) {
            $obj = $text | ConvertFrom-Json
            foreach ($p in $obj.PSObject.Properties) { $h[$p.Name] = [string]$p.Value }
        }
    }
    return $h
}
function Get-UserEnv([string]$Name) {
    if ($UserEnvFile) {
        $h = Read-EnvFile
        if ($h.ContainsKey($Name)) { return $h[$Name] }
        return $null
    }
    return [Environment]::GetEnvironmentVariable($Name, 'User')
}
function Set-UserEnv([string]$Name, $Value) {
    if ($UserEnvFile) {
        $h = Read-EnvFile
        if ($null -eq $Value) { [void]$h.Remove($Name) } else { $h[$Name] = [string]$Value }
        Write-Utf8 $UserEnvFile (ConvertTo-Json -InputObject $h -Depth 3)
        return
    }
    [Environment]::SetEnvironmentVariable($Name, $Value, 'User')
}
function Get-EnvStoreLabel {
    if ($UserEnvFile) { return 'fake user environment ' + $UserEnvFile }
    return 'HKCU\Environment'
}

# ---------------------------------------------------------------- manifest (as install.ps1)
function Read-Manifest {
    $m = @{ env = @{}; venvs = @() }
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) {
        $obj = (Read-Utf8 $ManifestPath) | ConvertFrom-Json
        if ($obj.PSObject.Properties['env'] -and $obj.env) {
            foreach ($p in $obj.env.PSObject.Properties) {
                $e = $p.Value
                $m.env[$p.Name] = @{ value = [string]$e.value; hadPrevious = [bool]$e.hadPrevious; previous = $e.previous }
            }
        }
        if ($obj.PSObject.Properties['venvs'] -and $obj.venvs) { $m.venvs = @($obj.venvs | ForEach-Object { [string]$_ }) }
    }
    return $m
}
function Write-Manifest($M) {
    $doc = [ordered]@{
        tool = 'work-kb install.ps1'
        version = 1
        repo = $RepoRoot
        updated = (Get-Date -Format 's')
        env = $M.env
        venvs = @($M.venvs)
    }
    Write-Utf8 $ManifestPath (ConvertTo-Json -InputObject $doc -Depth 5)
}

$script:Manifest = $null

function Step-env {
    $m = $script:Manifest
    if (-not $m.env.ContainsKey($EnvName)) { Report 'env' 'ok' ('nothing recorded for ' + $EnvName + '; left as it is'); return }
    $entry = $m.env[$EnvName]
    $cur = Get-UserEnv $EnvName
    $store = Get-EnvStoreLabel
    # same path = still ours (install.ps1 compares paths the same way: case and a trailing backslash do not matter)
    if (-not ($cur -and (Test-SamePath $cur ([string]$entry.value)))) {
        $shown = '(not set)'
        if ($cur) { $shown = $cur }
        $done = Invoke-Action 'env' ('forget the manifest entry for ' + $EnvName + ' (it is now ' + $shown + ', not ' + $entry.value + ')') {
            [void]$m.env.Remove($EnvName)
        }
        if (Test-WhatIf) { Report 'env' 'whatif' ($EnvName + ' changed since install; would be left alone'); return }
        Report 'env' 'skipped' ($EnvName + ' is ' + $shown + ', not the value install.ps1 set (' + $entry.value + '); left alone')
        return
    }
    if ($entry.hadPrevious -and $null -ne $entry.previous) {
        $prev = [string]$entry.previous
        $done = Invoke-Action 'env' ('restore user environment variable ' + $EnvName + ' = ' + $prev + ' in ' + $store) {
            Set-UserEnv $EnvName $prev
            [void]$m.env.Remove($EnvName)
        }
        $what = 'restored ' + $EnvName + ' = ' + $prev
    } else {
        $done = Invoke-Action 'env' ('remove user environment variable ' + $EnvName + ' from ' + $store) {
            Set-UserEnv $EnvName $null
            [void]$m.env.Remove($EnvName)
        }
        $what = 'removed ' + $EnvName
    }
    if (Test-WhatIf) { Report 'env' 'whatif' $what; return }
    if (-not $done) { Report 'env' 'skipped' 'declined'; return }
    Report 'env' 'changed' $what
}

function Step-venv {
    $m = $script:Manifest
    $recorded = @($m.venvs | Where-Object { Test-SamePath $_ $VenvDir })
    if ($recorded.Count -eq 0) {
        if (Test-Path -LiteralPath $VenvDir) { Report 'venv' 'ok' ($VenvDir + ' was not created by install.ps1; left alone') }
        else { Report 'venv' 'ok' 'nothing recorded' }
        return
    }
    if (-not $RemoveVenv) { Report 'venv' 'skipped' ('kept ' + $VenvDir + ' (use -RemoveVenv to delete it)'); return }
    if (-not (Test-Path -LiteralPath $VenvDir)) {
        $done = Invoke-Action 'venv' ('forget ' + $VenvDir + ' (already gone)') { $m.venvs = @($m.venvs | Where-Object { -not (Test-SamePath $_ $VenvDir) }) }
        if (Test-WhatIf) { Report 'venv' 'whatif' ($VenvDir + ' already gone'); return }
        Report 'venv' 'ok' ($VenvDir + ' already gone'); return
    }
    $done = Invoke-Action 'venv' ('delete ' + $VenvDir) {
        Remove-TreeSafe $VenvDir
        $m.venvs = @($m.venvs | Where-Object { -not (Test-SamePath $_ $VenvDir) })
    }
    if (Test-WhatIf) { Report 'venv' 'whatif' ('delete ' + $VenvDir); return }
    if (-not $done) { Report 'venv' 'skipped' 'declined'; return }
    Report 'venv' 'changed' ('deleted ' + $VenvDir)
}

function Split-StepList([string[]]$Items) {
    return @($Items | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
}

$Only = @(Split-StepList $Only)
$unknown = @($Only | Where-Object { $AllSteps -notcontains $_ })
if ($unknown.Count -gt 0) {
    Out-Line ('unknown step(s): ' + ($unknown -join ', ') + '; valid: ' + ($AllSteps -join ', '))
    exit 2
}
if (-not $LocalAppData) { Out-Line 'LOCALAPPDATA not set'; exit 2 }

Out-Line ('work-kb uninstall: repo ' + $RepoRoot)
Out-Line ('  manifest ' + $ManifestPath + ' | user environment: ' + (Get-EnvStoreLabel))
if (Test-WhatIf) { Out-Line '  -WhatIf: nothing will be changed' }
try { $script:Manifest = Read-Manifest } catch { Out-Line ('cannot read the manifest: ' + $_.Exception.Message); exit 1 }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { Out-Line '  no manifest: install.ps1 has not changed anything here' }

$selected = @($AllSteps | Where-Object { $Only.Count -eq 0 -or $Only -contains $_ })
foreach ($step in $selected) {
    try { & ('Step-' + $step) } catch { Report $step 'failed' ('error: ' + $_.Exception.Message) }
}

# Save what is left, or remove the manifest (and its folder if empty) when nothing is left.
if ((Test-Path -LiteralPath $ManifestPath -PathType Leaf) -and -not (Test-WhatIf)) {
    if ($script:Manifest.env.Count -eq 0 -and @($script:Manifest.venvs).Count -eq 0) {
        [IO.File]::Delete($ManifestPath)
        Out-Line ('  removed ' + $ManifestPath)
        if (@(Get-ChildItem -LiteralPath $ManifestDir -Force).Count -eq 0) { [IO.Directory]::Delete($ManifestDir, $false) }
    } else {
        Write-Manifest $script:Manifest
    }
}

Out-Line ''
Out-Line ('  ' + [regex]::Unescape($Msg.Done))
$failedCount = @($script:Results | Where-Object { $_.Status -eq 'failed' }).Count
if ($failedCount -gt 0) { exit 1 }
exit 0
