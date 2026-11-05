<#
.SYNOPSIS
  Open Neovide on the work-kb Neovim server, or start a normal Neovide when no server listens.

.DESCRIPTION
  ASCII-only (Windows PowerShell 5.1 reads BOM-less scripts as cp949).
    pipe served      neovide --server \\.\pipe\work-kb-nvim            (prints ATTACH <pipe>)
    pipe not served  neovide -- --listen \\.\pipe\work-kb-nvim         (prints START <pipe>)
  In the fallback the embedded nvim listens on the same pipe, so nvim/capture.ahk and a
  later run of this launcher reach it too.
  -WhatIf prints the command it would run (WHATIF ...) without starting anything.

  Not tested automatically: whether Neovide really attaches and draws (needs a screen).
  Manual check: run nvim-server.ps1, then this file; a Neovide window must open on the
  running server (:echo v:servername shows the pipe), and closing the window must leave
  the server running (nvim-server.ps1 then prints RUNNING).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File nvim\bin\neovide-attach.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Pipe = '\\.\pipe\work-kb-nvim',
    [string]$Neovide = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Out-Line([string]$Text) { [Console]::Out.WriteLine($Text) }

function Test-Pipe([string]$Name) {
    try { return [bool](Test-Path -LiteralPath $Name) } catch { return $false }
}

function Resolve-RealExe([string]$Name) {
    $cmd = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { return $null }
    $shim = Join-Path (Split-Path -Parent $cmd.Path) ($Name + '.shim')
    if (Test-Path -LiteralPath $shim -PathType Leaf) {
        foreach ($line in [IO.File]::ReadAllLines($shim)) {
            if ($line -match '^\s*path\s*=\s*"?(.+?)"?\s*$') {
                if (Test-Path -LiteralPath $matches[1] -PathType Leaf) { return $matches[1] }
            }
        }
    }
    return $cmd.Path
}

if ($Pipe -notmatch '^\\\\\.\\pipe\\[^\\]+$') { Out-Line ('FAILED not a pipe name: ' + $Pipe); exit 1 }
if (-not $Neovide) { $Neovide = Resolve-RealExe 'neovide' }
if (-not $Neovide) { Out-Line 'FAILED neovide not found on PATH'; exit 1 }

if (Test-Pipe $Pipe) {
    $mode = 'ATTACH'
    $argv = @('--server', $Pipe)
} else {
    $mode = 'START'
    $argv = @('--', '--listen', $Pipe)
}
$line = $Neovide + ' ' + ($argv -join ' ')
if (-not $PSCmdlet.ShouldProcess($line, 'start')) { Out-Line ('WHATIF ' + $mode + ' ' + $line); exit 0 }
Start-Process -FilePath $Neovide -ArgumentList $argv | Out-Null
Out-Line ($mode + ' ' + $Pipe)
exit 0
