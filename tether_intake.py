"""
Tether intake: camera host image_ready → upload immediately.

Files from the SDK/simulator are complete JPEGs. There is no stability wait.
"""

import os
from concurrent.futures import ThreadPoolExecutor

from s3_upload import upload_image
from status_events import STATUS_PENDING, STATUS_UPLOADED, put_status
from upload_tracker import load_uploaded_files

IMAGE_EXTENSIONS = (".jpg", ".jpeg")


class TetherIntake:
    def __init__(self, session_id, s3_folder, tether_folder, log_queue, max_workers=10):
        self.session_id = session_id
        self.s3_folder = s3_folder
        self.tether_folder = os.path.abspath(tether_folder)
        self.log_queue = log_queue
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tether_upload")
        self._seen = set()

    def on_image_ready(self, file_path):
        file_path = os.path.abspath(file_path)
        if file_path in self._seen:
            return
        if not os.path.isfile(file_path):
            if self.log_queue:
                self.log_queue.put(f"image_ready missing file: {file_path}")
            return
        self._seen.add(file_path)
        uploaded = load_uploaded_files(self.tether_folder)
        if file_path in uploaded:
            put_status(self.log_queue, file_path, self.session_id, STATUS_UPLOADED)
            return
        put_status(self.log_queue, file_path, self.session_id, STATUS_PENDING)
        if self.log_queue:
            self.log_queue.put(f"Tether JPEG ready, uploading immediately: {file_path}")
        self._pool.submit(
            upload_image,
            file_path,
            self.s3_folder,
            self.tether_folder,
            self.log_queue,
            self.session_id,
        )

    def import_existing(self):
        """Show tracker hits as uploaded; queue anything not yet uploaded."""
        found = []
        uploaded = load_uploaded_files(self.tether_folder)
        try:
            names = os.listdir(self.tether_folder)
        except OSError as exc:
            if self.log_queue:
                self.log_queue.put(f"Could not scan tether folder: {exc}")
            return found
        for name in names:
            if name.startswith("."):
                continue
            if not name.lower().endswith(IMAGE_EXTENSIONS):
                continue
            path = os.path.abspath(os.path.join(self.tether_folder, name))
            found.append(path)
            if path in uploaded:
                self._seen.add(path)
                put_status(self.log_queue, path, self.session_id, STATUS_UPLOADED)
            else:
                self.on_image_ready(path)
        return found

    def shutdown(self, wait=False):
        self._pool.shutdown(wait=wait)
