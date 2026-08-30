$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

& $python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& $python scripts\make_app_icon.py
if ($LASTEXITCODE -ne 0) { throw "icon conversion failed" }

& $python -m PyInstaller --noconfirm GalleryUploader.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source }

$setup = "dist\LumTags-Uploader-Setup.exe"
if ($iscc) {
    if (Test-Path $setup) { Remove-Item $setup -Force }
    & $iscc "/DAppVersion=1.0.0" "installer\windows.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Installer: $setup"
} else {
    Write-Warning "Inno Setup 6 not found. Install it to build $setup"
}

Write-Host "App folder: dist\LumTags Uploader"
Write-Host "Run: dist\LumTags Uploader\LumTags Uploader.exe"
