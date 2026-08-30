import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os
from s3_upload import upload_image
from concurrent.futures import ThreadPoolExecutor
import atexit
from collections import defaultdict
from status_events import STATUS_FAILED, STATUS_PENDING, put_status

# Global variable for the wait delay after the file stops changing before attempting upload.
WAIT_BEFORE_UPLOAD = 10  # seconds

# Dictionary to control active monitors by a unique monitor_id.
monitor_control = {}
# Dictionary to store observer instances for proper cleanup
monitor_observers = {}
# Track pending uploads to avoid duplicates
pending_uploads = defaultdict(set)
pending_lock = threading.Lock()

# Global thread pool to limit concurrent upload threads
upload_thread_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="upload_worker")

def cleanup_all():
    """Cleanup function for application shutdown"""
    # Stop all monitors
    for monitor_id in list(monitor_control.keys()):
        stop_monitoring(monitor_id)
    # Shutdown thread pool
    upload_thread_pool.shutdown(wait=True)

# Register cleanup on exit
atexit.register(cleanup_all)

def delayed_upload(file_path, s3_folder, tracker_folder, log_queue, monitor_id):
    """
    Waits until the file is no longer being modified for WAIT_BEFORE_UPLOAD seconds,
    then calls upload_image.
    """
    # Remove from pending set when done
    def cleanup_pending():
        with pending_lock:
            pending_uploads[monitor_id].discard(file_path)
    
    try:
        last_size = -1
        stable_since = None

        while True:
            # Check if monitor is still active
            if not monitor_control.get(monitor_id, False):
                log_queue.put(f"Monitor {monitor_id} stopped. Aborting upload for {file_path}.")
                put_status(log_queue, file_path, monitor_id, STATUS_FAILED, reason="Monitor stopped")
                cleanup_pending()
                return
                
            try:
                current_size = os.path.getsize(file_path)
            except OSError:
                log_queue.put(f"File {file_path} is no longer accessible. Aborting upload.")
                put_status(log_queue, file_path, monitor_id, STATUS_FAILED, reason="File no longer accessible")
                cleanup_pending()
                return
            now = time.time()

            if current_size != last_size:
                last_size = current_size
                stable_since = now
                log_queue.put(f"Detected change in {file_path}, resetting wait timer.")
            elif stable_since and (now - stable_since) >= WAIT_BEFORE_UPLOAD:
                break

            time.sleep(1)

        log_queue.put(f"File {file_path} has been stable for {WAIT_BEFORE_UPLOAD} seconds. Uploading now.")
        upload_image(file_path, s3_folder, tracker_folder, log_queue, monitor_id=monitor_id)
    finally:
        cleanup_pending()

class ImageEventHandler(FileSystemEventHandler):
    def __init__(self, s3_folder, tracker_folder, monitor_id, log_queue):
        self.s3_folder = s3_folder
        self.tracker_folder = tracker_folder
        self.monitor_id = monitor_id
        self.log_queue = log_queue
        super().__init__()

    def on_created(self, event):
        # Only process file creation events (ignore directories)
        if event.is_directory:
            return
        # Check file extension for common image types.
        if event.src_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
            # Check for duplicates
            with pending_lock:
                if event.src_path in pending_uploads[self.monitor_id]:
                    return
                pending_uploads[self.monitor_id].add(event.src_path)
            
            self.log_queue.put(f"Detected new image: {event.src_path}. Waiting until file is stable for {WAIT_BEFORE_UPLOAD} seconds before upload.")
            put_status(self.log_queue, event.src_path, self.monitor_id, STATUS_PENDING)
            # Use thread pool instead of creating unlimited threads
            upload_thread_pool.submit(
                delayed_upload,
                event.src_path, self.s3_folder, self.tracker_folder, self.log_queue, self.monitor_id
            )


def initial_directory_scan(folder, s3_folder, tracker_folder, monitor_id, log_queue):
    """
    One-time scan of the directory for existing image files at startup.
    This helps pick up any files that might have been dropped in via FTP
    before monitoring started.
    """
    folder = os.path.abspath(folder)
    try:
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            if os.path.isfile(file_path) and filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                # Check for duplicates
                with pending_lock:
                    if file_path in pending_uploads[monitor_id]:
                        continue
                    pending_uploads[monitor_id].add(file_path)
                
                log_queue.put(f"Found existing file: {file_path}. Waiting until file is stable for {WAIT_BEFORE_UPLOAD} seconds before upload.")
                put_status(log_queue, file_path, monitor_id, STATUS_PENDING)
                upload_thread_pool.submit(
                    delayed_upload,
                    file_path, s3_folder, tracker_folder, log_queue, monitor_id
                )
    except OSError as e:
        log_queue.put(f"Error scanning directory {folder}: {e}")


def start_monitoring(folder, s3_folder, monitor_id, log_queue):
    """
    Monitors the specified folder for new image files and uploads them to S3.
    Uses watchdog for real-time monitoring and performs an initial scan for existing files.
    Uses the local folder as the tracker folder.
    """
    monitor_control[monitor_id] = True

    # Perform initial scan for existing files
    initial_directory_scan(folder, s3_folder, folder, monitor_id, log_queue)

    # Start watchdog observer for real-time events.
    event_handler = ImageEventHandler(s3_folder, folder, monitor_id, log_queue)
    observer = Observer()
    observer.schedule(event_handler, folder, recursive=False)
    observer.start()
    
    # Store observer for cleanup
    monitor_observers[monitor_id] = observer

    try:
        while monitor_control.get(monitor_id, False):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        # Clean up
        if monitor_id in monitor_observers:
            del monitor_observers[monitor_id]
        with pending_lock:
            if monitor_id in pending_uploads:
                del pending_uploads[monitor_id]
        log_queue.put(f"Stopped monitoring folder: {folder} for monitor_id: {monitor_id}")

def stop_monitoring(monitor_id):
    """
    Signals the monitoring loop for the given monitor_id to stop.
    """
    monitor_control[monitor_id] = False
    
    # Stop observer if it exists
    if monitor_id in monitor_observers:
        observer = monitor_observers[monitor_id]
        if observer.is_alive():
            observer.stop()

def cleanup_thread_pool():
    """
    Cleanup function to shutdown the thread pool gracefully.
    Call this when your application is shutting down.
    """
    upload_thread_pool.shutdown(wait=True)