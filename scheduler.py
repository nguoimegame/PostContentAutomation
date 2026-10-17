"""Scheduling logic for queued posts: pure computation, no UI and no disk."""
import calendar
import random
import uuid
from datetime import timedelta, datetime

RUN_AT_FORMAT = "%Y-%m-%d %H:%M"

# Fixed-length repeats. The others are "weekdays" (Monday to Friday), "monthly" (the same day each
# month) and "days:mon,wed,fri" (chosen days of the week); see is_valid_repeat.
REPEAT_STEPS = {
    "none": None,
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}
REPEAT_KINDS = ("none", "daily", "weekly", "weekdays", "monthly")
WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_NAMES = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật")
REPEAT_NAMES = {
    "none": "Không lặp",
    "daily": "Hằng ngày",
    "weekly": "Hằng tuần",
    "weekdays": "Thứ 2 đến Thứ 6",
    "monthly": "Hằng tháng",
}

STATUSES = ("pending", "running", "done", "failed", "missed")

# A job this late when the app starts still runs; anything later counts as missed
GRACE = timedelta(minutes=5)


def repeat_days(repeat):
    """The weekdays (0 = Monday) a "weekdays" or "days:..." repeat runs on; None for other repeats."""
    if repeat == "weekdays":
        return {0, 1, 2, 3, 4}
    if isinstance(repeat, str) and repeat.startswith("days:"):
        codes = [code.strip().lower() for code in repeat[5:].split(",") if code.strip()]
        if codes and all(code in WEEKDAY_CODES for code in codes):
            return {WEEKDAY_CODES.index(code) for code in codes}
    return None


def is_valid_repeat(repeat):
    return repeat in REPEAT_KINDS or repeat_days(repeat) is not None


def days_repeat(weekdays):
    """The repeat text for a set of weekdays (0 = Monday), e.g. {0, 2, 4} -> "days:mon,wed,fri"."""
    chosen = sorted({int(day) for day in weekdays})
    if not chosen or chosen[0] < 0 or chosen[-1] > 6:
        raise ValueError("Chưa chọn ngày trong tuần nào.")
    return "days:" + ",".join(WEEKDAY_CODES[day] for day in chosen)


def describe_repeat(repeat):
    if repeat in REPEAT_NAMES:
        return REPEAT_NAMES[repeat]
    days = repeat_days(repeat)
    if days:
        return ", ".join(WEEKDAY_NAMES[day] for day in sorted(days))
    return str(repeat)


def parse_run_at(text):
    return datetime.strptime(text.strip(), RUN_AT_FORMAT)


def format_run_at(moment):
    return moment.strftime(RUN_AT_FORMAT)


def new_job(content, page_url, image_path, run_at, repeat="none", account="", allow_duplicate=False, jitter_minutes=0):
    """A queue entry. `image_path` may name several files ("a.jpg|b.jpg"); `account` is a name from
    config["accounts"] (empty: the default login); `jitter_minutes` shifts each repeat by up to that many
    minutes either way so a repeating post does not go out at exactly the same time every day."""
    if not is_valid_repeat(repeat):
        raise ValueError(f"Kiểu lặp không hợp lệ: {repeat}")
    if jitter_minutes < 0:
        raise ValueError("Độ lệch giờ không được âm.")
    job = {
        "id": uuid.uuid4().hex,
        "content": content,
        "page_url": page_url,
        "image_path": image_path,
        "run_at": format_run_at(run_at),
        "repeat": repeat,
        "status": "pending",
        "last_run": None,
        "last_message": "",
    }
    if account:
        job["account"] = account
    if allow_duplicate:
        job["allow_duplicate"] = True
    if jitter_minutes:
        job["jitter_minutes"] = int(jitter_minutes)
    if repeat == "monthly":
        job["anchor_day"] = run_at.day
    return job


