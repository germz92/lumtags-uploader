"""JSON-line protocol between Python and the camera host process."""

import json

from platform_support import usb_hint

PROTOCOL_VERSION = "1.0"

CMD_PING = "ping"
CMD_ENUMERATE = "enumerate"
CMD_CONNECT = "connect"
CMD_DISCONNECT = "disconnect"
CMD_SHUTDOWN = "shutdown"
CMD_SIMULATE_SHOT = "simulate_shot"
CMD_SIMULATE_DISCONNECT = "simulate_disconnect"

EVENT_HELLO = "hello"
EVENT_REPLY = "reply"
EVENT_CONNECTED = "connected"
EVENT_DISCONNECTED = "disconnected"
EVENT_RECONNECTING = "reconnecting"
EVENT_IMAGE_READY = "image_ready"
EVENT_CAMERA_FOUND = "camera_found"
EVENT_ERROR = "error"

BACKEND_CRSDK = "crsdk"
BACKEND_SIMULATOR = "simulator"

HINT_USB = usb_hint()


def encode(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


def decode_line(line):
    line = (line or "").strip()
    if not line:
        return None
    return json.loads(line)


def command(cmd, req_id, **fields):
    payload = {"cmd": cmd, "id": req_id}
    payload.update(fields)
    return payload


def reply(req_id, ok, data=None, error=None, hint=None):
    payload = {"type": EVENT_REPLY, "id": req_id, "ok": bool(ok)}
    if data is not None:
        payload["data"] = data
    if error:
        payload["error"] = error
    if hint:
        payload["hint"] = hint
    return payload


def event(name, **fields):
    payload = {"type": "event", "name": name}
    payload.update(fields)
    return payload
