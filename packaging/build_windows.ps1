# Build the self-contained Windows app and (if Inno Setup is available) the
# installer, from a clean virtual environment. Run from the repository root:
#
#     powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Produces:
#   dist\ELAN Corpus Label Comparer\        (portable one-folder app)
#   dist-installer\ELAN-Corpus-Label-Comparer-Setup.exe   (if Inno Setup found)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1. Clean, isolated build environment (python.org / system Python, not conda).
$venv = Join-Path $root ".build-venv"
if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
py -3.12 -m venv $venv
$py = Join-Path $venv "Scripts\python.exe"

& $py -m pip install --upgrade pip
& $py -m pip install -e ".[build]"

# 2. Freeze the app (clean previous output first).
foreach ($d in @("build", "dist", "dist-installer")) {
    if (Test-Path (Join-Path $root $d)) { Remove-Item -Recurse -Force (Join-Path $root $d) }
}
& $py -m PyInstaller --noconfirm "packaging\cclc.spec"
Write-Host "Portable app: dist\ELAN Corpus Label Comparer\" -ForegroundColor Green

# 3. Build the installer if Inno Setup's compiler (iscc) is on PATH.
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $candidate = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $candidate) { $iscc = $candidate } else { $iscc = $null }
}
if ($iscc) {
    & $iscc "packaging\installer.iss"
    Write-Host "Installer: dist-installer\ELAN-Corpus-Label-Comparer-Setup.exe" -ForegroundColor Green
} else {
    Write-Warning "Inno Setup (iscc) not found - skipped installer. Install from https://jrsoftware.org/isdl.php, then run: iscc packaging\installer.iss"
}
