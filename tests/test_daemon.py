import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import daemon
import enqueue
import runner
import scheduler
import store

SESSION = {"cookies": [{"name": "c_user", "value": "1", "domain": ".facebook.com", "path": "/", "expires": -1}], "origins": []}
T0 = datetime(2026, 10, 17, 9, 0)
HI = "https://www.facebook.com/hienglish"


class Clock:
    def __init__(self, moment=T0):
        self.moment = moment

    def __call__(self):
        return self.moment

    def advance(self, **kwargs):
        self.moment += timedelta(**kwargs)


class DaemonTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        store.save_json(self.path("state.json"), SESSION)
        store.save_config(self.path("config.json"), {"pages": [{"name": "Hi English", "url": HI, "language": ""}]})
        self.clock = Clock()
        self.commands = []
        self.exit_codes = []
        self.output = []
        self.notes = []
        self.daemon = self.make_daemon()

    def make_daemon(self):
        return daemon.Daemon(
            base_dir=self.dir,
            python_exe="python",
            run_command=self.fake_run,
            now=self.clock,
            notifier=lambda settings, event, text, job: self.notes.append((event, job["id"] if job else None, text)),
            sleep=lambda seconds: None,
            echo=False,
        )

    def path(self, name):
        return os.path.join(self.dir, name)

    def fake_run(self, command, cwd, on_line):
        index = command.index("--content-file")
        with open(command[index + 1], encoding="utf-8", newline="") as handle:
            content = handle.read()
        self.commands.append({"command": command, "content": content, "cwd": cwd})
        for line in self.output:
            on_line(line)
        code = self.exit_codes.pop(0) if self.exit_codes else 0
        return code(self) if callable(code) else code

    def add(self, content="Xin chào", run_at=None, **kwargs):
        job = scheduler.new_job(content, kwargs.pop("page_url", HI), kwargs.pop("image_path", ""),
                                run_at or T0 - timedelta(minutes=1), **kwargs)
        jobs = store.load_queue(self.path("queue.json"))
        jobs.append(job)
        store.save_queue(self.path("queue.json"), jobs)
        return job["id"]

    def job(self, job_id):
        return next(job for job in store.load_queue(self.path("queue.json")) if job["id"] == job_id)


class PublishTest(DaemonTestCase):
    def test_a_due_post_is_published_and_marked_done(self):
        job_id = self.add("Nội dung bài")
        self.daemon.start()
        self.assertTrue(self.daemon.tick())
        job = self.job(job_id)
        self.assertEqual(job["status"], "done")
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.commands[0]["content"], "Nội dung bài")
        self.assertIn(HI, self.commands[0]["command"])
        self.assertEqual(self.notes[0][0], "done")

    def test_the_content_file_is_removed_afterwards(self):
        self.add()
        self.daemon.tick()
        self.assertFalse(os.path.exists(self.commands[0]["command"][self.commands[0]["command"].index("--content-file") + 1]))

    def test_nothing_runs_when_nothing_is_due(self):
        self.add(run_at=T0 + timedelta(hours=1))
        self.assertFalse(self.daemon.tick())
        self.assertEqual(self.commands, [])

    def test_only_one_post_per_pass_earliest_first(self):
        late = self.add("Sau", run_at=T0 - timedelta(minutes=1))
        early = self.add("Trước", run_at=T0 - timedelta(minutes=3))
        self.daemon.tick()
        self.assertEqual([c["content"] for c in self.commands], ["Trước"])
        self.assertEqual((self.job(early)["status"], self.job(late)["status"]), ("done", "pending"))
        self.daemon.tick()
        self.assertEqual([c["content"] for c in self.commands], ["Trước", "Sau"])

    def test_the_link_of_the_post_is_stored(self):
        job_id = self.add()
        self.output = ["Đang đăng...", "POST_VERIFIED=yes", "POST_URL=https://www.facebook.com/x/posts/5"]
        self.daemon.tick()
        job = self.job(job_id)
        self.assertEqual(job["post_url"], "https://www.facebook.com/x/posts/5")
        self.assertIn("hiện trên trang", job["last_message"])
        self.assertIn("https://www.facebook.com/x/posts/5", self.notes[0][2])

    def test_a_post_not_seen_on_the_page_says_so(self):
        job_id = self.add()
        self.output = ["POST_VERIFIED=no"]
        self.daemon.tick()
        self.assertEqual(self.job(job_id)["status"], "done")
        self.assertIn("chưa thấy bài", self.job(job_id)["last_message"])

    def test_the_scheduler_switch_is_respected(self):
        self.add()
        config = store.load_config(self.path("config.json"))
        config["scheduler_enabled"] = False
        store.save_config(self.path("config.json"), config)
        self.assertFalse(self.daemon.tick())

    def test_command_carries_the_job_options(self):
        store.save_json(self.path("state.shop2.json"), SESSION)
        self.add(image_path="a.png|b.png", account="shop2")
        self.daemon.tick()
        command = self.commands[0]["command"]
        self.assertEqual([command[i + 1] for i, part in enumerate(command) if part == "--image"], ["a.png", "b.png"])
        self.assertEqual(command[command.index("--account") + 1], "shop2")

    def test_repeating_posts_may_repeat_their_text(self):
        self.add(repeat="daily")
        self.daemon.tick()
        self.assertIn("--allow-duplicate", self.commands[0]["command"])

    def test_a_repeating_post_is_scheduled_again(self):
        job_id = self.add(repeat="weekdays", run_at=datetime(2026, 10, 16, 8, 59))  # Friday
        self.clock.moment = datetime(2026, 10, 16, 9, 0)
        self.daemon.tick()
        job = self.job(job_id)
        self.assertEqual((job["status"], job["run_at"]), ("pending", "2026-10-19 08:59"))


