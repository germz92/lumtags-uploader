#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."

APP="dist/LumTags Uploader.app"
DMG="dist/LumTags Uploader.dmg"
VOL="LumTags Uploader"

if [[ ! -d "$APP" ]]; then
  echo "Build the .app first: ./scripts/build_macos.sh" >&2
  exit 1
fi

rm -f "$DMG"
hdiutil create -volname "$VOL" -srcfolder "$APP" -ov -format UDZO "$DMG"
echo "DMG: $DMG"
