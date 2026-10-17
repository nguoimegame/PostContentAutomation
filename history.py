"""Append-only log of every post attempt (history.jsonl), one JSON object per line.

Written by post.py, so it covers posts made from the command line, the app and the daemon alike.
Also the source for the duplicate and rate checks in spam.py.

    python history.py [--limit 20] [--status posted] [--json]
"""
import argparse
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(BASE_DIR, "history.jsonl")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def now_text(moment=None):
    return (moment or datetime.now()).strftime(TIME_FORMAT)


def append(path, record, moment=None):
    """Add one record. Returns False if it could not be written (never raises: a log must not stop a post)."""
    entry = {"ts": now_text(moment), **record}
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def parse_time(text):
    try:
        return datetime.strptime(text, TIME_FORMAT)
    except (TypeError, ValueError):
        return None


def load(path):
    """All readable records, oldest first. Damaged lines are skipped."""
    records = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and parse_time(record.get("ts")) is not None:
                    records.append(record)
    except OSError:
        pass
    return records


def summarize(record, width=60):
    content = " ".join(str(record.get("content", "")).split())
    if len(content) > width:
        content = content[: width - 1] + "…"
    link = f"  {record['post_url']}" if record.get("post_url") else ""
    return f"{record['ts']}  {record.get('status', '?'):<15} {record.get('page_url') or '(trang cá nhân)'}  {content}{link}"


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Xem lịch sử đăng bài (history.jsonl).")
    parser.add_argument("--limit", type=int, default=20, help="Số bản ghi mới nhất (mặc định 20, 0 là tất cả).")
    parser.add_argument("--status", default=None, help="Chỉ lấy một trạng thái: posted, failed, session_expired, blocked.")
    parser.add_argument("--json", action="store_true", help="In JSON.")
    args = parser.parse_args(argv)

    records = load(HISTORY_PATH)
    if args.status:
        records = [record for record in records if record.get("status") == args.status]
    if args.limit > 0:
        records = records[-args.limit:]
    if args.json:
        print(json.dumps({"ok": True, "records": records}, ensure_ascii=False))
    else:
        for record in records:
            print(summarize(record))
        if not records:
            print("Chưa có bản ghi nào.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
