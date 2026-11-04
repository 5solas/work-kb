# Save the clipboard image as PNG. ASCII only; the caller (Emacs) owns user messages.
# Exit codes: 0 ok (or image present with -Probe), 2 no image on clipboard, 3 error.
param(
    [string]$Out,
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'

function Fail([string]$msg) {
    [Console]::Error.WriteLine($msg)
    exit 3
}

try {
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne [Threading.ApartmentState]::STA) {
        Fail 'clip_image: STA required (run powershell.exe with -STA)'
    }
    if (-not $Probe -and [string]::IsNullOrWhiteSpace($Out)) {
        Fail 'clip_image: -Out is required'
    }
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    # The clipboard may be briefly locked by another process; retry a few times.
    $has = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $has = [Windows.Forms.Clipboard]::ContainsImage()
            break
        } catch {
            if ($i -eq 9) { throw }
            Start-Sleep -Milliseconds 100
        }
    }
    if (-not $has) {
        exit 2
    }
    if ($Probe) {
        exit 0
    }

    if ([IO.Path]::IsPathRooted($Out)) {
        $full = [IO.Path]::GetFullPath($Out)
    } else {
        $full = [IO.Path]::GetFullPath((Join-Path $PWD.Path $Out))
    }
    $dir = [IO.Path]::GetDirectoryName($full)
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        [void](New-Item -ItemType Directory -Path $dir -Force)
    }

    $img = [Windows.Forms.Clipboard]::GetImage()
    if ($null -eq $img) {
        exit 2
    }
    try {
        $img.Save($full, [Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $img.Dispose()
    }
    exit 0
} catch {
    Fail ('clip_image: ' + $_.Exception.Message)
}
