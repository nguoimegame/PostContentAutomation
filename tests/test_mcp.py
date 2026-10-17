import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

import history
import mcp_server
import store

NOW = datetime(2026, 10, 17, 9, 0)
HI = "https://www.facebook.com/hienglish"
SESSION = {"cookies": [{"name": "c_user", "value": "1", "domain": ".facebook.com", "path": "/", "expires": -1}], "origins": []}


class McpTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        store.save_config(
            os.path.join(self.dir, "config.json"),
            {"pages": [{"name": "Hi English", "url": HI, "language": "English (Mỹ)"}],
             "templates": [{"name": "Chào", "content": "xin chào", "media_path": ""}], "accounts": ["shop2"]},
        )
        store.save_json(os.path.join(self.dir, "state.json"), SESSION)
        self.calls = []
        self.reply = {"ok": True, "status": "posted", "exit_code": 0, "message": "xong"}
        self.stdout = None
        self.server = mcp_server.Server(base_dir=self.dir, python_exe="python", run_process=self.fake_process, now=lambda: NOW)

    def fake_process(self, command, **kwargs):
        content = None
        if "--content-file" in command:
            with open(command[command.index("--content-file") + 1], encoding="utf-8", newline="") as handle:
                content = handle.read()
        self.calls.append({"command": command, "content": content, "kwargs": kwargs})
        out = self.stdout if self.stdout is not None else "Dang mo...\n" + json.dumps(self.reply) + "\n"
        return SimpleNamespace(stdout=out, stderr="nhật ký\ndòng cuối", returncode=0 if self.reply.get("ok") else 1)

    def rpc(self, method, params=None, request_id=1):
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        return self.server.handle(message)

    def call(self, name, arguments=None):
        response = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        result = response["result"]
        return json.loads(result["content"][0]["text"]), result["isError"]

    def flags(self, index=0):
        return self.calls[index]["command"]


class ProtocolTest(McpTestCase):
    def test_initialize_agrees_on_a_known_version(self):
        for version in mcp_server.PROTOCOLS:
            response = self.rpc("initialize", {"protocolVersion": version})
            self.assertEqual(response["result"]["protocolVersion"], version)

    def test_an_unknown_version_gets_the_latest_one(self):
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], mcp_server.PROTOCOLS[0])

    def test_initialize_describes_the_server_and_its_rules(self):
        result = self.rpc("initialize", {})["result"]
        self.assertEqual(result["serverInfo"]["name"], "facebook-auto-poster")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("confirm=true", result["instructions"])

    def test_notifications_get_no_answer(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_ping(self):
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_unknown_methods_are_errors(self):
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)

    def test_malformed_messages_are_errors(self):
        self.assertEqual(self.server.handle({"id": 1})["error"]["code"], -32600)
        self.assertEqual(self.server.handle("text")["error"]["code"], -32600)
        self.assertEqual(self.rpc("tools/call", {"arguments": {}})["error"]["code"], -32602)

    def test_batches_are_answered_together(self):
        batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
        self.assertEqual([item["id"] for item in self.server.handle(batch)], [1, 2])

    def test_every_tool_is_described(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual(
            sorted(tool["name"] for tool in tools),
            ["daemon_status", "dry_run_post", "edit_job", "get_history", "list_pages", "list_queue", "post_now",
             "remove_job", "schedule_post", "selftest"],
        )
        for tool in tools:
            self.assertNotIn("handler", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
            self.assertIn("readOnlyHint", tool["annotations"])

    def test_tools_that_publish_are_marked_and_say_to_ask_first(self):
        tools = {tool["name"]: tool for tool in self.rpc("tools/list")["result"]["tools"]}
        for name in ("post_now", "schedule_post", "edit_job"):
            self.assertTrue(tools[name]["annotations"]["destructiveHint"], name)
            self.assertIn("confirm", tools[name]["inputSchema"]["required"], name)
            self.assertIn("ask the user", tools[name]["description"].lower(), name)
        for name in ("list_pages", "list_queue", "get_history", "daemon_status"):
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"], name)

    def test_the_serve_loop_reads_lines_and_writes_lines(self):
        source = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nkhông phải json\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        sink = io.StringIO()
        self.server.serve(source, sink)
        lines = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["error"]["code"], -32700)

    def test_a_bug_in_a_tool_does_not_stop_the_server(self):
        self.server.tools["list_pages"]["handler"] = lambda args: 1 / 0
        data, is_error = self.call("list_pages")
        self.assertTrue(is_error)
        self.assertIn("ZeroDivisionError", data["message"])
        self.assertEqual(self.rpc("ping")["result"], {})


class ValidationTest(McpTestCase):
    def test_missing_unknown_and_mistyped_arguments_are_refused(self):
        cases = [
            ("post_now", {"confirm": True}, "page"),
            ("list_queue", {"extra": 1}, "extra"),
            ("list_queue", {"status": "lạ"}, "status"),
            ("get_history", {"limit": "nhiều"}, "limit"),
            ("get_history", {"limit": 0}, "limit"),
            ("get_history", {"limit": True}, "limit"),
            ("dry_run_post", {"page": "personal", "images": "a.png"}, "images"),
            ("schedule_post", {"content": "x", "pages": [], "at": "2026-10-18 08:30", "confirm": True}, "pages"),
            ("nope", {}, "nope"),
        ]
        for name, arguments, expected in cases:
            with self.subTest(name=name, arguments=arguments):
                data, is_error = self.call(name, arguments)
                self.assertTrue(is_error)
                self.assertIn(expected, data["message"])
        self.assertEqual(self.calls, [])

    def test_arguments_must_be_an_object(self):
        response = self.rpc("tools/call", {"name": "list_pages", "arguments": [1]})
        self.assertTrue(response["result"]["isError"])


class ConfirmationTest(McpTestCase):
    def test_nothing_publishes_without_confirm(self):
        for name, arguments in (
            ("post_now", {"content": "x", "page": "personal"}),
            ("post_now", {"content": "x", "page": "personal", "confirm": False}),
            ("schedule_post", {"content": "x", "pages": ["personal"], "at": "2026-10-18 08:30"}),
            ("edit_job", {"job_id": "abc"}),
            ("post_now", {"content": "x", "page": "personal", "confirm": "yes"}),
        ):
            with self.subTest(name=name, arguments=arguments):
                data, is_error = self.call(name, arguments)
                self.assertTrue(is_error)
                self.assertIn("confirm=true", data["message"])
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "queue.json")))


