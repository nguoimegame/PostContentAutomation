"""Make one idea into a different text per page: placeholders and random choices.

    {page} {language} {date} {time} {weekday}   replaced when the post is queued
    {Xin chào|Chào bạn|Hello}                    one of the options, chosen at random

Vietnamese spellings work too: {tên_trang} {ngôn_ngữ} {ngày} {giờ} {thứ}. Text in braces
that is neither a known placeholder nor a list of options with "|" is left as it is.
"""
import random
import re

WEEKDAYS = ["Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật"]
CHOICE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def has_markup(text):
    return bool(CHOICE.search(text)) or any(name in PLACEHOLDER_NAMES for name in PLACEHOLDER.findall(text))


def render(text, page_name="", language="", moment=None, rng=random):
    """`text` with its random choices made and placeholders filled in."""
    text = CHOICE.sub(lambda match: rng.choice(match.group(1).split("|")), text)
    values = {
        "page": page_name,
        "language": language,
        "date": moment.strftime("%d/%m/%Y") if moment else "",
        "time": moment.strftime("%H:%M") if moment else "",
        "weekday": WEEKDAYS[moment.weekday()] if moment else "",
    }
    return PLACEHOLDER.sub(lambda match: values.get(ALIASES.get(match.group(1), match.group(1)), match.group(0)), text)


ALIASES = {
    "tên_trang": "page",
    "ngôn_ngữ": "language",
    "ngày": "date",
    "giờ": "time",
    "thứ": "weekday",
}
PLACEHOLDER_NAMES = set(ALIASES) | {"page", "language", "date", "time", "weekday"}


def pick(contents, index):
    """The text for the `index`-th target when several versions are given (they take turns)."""
    return contents[index % len(contents)]
