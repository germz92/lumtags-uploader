"""Load and cache event cover thumbnails from S3 keys or HTTPS URLs."""

import hashlib
import os
import urllib.request
from io import BytesIO

from PIL import Image

from image_util import pil_to_qimage
from platform_support import app_support_dir

THUMB_SIZE = (320, 200)
LOGO_BG = (31, 35, 42)


def _cache_path(image_ref, contain):
    folder = os.path.join(app_support_dir(), "event_thumbs")
    os.makedirs(folder, exist_ok=True)
    token = f"{image_ref[0]}:{image_ref[1]}:{int(contain)}"
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]
    return os.path.join(folder, f"{digest}.jpg")


def _download_bytes(image_ref):
    kind, value = image_ref
    if kind == "url":
        request = urllib.request.Request(value, headers={"User-Agent": "LumTagsUploader"})
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.read()
    if kind == "s3":
        from s3_upload import S3_BUCKET, s3_client

        if s3_client is None or not S3_BUCKET:
            return None
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=value)
        return response["Body"].read()
    return None


def _fit_logo(image, size):
    work = image.convert("RGBA") if image.mode in ("RGBA", "LA", "P") else image.convert("RGB")
    work.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, LOGO_BG)
    x = (size[0] - work.width) // 2
    y = (size[1] - work.height) // 2
    if work.mode == "RGBA":
        canvas.paste(work, (x, y), work)
    else:
        canvas.paste(work.convert("RGB"), (x, y))
    return canvas


def load_event_thumb(event_id, image_ref, size=THUMB_SIZE, contain=False):
    if not image_ref:
        return None
    cache = _cache_path(image_ref, contain)
    try:
        if os.path.isfile(cache):
            image = Image.open(cache)
        else:
            data = _download_bytes(image_ref)
            if not data:
                return None
            image = Image.open(BytesIO(data))
            if contain:
                image = _fit_logo(image, size)
            else:
                image = image.convert("RGB")
                image.thumbnail(size, Image.Resampling.LANCZOS)
            image.save(cache, "JPEG", quality=82)
        return pil_to_qimage(image)
    except Exception:
        return None
