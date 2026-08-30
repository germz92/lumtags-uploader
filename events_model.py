"""Event / collection parsing — same rules as the old event tree."""

import os
from datetime import datetime, timezone
from urllib.parse import quote_plus

GALLERY_ORIGIN = os.environ.get("GALLERY_PUBLIC_URL", "https://www.lumtags.com").rstrip("/")

DATE_KEYS = ("eventDate", "event_date", "date", "startDate", "start_date", "createdAt")


def coerce_datetime(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        try:
            return datetime(value.year, value.month, value.day)
        except Exception:
            return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(timestamp)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, dict) and "$date" in value:
        return coerce_datetime(value["$date"])
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt)
            except ValueError:
                continue
    return None


def parse_event_date(raw):
    for key in DATE_KEYS:
        if key in raw:
            parsed = coerce_datetime(raw.get(key))
            if parsed is not None:
                return parsed
    return None


def format_event_date(value):
    if not value:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%b %d, %Y")


class Collection:
    def __init__(self, event_id, event_name, collection_name, parent_name, s3_folder):
        self.event_id = event_id
        self.event_name = event_name
        self.collection_name = collection_name
        self.parent_name = parent_name or ""
        self.s3_folder = s3_folder

    @property
    def id(self):
        return f"{self.event_id}_{self.s3_folder}"

    @property
    def display_name(self):
        if self.parent_name:
            return f"{self.parent_name}  ›  {self.collection_name}"
        return self.collection_name

    @property
    def full_label(self):
        return f"{self.event_name}  ›  {self.display_name}"

    @property
    def live_gallery_url(self):
        if not self.event_id or not self.collection_name:
            return ""
        tab = quote_plus(self.collection_name)
        return f"{GALLERY_ORIGIN}/#/clients/{self.event_id}?tab={tab}"


def parse_event_image(raw, client_logo=None):
    featured = raw.get("featuredImage")
    if isinstance(featured, str) and featured.strip():
        value = featured.strip()
        return (("url", value) if value.startswith("http") else ("s3", value)), "cover"
    for highlight in raw.get("highlights") or []:
        if isinstance(highlight, dict):
            value = highlight.get("src_key") or highlight.get("src") or highlight.get("url")
        else:
            value = highlight
        if isinstance(value, str) and value.strip():
            value = value.strip()
            return (("url", value) if value.startswith("http") else ("s3", value)), "cover"
    branding = raw.get("branding") or {}
    for field in ("bannerImageDesktop", "bannerImageMobile"):
        value = branding.get(field)
        if isinstance(value, str) and value.startswith("http"):
            return ("url", value), "cover"
    if isinstance(client_logo, str) and client_logo.strip():
        return ("url", client_logo.strip()), "logo"
    return None, None


class Event:
    def __init__(self, event_id, name, collections, event_date=None, image_ref=None, image_kind=None):
        self.id = event_id
        self.name = name
        self.collections = collections
        self.event_date = event_date
        self.image_ref = image_ref
        self.image_kind = image_kind or "cover"

    @property
    def date_label(self):
        return format_event_date(self.event_date)


def parse_events(raw_events, client_logos=None):
    events = list(raw_events or [])
    logos = client_logos or {}
    try:
        events.sort(key=lambda e: e.get("createdAt") or e.get("_id"), reverse=True)
    except Exception:
        pass

    parsed = []
    for event in events:
        event_id = str(event.get("_id"))
        event_name = event.get("name", "Unnamed Event")
        client_logo = logos.get(str(event.get("clientId") or ""))

        subcollection_groups = event.get("subcollectionGroups", [])
        parent_folders = {g.get("parentFolder", "") for g in subcollection_groups}
        child_to_parent_folder = {}
        for group in subcollection_groups:
            for child_folder in group.get("childFolders", []):
                child_to_parent_folder[child_folder] = group.get("parentFolder", "")

        folder_to_name = {}
        collections = event.get("eventCollections", [])
        for col_array in collections:
            for sub in col_array:
                folder = sub.get("collection_folder", "")
                name = sub.get("collection_name", "")
                if folder:
                    folder_to_name[folder] = name

        parsed_collections = []
        for col_array in collections:
            for idx, sub in enumerate(col_array):
                collection_name = sub.get("collection_name", f"Collection {idx + 1}")
                collection_folder = sub.get("collection_folder", "undefined_folder")
                if collection_folder == "__hidden__":
                    continue
                if collection_folder in parent_folders:
                    continue
                parent_folder = child_to_parent_folder.get(collection_folder)
                parent_name = folder_to_name.get(parent_folder, "") if parent_folder else ""
                parsed_collections.append(Collection(
                    event_id=event_id,
                    event_name=event_name,
                    collection_name=collection_name,
                    parent_name=parent_name,
                    s3_folder=collection_folder,
                ))
        image_ref, image_kind = parse_event_image(event, client_logo)
        parsed.append(Event(
            event_id,
            event_name,
            parsed_collections,
            event_date=parse_event_date(event),
            image_ref=image_ref,
            image_kind=image_kind,
        ))
    return parsed


def find_collection(events, event_id, s3_folder):
    for event in events:
        if event.id != event_id:
            continue
        for collection in event.collections:
            if collection.s3_folder == s3_folder:
                return event, collection
    return None, None
