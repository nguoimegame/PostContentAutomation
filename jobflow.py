"""What happens around one queued post, shared by the app (app.py) and the background runner (daemon.py).

Turns post.py's exit code and output into a job status, decides on a retry, and says who to notify.
"""
import os
import re
import tempfile

import ai as ai_module
import ai_features
import notify
import runner
import scheduler

SESSION_EXPIRED_MESSAGE = (
    "Phiên đăng nhập Facebook đã hết hạn hoặc bị checkpoint, hãy đăng nhập lại ở tab Cài đặt (hoặc chạy login.py)"
)


class Outcome:
    """The result of a post attempt.

    event is what to notify about (None: nothing), or "retry" when the job was put back in the queue.
    """

    def __init__(self, success, message, event, stopped=False, retry=False, session_expired=False):
        self.success = success
        self.message = message
        self.event = event
        self.stopped = stopped
        self.retry = retry
        self.session_expired = session_expired


def new_task(job, content_file):
    return {"kind": "post", "job": job, "content_file": content_file, "stopped": False, "post_url": None, "verified": None}


def capture_line(task, line):
    """Pick the markers post.py prints (the post's link, whether it was seen on the page) out of its output."""
    if line.startswith(runner.POST_URL_MARK):
        task["post_url"] = line[len(runner.POST_URL_MARK):].strip() or None
    elif line.startswith(runner.POST_VERIFIED_MARK):
        task["verified"] = line[len(runner.POST_VERIFIED_MARK):].strip().lower() == "yes"


def describe(exit_code, stopped, post_url=None, verified=None):
    """(Outcome) for a finished post.py run."""
    if stopped:
        return Outcome(False, "Đã dừng theo yêu cầu", None, stopped=True)
    if exit_code == 0:
        if post_url:
            message = "Đã đăng, bài đã hiện trên trang"
        elif verified is False:
            message = "Đã đăng nhưng chưa thấy bài trên trang (nhóm có kiểm duyệt?), hãy kiểm tra trên Facebook"
        else:
            message = "Đã đăng, hộp soạn bài đã đóng"
        return Outcome(True, message, "done")
    if exit_code == runner.SESSION_EXPIRED_EXIT:
        return Outcome(False, SESSION_EXPIRED_MESSAGE, "session_expired", session_expired=True)
    if exit_code == runner.BLOCKED_EXIT:
        return Outcome(False, "Bị chặn: nội dung giống bài đã đăng gần đây hoặc đã đăng quá nhiều, xem nhật ký", "blocked")
    return Outcome(False, f"Thất bại (mã thoát {exit_code}), xem nhật ký và ảnh trong thư mục debug", "failed")


def finish(jobs, job, exit_code, stopped, post_url, verified, settings, now):
    """Record a finished run on `job` (retry it, or mark it done/failed and move a repeat on). Returns the Outcome."""
    outcome = describe(exit_code, stopped, post_url, verified)
    if post_url:
        job["post_url"] = post_url
    elif outcome.success:
        job.pop("post_url", None)
    if exit_code == runner.RETRYABLE_EXIT and not stopped:
        # Nothing was posted, so another try cannot publish it twice
        if scheduler.schedule_retry(
            job, now, settings.get("retry_attempts", 0), settings.get("retry_delay_minutes", 5), outcome.message
        ):
            outcome.retry = True
            outcome.event = None
            outcome.message = job["last_message"]
            return outcome
    scheduler.after_run(job, outcome.success, outcome.message, now)
    scheduler.hold_back_batch(jobs, job, now)
    return outcome


def write_content_file(content):
    handle, path = tempfile.mkstemp(prefix="fbpost_", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return path


def job_command(python_exe, base_dir, job, content_file, settings):
    """The post.py command line that publishes `job`."""
    return runner.build_post_command(
        python_exe,
        base_dir,
        content_file,
        page_url=job["page_url"],
        image_path=job["image_path"],
        headless=settings["headless"],
        account=job.get("account", ""),
        # A repeating post is meant to say the same thing every time
        allow_duplicate=bool(job.get("allow_duplicate")) or job["repeat"] != "none",
    )


def page_label(settings, url):
    if not url:
        return "(Trang cá nhân)"
    for page in settings["pages"]:
        if page["url"] == url:
            return page["name"]
    return url


def notification(settings, job, outcome):
    """(event, text) to send for a finished job, or None when there is nothing to tell."""
    if outcome.event is None:
        return None
    text = notify.describe(outcome.event, job, page_label(settings, job["page_url"]), outcome.message)
    return outcome.event, text


def exit_code_in(message):
    """The exit code a failure message mentions ("... (mã thoát 4) ..."), or None."""
    found = re.search(r"mã thoát (\d+)", message or "")
    return int(found.group(1)) if found else None


def newest_screenshot(base_dir):
    folder = os.path.join(base_dir, "debug")
    try:
        paths = [os.path.join(folder, name) for name in os.listdir(folder) if name.startswith("latest_") and name.endswith(".png")]
    except OSError:
        return None
    return max(paths, key=os.path.getmtime) if paths else None


def explanation_for(settings, outcome, job, log_lines, base_dir, engine_factory=None):
    """An AI explanation to add to a failure notification, or "" when the option is off, the event is not a plain
    failure, or the AI cannot be reached. Off by default: it sends the log and a screenshot to Anthropic."""
    if outcome.event != "failed" or not settings.get("ai", {}).get("explain_failures"):
        return ""
    engine = engine_factory() if engine_factory else ai_module.AI(settings)
    return ai_features.explain_for_notification(
        engine, job, outcome.message, list(log_lines), exit_code_in(outcome.message), newest_screenshot(base_dir)
    )
