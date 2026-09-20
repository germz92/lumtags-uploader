#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."

APP="dist/LumTags Uploader.app"
DMG="dist/LumTags Uploader.dmg"
VOL="LumTags Uploader"
STAGE="dist/dmg_stage"

if [[ ! -d "$APP" ]]; then
  echo "Build the .app first: ./scripts/build_macos.sh" >&2
  exit 1
fi

rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/How to install.txt" <<'EOF'
LumTags Uploader

1. Drag "LumTags Uploader" onto Applications.
2. The first time, right-click the app and choose Open (it is unsigned).
3. Put your .env file here so uploads work:
   ~/Library/Application Support/GalleryUploader/.env

The app is self-contained. Python, Qt, and the Sony USB host are inside the bundle.
EOF

hdiutil create -volname "$VOL" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "DMG: $DMG"
