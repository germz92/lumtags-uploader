from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
import os
from dotenv import load_dotenv
import atexit
import threading
from logger import get_logger
from platform_support import executable_dir

logger = get_logger("db")
load_dotenv()
load_dotenv(os.path.join(executable_dir(), ".env"))

MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "test")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "events")

# Thread-safe client management
_client = None
_client_lock = threading.Lock()

def get_client():
    """Get or create MongoDB client with proper error handling"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                try:
                    _client = MongoClient(
                        MONGO_URI,
                        serverSelectionTimeoutMS=20000,
                        connectTimeoutMS=20000,
                        socketTimeoutMS=20000,
                        maxPoolSize=10,
                        minPoolSize=1
                    )
                    # Test connection
                    _client.admin.command('ismaster')
                except (ConnectionFailure, ServerSelectionTimeoutError) as e:
                    logger.error(f"Failed to connect to MongoDB: {e}")
                    raise Exception(f"Failed to connect to MongoDB: {e}")
    return _client

def close_connection():
    """Close MongoDB connection"""
    global _client
    if _client:
        _client.close()
        _client = None

# Register cleanup on exit
atexit.register(close_connection)

def get_events():
    """
    Retrieve all events from the 'events' collection in the MongoDB database.
    """
    try:
        client = get_client()
        db = client[DATABASE_NAME]
        events = list(db[COLLECTION_NAME].find())
        return events
    except Exception as e:
        logger.error(f"Error retrieving events: {e}")
        return []


def get_client_logos():
    """Map client id -> logo URL for event-cover fallbacks."""
    try:
        client = get_client()
        db = client[DATABASE_NAME]
        logos = {}
        for doc in db["clients"].find({}, {"clientLogo": 1}):
            url = doc.get("clientLogo")
            if url:
                logos[str(doc["_id"])] = url
        return logos
    except Exception as e:
        logger.error(f"Error retrieving client logos: {e}")
        return {}