#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm GalleryUploader.spec
echo "App: dist/LumTags Uploader.app"
echo "Drag it to Applications, or run: open \"dist/LumTags Uploader.app\""
