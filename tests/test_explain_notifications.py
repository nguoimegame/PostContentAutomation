import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

import ai
import app as app_module
import daemon
import jobflow
import runner
import scheduler
import store
from tests.test_ai_features import FakeEngine
from tests.test_app import AppTestCase
from tests.test_daemon import DaemonTestCase

ANSWER = {"summary": "Facebook đổi giao diện", "cause": "Không thấy nút", "steps": ["Chạy selftest", "Cập nhật bộ chọn"],
          "safe_to_retry": True, "check_facebook": False}


def failed_outcome(message="Thất bại (mã thoát 1), xem nhật ký và ảnh trong thư mục debug"):
    return jobflow.Outcome(False, message, "failed")


class ExplanationForTest(unittest.TestCase):
    def settings(self, **ai_settings):
        return {"ai": {"explain_failures": True, **ai_settings}}

    def test_nothing_is_sent_to_the_ai_unless_the_option_is_on(self):
        engine = FakeEngine(dict(ANSWER))
        text = jobflow.explanation_for({"ai": {"explain_failures": False}}, failed_outcome(), {}, ["dòng"], ".", lambda: engine)
        self.assertEqual((text, engine.calls), ("", []))
        self.assertEqual(jobflow.explanation_for({}, failed_outcome(), {}, [], ".", lambda: engine), "")

    def test_only_plain_failures_are_explained(self):
        for event in ("done", "session_expired", "blocked", "missed", None):
            engine = FakeEngine(dict(ANSWER))
            outcome = jobflow.Outcome(False, "x", event)
            self.assertEqual(jobflow.explanation_for(self.settings(), outcome, {}, [], ".", lambda: engine), "", event)
            self.assertEqual(engine.calls, [])

    def test_a_failure_is_explained_with_its_log_exit_code_and_screenshot(self):
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "debug"))
            old, new = (os.path.join(folder, "debug", f"latest_{kind}.png") for kind in ("old", "new"))
            for path, stamp in ((old, 1), (new, 2000000000)):
                with open(path, "wb") as handle:
                    handle.write(b"x")
                os.utime(path, (stamp, stamp))
            engine = FakeEngine(dict(ANSWER))
            outcome = failed_outcome("Thất bại (mã thoát 4), xem nhật ký")
            text = jobflow.explanation_for(self.settings(), outcome, {"content": "Bài"}, ["Khong tim thay nut"], folder, lambda: engine)
        self.assertIn("Facebook đổi giao diện", text)
        self.assertIn("• Chạy selftest", text)
        self.assertIn("Exit code: 4", engine.last["user"])
        self.assertIn("Khong tim thay nut", engine.last["user"])
        self.assertEqual(engine.last["images"], [new])

    def test_an_unreachable_ai_adds_nothing(self):
        engine = FakeEngine(ai.AIError("mất mạng"))
        self.assertEqual(jobflow.explanation_for(self.settings(), failed_outcome(), {}, [], ".", lambda: engine), "")

    def test_the_exit_code_is_read_from_the_message(self):
        self.assertEqual(jobflow.exit_code_in("Thất bại (mã thoát 4), xem"), 4)
        self.assertIsNone(jobflow.exit_code_in("Đã dừng"))
        self.assertIsNone(jobflow.exit_code_in(None))


class DaemonExplanationTest(DaemonTestCase):
    def enable(self):
        config = store.load_config(self.path("config.json"))
        config["ai"]["explain_failures"] = True
        store.save_config(self.path("config.json"), config)

    def test_a_failure_notification_carries_the_explanation(self):
        self.enable()
        engine = FakeEngine(dict(ANSWER))
        self.daemon.ai_factory = lambda: engine
        self.add()
        self.output = ["Dang mo trinh duyet...", "Khong tim thay nut tao bai viet"]
        self.exit_codes = [1]
        self.daemon.tick()
        event, _, text = self.notes[0]
        self.assertEqual(event, "failed")
        self.assertIn("Facebook đổi giao diện", text)
        self.assertIn("Khong tim thay nut tao bai viet", engine.last["user"])
        self.assertIn("Exit code: 1", engine.last["user"])

    def test_nothing_is_asked_when_the_option_is_off(self):
        engine = FakeEngine(dict(ANSWER))
        self.daemon.ai_factory = lambda: engine
        self.add()
        self.exit_codes = [1]
        self.daemon.tick()
        self.assertEqual(engine.calls, [])
        self.assertNotIn("Facebook đổi giao diện", self.notes[0][2])

    def test_success_and_expired_sessions_are_not_explained(self):
        self.enable()
        engine = FakeEngine(dict(ANSWER), dict(ANSWER))
        self.daemon.ai_factory = lambda: engine
        self.add(run_at=datetime(2026, 10, 17, 8, 0))
        self.add(run_at=datetime(2026, 10, 17, 8, 1))
        self.exit_codes = [0, runner.SESSION_EXPIRED_EXIT]
        self.daemon.tick()
        self.daemon.tick()
        self.assertEqual(engine.calls, [])

    def test_a_failing_ai_does_not_stop_the_notification(self):
        self.enable()
        self.daemon.ai_factory = lambda: FakeEngine(ai.AIError("mất mạng"))
        self.add()
        self.exit_codes = [1]
        self.daemon.tick()
        self.assertEqual(self.notes[0][0], "failed")


class AppExplanationTest(AppTestCase):
    def prepare(self, explain):
        app = self.start_app()
        app.set_notifications({"telegram_token": "T", "telegram_chat": "1", "events": ["failed"]})
        app.settings["ai"]["explain_failures"] = explain
        self.engine = FakeEngine(dict(ANSWER))
        app.ai_factory = lambda: self.engine
        return app

    def test_the_explanation_is_added_off_the_ui_thread(self):
        app = self.prepare(True)
        sent = threading.Event()
        seen = {}

        def fake_notify(settings, event, text, job=None, senders=None):
            seen.update(event=event, text=text, thread=threading.current_thread())
            sent.set()
            return []

        with mock.patch.object(app_module.notify, "notify", fake_notify):
            app.enqueue("FAIL", "", "", datetime.now() - timedelta(minutes=1), "none")
            app.tick()
            self.pump(lambda: not app.is_busy())
            self.assertTrue(sent.wait(10))
        self.assertEqual(seen["event"], "failed")
        self.assertIn("Facebook đổi giao diện", seen["text"])
        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertIn("Đăng bài giả lập", self.engine.last["user"])

    def test_when_the_option_is_off_the_ai_is_not_used(self):
        app = self.prepare(False)
        with mock.patch.object(app_module.notify, "notify_in_background") as send:
            app.enqueue("FAIL", "", "", datetime.now() - timedelta(minutes=1), "none")
            app.tick()
            self.pump(lambda: not app.is_busy())
        send.assert_called_once()
        self.assertEqual(self.engine.calls, [])

    def test_without_a_channel_nothing_is_asked_even_if_the_option_is_on(self):
        app = self.start_app()
        app.settings["ai"]["explain_failures"] = True
        self.engine = FakeEngine(dict(ANSWER))
        app.ai_factory = lambda: self.engine
        app.enqueue("FAIL", "", "", datetime.now() - timedelta(minutes=1), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(self.engine.calls, [])


if __name__ == "__main__":
    unittest.main()
