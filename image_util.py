"""PIL to Qt image conversion. QImage work can run off the UI thread."""

import os
import time

from PIL import Image, ImageOps
from PySide6.QtGui import QImage, QPixmap

THUMB_SIZE = (512, 384)
THUMB_RETRIES = 8
THUMB_RETRY_WAIT = 0.15


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


def thumb_cache_dir(tether_folder):
    path = os.path.join(tether_folder, ".thumbs")
    os.makedirs(path, exist_ok=True)
    return path


def make_thumbnail(file_path, tether_folder, size=THUMB_SIZE):
    cache = os.path.join(thumb_cache_dir(tether_folder), os.path.basename(file_path) + ".512.jpg")
    for attempt in range(THUMB_RETRIES):
        try:
            if not jpeg_is_complete(file_path):
                time.sleep(THUMB_RETRY_WAIT)
                continue
            if not os.path.isfile(cache) or os.path.getmtime(file_path) > os.path.getmtime(cache):
                image = Image.open(file_path)
                image = ImageOps.exif_transpose(image)
                image.load()
                image.thumbnail(size, Image.Resampling.LANCZOS)
                image.convert("RGB").save(cache, "JPEG", quality=82)
            return Image.open(cache)
        except Exception:
            time.sleep(THUMB_RETRY_WAIT)
    return Image.new("RGB", size, (31, 35, 42))


def pil_to_qimage(image: Image.Image) -> QImage:
    rgb = image.convert("RGB")
    width, height = rgb.size
    data = rgb.tobytes("raw", "RGB")
    qimage = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(image))
