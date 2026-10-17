"""Load and save the settings and the post queue as JSON files."""
import contextlib
import copy
import json
import os
import re
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
    "accounts": [],  # names of extra Facebook accounts; each keeps its own state.<name>.json
    "retry_attempts": 2,  # extra tries after a failure that certainly posted nothing
    "retry_delay_minutes": 5,
    "grace_minutes": 5,  # a post this late when the app or daemon wakes up still runs; later counts as missed
    "spam": {
        "duplicate_days": 7,
        "similarity": 0.9,
        "max_per_day": 15,
        "max_per_page_per_day": 3,
    },
    "ai": {
        "api_key": "",  # or the ANTHROPIC_API_KEY environment variable
        "model": "claude-opus-5-5",
        "effort": "medium",
        "fallbacks": True,  # let the API retry on another model if a safeguard declines a request
        "voice": "",  # how your posts should sound, added to every writing request
        "explain_failures": False,  # add an AI explanation to failure notifications (sends the log to Anthropic)
    },
    "notifications": {
        "events": ["done", "failed", "session_expired"],
        "telegram": {"bot_token": "", "chat_id": ""},
        "webhook_url": "",
        "email": {"host": "", "port": 587, "user": "", "password": "", "sender": "", "to": "", "starttls": True},
    },
}

ACCOUNT_NAME = re.compile(r"^[\w-]{1,40}$")
# Several files in one text field (the app's entry boxes, queue.json): "|" cannot be part of a Windows file name
MEDIA_SEPARATOR = "|"


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


def _merge_defaults(default, saved):
    """`saved` laid over `default`, keeping the default for anything of the wrong type."""
    merged = copy.deepcopy(default)
    if not isinstance(saved, dict):
        return merged
    for key, value in saved.items():
        if key in default and isinstance(default[key], dict):
            merged[key] = _merge_defaults(default[key], value)
        elif key in default and type(value) is not type(default[key]) and not (
            # JSON has one number type: 0.9 may be written as 1
            isinstance(default[key], float) and type(value) is int
        ):
            continue
        else:
            merged[key] = value
    return merged


def load_config(path):
    """Read the settings; anything missing or of the wrong type falls back to its default."""
    return load_config_from_dict(load_json(path, {}))


def load_config_from_dict(saved):
    """`saved` (settings as read from JSON) checked and completed like load_config does."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key in DEFAULT_CONFIG and isinstance(DEFAULT_CONFIG[key], dict):
                config[key] = _merge_defaults(DEFAULT_CONFIG[key], value)
                continue
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
    for key in ("page_gap_minutes", "retry_attempts", "retry_delay_minutes", "grace_minutes"):
        if config[key] < 0:
            config[key] = DEFAULT_CONFIG[key]
    config["accounts"] = [name for name in config["accounts"] if isinstance(name, str) and ACCOUNT_NAME.match(name)]
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


def state_path_for(base_dir, account=""):
    """Where the login session of `account` is kept; the default account uses state.json."""
    if not account or account == "default":
        return os.path.join(base_dir, "state.json")
    if not ACCOUNT_NAME.match(account):
        raise ValueError(f"Tên tài khoản không hợp lệ: {account!r} (chỉ chữ, số, gạch ngang và gạch dưới).")
    return os.path.join(base_dir, f"state.{account}.json")


def split_media(value):
    """The files named in a text field or job: "a.jpg|b.mp4" -> ["a.jpg", "b.mp4"]."""
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = str(value or "").split(MEDIA_SEPARATOR)
    return [part.strip() for part in parts if part and part.strip()]


def join_media(paths):
    return MEDIA_SEPARATOR.join(split_media(paths))


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


@contextlib.contextmanager
def locked(path, timeout=15.0, interval=0.1):
    """Hold an exclusive lock on `path` for a short critical section, waiting for others to finish.

    Used around read-modify-write cycles of queue.json by the daemon and enqueue.py.
    Raises TimeoutError if the lock stays taken for `timeout` seconds.
    """
    deadline = time.monotonic() + timeout
    handle = lock_file(path)
    while handle is None:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Không khóa được {os.path.basename(path)} (đang bị chương trình khác giữ).")
        time.sleep(interval)
        handle = lock_file(path)
    try:
        yield
    finally:
        handle.close()