class FailureTest(DaemonTestCase):
    def test_a_failed_post_is_marked_failed_and_reported(self):
        job_id = self.add()
        self.exit_codes = [1]
        self.daemon.tick()
        self.assertEqual(self.job(job_id)["status"], "failed")
        self.assertEqual(self.notes[0][0], "failed")

    def test_a_session_that_expired_is_reported_as_such(self):
        job_id = self.add()
        self.exit_codes = [runner.SESSION_EXPIRED_EXIT]
        self.daemon.tick()
        self.assertEqual(self.job(job_id)["status"], "failed")
        self.assertEqual(self.notes[0][0], "session_expired")

    def test_a_blocked_post_is_not_retried(self):
        job_id = self.add()
        self.exit_codes = [runner.BLOCKED_EXIT]
        self.daemon.tick()
        self.assertEqual(self.job(job_id)["status"], "failed")
        self.assertEqual(self.notes[0][0], "blocked")

    def test_no_login_means_no_attempt(self):
        os.remove(self.path("state.json"))
        job_id = self.add()
        self.assertFalse(self.daemon.tick())
        self.assertEqual(self.commands, [])
        job = self.job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("Chưa đăng nhập", job["last_message"])
        self.assertEqual(self.notes[0][0], "session_expired")

    def test_the_login_of_the_jobs_account_is_what_counts(self):
        job_id = self.add(account="shop2")
        self.assertFalse(self.daemon.tick())
        self.assertIn("shop2", self.job(job_id)["last_message"])

    def test_a_start_failure_marks_the_post_failed(self):
        job_id = self.add()

        def broken(command, cwd, on_line):
            raise OSError("không có python")

        self.daemon.run_command = broken
        self.daemon.tick()
        self.assertEqual(self.job(job_id)["status"], "failed")

    def test_the_runner_survives_an_error_in_a_pass(self):
        self.add()
        calls = []

        def explode():
            calls.append(1)
            raise RuntimeError("hỏng")

        self.daemon.tick = explode
        self.assertEqual(self.daemon.run_forever(once=True), 0)
        self.assertEqual(calls, [1])


