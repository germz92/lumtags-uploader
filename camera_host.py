"""
Python client for the camera host process.

Prefers a built native `crsdk_host` (Sony CRSDK). Falls back to
`crsdk_simulator.py`, which speaks the same JSON-line protocol.
Reconnect retries live here so both backends get the same backoff.
"""

import os
import sys
import threading
import queue
import time
import subprocess

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
    EVENT_REPLY,
    HINT_USB,
    command,
    decode_line,
    encode,
)
from platform_support import (
    IS_FROZEN,
    kill_stale_camera_hosts,
    native_host_candidates,
    popen_kwargs,
    resource_root,
)

CONNECT_ATTEMPTS = 4
CONNECT_TIMEOUT = 28

RECONNECT_BACKOFF = (2, 3, 5, 10)


def find_native_host():
    for path in native_host_candidates():
        if path and os.path.isfile(path):
            return path
    return None


def _simulator_args():
    if IS_FROZEN:
        return [sys.executable, "--host-simulator"]
    return [sys.executable, "-u", os.path.join(resource_root(), "crsdk_simulator.py")]


class CameraHost:
    def __init__(self, event_queue=None):
        self.event_queue = event_queue if event_queue is not None else queue.Queue()
        self._proc = None
        self._reader = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending = {}
        self.backend = BACKEND_SIMULATOR
        self.using_simulator = True
        self.connected = False
        self.camera = None
        self.save_dir = ""
        self._want_connected = False
        self._device_id = ""
        self._reconnect_thread = None
        self._stop = threading.Event()

    def start(self):
        self._stop.clear()
        kill_stale_camera_hosts()
        native = find_native_host()
        if native:
            args = [native]
            self.using_simulator = False
            host_cwd = os.path.dirname(os.path.abspath(native))
        else:
            args = _simulator_args()
            self.using_simulator = True
            host_cwd = resource_root()

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=host_cwd,
            **popen_kwargs(),
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        err_thread = threading.Thread(target=self._read_stderr, daemon=True)
        err_thread.start()

        hello = self._wait_for(lambda e: e.get("type") == EVENT_HELLO, timeout=8)
        if hello:
            self.backend = hello.get("backend", self.backend)
            self.using_simulator = self.backend == BACKEND_SIMULATOR
        return hello

    def is_running(self):
        return bool(self._proc and self._proc.poll() is None)

    def close(self):
        self._stop.set()
        self._want_connected = False
        try:
            self._send(CMD_SHUTDOWN, wait=False)
        except Exception:
            pass
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self.connected = False

    def restart(self):
        self.close()
        self._stop = threading.Event()
        self._pending = {}
        return self.start()

    def enumerate(self, timeout=20):
        reply = self._send(CMD_ENUMERATE, timeout=timeout)
        if not reply or not reply.get("ok"):
            error = (reply or {}).get("error") or "Could not list cameras."
            hint = (reply or {}).get("hint") or HINT_USB
            raise CameraHostError(error, hint=hint)
        return list((reply.get("data") or {}).get("cameras") or [])

    def connect(self, device_id, save_dir, timeout=CONNECT_TIMEOUT, on_attempt=None):
        self._device_id = device_id
        self.save_dir = os.path.abspath(save_dir)
        self._want_connected = True
        last_error = None
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            if on_attempt:
                on_attempt(attempt, CONNECT_ATTEMPTS)
            try:
                return self._connect_once(device_id, self.save_dir, timeout)
            except CameraHostError as exc:
                last_error = exc
                if attempt >= CONNECT_ATTEMPTS or self._stop.is_set():
                    break
                time.sleep(1.2)
                if attempt >= 2:
                    try:
                        self.restart()
                    except Exception:
                        pass
        raise last_error or CameraHostError("Could not connect to the camera.")

    def _connect_once(self, device_id, save_dir, timeout):
        reply = self._send(
            CMD_CONNECT,
            timeout=timeout,
            device_id=device_id or "",
            save_dir=save_dir,
        )
        if not reply or not reply.get("ok"):
            error = (reply or {}).get("error") or "Could not connect to the camera."
            hint = (reply or {}).get("hint") or HINT_USB
            raise CameraHostError(error, hint=hint)
        data = reply.get("data") or {}
        self.camera = data.get("camera") or {"id": device_id or "0"}
        self.connected = True
        return data

    def disconnect(self):
        self._want_connected = False
        self._send(CMD_DISCONNECT, wait=False)
        self.connected = False

    def simulate_shot(self):
        reply = self._send(CMD_SIMULATE_SHOT, timeout=15)
        if not reply or not reply.get("ok"):
            raise CameraHostError((reply or {}).get("error") or "Simulate shot failed.")
        return (reply.get("data") or {}).get("path")

    def simulate_disconnect(self):
        self._send(CMD_SIMULATE_DISCONNECT, wait=False)

    def _send(self, cmd, timeout=8, wait=True, **fields):
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            waiter = queue.Queue()
            if wait:
                self._pending[req_id] = waiter
            line = encode(command(cmd, req_id, **fields))
            if not self._proc or not self._proc.stdin:
                raise CameraHostError("Camera host is not running.")
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        if not wait:
            return None
        try:
            return waiter.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(req_id, None)
            raise CameraHostError(f"Camera host timed out on {cmd}.")

    def _read_stdout(self):
        if not self._proc or not self._proc.stdout:
            return
        for raw in self._proc.stdout:
            if self._stop.is_set():
                break
            try:
                msg = decode_line(raw)
            except Exception:
                continue
            if not msg:
                continue
            self._dispatch(msg)

    def _read_stderr(self):
        if not self._proc or not self._proc.stderr:
            return
        for raw in self._proc.stderr:
            text = raw.strip()
            if text:
                self.event_queue.put({
                    "type": "event",
                    "name": EVENT_ERROR,
                    "message": text,
                })

    def _dispatch(self, msg):
        if msg.get("type") == EVENT_HELLO:
            self.backend = msg.get("backend", self.backend)
            self.using_simulator = self.backend == BACKEND_SIMULATOR
            self.event_queue.put(msg)
            return

        if msg.get("type") == EVENT_REPLY:
            req_id = msg.get("id")
            with self._lock:
                waiter = self._pending.pop(req_id, None)
            if waiter:
                waiter.put(msg)
            return

        if msg.get("type") == "event":
            name = msg.get("name")
            if name == EVENT_CONNECTED:
                previous = self.camera or {}
                self.connected = True
                self.camera = {
                    "id": msg.get("id") or previous.get("id"),
                    "model": msg.get("model") or previous.get("model"),
                    "name": msg.get("name") or previous.get("name"),
                    "serial": msg.get("serial") or previous.get("serial"),
                }
            elif name == EVENT_RECONNECTING:
                self.connected = False
            elif name == EVENT_DISCONNECTED:
                self.connected = False
                if self._want_connected and not self._stop.is_set():
                    self._start_reconnect()
            elif name == EVENT_IMAGE_READY and not self.connected:
                self.connected = True
                restored = dict(self.camera or {})
                restored.update({"type": "event", "name": EVENT_CONNECTED})
                self.event_queue.put(restored)
            self.event_queue.put(msg)

    def _start_reconnect(self):
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        attempt = 0
        while self._want_connected and not self._stop.is_set():
            if self.connected:
                return
            delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
            self.event_queue.put({
                "type": "event",
                "name": EVENT_RECONNECTING,
                "attempt": attempt + 1,
                "delay": delay,
            })
            time.sleep(delay)
            if not self._want_connected or self._stop.is_set():
                return
            try:
                cameras = self.enumerate(timeout=8)
                if not cameras:
                    attempt += 1
                    continue
                self.connect(self._device_id or cameras[0].get("id") or "", self.save_dir, timeout=25)
                return
            except Exception:
                attempt += 1

    def _wait_for(self, predicate, timeout=8):
        deadline = time.time() + timeout
        leftover = []
        try:
            while time.time() < deadline:
                try:
                    msg = self.event_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if predicate(msg):
                    return msg
                leftover.append(msg)
        finally:
            for msg in leftover:
                self.event_queue.put(msg)
        return None


class CameraHostError(Exception):
    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint or HINT_USB
