"""Tell the user how a post went: Telegram, a webhook (for Zalo and other bridges) or email.

Settings live under "notifications" in config.json; the secrets can come from the environment instead
(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, NOTIFY_WEBHOOK_URL, SMTP_PASSWORD) so they need not be written to disk.
Sending never raises: a notification problem must not turn a good post into a failed one.

    python notify.py --test        send a test message to every configured channel
"""
import argparse
import json
import os
import smtplib
import sys
import threading
import urllib.parse
import urllib.request
from email.message import EmailMessage

EVENTS = ("done", "failed", "session_expired", "missed", "blocked")
DEFAULT_EVENTS = ["done", "failed", "session_expired"]
TIMEOUT = 15

ICONS = {"done": "✅", "failed": "❌", "session_expired": "🔑", "missed": "⏰", "blocked": "🛑", "test": "🔔"}


def settings_from(config):
    raw = (config or {}).get("notifications", {}) or {}
    telegram = dict(raw.get("telegram", {}) or {})
    email = dict(raw.get("email", {}) or {})
    env = os.environ
    telegram["bot_token"] = env.get("TELEGRAM_BOT_TOKEN") or telegram.get("bot_token", "")
    telegram["chat_id"] = env.get("TELEGRAM_CHAT_ID") or telegram.get("chat_id", "")
    email["password"] = env.get("SMTP_PASSWORD") or email.get("password", "")
    return {
        "events": raw.get("events", DEFAULT_EVENTS),
        "telegram": telegram,
        "webhook_url": env.get("NOTIFY_WEBHOOK_URL") or raw.get("webhook_url", ""),
        "email": email,
    }


def channels(settings):
    """The names of the channels that have enough settings to be used."""
    found = []
    if settings["telegram"].get("bot_token") and settings["telegram"].get("chat_id"):
        found.append("telegram")
    if settings["webhook_url"]:
        found.append("webhook")
    email = settings["email"]
    if email.get("host") and email.get("to"):
        found.append("email")
    return found


def send_telegram(settings, event, text, job, opener=urllib.request.urlopen):
    token = settings["telegram"]["bot_token"]
    data = urllib.parse.urlencode({"chat_id": settings["telegram"]["chat_id"], "text": text[:4000]}).encode("utf-8")
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with opener(request, timeout=TIMEOUT) as response:
        if response.status >= 300:
            raise OSError(f"Telegram trả về mã {response.status}")


def send_webhook(settings, event, text, job, opener=urllib.request.urlopen):
    body = json.dumps({"event": event, "text": text, "job": job}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        settings["webhook_url"], data=body, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    with opener(request, timeout=TIMEOUT) as response:
        if response.status >= 300:
            raise OSError(f"Webhook trả về mã {response.status}")


def send_email(settings, event, text, job, smtp_class=smtplib.SMTP):
    email = settings["email"]
    message = EmailMessage()
    message["Subject"] = text.splitlines()[0][:150] if text else event
    message["From"] = email.get("sender") or email.get("user") or email["to"]
    message["To"] = email["to"]
    message.set_content(text)
    with smtp_class(email["host"], int(email.get("port", 587) or 587), timeout=TIMEOUT) as server:
        if email.get("starttls", True):
            server.starttls()
        if email.get("user"):
            server.login(email["user"], email.get("password", ""))
        server.send_message(message)


SENDERS = {"telegram": send_telegram, "webhook": send_webhook, "email": send_email}


def describe(event, job=None, page_label="", detail=""):
    """The message text for an event about `job`."""
    titles = {
        "done": "Đã đăng bài",
        "failed": "Đăng bài thất bại",
        "session_expired": "Phiên đăng nhập Facebook đã hết hạn, hãy đăng nhập lại",
        "missed": "Bài hẹn giờ bị lỡ",
        "blocked": "Bài bị chặn (nghi trùng lặp hoặc đăng quá nhiều)",
        "test": "Thông báo thử",
    }
    lines = [f"{ICONS.get(event, '')} {titles.get(event, event)}".strip()]
    if job:
        lines.append(f"Nơi đăng: {page_label or job.get('page_url') or '(trang cá nhân)'}")
        preview = " ".join(str(job.get("content", "")).split())
        if preview:
            lines.append(f"Nội dung: {preview[:120]}{'…' if len(preview) > 120 else ''}")
    if detail:
        lines.append(detail)
    if job and job.get("post_url"):
        lines.append(f"Link bài: {job['post_url']}")
    return "\n".join(lines)


def notify(config, event, text, job=None, senders=None):
    """Send `text` about `event` to every channel. Returns [(channel, error_or_None)]; never raises."""
    settings = settings_from(config)
    if event != "test" and event not in settings["events"]:
        return []
    senders = senders or SENDERS
    payload = None
    if job:
        payload = {key: job.get(key) for key in ("id", "page_url", "run_at", "status", "post_url", "last_message")}
    results = []
    for channel in channels(settings):
        try:
            senders[channel](settings, event, text, payload)
            results.append((channel, None))
        except Exception as error:  # noqa: BLE001 - any failure is reported, never raised
            results.append((channel, f"{type(error).__name__}: {error}"))
    return results


def notify_in_background(config, event, text, job=None, on_result=None, senders=None):
    """`notify` on a thread so a slow network never freezes the app. Returns the thread."""

    def work():
        results = notify(config, event, text, job, senders)
        if on_result:
            on_result(results)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    import store

    parser = argparse.ArgumentParser(description="Gửi thông báo thử tới các kênh đã cấu hình.")
    parser.add_argument("--test", action="store_true", help="Gửi một tin nhắn thử.")
    args = parser.parse_args(argv)
    config = store.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    found = channels(settings_from(config))
    if not found:
        print("Chưa cấu hình kênh nào (mục notifications trong config.json).")
        return 1
    if not args.test:
        print("Các kênh đã cấu hình: " + ", ".join(found))
        return 0
    failed = False
    for channel, error in notify(config, "test", describe("test"), None):
        print(f"{channel}: " + ("OK" if error is None else f"LỖI {error}"))
        failed = failed or error is not None
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
