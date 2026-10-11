import contextlib
import io
import os
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
