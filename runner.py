"""Run post.py / login.py as child processes and stream their output back."""
import os
import subprocess
import threading

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Exit code post.py uses for "Facebook wants a new login" (post.EXIT_SESSION_EXPIRED).
# Repeated here because importing post would pull Playwright into the UI process.
SESSION_EXPIRED_EXIT = 2


def console_python(executable):
    """Swap pythonw.exe for the python.exe next to it so the child has a stdout."""
    folder, name = os.path.split(executable)
    if name.lower() == "pythonw.exe":
        candidate = os.path.join(folder, "python.exe")
        if os.path.exists(candidate):
            return candidate
    return executable


def build_post_command(python_exe, base_dir, content_file, page_url="", image_path="", headless=False):
    command = [python_exe, "-u", os.path.join(base_dir, "post.py"), "--content-file", content_file]
    if page_url:
        command += ["--page", page_url]
    if image_path:
        command += ["--image", image_path]
    if headless:
        command.append("--headless")
    return command


def build_login_command(python_exe, base_dir):
    return [python_exe, "-u", os.path.join(base_dir, "login.py")]


class ProcessRunner:
    """Runs one child process at a time.

    `on_line(text)` and `on_exit(code)` are called from the reader thread, not
    the UI thread: the caller has to hand them over to its own thread.
    """

    def __init__(self, on_line, on_exit):
        self._on_line = on_line
        self._on_exit = on_exit
        self._process = None
        self._thread = None

    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def start(self, command, cwd=None):
        if self.is_running():
            raise RuntimeError("Đang có một tác vụ khác chạy.")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self._process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        self._thread = threading.Thread(target=self._pump, args=(self._process,), daemon=True)
        self._thread.start()

    def _pump(self, process):
        # Leaving the block closes the pipes and waits for the exit code
        with process:
            for line in process.stdout:
                self._on_line(line.rstrip("\r\n"))
        self._on_exit(process.returncode)

    def send_enter(self):
        """Send ENTER to a script waiting on input() (login.py)."""
        if not self.is_running():
            return
        try:
            self._process.stdin.write("\n")
            self._process.stdin.flush()
        except (OSError, ValueError):
            pass

    def stop(self):
        """Kill the child process together with whatever it spawned (the browser)."""
        if not self.is_running():
            return
        if os.name == "nt":
            killed = subprocess.run(
                ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=NO_WINDOW,
            )
            if killed.returncode == 0:
                return
        self._process.kill()

    def wait(self, timeout=None):
        """Block until on_exit has been called. Returns False on timeout."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()
