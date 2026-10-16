import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime

import enqueue
import store

NOW = datetime(2026, 10, 17, 9, 0)
PAGE = "https://www.facebook.com/hienglish"
OTHER = "https://www.facebook.com/groups/jaecoo"


class EnqueueTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, name):
        return os.path.join(self.dir, name)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = enqueue.main(["--json", *argv], base_dir=self.dir, now=NOW)
        return code, json.loads(out.getvalue())

    def queue(self):
        return store.load_queue(self.path("queue.json"))


class AddTest(EnqueueTestCase):
    def test_adds_a_job_to_the_queue(self):
        code, result = self.run_cli("add", "xin chào", "--page", PAGE, "--at", "2026-10-18 08:30")
        self.assertEqual(code, 0)
        job = self.queue()[0]
        self.assertEqual((job["content"], job["page_url"], job["run_at"], job["status"]),
                         ("xin chào", PAGE, "2026-10-18 08:30", "pending"))
        self.assertEqual(result["added"][0]["id"], job["id"])

    def test_keeps_the_jobs_already_in_the_queue(self):
        self.run_cli("add", "một", "--page", PAGE, "--at", "2026-10-18 08:30")
        self.run_cli("add", "hai", "--page", PAGE, "--at", "2026-10-19 08:30")
        self.assertEqual([job["content"] for job in self.queue()], ["một", "hai"])

    def test_several_pages_become_a_staggered_batch(self):
        code, _ = self.run_cli("add", "xin chào", "--page", PAGE, "--page", OTHER, "--at", "2026-10-18 08:30", "--gap", "7")
        first, second = self.queue()
        self.assertEqual((code, first["run_at"], second["run_at"]), (0, "2026-10-18 08:30", "2026-10-18 08:37"))
        self.assertEqual(first["batch"], second["batch"])
        self.assertEqual(second["gap_minutes"], 7)

    def test_the_gap_defaults_to_the_one_in_config(self):
        store.save_config(self.path("config.json"), {"page_gap_minutes": 3})
        self.run_cli("add", "xin chào", "--page", PAGE, "--page", OTHER, "--at", "2026-10-18 08:30")
        self.assertEqual(self.queue()[1]["run_at"], "2026-10-18 08:33")

    def test_reads_the_content_from_a_utf8_file(self):
        with open(self.path("bai.txt"), "w", encoding="utf-8", newline="") as handle:
            handle.write("✨ Dòng 1\n- dòng 2")
        self.run_cli("add", "--content-file", self.path("bai.txt"), "--page", PAGE, "--at", "2026-10-18 08:30")
        self.assertEqual(self.queue()[0]["content"], "✨ Dòng 1\n- dòng 2")

    def test_stores_the_attachment_as_an_absolute_path(self):
        with open(self.path("anh.png"), "wb") as handle:
            handle.write(b"png")
        self.run_cli("add", "xin chào", "--page", PAGE, "--image", self.path("anh.png"), "--at", "2026-10-18 08:30")
        self.assertEqual(self.queue()[0]["image_path"], os.path.abspath(self.path("anh.png")))

    def test_a_repeating_job_keeps_its_repeat(self):
        self.run_cli("add", "xin chào", "--page", PAGE, "--at", "2026-10-18 08:30", "--repeat", "daily")
        self.assertEqual(self.queue()[0]["repeat"], "daily")

    def refuses(self, *argv):
        code, result = self.run_cli("add", *argv)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertFalse(os.path.exists(self.path("queue.json")))
        return result["message"]

    def test_refuses_a_time_that_has_passed(self):
        self.refuses("xin chào", "--page", PAGE, "--at", "2026-10-16 08:30")

    def test_refuses_a_badly_written_time(self):
        self.refuses("xin chào", "--page", PAGE, "--at", "ngày mai")

    def test_refuses_an_address_that_is_not_facebook(self):
        self.refuses("xin chào", "--page", "https://example.com/trang", "--at", "2026-10-18 08:30")

    def test_refuses_empty_content(self):
        self.refuses("  ", "--page", PAGE, "--at", "2026-10-18 08:30")

    def test_refuses_a_missing_attachment(self):
        self.refuses("xin chào", "--page", PAGE, "--image", self.path("khong-co.png"), "--at", "2026-10-18 08:30")


class AppOpenTest(EnqueueTestCase):
    def test_changes_are_refused_while_the_app_holds_the_lock(self):
        lock = store.lock_file(self.path("app.lock"))
        self.addCleanup(lock.close)
        code, result = self.run_cli("add", "xin chào", "--page", PAGE, "--at", "2026-10-18 08:30")
        self.assertEqual((code, result["ok"]), (enqueue.EXIT_APP_OPEN, False))
        self.assertFalse(os.path.exists(self.path("queue.json")))

    def test_listing_still_works_while_the_app_is_open(self):
        self.run_cli("add", "xin chào", "--page", PAGE, "--at", "2026-10-18 08:30")
        lock = store.lock_file(self.path("app.lock"))
        self.addCleanup(lock.close)
        code, result = self.run_cli("list")
        self.assertEqual((code, len(result["jobs"])), (0, 1))

    def test_the_lock_is_released_afterwards(self):
        self.run_cli("add", "xin chào", "--page", PAGE, "--at", "2026-10-18 08:30")
        lock = store.lock_file(self.path("app.lock"))
        self.assertIsNotNone(lock)
        lock.close()


class ListAndRemoveTest(EnqueueTestCase):
    def test_an_empty_queue_lists_nothing(self):
        self.assertEqual(self.run_cli("list"), (0, {"ok": True, "jobs": []}))

    def test_removes_only_the_named_job(self):
        self.run_cli("add", "một", "--page", PAGE, "--at", "2026-10-18 08:30")
        self.run_cli("add", "hai", "--page", PAGE, "--at", "2026-10-19 08:30")
        first, second = self.queue()
        code, result = self.run_cli("remove", first["id"])
        self.assertEqual((code, result["removed"]), (0, first["id"]))
        self.assertEqual([job["id"] for job in self.queue()], [second["id"]])

    def test_an_unknown_id_is_an_error(self):
        code, result = self.run_cli("remove", "khong-co")
        self.assertEqual((code, result["ok"]), (1, False))

    def test_unreadable_entries_are_kept_aside_before_they_are_dropped(self):
        store.save_json(self.path("queue.json"), [{"content": "không có giờ"}])
        self.run_cli("add", "xin chào", "--page", PAGE, "--at", "2026-10-18 08:30")
        self.assertTrue(os.path.exists(self.path("queue.json.corrupt")))
        self.assertEqual(len(self.queue()), 1)


if __name__ == "__main__":
    unittest.main()