class RetryTest(DaemonTestCase):
    def test_a_failure_before_the_button_is_tried_again_later(self):
        job_id = self.add()
        self.exit_codes = [runner.RETRYABLE_EXIT, 0]
        self.daemon.tick()
        job = self.job(job_id)
        self.assertEqual((job["status"], job["attempts"]), ("pending", 1))
        self.assertEqual(self.notes, [])
        self.assertFalse(self.daemon.tick())  # the wait is not over
        self.clock.advance(minutes=6)
        self.assertTrue(self.daemon.tick())
        self.assertEqual(self.job(job_id)["status"], "done")
        self.assertEqual([n[0] for n in self.notes], ["done"])

    def test_tries_run_out(self):
        job_id = self.add()
        self.exit_codes = [runner.RETRYABLE_EXIT] * 3
        for _ in range(3):
            self.daemon.tick()
            self.clock.advance(minutes=6)
        job = self.job(job_id)
        self.assertEqual(len(self.commands), 3)
        self.assertEqual(job["status"], "failed")
        self.assertEqual([n[0] for n in self.notes], ["failed"])

    def test_the_number_of_retries_comes_from_the_config(self):
        config = store.load_config(self.path("config.json"))
        config["retry_attempts"] = 0
        store.save_config(self.path("config.json"), config)
        job_id = self.add()
        self.exit_codes = [runner.RETRYABLE_EXIT]
        self.daemon.tick()
        self.assertEqual(self.job(job_id)["status"], "failed")

    def test_a_failure_after_the_button_is_never_retried(self):
        job_id = self.add()
        self.exit_codes = [1]
        self.daemon.tick()
        self.assertEqual(self.job(job_id)["status"], "failed")
        self.assertEqual(len(self.commands), 1)


class MissedTest(DaemonTestCase):
    def test_posts_that_are_too_late_at_start_are_missed_and_reported(self):
        job_id = self.add(run_at=T0 - timedelta(hours=3))
        self.daemon.start()
        self.assertEqual(self.job(job_id)["status"], "missed")
        self.assertEqual([n[0] for n in self.notes], ["missed"])
        self.assertFalse(self.daemon.tick())

    def test_a_cut_off_post_is_failed_at_start(self):
        job_id = self.add()
        jobs = store.load_queue(self.path("queue.json"))
        jobs[0]["status"] = "running"
        store.save_queue(self.path("queue.json"), jobs)
        self.daemon.start()
        self.assertEqual(self.job(job_id)["status"], "failed")

    def test_posts_that_came_due_while_the_computer_slept_are_missed(self):
        self.daemon.start()
        job_id = self.add(run_at=T0 + timedelta(minutes=10))
        self.clock.advance(hours=2)
        self.assertFalse(self.daemon.tick())
        self.assertEqual(self.job(job_id)["status"], "missed")
        self.assertEqual(self.notes[0][0], "missed")

    def test_the_grace_period_comes_from_the_config(self):
        config = store.load_config(self.path("config.json"))
        config["grace_minutes"] = 240
        store.save_config(self.path("config.json"), config)
        self.daemon.start()
        job_id = self.add(run_at=T0 + timedelta(minutes=10))
        self.clock.advance(hours=2)
        self.assertTrue(self.daemon.tick())
        self.assertEqual(self.job(job_id)["status"], "done")

    def test_a_long_post_does_not_make_the_next_ones_look_missed(self):
        self.daemon.start()
        slow = self.add("Chậm")
        later = self.add("Sau đó", run_at=T0 + timedelta(minutes=2))
        self.exit_codes = [lambda test: test.clock.advance(minutes=9) or 0]
        self.daemon.tick()
        self.assertEqual(self.job(slow)["status"], "done")
        self.assertTrue(self.daemon.tick())
        self.assertEqual(self.job(later)["status"], "done")


