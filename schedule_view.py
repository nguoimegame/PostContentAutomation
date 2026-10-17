"""Date arithmetic for the calendar tab: no tkinter here, so it can be tested without a window."""
import calendar
from datetime import date, datetime, time, timedelta

import scheduler

SNAP_MINUTES = 15
MAX_PROJECTED = 60


def week_start(day):
    """The Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def week_days(day):
    first = week_start(day)
    return [first + timedelta(days=offset) for offset in range(7)]


def month_weeks(year, month):
    """The weeks (Monday first, 7 dates each) that cover a month, including days of the neighbouring months."""
    return calendar.Calendar(0).monthdatescalendar(year, month)


def shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def snap(moment, minutes=SNAP_MINUTES):
    """`moment` rounded to the nearest `minutes` (seconds dropped)."""
    total = moment.hour * 60 + moment.minute
    rounded = int(round(total / minutes)) * minutes
    base = datetime.combine(moment.date(), time(0, 0))
    return min(base + timedelta(minutes=rounded), base + timedelta(days=1) - timedelta(minutes=minutes))


def minutes_to_time(minutes):
    """A time of day from minutes after midnight, kept inside the day and on the snap grid."""
    minutes = max(0, min(int(minutes), 24 * 60 - SNAP_MINUTES))
    minutes = minutes // SNAP_MINUTES * SNAP_MINUTES
    return time(minutes // 60, minutes % 60)


def jobs_by_day(jobs):
    """{date: [job, ...]} with each day's jobs in time order. A job is placed by the time it will actually run."""
    days = {}
    for job in jobs:
        try:
            moment = scheduler.ready_at(job)
        except (TypeError, ValueError, KeyError):
            continue
        days.setdefault(moment.date(), []).append((moment, job))
    return {day: [job for _, job in sorted(items, key=lambda item: item[0])] for day, items in days.items()}


def projected_runs(job, start, end, limit=MAX_PROJECTED):
    """The later runs of a repeating, waiting job that fall in [start, end): shown as faint copies in the calendar."""
    if job.get("status") != "pending" or job.get("repeat", "none") == "none":
        return []
    try:
        current = scheduler.parse_run_at(job["run_at"])
    except (TypeError, ValueError, KeyError):
        return []
    runs = []
    reference = max(current, start - timedelta(seconds=1))
    while len(runs) < limit:
        upcoming = scheduler.next_occurrence(current, job["repeat"], reference, job.get("anchor_day"))
        if upcoming is None or upcoming >= end:
            break
        if upcoming > current:
            runs.append(upcoming)
        reference = upcoming
        current = upcoming
    return runs


def drop_target(job, new_moment, now, mode):
    """The run time a dragged job gets when dropped on `new_moment`.

    In week view the dropped time is used (snapped); in month view only the day changes. Raises ValueError for
    jobs that cannot move or a time that has passed.
    """
    if job.get("status") != "pending":
        raise ValueError("Chỉ kéo được các bài đang chờ.")
    old = scheduler.parse_run_at(job["run_at"])
    if mode == "month":
        target = datetime.combine(new_moment.date(), old.time())
    else:
        target = snap(new_moment)
    if target <= now:
        raise ValueError("Thời điểm mới phải ở tương lai.")
    return target


def title_for(mode, anchor):
    if mode == "month":
        return f"Tháng {anchor.month}/{anchor.year}"
    days = week_days(anchor)
    return f"Tuần {days[0]:%d/%m} – {days[-1]:%d/%m/%Y}"
