# Mac developer brief — LumTags Uploader DMG

Send this file to the Mac developer. The goal is a working **`LumTags Uploader.dmg`** they can hand back. The Windows build is already handled elsewhere.

Repo (private): https://github.com/germz92/lumtags-uploader

Ask the repo owner to **invite your GitHub account**. Also get these **off git**, in a separate drop (AirDrop / 1Password / zip):

1. `.env` (Mongo + AWS keys)
2. The **Mac** Sony Camera Remote SDK zip (not the Windows one)

Do **not** copy a Windows `.venv`, `crsdk_host/build`, or `CRSDK-Win64`. Those will not work on a Mac.

---

## What you are building

Python + Qt app (`main.py`). It talks to a native helper, `crsdk_host`, which uses Sony’s Camera Remote SDK over USB. PyInstaller wraps that into `LumTags Uploader.app`. `scripts/build_dmg.sh` wraps the app in a DMG.

A photographer build needs a **real** `crsdk_host` linked against the Mac SDK. If that binary is missing, the app falls back to a JPEG simulator.

---

## 1. Machine setup

- Apple silicon or Intel Mac
- Xcode Command Line Tools: `xcode-select --install`
- CMake (`brew install cmake`)
- Python 3.10+ (python.org or `brew install python`)
- A Sony mirrorless set to **Remote Shoot (PC Remote)** and a **data** USB cable
- If macOS asks about USB accessories, allow it

---

## 2. Clone and run from source first

```
git clone https://github.com/germz92/lumtags-uploader.git
cd lumtags-uploader
git checkout main
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Put `.env` in the project root (same folder as `main.py`).

Unpack the **Mac** Sony SDK somewhere **outside** the repo, for example:

`~/sdk/CrSDK_Mac`

Then build the camera host:

```
export CRSDK_ROOT="$HOME/sdk/CrSDK_Mac"
cmake -S crsdk_host -B crsdk_host/build -DCRSDK_ROOT="$CRSDK_ROOT"
cmake --build crsdk_host/build --config Release
```

You want `crsdk_host/build/crsdk_host`. Copy any Sony `.dylib` folders the SDK expects to sit next to that binary (same idea as Windows `CrAdapter` — keep adapter folders as subfolders; do not flatten them).

Run:

```
python3 main.py
```

The log should say `Camera host started (crsdk)`, not `simulator`. Walk the setup wizard, connect the camera, shoot a JPEG, confirm it appears and uploads.

**Stop here if the camera does not connect from source.** Packaging will only freeze a broken host.

---

## 3. Build the `.app`

```
source .venv/bin/activate
chmod +x scripts/build_macos.sh scripts/build_dmg.sh
./scripts/build_macos.sh
open "dist/LumTags Uploader.app"
```

PyInstaller uses `GalleryUploader.spec`. It should pick up `crsdk_host` from `crsdk_host/build/crsdk_host` and the icon from `assets/app_icon.png`.

Test the **packaged** app the same way: camera connect + one live JPEG. The `.app` must still find `crsdk_host` and the Sony dylibs. If the packaged app drops to the simulator, copy the host + dylibs/adapters into the app bundle next to the executable (or next to `crsdk_host`) and try again. Note what you did so we can bake it into the spec.

---

## 4. Build the DMG

```
./scripts/build_dmg.sh
```

Output: `dist/LumTags Uploader.dmg`

That script uses `hdiutil` (UDZO). A signed/notarized DMG is what other people can open without Gatekeeper blocking it.

---

## 5. Sign and notarize (needed for anyone else’s Mac)

Requires an **Apple Developer Program** account and a **Developer ID Application** cert in Keychain.

1. Create an app-specific password at appleid.apple.com and store it in Keychain as `AC_PASSWORD`.
2. Replace the identity and Apple ID:

```
APP="dist/LumTags Uploader.app"
DMG="dist/LumTags Uploader.dmg"
IDENTITY="Developer ID Application: Your Name (TEAMID)"

codesign --deep --force --options runtime --sign "$IDENTITY" "$APP"
./scripts/build_dmg.sh
xcrun notarytool submit "$DMG" \
  --apple-id "you@example.com" \
  --team-id TEAMID \
  --password "@keychain:AC_PASSWORD" \
  --wait
xcrun stapler staple "$DMG"
```

If notarization fails:

```
xcrun notarytool history --apple-id "you@example.com" --team-id TEAMID --password "@keychain:AC_PASSWORD"
xcrun notarytool log <SUBMISSION_ID> --apple-id "you@example.com" --team-id TEAMID --password "@keychain:AC_PASSWORD"
```

USB / camera access may need extra entitlements under hardened runtime. If the signed app connects worse than the unsigned `.app`, that is the first place to look.

If you do **not** have a Developer ID, still send the unsigned DMG and say so. It will only run on Macs that right-click → Open.

---

## 6. What to send back

- `dist/LumTags Uploader.dmg`
- Whether it is **signed + notarized** or unsigned
- Mac model / chip (Apple silicon vs Intel) and macOS version
- Confirmation that the **packaged** app connected a Sony camera and uploaded one JPEG
- Any extra files you had to copy into the `.app` (dylibs, adapter folders)
- Build notes or errors (cmake, PyInstaller, notarize log)

Do not commit `.env`, the Sony SDK, `.venv`, or `crsdk_host/build`.

---

## Quick commands (after setup)

```
source .venv/bin/activate
export CRSDK_ROOT="$HOME/sdk/CrSDK_Mac"
python3 main.py                          # source test
./scripts/build_macos.sh                 # .app
./scripts/build_dmg.sh                   # .dmg
```

More context: [PACKAGING.md](PACKAGING.md) and [crsdk_host/README.md](crsdk_host/README.md).
