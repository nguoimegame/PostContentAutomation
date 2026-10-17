"""Tkinter UI for the Facebook auto-poster: compose, queue, pages, settings."""
import os
import queue
import re
import sys
import tempfile
import threading
import tkinter as tk
import traceback
from datetime import datetime, timedelta
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import ai as ai_module
import ai_features
import backup
import calendar_tab
import history
import jobflow
import notify
import runner
import scheduler
import store
import variants

PERSONAL = "(Trang cá nhân)"
DEFAULT_ACCOUNT = "(Mặc định)"
REPEAT_LABELS = {key: scheduler.describe_repeat(key) for key in ("none", "daily", "weekly", "weekdays", "monthly")}
REPEAT_BY_LABEL = {label: key for key, label in REPEAT_LABELS.items()}
CUSTOM_DAYS_LABEL = "Chọn các thứ…"


def repeat_label(repeat):
    return scheduler.describe_repeat(repeat)


def repeat_from_label(label):
    """The repeat setting for a label shown in a repeat box: a fixed choice or a list like "Thứ 2, Thứ 4"."""
    if label in REPEAT_BY_LABEL:
        return REPEAT_BY_LABEL[label]
    names = [part.strip() for part in label.split(",")]
    days = {scheduler.WEEKDAY_NAMES.index(name) for name in names if name in scheduler.WEEKDAY_NAMES}
    if days and len(days) == len(names):
        return scheduler.days_repeat(days)
    raise ValueError("Hãy chọn kiểu lặp trong danh sách.")


def format_review(result):
    """The text of the review dialog."""
    verdicts = {"ok": "Có thể đăng như hiện tại.", "fix": "Nên sửa vài chỗ trước khi đăng.", "risky": "Có rủi ro: nên xem kỹ trước khi đăng."}
    lines = [verdicts.get(result.get("verdict"), ""), result.get("summary", "")]
    for issue in result.get("issues", []):
        lines.append(f"• [{issue['kind']}] “{issue['quote']}”: {issue['problem']}\n    → {issue['suggestion']}")
    return "\n".join(line for line in lines if line)


def format_explanation(result, message=""):
    lines = [result["summary"], "", f"Nguyên nhân: {result['cause']}", ""]
    lines += [f"{number}. {step}" for number, step in enumerate(result["steps"], start=1)]
    lines.append("")
    lines.append("Có thể thử lại." if result["safe_to_retry"] else "Đừng đăng lại ngay: bài có thể đã lên.")
    if result["check_facebook"]:
        lines.append("Hãy mở Facebook kiểm tra xem bài đã đăng chưa.")
    if message:
        lines += ["", f"(Ghi chú của bài: {message})"]
    return "\n".join(lines)


def repeat_choices():
    return list(REPEAT_LABELS.values()) + [CUSTOM_DAYS_LABEL]

STATUS_LABELS = {
    "pending": "Chờ",
    "running": "Đang chạy",
    "done": "Xong",
    "failed": "Lỗi",
    "missed": "Lỡ",
}
LENGTH_LABELS = {"Ngắn": "short", "Vừa": "medium", "Dài": "long"}
# (operation, menu text) of the "✨ AI giúp…" menu in the compose tab; None is a separator
AI_MENU = [
    ("review", "Kiểm tra bài trước khi đăng"),
    ("fix", "Sửa chính tả và ngữ pháp"),
    (None, ""),
    ("hashtags", "Thêm hashtag"),
    ("title", "Thêm dòng mở đầu thu hút"),
    ("cta", "Thêm lời kêu gọi hành động"),
    ("emoji", "Thêm emoji"),
    ("shorten", "Rút gọn"),
    ("expand", "Mở rộng"),
    (None, ""),
    ("localize", "Dịch cho các nơi đăng đã chọn"),
    ("variants", "Tạo vài biến thể"),
    (None, ""),
    ("describe", "Viết mô tả cho ảnh đang chọn"),
    ("card", "Tạo ảnh chữ từ nội dung"),
]
NOTIFY_EVENT_LABELS = {
    "done": "Đăng xong",
    "failed": "Đăng lỗi",
    "session_expired": "Phiên đăng nhập hết hạn",
    "missed": "Bài bị lỡ",
    "blocked": "Bị chặn (trùng/quá nhiều)",
}
STATUS_COLORS = {
    "running": "#0b61a4",
    "done": "#6b6b6b",
    "failed": "#b3261e",
    "missed": "#a15c00",
}
# Offered in the Fanpage language box; anything else can be typed in
LANGUAGES = [
    "Tiếng Việt (Việt Nam)",
    "English (Mỹ)",
    "English (Anh)",
    "English (Úc)",
    "English (Canada)",
    "English (Philippines)",
    "English (Ấn Độ)",
    "日本語 (Nhật Bản)",
    "한국어 (Hàn Quốc)",
    "中文 (Trung Quốc)",
    "中文 (Đài Loan)",
    "ไทย (Thái Lan)",
    "Bahasa Indonesia (Indonesia)",
    "Español (Tây Ban Nha)",
    "Français (Pháp)",
    "Deutsch (Đức)",
]
MEDIA_TYPES = [
    ("Ảnh và video", "*.png *.jpg *.jpeg *.gif *.webp *.bmp *.mp4 *.mov *.avi *.mkv *.webm *.m4v *.wmv"),
    ("Ảnh", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
    ("Video", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v *.wmv"),
    ("Tất cả", "*.*"),
]

TICK_MS = 30000
POLL_MS = 100
MAX_LOG_LINES = 2000


class App:
    def __init__(self, root, base_dir=None):
        self.root = root
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(self.base_dir, "config.json")
        self.queue_path = os.path.join(self.base_dir, "queue.json")
        self.state_path = os.path.join(self.base_dir, "state.json")
        self.python_exe = runner.console_python(sys.executable)

        self.settings = store.load_config(self.config_path)
        self.jobs, dropped = scheduler.valid_jobs(store.load_queue(self.queue_path))
        if dropped:
            # The next save rewrites queue.json without the entries that could not be read
            store.keep_aside(self.queue_path)
        recovered = scheduler.recover_at_startup(self.jobs, datetime.now(), self._grace())
        self._last_tick = datetime.now()
        # Set when post.py reports that Facebook wants a new login; cleared by the next
        # login or the next post that goes through
        self._session_expired = False

        # The runner calls back from its reader thread; events are handed to
        # the UI thread through this queue and drained by _poll_events
        self.events = queue.Queue()
        self.runner = runner.ProcessRunner(
            lambda line: self.events.put(("line", line)),
            lambda code: self.events.put(("exit", code)),
        )
        self.task = None
        self._closed = False
        self._timers = set()
        # AI helpers: ai_factory can be replaced (tests); results of the last requests are kept for the AI tab
        self.ai_factory = lambda: ai_module.AI(self.settings)
        self._ai_busy = False
        self.ai_drafts = []
        self.series_posts = []
        self.assistant_history = []
        self.assistant_proposals = []

        self._build_ui()
        root.report_callback_exception = self._on_callback_error
        if dropped or recovered:
            self._save_queue()
        if dropped:
            self.log(f"⚠ Bỏ qua {dropped} mục hỏng trong queue.json, bản gốc được giữ ở queue.json.corrupt")
        self._refresh_pages()
        self._refresh_templates()
        self._refresh_queue()
        self._refresh_session()
        self._refresh_accounts()
        self._update_controls()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_events()
        self._later(1000, self._tick_loop)

    def _later(self, delay_ms, callback):
        """root.after, but tracked so shutdown can cancel what is still pending."""

        def run():
            self._timers.discard(timer)
            callback()

        timer = self.root.after(delay_ms, run)
        self._timers.add(timer)

    def _grace(self):
        """How late a post may be and still run (a later one counts as missed)."""
        return timedelta(minutes=self.settings["grace_minutes"])

    def _render(self, content, page_url, moment):
        """`content` with {page}, {language}, {date}… and {a|b} choices filled in for a page."""
        name, language = "", ""
        for page in self.settings["pages"]:
            if page["url"] == page_url:
                name, language = page["name"], page.get("language", "")
        return variants.render(content, name, language, moment)

    # ------------------------------------------------------------------
    # Pages and templates
    # ------------------------------------------------------------------

    def add_page(self, name, url, language="", voice=""):
        """Add a Fanpage, or update the one with the same name.

        `language` says which language and country the page is for, e.g. "English (Mỹ)".
        `voice` says how the page's posts should sound; the AI follows it when it writes for the page.
        """
        name = name.strip()
        if not name:
            raise ValueError("Chưa nhập tên Fanpage.")
        if name == PERSONAL:
            raise ValueError("Tên này đã được dùng cho trang cá nhân.")
        url = store.normalize_page_url(url)
        page = {"name": name, "url": url, "language": language.strip()}
        if voice.strip():
            page["voice"] = voice.strip()
        self._upsert(self.settings["pages"], page)
        self._save_settings()
        self._refresh_pages()
        self._refresh_queue()

    def remove_page(self, name):
        self.settings["pages"] = [p for p in self.settings["pages"] if p["name"] != name]
        self._save_settings()
        self._refresh_pages()
        self._refresh_queue()

    def save_template(self, name, content, media_path=""):
        """Add a content template, or overwrite the one with the same name.

        `media_path` is an optional image or video posted along with the text.
        """
        name = name.strip()
        media_path = media_path.strip()
        if not name:
            raise ValueError("Chưa nhập tên mẫu.")
        if not content.strip() and not media_path:
            raise ValueError("Mẫu chưa có nội dung hoặc ảnh/video.")
        if media_path and not os.path.isfile(media_path):
            raise ValueError(f"Không tìm thấy tệp ảnh/video:\n{media_path}")
        self._upsert(
            self.settings["templates"],
            {"name": name, "content": content, "media_path": media_path},
        )
        self._save_settings()
        self._refresh_templates()

    def find_template(self, name):
        for template in self.settings["templates"]:
            if template["name"] == name:
                return template
        return None

    def apply_template(self, name):
        """Replace the post being composed with a template's text and file."""
        template = self.find_template(name)
        if template is None:
            raise ValueError("Hãy chọn một mẫu trong danh sách.")
        self._set_compose_content(template["content"])
        # Templates saved before attachments existed have no media_path
        self.media_var.set(template.get("media_path", ""))

    def remove_template(self, name):
        self.settings["templates"] = [
            t for t in self.settings["templates"] if t["name"] != name
        ]
        self._save_settings()
        self._refresh_templates()

    @staticmethod
    def _upsert(items, new_item):
        for index, item in enumerate(items):
            if item["name"] == new_item["name"]:
                items[index] = new_item
                return
        items.append(new_item)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def set_headless(self, enabled):
        self.settings["headless"] = bool(enabled)
        self.headless_var.set(self.settings["headless"])
        self._save_settings()

    def set_scheduler_enabled(self, enabled):
        self.settings["scheduler_enabled"] = bool(enabled)
        self.scheduler_var.set(self.settings["scheduler_enabled"])
        self._save_settings()
        self._update_controls()

    def set_page_gap(self, minutes):
        """Minutes to wait between targets when one post goes to several of them."""
        try:
            minutes = int(str(minutes).strip())
        except ValueError:
            raise ValueError("Giãn cách phải là số phút nguyên, ví dụ 5.") from None
        if minutes < 0:
            raise ValueError("Giãn cách không được âm.")
        self.settings["page_gap_minutes"] = minutes
        self.gap_var.set(str(minutes))
        self._save_settings()
        return minutes

    def available_targets(self):
        """Everything a post can go to: the personal profile, then the saved pages."""
        return [PERSONAL] + [page["name"] for page in self.settings["pages"]]

    def select_targets(self, names):
        """Make `names` the targets the next post goes to."""
        self._targets = [name for name in self.available_targets() if name in names]
        self._remember_targets()

    def selected_targets(self):
        return list(self._targets)

    def open_target_dialog(self):
        """Open the popup for picking targets; its OK button calls select_targets."""
        labels = {
            page["name"]: f"{page['name']}  ·  {page['language']}"
            for page in self.settings["pages"]
            if page.get("language")
        }
        return TargetDialog(
            self.root,
            self.available_targets(),
            self.selected_targets(),
            self.select_targets,
            self._px,
            labels,
        )

    def selected_page_urls(self):
        """URLs of the ticked targets, in list order; "" stands for the personal profile."""
        return [self._page_url(name) for name in self.selected_targets()]

    def _remember_targets(self):
        names = self.selected_targets()
        self.target_count_var.set(f"Đã chọn {len(names)} nơi: " + ", ".join(names) if names else "Chưa chọn nơi nào")
        self.settings["last_pages"] = names
        self._save_settings()

    # A file briefly held by a sync tool or antivirus must not break the action in
    # progress: everything is still in memory and is written again on the next change

    def _save_settings(self):
        try:
            store.save_config(self.config_path, self.settings)
        except OSError as error:
            self.log(f"⚠ Không lưu được config.json: {error}")

    def _save_queue(self):
        try:
            store.save_queue(self.queue_path, self.jobs)
        except OSError as error:
            self.log(f"⚠ Không lưu được queue.json: {error}")

    # ------------------------------------------------------------------
    # Queue and scheduling
    # ------------------------------------------------------------------

    def enqueue(self, content, page_url, image_path, run_at, repeat="none", account="", jitter=0, allow_duplicate=False):
        job = scheduler.new_job(
            self._render(content, page_url, run_at), page_url, image_path, run_at, repeat, account, allow_duplicate, jitter
        )
        self.jobs.append(job)
        self._save_queue()
        self._refresh_queue()
        return job

    def enqueue_many(self, content, page_urls, image_path, run_at, repeat="none", gap_minutes=0, account="", jitter=0,
                     allow_duplicate=False):
        """Queue the same post once per target, `gap_minutes` apart, starting at `run_at`."""
        run_times = scheduler.stagger(run_at, len(page_urls), gap_minutes)
        jobs = [
            scheduler.new_job(
                self._render(content, page_url, moment), page_url, image_path, moment, repeat, account,
                allow_duplicate, jitter,
            )
            for page_url, moment in zip(page_urls, run_times)
        ]
        if len(jobs) > 1:
            scheduler.link_batch(jobs, gap_minutes)
        self.jobs.extend(jobs)
        self._save_queue()
        self._refresh_queue()
        return jobs

    def move_job(self, job_id, moment):
        """Give a waiting post a new run time (used by the calendar). Raises ValueError if it cannot move."""
        job = self.find_job(job_id)
        if job is None or job["status"] != "pending":
            raise ValueError("Chỉ đổi được giờ của các bài đang chờ.")
        if moment <= datetime.now():
            raise ValueError("Thời điểm mới phải ở tương lai.")
        job["run_at"] = scheduler.format_run_at(moment)
        job["last_message"] = ""
        for key in ("not_before", "attempts", "base_run_at"):
            job.pop(key, None)
        if job["repeat"] == "monthly":
            job["anchor_day"] = moment.day
        self._save_queue()
        self._refresh_queue()

    def remove_job(self, job_id):
        self.jobs = [job for job in self.jobs if job["id"] != job_id]
        self._save_queue()
        self._refresh_queue()

    def find_job(self, job_id):
        for job in self.jobs:
            if job["id"] == job_id:
                return job
        return None

    def tick(self):
        """Publish the next due post, if any and if nothing else is running."""
        if self._closed or not self.settings["scheduler_enabled"]:
            return
        now = datetime.now()
        # Ticks stop while the computer sleeps or the scheduler is off. Posts that came
        # due in such a gap are flagged as missed, like at startup, not all posted late.
        paused = now - self._last_tick > self._grace()
        self._last_tick = now
        if paused and scheduler.skip_overdue(
            self.jobs, now, self._grace(), reason="máy ngủ hoặc hẹn giờ đang tắt"
        ):
            self._save_queue()
            self._refresh_queue()
        if self.is_busy():
            return
        due = scheduler.due_jobs(self.jobs, now)
        if due:
            self.run_job(due[0])

    def _tick_loop(self):
        # An error in one tick must not end the loop, or nothing is ever posted again
        try:
            self.tick()
        finally:
            if not self._closed:
                self._later(TICK_MS, self._tick_loop)

    def run_job(self, job):
        """Publish a queued post now; a post that cannot start is marked failed."""
        if self.is_busy():
            return
        try:
            self._start_post(job["content"], job["page_url"], job["image_path"], job)
        except RuntimeError as error:
            scheduler.after_run(job, False, str(error), datetime.now())
            self.log(f"✖ Không đăng được bài hẹn {job['run_at']}: {error}")
        else:
            job["status"] = "running"
        self._save_queue()
        self._refresh_queue()

    # ------------------------------------------------------------------
    # Running post.py / login.py
    # ------------------------------------------------------------------

    def is_busy(self):
        return self.task is not None

    def publish_now(self, content, page_url="", image_path="", account=""):
        """Publish immediately. Raises RuntimeError if it cannot start."""
        content = self._render(content, page_url, datetime.now())
        self._start_post(content, page_url, image_path, None, account)

    def publish_to(self, content, page_urls, image_path="", gap_minutes=0, account=""):
        """Publish to each target in turn, starting now.

        One target is posted directly. Several go through the queue, one post
        per target and `gap_minutes` apart, so each has its own status and a
        failure does not stop the rest. Returns the queued jobs (none for one target).
        """
        if not page_urls:
            raise ValueError("Chưa chọn nơi đăng.")
        if len(page_urls) == 1:
            self.publish_now(content, page_urls[0], image_path, account)
            return []
        self._check_can_start(account)
        if not self.settings["scheduler_enabled"]:
            raise RuntimeError(
                "Đăng lên nhiều nơi cần bật “Tự động đăng khi đến giờ” ở tab Hàng đợi."
            )
        start = datetime.now().replace(second=0, microsecond=0)
        jobs = self.enqueue_many(content, page_urls, image_path, start, "none", gap_minutes, account)
        self.run_job(jobs[0])
        return jobs

    def state_path_for(self, account=""):
        return store.state_path_for(self.base_dir, account)

    def _check_can_start(self, account=""):
        if self.is_busy():
            raise RuntimeError("Đang có một tác vụ khác chạy, hãy đợi nó xong.")
        if store.session_saved_at(self.state_path_for(account)) is None:
            who = f" (tài khoản {account})" if account else ""
            raise RuntimeError(f"Chưa đăng nhập Facebook{who}. Vào tab Cài đặt để đăng nhập trước.")

    def _start_post(self, content, page_url, image_path, job, account=""):
        account = job.get("account", "") if job is not None else (account or "")
        self._check_can_start(account)

        content_file = jobflow.write_content_file(content)
        if job is not None:
            command = jobflow.job_command(self.python_exe, self.base_dir, job, content_file, self.settings)
        else:
            command = runner.build_post_command(
                self.python_exe,
                self.base_dir,
                content_file,
                page_url=page_url,
                image_path=image_path,
                headless=self.settings["headless"],
                account=account,
            )
        try:
            self.runner.start(command, cwd=self.base_dir)
        except OSError as error:
            self._remove_file(content_file)
            raise RuntimeError(f"Không chạy được post.py: {error}") from error

        self.task = jobflow.new_task(job, content_file)
        self.task.update(content=content, page_url=page_url)
        self.log(f"▶ Bắt đầu đăng bài lên {self._page_label(page_url)}")
        self._update_controls()

    def start_login(self, account=""):
        """Open the browser for a manual Facebook login (login.py)."""
        if self.is_busy():
            raise RuntimeError("Đang có một tác vụ khác chạy, hãy đợi nó xong.")
        store.state_path_for(self.base_dir, account)  # rejects an unusable account name
        command = runner.build_login_command(self.python_exe, self.base_dir, account)
        try:
            self.runner.start(command, cwd=self.base_dir)
        except OSError as error:
            raise RuntimeError(f"Không chạy được login.py: {error}") from error
        self.task = {"kind": "login", "stopped": False, "account": account}
        self.log("▶ Mở trình duyệt để đăng nhập Facebook" + (f" (tài khoản {account})" if account else ""))
        self._update_controls()

    def confirm_login(self):
        """Tell login.py the user has finished logging in, so it saves the session."""
        if self.task and self.task["kind"] == "login":
            self.runner.send_enter()

    def stop_task(self):
        if self.task:
            self.task["stopped"] = True
            self.runner.stop()

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "line":
                    if self.task and self.task["kind"] == "post":
                        jobflow.capture_line(self.task, payload)
                        self.task.setdefault("log", []).append(payload)
                    self.log(payload)
                elif kind == "call":
                    payload()  # work handed over by a thread (an AI answer)
                else:
                    self._finish_task(payload)
        except queue.Empty:
            pass
        finally:
            # Keep polling even if handling an event failed, or the app stays "busy" forever
            if not self._closed:
                self._later(POLL_MS, self._poll_events)

    def _finish_task(self, exit_code):
        task, self.task = self.task, None
        if task is None:
            return
        success = exit_code == 0 and not task["stopped"]
        if task["kind"] == "post":
            self._remove_file(task["content_file"])
            job = task["job"]
            now = datetime.now()
            if job is not None:
                outcome = jobflow.finish(
                    self.jobs, job, exit_code, task["stopped"], task["post_url"], task["verified"], self.settings, now
                )
            else:
                outcome = jobflow.describe(exit_code, task["stopped"], task["post_url"], task["verified"])
            success, message = outcome.success, outcome.message
            # Only a post that went through proves the session works again: an unrelated
            # failure (no network) says nothing about it
            if success or outcome.session_expired:
                self._session_expired = outcome.session_expired
                self._refresh_session()
            if job is not None:
                self._save_queue()
                self._refresh_queue()
            self._notify_outcome(
                job or {"content": task["content"], "page_url": task["page_url"], "post_url": task["post_url"]},
                outcome,
                task.get("log", []),
            )
        else:
            if success and not task.get("account"):
                self._session_expired = False
            self._refresh_session()
            message = "Đã lưu phiên đăng nhập" if success else "Đăng nhập chưa hoàn tất"
        self.log(("✔ " if success else "✖ ") + message)
        self._update_controls()
        # Pick up the next due post without waiting for the regular tick
        self._later(500, self.tick)

    def _notify_outcome(self, job, outcome, log_lines=()):
        """Send the result of a post to the channels set up in Cài đặt, if any."""
        message = jobflow.notification(self.settings, job, outcome)
        if message is None or not notify.channels(notify.settings_from(self.settings)):
            return
        event, body = message
        if outcome.event == "failed" and self.settings["ai"]["explain_failures"]:
            # Asking the AI takes seconds: do it, and send the notification, off the UI thread
            lines = list(log_lines)[-80:]

            def work():
                explanation = jobflow.explanation_for(self.settings, outcome, job, lines, self.base_dir, self.ai_factory)
                text = body + ("\n" + explanation if explanation else "")
                self._on_notified(notify.notify(self.settings, event, text, job))

            threading.Thread(target=work, daemon=True).start()
            return
        notify.notify_in_background(self.settings, event, body, job, on_result=self._on_notified)

    def _on_notified(self, results):
        # Called from the sending thread: the log is only touched from the UI thread
        for channel, error in results:
            if error:
                self.events.put(("line", f"⚠ Không gửi được thông báo qua {channel}: {error}"))

    @staticmethod
    def _remove_file(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        for timer in self._timers:
            self.root.after_cancel(timer)
        self._timers.clear()
        self.runner.stop()
        if self.task and self.task.get("content_file"):
            self._remove_file(self.task["content_file"])
        self.root.destroy()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = self.root
        root.title("Facebook Auto Poster")
        # Fonts follow the display scaling but pixel sizes do not, so sizes
        # given in pixels are scaled by hand (see _px)
        self._scale = max(1.0, root.winfo_fpixels("1i") / 96)
        width = min(self._px(960), root.winfo_screenwidth() - self._px(40))
        height = min(self._px(800), root.winfo_screenheight() - self._px(90))
        root.geometry(f"{width}x{height}")
        root.minsize(min(self._px(800), width), min(self._px(620), height))

        style = ttk.Style(root)
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")
        line_height = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Treeview", rowheight=line_height + self._px(8))

        bar = ttk.Frame(root)
        bar.pack(side="bottom", fill="x", padx=8, pady=6)
        self.status_var = tk.StringVar(master=root)
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        self.stop_button = ttk.Button(bar, text="Dừng tác vụ", command=self._on_stop_clicked)
        self.stop_button.pack(side="right")

        panes = ttk.Panedwindow(root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.notebook = ttk.Notebook(panes)
        panes.add(self.notebook, weight=4)
        log_frame = ttk.Labelframe(panes, text="Nhật ký")
        panes.add(log_frame, weight=1)
        self.log_widget = ScrolledText(
            log_frame, height=8, state="disabled", wrap="word", font=("Consolas", 9)
        )
        self.log_widget.pack(fill="both", expand=True, padx=4, pady=4)

        self._build_compose_tab()
        self._build_queue_tab()
        self._build_calendar_tab()
        self._build_ai_tab()
        self._build_library_tab()
        self._build_settings_tab()
        self._build_advanced_tab()

    def _px(self, pixels):
        return int(pixels * self._scale)

    def _build_compose_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Đăng bài  ")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)

        ttk.Label(tab, text="Đăng lên:").grid(row=0, column=0, sticky="nw", pady=3)
        targets = ttk.Frame(tab)
        targets.grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)
        targets.columnconfigure(0, weight=1)
        # The targets are picked in a popup (TargetDialog); this row only shows the result
        self._targets = []
        self.target_count_var = tk.StringVar(master=self.root)
        summary = ttk.Label(targets, textvariable=self.target_count_var, justify="left")
        summary.grid(row=0, column=0, sticky="new", pady=(2, 0))
        summary.bind("<Configure>", lambda event: summary.configure(wraplength=event.width))

        target_tools = ttk.Frame(targets)
        target_tools.grid(row=0, column=1, sticky="n", padx=(8, 0))
        ttk.Button(target_tools, text="Chọn nơi đăng…", command=self.open_target_dialog).grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        ttk.Label(target_tools, text="Giãn cách (phút):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.gap_var = tk.StringVar(master=self.root, value=str(self.settings["page_gap_minutes"]))
        gap_box = ttk.Spinbox(
            target_tools,
            textvariable=self.gap_var,
            from_=0,
            to=1440,
            width=5,
            command=self._on_gap_edited,
        )
        gap_box.grid(row=1, column=1, sticky="e", padx=(6, 0), pady=(6, 0))
        gap_box.bind("<FocusOut>", lambda event: self._on_gap_edited())
        gap_box.bind("<Return>", lambda event: self._on_gap_edited())
        ttk.Label(target_tools, text="Tài khoản:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.account_var = tk.StringVar(master=self.root, value=DEFAULT_ACCOUNT)
        self.account_box = ttk.Combobox(
            target_tools, textvariable=self.account_var, values=[DEFAULT_ACCOUNT], state="readonly", width=12
        )
        self.account_box.grid(row=2, column=1, sticky="e", padx=(6, 0), pady=(6, 0))

        ttk.Label(tab, text="Mẫu nội dung:").grid(row=1, column=0, sticky="w", pady=3)
        self.template_var = tk.StringVar(master=self.root)
        self.template_box = ttk.Combobox(tab, textvariable=self.template_var, state="readonly")
        self.template_box.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(tab, text="Chèn mẫu", command=self._on_insert_template_clicked).grid(
            row=1, column=2, padx=(6, 0)
        )
        ttk.Button(tab, text="Lưu thành mẫu…", command=self._on_save_as_template_clicked).grid(
            row=1, column=3, padx=(6, 0)
        )

        ttk.Label(tab, text="Nội dung:").grid(row=2, column=0, sticky="nw", pady=3)
        self.content_box = ScrolledText(tab, height=10, wrap="word", font=("Segoe UI", 10), undo=True)
        self.content_box.grid(row=2, column=1, columnspan=3, sticky="nsew", pady=3)
        self.content_box.bind("<KeyRelease>", lambda event: self._update_count())

        self.count_var = tk.StringVar(master=self.root)
        ttk.Label(tab, textvariable=self.count_var, style="Hint.TLabel").grid(
            row=3, column=1, columnspan=3, sticky="e"
        )
        self.ai_menu_button = ttk.Menubutton(tab, text="✨ AI giúp…")
        self.ai_menu_button.grid(row=3, column=1, sticky="w")
        ai_menu = tk.Menu(self.ai_menu_button, tearoff=False)
        for operation, label in AI_MENU:
            if operation is None:
                ai_menu.add_separator()
            else:
                ai_menu.add_command(label=label, command=lambda op=operation: self._on_ai_menu(op))
        self.ai_menu_button["menu"] = ai_menu

        ttk.Label(tab, text="Ảnh/video:").grid(row=4, column=0, sticky="w", pady=3)
        self.media_var = tk.StringVar(master=self.root)
        ttk.Entry(tab, textvariable=self.media_var).grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Button(
            tab, text="Chọn tệp…", command=lambda: self._choose_media(self.media_var)
        ).grid(row=4, column=2, padx=(6, 0))
        ttk.Button(tab, text="Bỏ tệp", command=lambda: self.media_var.set("")).grid(
            row=4, column=3, padx=(6, 0)
        )

        schedule = ttk.Labelframe(tab, text="Hẹn giờ", padding=8)
        schedule.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(10, 4))
        ttk.Label(schedule, text="Thời điểm:").pack(side="left")
        self.run_at_var = tk.StringVar(master=self.root, value=self._default_run_at())
        ttk.Entry(schedule, textvariable=self.run_at_var, width=18).pack(side="left", padx=(6, 4))
        ttk.Label(schedule, text="(YYYY-MM-DD HH:MM)", style="Hint.TLabel").pack(side="left")
        ttk.Label(schedule, text="Lặp:").pack(side="left", padx=(16, 0))
        self.repeat_var = tk.StringVar(master=self.root, value=REPEAT_LABELS["none"])
        self.repeat_box = ttk.Combobox(
            schedule, textvariable=self.repeat_var, values=repeat_choices(), state="readonly", width=16
        )
        self.repeat_box.pack(side="left", padx=6)
        self.repeat_box.bind("<<ComboboxSelected>>", lambda event: self._on_repeat_selected(self.repeat_var, self.root))
        ttk.Label(schedule, text="Lệch ±").pack(side="left", padx=(8, 0))
        self.jitter_var = tk.StringVar(master=self.root, value="0")
        ttk.Spinbox(schedule, textvariable=self.jitter_var, from_=0, to=720, width=4).pack(side="left", padx=(4, 0))
        ttk.Label(schedule, text="phút", style="Hint.TLabel").pack(side="left", padx=(2, 0))
        ttk.Button(schedule, text="Thêm vào hàng đợi", command=self._on_enqueue_clicked).pack(
            side="right"
        )

        self.publish_button = ttk.Button(
            tab, text="Đăng ngay", style="Accent.TButton", command=self._on_publish_now_clicked
        )
        self.publish_button.grid(row=6, column=0, columnspan=4, sticky="e", pady=(6, 0), ipadx=12, ipady=2)
        self._update_count()

    def _build_queue_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Hàng đợi  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        self.scheduler_var = tk.BooleanVar(master=self.root, value=self.settings["scheduler_enabled"])
        ttk.Checkbutton(
            tab,
            text="Tự động đăng khi đến giờ (ứng dụng phải đang mở)",
            variable=self.scheduler_var,
            command=lambda: self.set_scheduler_enabled(self.scheduler_var.get()),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        columns = ("run_at", "repeat", "page", "content", "status", "note", "link")
        headings = ("Giờ chạy", "Lặp", "Đăng lên", "Nội dung", "Trạng thái", "Ghi chú", "Link bài")
        widths = (120, 90, 110, 170, 70, 190, 140)
        self.queue_table = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for column, heading, width in zip(columns, headings, widths):
            self.queue_table.heading(column, text=heading)
            self.queue_table.column(
                column, width=self._px(width), stretch=column in ("content", "note")
            )
        for status, color in STATUS_COLORS.items():
            self.queue_table.tag_configure(status, foreground=color)
        self.queue_table.grid(row=1, column=0, sticky="nsew")
        self.queue_table.bind("<Double-1>", lambda event: self._on_edit_job_clicked())
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.queue_table.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.queue_table.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(tab)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.run_job_button = ttk.Button(buttons, text="Đăng ngay", command=self._on_run_job_clicked)
        self.run_job_button.pack(side="left")
        ttk.Button(buttons, text="Sửa…", command=self._on_edit_job_clicked).pack(side="left", padx=6)
        ttk.Button(buttons, text="Xóa", command=self._on_remove_job_clicked).pack(side="left")
        ttk.Button(buttons, text="Mở link bài", command=self._on_open_link_clicked).pack(side="left", padx=6)
        ttk.Button(buttons, text="✨ Giải thích lỗi", command=self._on_explain_clicked).pack(side="left")
        ttk.Button(buttons, text="Xóa các bài đã xong", command=self._on_clear_done_clicked).pack(
            side="right"
        )

    def _build_library_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Fanpage & Mẫu  ")
        tab.columnconfigure(0, weight=2, uniform="library")
        tab.columnconfigure(1, weight=3, uniform="library")
        tab.rowconfigure(0, weight=1)

        pages = ttk.Labelframe(tab, text="Fanpage", padding=8)
        pages.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        pages.columnconfigure(1, weight=1)
        pages.rowconfigure(0, weight=1)
        self.page_list = tk.Listbox(pages, exportselection=False, height=8)
        self.page_list.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        self.page_list.bind("<<ListboxSelect>>", self._on_page_list_selected)
        ttk.Label(pages, text="Tên:").grid(row=1, column=0, sticky="w", pady=2)
        self.page_name_var = tk.StringVar(master=self.root)
        ttk.Entry(pages, textvariable=self.page_name_var).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(pages, text="URL:").grid(row=2, column=0, sticky="w", pady=2)
        self.page_url_var = tk.StringVar(master=self.root)
        ttk.Entry(pages, textvariable=self.page_url_var).grid(row=2, column=1, sticky="ew", pady=2)
        ttk.Label(pages, text="Ví dụ: facebook.com/tentrang", style="Hint.TLabel").grid(
            row=3, column=1, sticky="w"
        )
        ttk.Label(pages, text="Giọng văn:").grid(row=5, column=0, sticky="w", pady=2)
        self.page_voice_var = tk.StringVar(master=self.root)
        ttk.Entry(pages, textvariable=self.page_voice_var).grid(row=5, column=1, sticky="ew", pady=2)
        ttk.Label(pages, text="Ví dụ: thân mật, xưng “mình - bạn” (AI dùng khi soạn bài)", style="Hint.TLabel").grid(
            row=6, column=1, sticky="w"
        )
        ttk.Label(pages, text="Ngôn ngữ:").grid(row=4, column=0, sticky="w", pady=2)
        self.page_language_var = tk.StringVar(master=self.root)
        # Not readonly: a language or country missing from the list can be typed in
        self.page_language_box = ttk.Combobox(
            pages, textvariable=self.page_language_var, values=LANGUAGES
        )
        self.page_language_box.grid(row=4, column=1, sticky="ew", pady=2)
        page_buttons = ttk.Frame(pages)
        page_buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(page_buttons, text="Lưu Fanpage", command=self._on_save_page_clicked).pack(side="left")
        ttk.Button(page_buttons, text="Xóa", command=self._on_remove_page_clicked).pack(
            side="left", padx=(6, 0)
        )

        templates = ttk.Labelframe(tab, text="Mẫu nội dung", padding=8)
        templates.grid(row=0, column=1, sticky="nsew")
        templates.columnconfigure(1, weight=1)
        templates.rowconfigure(2, weight=1)
        self.template_list = tk.Listbox(templates, exportselection=False, height=5)
        self.template_list.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.template_list.bind("<<ListboxSelect>>", self._on_template_list_selected)
        ttk.Label(templates, text="Tên mẫu:").grid(row=1, column=0, sticky="w", pady=2)
        self.template_name_var = tk.StringVar(master=self.root)
        ttk.Entry(templates, textvariable=self.template_name_var).grid(
            row=1, column=1, sticky="ew", pady=2
        )
        self.template_content_box = ScrolledText(
            templates, height=8, wrap="word", font=("Segoe UI", 10), undo=True
        )
        self.template_content_box.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=4)
        ttk.Label(templates, text="Ảnh/video:").grid(row=3, column=0, sticky="w", pady=2)
        media_row = ttk.Frame(templates)
        media_row.grid(row=3, column=1, sticky="ew", pady=2)
        media_row.columnconfigure(0, weight=1)
        self.template_media_var = tk.StringVar(master=self.root)
        ttk.Entry(media_row, textvariable=self.template_media_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            media_row, text="Chọn tệp…", command=lambda: self._choose_media(self.template_media_var)
        ).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(media_row, text="Bỏ tệp", command=lambda: self.template_media_var.set("")).grid(
            row=0, column=2, padx=(6, 0)
        )
        template_buttons = ttk.Frame(templates)
        template_buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(template_buttons, text="Dùng mẫu này", command=self._on_use_template_clicked).pack(
            side="left"
        )
        ttk.Button(template_buttons, text="Lưu mẫu", command=self._on_save_template_clicked).pack(
            side="left", padx=6
        )
        ttk.Button(template_buttons, text="Xóa", command=self._on_remove_template_clicked).pack(side="left")

    def _build_settings_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Cài đặt  ")
        tab.columnconfigure(0, weight=1)

        session = ttk.Labelframe(tab, text="Phiên đăng nhập Facebook", padding=10)
        session.grid(row=0, column=0, sticky="ew")
        self.session_label = ttk.Label(session, font=("Segoe UI", 10, "bold"))
        self.session_label.pack(anchor="w")
        ttk.Label(
            session,
            style="Hint.TLabel",
            justify="left",
            text=(
                "1. Bấm “Đăng nhập Facebook…”, một cửa sổ trình duyệt sẽ mở ra.\n"
                "2. Đăng nhập (kể cả xác thực 2 lớp) và đợi bảng tin hiện ra.\n"
                "3. Quay lại đây bấm “Tôi đã đăng nhập xong” để lưu phiên vào state.json.\n"
                "Tệp state.json chứa cookie đăng nhập: không chia sẻ cho bất kỳ ai."
            ),
        ).pack(anchor="w", pady=6)
        session_buttons = ttk.Frame(session)
        session_buttons.pack(anchor="w")
        self.login_button = ttk.Button(
            session_buttons, text="Đăng nhập Facebook…", command=self._on_login_clicked
        )
        self.login_button.pack(side="left")
        self.confirm_login_button = ttk.Button(
            session_buttons, text="Tôi đã đăng nhập xong", command=self.confirm_login
        )
        self.confirm_login_button.pack(side="left", padx=6)

        browser = ttk.Labelframe(tab, text="Trình duyệt", padding=10)
        browser.grid(row=1, column=0, sticky="ew", pady=10)
        self.headless_var = tk.BooleanVar(master=self.root, value=self.settings["headless"])
        ttk.Checkbutton(
            browser,
            text="Chạy ẩn trình duyệt khi đăng bài (headless)",
            variable=self.headless_var,
            command=lambda: self.set_headless(self.headless_var.get()),
        ).pack(anchor="w")
        ttk.Label(
            browser,
            style="Hint.TLabel",
            text="Nên tắt tùy chọn này khi mới dùng để nhìn thấy trình duyệt thao tác.",
        ).pack(anchor="w")

        check = ttk.Labelframe(tab, text="Kiểm tra kết quả", padding=10)
        check.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            check,
            style="Hint.TLabel",
            wraplength=self._px(860),
            justify="left",
            text=(
                "Trạng thái “Xong” nghĩa là Facebook đã nhận bài (hộp soạn bài đóng lại sau khi bấm Đăng). "
                "Nếu thấy cột “Link bài” có địa chỉ, bài đã hiện trên trang. Nhóm có kiểm duyệt vẫn có thể giữ bài chờ duyệt: "
                "mở ảnh kết quả hoặc vào Facebook để xem."
            ),
        ).pack(anchor="w", pady=(0, 6))
        check_buttons = ttk.Frame(check)
        check_buttons.pack(anchor="w")
        ttk.Button(
            check_buttons,
            text="Mở ảnh kết quả gần nhất",
            command=lambda: self._open_path(os.path.join(self.base_dir, "debug", "latest_result.png")),
        ).pack(side="left")
        ttk.Button(
            check_buttons, text="Mở thư mục dự án", command=lambda: self._open_path(self.base_dir)
        ).pack(side="left", padx=6)

    # ------------------------------------------------------------------
    # Calendar and advanced tabs
    # ------------------------------------------------------------------

    def _calendar_label(self, job):
        preview = " ".join(job["content"].split())[:30] or "(ảnh/video)"
        return f"{self._page_label(job['page_url'])}: {preview}"

    def _build_calendar_tab(self):
        self.calendar = calendar_tab.CalendarTab(
            self.notebook,
            get_jobs=lambda: self.jobs,
            move_job=self.move_job,
            open_job=self._open_job_by_id,
            label_for=self._calendar_label,
            px=self._px,
            on_error=lambda message: self._error(message),
        )
        self.notebook.add(self.calendar, text="  Lịch  ")

    def _build_advanced_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="  Nâng cao  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        inner = ttk.Notebook(tab)
        inner.grid(row=0, column=0, sticky="nsew")
        self._build_accounts_page(inner)
        self._build_notify_page(inner)
        self._build_safety_page(inner)
        self._build_ai_settings_page(inner)
        self._build_backup_page(inner)
        self._sync_advanced_widgets()

    def _build_accounts_page(self, inner):
        page = ttk.Frame(inner, padding=10)
        inner.add(page, text="  Tài khoản  ")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        ttk.Label(
            page,
            style="Hint.TLabel",
            justify="left",
            wraplength=self._px(820),
            text=(
                "Mỗi tài khoản Facebook có một phiên đăng nhập riêng (state.<tên>.json). Chọn tài khoản dùng để đăng ở "
                "tab Đăng bài; bài hẹn giờ nhớ tài khoản đã chọn. Tài khoản (Mặc định) là phiên ở tab Cài đặt."
            ),
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.account_list = tk.Listbox(page, exportselection=False, height=6)
        self.account_list.grid(row=1, column=0, sticky="nsew")
        buttons = ttk.Frame(page)
        buttons.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Thêm tài khoản…", command=self._on_add_account_clicked).pack(side="left")
        ttk.Button(buttons, text="Xóa khỏi danh sách", command=self._on_remove_account_clicked).pack(
            side="left", padx=6
        )
        self.account_login_button = ttk.Button(
            buttons, text="Đăng nhập tài khoản đã chọn…", command=self._on_login_account_clicked
        )
        self.account_login_button.pack(side="left")
        self.confirm_account_login_button = ttk.Button(
            buttons, text="Tôi đã đăng nhập xong", command=self.confirm_login
        )
        self.confirm_account_login_button.pack(side="left", padx=6)

    def _build_notify_page(self, inner):
        page = ttk.Frame(inner, padding=10)
        inner.add(page, text="  Thông báo  ")
        page.columnconfigure(1, weight=1)
        ttk.Label(
            page,
            style="Hint.TLabel",
            justify="left",
            wraplength=self._px(820),
            text=(
                "Nhận tin khi bài đăng xong, lỗi hoặc phiên đăng nhập hết hạn. Telegram: tạo bot bằng @BotFather rồi dán "
                "token và chat ID. Webhook nhận JSON, dùng để nối tới Zalo/Make/n8n. Token và mật khẩu được lưu trong "
                "config.json (không đưa vào git); có thể đặt biến môi trường TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "
                "NOTIFY_WEBHOOK_URL, SMTP_PASSWORD thay thế."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.notify_vars = {}
        fields = [
            ("telegram_token", "Telegram bot token:", "*"),
            ("telegram_chat", "Telegram chat ID:", None),
            ("webhook", "Webhook URL:", None),
            ("email_host", "Email SMTP máy chủ:", None),
            ("email_port", "Cổng SMTP:", None),
            ("email_user", "Tài khoản email:", None),
            ("email_password", "Mật khẩu email:", "*"),
            ("email_sender", "Gửi từ (From):", None),
            ("email_to", "Gửi tới (To):", None),
        ]
        for row, (key, label, secret) in enumerate(fields, start=1):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=2)
            variable = tk.StringVar(master=self.root)
            self.notify_vars[key] = variable
            ttk.Entry(page, textvariable=variable, show=secret or "").grid(row=row, column=1, sticky="ew", pady=2)
        events = ttk.Labelframe(page, text="Báo khi", padding=6)
        events.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.notify_event_vars = {}
        for event, label in NOTIFY_EVENT_LABELS.items():
            variable = tk.BooleanVar(master=self.root)
            self.notify_event_vars[event] = variable
            ttk.Checkbutton(events, text=label, variable=variable).pack(side="left", padx=(0, 12))
        buttons = ttk.Frame(page)
        buttons.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Lưu", command=self._on_save_notifications_clicked).pack(side="left")
        ttk.Button(buttons, text="Lưu và gửi thử", command=self._on_test_notification_clicked).pack(side="left", padx=6)

    def _build_safety_page(self, inner):
        page = ttk.Frame(inner, padding=10)
        inner.add(page, text="  An toàn & thử lại  ")
        page.columnconfigure(1, weight=1)
        ttk.Label(
            page,
            style="Hint.TLabel",
            justify="left",
            wraplength=self._px(820),
            text=(
                "Chỉ thử lại khi chắc chắn bài chưa được đăng (lỗi trước khi bấm nút Đăng); lỗi sau đó không bao giờ "
                "thử lại để tránh đăng trùng. Chặn đăng khi nội dung giống bài đã đăng gần đây lên cùng nơi, hoặc đã "
                "đăng quá nhiều trong 24 giờ (đặt 0 để tắt từng giới hạn)."
            ),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.safety_vars = {}
        fields = [
            ("retry_attempts", "Số lần thử lại tối đa:", "lần"),
            ("retry_delay_minutes", "Chờ giữa các lần thử:", "phút"),
            ("grace_minutes", "Bài trễ quá bao lâu thì tính là Lỡ:", "phút"),
            ("duplicate_days", "Coi là trùng nếu giống bài trong:", "ngày gần đây (0 = không kiểm tra)"),
            ("similarity_percent", "Mức giống nhau coi là trùng:", "% (50–100)"),
            ("max_per_day", "Tối đa bài mỗi 24 giờ (tất cả nơi):", "bài (0 = không giới hạn)"),
            ("max_per_page_per_day", "Tối đa bài mỗi 24 giờ cho một nơi:", "bài (0 = không giới hạn)"),
        ]
        for row, (key, label, unit) in enumerate(fields, start=1):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=3)
            variable = tk.StringVar(master=self.root)
            self.safety_vars[key] = variable
            ttk.Spinbox(page, textvariable=variable, from_=0, to=100000, width=7).grid(
                row=row, column=1, sticky="w", padx=6, pady=3
            )
            ttk.Label(page, text=unit, style="Hint.TLabel").grid(row=row, column=2, sticky="w")
        ttk.Button(page, text="Lưu", command=self._on_save_safety_clicked).grid(
            row=len(fields) + 1, column=0, sticky="w", pady=(10, 0)
        )

    def _build_backup_page(self, inner):
        page = ttk.Frame(inner, padding=10)
        inner.add(page, text="  Sao lưu & chạy nền  ")
        backup_box = ttk.Labelframe(page, text="Sao lưu", padding=10)
        backup_box.pack(fill="x")
        ttk.Label(
            backup_box,
            style="Hint.TLabel",
            justify="left",
            wraplength=self._px(820),
            text=(
                "Xuất Fanpage, mẫu, cài đặt và hàng đợi ra một tệp để chuyển sang máy khác. Phiên đăng nhập "
                "(state.json) không bao giờ được đưa vào; token và mật khẩu thông báo cũng được bỏ ra."
            ),
        ).pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(backup_box)
        row.pack(anchor="w")
        ttk.Button(row, text="Xuất ra tệp…", command=self._on_export_backup_clicked).pack(side="left")
        ttk.Button(row, text="Nhập (gộp)…", command=lambda: self._on_import_backup_clicked(False)).pack(
            side="left", padx=6
        )
        ttk.Button(row, text="Nhập (ghi đè)…", command=lambda: self._on_import_backup_clicked(True)).pack(side="left")

        daemon_box = ttk.Labelframe(page, text="Chạy nền không cần mở cửa sổ", padding=10)
        daemon_box.pack(fill="x", pady=(10, 0))
        ttk.Label(
            daemon_box,
            style="Hint.TLabel",
            justify="left",
            wraplength=self._px(820),
            text=(
                "Đóng cửa sổ này rồi chạy trong thư mục dự án:\n"
                "    python daemon.py run             đăng các bài đến giờ (Ctrl+C để dừng)\n"
                "    python daemon.py install-task    tự chạy mỗi khi đăng nhập Windows\n"
                "    python daemon.py status          xem đang chạy hay không\n"
                "Chỉ một trong cửa sổ này và chương trình nền được chạy cùng lúc. Bài thêm bằng enqueue.py được "
                "chương trình nền nhận ngay."
            ),
        ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Accounts, notifications, safety settings, backup
    # ------------------------------------------------------------------

    def selected_account(self):
        """The account chosen in the compose tab: "" for the default one."""
        value = self.account_var.get()
        return "" if value in ("", DEFAULT_ACCOUNT) else value

    def add_account(self, name):
        name = name.strip()
        if not name or name == "default":
            raise ValueError("Hãy nhập tên cho tài khoản (không dùng “default”).")
        store.state_path_for(self.base_dir, name)  # rejects names that cannot be a file name
        if name in self.settings["accounts"]:
            raise ValueError(f"Tài khoản “{name}” đã có.")
        self.settings["accounts"].append(name)
        self._save_settings()
        self._refresh_accounts()

    def remove_account(self, name):
        """Forget an account. Its saved login file stays on disk."""
        if name not in self.settings["accounts"]:
            raise ValueError("Không có tài khoản này.")
        self.settings["accounts"].remove(name)
        self._save_settings()
        self._refresh_accounts()

    def _refresh_accounts(self):
        names = [DEFAULT_ACCOUNT] + self.settings["accounts"]
        self.account_box["values"] = names
        if self.account_var.get() not in names:
            self.account_var.set(DEFAULT_ACCOUNT)
        self.account_list.delete(0, "end")
        for account in [""] + self.settings["accounts"]:
            saved = store.session_saved_at(self.state_path_for(account))
            state = f"Đã đăng nhập (phiên lưu lúc {saved:%Y-%m-%d %H:%M})" if saved else "Chưa đăng nhập"
            self.account_list.insert("end", f"{account or DEFAULT_ACCOUNT}  —  {state}")

    def _account_chosen_in_list(self):
        chosen = self.account_list.curselection()
        accounts = [""] + self.settings["accounts"]
        return accounts[chosen[0]] if chosen else None

    def _on_add_account_clicked(self):
        name = simpledialog.askstring(
            "Thêm tài khoản", "Tên tài khoản (chữ, số, gạch ngang hoặc gạch dưới):", parent=self.root
        )
        if name is None:
            return
        try:
            self.add_account(name)
        except ValueError as error:
            self._error(error)
        else:
            self.log(f"Đã thêm tài khoản “{name.strip()}”. Bấm “Đăng nhập tài khoản đã chọn…” để đăng nhập.")

    def _on_remove_account_clicked(self):
        account = self._account_chosen_in_list()
        if not account:
            self._error("Hãy chọn một tài khoản phụ trong danh sách (không xóa được tài khoản mặc định).")
            return
        if messagebox.askyesno(
            "Xóa tài khoản",
            f"Bỏ “{account}” khỏi danh sách? Tệp phiên đăng nhập của nó không bị xóa.",
            parent=self.root,
        ):
            self.remove_account(account)

    def _on_login_account_clicked(self):
        account = self._account_chosen_in_list()
        if account is None:
            self._error("Hãy chọn một tài khoản trong danh sách.")
            return
        try:
            self.start_login(account)
        except (RuntimeError, ValueError) as error:
            self._error(error)

    def set_notifications(self, values):
        """Save the notification settings. `values` uses the keys of the fields in the Thông báo page."""
        try:
            port = int(str(values.get("email_port", "587")).strip() or 587)
        except ValueError:
            raise ValueError("Cổng SMTP phải là một số.") from None
        events = [event for event in values.get("events", []) if event in notify.EVENTS]
        self.settings["notifications"] = {
            "events": events,
            "telegram": {
                "bot_token": values.get("telegram_token", "").strip(),
                "chat_id": values.get("telegram_chat", "").strip(),
            },
            "webhook_url": values.get("webhook", "").strip(),
            "email": {
                "host": values.get("email_host", "").strip(),
                "port": port,
                "user": values.get("email_user", "").strip(),
                "password": values.get("email_password", ""),
                "sender": values.get("email_sender", "").strip(),
                "to": values.get("email_to", "").strip(),
                "starttls": self.settings["notifications"]["email"].get("starttls", True),
            },
        }
        self._save_settings()

    def send_test_notification(self):
        """Send a test message to every configured channel. Raises RuntimeError if there is none."""
        if not notify.channels(notify.settings_from(self.settings)):
            raise RuntimeError("Chưa cấu hình kênh thông báo nào (Telegram, webhook hoặc email).")
        notify.notify_in_background(
            self.settings, "test", notify.describe("test"), None, on_result=self._on_test_notified
        )

    def _on_test_notified(self, results):
        for channel, error in results:
            line = f"✔ Đã gửi thông báo thử qua {channel}" if error is None else f"⚠ Không gửi được qua {channel}: {error}"
            self.events.put(("line", line))

    def _notify_values(self):
        values = {key: variable.get() for key, variable in self.notify_vars.items()}
        values["events"] = [event for event, variable in self.notify_event_vars.items() if variable.get()]
        return values

    def _on_save_notifications_clicked(self):
        try:
            self.set_notifications(self._notify_values())
        except ValueError as error:
            self._error(error)
        else:
            self.log("Đã lưu cài đặt thông báo")

    def _on_test_notification_clicked(self):
        try:
            self.set_notifications(self._notify_values())
            self.send_test_notification()
        except (ValueError, RuntimeError) as error:
            self._error(error)
        else:
            self.log("Đang gửi thông báo thử…")

    def set_safety(self, values):
        """Save the retry, late-post and spam settings. Raises ValueError for anything that is not a sensible number."""
        numbers = {}
        for key in ("retry_attempts", "retry_delay_minutes", "grace_minutes", "duplicate_days", "similarity_percent",
                    "max_per_day", "max_per_page_per_day"):
            try:
                numbers[key] = int(str(values[key]).strip())
            except (KeyError, ValueError):
                raise ValueError("Mọi ô trong mục này phải là số nguyên từ 0 trở lên.") from None
            if numbers[key] < 0:
                raise ValueError("Mọi ô trong mục này phải là số nguyên từ 0 trở lên.")
        if not 50 <= numbers["similarity_percent"] <= 100:
            raise ValueError("Mức giống nhau phải từ 50 đến 100 (%).")
        self.settings["retry_attempts"] = numbers["retry_attempts"]
        self.settings["retry_delay_minutes"] = numbers["retry_delay_minutes"]
        self.settings["grace_minutes"] = numbers["grace_minutes"]
        self.settings["spam"] = {
            "duplicate_days": numbers["duplicate_days"],
            "similarity": numbers["similarity_percent"] / 100,
            "max_per_day": numbers["max_per_day"],
            "max_per_page_per_day": numbers["max_per_page_per_day"],
        }
        self._save_settings()

    def _on_save_safety_clicked(self):
        try:
            self.set_safety({key: variable.get() for key, variable in self.safety_vars.items()})
        except ValueError as error:
            self._error(error)
        else:
            self.log("Đã lưu cài đặt an toàn và thử lại")

    def _sync_advanced_widgets(self):
        """Show the saved settings in the Nâng cao tab's boxes."""
        notifications = self.settings["notifications"]
        email = notifications["email"]
        shown = {
            "telegram_token": notifications["telegram"]["bot_token"],
            "telegram_chat": notifications["telegram"]["chat_id"],
            "webhook": notifications["webhook_url"],
            "email_host": email["host"],
            "email_port": str(email["port"]),
            "email_user": email["user"],
            "email_password": email["password"],
            "email_sender": email["sender"],
            "email_to": email["to"],
        }
        for key, value in shown.items():
            self.notify_vars[key].set(value)
        for event, variable in self.notify_event_vars.items():
            variable.set(event in notifications["events"])
        spam_settings = self.settings["spam"]
        shown = {
            "retry_attempts": self.settings["retry_attempts"],
            "retry_delay_minutes": self.settings["retry_delay_minutes"],
            "grace_minutes": self.settings["grace_minutes"],
            "duplicate_days": spam_settings["duplicate_days"],
            "similarity_percent": round(spam_settings["similarity"] * 100),
            "max_per_day": spam_settings["max_per_day"],
            "max_per_page_per_day": spam_settings["max_per_page_per_day"],
        }
        for key, value in shown.items():
            self.safety_vars[key].set(str(value))
        ai_settings = self.settings["ai"]
        for key in ("api_key", "model", "effort"):
            self.ai_vars[key].set(ai_settings[key])
        self.ai_vars["fallbacks"].set(bool(ai_settings["fallbacks"]))
        self.ai_vars["explain_failures"].set(bool(ai_settings["explain_failures"]))
        self.ai_voice_box.delete("1.0", "end")
        self.ai_voice_box.insert("1.0", ai_settings["voice"])

    def export_backup(self, path, include_secrets=False):
        """Write pages, templates, settings and the queue to `path`. Returns the data written."""
        return backup.write_export(self.base_dir, path, include_secrets=include_secrets)

    def import_backup(self, path, replace=False):
        """Merge (or, with `replace`, overwrite with) a backup file. Returns a report of what changed."""
        try:
            data = backup.read_backup(path)
        except backup.BackupError as error:
            raise ValueError(str(error)) from None
        config, jobs, report = backup.merge_data(self.base_dir, data, replace)
        self.settings, self.jobs = config, jobs
        self._save_settings()
        self._save_queue()
        self._refresh_pages()
        self._refresh_templates()
        self._refresh_queue()
        self._refresh_accounts()
        self._sync_advanced_widgets()
        self.headless_var.set(self.settings["headless"])
        self.scheduler_var.set(self.settings["scheduler_enabled"])
        self.gap_var.set(str(self.settings["page_gap_minutes"]))
        return report

    def _on_export_backup_clicked(self):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Xuất sao lưu",
            defaultextension=".json",
            initialfile=f"sao-luu-{datetime.now():%Y%m%d}.json",
            filetypes=[("Tệp sao lưu", "*.json")],
        )
        if not path:
            return
        try:
            data = self.export_backup(path)
        except OSError as error:
            self._error(f"Không ghi được tệp: {error}")
            return
        self.log(
            f"Đã xuất {len(data['config']['pages'])} Fanpage, {len(data['config']['templates'])} mẫu, "
            f"{len(data['queue'])} bài hẹn ra {path}"
        )

    def _on_import_backup_clicked(self, replace):
        path = filedialog.askopenfilename(
            parent=self.root, title="Nhập sao lưu", filetypes=[("Tệp sao lưu", "*.json"), ("Tất cả", "*.*")]
        )
        if not path:
            return
        if replace and not messagebox.askyesno(
            "Ghi đè",
            "Ghi đè Fanpage, mẫu, cài đặt và hàng đợi hiện tại bằng tệp này?",
            icon="warning",
            parent=self.root,
        ):
            return
        try:
            report = self.import_backup(path, replace)
        except (ValueError, OSError) as error:
            self._error(error)
            return
        self.log(
            f"Đã nhập: {report['jobs_added']} bài hẹn (bỏ qua {report['jobs_skipped']}), "
            f"{report['pages']} Fanpage, {report['templates']} mẫu"
        )
        if report["missing_files"]:
            self.log("⚠ Chưa có các tệp đính kèm sau trên máy này: " + "; ".join(report["missing_files"]))

    # ------------------------------------------------------------------
    # Small helpers for the repeat and jitter boxes
    # ------------------------------------------------------------------

    @staticmethod
    def _read_jitter(text):
        try:
            minutes = int(str(text).strip() or 0)
        except ValueError:
            raise ValueError("Độ lệch giờ phải là số phút nguyên.") from None
        if minutes < 0:
            raise ValueError("Độ lệch giờ không được âm.")
        return minutes

    def _on_repeat_selected(self, variable, parent):
        """The “Chọn các thứ…” entry asks which weekdays; the box then shows them (e.g. “Thứ 2, Thứ 4”)."""
        if variable.get() != CUSTOM_DAYS_LABEL:
            return
        days = self._ask_days(parent)
        variable.set(repeat_label(scheduler.days_repeat(days)) if days else REPEAT_LABELS["none"])

    def _ask_days(self, parent):
        """Let the user tick weekdays. Returns a set of weekday numbers (0 = Monday), or None if cancelled."""
        dialog = tk.Toplevel(parent)
        dialog.title("Lặp vào các thứ")
        dialog.transient(parent)
        variables = []
        for index, name in enumerate(scheduler.WEEKDAY_NAMES):
            variable = tk.BooleanVar(master=dialog, value=index < 5)
            variables.append(variable)
            ttk.Checkbutton(dialog, text=name, variable=variable).grid(row=index, column=0, sticky="w", padx=14, pady=1)
        chosen = {"days": None}

        def ok():
            days = {index for index, variable in enumerate(variables) if variable.get()}
            if not days:
                messagebox.showinfo("Chưa chọn", "Hãy tích ít nhất một ngày.", parent=dialog)
                return
            chosen["days"] = days
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=7, column=0, pady=10)
        ttk.Button(buttons, text="OK", style="Accent.TButton", command=ok).pack(side="left")
        ttk.Button(buttons, text="Hủy", command=dialog.destroy).pack(side="left", padx=6)
        dialog.grab_set()
        dialog.wait_window()
        return chosen["days"]

    # ------------------------------------------------------------------
    # AI helpers: logic
    # ------------------------------------------------------------------

    def run_ai(self, work, done):
        """Run `work(engine)` on a thread. `done(result, error)` is then called on the UI thread, with `error` the
        message of an AIError (or None). Raises RuntimeError if an earlier request is still running."""
        if self._ai_busy:
            raise RuntimeError("AI đang xử lý yêu cầu trước, hãy đợi một chút.")
        self._ai_busy = True
        self._update_controls()
        engine = self.ai_factory()

        def worker():
            result, error = None, None
            try:
                result = work(engine)
            except (ai_module.AIError, ValueError) as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - shown to the user instead of a silent thread death
                error = f"Lỗi không mong đợi: {type(exc).__name__}: {exc}"
            self.events.put(("call", lambda: self._finish_ai(engine, result, error, done)))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_ai(self, engine, result, error, done):
        self._ai_busy = False
        self._update_controls()
        usage = engine.describe_usage() if hasattr(engine, "describe_usage") else ""
        if usage:
            self.log(usage)
        if error:
            self.log(f"✖ AI: {error}")
        done(result, error)

    def _ai_targets(self, names):
        """The pages named (or the personal profile) as the AI features want them."""
        targets = []
        for name in names:
            if name == PERSONAL:
                targets.append({"name": PERSONAL, "language": ai_features.DEFAULT_LANGUAGE})
                continue
            page = next((item for item in self.settings["pages"] if item["name"] == name), None)
            if page is None:
                raise ValueError(f"Không có Fanpage “{name}”.")
            targets.append(
                {"name": name, "language": page.get("language", ""), "voice": page.get("voice", ""), "url": page["url"]}
            )
        return targets

    def ai_draft(self, idea, tone, length, details, names, done):
        targets = self._ai_targets(names)
        self.run_ai(lambda engine: ai_features.draft_posts(engine, idea, targets, tone, length, details), done)

    def ai_localize(self, content, names, done):
        targets = self._ai_targets(names)
        self.run_ai(lambda engine: ai_features.localize(engine, content, targets), done)

    def ai_variants(self, content, count, done):
        self.run_ai(lambda engine: ai_features.variants(engine, content, count), done)

    def ai_polish(self, operation, content, done):
        self.run_ai(lambda engine: ai_features.polish(engine, content, operation), done)

    def ai_review(self, content, done):
        self.run_ai(lambda engine: ai_features.review(engine, content), done)

    def ai_series(self, topic, count, name, tone, done):
        target = self._ai_targets([name])[0]
        self.run_ai(lambda engine: ai_features.plan_series(engine, topic, count, target, tone), done)

    def ai_times(self, name, done):
        target = self._ai_targets([name])[0]
        records = history.load(os.path.join(self.base_dir, "history.jsonl"))
        self.run_ai(lambda engine: ai_features.suggest_times(engine, target, records, datetime.now()), done)

    def ai_reply(self, comment, post_text, tone, done):
        self.run_ai(lambda engine: ai_features.suggest_replies(engine, comment, post_text, tone), done)

    def ai_describe(self, image_path, done):
        self.run_ai(lambda engine: ai_features.describe_image(engine, image_path), done)

    def ai_card(self, content, done):
        """Design and draw a text picture for `content`; done gets {"card": spec, "file": path}."""
        out_dir = os.path.join(self.base_dir, "cards")

        def work(engine):
            import cards

            spec = ai_features.design_card(engine, content)
            file = cards.render_card(spec, os.path.join(out_dir, f"the-{datetime.now():%Y%m%d-%H%M%S}.png"))
            return {"card": spec, "file": file}

        self.run_ai(work, done)

    def _log_tail(self, count=80):
        lines = self.log_widget.get("1.0", "end").splitlines()
        return [line for line in lines if line.strip()][-count:]

    def ai_explain(self, job, done):
        """Explain why `job` failed, using the log and the newest screenshot."""
        message = job["last_message"]
        found = re.search(r"mã thoát (\d+)", message)
        exit_code = int(found.group(1)) if found else None
        shots = os.path.join(self.base_dir, "debug")
        newest = None
        try:
            paths = [os.path.join(shots, name) for name in os.listdir(shots) if name.startswith("latest_") and name.endswith(".png")]
            newest = max(paths, key=os.path.getmtime) if paths else None
        except OSError:
            pass
        lines = self._log_tail()
        self.run_ai(lambda engine: ai_features.explain_failure(engine, message, lines, exit_code, newest), done)

    def ai_assistant(self, message, done):
        """One turn with the scheduling assistant. The conversation is kept for the next turn."""
        pages = [{"name": page["name"], "language": page.get("language", ""), "voice": page.get("voice", "")}
                 for page in self.settings["pages"]]
        pages.insert(0, {"name": PERSONAL, "language": ai_features.DEFAULT_LANGUAGE})
        history_so_far = list(self.assistant_history)
        templates = [template["name"] for template in self.settings["templates"]]

        def finished(result, error):
            if result is not None:
                self.assistant_history = result["history"]
                self.assistant_proposals = result["proposals"]
            done(result, error)

        self.run_ai(
            lambda engine: ai_features.assistant_turn(engine, message, history_so_far, pages, datetime.now(), templates),
            finished,
        )

    def reset_assistant(self):
        self.assistant_history = []
        self.assistant_proposals = []

    def queue_ai_posts(self, items, run_at, image_path="", gap_minutes=None):
        """Queue posts written by the AI: `items` are {"name": page name, "content": text}, each to its own page, the
        first at `run_at` and the rest `gap_minutes` apart. Returns the new jobs."""
        gap = self.settings["page_gap_minutes"] if gap_minutes is None else gap_minutes
        moments = scheduler.stagger(run_at, len(items), gap)
        account = self.selected_account()
        jobs = [
            scheduler.new_job(item["content"], "" if item["name"] == PERSONAL else self._page_url(item["name"]),
                              image_path, moment, "none", account)
            for item, moment in zip(items, moments)
        ]
        self.jobs.extend(jobs)
        self._save_queue()
        self._refresh_queue()
        return jobs

    def apply_proposals(self, proposals, gap_minutes=None):
        """Queue what the scheduling assistant proposed (after the user agreed). Returns the new jobs."""
        gap = self.settings["page_gap_minutes"] if gap_minutes is None else gap_minutes
        account = self.selected_account()
        jobs = []
        for proposal in proposals:
            start = scheduler.parse_run_at(proposal["at"])
            for page_name, moment in zip(proposal["pages"], scheduler.stagger(start, len(proposal["pages"]), gap)):
                url = "" if page_name == PERSONAL else self._page_url(page_name)
                jobs.append(scheduler.new_job(proposal["content"], url, "", moment, proposal.get("repeat", "none"), account))
        self.jobs.extend(jobs)
        self._save_queue()
        self._refresh_queue()
        return jobs

    def set_ai_settings(self, values):
        """Save the AI settings (key, model, effort, switches, house style)."""
        effort = values.get("effort", "medium")
        if effort not in ai_module.EFFORTS:
            raise ValueError(f"Mức effort phải là một trong: {', '.join(ai_module.EFFORTS)}.")
        model = values.get("model", "").strip()
        if not model:
            raise ValueError("Hãy chọn mô hình.")
        self.settings["ai"] = {
            "api_key": values.get("api_key", "").strip(),
            "model": model,
            "effort": effort,
            "fallbacks": bool(values.get("fallbacks", True)),
            "voice": values.get("voice", "").strip(),
            "explain_failures": bool(values.get("explain_failures", False)),
        }
        self._save_settings()

    def test_ai_connection(self, done):
        self.run_ai(lambda engine: {"reply": ai_module.check(engine), "model": engine.model}, done)

    # ------------------------------------------------------------------
    # AI helpers: tabs and dialogs
    # ------------------------------------------------------------------

    def _scrolled(self, parent, height, **options):
        return ScrolledText(parent, height=height, wrap="word", font=("Segoe UI", 10), undo=True, **options)

    def _build_ai_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="  Trợ lý AI  ")
        self.ai_tab = tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(
            tab,
            style="Hint.TLabel",
            wraplength=self._px(860),
            justify="left",
            text=(
                "Claude soạn nháp và đề xuất; bạn xem lại rồi mới đăng. Nội dung bạn nhờ AI xử lý (bài viết, ảnh, nhật ký lỗi) "
                "được gửi tới Anthropic và tốn phí theo khóa API của bạn: nhập khóa ở Nâng cao → Trợ lý AI."
            ),
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.ai_inner = ttk.Notebook(tab)
        self.ai_inner.grid(row=1, column=0, sticky="nsew")
        self._build_ai_write_page(self.ai_inner)
        self._build_ai_series_page(self.ai_inner)
        self._build_ai_assistant_page(self.ai_inner)
        self._build_ai_tools_page(self.ai_inner)

    def _build_ai_write_page(self, inner):
        page = ttk.Frame(inner, padding=8)
        inner.add(page, text="  Soạn bài  ")
        page.columnconfigure(1, weight=1)
        page.rowconfigure(5, weight=1)
        ttk.Label(page, text="Ý tưởng:").grid(row=0, column=0, sticky="nw", pady=2)
        self.ai_idea_box = self._scrolled(page, 4)
        self.ai_idea_box.grid(row=0, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(page, text="Giọng điệu:").grid(row=1, column=0, sticky="w", pady=2)
        self.ai_tone_var = tk.StringVar(master=self.root)
        ttk.Entry(page, textvariable=self.ai_tone_var).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(page, text="Độ dài:").grid(row=1, column=2, sticky="e", padx=(10, 4))
        self.ai_length_var = tk.StringVar(master=self.root, value="Vừa")
        ttk.Combobox(page, textvariable=self.ai_length_var, values=list(LENGTH_LABELS), state="readonly", width=8).grid(
            row=1, column=3, sticky="e"
        )
        ttk.Label(page, text="Thông tin có thật:").grid(row=2, column=0, sticky="w", pady=2)
        self.ai_details_var = tk.StringVar(master=self.root)
        ttk.Entry(page, textvariable=self.ai_details_var).grid(row=2, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(
            page, style="Hint.TLabel", text="Giá, ngày, địa chỉ… AI chỉ được dùng đúng những gì ghi ở đây, không tự bịa thêm."
        ).grid(row=3, column=1, columnspan=3, sticky="w")
        action_row = ttk.Frame(page)
        action_row.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 4))
        self.ai_write_button = ttk.Button(action_row, text="✨ Soạn bài cho các nơi đã chọn", style="Accent.TButton",
                                          command=self._on_ai_write_clicked)
        self.ai_write_button.pack(side="left")
        ttk.Label(action_row, style="Hint.TLabel", text="  (chọn nơi đăng ở tab Đăng bài; mỗi nơi một bản theo ngôn ngữ và giọng văn của nó)").pack(side="left")
        self.ai_draft_table = ttk.Treeview(page, columns=("name", "preview"), show="headings", height=4, selectmode="browse")
        self.ai_draft_table.heading("name", text="Nơi đăng")
        self.ai_draft_table.heading("preview", text="Bản nháp")
        self.ai_draft_table.column("name", width=self._px(180), stretch=False)
        self.ai_draft_table.column("preview", width=self._px(600))
        self.ai_draft_table.grid(row=5, column=0, columnspan=4, sticky="nsew")
        self.ai_draft_table.bind("<<TreeviewSelect>>", lambda event: self._on_draft_selected())
        self.ai_draft_box = self._scrolled(page, 8)
        self.ai_draft_box.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        self.ai_draft_box.bind("<KeyRelease>", lambda event: self._on_draft_edited())
        page.rowconfigure(6, weight=2)
        buttons = ttk.Frame(page)
        buttons.grid(row=7, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="Dùng bản này ở tab Đăng bài", command=self._on_use_draft_clicked).pack(side="left")
        ttk.Button(buttons, text="Thêm tất cả vào hàng đợi (giờ ở tab Đăng bài)", command=self._on_queue_drafts_clicked).pack(
            side="left", padx=6
        )

    def _build_ai_series_page(self, inner):
        page = ttk.Frame(inner, padding=8)
        inner.add(page, text="  Chuỗi bài  ")
        page.columnconfigure(1, weight=1)
        page.rowconfigure(5, weight=1)
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.series_vars = {
            "topic": tk.StringVar(master=self.root),
            "count": tk.StringVar(master=self.root, value="7"),
            "page": tk.StringVar(master=self.root, value=PERSONAL),
            "tone": tk.StringVar(master=self.root),
            "start": tk.StringVar(master=self.root, value=tomorrow),
            "time": tk.StringVar(master=self.root, value="08:30"),
            "every": tk.StringVar(master=self.root, value="1"),
        }
        ttk.Label(page, text="Chủ đề:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(page, textvariable=self.series_vars["topic"]).grid(row=0, column=1, columnspan=5, sticky="ew", pady=2)
        ttk.Label(page, text="Đăng lên:").grid(row=1, column=0, sticky="w", pady=2)
        self.series_page_box = ttk.Combobox(page, textvariable=self.series_vars["page"], values=[PERSONAL], state="readonly")
        self.series_page_box.grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(page, text="Số bài:").grid(row=1, column=2, sticky="e", padx=(10, 4))
        ttk.Spinbox(page, textvariable=self.series_vars["count"], from_=1, to=ai_features.MAX_SERIES, width=5).grid(row=1, column=3, sticky="w")
        ttk.Label(page, text="Giọng điệu:").grid(row=1, column=4, sticky="e", padx=(10, 4))
        ttk.Entry(page, textvariable=self.series_vars["tone"], width=18).grid(row=1, column=5, sticky="w")
        ttk.Label(page, text="Từ ngày:").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(page, textvariable=self.series_vars["start"], width=12).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(page, text="Lúc:").grid(row=2, column=2, sticky="e", padx=(10, 4))
        ttk.Entry(page, textvariable=self.series_vars["time"], width=7).grid(row=2, column=3, sticky="w")
        ttk.Label(page, text="Cách nhau (ngày):").grid(row=2, column=4, sticky="e", padx=(10, 4))
        ttk.Spinbox(page, textvariable=self.series_vars["every"], from_=1, to=60, width=5).grid(row=2, column=5, sticky="w")
        row = ttk.Frame(page)
        row.grid(row=3, column=0, columnspan=6, sticky="w", pady=(6, 4))
        ttk.Button(row, text="✨ Lập chuỗi bài", style="Accent.TButton", command=self._on_ai_series_clicked).pack(side="left")
        ttk.Button(row, text="Gợi ý giờ đăng", command=self._on_ai_times_clicked).pack(side="left", padx=6)
        self.series_hint_var = tk.StringVar(master=self.root)
        ttk.Label(page, textvariable=self.series_hint_var, style="Hint.TLabel", wraplength=self._px(820), justify="left").grid(
            row=4, column=0, columnspan=6, sticky="w"
        )
        self.series_table = ttk.Treeview(page, columns=("when", "angle", "preview"), show="headings", height=6, selectmode="extended")
        for column, heading, width in (("when", "Giờ đăng", 130), ("angle", "Góc", 130), ("preview", "Nội dung", 500)):
            self.series_table.heading(column, text=heading)
            self.series_table.column(column, width=self._px(width), stretch=column == "preview")
        self.series_table.grid(row=5, column=0, columnspan=6, sticky="nsew")
        self.series_table.bind("<<TreeviewSelect>>", lambda event: self._on_series_selected())
        self.series_box = self._scrolled(page, 7)
        self.series_box.grid(row=6, column=0, columnspan=6, sticky="nsew", pady=(4, 0))
        self.series_box.bind("<KeyRelease>", lambda event: self._on_series_edited())
        page.rowconfigure(6, weight=2)
        ttk.Button(page, text="Thêm các bài đã chọn vào hàng đợi", command=self._on_queue_series_clicked).grid(
            row=7, column=0, columnspan=6, sticky="w", pady=(6, 0)
        )

    def _build_ai_assistant_page(self, inner):
        page = ttk.Frame(inner, padding=8)
        inner.add(page, text="  Trợ lý hẹn giờ  ")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        ttk.Label(
            page,
            style="Hint.TLabel",
            wraplength=self._px(820),
            justify="left",
            text=(
                "Nói điều bạn muốn, ví dụ: “đăng bài chào hè lên Hi English lúc 8 giờ sáng mai và nhóm Jaecoo lúc 8 giờ tối”. "
                "Trợ lý chỉ đề xuất; không có gì được thêm vào hàng đợi cho tới khi bạn bấm xác nhận."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.assistant_log = ScrolledText(page, height=8, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.assistant_log.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=4)
        self.assistant_var = tk.StringVar(master=self.root)
        entry = ttk.Entry(page, textvariable=self.assistant_var)
        entry.grid(row=2, column=0, sticky="ew")
        entry.bind("<Return>", lambda event: self._on_assistant_send_clicked())
        ttk.Button(page, text="Gửi", command=self._on_assistant_send_clicked).grid(row=2, column=1, padx=(6, 0))
        self.proposal_table = ttk.Treeview(page, columns=("when", "pages", "repeat", "preview"), show="headings", height=4,
                                           selectmode="extended")
        for column, heading, width in (("when", "Giờ", 120), ("pages", "Nơi đăng", 170), ("repeat", "Lặp", 90), ("preview", "Nội dung", 400)):
            self.proposal_table.heading(column, text=heading)
            self.proposal_table.column(column, width=self._px(width), stretch=column == "preview")
        self.proposal_table.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        buttons = ttk.Frame(page)
        buttons.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="Thêm các đề xuất đã chọn vào hàng đợi", command=self._on_apply_proposals_clicked).pack(side="left")
        ttk.Button(buttons, text="Cuộc trò chuyện mới", command=self._on_assistant_reset_clicked).pack(side="left", padx=6)

    def _build_ai_tools_page(self, inner):
        page = ttk.Frame(inner, padding=8)
        inner.add(page, text="  Công cụ  ")
        page.columnconfigure(0, weight=1)
        reply = ttk.Labelframe(page, text="Trả lời bình luận (dán bình luận vào đây; chương trình không đọc hay gửi bình luận)", padding=8)
        reply.grid(row=0, column=0, sticky="ew")
        reply.columnconfigure(1, weight=1)
        ttk.Label(reply, text="Bình luận:").grid(row=0, column=0, sticky="nw")
        self.reply_comment_box = self._scrolled(reply, 3)
        self.reply_comment_box.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)
        ttk.Label(reply, text="Giọng điệu:").grid(row=1, column=0, sticky="w")
        self.reply_tone_var = tk.StringVar(master=self.root)
        ttk.Entry(reply, textvariable=self.reply_tone_var).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Button(reply, text="✨ Gợi ý trả lời", command=self._on_ai_reply_clicked).grid(row=1, column=2, padx=(6, 0))
        self.reply_list = tk.Listbox(reply, height=4, exportselection=False)
        self.reply_list.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.reply_flags_var = tk.StringVar(master=self.root)
        ttk.Label(reply, textvariable=self.reply_flags_var, foreground="#a15c00", wraplength=self._px(780), justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w"
        )
        ttk.Button(reply, text="Chép câu đã chọn", command=self._on_copy_reply_clicked).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.reply_texts = []

        picture = ttk.Labelframe(page, text="Ảnh", padding=8)
        picture.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            picture,
            style="Hint.TLabel",
            wraplength=self._px(780),
            justify="left",
            text=(
                "Mô tả ảnh: AI nhìn ảnh đầu tiên ở ô Ảnh/video của tab Đăng bài và viết mô tả cùng chú thích. "
                "Ảnh chữ: AI chọn chữ, chương trình vẽ ra một ảnh vuông (không phải ảnh chụp hay ảnh do AI vẽ) và gắn vào bài."
            ),
        ).pack(anchor="w")
        row = ttk.Frame(picture)
        row.pack(anchor="w", pady=(4, 0))
        ttk.Button(row, text="✨ Mô tả ảnh đang chọn", command=lambda: self._on_ai_menu("describe")).pack(side="left")
        ttk.Button(row, text="✨ Tạo ảnh chữ từ nội dung bài", command=lambda: self._on_ai_menu("card")).pack(side="left", padx=6)
        self.picture_result_var = tk.StringVar(master=self.root)
        ttk.Label(picture, textvariable=self.picture_result_var, wraplength=self._px(780), justify="left").pack(anchor="w", pady=(4, 0))

    def _build_ai_settings_page(self, inner):
        page = ttk.Frame(inner, padding=10)
        inner.add(page, text="  Trợ lý AI  ")
        page.columnconfigure(1, weight=1)
        ttk.Label(
            page,
            style="Hint.TLabel",
            justify="left",
            wraplength=self._px(820),
            text=(
                "Dùng Claude của Anthropic. Khóa API lấy ở console.anthropic.com (tính phí theo lượng dùng, mỗi lần gọi ghi chi phí "
                "ước tính vào nhật ký). Có thể đặt biến môi trường ANTHROPIC_API_KEY thay vì nhập ở đây. Khóa lưu trong config.json "
                "(không đưa vào git). Nội dung bạn nhờ AI xử lý được gửi tới Anthropic."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.ai_vars = {
            "api_key": tk.StringVar(master=self.root),
            "model": tk.StringVar(master=self.root),
            "effort": tk.StringVar(master=self.root),
            "fallbacks": tk.BooleanVar(master=self.root),
            "explain_failures": tk.BooleanVar(master=self.root),
        }
        ttk.Label(page, text="Khóa API:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(page, textvariable=self.ai_vars["api_key"], show="*").grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(page, text="Mô hình:").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Combobox(page, textvariable=self.ai_vars["model"], values=list(ai_module.MODELS), width=24).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(page, text="Mức suy nghĩ (effort):").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Combobox(page, textvariable=self.ai_vars["effort"], values=list(ai_module.EFFORTS), state="readonly", width=10).grid(
            row=3, column=1, sticky="w", pady=2
        )
        ttk.Label(page, text="Giọng chung của các bài:").grid(row=4, column=0, sticky="nw", pady=2)
        self.ai_voice_box = self._scrolled(page, 3)
        self.ai_voice_box.grid(row=4, column=1, sticky="ew", pady=2)
        ttk.Checkbutton(
            page, text="Cho API tự thử lại trên mô hình khác nếu yêu cầu bị từ chối do bộ lọc an toàn", variable=self.ai_vars["fallbacks"]
        ).grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            page,
            text="Khi bài lỗi, tự nhờ AI giải thích trong thông báo (gửi nhật ký và ảnh chụp lỗi cho Anthropic)",
            variable=self.ai_vars["explain_failures"],
        ).grid(row=6, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(page)
        buttons.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Lưu", command=self._on_save_ai_settings_clicked).pack(side="left")
        ttk.Button(buttons, text="Lưu và kiểm tra kết nối", command=self._on_test_ai_clicked).pack(side="left", padx=6)

    def _ai_values(self):
        values = {key: variable.get() for key, variable in self.ai_vars.items()}
        values["voice"] = self.ai_voice_box.get("1.0", "end-1c")
        return values

    def _on_save_ai_settings_clicked(self):
        try:
            self.set_ai_settings(self._ai_values())
        except ValueError as error:
            self._error(error)
        else:
            self.log("Đã lưu cài đặt Trợ lý AI")

    def _on_test_ai_clicked(self):
        try:
            self.set_ai_settings(self._ai_values())
            self.test_ai_connection(self._on_ai_tested)
        except (ValueError, RuntimeError) as error:
            self._error(error)

    def _on_ai_tested(self, result, error):
        if error:
            self._error(error)
        else:
            self.log(f"✔ Kết nối được với {result['model']}")

    def _ai_done(self, handler):
        """A `done` callback that shows an error and otherwise passes the result on."""

        def done(result, error):
            if error:
                self._error(error)
            else:
                handler(result)

        return done

    def _show_text_dialog(self, title, content, actions=()):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry(f"{self._px(640)}x{self._px(460)}")
        box = ScrolledText(dialog, wrap="word", font=("Segoe UI", 10))
        box.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        box.insert("1.0", content)
        box.configure(state="disabled")
        row = ttk.Frame(dialog)
        row.pack(anchor="e", padx=10, pady=8)
        for label, command in actions:
            ttk.Button(row, text=label, command=lambda c=command: (dialog.destroy(), c())).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Đóng", command=dialog.destroy).pack(side="left")
        return dialog

    def _replace_compose_content(self, content):
        """Put `content` in the compose box so that one undo (Ctrl+Z) brings the old text back."""
        box = self.content_box
        box.configure(autoseparators=False)
        box.edit_separator()
        self._set_compose_content(content)
        box.edit_separator()
        box.configure(autoseparators=True)

    # -- compose tab: the "✨ AI giúp…" menu ----------------------------------------------------------------------

    def _on_ai_menu(self, operation):
        content = self._compose_content()
        try:
            if operation == "describe":
                files = store.split_media(self.media_var.get())
                if not files:
                    raise ValueError("Hãy chọn một ảnh ở ô Ảnh/video trước.")
                self.ai_describe(files[0], self._ai_done(self._show_description))
            elif operation == "card":
                self._need_text(content)
                self.ai_card(content, self._ai_done(self._attach_card))
            elif operation == "review":
                self._need_text(content)
                self.ai_review(content, self._ai_done(self._show_review))
            elif operation == "localize":
                self._need_text(content)
                self.ai_localize(content, self.selected_targets(), self._ai_done(self._show_drafts))
            elif operation == "variants":
                self._need_text(content)
                self.ai_variants(content, 3, self._ai_done(self._choose_variant))
            else:
                self._need_text(content)
                self.ai_polish(operation, content, self._ai_done(self._apply_polish))
        except (ValueError, RuntimeError) as error:
            self._error(error)

    @staticmethod
    def _need_text(content):
        if not content.strip():
            raise ValueError("Hãy viết hoặc dán nội dung bài ở ô Nội dung trước.")

    def _apply_polish(self, result):
        self._replace_compose_content(result["text"])
        self.log("✨ " + (result["notes"] or "Đã chỉnh bài") + " (Ctrl+Z để hoàn tác)")

    def _show_review(self, result):
        actions = []
        if result["improved_text"].strip() != self._compose_content().strip():
            actions.append(("Dùng bản đã sửa", lambda: self._replace_compose_content(result["improved_text"])))
        self._show_text_dialog("Kiểm tra bài", format_review(result), actions)

    def _choose_variant(self, variants):
        dialog = tk.Toplevel(self.root)
        dialog.title("Chọn một biến thể")
        dialog.transient(self.root)
        dialog.geometry(f"{self._px(640)}x{self._px(380)}")
        listbox = tk.Listbox(dialog, exportselection=False)
        for text in variants:
            listbox.insert("end", " ".join(text.split())[:140])
        listbox.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        preview = ScrolledText(dialog, height=6, wrap="word", font=("Segoe UI", 10))
        preview.pack(fill="x", padx=10)

        def show(event=None):
            chosen = listbox.curselection()
            preview.delete("1.0", "end")
            if chosen:
                preview.insert("1.0", variants[chosen[0]])

        listbox.bind("<<ListboxSelect>>", show)

        def use():
            chosen = listbox.curselection()
            if chosen:
                self._replace_compose_content(variants[chosen[0]])
                dialog.destroy()

        row = ttk.Frame(dialog)
        row.pack(anchor="e", padx=10, pady=8)
        ttk.Button(row, text="Dùng bản này", style="Accent.TButton", command=use).pack(side="left")
        ttk.Button(row, text="Đóng", command=dialog.destroy).pack(side="left", padx=(6, 0))
        self.variant_dialog, self.variant_list = dialog, listbox
        return dialog

    def _show_description(self, result):
        self.picture_result_var.set(f"Alt text: {result['alt_text']}\nChú thích: {result['caption']}")
        actions = []
        if result["caption"]:
            actions.append(("Thêm chú thích vào bài", lambda: self._append_to_compose(result["caption"])))
        self._show_text_dialog("Mô tả ảnh", f"Alt text:\n{result['alt_text']}\n\nChú thích gợi ý:\n{result['caption']}", actions)

    def _append_to_compose(self, extra):
        current = self._compose_content().rstrip()
        self._replace_compose_content((current + "\n\n" if current else "") + extra)

    def _attach_card(self, result):
        files = store.split_media(self.media_var.get())
        self.media_var.set(store.join_media(files + [result["file"]]))
        self.picture_result_var.set(f"Đã vẽ ảnh chữ và gắn vào bài: {result['file']}")
        self.log(f"✨ Đã tạo ảnh chữ {result['file']}")

    def _show_drafts(self, posts):
        self._set_drafts(posts)
        self.notebook.select(self.ai_tab)
        self.ai_inner.select(0)

    # -- the writing page ---------------------------------------------------------------------------------------

    def _set_drafts(self, posts):
        self.ai_drafts = [dict(post) for post in posts]
        self.ai_draft_table.delete(*self.ai_draft_table.get_children())
        for index, post in enumerate(self.ai_drafts):
            preview = " ".join(post["content"].split())[:120]
            self.ai_draft_table.insert("", "end", iid=str(index), values=(post["name"], preview))
        if self.ai_drafts:
            self.ai_draft_table.selection_set("0")
        self._on_draft_selected()

    def _selected_draft_index(self):
        chosen = self.ai_draft_table.selection()
        return int(chosen[0]) if chosen else None

    def _on_draft_selected(self):
        index = self._selected_draft_index()
        self.ai_draft_box.delete("1.0", "end")
        if index is not None and index < len(self.ai_drafts):
            self.ai_draft_box.insert("1.0", self.ai_drafts[index]["content"])

    def _on_draft_edited(self):
        index = self._selected_draft_index()
        if index is not None and index < len(self.ai_drafts):
            content = self.ai_draft_box.get("1.0", "end-1c")
            self.ai_drafts[index]["content"] = content
            self.ai_draft_table.set(str(index), "preview", " ".join(content.split())[:120])

    def _on_ai_write_clicked(self):
        idea = self.ai_idea_box.get("1.0", "end-1c")
        try:
            if not idea.strip():
                raise ValueError("Hãy nhập ý tưởng hoặc chủ đề cho bài viết.")
            names = self.selected_targets()
            if not names:
                raise ValueError("Chưa chọn nơi đăng. Bấm “Chọn nơi đăng…” ở tab Đăng bài.")
            self.ai_draft(idea, self.ai_tone_var.get(), LENGTH_LABELS[self.ai_length_var.get()], self.ai_details_var.get(),
                          names, self._ai_done(self._show_drafts))
        except (ValueError, RuntimeError, ai_module.AIError) as error:
            self._error(error)

    def _on_use_draft_clicked(self):
        index = self._selected_draft_index()
        if index is None:
            self._error("Hãy chọn một bản nháp trong danh sách.")
            return
        draft = self.ai_drafts[index]
        self._replace_compose_content(draft["content"])
        if draft["name"] == PERSONAL or any(page["name"] == draft["name"] for page in self.settings["pages"]):
            self.select_targets([draft["name"]])
        self.notebook.select(0)

    def _on_queue_drafts_clicked(self):
        if not self.ai_drafts:
            self._error("Chưa có bản nháp nào.")
            return
        try:
            run_at, _ = self._read_schedule(self.run_at_var.get(), REPEAT_LABELS["none"])
        except ValueError as error:
            self._error(error)
            return
        if not messagebox.askyesno(
            "Thêm vào hàng đợi",
            f"Thêm {len(self.ai_drafts)} bài do AI soạn vào hàng đợi, bài đầu lúc {scheduler.format_run_at(run_at)}?\n"
            "Hãy chắc là bạn đã đọc kỹ từng bản.",
            parent=self.root,
        ):
            return
        jobs = self.queue_ai_posts(self.ai_drafts, run_at, self.media_var.get().strip())
        self.log(f"Đã thêm {len(jobs)} bài do AI soạn vào hàng đợi, từ {jobs[0]['run_at']} đến {jobs[-1]['run_at']}")

    # -- the series page ----------------------------------------------------------------------------------------

    def _on_ai_series_clicked(self):
        try:
            count = int(self.series_vars["count"].get())
            every = int(self.series_vars["every"].get())
            start = datetime.strptime(self.series_vars["start"].get().strip(), "%Y-%m-%d").date()
            times = ai_features.series_times(start, self.series_vars["time"].get(), count, every)
        except ValueError as error:
            self._error(error if "dạng" in str(error) or "phải" in str(error) else "Số bài, ngày bắt đầu (YYYY-MM-DD) và khoảng cách phải hợp lệ.")
            return
        page = self.series_vars["page"].get()
        if not self.series_vars["topic"].get().strip():
            self._error("Hãy nhập chủ đề cho chuỗi bài.")
            return

        def done(posts, error):
            if error:
                self._error(error)
                return
            self.series_posts = [
                {"at": moment, "angle": post["angle"], "content": post["content"], "name": page}
                for post, moment in zip(posts, times)
            ]
            self._refresh_series()

        try:
            self.ai_series(self.series_vars["topic"].get(), count, page, self.series_vars["tone"].get(), done)
        except (ValueError, RuntimeError) as error:
            self._error(error)

    def _refresh_series(self):
        self.series_table.delete(*self.series_table.get_children())
        for index, post in enumerate(self.series_posts):
            self.series_table.insert(
                "", "end", iid=str(index),
                values=(scheduler.format_run_at(post["at"]), post["angle"], " ".join(post["content"].split())[:100]),
            )
        if self.series_posts:
            self.series_table.selection_set(*[str(i) for i in range(len(self.series_posts))])
            self.series_table.focus("0")
        self._on_series_selected()

    def _on_series_selected(self):
        focus = self.series_table.focus()
        self.series_box.delete("1.0", "end")
        if focus != "" and int(focus) < len(self.series_posts):
            self.series_box.insert("1.0", self.series_posts[int(focus)]["content"])

    def _on_series_edited(self):
        focus = self.series_table.focus()
        if focus != "" and int(focus) < len(self.series_posts):
            content = self.series_box.get("1.0", "end-1c")
            self.series_posts[int(focus)]["content"] = content
            self.series_table.set(focus, "preview", " ".join(content.split())[:100])

    def queue_series(self, indices):
        """Queue the chosen posts of the planned series. Returns (jobs, skipped_because_past)."""
        now = datetime.now()
        jobs, skipped = [], 0
        for index in indices:
            post = self.series_posts[index]
            if post["at"] <= now:
                skipped += 1
                continue
            url = "" if post["name"] == PERSONAL else self._page_url(post["name"])
            jobs.append(scheduler.new_job(post["content"], url, "", post["at"], "none", self.selected_account()))
        self.jobs.extend(jobs)
        self._save_queue()
        self._refresh_queue()
        return jobs, skipped

    def _on_queue_series_clicked(self):
        chosen = [int(item) for item in self.series_table.selection()]
        if not chosen:
            self._error("Hãy chọn các bài muốn thêm vào hàng đợi.")
            return
        if not messagebox.askyesno(
            "Thêm vào hàng đợi", f"Thêm {len(chosen)} bài do AI soạn vào hàng đợi theo các giờ đã lập?\nHãy chắc là bạn đã đọc kỹ từng bài.",
            parent=self.root,
        ):
            return
        jobs, skipped = self.queue_series(sorted(chosen))
        self.log(f"Đã thêm {len(jobs)} bài vào hàng đợi" + (f" (bỏ {skipped} bài có giờ đã qua)" if skipped else ""))

    def _on_ai_times_clicked(self):
        try:
            self.ai_times(self.series_vars["page"].get(), self._ai_done(self._show_times))
        except (ValueError, RuntimeError) as error:
            self._error(error)

    def _show_times(self, result):
        lines = [f"{item['weekday']} {item['time']}: {item['reason']}" for item in result["suggestions"]]
        self.series_hint_var.set("Giờ gợi ý — " + "; ".join(lines) + f"\n{result['caveat']}")

    # -- the assistant page ---------------------------------------------------------------------------------------

    def _assistant_say(self, who, text):
        self.assistant_log.configure(state="normal")
        self.assistant_log.insert("end", f"{who}: {text}\n\n")
        self.assistant_log.see("end")
        self.assistant_log.configure(state="disabled")

    def _on_assistant_send_clicked(self):
        message = self.assistant_var.get().strip()
        if not message:
            return
        try:
            self.ai_assistant(message, self._on_assistant_answer)
        except RuntimeError as error:
            self._error(error)
            return
        self._assistant_say("Bạn", message)
        self.assistant_var.set("")

    def _on_assistant_answer(self, result, error):
        if error:
            self._assistant_say("Lỗi", error)
            return
        lines = [result["reply"]] if result["reply"] else []
        lines += [f"? {question}" for question in result["questions"]]
        lines += [f"! {warning}" for warning in result["warnings"]]
        self._assistant_say("Trợ lý", "\n".join(lines) or "(không có gì để nói thêm)")
        self.proposal_table.delete(*self.proposal_table.get_children())
        for index, proposal in enumerate(result["proposals"]):
            self.proposal_table.insert(
                "", "end", iid=str(index),
                values=(proposal["at"], ", ".join(proposal["pages"]), repeat_label(proposal["repeat"]),
                        " ".join(proposal["content"].split())[:100]),
            )
        if result["proposals"]:
            self.proposal_table.selection_set(*[str(i) for i in range(len(result["proposals"]))])

    def _on_apply_proposals_clicked(self):
        chosen = [self.assistant_proposals[int(item)] for item in self.proposal_table.selection()]
        if not chosen:
            self._error("Chưa có đề xuất nào được chọn.")
            return
        if not messagebox.askyesno(
            "Thêm vào hàng đợi", f"Thêm {len(chosen)} đề xuất vào hàng đợi? Hãy chắc là bạn đã đọc kỹ nội dung.", parent=self.root
        ):
            return
        jobs = self.apply_proposals(chosen)
        self.log(f"Đã thêm {len(jobs)} bài theo đề xuất của trợ lý")
        self._assistant_say("Hệ thống", f"Đã thêm {len(jobs)} bài vào hàng đợi.")
        self.proposal_table.delete(*self.proposal_table.get_children())
        self.assistant_proposals = []

    def _on_assistant_reset_clicked(self):
        self.reset_assistant()
        self.assistant_log.configure(state="normal")
        self.assistant_log.delete("1.0", "end")
        self.assistant_log.configure(state="disabled")
        self.proposal_table.delete(*self.proposal_table.get_children())

    # -- tools page and the queue ---------------------------------------------------------------------------------

    def _on_ai_reply_clicked(self):
        comment = self.reply_comment_box.get("1.0", "end-1c")
        post_text = self._compose_content()
        try:
            if not comment.strip():
                raise ValueError("Hãy dán bình luận cần trả lời.")
            self.ai_reply(comment, post_text, self.reply_tone_var.get(), self._ai_done(self._show_replies))
        except (ValueError, RuntimeError) as error:
            self._error(error)

    def _show_replies(self, result):
        self.reply_texts = [item["text"] for item in result["replies"]]
        self.reply_list.delete(0, "end")
        for item in result["replies"]:
            self.reply_list.insert("end", f"({item['style']}) " + " ".join(item["text"].split()))
        self.reply_flags_var.set("Lưu ý: " + "; ".join(result["flags"]) if result["flags"] else "")

    def _on_copy_reply_clicked(self):
        chosen = self.reply_list.curselection()
        if not chosen:
            self._error("Hãy chọn một câu trả lời trong danh sách.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.reply_texts[chosen[0]])
        self.log("Đã chép câu trả lời vào clipboard")

    def _on_explain_clicked(self):
        job = self._selected_job()
        if job is None:
            return
        if not job["last_message"]:
            messagebox.showinfo("Chưa có gì để giải thích", "Bài này chưa có lỗi hay ghi chú nào.", parent=self.root)
            return
        try:
            self.ai_explain(job, self._ai_done(lambda result: self._show_text_dialog(
                "Vì sao bài này lỗi?", format_explanation(result, job["last_message"]))))
        except (ValueError, RuntimeError) as error:
            self._error(error)

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", f"[{stamp}] {text}\n")
        extra_lines = int(self.log_widget.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if extra_lines > 0:
            self.log_widget.delete("1.0", f"{extra_lines + 1}.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _refresh_pages(self):
        if getattr(self, "series_page_box", None) is not None:
            names = [PERSONAL] + [page["name"] for page in self.settings["pages"]]
            self.series_page_box["values"] = names
            if self.series_vars["page"].get() not in names:
                self.series_vars["page"].set(PERSONAL)
        entries = self.available_targets()
        # Config files written before multi-select only have a single last_page
        remembered = self.settings["last_pages"] or [self.settings.get("last_page", "")]
        ticked = [name for name in remembered if name in entries] or [PERSONAL]
        self.select_targets(ticked)
        self.page_list.delete(0, "end")
        for page in self.settings["pages"]:
            parts = [page["name"], page.get("language", ""), page["url"]]
            self.page_list.insert("end", "  —  ".join(part for part in parts if part))

    def _refresh_templates(self):
        names = [template["name"] for template in self.settings["templates"]]
        self.template_box["values"] = names
        if self.template_var.get() not in names:
            self.template_var.set("")
        self.template_list.delete(0, "end")
        for name in names:
            self.template_list.insert("end", name)

    def _refresh_queue(self):
        selected = self.queue_table.selection()
        self.queue_table.delete(*self.queue_table.get_children())
        for job in sorted(self.jobs, key=lambda job: job["run_at"]):
            preview = " ".join(job["content"].split())[:80] or "(chỉ có ảnh/video)"
            self.queue_table.insert(
                "",
                "end",
                iid=job["id"],
                values=(
                    job["run_at"],
                    repeat_label(job["repeat"]),
                    self._page_label(job["page_url"]),
                    preview,
                    STATUS_LABELS[job["status"]],
                    job["last_message"],
                    job.get("post_url", ""),
                ),
                tags=(job["status"],),
            )
        if selected and self.queue_table.exists(selected[0]):
            self.queue_table.selection_set(selected[0])
        if getattr(self, "calendar", None) is not None:
            self.calendar.refresh()
        self._update_controls()

    def _refresh_session(self):
        saved = store.session_saved_at(self.state_path)
        if saved is None:
            self.session_label.configure(text="Chưa đăng nhập", foreground="#b3261e")
        elif self._session_expired:
            self.session_label.configure(
                text="Phiên đăng nhập đã hết hạn, hãy đăng nhập lại", foreground="#b3261e"
            )
        else:
            self.session_label.configure(
                text=f"Đã đăng nhập (phiên lưu lúc {saved:%Y-%m-%d %H:%M})", foreground="#1a7f37"
            )

    def _update_controls(self):
        kind = self.task["kind"] if self.task else None
        idle = "normal" if kind is None else "disabled"
        self.publish_button.configure(state=idle)
        self.run_job_button.configure(state=idle)
        self.login_button.configure(state=idle)
        self.account_login_button.configure(state=idle)
        self.stop_button.configure(state="disabled" if kind is None else "normal")
        self.confirm_login_button.configure(state="normal" if kind == "login" else "disabled")
        self.confirm_account_login_button.configure(state="normal" if kind == "login" else "disabled")

        if kind == "post":
            status = "Đang đăng bài…"
        elif kind == "login":
            status = "Đang chờ bạn đăng nhập trong cửa sổ trình duyệt…"
        else:
            status = "Sẵn sàng"
            pending = sorted(job["run_at"] for job in self.jobs if job["status"] == "pending")
            if pending:
                status += f"  ·  Bài hẹn kế tiếp: {pending[0]}"
                if not self.settings["scheduler_enabled"]:
                    status += " (hẹn giờ đang tắt)"
        if getattr(self, "_ai_busy", False):
            status = "Đang hỏi AI…  ·  " + status
        self.status_var.set(status)

    def _update_count(self):
        self.count_var.set(f"{len(self._compose_content())} ký tự")

    # ------------------------------------------------------------------
    # Helpers shared by the UI handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_run_at():
        moment = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1)
        return scheduler.format_run_at(moment)

    def _page_label(self, url):
        if not url:
            return PERSONAL
        for page in self.settings["pages"]:
            if page["url"] == url:
                return page["name"]
        return url

    def _page_url(self, name):
        for page in self.settings["pages"]:
            if page["name"] == name:
                return page["url"]
        return ""

    def _compose_content(self):
        return self.content_box.get("1.0", "end-1c")

    def _set_compose_content(self, content):
        self.content_box.delete("1.0", "end")
        self.content_box.insert("1.0", content)
        self._update_count()

    def _choose_media(self, variable, parent=None):
        paths = filedialog.askopenfilenames(
            parent=parent or self.root, title="Chọn ảnh hoặc video (có thể chọn nhiều tệp)", filetypes=MEDIA_TYPES
        )
        if paths:
            variable.set(store.join_media([os.path.normpath(path) for path in paths]))

    @staticmethod
    def _check_post(content, image_path):
        files = store.split_media(image_path)
        if not content.strip() and not files:
            raise ValueError("Bài viết chưa có nội dung hoặc ảnh/video.")
        for path in files:
            if not os.path.isfile(path):
                raise ValueError(f"Không tìm thấy tệp ảnh/video:\n{path}")

    @staticmethod
    def _read_schedule(run_at_text, repeat_label):
        try:
            run_at = scheduler.parse_run_at(run_at_text)
        except ValueError:
            raise ValueError(
                "Thời điểm phải có dạng YYYY-MM-DD HH:MM, ví dụ 2026-10-12 09:30."
            ) from None
        if run_at <= datetime.now():
            raise ValueError("Thời điểm hẹn phải ở tương lai.")
        return run_at, repeat_from_label(repeat_label)

    def _confirm_repeat(self, repeat, parent=None):
        if repeat == "none":
            return True
        return messagebox.askokcancel(
            "Bài lặp lại",
            "Đăng lặp lại cùng một nội dung dễ bị Facebook coi là spam và có thể "
            "khiến tài khoản bị hạn chế.\n\nVẫn thêm bài lặp này?",
            icon="warning",
            parent=parent or self.root,
        )

    def _open_path(self, path):
        if not os.path.exists(path):
            messagebox.showinfo("Chưa có tệp", f"Chưa có:\n{path}", parent=self.root)
        elif hasattr(os, "startfile"):
            os.startfile(path)
        else:
            messagebox.showinfo("Đường dẫn", path, parent=self.root)

    def _error(self, error, parent=None):
        messagebox.showerror("Không thực hiện được", str(error), parent=parent or self.root)

    def _on_callback_error(self, kind, error, trace):
        """An unexpected error in a UI callback: show it in the log, not only on a console nobody sees."""
        if sys.stderr is not None:
            traceback.print_exception(kind, error, trace)
        try:
            self.log(f"✖ Lỗi không mong đợi: {kind.__name__}: {error}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # UI handlers: compose tab
    # ------------------------------------------------------------------

    def _on_gap_edited(self):
        try:
            self.set_page_gap(self.gap_var.get())
        except ValueError:
            # Put back the last valid value; the publish buttons report bad input themselves
            self.gap_var.set(str(self.settings["page_gap_minutes"]))

    def _read_targets(self):
        """The ticked targets and the gap between them, as (names, urls, gap_minutes)."""
        names = self.selected_targets()
        if not names:
            raise ValueError("Chưa chọn nơi đăng. Bấm “Chọn nơi đăng…” và tích ít nhất một ô.")
        gap = self.set_page_gap(self.gap_var.get()) if len(names) > 1 else 0
        return names, self.selected_page_urls(), gap

    @staticmethod
    def _describe_targets(names, gap):
        if len(names) == 1:
            return names[0]
        listing = "\n".join(f"  • {name}" for name in names)
        pace = f"cách nhau {gap} phút" if gap else "liền nhau"
        return f"{len(names)} nơi, lần lượt, {pace}:\n{listing}"

    def _on_insert_template_clicked(self):
        name = self.template_var.get()
        if self.find_template(name) is None:
            messagebox.showinfo("Chưa chọn mẫu", "Hãy chọn một mẫu trong danh sách.", parent=self.root)
            return
        if self._confirm_replace_draft():
            self.apply_template(name)

    def _confirm_replace_draft(self):
        """Ask before a template overwrites text or a file already in the composer."""
        if not self._compose_content().strip() and not self.media_var.get().strip():
            return True
        return messagebox.askyesno(
            "Thay nội dung",
            "Thay nội dung và tệp đính kèm đang soạn bằng mẫu này?",
            parent=self.root,
        )

    def _on_save_as_template_clicked(self):
        content = self._compose_content()
        media_path = self.media_var.get().strip()
        if not content.strip() and not media_path:
            self._error("Chưa có nội dung hoặc ảnh/video để lưu thành mẫu.")
            return
        name = simpledialog.askstring("Lưu thành mẫu", "Tên mẫu:", parent=self.root)
        if name is None:
            return
        try:
            self.save_template(name, content, media_path)
        except ValueError as error:
            self._error(error)
        else:
            self.log(f"Đã lưu mẫu “{name.strip()}”")

    def _on_publish_now_clicked(self):
        content = self._compose_content()
        image_path = self.media_var.get().strip()
        try:
            self._check_post(content, image_path)
            names, page_urls, gap = self._read_targets()
        except ValueError as error:
            self._error(error)
            return
        if not messagebox.askyesno(
            "Đăng ngay",
            f"Đăng bài này ngay bây giờ lên {self._describe_targets(names, gap)}",
            parent=self.root,
        ):
            return
        try:
            jobs = self.publish_to(content, page_urls, image_path, gap, self.selected_account())
        except RuntimeError as error:
            self._error(error)
            return
        if jobs:
            self.log(f"Đã xếp {len(jobs)} bài vào hàng đợi, bài cuối chạy lúc {jobs[-1]['run_at']}")

    def _on_enqueue_clicked(self):
        content = self._compose_content()
        image_path = self.media_var.get().strip()
        try:
            self._check_post(content, image_path)
            names, page_urls, gap = self._read_targets()
            run_at, repeat = self._read_schedule(self.run_at_var.get(), self.repeat_var.get())
        except ValueError as error:
            self._error(error)
            return
        if not self._confirm_repeat(repeat):
            return
        try:
            jitter = self._read_jitter(self.jitter_var.get())
        except ValueError as error:
            self._error(error)
            return
        jobs = self.enqueue_many(
            content, page_urls, image_path, run_at, repeat, gap, self.selected_account(), jitter
        )
        if len(jobs) == 1:
            self.log(f"Đã thêm bài hẹn {jobs[0]['run_at']} vào hàng đợi")
        else:
            self.log(
                f"Đã thêm {len(jobs)} bài vào hàng đợi, từ {jobs[0]['run_at']} đến {jobs[-1]['run_at']}"
            )

    # ------------------------------------------------------------------
    # UI handlers: queue tab
    # ------------------------------------------------------------------

    def _selected_job(self):
        selected = self.queue_table.selection()
        job = self.find_job(selected[0]) if selected else None
        if job is None:
            messagebox.showinfo("Chưa chọn bài", "Hãy chọn một bài trong hàng đợi.", parent=self.root)
        return job

    def _on_run_job_clicked(self):
        job = self._selected_job()
        if job is None:
            return
        if not messagebox.askyesno(
            "Đăng ngay",
            f"Đăng bài này lên {self._page_label(job['page_url'])} ngay bây giờ?",
            parent=self.root,
        ):
            return
        self.run_job(job)

    def _on_remove_job_clicked(self):
        job = self._selected_job()
        if job is None:
            return
        if job["status"] == "running":
            self._error("Bài này đang chạy. Hãy dừng tác vụ trước khi xóa.")
            return
        if messagebox.askyesno("Xóa bài", "Xóa bài này khỏi hàng đợi?", parent=self.root):
            self.remove_job(job["id"])

    def _on_clear_done_clicked(self):
        self.jobs = [job for job in self.jobs if job["status"] != "done"]
        self._save_queue()
        self._refresh_queue()

    def _on_open_link_clicked(self):
        job = self._selected_job()
        if job is None:
            return
        if not job.get("post_url"):
            messagebox.showinfo(
                "Chưa có link",
                "Chưa biết link của bài này (chưa đăng, hoặc chưa thấy bài trên trang).",
                parent=self.root,
            )
            return
        self._open_url(job["post_url"])

    def _open_url(self, url):
        import webbrowser

        webbrowser.open(url)

    def _open_job_by_id(self, job_id):
        job = self.find_job(job_id)
        if job is None:
            return
        if job["status"] == "running":
            self._error("Bài này đang chạy nên chưa sửa được.")
            return
        self._open_job_dialog(job)

    def _on_edit_job_clicked(self):
        job = self._selected_job()
        if job is None:
            return
        if job["status"] == "running":
            self._error("Bài này đang chạy nên chưa sửa được.")
            return
        self._open_job_dialog(job)

    def _open_job_dialog(self, job):
        dialog = tk.Toplevel(self.root)
        dialog.title("Sửa bài hẹn giờ")
        dialog.transient(self.root)
        dialog.geometry(f"{self._px(640)}x{self._px(540)}")
        dialog.columnconfigure(1, weight=1)
        dialog.rowconfigure(1, weight=1)

        ttk.Label(dialog, text="Đăng lên:").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 3))
        page_var = tk.StringVar(master=dialog, value=self._page_label(job["page_url"]))
        choices = [PERSONAL] + [page["name"] for page in self.settings["pages"]]
        if page_var.get() not in choices:
            choices.append(page_var.get())
        ttk.Combobox(dialog, textvariable=page_var, values=choices, state="readonly").grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=(10, 3)
        )

        ttk.Label(dialog, text="Nội dung:").grid(row=1, column=0, sticky="nw", padx=10, pady=3)
        content_box = ScrolledText(dialog, height=10, wrap="word", font=("Segoe UI", 10), undo=True)
        content_box.grid(row=1, column=1, columnspan=2, sticky="nsew", padx=(0, 10), pady=3)
        content_box.insert("1.0", job["content"])

        ttk.Label(dialog, text="Ảnh/video:").grid(row=2, column=0, sticky="w", padx=10, pady=3)
        image_var = tk.StringVar(master=dialog, value=job["image_path"])
        ttk.Entry(dialog, textvariable=image_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(
            dialog, text="Chọn tệp…", command=lambda: self._choose_media(image_var, dialog)
        ).grid(row=2, column=2, padx=(6, 10))

        ttk.Label(dialog, text="Thời điểm:").grid(row=3, column=0, sticky="w", padx=10, pady=3)
        run_at_var = tk.StringVar(master=dialog, value=job["run_at"])
        ttk.Entry(dialog, textvariable=run_at_var, width=18).grid(row=3, column=1, sticky="w", pady=3)

        ttk.Label(dialog, text="Lặp:").grid(row=4, column=0, sticky="w", padx=10, pady=3)
        repeat_var = tk.StringVar(master=dialog, value=repeat_label(job["repeat"]))
        repeat_box = ttk.Combobox(
            dialog, textvariable=repeat_var, values=repeat_choices(), state="readonly", width=24
        )
        repeat_box.grid(row=4, column=1, sticky="w", pady=3)
        repeat_box.bind("<<ComboboxSelected>>", lambda event: self._on_repeat_selected(repeat_var, dialog))

        ttk.Label(dialog, text="Tài khoản:").grid(row=5, column=0, sticky="w", padx=10, pady=3)
        account_var = tk.StringVar(master=dialog, value=job.get("account") or DEFAULT_ACCOUNT)
        accounts = [DEFAULT_ACCOUNT] + self.settings["accounts"]
        if account_var.get() not in accounts:
            accounts.append(account_var.get())
        ttk.Combobox(dialog, textvariable=account_var, values=accounts, state="readonly", width=16).grid(
            row=5, column=1, sticky="w", pady=3
        )
        ttk.Label(dialog, text="Lệch giờ ± (phút):").grid(row=6, column=0, sticky="w", padx=10, pady=3)
        jitter_var = tk.StringVar(master=dialog, value=str(job.get("jitter_minutes", 0)))
        ttk.Spinbox(dialog, textvariable=jitter_var, from_=0, to=720, width=5).grid(row=6, column=1, sticky="w", pady=3)

        def save():
            content = content_box.get("1.0", "end-1c")
            image_path = image_var.get().strip()
            try:
                self._check_post(content, image_path)
                run_at, repeat = self._read_schedule(run_at_var.get(), repeat_var.get())
                jitter = self._read_jitter(jitter_var.get())
            except ValueError as error:
                self._error(error, dialog)
                return
            if repeat != job["repeat"] and not self._confirm_repeat(repeat, dialog):
                return
            # A page that was deleted from the list keeps the URL stored on the job
            page_url = job["page_url"]
            if page_var.get() != self._page_label(job["page_url"]):
                page_url = self._page_url(page_var.get())
            job.update(
                content=content,
                image_path=image_path,
                page_url=page_url,
                run_at=scheduler.format_run_at(run_at),
                repeat=repeat,
                status="pending",
                last_message="",
            )
            # A time set by hand replaces the wait that a multi-target post put on this job
            for key in ("not_before", "attempts", "base_run_at"):
                job.pop(key, None)
            account = "" if account_var.get() == DEFAULT_ACCOUNT else account_var.get()
            for key, value in (("account", account), ("jitter_minutes", jitter)):
                if value:
                    job[key] = value
                else:
                    job.pop(key, None)
            if repeat == "monthly":
                job["anchor_day"] = run_at.day
            self._save_queue()
            self._refresh_queue()
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=7, column=0, columnspan=3, sticky="e", padx=10, pady=10)
        ttk.Button(buttons, text="Lưu", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(buttons, text="Hủy", command=dialog.destroy).pack(side="left", padx=(6, 0))
        dialog.grab_set()

    # ------------------------------------------------------------------
    # UI handlers: pages and templates tab
    # ------------------------------------------------------------------

    def _on_page_list_selected(self, event=None):
        selected = self.page_list.curselection()
        if selected:
            page = self.settings["pages"][selected[0]]
            self.page_name_var.set(page["name"])
            self.page_url_var.set(page["url"])
            self.page_language_var.set(page.get("language", ""))
            self.page_voice_var.set(page.get("voice", ""))

    def _on_save_page_clicked(self):
        try:
            self.add_page(
                self.page_name_var.get(),
                self.page_url_var.get(),
                self.page_language_var.get(),
                self.page_voice_var.get(),
            )
        except ValueError as error:
            self._error(error)
        else:
            self.log(f"Đã lưu Fanpage “{self.page_name_var.get().strip()}”")

    def _on_remove_page_clicked(self):
        name = self.page_name_var.get().strip()
        if not self._page_url(name):
            messagebox.showinfo("Chưa chọn Fanpage", "Hãy chọn một Fanpage trong danh sách.", parent=self.root)
            return
        if messagebox.askyesno("Xóa Fanpage", f"Xóa “{name}” khỏi danh sách?", parent=self.root):
            self.remove_page(name)
            self.page_name_var.set("")
            self.page_url_var.set("")
            self.page_language_var.set("")

    def _on_template_list_selected(self, event=None):
        selected = self.template_list.curselection()
        if selected:
            template = self.settings["templates"][selected[0]]
            self.template_name_var.set(template["name"])
            self.template_content_box.delete("1.0", "end")
            self.template_content_box.insert("1.0", template["content"])
            self.template_media_var.set(template.get("media_path", ""))

    def _on_save_template_clicked(self):
        try:
            self.save_template(
                self.template_name_var.get(),
                self.template_content_box.get("1.0", "end-1c"),
                self.template_media_var.get(),
            )
        except ValueError as error:
            self._error(error)
        else:
            self.log(f"Đã lưu mẫu “{self.template_name_var.get().strip()}”")

    def _on_remove_template_clicked(self):
        name = self.template_name_var.get().strip()
        if name not in self.template_box["values"]:
            messagebox.showinfo("Chưa chọn mẫu", "Hãy chọn một mẫu trong danh sách.", parent=self.root)
            return
        if messagebox.askyesno("Xóa mẫu", f"Xóa mẫu “{name}”?", parent=self.root):
            self.remove_template(name)
            self.template_name_var.set("")
            self.template_content_box.delete("1.0", "end")
            self.template_media_var.set("")

    def _on_use_template_clicked(self):
        # Uses what is in the editor right now, including edits not saved yet
        content = self.template_content_box.get("1.0", "end-1c")
        media_path = self.template_media_var.get().strip()
        if not content.strip() and not media_path:
            self._error("Mẫu chưa có nội dung hoặc ảnh/video.")
            return
        if not self._confirm_replace_draft():
            return
        self._set_compose_content(content)
        self.media_var.set(media_path)
        self.notebook.select(0)

    # ------------------------------------------------------------------
    # UI handlers: settings tab, status bar, window
    # ------------------------------------------------------------------

    def _on_login_clicked(self):
        if store.session_saved_at(self.state_path) is not None and not messagebox.askyesno(
            "Đăng nhập lại",
            "Đã có phiên đăng nhập. Đăng nhập lại sẽ ghi đè phiên cũ. Tiếp tục?",
            parent=self.root,
        ):
            return
        try:
            self.start_login()
        except RuntimeError as error:
            self._error(error)

    def _on_stop_clicked(self):
        if self.task and messagebox.askyesno(
            "Dừng tác vụ", "Dừng tác vụ đang chạy và đóng trình duyệt?", parent=self.root
        ):
            self.stop_task()

    def _on_close(self):
        if self.is_busy() and not messagebox.askyesno(
            "Thoát", "Đang có tác vụ chạy. Dừng tác vụ và thoát?", parent=self.root
        ):
            return
        self.shutdown()


class TargetDialog:
    """Popup with one checkbox per target. OK hands the ticked names to `on_ok`.

    `labels` optionally maps a name to the text shown on its checkbox.
    """

    MAX_VISIBLE_ROWS = 12

    def __init__(self, parent, names, ticked, on_ok, px, labels=None):
        labels = labels or {}
        self._on_ok = on_ok
        self.window = window = tk.Toplevel(parent)
        window.title("Chọn nơi đăng")
        window.transient(parent)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        ttk.Label(window, text="Tích vào các nơi muốn đăng bài:").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4)
        )

        # The checkboxes live in a canvas so a long list can scroll
        row_height = tkfont.nametofont("TkDefaultFont").metrics("linespace") + px(8)
        rows = min(max(len(names), 4), self.MAX_VISIBLE_ROWS)
        self._canvas = canvas = tk.Canvas(
            window,
            width=px(420),
            height=row_height * rows,
            highlightthickness=1,
            highlightbackground="#adadad",
            background=ttk.Style(window).lookup("TFrame", "background"),
        )
        canvas.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
        scroll = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 10))
        canvas.configure(yscrollcommand=scroll.set)
        self._holder = holder = ttk.Frame(canvas)
        holder_window = canvas.create_window((0, 0), window=holder, anchor="nw")
        holder.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(holder_window, width=event.width))

        self.vars = {}  # name -> BooleanVar
        self.checks = {}  # name -> Checkbutton
        for name in names:
            variable = tk.BooleanVar(master=window, value=name in ticked)
            box = ttk.Checkbutton(holder, text=labels.get(name, name), variable=variable)
            box.pack(anchor="w", fill="x", padx=6)
            self.vars[name] = variable
            self.checks[name] = box
        for widget in (canvas, holder, *self.checks.values()):
            widget.bind("<MouseWheel>", self._on_wheel)

        buttons = ttk.Frame(window)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        ttk.Button(buttons, text="Chọn tất cả", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(buttons, text="Bỏ chọn", command=lambda: self.set_all(False)).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="Hủy", command=self.cancel).pack(side="right")
        ttk.Button(buttons, text="OK", style="Accent.TButton", command=self.ok).pack(
            side="right", padx=6
        )

        window.bind("<Return>", lambda event: self.ok())
        window.bind("<Escape>", lambda event: self.cancel())
        window.protocol("WM_DELETE_WINDOW", self.cancel)
        window.geometry(f"+{parent.winfo_rootx() + px(60)}+{parent.winfo_rooty() + px(60)}")
        window.grab_set()

    def set_all(self, ticked):
        for variable in self.vars.values():
            variable.set(ticked)

    def ok(self):
        names = [name for name, variable in self.vars.items() if variable.get()]
        self.window.destroy()
        self._on_ok(names)

    def cancel(self):
        self.window.destroy()

    def _on_wheel(self, event):
        # Only scroll when the checkboxes do not all fit, otherwise the list would drift
        if self._holder.winfo_reqheight() > self._canvas.winfo_height():
            self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--daemon" in argv:
        import daemon

        return daemon.main(["run"] + [arg for arg in argv if arg != "--daemon"])
    try:
        # Sharp text on high-DPI Windows displays
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    # Two copies working on the same queue.json would both publish every scheduled post.
    # The lock is held for as long as this variable lives, i.e. until the app exits.
    folder = os.path.dirname(os.path.abspath(__file__))
    lock = store.lock_file(os.path.join(folder, "app.lock"))
    if lock is None:
        root.withdraw()
        running = store.lock_file(os.path.join(folder, "daemon.lock"))
        if running is None:
            reason = (
                "Chương trình nền (daemon.py) đang chạy và đăng các bài hẹn giờ.\n\n"
                "Hãy dừng nó (python daemon.py status để kiểm tra) trước khi mở cửa sổ này."
            )
        else:
            running.close()
            reason = "Ứng dụng đang mở ở một cửa sổ khác.\n\nChạy hai bản cùng lúc sẽ đăng trùng các bài hẹn giờ."
        messagebox.showerror("Facebook Auto Poster", reason)
        root.destroy()
        return 3
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
