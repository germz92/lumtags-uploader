$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm GalleryUploader.spec

Write-Host "App folder: dist\LumTags Uploader"
Write-Host "Run: dist\LumTags Uploader\LumTags Uploader.exe"
