"""The calendar tab: queued posts laid out by week or month, with drag and drop to change their time."""
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import ttk

import schedule_view

DAY_NAMES = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
STATUS_FILL = {
    "pending": "#d6e8ff",
    "running": "#cde6ff",
    "done": "#e3e3e3",
    "failed": "#ffd9d6",
    "missed": "#ffe9c4",
}
GHOST_FILL = "#f2f7ff"
OUTLINE = "#8aa4c8"
TODAY_FILL = "#fff8dc"
DRAG_PIXELS = 5


class CalendarTab(ttk.Frame):
    """get_jobs() -> list of jobs, move_job(job_id, datetime) moves one (raising ValueError if it may not),
    open_job(job_id) edits one, label_for(job) is the text on its chip."""

    def __init__(self, parent, get_jobs, move_job, open_job, label_for, now=datetime.now, px=lambda value: value, on_error=None):
        super().__init__(parent, padding=6)
        self.get_jobs = get_jobs
        self.move_job = move_job
        self.open_job = open_job
        self.label_for = label_for
        self.now = now
        self.px = px
        self.on_error = on_error or (lambda message: None)
        self.mode = "week"
        self.anchor = now().date()
        self.chips = []  # (x0, y0, x1, y1, job_id, movable)
        self._drag = None
        self.geometry = {}

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="◀", width=3, command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(bar, text="Hôm nay", command=self.go_today).pack(side="left", padx=4)
        ttk.Button(bar, text="▶", width=3, command=lambda: self.step(1)).pack(side="left")
        self.title_var = tk.StringVar(master=self)
        ttk.Label(bar, textvariable=self.title_var, font=("Segoe UI", 10, "bold")).pack(side="left", padx=12)
        self.mode_var = tk.StringVar(master=self, value="week")
        for value, text in (("month", "Tháng"), ("week", "Tuần")):
            ttk.Radiobutton(bar, text=text, value=value, variable=self.mode_var, command=self._on_mode).pack(side="right", padx=2)
        ttk.Label(self, style="Hint.TLabel", text="Kéo một bài đang chờ sang ô khác để đổi giờ (tuần: theo từng 15 phút; tháng: đổi ngày). Nhấp đúp để sửa.").pack(anchor="w", pady=(4, 2))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, background="white", highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda event: self.refresh())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"))

    # -- navigation ---------------------------------------------------------------------------------------

    def _on_mode(self):
        self.mode = self.mode_var.get()
        self.refresh()

    def set_mode(self, mode):
        self.mode_var.set(mode)
        self._on_mode()

    def go_today(self):
        self.anchor = self.now().date()
        self.refresh()

    def step(self, direction):
        if self.mode == "month":
            year, month = schedule_view.shift_month(self.anchor.year, self.anchor.month, direction)
            self.anchor = date(year, month, 1)
        else:
            self.anchor += timedelta(days=7 * direction)
        self.refresh()

    # -- drawing ------------------------------------------------------------------------------------------

    def refresh(self):
        self.canvas.delete("all")
        self.chips = []
        self.title_var.set(schedule_view.title_for(self.mode, self.anchor))
        width = max(self.canvas.winfo_width(), self.px(560))
        if self.mode == "month":
            self._draw_month(width, max(self.canvas.winfo_height(), self.px(300)))
        else:
            self._draw_week(width)

    def _chip(self, x0, y0, x1, y1, job, moment, ghost=False):
        fill = GHOST_FILL if ghost else STATUS_FILL.get(job["status"], "white")
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=OUTLINE, dash=(2, 2) if ghost else ())
        text = f"{moment:%H:%M} {self.label_for(job)}"
        self.canvas.create_text(x0 + 3, (y0 + y1) / 2, text=text, anchor="w", width=max(x1 - x0 - 6, 10),
                                font=("Segoe UI", 8), fill="#555555" if ghost else "#111111")
        if not ghost:
            self.chips.append((x0, y0, x1, y1, job["id"], job["status"] == "pending"))

    def _draw_week(self, width):
        gutter, header, row = self.px(46), self.px(24), self.px(30)
        col = (width - gutter) / 7
        days = schedule_view.week_days(self.anchor)
        self.geometry = {"mode": "week", "gutter": gutter, "header": header, "row": row, "col": col, "days": days}
        today = self.now().date()
        for index, day in enumerate(days):
            x0 = gutter + index * col
            if day == today:
                self.canvas.create_rectangle(x0, 0, x0 + col, header + 24 * row, fill=TODAY_FILL, outline="")
            self.canvas.create_text(x0 + col / 2, header / 2, text=f"{DAY_NAMES[index]} {day:%d/%m}", font=("Segoe UI", 9, "bold"))
            self.canvas.create_line(x0, 0, x0, header + 24 * row, fill="#dddddd")
        for hour in range(24):
            y = header + hour * row
            self.canvas.create_line(gutter, y, width, y, fill="#e6e6e6")
            self.canvas.create_text(gutter - 6, y + 8, text=f"{hour:02d}:00", anchor="e", font=("Segoe UI", 8), fill="#777777")
        if today in days:
            moment = self.now()
            y = header + (moment.hour + moment.minute / 60) * row
            self.canvas.create_line(gutter, y, width, y, fill="#d93025", width=2)

        first = datetime.combine(days[0], datetime.min.time())
        last = first + timedelta(days=7)
        by_day = schedule_view.jobs_by_day(self.get_jobs())
        for index, day in enumerate(days):
            slots = {}
            for job in by_day.get(day, []):
                moment = _ready(job)
                slots.setdefault(moment.hour, []).append((moment, job))
            for hour, items in slots.items():
                share = (col - 4) / len(items)
                for position, (moment, job) in enumerate(items):
                    x0 = gutter + index * col + 2 + position * share
                    y0 = header + (moment.hour + moment.minute / 60) * row
                    self._chip(x0, y0, x0 + share - 2, y0 + row - 2, job, moment)
        for job in self.get_jobs():
            for moment in schedule_view.projected_runs(job, first, last):
                index = (moment.date() - days[0]).days
                x0 = gutter + index * col + 2
                y0 = header + (moment.hour + moment.minute / 60) * row
                self._chip(x0, y0, x0 + col - 4, y0 + row - 2, job, moment, ghost=True)
        self.canvas.configure(scrollregion=(0, 0, width, header + 24 * row))
        if not getattr(self, "_scrolled", False):
            self._scrolled = True
            self.canvas.yview_moveto((header + 7 * row) / (header + 24 * row))

    def _draw_month(self, width, height):
        weeks = schedule_view.month_weeks(self.anchor.year, self.anchor.month)
        header = self.px(22)
        row = max((height - header) / len(weeks), self.px(70))
        col = width / 7
        line = self.px(15)
        self.geometry = {"mode": "month", "header": header, "row": row, "col": col, "weeks": weeks}
        today = self.now().date()
        by_day = schedule_view.jobs_by_day(self.get_jobs())
        for index, name in enumerate(DAY_NAMES):
            self.canvas.create_text(index * col + col / 2, header / 2, text=name, font=("Segoe UI", 9, "bold"))
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                x0, y0 = c * col, header + r * row
                fill = TODAY_FILL if day == today else ("#fafafa" if day.month != self.anchor.month else "white")
                self.canvas.create_rectangle(x0, y0, x0 + col, y0 + row, fill=fill, outline="#dddddd")
                self.canvas.create_text(x0 + 4, y0 + 8, text=str(day.day), anchor="w", font=("Segoe UI", 8, "bold"),
                                        fill="#999999" if day.month != self.anchor.month else "#333333")
                items = by_day.get(day, [])
                room = max(int((row - self.px(20)) // line), 1)
                shown = items if len(items) <= room else items[: room - 1]
                for k, job in enumerate(shown):
                    top = y0 + self.px(18) + k * line
                    self._chip(x0 + 2, top, x0 + col - 2, top + line - 1, job, _ready(job))
                if len(shown) < len(items):
                    self.canvas.create_text(x0 + 4, y0 + self.px(18) + len(shown) * line + line / 2, anchor="w",
                                            text=f"+{len(items) - len(shown)} bài nữa", font=("Segoe UI", 8), fill="#777777")
        self.canvas.configure(scrollregion=(0, 0, width, header + row * len(weeks)))

    # -- positions ----------------------------------------------------------------------------------------

    def moment_at(self, x, y):
        """The date and time under canvas position (x, y), or None outside the grid."""
        geometry = self.geometry
        if not geometry:
            return None
        if geometry["mode"] == "week":
            if x < geometry["gutter"] or y < geometry["header"]:
                return None
            index = int((x - geometry["gutter"]) // geometry["col"])
            if not 0 <= index < 7:
                return None
            minutes = (y - geometry["header"]) / geometry["row"] * 60
            return datetime.combine(geometry["days"][index], schedule_view.minutes_to_time(round(minutes / 15) * 15))
        if y < geometry["header"]:
            return None
        r, c = int((y - geometry["header"]) // geometry["row"]), int(x // geometry["col"])
        if not (0 <= r < len(geometry["weeks"]) and 0 <= c < 7):
            return None
        return datetime.combine(geometry["weeks"][r][c], datetime.min.time())

    def chip_at(self, x, y):
        for x0, y0, x1, y1, job_id, movable in reversed(self.chips):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return job_id, movable
        return None

    # -- mouse --------------------------------------------------------------------------------------------

    def _canvas_xy(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _on_press(self, event):
        x, y = self._canvas_xy(event)
        hit = self.chip_at(x, y)
        self._drag = {"job_id": hit[0], "movable": hit[1], "start": (x, y), "moved": False, "ghost": None} if hit else None

    def _on_motion(self, event):
        drag = self._drag
        if not drag or not drag["movable"]:
            return
        x, y = self._canvas_xy(event)
        if not drag["moved"] and abs(x - drag["start"][0]) + abs(y - drag["start"][1]) < self.px(DRAG_PIXELS):
            return
        drag["moved"] = True
        if drag["ghost"] is not None:
            self.canvas.delete(drag["ghost"])
        w, h = self.px(60), self.px(14)
        drag["ghost"] = self.canvas.create_rectangle(x - w / 2, y - h / 2, x + w / 2, y + h / 2, fill="#ffd966", outline="#b38f00")

    def _on_release(self, event):
        drag, self._drag = self._drag, None
        if not drag or not drag["moved"]:
            return
        if drag["ghost"] is not None:
            self.canvas.delete(drag["ghost"])
        x, y = self._canvas_xy(event)
        self.drop(drag["job_id"], x, y)

    def drop(self, job_id, x, y):
        """Move a job to the grid position (x, y). Errors are reported through on_error, then the view is redrawn."""
        moment = self.moment_at(x, y)
        if moment is not None:
            job = next((item for item in self.get_jobs() if item["id"] == job_id), None)
            try:
                if job is None:
                    raise ValueError("Không tìm thấy bài này nữa.")
                target = schedule_view.drop_target(job, moment, self.now(), self.mode)
                if target != _ready(job):
                    self.move_job(job_id, target)
            except ValueError as error:
                self.on_error(str(error))
        self.refresh()

    def _on_double(self, event):
        hit = self.chip_at(*self._canvas_xy(event))
        if hit:
            self.open_job(hit[0])


def _ready(job):
    import scheduler

    return scheduler.ready_at(job)
