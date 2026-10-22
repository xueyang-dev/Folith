# Build the Folith Qt desktop bundle for Windows x64.
#
# Produces the same layout the release checks consume:
#   build/qt-windows/   build record, icon, OCR payload, PyInstaller work
#   dist/qt-windows/    portable one-folder application (Folith\Folith.exe)
#   dist/installer/     Folith-Qt-<version>-windows-x64.zip
#                       Folith-Setup-<version>-windows-x64.exe (NSIS, optional)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build_qt_windows.ps1 `
#       -Python .venv-windows\Scripts\python.exe -Tesseract "C:\Program Files\Tesseract-OCR\tesseract.exe"
#
#   -NoOcr        explicitly build without the OCR payload (never call the result OCR-complete)
#   -PortableOnly skip the NSIS installer even when makensis is available
#   -Clean        remove previous work/dist output before building
#
# The build record is written from the checkout that is building.  Release
# builds must run from a clean worktree so worktree_dirty=false is a fact.

[CmdletBinding()]
param(
    [string]$Python = $env:FOLITH_BUILD_PYTHON,
    [string]$Tesseract = $env:FOLIOTHREAD_TESSERACT_CMD,
    [switch]$NoOcr,
    [switch]$PortableOnly,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OutDir = Join-Path $Root "build\qt-windows"
$DistDir = Join-Path $Root "dist\qt-windows"
$InstallerDir = Join-Path $Root "dist\installer"
$BundleDir = Join-Path $DistDir "Folith"
$BuildInfo = Join-Path $OutDir "desktop_build_info.json"
$Icon = Join-Path $OutDir "Folith.ico"
$Payload = Join-Path $OutDir "tesseract"

if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
    throw "This script builds the Windows x64 artifact and must run on Windows."
}
if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "Windows x64 is the supported release target; found $env:PROCESSOR_ARCHITECTURE."
}
if (-not $Python) {
    $candidate = Join-Path $Root ".venv-windows\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $Python = $candidate
    } else {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}
if (-not (Test-Path -LiteralPath $Python)) {
    $resolved = Get-Command $Python -ErrorAction SilentlyContinue
    if ($resolved) { $Python = $resolved.Source }
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python interpreter not found: $Python"
}
$Python = (Resolve-Path -LiteralPath $Python).Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:FOLITH_QT_OUT_DIR = $OutDir
$env:FOLITH_QT_DIST_DIR = $DistDir
$env:FOLITH_BUILD_INFO = $BuildInfo
$env:PYINSTALLER_CONFIG_DIR = Join-Path $OutDir "pyinstaller-cache"