class PostingTest(McpTestCase):
    def test_post_now_runs_post_py_with_the_content_in_a_file(self):
        data, is_error = self.call("post_now", {"content": "Nội dung\nnhiều dòng", "page": "Hi English", "confirm": True})
        self.assertFalse(is_error)
        self.assertEqual(data["status"], "posted")
        command = self.flags()
        self.assertEqual(command[:2], ["python", "-u"])
        self.assertTrue(command[2].endswith("post.py"))
        self.assertIn("--json", command)
        self.assertEqual(command[command.index("--page") + 1], HI)
        self.assertEqual(self.calls[0]["content"], "Nội dung\nnhiều dòng")
        self.assertNotIn("--dry-run", command)

    def test_the_content_file_is_removed(self):
        self.call("post_now", {"content": "x", "page": "personal", "confirm": True})
        self.assertFalse(os.path.exists(self.flags()[self.flags().index("--content-file") + 1]))

    def test_personal_means_no_page_argument(self):
        self.call("post_now", {"content": "x", "page": "personal", "confirm": True})
        self.assertNotIn("--page", self.flags())

    def test_files_account_and_duplicate_flag_are_passed(self):
        self.call("post_now", {"content": "x", "page": "personal", "images": ["a.png", "b.mp4"], "account": "shop2",
                               "allow_duplicate": True, "confirm": True})
        command = self.flags()
        self.assertEqual([command[i + 1] for i, part in enumerate(command) if part == "--image"], ["a.png", "b.mp4"])
        self.assertEqual(command[command.index("--account") + 1], "shop2")
        self.assertIn("--allow-duplicate", command)

    def test_a_post_with_only_files_is_fine_but_nothing_at_all_is_not(self):
        data, is_error = self.call("post_now", {"page": "personal", "images": ["a.png"], "confirm": True})
        self.assertFalse(is_error)
        data, is_error = self.call("post_now", {"page": "personal", "confirm": True})
        self.assertTrue(is_error)

    def test_a_failed_post_is_an_error_result_with_the_details(self):
        self.reply = {"ok": False, "status": "blocked", "exit_code": 5, "message": "Bị chặn"}
        data, is_error = self.call("post_now", {"content": "x", "page": "personal", "confirm": True})
        self.assertTrue(is_error)
        self.assertEqual((data["status"], data["exit_code"]), ("blocked", 5))

    def test_the_tail_of_the_log_is_included(self):
        data, _ = self.call("post_now", {"content": "x", "page": "personal", "confirm": True})
        self.assertEqual(data["tail_of_log"], ["nhật ký", "dòng cuối"])

    def test_an_unknown_page_is_explained(self):
        data, is_error = self.call("post_now", {"content": "x", "page": "không có", "confirm": True})
        self.assertTrue(is_error)
        self.assertIn("Hi English", data["message"])
        self.assertEqual(self.calls, [])

    def test_output_without_a_result_is_reported(self):
        self.stdout = "chỉ có nhật ký\n"
        data, is_error = self.call("post_now", {"content": "x", "page": "personal", "confirm": True})
        self.assertTrue(is_error)
        self.assertIn("không trả về kết quả", data["message"])

    def test_a_timeout_warns_that_the_post_may_be_live(self):
        def slow(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 1)

        self.server.run_process = slow
        data, is_error = self.call("post_now", {"content": "x", "page": "personal", "confirm": True})
        self.assertTrue(is_error)
        self.assertIn("get_history", data["message"])

    def test_dry_run_needs_no_confirmation_and_never_posts(self):
        data, is_error = self.call("dry_run_post", {"content": "x", "page": "Hi English"})
        self.assertFalse(is_error)
        self.assertIn("--dry-run", self.flags())

    def test_selftest(self):
        self.reply = {"ok": True, "exit_code": 0, "message": "ổn", "report": {}}
        data, is_error = self.call("selftest", {"page": "Hi English", "account": "shop2"})
        self.assertFalse(is_error)
        command = self.flags()
        self.assertIn("--selftest", command)
        self.assertEqual(command[command.index("--page") + 1], HI)
        self.assertNotIn("--content-file", command)


