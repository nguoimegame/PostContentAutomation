import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import post


class EmptyWorkingDirTest(unittest.TestCase):
    """Chạy trong thư mục trống: không có state.json nên không mở trình duyệt."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.dir)

    def quietly(self, func, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)


class MakePostResultTest(EmptyWorkingDirTest):
    def missing_state(self):
        return os.path.join(self.dir, "khong-co", "state.json")

    def test_a_missing_session_means_a_login_is_needed(self):
        with self.assertRaises(post.SessionExpired):
            self.quietly(post.make_post, "xin chào", state_path=self.missing_state())

    def test_the_session_is_looked_up_next_to_the_scripts_not_in_the_working_directory(self):
        self.assertEqual(post.STATE_PATH, os.path.join(os.path.dirname(os.path.abspath(post.__file__)), "state.json"))


class MainRunner(EmptyWorkingDirTest):
    def run_main(self, *argv, **patches):
        with mock.patch.object(post, "make_post", **patches) as make_post:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = post.main(list(argv))
        return code, out.getvalue(), err.getvalue(), make_post


class MainExitCodeTest(MainRunner):
    def test_exit_code_is_1_when_posting_fails(self):
        self.assertEqual(self.run_main("xin chào", return_value=False)[0], 1)

    def test_exit_code_is_2_when_there_is_no_session(self):
        code = self.run_main("xin chào", side_effect=post.SessionExpired("het han"))[0]
        self.assertEqual(code, post.EXIT_SESSION_EXPIRED)

    def test_exit_code_is_0_when_posted(self):
        self.assertEqual(self.run_main("xin chào", return_value=True)[0], 0)

    def test_exit_code_is_1_for_a_missing_content_file(self):
        self.assertEqual(self.quietly(post.main, ["--content-file", "khong-co.txt"]), 1)

    def test_dry_run_is_passed_on(self):
        _, _, _, make_post = self.run_main("xin chào", "--dry-run", return_value=True)
        self.assertIs(make_post.call_args.kwargs["dry_run"], True)


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

    def test_reports_a_failed_post(self):
        code, result, _ = self.result("xin chào", return_value=False)
        self.assertEqual((code, result["ok"], result["status"]), (1, False, "failed"))

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

    def test_lists_the_debug_screenshots_written_during_the_run(self):
        def screenshot(*args, **kwargs):
            with open("debug_not_posted.png", "wb") as handle:
                handle.write(b"png")
            return False

        with open("debug_old.png", "wb") as handle:
            handle.write(b"png")
        os.utime("debug_old.png", (1, 1))
        _, result, _ = self.result("xin chào", side_effect=screenshot)
        self.assertEqual(result["debug_images"], ["debug_not_posted.png"])


class ListPagesTest(MainRunner):
    def setUp(self):
        super().setUp()
        config = {"pages": [{"name": "Hi English", "url": "https://www.facebook.com/hienglish", "language": "English (Mỹ)"}],
                  "templates": [{"name": "Chào", "content": "xin chào", "media_path": ""}]}
        path = os.path.join(self.dir, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        patcher = mock.patch.object(post, "CONFIG_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(post, "STATE_PATH", os.path.join(self.dir, "state.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lists_pages_and_templates_without_posting(self):
        code, out, _, make_post = self.run_main("--list-pages")
        self.assertEqual(code, 0)
        self.assertIn("Hi English [English (Mỹ)]: https://www.facebook.com/hienglish", out)
        self.assertIn("Chào", out)
        make_post.assert_not_called()

    def test_json_listing(self):
        code, out, _, _ = self.run_main("--list-pages", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["pages"][0]["url"], "https://www.facebook.com/hienglish")
        self.assertEqual(data["templates"], ["Chào"])
        self.assertIsNone(data["session_saved_at"])


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
        result = self.run_python(
            "import post; post.main(['--content-file', 'khong-co.txt']); print('\\u0110\\u0103ng b\\u00e0i')"
        )
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
