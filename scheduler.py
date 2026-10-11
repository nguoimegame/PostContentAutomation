"""Scheduling logic for queued posts: pure computation, no UI and no disk."""
import uuid
from datetime import timedelta, datetime

RUN_AT_FORMAT = "%Y-%m-%d %H:%M"

REPEAT_STEPS = {
    "none": None,
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}

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


def stagger(start, count, gap_minutes):
    """Run times for posting the same content to `count` targets, one after another."""
    if gap_minutes < 0:
        raise ValueError("Giãn cách không được âm.")
    return [start + timedelta(minutes=gap_minutes * index) for index in range(count)]


def due_jobs(jobs, now):
    """Pending jobs whose time has come, earliest first."""
    due = [
        job
        for job in jobs
        if job["status"] == "pending" and parse_run_at(job["run_at"]) <= now
    ]
    return sorted(due, key=lambda job: parse_run_at(job["run_at"]))


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
    upcoming = next_occurrence(parse_run_at(job["run_at"]), job["repeat"], now)
    if upcoming is None:
        job["status"] = "done" if success else "failed"
    else:
        job["status"] = "pending"
        job["run_at"] = format_run_at(upcoming)


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
            continue
        if job["status"] != "pending":
            continue
        run_at = parse_run_at(job["run_at"])
        if run_at >= now - grace:
            continue
        upcoming = next_occurrence(run_at, job["repeat"], now)
        if upcoming is None:
            job["status"] = "missed"
            job["last_message"] = "Đã quá giờ khi ứng dụng đang tắt"
        else:
            job["last_message"] = f"Lỡ lần chạy {job['run_at']} (ứng dụng đang tắt)"
            job["run_at"] = format_run_at(upcoming)
        changed = True
    return changed