$Version = (& $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
if (-not $Version) { throw "Could not read the project version from pyproject.toml" }
Write-Host "=== Folith $Version (Windows x64) ==="

if ($Clean) {
    foreach ($path in @($DistDir, (Join-Path $OutDir "work"), (Join-Path $OutDir "pyinstaller-cache"))) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
}
New-Item -ItemType Directory -Force -Path $OutDir, $DistDir, $InstallerDir | Out-Null

Write-Host "=== build record (shell=qt) ==="
& $Python (Join-Path $Root "scripts\write_desktop_build_info.py") `
    --out $BuildInfo --backend pyinstaller --platform windows --arch AMD64 --shell qt
if ($LASTEXITCODE -ne 0) { throw "writing the build record failed" }

Write-Host "=== application icon ==="
& $Python (Join-Path $Root "scripts\build_app_icon.py") --ico-out $Icon
if ($LASTEXITCODE -ne 0) { throw "building the Windows icon failed" }

if ($NoOcr) {
    if (Test-Path -LiteralPath $Payload) { Remove-Item -LiteralPath $Payload -Recurse -Force }
    Write-Host "=== OCR payload: intentionally omitted (-NoOcr) ==="
} else {
    Write-Host "=== OCR payload (Tesseract + eng/chi_sim/ara) ==="
    $stageArgs = @((Join-Path $Root "scripts\stage_tesseract_payload.py"), "--out", $Payload, "--force")
    if ($Tesseract) { $stageArgs += @("--tesseract", $Tesseract) }
    & $Python @stageArgs
    if ($LASTEXITCODE -ne 0) { throw "staging the OCR payload failed" }
}

Write-Host "=== PyInstaller ==="
& $Python -m PyInstaller --noconfirm `
    --distpath $DistDir --workpath (Join-Path $OutDir "work") `
    (Join-Path $Root "packaging\folith-qt.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Exe = Join-Path $BundleDir "Folith.exe"
if (-not (Test-Path -LiteralPath $Exe)) { throw "the frozen executable is missing: $Exe" }
# The uninstaller removes exactly these entries (never the whole folder), so a
# new top-level file would be left behind after uninstalling.
$TopLevel = @(Get-ChildItem -LiteralPath $BundleDir -Force | ForEach-Object { $_.Name })
$Expected = @("Folith.exe", "_internal")
if ($TopLevel.Count -ne $Expected.Count -or
        @($TopLevel | Where-Object { $Expected -notcontains $_ }).Count -ne 0) {
    throw "unexpected bundle top level ($($TopLevel -join ', ')); update the NSIS uninstall section"
}

Write-Host "=== portable ZIP ==="
$Zip = Join-Path $InstallerDir "Folith-Qt-$Version-windows-x64.zip"
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $BundleDir,
    $Zip,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true)
if (-not (Test-Path -LiteralPath $Zip)) { throw "creating the portable ZIP failed" }

Write-Host "=== installer (NSIS) ==="
$Installer = Join-Path $InstallerDir "Folith-Setup-$Version-windows-x64.exe"
$Makensis = Get-Command makensis -ErrorAction SilentlyContinue
if (-not $Makensis) {
    # A fresh NSIS install (e.g. choco in an earlier CI step) is not on this
    # session's PATH yet.  Newer Chocolatey NSIS packages deploy through
    # nsis.install, so check the usual folder and then fall back to a shallow
    # recursive search under the Chocolatey library.
    $candidates = @()
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if ($base) { $candidates += (Join-Path $base "NSIS\makensis.exe") }
    }
    $candidates += (Join-Path $env:ProgramData "chocolatey\lib\nsis\tools\makensis.exe")
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $Makensis = Get-Command $candidate
            break
        }
    }
}
if (-not $Makensis -and $env:ProgramData) {
    $found = Get-ChildItem -LiteralPath (Join-Path $env:ProgramData "chocolatey\lib") `
        -Filter makensis.exe -File -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { $Makensis = Get-Command $found.FullName }
}
if ($PortableOnly) {
    Write-Host "installer skipped (-PortableOnly)"
} elseif (-not $Makensis) {
    throw "makensis was not found; install NSIS or pass -PortableOnly explicitly"
} else {
    Write-Host "using makensis $($Makensis.Source)"
    if (Test-Path -LiteralPath $Installer) { Remove-Item -LiteralPath $Installer -Force }
    & $Makensis.Source -V2 "-DAPP_VERSION=$Version" "-DBASE=$Root" "-DOUT_FILE=$Installer" `
        (Join-Path $Root "packaging\folith-windows.nsi")
    if ($LASTEXITCODE -ne 0) { throw "NSIS failed" }
    if (-not (Test-Path -LiteralPath $Installer)) { throw "the installer was not created" }
}

$Summary = [ordered]@{
    app_version = $Version
    target_platform = "windows"
    target_arch = "AMD64"
    portable_zip = $Zip
    installer = if ($PortableOnly -or -not (Test-Path -LiteralPath $Installer)) { $null } else { $Installer }
    blocker_directory = $BundleDir
    exe = $Exe
    ocr_bundled = (-not $NoOcr)
    build_info = $BuildInfo
}
$SummaryPath = Join-Path $OutDir "windows-package.json"
$Summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $SummaryPath -Encoding UTF8
$Summary | ConvertTo-Json -Depth 4

Write-Host ""
Write-Host "Portable ZIP: $Zip"
if ($Summary.installer) { Write-Host "Installer:    $($Summary.installer)" }
Write-Host "Bundle:       $BundleDir"
