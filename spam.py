"""Guards against posting the same text twice and against posting too often.

Based on history.jsonl: only posts that went through (status "posted") count.
"""
import re
from datetime import timedelta
from difflib import SequenceMatcher

import history

DEFAULTS = {
    "duplicate_days": 7,  # how far back a similar post on the same page counts as a duplicate (0 = off)
    "similarity": 0.9,  # 0..1, how alike two texts must be
    "max_per_day": 15,  # posts in the last 24 hours, all pages together (0 = no limit)
    "max_per_page_per_day": 3,  # posts in the last 24 hours on one page (0 = no limit)
}

# Comparing very long posts is slow and the start of a text is what repeats
COMPARE_CHARS = 3000


class Problem:
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __repr__(self):
        return f"Problem({self.code!r}, {self.message!r})"


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()[:COMPARE_CHARS]


def similarity(first, second):
    first, second = normalize(first), normalize(second)
    if not first or not second:
        return 0.0
    matcher = SequenceMatcher(None, first, second, autojunk=False)
    # The cheap upper bounds rule out most pairs before the slow exact ratio
    if matcher.real_quick_ratio() < 0.5 or matcher.quick_ratio() < 0.5:
        return 0.0
    return matcher.ratio()


def same_page(first, second):
    return (first or "").rstrip("/").lower() == (second or "").rstrip("/").lower()


def settings_from(config):
    merged = dict(DEFAULTS)
    merged.update((config or {}).get("spam", {}) or {})
    return merged


def check(content, page_url, records, now, settings=None, allow_duplicate=False):
    """The reasons not to post `content` to `page_url` now (empty list: go ahead)."""
    settings = {**DEFAULTS, **(settings or {})}
    posted = []
    for record in records:
        moment = history.parse_time(record.get("ts"))
        if record.get("status") == "posted" and moment is not None and moment <= now:
            posted.append((moment, record))
    problems = []

    day_ago = now - timedelta(hours=24)
    last_day = [(moment, record) for moment, record in posted if moment > day_ago]
    if settings["max_per_day"] and len(last_day) >= settings["max_per_day"]:
        problems.append(Problem("daily_limit", f"Đã đăng {len(last_day)} bài trong 24 giờ qua (giới hạn {settings['max_per_day']})."))
    on_page = [pair for pair in last_day if same_page(pair[1].get("page_url"), page_url)]
    if settings["max_per_page_per_day"] and len(on_page) >= settings["max_per_page_per_day"]:
        problems.append(
            Problem("page_limit", f"Đã đăng {len(on_page)} bài lên nơi này trong 24 giờ qua (giới hạn {settings['max_per_page_per_day']}).")
        )

    if settings["duplicate_days"] and not allow_duplicate and normalize(content):
        since = now - timedelta(days=settings["duplicate_days"])
        for moment, record in reversed(posted):
            if moment < since:
                break
            if not same_page(record.get("page_url"), page_url):
                continue
            score = similarity(content, record.get("content", ""))
            if score >= settings["similarity"]:
                problems.append(
                    Problem("duplicate", f"Nội dung giống {score:.0%} bài đã đăng lúc {record['ts'].replace('T', ' ')} lên nơi này.")
                )
                break
    return problems
