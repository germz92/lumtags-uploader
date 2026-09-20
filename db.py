from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
import os
import time
from dotenv import load_dotenv
import atexit
import threading
from logger import get_logger
from platform_support import app_support_dir, executable_dir

logger = get_logger("db")
load_dotenv()
load_dotenv(os.path.join(executable_dir(), ".env"))
load_dotenv(os.path.join(app_support_dir(), ".env"))

MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "test")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "events")

_client = None
_client_lock = threading.Lock()


def get_client():
    """Get or create MongoDB client. First real query does server selection."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MongoClient(
                    MONGO_URI,
                    serverSelectionTimeoutMS=8000,
                    connectTimeoutMS=8000,
                    socketTimeoutMS=15000,
                    maxPoolSize=10,
                    minPoolSize=0,
                    # Event documents are mostly repetitive text, so they shrink
                    # a lot on the wire. zlib ships with Python; the faster
                    # codecs would add a dependency.
                    compressors="zlib",
                    zlibCompressionLevel=6,
                )
    return _client


def close_connection():
    global _client
    with _client_lock:
        if _client:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


atexit.register(close_connection)


def _with_retry(label, work):
    last = None
    for attempt in range(2):
        try:
            return work()
        except (ConnectionFailure, ServerSelectionTimeoutError, Exception) as exc:
            last = exc
            logger.error(f"{label} failed (try {attempt + 1}): {exc}")
            close_connection()
            if attempt == 0:
                time.sleep(0.4)
    raise last


# Each collection embeds an "images" array carrying face-recognition embeddings.
# That is the entire weight of the events collection — hundreds of megabytes —
# and the wizard only reads collection_name and collection_folder. Leave the
# rest of the document intact so parsing keeps working if it grows a field.
EVENT_FIELDS_EXCLUDED = {"eventCollections.images": 0}


def get_events():
    def read():
        return list(get_client()[DATABASE_NAME][COLLECTION_NAME].find({}, EVENT_FIELDS_EXCLUDED))

    return _with_retry("Events", read)


def get_client_logos():
    def read():
        logos = {}
        for doc in get_client()[DATABASE_NAME]["clients"].find({}, {"clientLogo": 1}):
            url = doc.get("clientLogo")
            if url:
                logos[str(doc["_id"])] = url
        return logos

    try:
        return _with_retry("Client logos", read)
    except Exception:
        return {}
