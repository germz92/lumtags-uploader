# LumTags Uploader

Sony USB tether app for Windows and macOS. Guided setup picks an event and
collection, saves JPEGs to a local tether folder, and uploads them to S3.

## Run from source

Needs Python 3.10+.

**macOS:** use the python.org installer or Homebrew Python.

```
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
pip install -r requirements.txt
python3 main.py
```

**Windows:**

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Without a built camera host, the app uses the JPEG simulator. That is enough
to walk the wizard and upload path on either OS.

## Package for photographers

See [PACKAGING.md](PACKAGING.md) for the Mac `.app` / `.dmg` and Windows installer steps.

The Windows installer is self-contained (Python, Qt, Sony host). The camera USB driver is a separate one-time Device Manager step — it cannot go in the per-user installer.

Hand this to a Mac developer: [MAC_DMG.md](MAC_DMG.md).

```
# macOS
./scripts/build_macos.sh
./scripts/build_dmg.sh

# Windows
powershell -File scripts/build_windows.ps1
```

## Real Sony camera

1. Get the SDK for **your OS** from
   [Sony Camera Remote SDK](https://support.d-imaging.sony.co.jp/app/sdk/en/index.html)
   (Mac zip on a Mac, Win64 zip on Windows). Keep it off git.
2. Build `crsdk_host` — see [crsdk_host/README.md](crsdk_host/README.md).
3. Camera USB mode: **Remote Shoot (PC Remote)**. Quit Imaging Edge.
   - Windows: install libusbK.
   - macOS: data cable only; allow USB accessories if asked.

Session files live in `%APPDATA%\GalleryUploader` on Windows and
`~/Library/Application Support/GalleryUploader` on macOS.
