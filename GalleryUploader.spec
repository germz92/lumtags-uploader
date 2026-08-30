# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.building.osx import BUNDLE

block_cipher = None
root = os.path.abspath(SPECPATH)

host_name = "crsdk_host.exe" if sys.platform == "win32" else "crsdk_host"
host_candidates = [
    os.path.join(root, "crsdk_host", "build", "Release", host_name),
    os.path.join(root, "crsdk_host", "build", host_name),
    os.path.join(root, "dist", host_name),
]
binaries = []
for path in host_candidates:
    if os.path.isfile(path):
        binaries.append((path, "."))
        break

a = Analysis(
    ["main.py"],
    pathex=[root],
    binaries=binaries,
    datas=[
        (os.path.join(root, "assets", "app_icon.ico"), "assets"),
        (os.path.join(root, "assets", "app_icon.png"), "assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LumTags Uploader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(root, "assets", "app_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LumTags Uploader",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="LumTags Uploader.app",
        icon=os.path.join(root, "assets", "app_icon.png"),
        bundle_identifier="com.lumtags.uploader",
        info_plist={
            "CFBundleName": "LumTags Uploader",
            "CFBundleDisplayName": "LumTags Uploader",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSUSBAccessoryUsageDescription": (
                "LumTags Uploader connects to your Sony camera over USB to receive JPEGs."
            ),
        },
    )
