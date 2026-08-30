"""Write assets/app_icon.ico with the sizes Windows uses on the taskbar."""

import os
import struct
from io import BytesIO

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG_PATH = os.path.join(ROOT, "assets", "app_icon.png")
ICO_PATH = os.path.join(ROOT, "assets", "app_icon.ico")
SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(image):
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def main():
    src = Image.open(PNG_PATH).convert("RGBA")
    entries = []
    for size in SIZES:
        resized = src.resize((size, size), Image.Resampling.LANCZOS)
        entries.append((size, png_bytes(resized)))

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    payload = b""
    for size, data in entries:
        stored = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        payload += data

    with open(ICO_PATH, "wb") as handle:
        handle.write(header + directory + payload)

    print(f"Wrote {ICO_PATH} ({os.path.getsize(ICO_PATH)} bytes, {len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
