"""Background runner: publishes the queued posts without the app's window.

    python daemon.py run            keep running and post whatever is due (Ctrl+C stops it)
    python daemon.py run --once     do one pass and exit
    python daemon.py status         is it running, and what is waiting
    python daemon.py install-task   start it automatically when you log in to Windows (Task Scheduler)
    python daemon.py uninstall-task remove that task

`python app.py --daemon` does the same as `run`. Only one of the window and the runner can work on a
folder at a time. The runner re-reads queue.json on every pass, so `enqueue.py` can add, edit and remove
posts while it runs. It needs the same things as the app: a saved login (login.py) and a computer
that is awake (posts that came due while it slept are marked missed, like in the app).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta

import jobflow
import notify
import runner
import scheduler
import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POLL_SECONDS = 15
LOG_LIMIT = 1_000_000
TASK_NAME = "FacebookAutoPoster"
EXIT_ALREADY_RUNNING = 3


def paths(base_dir):
    return {
        "queue": os.path.join(base_dir, "queue.json"),
        "config": os.path.join(base_dir, "config.json"),
        "app_lock": os.path.join(base_dir, "app.lock"),
        "daemon_lock": os.path.join(base_dir, "daemon.lock"),
        "queue_lock": os.path.join(base_dir, "queue.lock"),
        "log": os.path.join(base_dir, "daemon.log"),
        "status": os.path.join(base_dir, "daemon.status.json"),
    }


def background_python(executable=None):
    """pythonw.exe (no console window) next to the interpreter, if there is one."""
    executable = executable or sys.executable
    folder, name = os.path.split(executable)
    candidate = os.path.join(folder, "pythonw.exe")
    if name.lower() == "python.exe" and os.path.exists(candidate):
        return candidate
    return executable


class Daemon:
    def __init__(self, base_dir=BASE_DIR, python_exe=None, run_command=None, now=datetime.now, notifier=None,
                 sleep=time.sleep, echo=True, ai_factory=None):
        self.base_dir = base_dir
        self.files = paths(base_dir)
        self.python_exe = python_exe or runner.console_python(sys.executable)
        self.run_command = run_command or runner.run_blocking
        self.now = now
        self.notifier = notifier or notify.notify
        self.sleep = sleep
        self.echo = echo
        self.ai_factory = ai_factory  # () -> an ai.AI; None builds one from config.json when it is needed
        self.last_tick = None
        self.started_at = None

    # -- small helpers ------------------------------------------------------------------------------------

    def log(self, text):
        line = f"[{self.now():%Y-%m-%d %H:%M:%S}] {text}"
        if self.echo:
            try:
                print(line, flush=True)
            except (OSError, ValueError, UnicodeError):
                pass
        try:
            path = self.files["log"]
            if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def settings(self):
        return store.load_config(self.files["config"])

    def read_jobs(self):
        jobs, dropped = scheduler.valid_jobs(store.load_queue(self.files["queue"]))
        if dropped:
            store.keep_aside(self.files["queue"])
            self.log(f"⚠ Bỏ qua {dropped} mục hỏng trong queue.json, bản gốc được giữ ở queue.json.corrupt")
        return jobs

    def save_jobs(self, jobs):
        try:
            store.save_queue(self.files["queue"], jobs)
        except OSError as error:
            self.log(f"⚠ Không lưu được queue.json: {error}")

    def write_status(self, **extra):
        data = {"pid": os.getpid(), "started_at": self.started_at, "last_tick": self.now().isoformat(timespec="seconds")}
        data.update(extra)
        try:
            store.save_json(self.files["status"], data)
        except OSError:
            pass

    def tell(self, settings, event, job, detail="", log_lines=()):
        text = notify.describe(event, job, jobflow.page_label(settings, job["page_url"]), detail)
        self.notifier(settings, event, text, job)

    # -- one pass ----------------------------------------------------------------------------------------

    def start(self):
        """Tidy the queue left by the last run: cut-off posts become failed, late ones missed."""
        settings = self.settings()
        grace = timedelta(minutes=settings["grace_minutes"])
        self.started_at = self.now().isoformat(timespec="seconds")
        with store.locked(self.files["queue_lock"]):
            jobs = self.read_jobs()
            before = {job["id"]: job["status"] for job in jobs}
            changed = scheduler.recover_at_startup(jobs, self.now(), grace)
            if changed:
                self.save_jobs(jobs)
        self.report_missed(settings, jobs, before)
        self.last_tick = self.now()

    def report_missed(self, settings, jobs, before):
        for job in jobs:
            if job["status"] == "missed" and before.get(job["id"]) != "missed":
                self.log(f"⏰ Lỡ bài hẹn {job['run_at']}: {job['last_message']}")
                self.tell(settings, "missed", job, job["last_message"])

    def tick(self):
        """Publish the next due post, if there is one. Returns True when a post was attempted."""
        now = self.now()
        settings = self.settings()
        grace = timedelta(minutes=settings["grace_minutes"])
        job = None
        failed_early = None
        with store.locked(self.files["queue_lock"]):
            jobs = self.read_jobs()
            before = {item["id"]: item["status"] for item in jobs}
            changed = False
            if self.last_tick is not None and now - self.last_tick > grace:
                # The computer slept or this process was frozen: what came due meanwhile is missed, not posted late
                changed = scheduler.skip_overdue(jobs, now, grace, "máy ngủ hoặc chương trình nền tạm dừng")
            self.last_tick = now
            if settings["scheduler_enabled"]:
                due = scheduler.due_jobs(jobs, now)
                if due:
                    job = due[0]
                    if store.session_saved_at(store.state_path_for(self.base_dir, job.get("account", ""))) is None:
                        who = f" (tài khoản {job['account']})" if job.get("account") else ""
                        scheduler.after_run(job, False, f"Chưa đăng nhập Facebook{who}. Chạy login.py để đăng nhập.", now)
                        scheduler.hold_back_batch(jobs, job, now)
                        failed_early, job = job, None
                    else:
                        job["status"] = "running"
                    changed = True
            if changed:
                self.save_jobs(jobs)
        self.report_missed(settings, jobs, before)
        if failed_early is not None:
            self.log(f"✖ {failed_early['last_message']}")
            self.tell(settings, "session_expired", failed_early, failed_early["last_message"])
        if job is None:
            self.write_status()
            return False
        self.publish(job, settings)
        self.last_tick = self.now()
        self.write_status()
        return True

    def publish(self, job, settings):
        job_id = job["id"]
        content_file = jobflow.write_content_file(job["content"])
        task = jobflow.new_task(job, content_file)
        command = jobflow.job_command(self.python_exe, self.base_dir, job, content_file, settings)
        self.log(f"▶ Đăng bài hẹn {job['run_at']} lên {jobflow.page_label(settings, job['page_url'])}")

        shown = []

        def on_line(line):
            jobflow.capture_line(task, line)
            shown.append(line)
            self.log(line)

        try:
            code = self.run_command(command, self.base_dir, on_line)
        except OSError as error:
            self.log(f"✖ Không chạy được post.py: {error}")
            code = 1
        finally:
            try:
                os.remove(content_file)
            except OSError:
                pass

        now = self.now()
        with store.locked(self.files["queue_lock"]):
            jobs = self.read_jobs()
            current = next((item for item in jobs if item["id"] == job_id), None)
            if current is None:
                self.log("⚠ Bài này đã bị xóa khỏi hàng đợi trong lúc đang đăng.")
                return None
            outcome = jobflow.finish(jobs, current, code, False, task["post_url"], task["verified"], settings, now)
            self.save_jobs(jobs)
        self.log(("✔ " if outcome.success else "✖ ") + outcome.message)
        if outcome.event:
            detail = outcome.message
            explanation = jobflow.explanation_for(settings, outcome, current, shown[-80:], self.base_dir,
                                                  (lambda: self.ai_factory()) if self.ai_factory else None)
            if explanation:
                detail += "\n" + explanation
            self.tell(settings, outcome.event, current, detail)
        return outcome

    # -- running ------------------------------------------------------------------------------------------

    def run_forever(self, once=False, poll=POLL_SECONDS):
        """Take the locks and keep posting. Returns an exit code."""
        app_lock = store.lock_file(self.files["app_lock"])
        if app_lock is None:
            self.log("Ứng dụng hoặc một chương trình nền khác đang chạy trong thư mục này; chỉ một bản được chạy.")
            return EXIT_ALREADY_RUNNING
        daemon_lock = store.lock_file(self.files["daemon_lock"])
        try:
            self.start()
            self.log("Chương trình nền đã chạy. Nhấn Ctrl+C để dừng.")
            while True:
                try:
                    self.tick()
                except Exception as error:  # noqa: BLE001 - one bad pass must not stop the runner for good
                    self.log(f"✖ Lỗi không mong đợi: {type(error).__name__}: {error}")
                    self.log(traceback.format_exc().rstrip())
                if once:
                    return 0
                self.sleep(poll)
        except KeyboardInterrupt:
            self.log("Đã dừng.")
            return 0
        finally:
            if daemon_lock is not None:
                daemon_lock.close()
            app_lock.close()


# -----------------------------------------------------------------------------------------------------------
# Status and Task Scheduler
# -----------------------------------------------------------------------------------------------------------


def status(base_dir=BASE_DIR):
    files = paths(base_dir)
    probe = store.lock_file(files["daemon_lock"])
    running = probe is None
    if probe is not None:
        probe.close()
    info = store.load_json(files["status"], {}) if running else {}
    jobs, _ = scheduler.valid_jobs(store.load_queue(files["queue"]))
    pending = sorted((job for job in jobs if job["status"] == "pending"), key=scheduler.ready_at)
    return {
        "running": running,
        "pid": info.get("pid") if running else None,
        "started_at": info.get("started_at") if running else None,
        "last_tick": info.get("last_tick") if running else None,
        "pending": len(pending),
        "next_run": scheduler.format_run_at(scheduler.ready_at(pending[0])) if pending else None,
    }


def task_command(script=None, executable=None):
    """The command Task Scheduler runs at logon."""
    script = script or os.path.join(BASE_DIR, "daemon.py")
    return f'"{background_python(executable)}" "{script}" run'


def schtasks_create(script=None, executable=None):
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON", "/TR", task_command(script, executable),
            "/RL", "LIMITED", "/F"]


def schtasks_delete():
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def run_schtasks(command, runner_func=subprocess.run):
    if os.name != "nt":
        print("Lệnh này chỉ dùng được trên Windows. Trên Linux/macOS hãy dùng systemd, launchd hoặc cron để chạy "
              "`python daemon.py run`.")
        return 1
    result = runner_func(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (result.stdout or "") + (result.stderr or "")
    print(output.strip())
    return result.returncode


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Chương trình nền đăng bài theo hàng đợi.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Chạy và đăng các bài đến giờ.")
    run.add_argument("--once", action="store_true", help="Chỉ chạy một lượt rồi thoát.")
    run.add_argument("--poll", type=int, default=POLL_SECONDS, help="Số giây giữa hai lượt kiểm tra.")
    stat = commands.add_parser("status", help="Xem chương trình nền có đang chạy không.")
    stat.add_argument("--json", action="store_true")
    commands.add_parser("install-task", help="Tự chạy khi đăng nhập Windows (Task Scheduler).")
    commands.add_parser("uninstall-task", help="Gỡ tác vụ đã cài.")
    args = parser.parse_args(argv)

    if args.command == "run":
        return Daemon().run_forever(once=args.once, poll=args.poll)
    if args.command == "status":
        info = status()
        if args.json:
            print(json.dumps(info, ensure_ascii=False))
        else:
            print(f"Đang chạy (pid {info['pid']}, từ {info['started_at']})" if info["running"] else "Không chạy")
            print(f"Bài đang chờ: {info['pending']}" + (f", bài kế tiếp: {info['next_run']}" if info["next_run"] else ""))
        return 0
    if args.command == "install-task":
        code = run_schtasks(schtasks_create())
        if code == 0:
            print("Đã cài. Chương trình nền sẽ tự chạy mỗi khi bạn đăng nhập Windows.")
        return code
    return run_schtasks(schtasks_delete())


if __name__ == "__main__":
    sys.exit(main())
