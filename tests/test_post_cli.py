import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import history
import post
import store

REAL_STATE_PATH = post.STATE_PATH


class EmptyWorkingDirTest(unittest.TestCase):
    """Runs in an empty folder with its own config, history and screenshots: nothing real is touched."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.dir)
        for name, value in (
            ("CONFIG_PATH", os.path.join(self.dir, "config.json")),
            ("HISTORY_PATH", os.path.join(self.dir, "history.jsonl")),
            ("DEBUG_DIR", os.path.join(self.dir, "debug")),
            ("STATE_PATH", os.path.join(self.dir, "state.json")),
        ):
            patcher = mock.patch.object(post, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def quietly(self, func, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)

    def records(self):
        return history.load(post.HISTORY_PATH)


class MakePostResultTest(EmptyWorkingDirTest):
    def test_a_missing_session_means_a_login_is_needed(self):
        with self.assertRaises(post.SessionExpired):
            self.quietly(post.make_post, "xin chào", state_path=os.path.join(self.dir, "khong-co", "state.json"))

    def test_the_session_is_looked_up_next_to_the_scripts_not_in_the_working_directory(self):
        self.assertEqual(REAL_STATE_PATH, os.path.join(os.path.dirname(os.path.abspath(post.__file__)), "state.json"))

    def fake_open(self, seen):
        def fake_open(state_path, headless):
            seen["path"] = state_path
            raise post.SessionExpired("x")

        return fake_open

    def test_a_named_account_has_its_own_session_file(self):
        seen = {}
        with mock.patch.object(post, "open_session", self.fake_open(seen)):
            with self.assertRaises(post.SessionExpired):
                self.quietly(post.make_post, "xin chào", account="shop2")
        self.assertEqual(os.path.basename(seen["path"]), "state.shop2.json")

    def test_the_default_account_uses_state_json(self):
        seen = {}
        with mock.patch.object(post, "open_session", self.fake_open(seen)):
            with self.assertRaises(post.SessionExpired):
                self.quietly(post.make_post, "xin chào")
        self.assertEqual(seen["path"], post.STATE_PATH)


class MainRunner(EmptyWorkingDirTest):
    def run_main(self, *argv, **patches):
        with mock.patch.object(post, "make_post", **patches) as make_post:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = post.main(list(argv))
        return code, out.getvalue(), err.getvalue(), make_post


def pressed_the_button(*args, **kwargs):
    post._progress["clicked"] = True
    return False


class MainExitCodeTest(MainRunner):
    def test_exit_code_is_0_when_posted(self):
        self.assertEqual(self.run_main("xin chào", return_value=True)[0], 0)

    def test_a_failure_before_the_button_is_retryable(self):
        self.assertEqual(self.run_main("xin chào", return_value=False)[0], post.EXIT_RETRYABLE)

    def test_a_failure_after_the_button_is_not_retried(self):
        self.assertEqual(self.run_main("xin chào", side_effect=pressed_the_button)[0], 1)

    def test_exit_code_is_2_when_there_is_no_session(self):
        code = self.run_main("xin chào", side_effect=post.SessionExpired("het han"))[0]
        self.assertEqual(code, post.EXIT_SESSION_EXPIRED)

    def test_exit_code_is_1_for_a_missing_content_file(self):
        self.assertEqual(self.quietly(post.main, ["--content-file", "khong-co.txt"]), 1)

    def test_exit_code_is_1_for_a_missing_attachment_and_no_browser_is_started(self):
        code, _, _, make_post = self.run_main("xin chào", "--image", "khong-co.png", return_value=True)
        self.assertEqual(code, 1)
        make_post.assert_not_called()

    def test_exit_code_is_1_for_a_bad_account_name(self):
        code, _, _, make_post = self.run_main("xin chào", "--account", "../x", return_value=True)
        self.assertEqual(code, 1)
        make_post.assert_not_called()

    def test_dry_run_is_passed_on(self):
        _, _, _, make_post = self.run_main("xin chào", "--dry-run", return_value=True)
        self.assertIs(make_post.call_args.kwargs["dry_run"], True)

    def test_account_and_several_files_are_passed_on(self):
        for name in ("a.png", "b.png"):
            with open(name, "wb") as handle:
                handle.write(b"x")
        _, _, _, make_post = self.run_main(
            "xin chào", "--account", "shop2", "--image", "a.png", "--image", "b.png", return_value=True
        )
        self.assertEqual(make_post.call_args.kwargs["account"], "shop2")
        self.assertEqual(make_post.call_args.kwargs["image_path"], "a.png|b.png")


class JsonOutputTest(MainRunner):
    def result(self, *argv, **patches):
        code, out, err, _ = self.run_main(*argv, "--json", **patches)
        return code, json.loads(out), err

    def test_stdout_holds_only_the_json_result(self):
        def noisy(*args, **kwargs):
            print("Dang mo trinh duyet...")
            return True

        code, result, err = self.result("xin chào", side_effect=noisy)
        self.assertEqual((code, result["ok"], result["status"], result["exit_code"]), (0, True, "posted", 0))
        self.assertIn("Dang mo trinh duyet", err)

    def test_reports_a_post_that_can_be_retried(self):
        code, result, _ = self.result("xin chào", return_value=False)
        self.assertEqual((code, result["ok"], result["status"], result["retryable"]), (4, False, "failed", True))

    def test_reports_a_failure_that_may_have_posted(self):
        code, result, _ = self.result("xin chào", side_effect=pressed_the_button)
        self.assertEqual((code, result["status"], result["retryable"]), (1, "failed", False))
        self.assertIn("kiem tra", result["message"])

    def test_reports_an_expired_session(self):
        code, result, _ = self.result("xin chào", side_effect=post.SessionExpired("het han"))
        self.assertEqual((code, result["ok"], result["status"]), (2, False, "session_expired"))

    def test_reports_a_dry_run(self):
        code, result, _ = self.result("xin chào", "--dry-run", return_value=True)
        self.assertEqual((code, result["ok"], result["status"]), (0, True, "dry_run"))

    def test_reports_missing_content(self):
        code, out, _, _ = self.run_main("--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "failed")

    def test_reports_the_link_of_the_post(self):
        def posted(*args, **kwargs):
            post._progress.update(post_url="https://www.facebook.com/x/posts/1", verified=True)
            return True

        code, result, _ = self.result("xin chào", side_effect=posted)
        self.assertEqual((result["post_url"], result["verified"]), ("https://www.facebook.com/x/posts/1", True))
        self.assertIn("hien tren trang", result["message"])

    def test_says_when_the_post_was_not_seen_on_the_page(self):
        def posted(*args, **kwargs):
            post._progress.update(post_url=None, verified=False)
            return True

        _, result, _ = self.result("xin chào", side_effect=posted)
        self.assertEqual(result["status"], "posted")
        self.assertIs(result["verified"], False)
        self.assertIn("kiem duyet", result["message"])

    def test_lists_the_screenshots_written_during_the_run(self):
        os.makedirs(post.DEBUG_DIR)
        old = os.path.join(post.DEBUG_DIR, "20260101-000000_old.png")
        with open(old, "wb") as handle:
            handle.write(b"png")
        os.utime(old, (1, 1))

        def screenshot(*args, **kwargs):
            with open(os.path.join(post.DEBUG_DIR, "20261017-090000_not_posted.png"), "wb") as handle:
                handle.write(b"png")
            return False

        _, result, _ = self.result("xin chào", side_effect=screenshot)
        self.assertEqual([os.path.basename(path) for path in result["debug_images"]], ["20261017-090000_not_posted.png"])


class HistoryAndSpamTest(MainRunner):
    def test_a_posted_post_is_recorded(self):
        def posted(*args, **kwargs):
            post._progress.update(post_url="https://www.facebook.com/x/posts/1", verified=True)
            return True

        self.run_main("xin chào cả nhà", "--page", "https://www.facebook.com/hi", "--account", "shop", side_effect=posted)
        (record,) = self.records()
        self.assertEqual(
            (record["status"], record["content"], record["page_url"], record["account"], record["post_url"]),
            ("posted", "xin chào cả nhà", "https://www.facebook.com/hi", "shop", "https://www.facebook.com/x/posts/1"),
        )

    def test_failures_are_recorded_too(self):
        self.run_main("xin chào", return_value=False)
        self.run_main("xin chào", side_effect=post.SessionExpired("het han"))
        self.assertEqual([r["status"] for r in self.records()], ["failed", "session_expired"])

    def test_a_dry_run_leaves_no_record(self):
        self.run_main("xin chào", "--dry-run", return_value=True)
        self.assertEqual(self.records(), [])

    def test_the_same_text_on_the_same_page_is_refused(self):
        self.run_main("Xin chào cả nhà", "--page", "https://www.facebook.com/hi", return_value=True)
        code, out, _, make_post = self.run_main(
            "xin chào cả nhà", "--page", "https://www.facebook.com/hi", return_value=True
        )
        self.assertEqual(code, post.EXIT_BLOCKED)
        self.assertEqual(make_post.call_count, 0)
        self.assertEqual(self.records()[-1]["status"], "blocked")

    def test_blocked_is_reported_in_json(self):
        self.run_main("Xin chào cả nhà", return_value=True)
        code, out, _, _ = self.run_main("Xin chào cả nhà", "--json", return_value=True)
        result = json.loads(out)
        self.assertEqual(
            (code, result["status"], result["ok"], result["retryable"]), (post.EXIT_BLOCKED, "blocked", False, False)
        )
        self.assertIn("--force", result["message"])

    def test_the_same_text_on_another_page_is_fine(self):
        self.run_main("Xin chào cả nhà", "--page", "https://www.facebook.com/hi", return_value=True)
        code = self.run_main("Xin chào cả nhà", "--page", "https://www.facebook.com/khac", return_value=True)[0]
        self.assertEqual(code, 0)

    def test_force_and_allow_duplicate_skip_the_check(self):
        self.run_main("Xin chào cả nhà", return_value=True)
        self.assertEqual(self.run_main("Xin chào cả nhà", "--force", return_value=True)[0], 0)
        self.assertEqual(self.run_main("Xin chào cả nhà", "--allow-duplicate", return_value=True)[0], 0)

    def test_force_also_skips_the_daily_limit(self):
        store.save_config(post.CONFIG_PATH, {"spam": {"max_per_page_per_day": 1}})
        self.run_main("bài thứ nhất về chủ đề này", return_value=True)
        self.assertEqual(self.run_main("một chủ đề khác hẳn", return_value=True)[0], post.EXIT_BLOCKED)
        self.assertEqual(self.run_main("một chủ đề khác hẳn", "--force", return_value=True)[0], 0)

    def test_the_limits_come_from_the_config(self):
        store.save_config(post.CONFIG_PATH, {"spam": {"max_per_page_per_day": 0, "max_per_day": 0, "duplicate_days": 0}})
        for _ in range(3):
            self.assertEqual(self.run_main("Xin chào cả nhà", return_value=True)[0], 0)

    def test_a_dry_run_only_warns(self):
        self.run_main("Xin chào cả nhà", return_value=True)
        code, out, _, make_post = self.run_main("Xin chào cả nhà", "--dry-run", "--json", return_value=True)
        result = json.loads(out)
        self.assertEqual((code, result["status"]), (0, "dry_run"))
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(make_post.call_count, 1)

    def test_an_old_post_does_not_block(self):
        old = datetime.now() - timedelta(days=30)
        history.append(post.HISTORY_PATH, {"status": "posted", "content": "Xin chào cả nhà", "page_url": ""}, old)
        self.assertEqual(self.run_main("Xin chào cả nhà", return_value=True)[0], 0)


class ListPagesTest(MainRunner):
    def setUp(self):
        super().setUp()
        config = {
            "pages": [{"name": "Hi English", "url": "https://www.facebook.com/hienglish", "language": "English (Mỹ)"}],
            "templates": [{"name": "Chào", "content": "xin chào", "media_path": ""}],
            "accounts": ["shop2"],
        }
        with open(post.CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(config, handle)

    def test_lists_pages_and_templates_without_posting(self):
        code, out, _, make_post = self.run_main("--list-pages")
        self.assertEqual(code, 0)
        self.assertIn("Hi English [English (Mỹ)]: https://www.facebook.com/hienglish", out)
        self.assertIn("Chào", out)
        self.assertIn("shop2", out)
        make_post.assert_not_called()

    def test_json_listing(self):
        code, out, _, _ = self.run_main("--list-pages", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["pages"][0]["url"], "https://www.facebook.com/hienglish")
        self.assertEqual(data["templates"], ["Chào"])
        self.assertIsNone(data["session_saved_at"])
        self.assertEqual([a["name"] for a in data["accounts"]], ["default", "shop2"])


class SelfTestCliTest(MainRunner):
    def test_the_report_is_printed_as_json(self):
        report = {"ok": True, "logged_in": True, "problems": [], "create_post": {}, "textbox": {}, "buttons": {},
                  "upload_input": False, "url": "x", "composer_opened": True}
        with mock.patch.object(post, "run_selftest", return_value=(0, report, "ok")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(post.main(["--selftest", "--json"]), 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])

    def test_a_missing_session_is_exit_2(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = post.main(["--selftest", "--json"])
        self.assertEqual(code, post.EXIT_SESSION_EXPIRED)


class OutputEncodingTest(unittest.TestCase):
    """Output redirected to a file or pipe uses the system code page, not UTF-8."""

    def run_python(self, code):
        # An ASCII-only stdout stands in for a code page that has no Vietnamese letters
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.abspath(post.__file__)),
            env=env,
            capture_output=True,
        )

    def test_vietnamese_cannot_be_printed_without_the_fix(self):
        result = self.run_python("print('\\u0110\\u0103ng b\\u00e0i')")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"UnicodeEncodeError", result.stderr)

    def test_use_utf8_output_makes_printing_safe(self):
        result = self.run_python("import post; post.use_utf8_output(); print('\\u0110\\u0103ng b\\u00e0i')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode("utf-8").strip(), "Đăng bài")

    def test_the_command_line_switches_to_utf8_before_printing(self):
        # No content at all: the run ends before anything is written to history.jsonl
        result = self.run_python("import post; post.main(['--list-pages']); print('\\u0110\\u0103ng b\\u00e0i')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Đăng bài", result.stdout.decode("utf-8"))


class ResolveContentTest(EmptyWorkingDirTest):
    def test_uses_the_positional_text(self):
        self.assertEqual(post.resolve_content("xin chào", None), "xin chào")

    def test_reads_the_content_file_as_utf8(self):
        text = "✨ Dòng 1\n- dòng 2 bắt đầu bằng gạch\n"
        path = os.path.join(self.dir, "content.txt")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        self.assertEqual(post.resolve_content(None, path), text)

    def test_content_file_wins_over_positional_text(self):
        path = os.path.join(self.dir, "content.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("từ tệp")
        self.assertEqual(post.resolve_content("từ dòng lệnh", path), "từ tệp")

    def test_requires_some_content(self):
        with self.assertRaises(ValueError):
            post.resolve_content(None, None)


if __name__ == "__main__":
    unittest.main()
