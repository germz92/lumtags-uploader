import os
import time
from dataclasses import dataclass
from typing import Optional

KIND = "upload_status"

STATUS_PENDING = "pending"
STATUS_UPLOADING = "uploading"
STATUS_UPLOADED = "uploaded"
STATUS_FAILED = "failed"
STATUS_DISMISSED = "dismissed"

PENDING_STATUSES = (STATUS_PENDING, STATUS_UPLOADING)


@dataclass
class StatusEvent:
    kind: str = KIND
    file_path: str = ""
    filename: str = ""
    monitor_id: str = ""
    status: str = STATUS_PENDING
    reason: Optional[str] = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.file_path:
            self.file_path = os.path.abspath(self.file_path)
        if not self.filename and self.file_path:
            self.filename = os.path.basename(self.file_path)
        if not self.timestamp:
            self.timestamp = time.time()


def is_status_event(message):
    return isinstance(message, StatusEvent)


def put_status(log_queue, file_path, monitor_id, status, reason=None):
    """Push a structured queue event. No-op if there is no queue."""
    if log_queue is None:
        return
    log_queue.put(StatusEvent(
        file_path=file_path or "",
        monitor_id=monitor_id or "",
        status=status,
        reason=reason,
    ))
