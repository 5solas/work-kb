<#
.SYNOPSIS
  Start a headless Neovim that serves the work-kb pipe, unless one is already serving it.

.DESCRIPTION
  ASCII-only (Windows PowerShell 5.1 reads BOM-less scripts as cp949).
  Opt-in pre-start (install.ps1 -PreStart puts a Startup-folder shortcut to this file):
    nvim --headless --listen \\.\pipe\work-kb-nvim
  is started detached and hidden, so that "neovide --server \\.\pipe\work-kb-nvim"
  (nvim\bin\neovide-attach.ps1) opens a window on an already-loaded editor.

  Prints one line and exits:
    RUNNING <pipe>               the pipe was already served; nothing started   (exit 0)
    STARTED pid=<n> <pipe>       a new server is listening                      (exit 0)
    FAILED <reason>                                                             (exit 1)

  A named mutex serialises concurrent launches, so two quick starts give one server.
  The server uses the normal Neovim config (%LOCALAPPDATA%\nvim). Note that lazy.nvim
  fires VeryLazy only after a UI attaches, so the background work of the config starts
  when Neovide attaches, not at logon.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File nvim\bin\nvim-server.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Pipe = '\\.\pipe\work-kb-nvim',
    [string]$Nvim = '',
    [string]$WorkingDirectory = '',
    [int]$WaitMs = 10000
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Out-Line([string]$Text) { [Console]::Out.WriteLine($Text) }

function Test-Pipe([string]$Name) {
    try { return [bool](Test-Path -LiteralPath $Name) } catch { return $false }
}

# First nvim on PATH; follows a scoop shim (nvim.shim next to it) to the real nvim.exe
# so that no shim process stays alive next to the server.
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
if (-not $Nvim) { $Nvim = Resolve-RealExe 'nvim' }
if (-not $Nvim) { Out-Line 'FAILED nvim not found on PATH'; exit 1 }
if (-not $WorkingDirectory) { $WorkingDirectory = $env:USERPROFILE }

$mutexName = 'Local\work-kb-nvim-server-' + (($Pipe.Substring(9)) -replace '[^A-Za-z0-9_.-]', '_')
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$locked = $false
try {
    try { $locked = $mutex.WaitOne(30000) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (Test-Pipe $Pipe) { Out-Line ('RUNNING ' + $Pipe); exit 0 }
    if (-not $PSCmdlet.ShouldProcess(($Nvim + ' --headless --listen ' + $Pipe), 'start')) {
        Out-Line ('WHATIF start ' + $Nvim + ' --headless --listen ' + $Pipe)
        exit 0
    }
    $proc = Start-Process -FilePath $Nvim -ArgumentList @('--headless', '--listen', $Pipe) -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddMilliseconds($WaitMs)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Pipe $Pipe) { Out-Line ('STARTED pid=' + $proc.Id + ' ' + $Pipe); exit 0 }
        if ($proc.HasExited) { Out-Line ('FAILED nvim exited with code ' + $proc.ExitCode + ' before listening on ' + $Pipe); exit 1 }
        Start-Sleep -Milliseconds 50
    }
    Out-Line ('FAILED ' + $Pipe + ' did not appear within ' + $WaitMs + ' ms (pid ' + $proc.Id + ' left running)')
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
