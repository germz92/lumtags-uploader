import boto3
import os
import time
import csv
from dotenv import load_dotenv
from platform_support import executable_dir
from upload_tracker import load_uploaded_files, add_uploaded_file
from botocore.exceptions import ClientError, NoCredentialsError
from status_events import (
    STATUS_DISMISSED,
    STATUS_FAILED,
    STATUS_UPLOADED,
    STATUS_UPLOADING,
    put_status,
)
import random

load_dotenv()
load_dotenv(os.path.join(executable_dir(), ".env"))

# AWS configuration from environment variables
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET")

# Initialize the S3 client using boto3 with retry configuration
try:
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
        config=boto3.session.Config(
            retries={'max_attempts': 3, 'mode': 'adaptive'},
            connect_timeout=10,
            read_timeout=30
        )
    )
except NoCredentialsError:
    s3_client = None
    print("Warning: AWS credentials not found. S3 uploads will fail.")

def check_jpeg_corruption(file_path):
    """
    Check if a JPEG file is corrupted by validating its end marker.
    Returns True if file is valid, False if corrupted, None if not a JPEG.
    """
    if not file_path.lower().endswith(('.jpg', '.jpeg', '.JPG', '.JPEG')):
        return None  # Not a JPEG file
    
    if not os.path.exists(file_path):
        return False  # File doesn't exist
    
    try:
        with open(file_path, 'rb') as f:
            f.seek(-2, 2)  # Go to last 2 bytes
            end_marker = f.read(2)
            return end_marker == b'\xff\xd9'  # JPEG end marker
    except Exception:
        return False  # Error reading file

def record_skipped_file(file_path, reason):
    """
    Appends the skipped filename and reason to 'skipped_files.csv' located
    in the same directory as the file. Writes a header row if the file is new.
    """
    folder = os.path.dirname(file_path)
    record_path = os.path.join(folder, "skipped_files.csv")
    filename = os.path.basename(file_path)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(record_path)
    try:
        with open(record_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "filename", "reason"])
            writer.writerow([timestamp, filename, reason])
    except OSError as e:
        print(f"Warning: could not write to {record_path}: {e}")


def upload_image(file_path, s3_folder, tracker_folder, log_queue=None, monitor_id=None):
    """
    Uploads an image file to the S3 bucket under the given s3_folder only if:
      - The file has not already been uploaded (determined by the tracker file in tracker_folder).
      - The file size is greater than 500KB.
      - The file has had time to be fully written.
      - JPEG files are not corrupted.
    """
    file_path = os.path.abspath(file_path)
    
    def log_msg(message):
        if log_queue:
            log_queue.put(message)
        else:
            print(message)

    def fail(log_text, reason):
        log_msg(log_text)
        record_skipped_file(file_path, reason)
        put_status(log_queue, file_path, monitor_id, STATUS_FAILED, reason=reason)
    
    # Check if S3 client is available
    if s3_client is None:
        fail(
            f"Skipped {file_path}: S3 client not available (missing credentials)",
            "S3 client not available (missing credentials)",
        )
        return
    
    # Duplicate check using local tracker.
    uploaded_files = load_uploaded_files(tracker_folder)
    if file_path in uploaded_files:
        log_msg(f"Skipped {file_path}: already uploaded (tracker in {tracker_folder}).")
        put_status(log_queue, file_path, monitor_id, STATUS_DISMISSED)
        return

    # Check JPEG corruption
    jpeg_check = check_jpeg_corruption(file_path)
    if jpeg_check is False:
        fail(
            f"Skipped {file_path}: JPEG file is corrupted (missing end marker)",
            "JPEG file corrupted (missing end marker)",
        )
        return
    elif jpeg_check is True:
        log_msg(f"JPEG validation passed for {file_path}")

    # Wait for the file to be fully written (if its size is initially 0)
    max_attempts = 5
    wait_time = 1  # seconds
    attempts = 0
    
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        fail(f"Skipped {file_path}: File no longer accessible", "File no longer accessible")
        return
        
    while file_size == 0 and attempts < max_attempts:
        log_msg(f"Waiting for file to finish writing: {file_path}")
        time.sleep(wait_time)
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            fail(f"Skipped {file_path}: File no longer accessible", "File no longer accessible")
            return
        attempts += 1

    # Check file size threshold (500KB)
    if file_size < 500 * 1024:
        reason = f"File size {file_size} bytes is below the 500 KB threshold"
        fail(
            f"Skipped {file_path}: File size {file_size} bytes is less than 500KB threshold.",
            reason,
        )
        return

    filename = os.path.basename(file_path)
    s3_key = f"{s3_folder}/{filename}"
    
    # Implement retry logic with exponential backoff
    max_retries = 3
    retry_delay = 1

    put_status(log_queue, file_path, monitor_id, STATUS_UPLOADING)
    
    for attempt in range(max_retries):
        try:
            s3_client.upload_file(file_path, S3_BUCKET, s3_key)
            log_msg(f"Uploaded {file_path} to s3://{S3_BUCKET}/{s3_key}")
            # Mark this file as successfully uploaded.
            add_uploaded_file(file_path, tracker_folder)
            put_status(log_queue, file_path, monitor_id, STATUS_UPLOADED)
            return
        except (ClientError, Exception) as e:
            if attempt < max_retries - 1:
                # Add jitter to prevent thundering herd
                jitter = random.uniform(0.1, 0.5)
                sleep_time = retry_delay * (2 ** attempt) + jitter
                log_msg(f"Upload attempt {attempt + 1} failed for {file_path}: {e}. Retrying in {sleep_time:.1f}s")
                time.sleep(sleep_time)
            else:
                reason = f"Upload failed after {max_retries} attempts: {e}"
                log_msg(f"Error uploading {file_path} after {max_retries} attempts: {e}")
                record_skipped_file(file_path, reason)
                put_status(log_queue, file_path, monitor_id, STATUS_FAILED, reason=reason)

