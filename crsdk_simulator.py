"""
Camera host process that speaks the same JSON-line protocol as crsdk_host.

Used when the native Sony CRSDK binary is not built. A simulated Alpha body
writes complete JPEGs into the tether folder so the wizard and upload path
can be exercised without hardware.
"""

import os
import sys
import threading

from camera_protocol import (
    BACKEND_SIMULATOR,
    CMD_CONNECT,
    CMD_DISCONNECT,
    CMD_ENUMERATE,
    CMD_PING,
    CMD_SHUTDOWN,
    CMD_SIMULATE_DISCONNECT,
    CMD_SIMULATE_SHOT,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_HELLO,
    EVENT_IMAGE_READY,
    EVENT_RECONNECTING,
    HINT_USB,
    PROTOCOL_VERSION,
    decode_line,
    encode,
    event,
    reply,
)

SIM_DEVICE = {
    "id": "sim-ilce-7m4",
    "model": "ILCE-7M4",
    "name": "Sony ILCE-7M4 (simulator)",
    "serial": "SIM000000",
}

_lock = threading.Lock()
_connected = False
_save_dir = ""
_shot_index = 0
_stop = threading.Event()


def emit(obj):
    sys.stdout.write(encode(obj))
    sys.stdout.flush()


def write_jpeg(path):
    from PIL import Image, ImageDraw

    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Noise so the file stays above the 500KB gallery floor (solid color compresses too small).
    img = Image.frombytes("RGB", (2400, 1600), os.urandom(2400 * 1600 * 3))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 40, 2360, 1560), outline=(230, 168, 23), width=8)
    draw.rectangle((60, 60, 900, 200), fill=(28, 30, 38))
    draw.text((80, 80), os.path.basename(path), fill=(244, 244, 247))
    img.save(path, "JPEG", quality=88)


def handle(msg):
    global _connected, _save_dir, _shot_index

    cmd = msg.get("cmd")
    req_id = msg.get("id")

    if cmd == CMD_PING:
        emit(reply(req_id, True, {"backend": BACKEND_SIMULATOR}))
        return

    if cmd == CMD_ENUMERATE:
        emit(reply(req_id, True, {"cameras": [SIM_DEVICE]}))
        return

    if cmd == CMD_CONNECT:
        device_id = msg.get("device_id")
        save_dir = os.path.abspath(msg.get("save_dir") or "")
        if device_id and device_id != SIM_DEVICE["id"]:
            emit(reply(req_id, False, error="Unknown camera.", hint=HINT_USB))
            return
        if not save_dir:
            emit(reply(req_id, False, error="Missing tether folder."))
            return
        os.makedirs(save_dir, exist_ok=True)
        with _lock:
            _save_dir = save_dir
            _connected = True
        emit(reply(req_id, True, {"camera": SIM_DEVICE, "save_dir": save_dir}))
        emit(event(EVENT_CONNECTED, **SIM_DEVICE, save_dir=save_dir))
        return

    if cmd == CMD_DISCONNECT:
        with _lock:
            was = _connected
            _connected = False
        emit(reply(req_id, True))
        if was:
            emit(event(EVENT_DISCONNECTED, reason="Host disconnect requested"))
        return

    if cmd == CMD_SIMULATE_SHOT:
        with _lock:
            if not _connected:
                emit(reply(req_id, False, error="Camera is not connected."))
                return
            _shot_index += 1
            filename = f"DSC{ _shot_index:05d}.JPG"
            path = os.path.join(_save_dir, filename)
        try:
            write_jpeg(path)
        except Exception as exc:
            emit(reply(req_id, False, error=str(exc)))
            emit(event(EVENT_ERROR, message=str(exc)))
            return
        emit(reply(req_id, True, {"path": path}))
        emit(event(EVENT_IMAGE_READY, path=path, filename=filename))
        return

    if cmd == CMD_SIMULATE_DISCONNECT:
        with _lock:
            _connected = False
        emit(reply(req_id, True))
        emit(event(EVENT_DISCONNECTED, reason="USB lost (simulated)"))
        return

    if cmd == CMD_SHUTDOWN:
        emit(reply(req_id, True))
        _stop.set()
        return

    emit(reply(req_id, False, error=f"Unknown command: {cmd}"))


def stdin_loop():
    for raw in sys.stdin:
        if _stop.is_set():
            break
        try:
            msg = decode_line(raw)
        except Exception as exc:
            emit(event(EVENT_ERROR, message=f"Bad command JSON: {exc}"))
            continue
        if not msg:
            continue
        try:
            handle(msg)
        except Exception as exc:
            emit(event(EVENT_ERROR, message=str(exc)))
        if _stop.is_set():
            break


def main():
    emit({
        "type": EVENT_HELLO,
        "backend": BACKEND_SIMULATOR,
        "version": PROTOCOL_VERSION,
    })
    stdin_loop()


if __name__ == "__main__":
    main()
