import contextlib
import io
import json
import os
import tempfile
import time
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from unittest import mock

import app as app_module
import scheduler
import store

# What login.py leaves behind once Facebook has set its login cookie
SESSION = json.dumps(
    {"cookies": [{"name": "c_user", "value": "1", "domain": ".facebook.com", "path": "/", "expires": -1}],
     "origins": []}
)

# Stand-ins for the real scripts so the tests never open a browser or touch Facebook
FAKE_POST = """
import argparse, json, os, sys, time
parser = argparse.ArgumentParser()
parser.add_argument("--content-file")
parser.add_argument("--page")
parser.add_argument("--image", action="append", default=[])
parser.add_argument("--account", default="")
parser.add_argument("--allow-duplicate", action="store_true")
parser.add_argument("--headless", action="store_true")
args = parser.parse_args()
with open(args.content_file, encoding="utf-8", newline="") as f:
    content = f.read()
with open("posted.json", "w", encoding="utf-8") as f:
    json.dump({"content": content, "page": args.page, "image": args.image[0] if args.image else None,
               "images": args.image, "account": args.account, "allow_duplicate": args.allow_duplicate,
               "headless": args.headless, "content_file": args.content_file}, f)
with open("pages.log", "a", encoding="utf-8") as f:
    f.write((args.page or "") + "\\n")
print("Đăng bài giả lập")
if "SLOW" in content:
    time.sleep(120)
if "EXPIRED" in content:
    sys.exit(2)
if "RETRYABLE" in content and not os.path.exists("retried.flag"):
    open("retried.flag", "w").close()
    sys.exit(4)
if "ALWAYSRETRY" in content:
    sys.exit(4)
if "BLOCKED" in content:
    sys.exit(5)
if "LINK" in content:
    print("POST_VERIFIED=yes")
    print("POST_URL=https://www.facebook.com/x/posts/42")
if "NOTSEEN" in content:
    print("POST_VERIFIED=no")
sys.exit(1 if "FAIL" in content or "trang-hong" in (args.page or "") else 0)
"""

FAKE_LOGIN = f"""
print("chờ đăng nhập")
input()
with open("state.json", "w", encoding="utf-8") as f:
    f.write({SESSION!r})
print("đã lưu")
"""


class AppTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.write("post.py", FAKE_POST)
        self.write("login.py", FAKE_LOGIN)
        self.write("state.json", SESSION)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def read_json(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as handle:
            return json.load(handle)

    def exists(self, name):
        return os.path.exists(os.path.join(self.dir, name))

    def start_app(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Không có màn hình để chạy Tkinter: {error}")
        root.withdraw()
        self.app = app_module.App(root, base_dir=self.dir)
        self.addCleanup(self.app.shutdown)
        return self.app

    def pump(self, condition, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.root.update()
            if condition():
                return
            time.sleep(0.02)
        self.fail("Hết thời gian chờ")

    def log_text(self):
        return self.app.log_widget.get("1.0", "end")

    def past(self):
        return datetime.now() - timedelta(minutes=1)


class PagesAndTemplatesTest(AppTestCase):
    def test_added_page_is_saved_with_a_normalized_url(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/hienglishoffical")
        self.assertEqual(
            self.read_json("config.json")["pages"],
            [{"name": "Hi English", "url": "https://facebook.com/hienglishoffical", "language": ""}],
        )

    def test_adding_a_page_with_an_existing_name_updates_it(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/old", "English (Mỹ)")
        app.add_page("Hi English", "facebook.com/new", "English (Anh)")
        self.assertEqual(
            self.read_json("config.json")["pages"],
            [{"name": "Hi English", "url": "https://facebook.com/new", "language": "English (Anh)"}],
        )

    def test_page_language_is_saved_trimmed(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/hienglishoffical", "  English (Mỹ) ")
        self.assertEqual(self.read_json("config.json")["pages"][0]["language"], "English (Mỹ)")

    def test_page_list_shows_the_language(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/hienglishoffical", "English (Mỹ)")
        app.add_page("Không rõ", "facebook.com/abc")
        self.assertEqual(
            app.page_list.get(0, "end"),
            (
                "Hi English  —  English (Mỹ)  —  https://facebook.com/hienglishoffical",
                "Không rõ  —  https://facebook.com/abc",
            ),
        )

    def test_selecting_a_page_in_the_list_shows_its_language(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/hienglishoffical", "English (Mỹ)")
        app.page_list.selection_set(0)
        app._on_page_list_selected()
        self.assertEqual(app.page_language_var.get(), "English (Mỹ)")

    def test_page_saved_before_languages_existed_still_loads(self):
        store.save_json(
            os.path.join(self.dir, "config.json"),
            {"pages": [{"name": "Cũ", "url": "https://facebook.com/cu"}]},
        )
        app = self.start_app()
        app.page_list.selection_set(0)
        app._on_page_list_selected()
        self.assertEqual(app.page_language_var.get(), "")
        self.assertEqual(app.page_list.get(0), "Cũ  —  https://facebook.com/cu")

    def test_language_box_offers_common_languages(self):
        app = self.start_app()
        self.assertIn("Tiếng Việt (Việt Nam)", app.page_language_box.cget("values"))
        self.assertIn("English (Mỹ)", app.page_language_box.cget("values"))

    def test_page_with_a_non_facebook_url_is_rejected(self):
        app = self.start_app()
        with self.assertRaises(ValueError):
            app.add_page("Lạ", "https://example.com/page")
        self.assertEqual(app.settings["pages"], [])

    def test_page_needs_a_name(self):
        app = self.start_app()
        with self.assertRaises(ValueError):
            app.add_page("  ", "facebook.com/abc")

    def test_removed_page_is_gone_from_disk(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/hienglishoffical")
        app.remove_page("Hi English")
        self.assertEqual(self.read_json("config.json")["pages"], [])

    def test_saved_template_survives_a_restart(self):
        app = self.start_app()
        app.save_template("Mẹo hằng ngày", "✨ Mẹo hôm nay\n- dòng 2")
        app.shutdown()
        restarted = self.start_app()
        self.assertEqual(
            restarted.settings["templates"],
            [{"name": "Mẹo hằng ngày", "content": "✨ Mẹo hôm nay\n- dòng 2", "media_path": ""}],
        )

    def test_new_page_shows_up_in_the_compose_target_list(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/hienglishoffical")
        self.assertEqual(app.available_targets(), ["(Trang cá nhân)", "Hi English"])


class TemplateMediaTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.write("clip.mp4", "x")
        self.video = os.path.join(self.dir, "clip.mp4")

    def compose_content(self):
        return self.app.content_box.get("1.0", "end-1c")

    def test_template_keeps_its_attached_file(self):
        app = self.start_app()
        app.save_template("Video tuần", "Xem video nhé", self.video)
        self.assertEqual(
            self.read_json("config.json")["templates"],
            [{"name": "Video tuần", "content": "Xem video nhé", "media_path": self.video}],
        )

    def test_template_can_be_a_file_without_text(self):
        app = self.start_app()
        app.save_template("Chỉ video", "", self.video)
        self.assertEqual(app.settings["templates"][0]["media_path"], self.video)

    def test_template_needs_text_or_a_file(self):
        app = self.start_app()
        with self.assertRaises(ValueError):
            app.save_template("Rỗng", "  ", "")

    def test_template_with_a_missing_file_is_rejected(self):
        app = self.start_app()
        with self.assertRaises(ValueError):
            app.save_template("Hỏng", "Nội dung", os.path.join(self.dir, "khong-co.mp4"))
        self.assertEqual(app.settings["templates"], [])

    def test_applying_a_template_fills_the_text_and_the_file(self):
        app = self.start_app()
        app.save_template("Video tuần", "Xem video nhé", self.video)
        app.apply_template("Video tuần")
        self.assertEqual(self.compose_content(), "Xem video nhé")
        self.assertEqual(app.media_var.get(), self.video)

    def test_applying_a_text_only_template_clears_the_previous_file(self):
        app = self.start_app()
        app.save_template("Chữ", "Chỉ có chữ")
        app.media_var.set(self.video)
        app.apply_template("Chữ")
        self.assertEqual(app.media_var.get(), "")

    def test_template_saved_before_attachments_existed_still_applies(self):
        store.save_json(
            os.path.join(self.dir, "config.json"),
            {"templates": [{"name": "Cũ", "content": "Mẫu cũ"}]},
        )
        app = self.start_app()
        app.apply_template("Cũ")
        self.assertEqual(self.compose_content(), "Mẫu cũ")
        self.assertEqual(app.media_var.get(), "")

    def test_applying_an_unknown_template_is_an_error(self):
        app = self.start_app()
        with self.assertRaises(ValueError):
            app.apply_template("Không có")

    def test_selecting_a_template_in_the_list_shows_its_file(self):
        app = self.start_app()
        app.save_template("Video tuần", "Xem video nhé", self.video)
        app.template_list.selection_set(0)
        app._on_template_list_selected()
        self.assertEqual(app.template_media_var.get(), self.video)

    def test_post_made_from_a_template_uploads_its_file(self):
        app = self.start_app()
        app.save_template("Video tuần", "Xem video nhé", self.video)
        app.apply_template("Video tuần")
        app.publish_now(self.compose_content(), "", app.media_var.get())
        self.pump(lambda: not app.is_busy())
        self.assertEqual(self.read_json("posted.json")["image"], self.video)


class MultiPageTest(AppTestCase):
    A = "https://facebook.com/groups/nhom-a"
    B = "https://facebook.com/groups/nhom-b"
    BROKEN = "https://facebook.com/groups/trang-hong"

    def start_app_with_pages(self):
        app = self.start_app()
        app.add_page("Nhóm A", self.A)
        app.add_page("Nhóm B", self.B)
        app.add_page("Nhóm hỏng", self.BROKEN)
        return app

    def posted_pages(self):
        if not self.exists("pages.log"):
            return []
        with open(os.path.join(self.dir, "pages.log"), encoding="utf-8") as handle:
            return handle.read().splitlines()

    def test_selected_targets_come_back_in_list_order(self):
        app = self.start_app_with_pages()
        app.select_targets(["Nhóm B", "(Trang cá nhân)", "Nhóm A"])
        self.assertEqual(app.selected_page_urls(), ["", self.A, self.B])

    def ticked(self, dialog):
        return [name for name, box in dialog.checks.items() if "selected" in box.state()]

    def test_picker_popup_has_a_checkbox_for_every_target(self):
        app = self.start_app_with_pages()
        dialog = app.open_target_dialog()
        self.assertEqual(
            [box.cget("text") for box in dialog.checks.values()],
            ["(Trang cá nhân)", "Nhóm A", "Nhóm B", "Nhóm hỏng"],
        )

    def test_picker_popup_shows_the_language_of_each_page(self):
        app = self.start_app_with_pages()
        app.add_page("Nhóm A", self.A, "English (Mỹ)")
        dialog = app.open_target_dialog()
        self.assertEqual(dialog.checks["Nhóm A"].cget("text"), "Nhóm A  ·  English (Mỹ)")
        self.assertEqual(dialog.checks["Nhóm B"].cget("text"), "Nhóm B")
        dialog.checks["Nhóm A"].invoke()
        dialog.ok()
        self.assertEqual(app.selected_targets(), ["(Trang cá nhân)", "Nhóm A"])

    def test_picker_popup_opens_with_the_current_selection_ticked(self):
        app = self.start_app_with_pages()
        app.select_targets(["Nhóm A", "Nhóm hỏng"])
        self.assertEqual(self.ticked(app.open_target_dialog()), ["Nhóm A", "Nhóm hỏng"])

    def test_ok_brings_the_ticked_targets_back_to_the_main_screen(self):
        app = self.start_app_with_pages()
        dialog = app.open_target_dialog()
        dialog.checks["Nhóm B"].invoke()
        dialog.ok()
        self.assertEqual(app.selected_page_urls(), ["", self.B])
        self.assertEqual(app.target_count_var.get(), "Đã chọn 2 nơi: (Trang cá nhân), Nhóm B")
        self.assertEqual(self.read_json("config.json")["last_pages"], ["(Trang cá nhân)", "Nhóm B"])
        self.assertFalse(dialog.window.winfo_exists())

    def test_nothing_changes_until_ok_is_pressed(self):
        app = self.start_app_with_pages()
        dialog = app.open_target_dialog()
        dialog.checks["Nhóm B"].invoke()
        self.assertEqual(app.selected_page_urls(), [""])

    def test_cancel_keeps_the_previous_selection(self):
        app = self.start_app_with_pages()
        app.select_targets(["Nhóm A"])
        dialog = app.open_target_dialog()
        dialog.checks["Nhóm B"].invoke()
        dialog.checks["Nhóm A"].invoke()
        dialog.cancel()
        self.assertEqual(app.selected_page_urls(), [self.A])
        self.assertFalse(dialog.window.winfo_exists())

    def test_popup_can_tick_and_untick_everything(self):
        app = self.start_app_with_pages()
        dialog = app.open_target_dialog()
        dialog.set_all(True)
        self.assertEqual(self.ticked(dialog), ["(Trang cá nhân)", "Nhóm A", "Nhóm B", "Nhóm hỏng"])
        dialog.set_all(False)
        dialog.ok()
        self.assertEqual(app.selected_page_urls(), [])
        self.assertEqual(app.target_count_var.get(), "Chưa chọn nơi nào")

    def test_removed_page_is_dropped_from_the_selection(self):
        app = self.start_app_with_pages()
        app.select_targets(["Nhóm A", "Nhóm B"])
        app.remove_page("Nhóm A")
        self.assertNotIn("Nhóm A", app.available_targets())
        self.assertEqual(app.selected_page_urls(), [self.B])

    def test_summary_names_every_ticked_target(self):
        app = self.start_app_with_pages()
        app.select_targets(["Nhóm A", "Nhóm hỏng"])
        self.assertEqual(app.target_count_var.get(), "Đã chọn 2 nơi: Nhóm A, Nhóm hỏng")
        app.select_targets([])
        self.assertEqual(app.target_count_var.get(), "Chưa chọn nơi nào")

    def test_personal_profile_is_selected_by_default(self):
        app = self.start_app_with_pages()
        self.assertEqual(app.selected_page_urls(), [""])

    def test_selection_survives_adding_a_page(self):
        app = self.start_app_with_pages()
        app.select_targets(["Nhóm A", "Nhóm B"])
        app.add_page("Nhóm C", "facebook.com/groups/nhom-c")
        self.assertEqual(app.selected_page_urls(), [self.A, self.B])

    def test_selection_is_remembered_after_a_restart(self):
        app = self.start_app_with_pages()
        app.select_targets(["Nhóm A", "Nhóm B"])
        app.shutdown()
        self.assertEqual(self.start_app().selected_page_urls(), [self.A, self.B])

    def test_gap_defaults_to_five_minutes(self):
        self.assertEqual(self.start_app().settings["page_gap_minutes"], 5)

    def test_gap_setting_is_saved(self):
        app = self.start_app()
        app.set_page_gap(12)
        self.assertEqual(self.read_json("config.json")["page_gap_minutes"], 12)
        self.assertEqual(app.gap_var.get(), "12")

    def test_gap_must_be_a_whole_number_of_minutes_from_zero_up(self):
        app = self.start_app()
        for bad in ("-1", "abc", "2.5", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.set_page_gap(bad)
        self.assertEqual(app.settings["page_gap_minutes"], 5)

    def test_scheduling_for_several_pages_queues_one_post_per_page(self):
        app = self.start_app_with_pages()
        jobs = app.enqueue_many(
            "Bài chung", [self.A, self.B, self.BROKEN], "", datetime(2030, 1, 1, 9, 0), "none", 5
        )
        self.assertEqual([job["page_url"] for job in jobs], [self.A, self.B, self.BROKEN])
        self.assertEqual(
            [job["run_at"] for job in jobs],
            ["2030-01-01 09:00", "2030-01-01 09:05", "2030-01-01 09:10"],
        )
        self.assertEqual(len(self.read_json("queue.json")), 3)
        self.assertEqual(len(app.queue_table.get_children()), 3)

    def test_publishing_to_one_page_does_not_use_the_queue(self):
        app = self.start_app_with_pages()
        app.publish_to("Bài", [self.A], "", 5)
        self.pump(lambda: not app.is_busy())
        self.assertEqual(self.posted_pages(), [self.A])
        self.assertEqual(app.jobs, [])

    def test_publishing_to_several_pages_posts_to_each_in_order(self):
        app = self.start_app_with_pages()
        jobs = app.publish_to("Bài chung", [self.B, self.A], "", 0)
        self.pump(lambda: all(job["status"] == "done" for job in jobs), timeout=30)
        self.assertEqual(self.posted_pages(), [self.B, self.A])

    def test_a_failing_page_does_not_stop_the_following_pages(self):
        app = self.start_app_with_pages()
        jobs = app.publish_to("Bài chung", [self.A, self.BROKEN, self.B], "", 0)
        self.pump(lambda: all(job["status"] != "pending" and not app.is_busy() for job in jobs), timeout=30)
        self.assertEqual([job["status"] for job in jobs], ["done", "failed", "done"])
        self.assertEqual(self.posted_pages(), [self.A, self.BROKEN, self.B])

    def test_gap_holds_back_the_following_pages(self):
        app = self.start_app_with_pages()
        jobs = app.publish_to("Bài chung", [self.A, self.B, self.BROKEN], "", 5)
        self.pump(lambda: jobs[0]["status"] == "done")
        first, second, third = (scheduler.parse_run_at(job["run_at"]) for job in jobs)
        self.assertEqual(second - first, timedelta(minutes=5))
        self.assertEqual(third - first, timedelta(minutes=10))
        app.tick()
        self.assertFalse(app.is_busy())
        self.assertEqual([job["status"] for job in jobs[1:]], ["pending", "pending"])
        self.assertEqual(self.posted_pages(), [self.A])

    def test_gap_counts_from_when_the_previous_post_finished(self):
        app = self.start_app_with_pages()
        # Both posts are already due: only the gap after the first one holds the second back
        start = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=3)
        first, second = app.enqueue_many("Bài chung", [self.A, self.B], "", start, "none", 2)
        app.tick()
        self.pump(lambda: first["status"] == "done" and not app.is_busy())
        finished = datetime.now()
        self.assertGreaterEqual(scheduler.ready_at(second), finished + timedelta(seconds=110))
        self.assertIn("giãn cách", second["last_message"])
        app.tick()
        self.assertFalse(app.is_busy())
        self.assertEqual(self.posted_pages(), [self.A])
        self.assertIn("not_before", self.read_json("queue.json")[1])

    def test_posts_to_a_single_target_are_not_linked(self):
        app = self.start_app_with_pages()
        (job,) = app.enqueue_many("Bài", [self.A], "", datetime(2030, 1, 1, 9, 0), "none", 5)
        self.assertNotIn("batch", job)

    def test_publishing_to_several_pages_needs_the_scheduler_on(self):
        app = self.start_app_with_pages()
        app.set_scheduler_enabled(False)
        with self.assertRaises(RuntimeError):
            app.publish_to("Bài chung", [self.A, self.B], "", 0)
        self.assertEqual(app.jobs, [])
        self.assertFalse(app.is_busy())

    def test_publishing_to_several_pages_needs_a_login_session(self):
        os.remove(os.path.join(self.dir, "state.json"))
        app = self.start_app_with_pages()
        with self.assertRaises(RuntimeError):
            app.publish_to("Bài chung", [self.A, self.B], "", 0)
        self.assertEqual(app.jobs, [])

    def test_publishing_needs_at_least_one_target(self):
        app = self.start_app_with_pages()
        with self.assertRaises(ValueError):
            app.publish_to("Bài", [], "", 0)


class QueueTest(AppTestCase):
    def test_queued_post_is_saved_as_pending(self):
        app = self.start_app()
        app.enqueue("Bài hẹn giờ", "", "", datetime(2030, 1, 1, 9, 0), "daily")
        (job,) = self.read_json("queue.json")
        self.assertEqual(job["content"], "Bài hẹn giờ")
        self.assertEqual(job["run_at"], "2030-01-01 09:00")
        self.assertEqual(job["repeat"], "daily")
        self.assertEqual(job["status"], "pending")

    def test_queued_post_is_listed_in_the_queue_table(self):
        app = self.start_app()
        job = app.enqueue("Bài hẹn giờ", "", "", datetime(2030, 1, 1, 9, 0), "none")
        self.assertEqual(app.queue_table.get_children(), (job["id"],))

    def test_removed_job_is_gone_from_disk(self):
        app = self.start_app()
        job = app.enqueue("Bài hẹn giờ", "", "", datetime(2030, 1, 1, 9, 0), "none")
        app.remove_job(job["id"])
        self.assertEqual(self.read_json("queue.json"), [])

    def test_overdue_job_is_marked_missed_at_startup(self):
        old = scheduler.new_job("Bài cũ", "", "", datetime(2020, 1, 1, 9, 0), "none")
        store.save_queue(os.path.join(self.dir, "queue.json"), [old])
        app = self.start_app()
        self.assertEqual(app.jobs[0]["status"], "missed")
        self.assertEqual(self.read_json("queue.json")[0]["status"], "missed")

    def test_damaged_queue_entries_are_dropped_and_the_file_is_kept_aside(self):
        good = scheduler.new_job("Bài tốt", "", "", datetime(2030, 1, 1, 9, 0), "none")
        damaged = [good, {"content": "thiếu giờ chạy"}, "rác", dict(good, id="x", run_at="mai")]
        store.save_queue(os.path.join(self.dir, "queue.json"), damaged)
        app = self.start_app()
        self.assertEqual(app.jobs, [good])
        self.assertEqual(app.queue_table.get_children(), (good["id"],))
        self.assertEqual(self.read_json("queue.json"), [good])
        self.assertEqual(self.read_json("queue.json.corrupt"), damaged)
        self.assertIn("Bỏ qua 3 mục hỏng", self.log_text())

    def test_job_with_missing_fields_is_repaired_and_shown(self):
        self.write("queue.json", json.dumps([{"run_at": "2030-01-01 09:00", "repeat": "yearly"}]))
        app = self.start_app()
        (job,) = app.jobs
        self.assertEqual((job["repeat"], job["status"]), ("none", "pending"))
        self.assertEqual(app.queue_table.get_children(), (job["id"],))
        self.assertFalse(self.exists("queue.json.corrupt"))


class SchedulerRunTest(AppTestCase):
    def test_due_post_is_published_and_marked_done(self):
        app = self.start_app()
        app.add_page("Hi English", "facebook.com/hienglishoffical")
        content = "✨ Dòng 1\n- dòng 2 bắt đầu bằng gạch\n\"ngoặc kép\""
        job = app.enqueue(content, "https://facebook.com/hienglishoffical", "", self.past(), "none")
        app.tick()
        self.assertEqual(job["status"], "running")
        self.pump(lambda: not app.is_busy())

        posted = self.read_json("posted.json")
        self.assertEqual(posted["content"], content)
        self.assertEqual(posted["page"], "https://facebook.com/hienglishoffical")
        self.assertFalse(posted["headless"])
        self.assertEqual(job["status"], "done")
        self.assertEqual(self.read_json("queue.json")[0]["status"], "done")
        self.assertIn("Đăng bài giả lập", self.log_text())

    def test_temporary_content_file_is_removed_after_the_run(self):
        app = self.start_app()
        app.enqueue("Bài", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertFalse(os.path.exists(self.read_json("posted.json")["content_file"]))

    def test_failing_post_is_marked_failed_with_the_exit_code(self):
        app = self.start_app()
        job = app.enqueue("FAIL", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "failed")
        self.assertIn("1", job["last_message"])

    def test_repeating_post_is_rescheduled_after_it_runs(self):
        app = self.start_app()
        job = app.enqueue("Bài lặp", "", "", self.past(), "daily")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "pending")
        self.assertGreater(scheduler.parse_run_at(job["run_at"]), datetime.now())

    def test_future_post_is_not_published_yet(self):
        app = self.start_app()
        app.enqueue("Bài", "", "", datetime.now() + timedelta(hours=1), "none")
        app.tick()
        self.assertFalse(app.is_busy())
        self.assertFalse(self.exists("posted.json"))

    def test_nothing_is_published_while_the_scheduler_is_off(self):
        app = self.start_app()
        app.set_scheduler_enabled(False)
        job = app.enqueue("Bài", "", "", self.past(), "none")
        app.tick()
        self.assertFalse(app.is_busy())
        self.assertEqual(job["status"], "pending")
        self.assertFalse(self.read_json("config.json")["scheduler_enabled"])

    def test_only_one_post_runs_at_a_time(self):
        app = self.start_app()
        first = app.enqueue("Bài 1", "", "", self.past() - timedelta(minutes=1), "none")
        second = app.enqueue("Bài 2", "", "", self.past(), "none")
        app.tick()
        app.tick()
        self.assertEqual(first["status"], "running")
        self.assertEqual(second["status"], "pending")
        self.pump(lambda: second["status"] == "done", timeout=30)
        self.assertEqual(first["status"], "done")

    def test_job_fails_without_a_login_session(self):
        os.remove(os.path.join(self.dir, "state.json"))
        app = self.start_app()
        job = app.enqueue("Bài", "", "", self.past(), "none")
        app.tick()
        self.assertFalse(app.is_busy())
        self.assertEqual(job["status"], "failed")
        self.assertIn("đăng nhập", job["last_message"])
        self.assertFalse(self.exists("posted.json"))

    def test_post_that_came_due_while_ticks_were_paused_is_missed(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() - timedelta(minutes=30), "none")
        daily = app.enqueue("Bài lặp", "", "", datetime.now() - timedelta(minutes=30), "daily")
        # No tick for an hour: the computer was asleep
        app._last_tick = datetime.now() - timedelta(hours=1)
        app.tick()
        self.assertFalse(app.is_busy())
        self.assertEqual(job["status"], "missed")
        self.assertIn("máy ngủ", job["last_message"])
        self.assertGreater(scheduler.parse_run_at(daily["run_at"]), datetime.now())
        self.assertEqual(self.read_json("queue.json")[0]["status"], "missed")
        self.assertFalse(self.exists("posted.json"))

    def test_post_that_came_due_while_the_scheduler_was_off_is_missed(self):
        app = self.start_app()
        app.set_scheduler_enabled(False)
        job = app.enqueue("Bài", "", "", datetime.now() - timedelta(minutes=30), "none")
        app._last_tick = datetime.now() - timedelta(hours=1)
        app.tick()
        self.assertEqual(job["status"], "pending")
        app.set_scheduler_enabled(True)
        app.tick()
        self.assertEqual(job["status"], "missed")

    def test_post_that_waited_behind_other_posts_still_runs(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() - timedelta(minutes=30), "none")
        app.tick()
        self.assertEqual(job["status"], "running")
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "done")

    def test_post_shortly_after_a_pause_still_runs(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", self.past(), "none")
        app._last_tick = datetime.now() - timedelta(hours=1)
        app.tick()
        self.assertEqual(job["status"], "running")
        self.pump(lambda: not app.is_busy())

    def test_headless_setting_is_passed_to_the_script(self):
        app = self.start_app()
        app.set_headless(True)
        app.enqueue("Bài", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertTrue(self.read_json("posted.json")["headless"])
        self.assertTrue(self.read_json("config.json")["headless"])


class PublishNowTest(AppTestCase):
    def test_publishes_immediately_with_the_image(self):
        app = self.start_app()
        image = os.path.join(self.dir, "anh.png")
        self.write("anh.png", "x")
        app.publish_now("Đăng ngay 🎉", "", image)
        self.pump(lambda: not app.is_busy())
        posted = self.read_json("posted.json")
        self.assertEqual(posted["content"], "Đăng ngay 🎉")
        self.assertEqual(posted["image"], image)
        self.assertIsNone(posted["page"])

    def test_refuses_without_a_login_session(self):
        os.remove(os.path.join(self.dir, "state.json"))
        app = self.start_app()
        with self.assertRaises(RuntimeError):
            app.publish_now("Bài", "", "")
        self.assertFalse(app.is_busy())

    def test_refuses_while_another_task_is_running(self):
        app = self.start_app()
        app.publish_now("SLOW", "", "")
        with self.assertRaises(RuntimeError):
            app.publish_now("Bài 2", "", "")

    def test_stopping_a_running_job_marks_it_failed(self):
        app = self.start_app()
        job = app.enqueue("SLOW", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: "Đăng bài giả lập" in self.log_text())
        app.stop_task()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "failed")
        self.assertIn("dừng", job["last_message"].lower())


class FailureTest(AppTestCase):
    """Errors the app has to survive: a failed save, a crashing callback."""

    def break_saving(self, name):
        def refuse(path, data):
            raise PermissionError(13, "tệp đang bị khóa")

        original = getattr(store, name)
        setattr(store, name, refuse)
        self.addCleanup(setattr, store, name, original)

    def test_post_finishes_even_if_the_queue_cannot_be_saved(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", self.past(), "none")
        self.break_saving("save_queue")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "done")
        self.assertIn("Không lưu được queue.json", self.log_text())
        self.assertEqual(str(app.publish_button["state"]), "normal")

    def test_settings_change_survives_a_failed_save(self):
        app = self.start_app()
        self.break_saving("save_config")
        app.set_headless(True)
        self.assertTrue(app.settings["headless"])
        self.assertIn("Không lưu được config.json", self.log_text())

    def test_tick_loop_goes_on_after_an_error(self):
        app = self.start_app()

        def crash():
            raise RuntimeError("hỏng")

        app.tick = crash
        timers = set(app._timers)
        with self.assertRaises(RuntimeError):
            app._tick_loop()
        self.assertEqual(len(app._timers - timers), 1)

    def test_event_polling_goes_on_after_an_error(self):
        app = self.start_app()
        app.events.put(("exit", 0))
        app.events.put(("line", "dòng sau lỗi"))

        def crash(exit_code):
            raise RuntimeError("hỏng")

        app._finish_task = crash
        timers = set(app._timers)
        with self.assertRaises(RuntimeError):
            app._poll_events()
        self.assertEqual(len(app._timers - timers), 1)
        del app._finish_task
        self.pump(lambda: "dòng sau lỗi" in self.log_text())

    def test_unexpected_error_in_a_callback_is_shown_in_the_log(self):
        app = self.start_app()

        def crash():
            raise RuntimeError("hỏng giữa chừng")

        app.root.after(0, crash)
        with contextlib.redirect_stderr(io.StringIO()):
            self.pump(lambda: "hỏng giữa chừng" in self.log_text())
        self.assertIn("RuntimeError", self.log_text())


class LoginTest(AppTestCase):
    def test_login_saves_the_session_after_confirmation(self):
        os.remove(os.path.join(self.dir, "state.json"))
        app = self.start_app()
        app.start_login()
        self.pump(lambda: "chờ đăng nhập" in self.log_text())
        self.assertFalse(self.exists("state.json"))
        app.confirm_login()
        self.pump(lambda: not app.is_busy())
        self.assertTrue(self.exists("state.json"))
        self.assertIn("Đã đăng nhập", app.session_label["text"])

    def test_session_label_says_when_there_is_no_session(self):
        os.remove(os.path.join(self.dir, "state.json"))
        app = self.start_app()
        self.assertIn("Chưa đăng nhập", app.session_label["text"])

    def test_state_file_of_a_logged_out_browser_is_not_a_session(self):
        self.write("state.json", '{"cookies": [], "origins": []}')
        app = self.start_app()
        self.assertIn("Chưa đăng nhập", app.session_label["text"])
        with self.assertRaises(RuntimeError):
            app.publish_now("Bài", "", "")

    def test_expired_session_is_reported_on_the_job_and_the_settings_tab(self):
        app = self.start_app()
        job = app.enqueue("EXPIRED", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "failed")
        self.assertIn("hết hạn", job["last_message"])
        self.assertIn("hết hạn", app.session_label["text"])

    def test_logging_in_again_clears_the_expired_session(self):
        app = self.start_app()
        app.publish_now("EXPIRED", "", "")
        self.pump(lambda: not app.is_busy())
        self.assertIn("hết hạn", app.session_label["text"])
        app.start_login()
        self.pump(lambda: "chờ đăng nhập" in self.log_text())
        app.confirm_login()
        self.pump(lambda: not app.is_busy())
        self.assertIn("Đã đăng nhập", app.session_label["text"])

    def test_ordinary_failure_does_not_blame_the_session(self):
        app = self.start_app()
        app.publish_now("FAIL", "", "")
        self.pump(lambda: not app.is_busy())
        self.assertIn("Đã đăng nhập", app.session_label["text"])

    def test_session_stays_expired_until_a_post_goes_through(self):
        app = self.start_app()
        for content, expected in (("EXPIRED", "hết hạn"), ("FAIL", "hết hạn"), ("Bài", "Đã đăng nhập")):
            with self.subTest(content=content):
                app.publish_now(content, "", "")
                self.pump(lambda: not app.is_busy())
                self.assertIn(expected, app.session_label["text"])


class SingleInstanceTest(unittest.TestCase):
    def start(self, lock):
        with mock.patch.object(app_module.tk, "Tk"), \
                mock.patch.object(store, "lock_file", return_value=lock) as locked, \
                mock.patch.object(app_module.messagebox, "showerror") as refused, \
                mock.patch.object(app_module, "App") as started:
            app_module.main()
        return locked, refused, started

    def test_second_copy_refuses_to_start(self):
        locked, refused, started = self.start(lock=None)
        refused.assert_called_once()
        started.assert_not_called()

    def test_first_copy_starts_and_locks_its_own_folder(self):
        locked, refused, started = self.start(lock=object())
        refused.assert_not_called()
        started.assert_called_once()
        (lock_path,), _ = locked.call_args
        self.assertEqual(
            lock_path, os.path.join(os.path.dirname(os.path.abspath(app_module.__file__)), "app.lock")
        )


if __name__ == "__main__":
    unittest.main()
