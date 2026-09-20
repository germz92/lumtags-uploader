"""Windows and macOS paths, fonts, and camera-setup copy."""

import os
import sys
import subprocess
import time

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = getattr(sys, "frozen", False)

def _env_bool(name, fallback):
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return value.strip().lower() not in ("0", "false", "no", "off")


# Direct PTP backend: the host claims the camera's USB interface itself, so it
# takes the camera from Photos and ptpcamerad instead of asking them to let go.
# Default on Mac. Set LUMTAGS_CRSDK=1 (or LUMTAGS_PTP=0) to fall back to the SDK.
USE_PTP_BACKEND = (
    IS_MAC
    and _env_bool("LUMTAGS_PTP", True)
    and not _env_bool("LUMTAGS_CRSDK", False)
)
APP_NAME = "LumTags Uploader"
APP_ID = "com.lumtags.uploader"
WINDOWS_APP_ID = "Lumetry.LumTagsUploader"


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


def _icon_search_roots():
    return [resource_root(), executable_dir(), os.path.dirname(os.path.abspath(__file__))]


def _first_icon_named(names):
    for root in _icon_search_roots():
        for name in names:
            for path in (
                os.path.join(root, "assets", name),
                os.path.join(root, name),
            ):
                if os.path.isfile(path):
                    return path
    return ""


def app_icon_path():
    names = ("app_icon.ico", "app_icon.png") if IS_WINDOWS else ("app_icon.png", "app_icon.ico")
    return _first_icon_named(names)


def app_icon_pixmap(logical_size, widget=None):
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QGuiApplication, QIcon, QPixmap

    path = _first_icon_named(("app_icon.png", "app_icon.ico"))
    if not path:
        return QPixmap()
    dpr = 1.0
    if widget is not None:
        dpr = max(dpr, widget.devicePixelRatioF())
    screen = None
    if widget is not None:
        screen = widget.screen()
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is not None:
        dpr = max(dpr, screen.devicePixelRatio())
    icon = QIcon(path)
    pixmap = icon.pixmap(QSize(int(logical_size), int(logical_size)), dpr)
    if pixmap.isNull():
        pixmap = QPixmap(path).scaled(
            max(1, int(logical_size * dpr)),
            max(1, int(logical_size * dpr)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(dpr)
    return pixmap


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


def disable_mac_camera_hotplug():
    """Stop Photos / Image Capture from auto-opening when a camera is plugged in."""
    if not IS_MAC:
        return
    for args in (
        ["defaults", "write", "com.apple.ImageCapture", "disableHotPlug", "-bool", "YES"],
        ["defaults", "-currentHost", "write", "com.apple.ImageCapture", "disableHotPlug", "-bool", "YES"],
    ):
        try:
            subprocess.run(args, capture_output=True, timeout=4)
        except Exception:
            pass


# Apps that steal the Sony session. Do not kill USB system daemons (icdd) —
# Imaging Edge does not, and doing so makes EnumCameraObjects see nothing.
_MAC_COMPETING_APPS = (
    "Photos",
    "Image Capture",
    "Imaging Edge",
    "Imaging Edge Desktop",
    "Imaging Edge Remote",
)

_MAC_PTP_HELPERS = (
    "PTPCamera",
    "ptpcamerad",
)


def _killall(name, force=False):
    try:
        cmd = ["killall", "-9", name] if force else ["killall", name]
        subprocess.run(cmd, capture_output=True, timeout=4)
    except Exception:
        pass


def quit_mac_camera_apps():
    """Ask competing camera apps to quit, same as Imaging Edge expects."""
    if not IS_MAC:
        return
    disable_mac_camera_hotplug()
    for name in _MAC_COMPETING_APPS:
        _killall(name)


def release_mac_ptp_claimants():
    """Quit competing apps and free Apple's PTP helper once."""
    if not IS_MAC:
        return
    quit_mac_camera_apps()
    for name in _MAC_PTP_HELPERS:
        _killall(name, force=True)


def sony_usb_present():
    """True when a Sony USB device is visible to the OS (Mac). Other platforms skip the check."""
    if not IS_MAC:
        return True
    try:
        result = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-w0"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        text = result.stdout or ""
        return "idVendor\" = 1356" in text or "idVendor = 1356" in text or "Sony" in text
    except Exception:
        return True


def wait_for_sony_usb(timeout=10, on_status=None):
    if sony_usb_present():
        return True
    deadline = time.time() + timeout
    if on_status:
        on_status("Waiting for the camera on USB…")
    while time.time() < deadline:
        time.sleep(0.4)
        if sony_usb_present():
            return True
    return False


def prepare_camera_usb(on_status=None):
    """Once: close competing apps, free PTP, then leave USB alone for the Sony SDK."""
    if USE_PTP_BACKEND:
        # The host seizes the interface on its own, so there is no reason to
        # close the photographer's other apps.
        return
    if on_status:
        on_status("Closing Photos and Imaging Edge…")
    release_mac_ptp_claimants()
    time.sleep(2.0)


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


SUPPORTED_CAMERAS = (
    ("Alpha", (
        "α1 / α1 II",
        "α9 II / α9 III",
        "α7 IV / α7 V",
        "α7R IV / α7R IVA / α7R V / α7R VI",
        "α7S III",
        "α7C / α7C II / α7CR",
        "α6700",
    )),
    ("Cinema / FX", (
        "FX2 / FX3 / FX3A / FX30 / FX6",
        "FR7",
        "ILX-LR1",
    )),
    ("ZV / compact", (
        "ZV-E1",
        "ZV-E10 II",
        "RX0 II",
        "RX1R III",
    )),
    ("Broadcast / other", (
        "PXW-Z200 / PXW-Z300 / PXW-Z380",
        "HXR-NX800",
        "BRC-AM7",
        "MPC-2610",
    )),
)


def camera_setup_steps():
    if USE_PTP_BACKEND:
        return (
            "Set USB mode to Remote Shoot (PC Remote).",
            "Use a data USB-C cable and turn the camera on.",
        )
    if IS_MAC:
        return (
            "Set USB mode to Remote Shoot (PC Remote).",
            "Use a data USB-C cable. Quit Photos if it opens.",
        )
    return (
        "Set USB mode to Remote Shoot (PC Remote).",
        "Use a USB data cable (not charge-only) and turn the camera on.",
    )


def usb_hint():
    if USE_PTP_BACKEND:
        return (
            "Plug in a data USB-C cable and set USB mode to Remote Shoot (PC Remote). "
            "Photos and Image Capture can stay open — the app takes the camera from them. "
            "Allow the app if System Settings asks about USB accessories."
        )
    if IS_MAC:
        return (
            "Plug in a data USB-C cable and set USB mode to Remote Shoot (PC Remote). "
            "Quit Photos, Image Capture, and Imaging Edge — they steal the USB session. "
            "Allow the app if System Settings asks about USB accessories."
        )
    return (
        "Plug in USB and set the camera USB mode to Remote Shoot (PC Remote). "
        "Imaging Edge is not required. If it is installed, quit it so it does not "
        "hold the camera. In Device Manager the camera should be under "
        "libusbK USB Devices."
    )


def no_camera_hint():
    if USE_PTP_BACKEND:
        return (
            "No camera found. Check that the cable carries data, that the camera is on, "
            "and that USB mode is Remote Shoot (PC Remote), then Scan again. "
            "Trust the USB accessory if macOS prompts."
        )
    if IS_MAC:
        return (
            "No camera found. Check the cable and PC Remote mode. Quit Photos, Image "
            "Capture, and Imaging Edge, then Scan again. Trust the USB accessory if macOS prompts."
        )
    return (
        "No camera found. Confirm USB mode is Remote Shoot (PC Remote) and that "
        "Device Manager shows Sony Remote Control Camera under libusbK USB Devices. "
        "You do not need Imaging Edge."
    )


def connect_failed_hint():
    if USE_PTP_BACKEND:
        return (
            "Connect failed. Turn the camera off and back on, then Scan again."
        )
    if IS_MAC:
        return (
            "Connect failed. Quit Photos, Image Capture, and Imaging Edge, then Scan again."
        )
    return (
        "Connect failed. Set USB mode to Remote Shoot (PC Remote) and try Scan again."
    )


def popen_kwargs():
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs
