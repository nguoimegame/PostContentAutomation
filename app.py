"""Tkinter UI for the Facebook auto-poster: compose, queue, pages, settings."""
import os
import queue
import sys
import tempfile
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import runner
import scheduler
import store

PERSONAL = "(Trang cá nhân)"
REPEAT_LABELS = {"none": "Không lặp", "daily": "Hằng ngày", "weekly": "Hằng tuần"}
REPEAT_BY_LABEL = {label: key for key, label in REPEAT_LABELS.items()}
STATUS_LABELS = {
    "pending": "Chờ",
    "running": "Đang chạy",
    "done": "Xong",
    "failed": "Lỗi",
    "missed": "Lỡ",
}
STATUS_COLORS = {
    "running": "#0b61a4",
    "done": "#6b6b6b",
    "failed": "#b3261e",
    "missed": "#a15c00",
}
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
        self.jobs = store.load_queue(self.queue_path)
        if scheduler.recover_at_startup(self.jobs, datetime.now()):
            self._save_queue()

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

        self._build_ui()
        self._refresh_pages()
        self._refresh_templates()
        self._refresh_queue()
        self._refresh_session()
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

    # ------------------------------------------------------------------
    # Pages and templates
    # ------------------------------------------------------------------

    def add_page(self, name, url):
        """Add a Fanpage, or update the URL of the one with the same name."""
        name = name.strip()
        if not name:
            raise ValueError("Chưa nhập tên Fanpage.")
        if name == PERSONAL:
            raise ValueError("Tên này đã được dùng cho trang cá nhân.")
        url = store.normalize_page_url(url)
        self._upsert(self.settings["pages"], {"name": name, "url": url})
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
        return TargetDialog(
            self.root, self.available_targets(), self.selected_targets(), self.select_targets, self._px
        )

    def selected_page_urls(self):
        """URLs of the ticked targets, in list order; "" stands for the personal profile."""
        return [self._page_url(name) for name in self.selected_targets()]

    def _remember_targets(self):
        names = self.selected_targets()
        self.target_count_var.set(f"Đã chọn {len(names)} nơi: " + ", ".join(names) if names else "Chưa chọn nơi nào")
        self.settings["last_pages"] = names
        self._save_settings()

    def _save_settings(self):
        store.save_config(self.config_path, self.settings)

    def _save_queue(self):
        store.save_queue(self.queue_path, self.jobs)

    # ------------------------------------------------------------------
    # Queue and scheduling
    # ------------------------------------------------------------------

    def enqueue(self, content, page_url, image_path, run_at, repeat="none"):
        job = scheduler.new_job(content, page_url, image_path, run_at, repeat)
        self.jobs.append(job)
        self._save_queue()
        self._refresh_queue()
        return job

    def enqueue_many(self, content, page_urls, image_path, run_at, repeat="none", gap_minutes=0):
        """Queue the same post once per target, `gap_minutes` apart, starting at `run_at`."""
        run_times = scheduler.stagger(run_at, len(page_urls), gap_minutes)
        jobs = [
            scheduler.new_job(content, page_url, image_path, moment, repeat)
            for page_url, moment in zip(page_urls, run_times)
        ]
        self.jobs.extend(jobs)
        self._save_queue()
        self._refresh_queue()
        return jobs

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
        if self._closed or self.is_busy() or not self.settings["scheduler_enabled"]:
            return
        due = scheduler.due_jobs(self.jobs, datetime.now())
        if due:
            self.run_job(due[0])

    def _tick_loop(self):
        self.tick()
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

    def publish_now(self, content, page_url="", image_path=""):
        """Publish immediately. Raises RuntimeError if it cannot start."""
        self._start_post(content, page_url, image_path, None)

    def publish_to(self, content, page_urls, image_path="", gap_minutes=0):
        """Publish to each target in turn, starting now.

        One target is posted directly. Several go through the queue, one post
        per target and `gap_minutes` apart, so each has its own status and a
        failure does not stop the rest. Returns the queued jobs (none for one target).
        """
        if not page_urls:
            raise ValueError("Chưa chọn nơi đăng.")
        if len(page_urls) == 1:
            self.publish_now(content, page_urls[0], image_path)
            return []
        self._check_can_start()
        if not self.settings["scheduler_enabled"]:
            raise RuntimeError(
                "Đăng lên nhiều nơi cần bật “Tự động đăng khi đến giờ” ở tab Hàng đợi."
            )
        start = datetime.now().replace(second=0, microsecond=0)
        jobs = self.enqueue_many(content, page_urls, image_path, start, "none", gap_minutes)
        self.run_job(jobs[0])
        return jobs

    def _check_can_start(self):
        if self.is_busy():
            raise RuntimeError("Đang có một tác vụ khác chạy, hãy đợi nó xong.")
        if store.session_saved_at(self.state_path) is None:
            raise RuntimeError("Chưa đăng nhập Facebook. Vào tab Cài đặt để đăng nhập trước.")

    def _start_post(self, content, page_url, image_path, job):
        self._check_can_start()

        handle, content_file = tempfile.mkstemp(prefix="fbpost_", suffix=".txt")
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        command = runner.build_post_command(
            self.python_exe,
            self.base_dir,
            content_file,
            page_url=page_url,
            image_path=image_path,
            headless=self.settings["headless"],
        )
        try:
            self.runner.start(command, cwd=self.base_dir)
        except OSError as error:
            self._remove_file(content_file)
            raise RuntimeError(f"Không chạy được post.py: {error}") from error

        self.task = {"kind": "post", "job": job, "content_file": content_file, "stopped": False}
        self.log(f"▶ Bắt đầu đăng bài lên {self._page_label(page_url)}")
        self._update_controls()

    def start_login(self):
        """Open the browser for a manual Facebook login (login.py)."""
        if self.is_busy():
            raise RuntimeError("Đang có một tác vụ khác chạy, hãy đợi nó xong.")
        command = runner.build_login_command(self.python_exe, self.base_dir)
        try:
            self.runner.start(command, cwd=self.base_dir)
        except OSError as error:
            raise RuntimeError(f"Không chạy được login.py: {error}") from error
        self.task = {"kind": "login", "stopped": False}
        self.log("▶ Mở trình duyệt để đăng nhập Facebook")
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
                    self.log(payload)
                else:
                    self._finish_task(payload)
        except queue.Empty:
            pass
        self._later(POLL_MS, self._poll_events)

    def _finish_task(self, exit_code):
        task, self.task = self.task, None
        if task is None:
            return
        success = exit_code == 0 and not task["stopped"]
        if task["kind"] == "post":
            self._remove_file(task["content_file"])
            if task["stopped"]:
                message = "Đã dừng theo yêu cầu"
            elif success:
                message = "Script chạy xong, không báo lỗi"
            else:
                message = f"Thất bại (mã thoát {exit_code}), xem nhật ký và ảnh debug"
            job = task["job"]
            if job is not None:
                scheduler.after_run(job, success, message, datetime.now())
                self._save_queue()
                self._refresh_queue()
        else:
            self._refresh_session()
            message = "Đã lưu phiên đăng nhập" if success else "Đăng nhập chưa hoàn tất"
        self.log(("✔ " if success else "✖ ") + message)
        self._update_controls()
        # Pick up the next due post without waiting for the regular tick
        self._later(500, self.tick)

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
        self._build_library_tab()
        self._build_settings_tab()

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
        ttk.Combobox(
            schedule,
            textvariable=self.repeat_var,
            values=list(REPEAT_LABELS.values()),
            state="readonly",
            width=12,
        ).pack(side="left", padx=6)
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

        columns = ("run_at", "repeat", "page", "content", "status", "note")
        headings = ("Giờ chạy", "Lặp", "Đăng lên", "Nội dung", "Trạng thái", "Ghi chú")
        widths = (125, 80, 130, 230, 80, 250)
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
        page_buttons = ttk.Frame(pages)
        page_buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(6, 0))
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
                "Trạng thái “Xong” chỉ có nghĩa là script chạy hết mà không báo lỗi. "
                "Hãy mở ảnh kết quả hoặc vào Facebook để chắc chắn bài đã lên."
            ),
        ).pack(anchor="w", pady=(0, 6))
        check_buttons = ttk.Frame(check)
        check_buttons.pack(anchor="w")
        ttk.Button(
            check_buttons,
            text="Mở ảnh kết quả gần nhất",
            command=lambda: self._open_path(os.path.join(self.base_dir, "debug_result.png")),
        ).pack(side="left")
        ttk.Button(
            check_buttons, text="Mở thư mục dự án", command=lambda: self._open_path(self.base_dir)
        ).pack(side="left", padx=6)

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
        entries = self.available_targets()
        # Config files written before multi-select only have a single last_page
        remembered = self.settings["last_pages"] or [self.settings.get("last_page", "")]
        ticked = [name for name in remembered if name in entries] or [PERSONAL]
        self.select_targets(ticked)
        self.page_list.delete(0, "end")
        for page in self.settings["pages"]:
            self.page_list.insert("end", f"{page['name']}  —  {page['url']}")

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
                    REPEAT_LABELS[job["repeat"]],
                    self._page_label(job["page_url"]),
                    preview,
                    STATUS_LABELS[job["status"]],
                    job["last_message"],
                ),
                tags=(job["status"],),
            )
        if selected and self.queue_table.exists(selected[0]):
            self.queue_table.selection_set(selected[0])
        self._update_controls()

    def _refresh_session(self):
        saved = store.session_saved_at(self.state_path)
        if saved is None:
            self.session_label.configure(text="Chưa đăng nhập", foreground="#b3261e")
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
        self.stop_button.configure(state="disabled" if kind is None else "normal")
        self.confirm_login_button.configure(state="normal" if kind == "login" else "disabled")

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
        path = filedialog.askopenfilename(
            parent=parent or self.root, title="Chọn ảnh hoặc video", filetypes=MEDIA_TYPES
        )
        if path:
            variable.set(os.path.normpath(path))

    @staticmethod
    def _check_post(content, image_path):
        if not content.strip() and not image_path:
            raise ValueError("Bài viết chưa có nội dung hoặc ảnh/video.")
        if image_path and not os.path.isfile(image_path):
            raise ValueError(f"Không tìm thấy tệp ảnh/video:\n{image_path}")

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
        return run_at, REPEAT_BY_LABEL[repeat_label]

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
            jobs = self.publish_to(content, page_urls, image_path, gap)
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
        jobs = self.enqueue_many(content, page_urls, image_path, run_at, repeat, gap)
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
        dialog.geometry(f"{self._px(620)}x{self._px(460)}")
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
        repeat_var = tk.StringVar(master=dialog, value=REPEAT_LABELS[job["repeat"]])
        ttk.Combobox(
            dialog,
            textvariable=repeat_var,
            values=list(REPEAT_LABELS.values()),
            state="readonly",
            width=12,
        ).grid(row=4, column=1, sticky="w", pady=3)

        def save():
            content = content_box.get("1.0", "end-1c")
            image_path = image_var.get().strip()
            try:
                self._check_post(content, image_path)
                run_at, repeat = self._read_schedule(run_at_var.get(), repeat_var.get())
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
            self._save_queue()
            self._refresh_queue()
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", padx=10, pady=10)
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

    def _on_save_page_clicked(self):
        try:
            self.add_page(self.page_name_var.get(), self.page_url_var.get())
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
    """Popup with one checkbox per target. OK hands the ticked names to `on_ok`."""

    MAX_VISIBLE_ROWS = 12

    def __init__(self, parent, names, ticked, on_ok, px):
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
            box = ttk.Checkbutton(holder, text=name, variable=variable)
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


def main():
    try:
        # Sharp text on high-DPI Windows displays
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
