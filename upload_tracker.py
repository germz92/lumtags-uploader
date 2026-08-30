import os
import threading
import time
from collections import defaultdict

# Cache to avoid repeatedly reading tracker files
_tracker_cache = defaultdict(set)
_cache_timestamps = {}
_cache_lock = threading.Lock()
CACHE_DURATION = 300  # 5 minutes

# File lock for writing
WRITE_LOCK = threading.Lock()

def get_tracker_file(folder):
    """
    Returns the path for the tracker file stored inside the given folder.
    Using a hidden file (prefixed with a dot) helps avoid accidental scanning.
    """
    return os.path.join(folder, ".uploaded_files.txt")

def _is_cache_valid(folder):
    """Check if cache is still valid for the given folder"""
    if folder not in _cache_timestamps:
        return False
    return time.time() - _cache_timestamps[folder] < CACHE_DURATION

def load_uploaded_files(folder):
    """
    Loads and returns a set of absolute file paths that have been uploaded for the given folder.
    Uses caching to improve performance.
    """
    with _cache_lock:
        if _is_cache_valid(folder):
            return _tracker_cache[folder].copy()
    
    tracker_file = get_tracker_file(folder)
    uploaded_files = set()
    
    if os.path.exists(tracker_file):
        try:
            with open(tracker_file, "r", encoding='utf-8') as f:
                uploaded_files = set(line.strip() for line in f if line.strip())
        except (IOError, UnicodeDecodeError) as e:
            print(f"Warning: Could not read tracker file {tracker_file}: {e}")
    
    # Update cache
    with _cache_lock:
        _tracker_cache[folder] = uploaded_files.copy()
        _cache_timestamps[folder] = time.time()
    
    return uploaded_files

def add_uploaded_file(file_path, folder):
    """
    Appends the absolute path of the successfully uploaded file to the tracker file in the given folder.
    Also updates the cache to maintain consistency.
    """
    tracker_file = get_tracker_file(folder)
    file_path = os.path.abspath(file_path)
    
    with WRITE_LOCK:
        try:
            with open(tracker_file, "a", encoding='utf-8') as f:
                f.write(file_path + "\n")
        except IOError as e:
            print(f"Warning: Could not write to tracker file {tracker_file}: {e}")
            return
    
    # Update cache
    with _cache_lock:
        _tracker_cache[folder].add(file_path)
        _cache_timestamps[folder] = time.time()

def cleanup_tracker_file(folder, max_lines=10000):
    """
    Clean up tracker file if it gets too large by removing duplicate entries.
    This should be called periodically to prevent the file from growing indefinitely.
    """
    tracker_file = get_tracker_file(folder)
    
    if not os.path.exists(tracker_file):
        return
    
    try:
        # Count lines first
        with open(tracker_file, "r", encoding='utf-8') as f:
            line_count = sum(1 for _ in f)
        
        if line_count <= max_lines:
            return
        
        # Read all unique entries
        with open(tracker_file, "r", encoding='utf-8') as f:
            unique_files = set(line.strip() for line in f if line.strip())
        
        # Write back only unique entries
        with WRITE_LOCK:
            with open(tracker_file, "w", encoding='utf-8') as f:
                for file_path in sorted(unique_files):
                    f.write(file_path + "\n")
        
        # Update cache
        with _cache_lock:
            _tracker_cache[folder] = unique_files.copy()
            _cache_timestamps[folder] = time.time()
            
        print(f"Cleaned up tracker file {tracker_file}: {line_count} -> {len(unique_files)} lines")
        
    except (IOError, UnicodeDecodeError) as e:
        print(f"Warning: Could not cleanup tracker file {tracker_file}: {e}")

def clear_cache():
    """Clear the tracker cache. Useful for testing or manual cache invalidation."""
    with _cache_lock:
        _tracker_cache.clear()
        _cache_timestamps.clear()
