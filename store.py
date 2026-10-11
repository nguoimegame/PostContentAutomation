"""Load and save the settings and the post queue as JSON files."""
import copy
import json
import os
from datetime import datetime
from urllib.parse import urlsplit

DEFAULT_CONFIG = {
    "headless": False,
    "scheduler_enabled": True,
    "pages": [],  # [{"name": ..., "url": ...}]
    "templates": [],  # [{"name": ..., "content": ..., "media_path": ...}]
    "last_pages": [],  # names ticked in the compose tab
    "page_gap_minutes": 5,  # wait between targets when posting to several
}


def load_json(path, default):
    """Read a JSON file; return `default` if it is missing or unreadable.

    An unreadable file is renamed to `<name>.corrupt` so the next save does
    not overwrite it.
    """
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        try:
            os.replace(path, path + ".corrupt")
        except OSError:
            pass
        return default


def save_json(path, data):
    # Write to a temp file then rename, so a crash never leaves a half-written file
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def load_config(path):
    config = copy.deepcopy(DEFAULT_CONFIG)
    saved = load_json(path, {})
    if isinstance(saved, dict):
        config.update(saved)
    return config


def save_config(path, config):
    save_json(path, config)


def load_queue(path):
    jobs = load_json(path, [])
    return jobs if isinstance(jobs, list) else []


def save_queue(path, jobs):
    save_json(path, jobs)


def normalize_page_url(url):
    """Normalize a Fanpage URL to https://...facebook.com/<page>.

    Raises ValueError if the URL is empty or is not a Facebook address.
    """
    url = url.strip()
    if not url:
        raise ValueError("URL Fanpage đang để trống.")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        raise ValueError("URL phải là địa chỉ facebook.com.")
    if not parts.path.strip("/"):
        raise ValueError("URL chưa có tên trang, ví dụ: facebook.com/tentrang.")
    normalized = f"https://{host}{parts.path}"
    if parts.query:
        normalized += "?" + parts.query
    return normalized


def session_saved_at(state_path):
    """When the login session was saved, or None if there is no session yet."""
    if not os.path.exists(state_path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(state_path))
