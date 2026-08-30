"""Windows and macOS paths, fonts, and camera-setup copy."""

import os
import sys
import subprocess

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)
APP_NAME = "LumTags Uploader"
APP_ID = "com.lumtags.uploader"


def ui_font_family():
    if IS_MAC:
        return "Helvetica Neue"
    if IS_WINDOWS:
        return "Segoe UI"
    return "sans-serif"


def mono_font_family():
    if IS_MAC:
        return "Menlo"
    if IS_WINDOWS:
        return "Consolas"
    return "monospace"


def app_support_dir():
    if IS_MAC:
        root = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    elif IS_WINDOWS:
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(root, "GalleryUploader")
    os.makedirs(folder, exist_ok=True)
    return folder


def app_icon_path():
    root = resource_root()
    for name in ("app_icon.ico", "app_icon.png"):
        path = os.path.join(root, "assets", name)
        if os.path.isfile(path):
            return path
        bundled = os.path.join(root, name)
        if os.path.isfile(bundled):
            return bundled
    return ""


def resource_root():
    if IS_FROZEN:
        if IS_MAC:
            macos = os.path.dirname(os.path.abspath(sys.executable))
            resources = os.path.normpath(os.path.join(macos, "..", "Resources"))
            if os.path.isdir(resources):
                return resources
            return macos
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def executable_dir():
    if IS_FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def native_host_name():
    return "crsdk_host.exe" if IS_WINDOWS else "crsdk_host"


def kill_stale_camera_hosts():
    name = native_host_name()
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/IM", name, "/F"],
                capture_output=True,
                timeout=5,
                **popen_kwargs(),
            )
        else:
            subprocess.run(["pkill", "-x", name], capture_output=True, timeout=5)
    except Exception:
        pass


def native_host_candidates():
    root = resource_root()
    exe_dir = executable_dir()
    name = native_host_name()
    return [
        os.environ.get("CRSDK_HOST") or "",
        os.path.join(exe_dir, name),
        os.path.join(root, name),
        os.path.join(root, "crsdk_host", name),
        os.path.join(root, "crsdk_host", "build", name),
        os.path.join(root, "crsdk_host", "build", "Release", name),
        os.path.join(root, "crsdk_host", "build", "Release", "crsdk_host.exe"),
        os.path.join(root, "dist", name),
    ]


def default_parent_path():
    pictures = os.path.join(os.path.expanduser("~"), "Pictures")
    if os.path.isdir(pictures):
        return pictures
    return os.path.expanduser("~")


def camera_setup_steps():
    if IS_MAC:
        return (
            "Set USB mode to Remote Shoot (PC Remote).",
            "Plug in a data USB-C cable.",
        )
    return (
        "Set USB mode to Remote Shoot (PC Remote).",
        "Plug in a USB data cable (not a charge-only cable).",
    )


def usb_hint():
    if IS_MAC:
        return (
            "Plug in a data USB-C cable, set the camera USB mode to Remote Shoot "
            "(PC Remote), and quit Imaging Edge / Remote. On macOS you do not need "
            "libusbK. If the camera is not listed, try another cable and allow the "
            "app if System Settings asks about USB accessories."
        )
    return (
        "Plug in USB and set the camera USB mode to Remote Shoot (PC Remote). "
        "Imaging Edge is not required. If it is installed, quit it so it does not "
        "hold the camera. In Device Manager the camera should be under "
        "libusbK USB Devices."
    )


def no_camera_hint():
    if IS_MAC:
        return (
            "No camera found. Check the cable, PC Remote mode, and that Imaging Edge "
            "is quit. Trust the USB accessory if macOS prompts."
        )
    return (
        "No camera found. Confirm USB mode is Remote Shoot (PC Remote) and that "
        "Device Manager shows Sony Remote Control Camera under libusbK USB Devices. "
        "You do not need Imaging Edge."
    )


def connect_failed_hint():
    if IS_MAC:
        return (
            "Connect failed. Set USB mode to Remote Shoot (PC Remote) and quit Imaging Edge."
        )
    return (
        "Connect failed. Set USB mode to Remote Shoot (PC Remote) and try Scan again."
    )


def popen_kwargs():
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs
