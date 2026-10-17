import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import enqueue
import history
import scheduler
import store

NOW = datetime(2026, 10, 17, 9, 0)
HI = "https://www.facebook.com/hienglish"
GROUP = "https://www.facebook.com/groups/jaecoo"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        store.save_config(
            self.path("config.json"),
            {
                "pages": [
                    {"name": "Hi English", "url": HI, "language": "English (Mỹ)"},
                    {"name": "Nhóm Jaecoo", "url": GROUP, "language": "Tiếng Việt (Việt Nam)"},
                ],
                "accounts": ["shop2"],
            },
        )

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text, mode="w"):
        kwargs = {} if "b" in mode else {"encoding": "utf-8", "newline": ""}
        with open(self.path(name), mode, **kwargs) as handle:
            handle.write(text)
        return self.path(name)

    def run_cli(self, *argv, as_json=True):
        out, err = io.StringIO(), io.StringIO()
        args = (["--json"] if as_json else []) + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = enqueue.main(args, base_dir=self.dir, now=NOW)
        text = out.getvalue()
        return code, (json.loads(text) if as_json else text), err.getvalue()

    def queue(self):
        return store.load_queue(self.path("queue.json"))

    def add(self, *argv):
        code, result, _ = self.run_cli("add", *argv)
        self.assertEqual(code, 0, result)
        return result


class PagesByNameTest(Base):
    def test_a_saved_page_name_is_resolved(self):
        self.add("xin chào", "--page", "hi english", "--at", "2026-10-18 08:30")
        self.assertEqual(self.queue()[0]["page_url"], HI)

    def test_the_profile_is_posted_to_as_personal(self):
        self.add("xin chào", "--page", "personal", "--at", "2026-10-18 08:30")
        self.assertEqual(self.queue()[0]["page_url"], "")

    def test_an_unknown_name_lists_the_saved_ones(self):
        code, result, _ = self.run_cli("add", "xin chào", "--page", "không có", "--at", "2026-10-18 08:30")
        self.assertEqual(code, 1)
        self.assertIn("Hi English", result["message"])

    def test_a_url_of_a_saved_page_uses_the_saved_form(self):
        self.add("xin chào", "--page", "facebook.com/hienglish/", "--at", "2026-10-18 08:30")
        self.assertEqual(self.queue()[0]["page_url"], HI)


class VariantsTest(Base):
    def test_placeholders_are_filled_in_per_page(self):
        self.add("Chào {page} ({language}) lúc {time} {date}", "--page", "Hi English", "--page", "Nhóm Jaecoo",
                 "--at", "2026-10-18 08:30", "--gap", "10")
        first, second = self.queue()
        self.assertEqual(first["content"], "Chào Hi English (English (Mỹ)) lúc 08:30 18/10/2026")
        self.assertEqual(second["content"], "Chào Nhóm Jaecoo (Tiếng Việt (Việt Nam)) lúc 08:40 18/10/2026")

    def test_several_versions_take_turns(self):
        a = self.write("a.txt", "Bản A")
        b = self.write("b.txt", "Bản B")
        self.add("--content-file", a, "--content-file", b, "--page", "Hi English", "--page", "Nhóm Jaecoo",
                 "--page", "personal", "--at", "2026-10-18 08:30")
        self.assertEqual([job["content"] for job in self.queue()], ["Bản A", "Bản B", "Bản A"])

    def test_random_choices_are_made_once_per_page(self):
        self.add("{Một|Hai|Ba|Bốn|Năm|Sáu}", "--page", "Hi English", "--page", "Nhóm Jaecoo", "--page", "personal",
                 "--at", "2026-10-18 08:30")
        for job in self.queue():
            self.assertIn(job["content"], ["Một", "Hai", "Ba", "Bốn", "Năm", "Sáu"])

    def test_no_render_keeps_the_text_as_written(self):
        self.add("Chào {page}", "--page", "Hi English", "--at", "2026-10-18 08:30", "--no-render")
        self.assertEqual(self.queue()[0]["content"], "Chào {page}")


class OptionsTest(Base):
    def test_days_become_a_repeat(self):
        self.add("xin chào", "--page", "Hi English", "--at", "2026-10-19 08:30", "--days", "mon,wed,fri")
        self.assertEqual(self.queue()[0]["repeat"], "days:mon,wed,fri")

    def test_a_bad_repeat_is_refused(self):
        code, result, _ = self.run_cli("add", "x", "--page", "Hi English", "--at", "2026-10-19 08:30", "--repeat", "yearly")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertFalse(os.path.exists(self.path("queue.json")))

    def test_jitter_and_account(self):
        self.add("xin chào", "--page", "Hi English", "--at", "2026-10-19 08:30", "--repeat", "daily", "--jitter", "20",
                 "--account", "shop2")
        job = self.queue()[0]
        self.assertEqual((job["jitter_minutes"], job["account"]), (20, "shop2"))

    def test_an_unknown_account_is_refused(self):
        code, result, _ = self.run_cli("add", "x", "--page", "Hi English", "--at", "2026-10-19 08:30", "--account", "lạ")
        self.assertEqual(code, 1)
        self.assertIn("accounts", result["message"])

    def test_the_default_account_is_stored_as_none(self):
        self.add("xin chào", "--page", "Hi English", "--at", "2026-10-19 08:30", "--account", "default")
        self.assertNotIn("account", self.queue()[0])

    def test_several_files_are_joined(self):
        a, b = self.write("a.png", b"x", "wb"), self.write("b.png", b"x", "wb")
        self.add("xin chào", "--page", "Hi English", "--at", "2026-10-19 08:30", "--image", a, "--image", b)
        self.assertEqual(self.queue()[0]["image_path"], f"{os.path.abspath(a)}|{os.path.abspath(b)}")

    def test_a_post_with_only_media_is_allowed(self):
        a = self.write("a.png", b"x", "wb")
        self.add("", "--page", "Hi English", "--at", "2026-10-19 08:30", "--image", a)
        self.assertEqual(len(self.queue()), 1)


class WarningsTest(Base):
    def test_a_copy_of_a_recent_post_is_warned_about_but_added(self):
        history.append(self.path("history.jsonl"), {"status": "posted", "content": "Xin chào cả nhà", "page_url": HI},
                       NOW - timedelta(hours=2))
        result = self.add("xin chào cả nhà", "--page", "Hi English", "--at", "2026-10-18 08:30")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("giống", result["warnings"][0])
        self.assertEqual(len(self.queue()), 1)

    def test_strict_refuses_instead(self):
        history.append(self.path("history.jsonl"), {"status": "posted", "content": "Xin chào cả nhà", "page_url": HI},
                       NOW - timedelta(hours=2))
        code, result, _ = self.run_cli("add", "Xin chào cả nhà", "--page", "Hi English", "--at", "2026-10-18 08:30", "--strict")
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.path("queue.json")))

    def test_a_similar_waiting_post_is_warned_about(self):
        self.add("Xin chào cả nhà", "--page", "Hi English", "--at", "2026-10-18 08:30")
        result = self.add("xin chào cả nhà", "--page", "Hi English", "--at", "2026-10-19 08:30")
        self.assertIn("giống bài hẹn lúc 2026-10-18 08:30", result["warnings"][0])

    def test_the_same_text_on_two_pages_is_not_a_problem(self):
        result = self.add("Xin chào cả nhà", "--page", "Hi English", "--page", "Nhóm Jaecoo", "--at", "2026-10-18 08:30")
        self.assertEqual(result["warnings"], [])

    def test_a_repeating_post_may_say_the_same_thing(self):
        self.add("Xin chào cả nhà", "--page", "Hi English", "--at", "2026-10-18 08:30", "--repeat", "daily")
        result = self.add("Xin chào cả nhà", "--page", "Hi English", "--at", "2026-10-19 08:30", "--repeat", "daily")
        self.assertEqual(result["warnings"], [])


class EditTest(Base):
    def setUp(self):
        super().setUp()
        self.add("Bản đầu", "--page", "Hi English", "--at", "2026-10-18 08:30")
        self.job = self.queue()[0]

    def edit(self, *argv):
        return self.run_cli("edit", self.job["id"], *argv)

    def test_changes_the_time(self):
        code, result, _ = self.edit("--at", "2026-10-20 07:00")
        self.assertEqual((code, result["edited"]["run_at"]), (0, "2026-10-20 07:00"))
        self.assertEqual(self.queue()[0]["run_at"], "2026-10-20 07:00")

    def test_changes_the_text_page_and_repeat(self):
        content = self.write("moi.txt", "Bản mới\nDòng hai")
        self.edit("--content-file", content, "--page", "Nhóm Jaecoo", "--days", "tue,thu")
        job = self.queue()[0]
        self.assertEqual((job["content"], job["page_url"], job["repeat"]), ("Bản mới\nDòng hai", GROUP, "days:tue,thu"))

    def test_text_is_stored_as_given(self):
        self.edit("{page} giữ nguyên")
        self.assertEqual(self.queue()[0]["content"], "{page} giữ nguyên")

    def test_a_finished_post_waits_for_its_new_time(self):
        jobs = self.queue()
        jobs[0].update(status="failed", last_message="Lỗi", attempts=2, not_before="2026-10-18T09:00:00")
        store.save_queue(self.path("queue.json"), jobs)
        self.edit("--at", "2026-10-21 09:00")
        job = self.queue()[0]
        self.assertEqual((job["status"], job["last_message"]), ("pending", ""))
        for key in ("attempts", "not_before"):
            self.assertNotIn(key, job)

    def test_image_can_be_added_and_cleared(self):
        a = self.write("a.png", b"x", "wb")
        self.edit("--image", a)
        self.assertEqual(self.queue()[0]["image_path"], os.path.abspath(a))
        self.edit("--clear-image")
        self.assertEqual(self.queue()[0]["image_path"], "")

    def test_jitter_can_be_set_and_removed(self):
        self.edit("--repeat", "daily", "--jitter", "15")
        self.assertEqual(self.queue()[0]["jitter_minutes"], 15)
        self.edit("--jitter", "0")
        self.assertNotIn("jitter_minutes", self.queue()[0])

    def test_nothing_to_change_is_an_error(self):
        code, result, _ = self.edit()
        self.assertEqual((code, result["ok"]), (1, False))

    def test_unknown_id_and_running_jobs_are_refused(self):
        code, _, _ = self.run_cli("edit", "khong-co", "--at", "2026-10-20 07:00")
        self.assertEqual(code, 1)
        jobs = self.queue()
        jobs[0]["status"] = "running"
        store.save_queue(self.path("queue.json"), jobs)
        code, result, _ = self.edit("--at", "2026-10-20 07:00")
        self.assertEqual(code, 1)
        self.assertIn("đang chạy", result["message"])

    def test_several_pages_cannot_be_given(self):
        code, _, _ = self.edit("--page", "Hi English", "--page", "Nhóm Jaecoo")
        self.assertEqual(code, 1)


class ImportTest(Base):
    HEADER = "content,content_file,pages,at,image,repeat,account,jitter\n"

    def csv(self, rows, header=None):
        return self.write("bai.csv", "﻿" + (header or self.HEADER) + rows)

    def test_every_row_is_queued(self):
        path = self.csv(
            'Xin chào {page},,Hi English|Nhóm Jaecoo,2026-10-18 08:30,,none,,\n'
            ',bai-dai.txt,personal,2026-10-19 20:00,,weekdays,shop2,15\n'
        )
        self.write("bai-dai.txt", "Bài dài\nnhiều dòng")
        code, result, _ = self.run_cli("import", path, "--gap", "5")
        self.assertEqual(code, 0, result)
        jobs = self.queue()
        self.assertEqual(len(jobs), 3)
        contents = sorted(job["content"] for job in jobs)
        self.assertEqual(contents, ["Bài dài\nnhiều dòng", "Xin chào Hi English", "Xin chào Nhóm Jaecoo"])
        last = next(job for job in jobs if job["page_url"] == "")
        self.assertEqual((last["repeat"], last["account"], last["jitter_minutes"]), ("weekdays", "shop2", 15))

    def test_the_content_file_is_found_next_to_the_csv_and_images_too(self):
        os.makedirs(self.path("sub"))
        with open(self.path("sub/a.png"), "wb") as handle:
            handle.write(b"x")
        with open(self.path("sub/noi-dung.txt"), "w", encoding="utf-8") as handle:
            handle.write("Từ tệp")
        path = os.path.join(self.dir, "sub", "bai.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(self.HEADER + ",noi-dung.txt,Hi English,2026-10-18 08:30,a.png,none,,\n")
        code, _, _ = self.run_cli("import", path)
        self.assertEqual(code, 0)
        job = self.queue()[0]
        self.assertEqual(job["content"], "Từ tệp")
        self.assertEqual(job["image_path"], os.path.join(self.dir, "sub", "a.png"))

    def test_one_bad_row_adds_nothing_and_all_errors_are_listed(self):
        path = self.csv(
            "Tốt,,Hi English,2026-10-18 08:30,,none,,\n"
            "Sai giờ,,Hi English,ngày mai,,none,,\n"
            "Sai nơi,,Chỗ lạ,2026-10-18 08:30,,none,,\n"
        )
        code, result, _ = self.run_cli("import", path)
        self.assertEqual(code, 1)
        self.assertIn("Dòng 3", result["message"])
        self.assertIn("Dòng 4", result["message"])
        self.assertNotIn("Dòng 2", result["message"])
        self.assertFalse(os.path.exists(self.path("queue.json")))

    def test_dry_run_only_checks(self):
        path = self.csv("Tốt,,Hi English,2026-10-18 08:30,,none,,\n")
        code, result, _ = self.run_cli("import", path, "--dry-run")
        self.assertEqual((code, result["dry_run"], len(result["added"])), (0, True, 1))
        self.assertFalse(os.path.exists(self.path("queue.json")))

    def test_unknown_or_missing_columns_are_explained(self):
        code, result, _ = self.run_cli("import", self.csv("a,b\n", header="noi dung,nơi\n"))
        self.assertEqual(code, 1)
        self.assertIn("Cột không hiểu", result["message"])
        code, result, _ = self.run_cli("import", self.csv("a\n", header="content\n"))
        self.assertEqual(code, 1)
        self.assertIn("pages", result["message"])

    def test_an_empty_sheet_and_a_missing_file(self):
        code, _, _ = self.run_cli("import", self.csv(""))
        self.assertEqual(code, 1)
        code, _, _ = self.run_cli("import", self.path("khong-co.csv"))
        self.assertEqual(code, 1)

    def test_blank_lines_are_ignored(self):
        path = self.csv("\n,,,,,,,\nTốt,,Hi English,2026-10-18 08:30,,none,,\n")
        code, result, _ = self.run_cli("import", path)
        self.assertEqual((code, len(result["added"])), (0, 1))

    def test_the_template_is_itself_a_valid_sheet_shape(self):
        code, text, _ = self.run_cli("csv-template", as_json=False)
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("content,content_file,pages,at,image,repeat,account,jitter"))


class LockingTest(Base):
    def test_changes_are_refused_while_the_app_is_open(self):
        lock = store.lock_file(self.path("app.lock"))
        self.addCleanup(lock.close)
        code, result, _ = self.run_cli("add", "xin chào", "--page", "Hi English", "--at", "2026-10-18 08:30")
        self.assertEqual((code, result["ok"]), (enqueue.EXIT_APP_OPEN, False))

    def test_changes_are_allowed_while_the_background_runner_works(self):
        app, daemon = store.lock_file(self.path("app.lock")), store.lock_file(self.path("daemon.lock"))
        self.addCleanup(app.close)
        self.addCleanup(daemon.close)
        code, _, _ = self.run_cli("add", "xin chào", "--page", "Hi English", "--at", "2026-10-18 08:30")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.queue()), 1)

    def test_the_app_cannot_start_during_a_change_but_can_afterwards(self):
        # Holding the app lock while editing is what keeps the app from starting half way through
        self.add("xin chào", "--page", "Hi English", "--at", "2026-10-18 08:30")
        lock = store.lock_file(self.path("app.lock"))
        self.assertIsNotNone(lock)
        lock.close()

    def test_listing_never_needs_a_lock(self):
        lock = store.lock_file(self.path("app.lock"))
        self.addCleanup(lock.close)
        code, result, _ = self.run_cli("list")
        self.assertEqual((code, result["jobs"]), (0, []))

    def test_list_can_filter_by_status(self):
        self.add("một", "--page", "Hi English", "--at", "2026-10-18 08:30")
        jobs = self.queue()
        jobs[0]["status"] = "done"
        store.save_queue(self.path("queue.json"), jobs)
        self.add("hai", "--page", "Hi English", "--at", "2026-10-19 08:30")
        _, result, _ = self.run_cli("list", "--status", "pending")
        self.assertEqual([job["content"] for job in result["jobs"]], ["hai"])


if __name__ == "__main__":
    unittest.main()
