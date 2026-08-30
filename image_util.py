"""PIL to Qt image conversion. QImage work can run off the UI thread."""

import os

from PIL import Image
from PySide6.QtGui import QImage, QPixmap

THUMB_SIZE = (512, 384)


def thumb_cache_dir(tether_folder):
    path = os.path.join(tether_folder, ".thumbs")
    os.makedirs(path, exist_ok=True)
    return path


def make_thumbnail(file_path, tether_folder, size=THUMB_SIZE):
    cache = os.path.join(thumb_cache_dir(tether_folder), os.path.basename(file_path) + ".512.jpg")
    try:
        if not os.path.isfile(cache) or os.path.getmtime(file_path) > os.path.getmtime(cache):
            image = Image.open(file_path)
            image.thumbnail(size)
            image.convert("RGB").save(cache, "JPEG", quality=82)
        return Image.open(cache)
    except Exception:
        return Image.new("RGB", size, (31, 35, 42))


def pil_to_qimage(image: Image.Image) -> QImage:
    rgb = image.convert("RGB")
    width, height = rgb.size
    data = rgb.tobytes("raw", "RGB")
    qimage = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(image))
