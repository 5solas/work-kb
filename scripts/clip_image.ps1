# Save the clipboard image as PNG. ASCII only; the caller (Neovim) owns user messages.
# Exit codes: 0 ok (or image present with -Probe), 2 no image on clipboard, 3 error.
# -FromFile <image> is a test-only seam: it loads the bitmap from that file instead of
# the clipboard (the clipboard is never touched) and then takes the same save path.
# Relative -Out and -FromFile paths resolve against the current directory ($PWD).
param(
    [string]$Out,
    [switch]$Probe,
    [string]$FromFile
)

$ErrorActionPreference = 'Stop'

function Fail([string]$msg) {
    [Console]::Error.WriteLine($msg)
    exit 3
}

function Resolve-Full([string]$path) {
    if ([IO.Path]::IsPathRooted($path)) {
        return [IO.Path]::GetFullPath($path)
    }
    return [IO.Path]::GetFullPath((Join-Path $PWD.Path $path))
}

try {
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne [Threading.ApartmentState]::STA) {
        Fail 'clip_image: STA required (run powershell.exe with -STA)'
    }
    if (-not $Probe -and [string]::IsNullOrWhiteSpace($Out)) {
        Fail 'clip_image: -Out is required'
    }
    Add-Type -AssemblyName System.Drawing

    $img = $null
    if ($FromFile) {
        $src = Resolve-Full $FromFile
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
            Fail ('clip_image: -FromFile not found: ' + $src)
        }
        # Copy into a new Bitmap so the source file is not kept open.
        $loaded = [Drawing.Image]::FromFile($src)
        try {
            $img = New-Object Drawing.Bitmap $loaded
        } finally {
            $loaded.Dispose()
        }
    } else {
        Add-Type -AssemblyName System.Windows.Forms
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
        $img = [Windows.Forms.Clipboard]::GetImage()
        if ($null -eq $img) {
            exit 2
        }
    }

    try {
        if ($Probe) {
            exit 0
        }
        $full = Resolve-Full $Out
        $dir = [IO.Path]::GetDirectoryName($full)
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            [void](New-Item -ItemType Directory -Path $dir -Force)
        }
        $img.Save($full, [Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $img.Dispose()
    }
    exit 0
} catch {
    Fail ('clip_image: ' + $_.Exception.Message)
}
