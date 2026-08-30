# Packaging LumTags Uploader

Build the **Mac app on a Mac** and the **Windows app on Windows**. Sony’s Camera Remote SDK is OS-specific. Do not copy `CRSDK-Win64`, `.venv`, or `crsdk_host/build` between machines.

Keep `.env` and the Sony SDK off git. Copy those onto each machine separately.

## What you need on each machine

| | Windows | macOS |
|---|---|---|
| Python | 3.10+ | 3.10+ (python.org or Homebrew) |
| Camera host | Visual Studio + CMake | Xcode Command Line Tools + CMake |
| Sony SDK | Win64 zip | Mac zip |
| USB | libusbK + data cable | Data cable; allow USB accessories if asked |
| Secrets | `.env` in the project root | same |

Clone this repo, then:

```
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Put `.env` in the project root. Unpack the **matching** Sony SDK and set `CRSDK_ROOT` to that folder.

Build the camera host (see [crsdk_host/README.md](crsdk_host/README.md)):

```
cmake -S crsdk_host -B crsdk_host/build -DCRSDK_ROOT="/path/to/CRSDK"
cmake --build crsdk_host/build --config Release
```

Confirm the live camera path before you package:

```
python3 main.py                    # Windows: python main.py
```

If `crsdk_host` is missing, the app falls back to the JPEG simulator. That is fine for UI checks, not for a photographer build.

---

A copy-paste brief for a Mac developer is in [MAC_DMG.md](MAC_DMG.md).

## Mac: `.app` then `.dmg`

Do this only on a Mac, after `python3 main.py` talks to the camera.

### 1. PyInstaller app

```
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh
open "dist/LumTags Uploader.app"
```

The spec already bundles `assets/app_icon.png` and looks for `crsdk_host` next to the app.

### 2. Disk image

```
chmod +x scripts/build_dmg.sh
./scripts/build_dmg.sh
```

Output: `dist/LumTags Uploader.dmg`.

### 3. Sign and notarize (required for other people’s Macs)

Without this, Gatekeeper blocks the app.

1. Enroll in the [Apple Developer Program](https://developer.apple.com/programs/).
2. Create a **Developer ID Application** certificate in Keychain.
3. Create an app-specific password and store it in Keychain, for example as `AC_PASSWORD`.
4. Then:

```
APP="dist/LumTags Uploader.app"
IDENTITY="Developer ID Application: Your Name (TEAMID)"

codesign --deep --force --options runtime --sign "$IDENTITY" "$APP"
xcrun notarytool submit "dist/LumTags Uploader.dmg" \
  --apple-id "you@example.com" --team-id TEAMID --password "@keychain:AC_PASSWORD" --wait
xcrun stapler staple "dist/LumTags Uploader.dmg"
```

Hardened runtime may need extra entitlements later (USB / camera). If notarization fails, read the log from `notarytool log`.

---

## Windows: folder/exe then installer

Do this only on Windows, after `python main.py` talks to the camera.

Quit the running app first so `crsdk_host.exe` is not locked.

### 1. PyInstaller folder + Inno Setup

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) once. Then:

```
powershell -File scripts/build_windows.ps1
```

Output:

- `dist\LumTags Uploader\LumTags Uploader.exe` (onedir payload)
- `dist\LumTags-Uploader-Setup.exe` (per-user installer, same pattern as Catalog Builder)

The installer ships Python, Qt, the Sony camera host, `Cr_Core.dll` / `monitor_protocol*.dll`, and `CrAdapter\`. Photographers do not install Python.

**USB driver is not in the installer.** Sony’s libusbK package needs Administrator rights and is not redistributable here. After install, if Device Manager does not show **Sony Remote Control Camera** under **libusbK USB Devices**, install it once with Have Disk on `srcameradriver.inf` from the Camera Remote SDK `Driver.zip`.

Copy `.env` into the install folder (next to `LumTags Uploader.exe`). The installer never bundles secrets.

Copy `Cr_Core.dll`, `monitor_protocol*.dll`, and the `CrAdapter\` folder next to `crsdk_host.exe` in `crsdk_host\build\Release` before you package if they are not already there. Those come from the Windows SDK, not from git.

### 2. Optional code signing

```
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /n "Your Cert" ^
  "dist\LumTags-Uploader-Setup.exe"
```

Unsigned installers work on your own PCs. Other machines will show SmartScreen warnings.

---

## Transferring to a Mac

You do **not** need a Cursor workspace export.

```
git clone https://github.com/germz92/lumtags-uploader.git
```

Then create a new `.venv`, copy `.env`, unpack the **Mac** Sony SDK, and build `crsdk_host` on that Mac.

Do not copy:

- `.venv`
- `crsdk_host/build`
- `CRSDK-Win64` or any Windows SDK tree
- `dist`

---

## Release checklist

- [ ] Camera connects on that OS from source
- [ ] Packaged app finds `crsdk_host` and the Sony adapters
- [ ] `.env` is copied next to the installed `LumTags Uploader.exe` (never committed or attached to a GitHub release)
- [ ] One live JPEG uploads to the gallery
- [ ] Mac: notarized DMG opens on a second Mac
- [ ] Windows: installer runs on a clean PC and the Start Menu / taskbar icon is the LT mark
- [ ] Windows: camera appears under libusbK after the one-time driver step if needed
