"""PIL to Qt image conversion. QImage work can run off the UI thread."""

import os
import time
from datetime import datetime

from PIL import Image, ImageOps
from PIL.ExifTags import IFD
from PySide6.QtGui import QImage, QPixmap

THUMB_SIZE = (512, 384)
THUMB_RETRIES = 20
THUMB_RETRY_WAIT = 0.25
PLACEHOLDER_RGB = (31, 35, 42)


def jpeg_is_complete(file_path):
    if not file_path.lower().endswith((".jpg", ".jpeg")):
        return os.path.isfile(file_path)
    try:
        if not os.path.isfile(file_path) or os.path.getsize(file_path) < 1024:
            return False
        with open(file_path, "rb") as handle:
            handle.seek(-2, os.SEEK_END)
            return handle.read(2) == b"\xff\xd9"
    except OSError:
        return False


def _file_size_stable(file_path, settle=0.08):
    try:
        first = os.path.getsize(file_path)
        time.sleep(settle)
        return first >= 1024 and os.path.getsize(file_path) == first
    except OSError:
        return False


def file_ready_for_thumb(file_path):
    return jpeg_is_complete(file_path) and _file_size_stable(file_path)


def thumb_cache_dir(tether_folder):
    path = os.path.join(tether_folder, ".thumbs")
    os.makedirs(path, exist_ok=True)
    return path


def _thumb_cache_path(tether_folder, file_path):
    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = 0
    return os.path.join(thumb_cache_dir(tether_folder), f"{os.path.basename(file_path)}.{size}.512.jpg")


def is_placeholder_image(image):
    if image is None or image.size[0] < 8 or image.size[1] < 8:
        return True
    rgb = image.convert("RGB")
    samples = (
        (0, 0),
        (rgb.width - 1, 0),
        (0, rgb.height - 1),
        (rgb.width - 1, rgb.height - 1),
        (rgb.width // 2, rgb.height // 2),
    )
    return all(rgb.getpixel(point) == PLACEHOLDER_RGB for point in samples)


def pixmap_is_placeholder(pixmap):
    if pixmap is None or pixmap.isNull():
        return True
    image = pixmap.toImage()
    if image.width() < 8 or image.height() < 8:
        return True
    samples = (
        (0, 0),
        (image.width() - 1, 0),
        (0, image.height() - 1),
        (image.width() - 1, image.height() - 1),
        (image.width() // 2, image.height() // 2),
    )
    return all(
        (image.pixelColor(x, y).red(), image.pixelColor(x, y).green(), image.pixelColor(x, y).blue())
        == PLACEHOLDER_RGB
        for x, y in samples
    )


def make_thumbnail(file_path, tether_folder, size=THUMB_SIZE):
    if not tether_folder:
        tether_folder = os.path.dirname(file_path)
    if not tether_folder:
        return None
    for _attempt in range(THUMB_RETRIES):
        try:
            if not file_ready_for_thumb(file_path):
                time.sleep(THUMB_RETRY_WAIT)
                continue
            cache = _thumb_cache_path(tether_folder, file_path)
            if not os.path.isfile(cache) or os.path.getmtime(file_path) > os.path.getmtime(cache):
                image = Image.open(file_path)
                image = ImageOps.exif_transpose(image)
                image.load()
                image.thumbnail(size, Image.Resampling.LANCZOS)
                rgb = image.convert("RGB")
                if is_placeholder_image(rgb):
                    time.sleep(THUMB_RETRY_WAIT)
                    continue
                rgb.save(cache, "JPEG", quality=82)
            cached = Image.open(cache)
            cached.load()
            if is_placeholder_image(cached):
                os.remove(cache)
                time.sleep(THUMB_RETRY_WAIT)
                continue
            return cached
        except Exception:
            time.sleep(THUMB_RETRY_WAIT)
    return None


def _exif_number(value):
    if value is None:
        return None
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        if not value.denominator:
            return None
        return value.numerator / value.denominator
    if isinstance(value, tuple) and len(value) == 2 and value[1]:
        return value[0] / value[1]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_shutter(value):
    seconds = _exif_number(value)
    if seconds is None or seconds <= 0:
        return ""
    if seconds >= 1:
        return f"{seconds:g}s"
    return f"1/{max(1, round(1 / seconds))}"


def _format_aperture(value):
    number = _exif_number(value)
    if number is None:
        return ""
    return f"f/{number:g}"


def _format_focal(value):
    number = _exif_number(value)
    if number is None:
        return ""
    return f"{number:g}mm"


def _format_iso(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    try:
        return f"ISO {int(value)}"
    except (TypeError, ValueError):
        return ""


def _format_bytes(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _clock(value):
    return value.strftime("%I:%M:%S %p").lstrip("0")


def _format_taken(text, file_path):
    if isinstance(text, str) and text.strip():
        cleaned = text.strip()[:19]
        for candidate, fmt in (
            (cleaned, "%Y:%m:%d %H:%M:%S"),
            (cleaned.replace(":", "-", 2), "%Y-%m-%d %H:%M:%S"),
        ):
            try:
                return _clock(datetime.strptime(candidate, fmt))
            except ValueError:
                continue
    try:
        return _clock(datetime.fromtimestamp(os.path.getmtime(file_path)))
    except OSError:
        return ""


def read_jpeg_info(file_path):
    """Compact capture metadata for the shooting-view info strip."""
    name = os.path.basename(file_path)
    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = 0
    taken = _format_taken("", file_path)
    camera = ""
    settings = []
    try:
        with Image.open(file_path) as image:
            exif = image.getexif()
            extra = {}
            if exif:
                try:
                    extra = exif.get_ifd(IFD.Exif) or {}
                except Exception:
                    extra = {}
                make = (exif.get(0x010F) or "").strip()
                model = (exif.get(0x0110) or "").strip()
                camera = model or make
                if make and model and make.lower() not in model.lower():
                    camera = f"{make} {model}"
                taken = _format_taken(
                    extra.get(0x9003) or extra.get(0x9004) or exif.get(0x0132),
                    file_path,
                )
                focal = _format_focal(extra.get(0x920A))
                aperture = _format_aperture(extra.get(0x829D))
                shutter = _format_shutter(extra.get(0x829A))
                iso = _format_iso(extra.get(0x8827) or extra.get(0x8833))
                settings = [part for part in (focal, aperture, shutter, iso) if part]
    except Exception:
        pass
    return {
        "name": name,
        "time": taken,
        "size": _format_bytes(size),
        "camera": camera,
        "settings": "  ·  ".join(settings),
    }


def pil_to_qimage(image: Image.Image) -> QImage:
    rgb = image.convert("RGB")
    width, height = rgb.size
    data = rgb.tobytes("raw", "RGB")
    qimage = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(image))