def valid_jobs(items):
    """Jobs from a loaded queue file that are safe to use, with missing fields filled in.

    Returns (jobs, dropped): entries that are not jobs at all, or have no readable
    run time, are left out and counted in `dropped`.
    """
    jobs = []
    dropped = 0
    for item in items:
        try:
            run_at = format_run_at(parse_run_at(item["run_at"]))
        except (TypeError, KeyError, AttributeError, ValueError):
            dropped += 1
            continue
        job = dict(item)
        job["run_at"] = run_at
        for key in ("content", "page_url", "image_path", "last_message"):
            if not isinstance(job.get(key), str):
                job[key] = ""
        for key in ("account", "post_url"):
            if key in job and not isinstance(job[key], str):
                job.pop(key)
        for key in ("attempts", "jitter_minutes", "anchor_day"):
            if key in job and (isinstance(job[key], bool) or not isinstance(job[key], int) or job[key] < 0):
                job.pop(key)
        if "allow_duplicate" in job and not isinstance(job["allow_duplicate"], bool):
            job.pop("allow_duplicate")
        if "base_run_at" in job:
            try:
                job["base_run_at"] = format_run_at(parse_run_at(job["base_run_at"]))
            except (TypeError, AttributeError, ValueError):
                job.pop("base_run_at")
        if not isinstance(job.get("id"), str) or not job["id"]:
            job["id"] = uuid.uuid4().hex
        if not is_valid_repeat(job.get("repeat")):
            job["repeat"] = "none"
        if job.get("status") not in STATUSES:
            job["status"] = "pending"
        job.setdefault("last_run", None)
        if isinstance(job.get("gap_minutes", 0), bool) or not isinstance(job.get("gap_minutes", 0), (int, float)):
            job["gap_minutes"] = 0
        try:
            ready_at(job)
        except (TypeError, ValueError):
            job.pop("not_before", None)
        jobs.append(job)
    return jobs, dropped


def stagger(start, count, gap_minutes):
    """Run times for posting the same content to `count` targets, one after another."""
    if gap_minutes < 0:
        raise ValueError("Giãn cách không được âm.")
    return [start + timedelta(minutes=gap_minutes * index) for index in range(count)]


def link_batch(jobs, gap_minutes):
    """Mark `jobs` as one post going to several targets, `gap_minutes` apart.

    The gap is then kept from the moment each post finishes (see hold_back_batch),
    not only between the planned start times.
    """
    batch = uuid.uuid4().hex
    for job in jobs:
        job["batch"] = batch
        job["gap_minutes"] = gap_minutes


def ready_at(job):
    """When the job may start: its run time, or later if its batch is holding it back."""
    run_at = parse_run_at(job["run_at"])
    hold = job.get("not_before")
    return max(run_at, datetime.fromisoformat(hold)) if hold else run_at


def hold_back_batch(jobs, finished, now):
    """Keep the rest of a batch `gap_minutes` behind the post that finished at `now`.

    A post that takes longer than the gap (a video upload) would otherwise be
    followed immediately by the next target.
    """
    batch = finished.get("batch")
    if not batch:
        return
    hold = now + timedelta(minutes=finished.get("gap_minutes", 0))
    for job in jobs:
        if job is finished or job.get("batch") != batch or job["status"] != "pending":
            continue
        if ready_at(job) < hold:
            job["not_before"] = hold.isoformat(timespec="seconds")
            job["last_message"] = f"Chờ giãn cách đến {hold:%H:%M:%S}"


def due_jobs(jobs, now):
    """Pending jobs whose time has come, earliest first."""
    due = [job for job in jobs if job["status"] == "pending" and ready_at(job) <= now]
    return sorted(due, key=ready_at)


