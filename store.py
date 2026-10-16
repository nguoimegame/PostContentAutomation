"""Load and save the settings and the post queue as JSON files."""
import copy
import json
import os
import shutil
import time
from datetime import datetime
from urllib.parse import urlsplit

DEFAULT_CONFIG = {
    "headless": False,
    "scheduler_enabled": True,
    "pages": [],  # [{"name": ..., "url": ..., "language": ...}]
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


def keep_aside(path):
    """Copy a file to `<name>.corrupt` before its damaged entries are dropped on the next save."""
    try:
        shutil.copyfile(path, path + ".corrupt")
    except OSError:
        pass


def _named_items(items, *text_keys):
    return [
        item
        for item in items
        if isinstance(item, dict) and all(isinstance(item.get(key), str) for key in text_keys)
    ]


def load_config(path):
    """Read the settings; anything missing or of the wrong type falls back to its default."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    saved = load_json(path, {})
    if isinstance(saved, dict):
        for key, value in saved.items():
            # type() rather than isinstance: a bool must not pass for the number of minutes
            if key in DEFAULT_CONFIG and type(value) is not type(DEFAULT_CONFIG[key]):
                continue
            config[key] = value
    config["pages"] = _named_items(config["pages"], "name", "url")
    for page in config["pages"]:
        # Pages saved before languages existed have no "language"; that is fine
        if not isinstance(page.get("language", ""), str):
            page["language"] = ""
    config["templates"] = _named_items(config["templates"], "name", "content")
    config["last_pages"] = [name for name in config["last_pages"] if isinstance(name, str)]
    if config["page_gap_minutes"] < 0:
        config["page_gap_minutes"] = DEFAULT_CONFIG["page_gap_minutes"]
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
    """When the login session was saved, or None if there is no usable session.

    A state file without Facebook's login cookie (c_user), or with one that has
    expired, is a logged-out browser and does not count. Facebook can still end
    a session early; only post.py finds that out.
    """
    try:
        with open(state_path, encoding="utf-8") as handle:
            cookies = json.load(handle)["cookies"]
        logged_in = any(
            cookie["name"] == "c_user"
            and cookie.get("domain", "").endswith("facebook.com")
            # Playwright stores -1 for a cookie that lasts until the browser closes
            and (cookie.get("expires", -1) < 0 or cookie["expires"] > time.time())
            for cookie in cookies
        )
        return datetime.fromtimestamp(os.path.getmtime(state_path)) if logged_in else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def lock_file(path):
    """Take an exclusive lock on `path` so only one copy of the app works on this folder.

    Returns the open file, or None if another process holds the lock. The lock
    lasts until the file is closed or the process ends, even after a crash.
    """
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle
