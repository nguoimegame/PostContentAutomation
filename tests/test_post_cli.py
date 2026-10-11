import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

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
    def test_reports_failure_when_there_is_no_session(self):
        self.assertIs(self.quietly(post.make_post, "xin chào"), False)


class MainExitCodeTest(EmptyWorkingDirTest):
    def test_exit_code_is_1_when_posting_fails(self):
        self.assertEqual(self.quietly(post.main, ["xin chào"]), 1)

    def test_exit_code_is_1_for_a_missing_content_file(self):
        self.assertEqual(self.quietly(post.main, ["--content-file", "khong-co.txt"]), 1)


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