def _next_monthly(run_at, now, anchor_day):
    if run_at > now:
        return run_at
    year, month = now.year, now.month
    for _ in range(14):
        day = min(anchor_day or run_at.day, calendar.monthrange(year, month)[1])
        candidate = datetime(year, month, day, run_at.hour, run_at.minute)
        if candidate > now and candidate >= run_at:
            return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def next_occurrence(run_at, repeat, now, anchor_day=None):
    """Next run after `now` for a repeating job; None for a one-off job.

    A run time still in the future is returned as it is (as long as it falls on a day the repeat allows).
    """
    if repeat == "none" or not is_valid_repeat(repeat):
        return None
    step = REPEAT_STEPS.get(repeat)
    if step is not None:
        if run_at > now:
            return run_at
        return run_at + step * ((now - run_at) // step + 1)
    if repeat == "monthly":
        return _next_monthly(run_at, now, anchor_day)
    days = repeat_days(repeat)
    if run_at > now and run_at.weekday() in days:
        return run_at
    candidate = datetime.combine(max(run_at, now).date(), run_at.time())
    while candidate <= now or candidate < run_at or candidate.weekday() not in days:
        candidate += timedelta(days=1)
    return candidate


def advance(job, now, rng=random):
    """Move a repeating job on to its next run after a run (or a missed one).

    A job whose run time is still ahead stays where it is: it was run early by hand. With
    `jitter_minutes` the time is shifted by a random amount; the planned time without the shift is kept in
    `base_run_at` so the shifts do not add up from run to run.
    """
    run_at = parse_run_at(job["run_at"])
    if run_at > now:
        return
    base = parse_run_at(job.get("base_run_at") or job["run_at"])
    upcoming = next_occurrence(base, job["repeat"], max(now, base), job.get("anchor_day"))
    jitter = int(job.get("jitter_minutes") or 0)
    if jitter > 0:
        job["base_run_at"] = format_run_at(upcoming)
        shifted = upcoming + timedelta(minutes=rng.randint(-jitter, jitter))
        upcoming = shifted if shifted > now else upcoming
    else:
        job.pop("base_run_at", None)
    job["run_at"] = format_run_at(upcoming)


def after_run(job, success, message, now, rng=random):
    """Record the outcome of a run; a repeating job moves on to its next run."""
    job["last_run"] = format_run_at(now)
    job["last_message"] = message
    job.pop("not_before", None)
    job.pop("attempts", None)
    if job["repeat"] == "none":
        job["status"] = "done" if success else "failed"
    else:
        job["status"] = "pending"
        advance(job, now, rng)


def schedule_retry(job, now, max_attempts, delay_minutes, message=""):
    """Put a failed job back in line for another try. Returns False when its tries are used up.

    Only for failures that certainly posted nothing (post.py exit code 4): a retry after a post that
    may be live would publish it twice.
    """
    attempts = int(job.get("attempts", 0))
    if attempts >= max_attempts:
        return False
    retry_at = now + timedelta(minutes=delay_minutes)
    job["attempts"] = attempts + 1
    job["status"] = "pending"
    job["not_before"] = retry_at.isoformat(timespec="seconds")
    job["last_run"] = format_run_at(now)
    job["last_message"] = f"Lần {attempts + 1} lỗi, thử lại lúc {retry_at:%H:%M}: {message}".rstrip(": ")
    return True


def skip_overdue(jobs, now, grace=GRACE, reason="ứng dụng đang tắt"):
    """Flag pending jobs more than `grace` late as missed instead of posting them late.

    A repeating job moves on to its next run. `reason` says why nothing could be
    posted in the meantime. Returns True if any job was changed.
    """
    changed = False
    for job in jobs:
        if job["status"] != "pending" or ready_at(job) >= now - grace:
            continue
        job.pop("not_before", None)
        job.pop("attempts", None)
        if job["repeat"] == "none":
            job["status"] = "missed"
            job["last_message"] = f"Đã quá giờ khi {reason}"
        else:
            job["last_message"] = f"Lỡ lần chạy {job['run_at']} ({reason})"
            advance(job, now)
        changed = True
    return changed


def recover_at_startup(jobs, now, grace=GRACE):
    """Tidy the queue at startup: flag missed jobs and jobs cut off mid-run.

    Returns True if any job was changed.
    """
    changed = False
    for job in jobs:
        if job["status"] == "running":
            job["status"] = "failed"
            job["last_message"] = "Bị gián đoạn do ứng dụng đóng giữa chừng"
            changed = True
    return skip_overdue(jobs, now, grace) or changed
