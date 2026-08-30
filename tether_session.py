import json
import os
import re
from datetime import datetime

from platform_support import app_support_dir

SESSION_FILENAME = "session.json"


def session_file(tether_folder):
    return os.path.join(tether_folder, SESSION_FILENAME)


def last_pointer_path():
    return os.path.join(app_support_dir(), "last_session.json")


def write_session(tether_folder, payload):
    data = dict(payload)
    data["tether_folder"] = os.path.abspath(tether_folder)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(tether_folder, exist_ok=True)
    path = session_file(tether_folder)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    with open(last_pointer_path(), "w", encoding="utf-8") as handle:
        json.dump({"tether_folder": os.path.abspath(tether_folder)}, handle)
    return data


def read_session(tether_folder):
    path = session_file(tether_folder)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def load_last_session():
    pointer = last_pointer_path()
    if not os.path.isfile(pointer):
        return None
    try:
        with open(pointer, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    folder = data.get("tether_folder")
    if not folder or not os.path.isdir(folder):
        return None
    return read_session(folder)


def sanitize_folder_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*]', "", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or "Photos"


def suggested_folder_name(event, collection):
    parts = []
    if event and event.name:
        parts.append(event.name)
    if collection and collection.collection_name:
        parts.append(collection.collection_name)
    if event and getattr(event, "event_date", None):
        parts.append(event.event_date.strftime("%Y-%m-%d"))
    else:
        parts.append(datetime.now().strftime("%Y-%m-%d"))
    return sanitize_folder_name(" ".join(parts))


def create_tether_folder(parent_path, name):
    parent_path = os.path.abspath(parent_path)
    name = (name or "").strip()
    if not parent_path or not os.path.isdir(parent_path):
        raise ValueError("Choose an existing folder on this computer.")
    if not name:
        raise ValueError("Enter a folder name.")
    if any(sep in name for sep in ("/", "\\")):
        raise ValueError("Folder name cannot contain slashes.")
    folder = os.path.join(parent_path, name)
    os.makedirs(folder, exist_ok=True)
    return folder
