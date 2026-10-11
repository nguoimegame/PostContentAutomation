"""Scheduling logic for queued posts: pure computation, no UI and no disk."""
import uuid
from datetime import timedelta, datetime

RUN_AT_FORMAT = "%Y-%m-%d %H:%M"

REPEAT_STEPS = {
    "none": None,
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}

STATUSES = ("pending", "running", "done", "failed", "missed")

# A job this late when the app starts still runs; anything later counts as missed
GRACE = timedelta(minutes=5)


def parse_run_at(text):
    return datetime.strptime(text.strip(), RUN_AT_FORMAT)


def format_run_at(moment):
    return moment.strftime(RUN_AT_FORMAT)


def new_job(content, page_url, image_path, run_at, repeat="none"):
    if repeat not in REPEAT_STEPS:
        raise ValueError(f"Kiểu lặp không hợp lệ: {repeat}")
    return {
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
        if not isinstance(job.get("id"), str) or not job["id"]:
            job["id"] = uuid.uuid4().hex
        if job.get("repeat") not in REPEAT_STEPS:
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


def next_occurrence(run_at, repeat, now):
    """Next run after `now` for a repeating job; None for a one-off job."""
    step = REPEAT_STEPS[repeat]
    if step is None:
        return None
    if run_at > now:
        return run_at
    return run_at + step * ((now - run_at) // step + 1)


def after_run(job, success, message, now):
    """Record the outcome of a run; a repeating job moves on to its next run."""
    job["last_run"] = format_run_at(now)
    job["last_message"] = message
    job.pop("not_before", None)
    upcoming = next_occurrence(parse_run_at(job["run_at"]), job["repeat"], now)
    if upcoming is None:
        job["status"] = "done" if success else "failed"
    else:
        job["status"] = "pending"
        job["run_at"] = format_run_at(upcoming)


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
        upcoming = next_occurrence(parse_run_at(job["run_at"]), job["repeat"], now)
        if upcoming is None:
            job["status"] = "missed"
            job["last_message"] = f"Đã quá giờ khi {reason}"
        else:
            job["last_message"] = f"Lỡ lần chạy {job['run_at']} ({reason})"
            job["run_at"] = format_run_at(upcoming)
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
