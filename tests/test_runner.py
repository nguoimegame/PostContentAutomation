import os
import sys
import tempfile
import unittest

import runner


class BuildCommandTest(unittest.TestCase):
    def test_minimal_post_command(self):
        command = runner.build_post_command("py", "D:/app", "C:/tmp/content.txt")
        self.assertEqual(
            command,
            ["py", "-u", os.path.join("D:/app", "post.py"), "--content-file", "C:/tmp/content.txt"],
        )

    def test_post_command_with_every_option(self):
        command = runner.build_post_command(
            "py",
            "D:/app",
            "C:/tmp/content.txt",
            page_url="https://www.facebook.com/abc",
            image_path="D:/pic.png",
            headless=True,
        )
        self.assertEqual(command[command.index("--page") + 1], "https://www.facebook.com/abc")
        self.assertEqual(command[command.index("--image") + 1], "D:/pic.png")
        self.assertIn("--headless", command)

    def test_empty_options_are_left_out(self):
        command = runner.build_post_command(
            "py", "D:/app", "c.txt", page_url="", image_path="", headless=False
        )
        for flag in ("--page", "--image", "--headless"):
            self.assertNotIn(flag, command)

    def test_login_command(self):
        self.assertEqual(
            runner.build_login_command("py", "D:/app"),
            ["py", "-u", os.path.join("D:/app", "login.py")],
        )


class ConsolePythonTest(unittest.TestCase):
    def test_prefers_python_over_pythonw(self):
        with tempfile.TemporaryDirectory() as folder:
            windowed = os.path.join(folder, "pythonw.exe")
            console = os.path.join(folder, "python.exe")
            for path in (windowed, console):
                open(path, "w").close()
            self.assertEqual(runner.console_python(windowed), console)

    def test_keeps_pythonw_when_there_is_no_console_build(self):
        with tempfile.TemporaryDirectory() as folder:
            windowed = os.path.join(folder, "pythonw.exe")
            open(windowed, "w").close()
            self.assertEqual(runner.console_python(windowed), windowed)

    def test_leaves_a_normal_interpreter_alone(self):
        self.assertEqual(runner.console_python(sys.executable), sys.executable)


class ProcessRunnerTest(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.exit_codes = []
        self.runner = runner.ProcessRunner(self.lines.append, self.exit_codes.append)
        self.addCleanup(self.runner.stop)

    def run_python(self, code):
        self.runner.start([sys.executable, "-u", "-c", code])

    def test_streams_unicode_output_line_by_line(self):
        self.run_python("print('Đang tải trang ✨'); print('xong')")
        self.assertTrue(self.runner.wait(20))
        self.assertEqual(self.lines, ["Đang tải trang ✨", "xong"])
        self.assertEqual(self.exit_codes, [0])

    def test_reports_a_failing_exit_code(self):
        self.run_python("import sys; print('lỗi'); sys.exit(3)")
        self.assertTrue(self.runner.wait(20))
        self.assertEqual(self.exit_codes, [3])

    def test_error_output_is_captured_too(self):
        self.run_python("import sys; sys.stderr.write('hỏng\\n')")
        self.assertTrue(self.runner.wait(20))
        self.assertEqual(self.lines, ["hỏng"])

    def test_send_enter_unblocks_a_waiting_script(self):
        self.run_python("input(); print('đã lưu')")
        self.runner.send_enter()
        self.assertTrue(self.runner.wait(20))
        self.assertEqual(self.lines, ["đã lưu"])
        self.assertEqual(self.exit_codes, [0])

    def test_stop_ends_a_long_running_script(self):
        self.run_python("import time; print('bắt đầu'); time.sleep(120)")
        self.assertTrue(self.runner.is_running())
        self.runner.stop()
        self.assertTrue(self.runner.wait(20))
        self.assertFalse(self.runner.is_running())
        self.assertEqual(len(self.exit_codes), 1)
        self.assertNotEqual(self.exit_codes[0], 0)

    def test_refuses_to_start_two_scripts_at_once(self):
        self.run_python("import time; time.sleep(120)")
        with self.assertRaises(RuntimeError):
            self.run_python("print('hai')")

    def test_is_not_running_before_start(self):
        self.assertFalse(self.runner.is_running())


if __name__ == "__main__":
    unittest.main()