class QueueToolsTest(McpTestCase):
    def schedule(self, **extra):
        arguments = {"content": "Chào {page}", "pages": ["Hi English"], "at": "2026-10-18 08:30", "confirm": True}
        arguments.update(extra)
        return self.call("schedule_post", arguments)

    def queue(self):
        return store.load_queue(os.path.join(self.dir, "queue.json"))

    def test_schedule_adds_to_the_queue(self):
        data, is_error = self.schedule(repeat="weekdays", jitter_minutes=10, gap_minutes=3)
        self.assertFalse(is_error)
        job = self.queue()[0]
        self.assertEqual((job["content"], job["page_url"], job["run_at"], job["repeat"], job["jitter_minutes"]),
                         ("Chào Hi English", HI, "2026-10-18 08:30", "weekdays", 10))
        self.assertIn("daemon", data["note"])
        self.assertEqual(self.calls, [])

    def test_several_pages_are_one_batch(self):
        self.schedule(pages=["Hi English", "personal"], gap_minutes=7)
        first, second = self.queue()
        self.assertEqual((first["run_at"], second["run_at"]), ("2026-10-18 08:30", "2026-10-18 08:37"))

    def test_bad_input_is_explained_and_nothing_is_queued(self):
        for extra in ({"at": "mai"}, {"at": "2026-01-01 08:00"}, {"pages": ["Chỗ lạ"]}, {"repeat": "yearly"}):
            with self.subTest(extra=extra):
                data, is_error = self.schedule(**extra)
                self.assertTrue(is_error)
                self.assertTrue(data["message"])
        self.assertEqual(self.queue(), [])

    def test_list_edit_and_remove(self):
        self.schedule()
        data, _ = self.call("list_queue")
        (job,) = data["jobs"]
        data, is_error = self.call("edit_job", {"job_id": job["id"], "content": "{page} giữ nguyên", "at": "2026-10-19 09:00",
                                                "confirm": True})
        self.assertFalse(is_error)
        self.assertEqual(self.queue()[0]["content"], "{page} giữ nguyên")
        self.assertEqual(self.queue()[0]["run_at"], "2026-10-19 09:00")
        data, _ = self.call("list_queue", {"status": "done"})
        self.assertEqual(data["jobs"], [])
        data, is_error = self.call("remove_job", {"job_id": job["id"]})
        self.assertFalse(is_error)
        self.assertEqual(self.queue(), [])

    def test_removing_an_unknown_post_is_an_error(self):
        data, is_error = self.call("remove_job", {"job_id": "khong-co"})
        self.assertTrue(is_error)

    def test_changes_are_refused_while_the_window_is_open(self):
        lock = store.lock_file(os.path.join(self.dir, "app.lock"))
        self.addCleanup(lock.close)
        data, is_error = self.schedule()
        self.assertTrue(is_error)
        self.assertIn("app.py", data["message"])


class ReadOnlyToolsTest(McpTestCase):
    def test_list_pages(self):
        data, is_error = self.call("list_pages")
        self.assertFalse(is_error)
        self.assertEqual(data["pages"][0]["name"], "Hi English")
        self.assertEqual(data["templates"], ["Chào"])
        self.assertEqual(data["accounts"], [{"name": "default", "logged_in": True}, {"name": "shop2", "logged_in": False}])

    def test_the_login_itself_is_never_returned(self):
        text = json.dumps(self.call("list_pages")[0])
        self.assertNotIn("c_user", text)
        self.assertNotIn("cookies", text)

    def test_history(self):
        path = os.path.join(self.dir, "history.jsonl")
        for i in range(5):
            history.append(path, {"status": "posted" if i % 2 == 0 else "failed", "content": f"bài {i}"}, NOW)
        data, _ = self.call("get_history", {"limit": 2})
        self.assertEqual([r["content"] for r in data["records"]], ["bài 3", "bài 4"])
        data, _ = self.call("get_history", {"status": "posted"})
        self.assertEqual([r["content"] for r in data["records"]], ["bài 0", "bài 2", "bài 4"])

    def test_daemon_status(self):
        data, _ = self.call("daemon_status")
        self.assertFalse(data["running"])


class EndToEndTest(unittest.TestCase):
    def test_the_real_process_speaks_the_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            store.save_config(os.path.join(folder, "config.json"), {"pages": [{"name": "Trang", "url": HI, "language": ""}]})
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_pages", "arguments": {}}},
            ]
            done = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(os.path.abspath(mcp_server.__file__)), "mcp_server.py"),
                 "--base-dir", folder],
                input="".join(json.dumps(request) + "\n" for request in requests),
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        responses = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2])
        pages = json.loads(responses[1]["result"]["content"][0]["text"])["pages"]
        self.assertEqual(pages[0]["name"], "Trang")


if __name__ == "__main__":
    unittest.main()
