#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."

# Build the native host first. PyInstaller copies whatever is in
# crsdk_host/build, so skipping this silently ships the previous binary.
if command -v cmake >/dev/null 2>&1; then
  CMAKE_ARGS=(-S crsdk_host -B crsdk_host/build)
  if [[ -n "${CRSDK_ROOT:-}" ]]; then
    CMAKE_ARGS+=(-DCRSDK_ROOT="$CRSDK_ROOT")
  else
    echo "Note: CRSDK_ROOT is not set — building without the Sony SDK fallback." >&2
  fi
  cmake "${CMAKE_ARGS[@]}"
  cmake --build crsdk_host/build --config Release
else
  echo "Warning: cmake not found — packaging the existing crsdk_host build." >&2
fi

python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm GalleryUploader.spec

APP="dist/LumTags Uploader.app"
MACOS="$APP/Contents/MacOS"
FW="$APP/Contents/Frameworks"
HOST=""
for candidate in \
  "crsdk_host/build/crsdk_host" \
  "crsdk_host/build/Release/crsdk_host" \
  "$HOME/sdk/crsdk_host_bin/crsdk_host"
do
  if [[ -f "$candidate" ]]; then
    HOST="$candidate"
    break
  fi
done

if [[ -n "$HOST" && -d "$APP" ]]; then
  HOST_DIR="$(dirname "$HOST")"
  mkdir -p "$MACOS" "$FW/CrAdapter" "$MACOS/CrAdapter" "$MACOS/Contents/Frameworks/CrAdapter"
  cp -f "$HOST" "$MACOS/crsdk_host"
  chmod +x "$MACOS/crsdk_host"
  # Core Sony dylibs must sit next to crsdk_host (@executable_path).
  for dylib in "$HOST_DIR"/*.dylib; do
    [[ -f "$dylib" ]] || continue
    cp -f "$dylib" "$MACOS/"
    cp -f "$dylib" "$FW/"
  done
  ADAPTER=""
  if [[ -d "$HOST_DIR/CrAdapter" ]]; then
    ADAPTER="$HOST_DIR/CrAdapter"
  elif [[ -d "$HOST_DIR/Contents/Frameworks/CrAdapter" ]]; then
    ADAPTER="$HOST_DIR/Contents/Frameworks/CrAdapter"
  fi
  if [[ -n "$ADAPTER" ]]; then
    cp -R "$ADAPTER"/. "$FW/CrAdapter/"
    cp -R "$ADAPTER"/. "$MACOS/CrAdapter/"
    cp -R "$ADAPTER"/. "$MACOS/Contents/Frameworks/CrAdapter/"
  fi
  # Downloaded Sony dylibs carry quarantine; dyld will hang until it is stripped.
  xattr -cr "$MACOS/crsdk_host" "$MACOS"/*.dylib "$MACOS/CrAdapter" "$FW" 2>/dev/null || true
  ENT="packaging/macos.entitlements"
  # Set CODESIGN_IDENTITY to a Developer ID to produce a notarizable build.
  # The entitlements must be applied to the helper too: it is a separate binary
  # that talks to IOKit and loads Sony's dylibs.
  IDENTITY="${CODESIGN_IDENTITY:--}"
  SIGN_OPTS=(--force --sign "$IDENTITY" --timestamp=none)
  if [[ "$IDENTITY" != "-" ]]; then
    SIGN_OPTS=(--force --sign "$IDENTITY" --options runtime --timestamp)
  fi
  for dylib in "$MACOS"/*.dylib "$MACOS/CrAdapter"/*.dylib "$FW"/*.dylib "$FW/CrAdapter"/*.dylib; do
    [[ -f "$dylib" ]] || continue
    codesign "${SIGN_OPTS[@]}" "$dylib" || echo "Warning: could not sign $dylib" >&2
  done
  codesign "${SIGN_OPTS[@]}" --entitlements "$ENT" "$MACOS/crsdk_host" \
    || echo "Warning: could not sign crsdk_host" >&2
  # The bundle is signed last so the inner signatures are sealed into it.
  codesign "${SIGN_OPTS[@]}" --entitlements "$ENT" "$APP" \
    || echo "Warning: could not sign the app bundle" >&2
  echo "Bundled crsdk_host + Sony dylibs/adapters into the .app"

  # A packaged app with a pre-PTP helper silently falls back to the SDK, which
  # is exactly the regression this check exists to catch.
  if otool -L "$MACOS/crsdk_host" 2>/dev/null | grep -q IOKit; then
    echo "Verified: bundled crsdk_host has the direct PTP backend."
  else
    echo "Warning: bundled crsdk_host has no PTP backend — it will fall back to the Sony SDK." >&2
  fi
else
  echo "Warning: crsdk_host not found — packaged app will use the JPEG simulator" >&2
fi

echo "App: dist/LumTags Uploader.app"
echo "Drag it to Applications, or run: open \"dist/LumTags Uploader.app\""