class SharedQueueTest(DaemonTestCase):
    def test_a_post_added_while_another_is_publishing_is_kept(self):
        first = self.add("Một")

        def add_meanwhile(test):
            with contextlib.redirect_stdout(io.StringIO()):
                code = enqueue.main(["--json", "add", "Hai", "--page", "Hi English", "--at", "2026-10-18 09:00"],
                                    base_dir=test.dir, now=T0)
            self.assertEqual(code, 0)
            return 0

        self.exit_codes = [add_meanwhile]
        self.daemon.run_forever(once=True)
        jobs = store.load_queue(self.path("queue.json"))
        self.assertEqual(sorted(job["content"] for job in jobs), ["Hai", "Một"])
        self.assertEqual(self.job(first)["status"], "done")

    def test_a_post_removed_while_publishing_stays_removed(self):
        job_id = self.add()

        def remove_meanwhile(test):
            store.save_queue(test.path("queue.json"), [])
            return 0

        self.exit_codes = [remove_meanwhile]
        self.daemon.tick()
        self.assertEqual(store.load_queue(self.path("queue.json")), [])

    def test_changes_made_by_the_cli_work_while_the_runner_holds_its_locks(self):
        app, lock = store.lock_file(self.path("app.lock")), store.lock_file(self.path("daemon.lock"))
        self.addCleanup(app.close)
        self.addCleanup(lock.close)
        with contextlib.redirect_stdout(io.StringIO()):
            code = enqueue.main(["--json", "add", "Hai", "--page", "Hi English", "--at", "2026-10-18 09:00"],
                                base_dir=self.dir, now=T0)
        self.assertEqual(code, 0)


class LockingTest(DaemonTestCase):
    def test_a_second_runner_refuses(self):
        lock = store.lock_file(self.path("app.lock"))
        self.addCleanup(lock.close)
        self.assertEqual(self.daemon.run_forever(once=True), daemon.EXIT_ALREADY_RUNNING)

    def test_the_locks_are_released_afterwards(self):
        self.assertEqual(self.daemon.run_forever(once=True), 0)
        for name in ("app.lock", "daemon.lock"):
            lock = store.lock_file(self.path(name))
            self.assertIsNotNone(lock, name)
            lock.close()

    def test_status_reports_a_running_daemon(self):
        self.assertFalse(daemon.status(self.dir)["running"])
        self.add(run_at=T0 + timedelta(hours=1))
        held = store.lock_file(self.path("daemon.lock"))
        self.addCleanup(held.close)
        info = daemon.status(self.dir)
        self.assertTrue(info["running"])
        self.assertEqual((info["pending"], info["next_run"]), (1, "2026-10-17 10:00"))


class TaskSchedulerTest(unittest.TestCase):
    def test_the_logon_task_runs_the_daemon_without_a_window(self):
        command = daemon.schtasks_create(script="D:\\x\\daemon.py", executable="C:\\Python\\python.exe")
        self.assertEqual(command[:6], ["schtasks", "/Create", "/TN", daemon.TASK_NAME, "/SC", "ONLOGON"])
        task = command[command.index("/TR") + 1]
        self.assertTrue(task.endswith('"D:\\x\\daemon.py" run'))
        self.assertIn("/F", command)

    def test_the_task_can_be_removed(self):
        self.assertEqual(daemon.schtasks_delete(), ["schtasks", "/Delete", "/TN", daemon.TASK_NAME, "/F"])

    def test_pythonw_is_preferred_when_it_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("python.exe", "pythonw.exe"):
                open(os.path.join(folder, name), "w").close()
            self.assertEqual(daemon.background_python(os.path.join(folder, "python.exe")), os.path.join(folder, "pythonw.exe"))
        self.assertEqual(daemon.background_python("/usr/bin/python3"), "/usr/bin/python3")

    def test_the_command_is_run_through_schtasks(self):
        calls = []

        def fake(command, **kwargs):
            calls.append(command)
            return mock.Mock(returncode=0, stdout="THÀNH CÔNG", stderr="")

        with mock.patch.object(daemon.os, "name", "nt"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(daemon.run_schtasks(["schtasks", "/Query"], runner_func=fake), 0)
        self.assertEqual(calls, [["schtasks", "/Query"]])

    def test_other_systems_get_a_hint_instead(self):
        with mock.patch.object(daemon.os, "name", "posix"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(daemon.run_schtasks(["schtasks"], runner_func=lambda *a, **k: self.fail("không được chạy")), 1)


if __name__ == "__main__":
    unittest.main()
