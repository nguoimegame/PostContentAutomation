import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

import app as app_module
import notify
import scheduler
import store
from tests.test_app import SESSION, AppTestCase

HI = "https://facebook.com/hienglishoffical"


class AccountsTest(AppTestCase):
    def test_a_new_account_is_saved_and_offered_in_the_compose_tab(self):
        app = self.start_app()
        app.add_account("shop2")
        self.assertEqual(self.read_json("config.json")["accounts"], ["shop2"])
        self.assertEqual(list(app.account_box["values"]), [app_module.DEFAULT_ACCOUNT, "shop2"])

    def test_bad_names_are_refused(self):
        app = self.start_app()
        for name in ("", "  ", "default", "../x", "a b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                app.add_account(name)

    def test_a_name_cannot_be_added_twice(self):
        app = self.start_app()
        app.add_account("shop2")
        with self.assertRaises(ValueError):
            app.add_account("shop2")

    def test_removing_an_account_keeps_its_login_file(self):
        self.write("state.shop2.json", SESSION)
        app = self.start_app()
        app.add_account("shop2")
        app.remove_account("shop2")
        self.assertEqual(self.read_json("config.json")["accounts"], [])
        self.assertTrue(self.exists("state.shop2.json"))

    def test_the_list_shows_who_is_logged_in(self):
        self.write("state.shop2.json", SESSION)
        app = self.start_app()
        app.add_account("shop2")
        app.add_account("chua-co")
        lines = app.account_list.get(0, "end")
        self.assertIn("Đã đăng nhập", lines[0])
        self.assertIn("shop2  —  Đã đăng nhập", lines[1])
        self.assertIn("chua-co  —  Chưa đăng nhập", lines[2])

    def test_the_default_account_is_selected_by_default(self):
        app = self.start_app()
        self.assertEqual(app.selected_account(), "")
        app.add_account("shop2")
        app.account_var.set("shop2")
        self.assertEqual(app.selected_account(), "shop2")

    def test_a_removed_account_falls_back_to_the_default(self):
        app = self.start_app()
        app.add_account("shop2")
        app.account_var.set("shop2")
        app.remove_account("shop2")
        self.assertEqual(app.selected_account(), "")

    def test_a_post_uses_the_chosen_account(self):
        self.write("state.shop2.json", SESSION)
        app = self.start_app()
        app.add_account("shop2")
        app.publish_now("Bài", "", "", "shop2")
        self.pump(lambda: not app.is_busy())
        self.assertEqual(self.read_json("posted.json")["account"], "shop2")

    def test_the_default_account_sends_no_account_flag(self):
        app = self.start_app()
        app.publish_now("Bài", "", "")
        self.pump(lambda: not app.is_busy())
        self.assertEqual(self.read_json("posted.json")["account"], "")

    def test_an_account_without_a_login_cannot_post(self):
        app = self.start_app()
        app.add_account("shop2")
        with self.assertRaises(RuntimeError) as caught:
            app.publish_now("Bài", "", "", "shop2")
        self.assertIn("shop2", str(caught.exception))

    def test_a_queued_post_remembers_its_account(self):
        self.write("state.shop2.json", SESSION)
        app = self.start_app()
        app.add_account("shop2")
        job = app.enqueue("Bài", "", "", self.past(), "none", account="shop2")
        self.assertEqual(self.read_json("queue.json")[0]["account"], "shop2")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "done")
        self.assertEqual(self.read_json("posted.json")["account"], "shop2")

    def test_a_queued_post_for_an_account_without_a_login_fails(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", self.past(), "none", account="shop2")
        app.tick()
        self.assertEqual(job["status"], "failed")
        self.assertIn("shop2", job["last_message"])

    def test_login_starts_for_the_chosen_account(self):
        app = self.start_app()
        with mock.patch.object(app.runner, "start") as start:
            app.start_login("shop2")
        command = start.call_args.args[0]
        self.assertEqual(command[-2:], ["--account", "shop2"])
        self.assertEqual(app.task["account"], "shop2")

    def test_login_refuses_an_unusable_name(self):
        app = self.start_app()
        with self.assertRaises(ValueError):
            app.start_login("../x")

    def test_logging_in_another_account_does_not_clear_an_expired_default_session(self):
        app = self.start_app()
        app.publish_now("EXPIRED", "", "")
        self.pump(lambda: not app.is_busy())
        app.task = {"kind": "login", "stopped": False, "account": "shop2"}
        app._finish_task(0)
        self.assertIn("hết hạn", app.session_label["text"])


class RetryAndLinkTest(AppTestCase):
    def test_a_failure_before_the_button_is_tried_again(self):
        app = self.start_app()
        job = app.enqueue("RETRYABLE", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual((job["status"], job["attempts"]), ("pending", 1))
        self.assertIn("thử lại", job["last_message"])
        self.assertEqual(app.queue_table.set(job["id"], "status"), "Chờ")
        # The wait is over: the second try works
        job["not_before"] = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "done")
        self.assertNotIn("attempts", job)

    def test_tries_run_out(self):
        app = self.start_app()
        app.settings["retry_attempts"] = 1
        job = app.enqueue("ALWAYSRETRY", "", "", self.past(), "none")
        for _ in range(2):
            app.tick()
            self.pump(lambda: not app.is_busy())
            job["not_before"] = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        self.assertEqual(job["status"], "failed")

    def test_no_retries_when_the_setting_is_zero(self):
        app = self.start_app()
        app.settings["retry_attempts"] = 0
        job = app.enqueue("ALWAYSRETRY", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "failed")

    def test_a_blocked_post_is_failed_with_an_explanation(self):
        app = self.start_app()
        job = app.enqueue("BLOCKED", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "failed")
        self.assertIn("Bị chặn", job["last_message"])

    def test_the_link_of_the_post_is_kept_and_shown(self):
        app = self.start_app()
        job = app.enqueue("LINK", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["post_url"], "https://www.facebook.com/x/posts/42")
        self.assertEqual(self.read_json("queue.json")[0]["post_url"], "https://www.facebook.com/x/posts/42")
        self.assertEqual(app.queue_table.set(job["id"], "link"), "https://www.facebook.com/x/posts/42")
        self.assertIn("hiện trên trang", job["last_message"])

    def test_a_post_not_seen_on_the_page_is_done_but_says_so(self):
        app = self.start_app()
        job = app.enqueue("NOTSEEN", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertEqual(job["status"], "done")
        self.assertIn("chưa thấy bài", job["last_message"])
        self.assertNotIn("post_url", job)

    def test_open_link_button(self):
        app = self.start_app()
        job = app.enqueue("LINK", "", "", self.past(), "none")
        app.tick()
        self.pump(lambda: not app.is_busy())
        app.queue_table.selection_set(job["id"])
        with mock.patch.object(app, "_open_url") as opened:
            app._on_open_link_clicked()
        opened.assert_called_once_with("https://www.facebook.com/x/posts/42")

    def test_open_link_without_a_link_explains(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", self.past() + timedelta(days=1), "none")
        app.queue_table.selection_set(job["id"])
        with mock.patch.object(app_module.messagebox, "showinfo") as info, mock.patch.object(app, "_open_url") as opened:
            app._on_open_link_clicked()
        info.assert_called_once()
        opened.assert_not_called()

    def test_a_manual_post_without_a_queue_entry_still_finishes(self):
        app = self.start_app()
        app.publish_now("LINK", "", "")
        self.pump(lambda: not app.is_busy())
        self.assertIn("Đã đăng", self.log_text())


class GraceTest(AppTestCase):
    def test_the_late_limit_comes_from_the_settings(self):
        self.write("config.json", json.dumps({"grace_minutes": 90}))
        old = scheduler.new_job("Trễ 30 phút", "", "", datetime.now() - timedelta(minutes=30), "none")
        self.write("queue.json", json.dumps([old]))
        app = self.start_app()
        self.assertEqual(app.jobs[0]["status"], "pending")

    def test_with_the_default_it_is_missed(self):
        old = scheduler.new_job("Trễ 30 phút", "", "", datetime.now() - timedelta(minutes=30), "none")
        self.write("queue.json", json.dumps([old]))
        app = self.start_app()
        self.assertEqual(app.jobs[0]["status"], "missed")


class NotificationTest(AppTestCase):
    def values(self, **extra):
        values = {"telegram_token": "T", "telegram_chat": "42", "webhook": "", "email_host": "", "email_port": "587",
                  "email_user": "", "email_password": "", "email_sender": "", "email_to": "", "events": ["done", "failed"]}
        values.update(extra)
        return values

    def test_settings_are_saved(self):
        app = self.start_app()
        app.set_notifications(self.values(webhook=" https://hook.example/x "))
        saved = self.read_json("config.json")["notifications"]
        self.assertEqual(saved["telegram"], {"bot_token": "T", "chat_id": "42"})
        self.assertEqual(saved["webhook_url"], "https://hook.example/x")
        self.assertEqual(saved["events"], ["done", "failed"])

    def test_the_boxes_show_what_was_saved(self):
        app = self.start_app()
        app.set_notifications(self.values())
        app._sync_advanced_widgets()
        self.assertEqual(app.notify_vars["telegram_chat"].get(), "42")
        self.assertTrue(app.notify_event_vars["done"].get())
        self.assertFalse(app.notify_event_vars["missed"].get())

    def test_a_bad_port_is_refused(self):
        app = self.start_app()
        with self.assertRaises(ValueError):
            app.set_notifications(self.values(email_port="abc"))

    def test_unknown_events_are_ignored(self):
        app = self.start_app()
        app.set_notifications(self.values(events=["done", "lạ"]))
        self.assertEqual(app.settings["notifications"]["events"], ["done"])

    def test_a_finished_post_is_reported_to_the_configured_channels(self):
        app = self.start_app()
        app.set_notifications(self.values())
        with mock.patch.object(app_module.notify, "notify_in_background") as send:
            job = app.enqueue("LINK", HI, "", self.past(), "none")
            app.tick()
            self.pump(lambda: not app.is_busy())
        send.assert_called_once()
        settings, event, text, sent_job = send.call_args.args[:4]
        self.assertEqual((event, sent_job["id"]), ("done", job["id"]))
        self.assertIn("https://www.facebook.com/x/posts/42", text)

    def test_failures_and_expired_sessions_are_reported_with_their_own_event(self):
        app = self.start_app()
        app.set_notifications(self.values(events=["done", "failed", "session_expired"]))
        events = []
        with mock.patch.object(app_module.notify, "notify_in_background", side_effect=lambda s, e, *a, **k: events.append(e)):
            for content in ("FAIL", "EXPIRED"):
                app.publish_now(content, "", "")
                self.pump(lambda: not app.is_busy())
        self.assertEqual(events, ["failed", "session_expired"])

    def test_nothing_is_sent_when_no_channel_is_set_up(self):
        app = self.start_app()
        with mock.patch.object(app_module.notify, "notify_in_background") as send:
            app.publish_now("Bài", "", "")
            self.pump(lambda: not app.is_busy())
        send.assert_not_called()

    def test_a_retry_is_not_reported_yet(self):
        app = self.start_app()
        app.set_notifications(self.values())
        with mock.patch.object(app_module.notify, "notify_in_background") as send:
            app.enqueue("RETRYABLE", "", "", self.past(), "none")
            app.tick()
            self.pump(lambda: not app.is_busy())
        send.assert_not_called()

    def test_a_test_message_needs_a_channel(self):
        app = self.start_app()
        with self.assertRaises(RuntimeError):
            app.send_test_notification()

    def test_a_test_message_goes_to_the_channels(self):
        app = self.start_app()
        app.set_notifications(self.values())
        with mock.patch.object(app_module.notify, "notify_in_background") as send:
            app.send_test_notification()
        self.assertEqual(send.call_args.args[1], "test")

    def test_a_failed_channel_is_reported_in_the_log(self):
        app = self.start_app()
        app._on_notified([("telegram", "OSError: mất mạng"), ("webhook", None)])
        self.pump(lambda: "Không gửi được thông báo qua telegram" in self.log_text())


class SafetySettingsTest(AppTestCase):
    def values(self, **extra):
        values = {"retry_attempts": "3", "retry_delay_minutes": "10", "grace_minutes": "15", "duplicate_days": "14",
                  "similarity_percent": "80", "max_per_day": "20", "max_per_page_per_day": "4"}
        values.update(extra)
        return values

    def test_values_are_saved_where_the_other_modules_read_them(self):
        app = self.start_app()
        app.set_safety(self.values())
        config = store.load_config(os.path.join(self.dir, "config.json"))
        self.assertEqual((config["retry_attempts"], config["retry_delay_minutes"], config["grace_minutes"]), (3, 10, 15))
        self.assertEqual(config["spam"], {"duplicate_days": 14, "similarity": 0.8, "max_per_day": 20, "max_per_page_per_day": 4})

    def test_the_boxes_show_the_saved_values(self):
        app = self.start_app()
        app.set_safety(self.values())
        app._sync_advanced_widgets()
        self.assertEqual(app.safety_vars["similarity_percent"].get(), "80")
        self.assertEqual(app.safety_vars["retry_attempts"].get(), "3")

    def test_defaults_are_shown_at_first(self):
        app = self.start_app()
        self.assertEqual(app.safety_vars["max_per_page_per_day"].get(), "3")
        self.assertEqual(app.safety_vars["similarity_percent"].get(), "90")

    def test_nonsense_is_refused_and_nothing_changes(self):
        app = self.start_app()
        for bad in ({"retry_attempts": "-1"}, {"grace_minutes": "ít"}, {"similarity_percent": "20"},
                    {"similarity_percent": "101"}, {"max_per_day": ""}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.set_safety(self.values(**bad))
        self.assertEqual(app.settings["retry_attempts"], 2)

    def test_zero_turns_a_limit_off(self):
        app = self.start_app()
        app.set_safety(self.values(max_per_day="0", duplicate_days="0"))
        self.assertEqual(app.settings["spam"]["max_per_day"], 0)


class RepeatTest(AppTestCase):
    def test_the_repeat_box_offers_the_new_kinds(self):
        app = self.start_app()
        values = list(app.repeat_box["values"])
        self.assertIn("Thứ 2 đến Thứ 6", values)
        self.assertIn("Hằng tháng", values)
        self.assertEqual(values[-1], app_module.CUSTOM_DAYS_LABEL)

    def test_labels_map_back_to_repeats(self):
        for repeat in ("none", "daily", "weekly", "weekdays", "monthly", "days:mon,wed", "days:sun"):
            with self.subTest(repeat=repeat):
                self.assertEqual(app_module.repeat_from_label(app_module.repeat_label(repeat)), repeat)
        with self.assertRaises(ValueError):
            app_module.repeat_from_label("lung tung")

    def test_choosing_custom_days_asks_which(self):
        app = self.start_app()
        app.repeat_var.set(app_module.CUSTOM_DAYS_LABEL)
        with mock.patch.object(app, "_ask_days", return_value={0, 2, 4}):
            app._on_repeat_selected(app.repeat_var, app.root)
        self.assertEqual(app.repeat_var.get(), "Thứ 2, Thứ 4, Thứ 6")
        self.assertEqual(app_module.repeat_from_label(app.repeat_var.get()), "days:mon,wed,fri")

    def test_cancelling_the_day_picker_means_no_repeat(self):
        app = self.start_app()
        app.repeat_var.set(app_module.CUSTOM_DAYS_LABEL)
        with mock.patch.object(app, "_ask_days", return_value=None):
            app._on_repeat_selected(app.repeat_var, app.root)
        self.assertEqual(app.repeat_var.get(), "Không lặp")

    def test_other_choices_are_left_alone(self):
        app = self.start_app()
        app.repeat_var.set("Hằng ngày")
        with mock.patch.object(app, "_ask_days") as ask:
            app._on_repeat_selected(app.repeat_var, app.root)
        ask.assert_not_called()

    def test_scheduling_with_a_flexible_repeat_and_jitter(self):
        app = self.start_app()
        app.content_box.insert("1.0", "Bài hằng tuần")
        run_at = datetime.now() + timedelta(days=1)
        app.run_at_var.set(scheduler.format_run_at(run_at))
        app.repeat_var.set("Thứ 2 đến Thứ 6")
        app.jitter_var.set("25")
        with mock.patch.object(app_module.messagebox, "askokcancel", return_value=True):
            app._on_enqueue_clicked()
        job = self.read_json("queue.json")[0]
        self.assertEqual((job["repeat"], job["jitter_minutes"]), ("weekdays", 25))

    def test_a_bad_jitter_is_refused(self):
        app = self.start_app()
        app.content_box.insert("1.0", "Bài")
        app.run_at_var.set(scheduler.format_run_at(datetime.now() + timedelta(days=1)))
        app.jitter_var.set("abc")
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            app._on_enqueue_clicked()
        error.assert_called_once()
        self.assertEqual(app.jobs, [])

    def test_the_queue_table_names_the_repeat(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(days=1), "days:tue,thu")
        self.assertEqual(app.queue_table.set(job["id"], "repeat"), "Thứ 3, Thứ 5")

    def test_a_repeating_post_is_published_with_the_duplicate_flag(self):
        app = self.start_app()
        app.enqueue("Bài lặp", "", "", self.past(), "weekdays")
        app.tick()
        self.pump(lambda: not app.is_busy())
        self.assertTrue(self.read_json("posted.json")["allow_duplicate"])


class VariantsAndMediaTest(AppTestCase):
    A = "https://facebook.com/groups/nhom-a"
    B = "https://facebook.com/groups/nhom-b"

    def test_each_page_gets_its_own_text(self):
        app = self.start_app()
        app.add_page("Nhóm A", self.A, "English (Mỹ)")
        app.add_page("Nhóm B", self.B, "Tiếng Việt (Việt Nam)")
        run_at = datetime.now() + timedelta(days=1)
        jobs = app.enqueue_many("Chào {page} ({language})", [self.A, self.B], "", run_at, "none", 5)
        self.assertEqual([job["content"] for job in jobs], ["Chào Nhóm A (English (Mỹ))", "Chào Nhóm B (Tiếng Việt (Việt Nam))"])

    def test_text_without_markup_is_unchanged(self):
        app = self.start_app()
        job = app.enqueue("Giá {đặc biệt} 100%", "", "", datetime.now() + timedelta(days=1), "none")
        self.assertEqual(job["content"], "Giá {đặc biệt} 100%")

    def test_posting_now_fills_in_the_page(self):
        app = self.start_app()
        app.add_page("Nhóm A", self.A)
        app.publish_now("Gửi {page}", self.A, "")
        self.pump(lambda: not app.is_busy())
        self.assertEqual(self.read_json("posted.json")["content"], "Gửi Nhóm A")

    def test_several_files_are_checked_one_by_one(self):
        self.write("a.png", "x")
        a = os.path.join(self.dir, "a.png")
        app_module.App._check_post("", a)
        with self.assertRaises(ValueError) as caught:
            app_module.App._check_post("", f"{a}|{os.path.join(self.dir, 'khong-co.png')}")
        self.assertIn("khong-co.png", str(caught.exception))

    def test_several_files_reach_post_py_as_several_arguments(self):
        self.write("a.png", "x")
        self.write("b.png", "x")
        a, b = os.path.join(self.dir, "a.png"), os.path.join(self.dir, "b.png")
        app = self.start_app()
        app.publish_now("Bài", "", f"{a}|{b}")
        self.pump(lambda: not app.is_busy())
        self.assertEqual(self.read_json("posted.json")["images"], [a, b])

    def test_the_file_picker_joins_several_files(self):
        app = self.start_app()
        with mock.patch.object(app_module.filedialog, "askopenfilenames", return_value=("D:/a.png", "D:/b.mp4")):
            app._choose_media(app.media_var)
        self.assertEqual(app.media_var.get(), os.path.normpath("D:/a.png") + "|" + os.path.normpath("D:/b.mp4"))

    def test_cancelling_the_picker_keeps_the_old_choice(self):
        app = self.start_app()
        app.media_var.set("cu.png")
        with mock.patch.object(app_module.filedialog, "askopenfilenames", return_value=()):
            app._choose_media(app.media_var)
        self.assertEqual(app.media_var.get(), "cu.png")


class CalendarTabTest(AppTestCase):
    def test_there_is_a_calendar_tab_showing_the_queue(self):
        app = self.start_app()
        tabs = [app.notebook.tab(tab, "text").strip() for tab in app.notebook.tabs()]
        self.assertIn("Lịch", tabs)
        self.assertIn("Nâng cao", tabs)
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(days=2), "none")
        app.calendar.anchor = scheduler.parse_run_at(job["run_at"]).date()
        app.calendar.refresh()
        self.assertEqual([chip[4] for chip in app.calendar.chips], [job["id"]])

    def test_moving_a_post_changes_its_time_and_clears_waits(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(hours=2), "none")
        job["not_before"] = "2030-01-01T00:00:00"
        later = (datetime.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        app.move_job(job["id"], later)
        self.assertEqual(job["run_at"], scheduler.format_run_at(later))
        self.assertNotIn("not_before", job)
        self.assertEqual(self.read_json("queue.json")[0]["run_at"], scheduler.format_run_at(later))

    def test_only_waiting_posts_in_the_future_move(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(hours=2), "none")
        with self.assertRaises(ValueError):
            app.move_job(job["id"], datetime.now() - timedelta(hours=1))
        job["status"] = "done"
        with self.assertRaises(ValueError):
            app.move_job(job["id"], datetime.now() + timedelta(days=1))
        with self.assertRaises(ValueError):
            app.move_job("khong-co", datetime.now() + timedelta(days=1))

    def test_a_monthly_post_follows_its_new_day(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(days=3), "monthly")
        target = (datetime.now() + timedelta(days=9)).replace(second=0, microsecond=0)
        app.move_job(job["id"], target)
        self.assertEqual(job["anchor_day"], target.day)

    def test_the_calendar_follows_the_queue(self):
        app = self.start_app()
        calls = []
        app.calendar.refresh = lambda: calls.append(1)
        app.enqueue("Bài", "", "", datetime.now() + timedelta(hours=2), "none")
        self.assertTrue(calls)

    def test_a_post_can_be_opened_from_the_calendar(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(hours=2), "none")
        with mock.patch.object(app, "_open_job_dialog") as opened:
            app._open_job_by_id(job["id"])
        opened.assert_called_once_with(job)

    def test_a_running_post_is_not_opened(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(hours=2), "none")
        job["status"] = "running"
        with mock.patch.object(app, "_open_job_dialog") as opened, mock.patch.object(app_module.messagebox, "showerror"):
            app._open_job_by_id(job["id"])
        opened.assert_not_called()


def find_widgets(widget, kind):
    found = [widget] if widget.winfo_class() == kind else []
    for child in widget.winfo_children():
        found += find_widgets(child, kind)
    return found


class EditDialogTest(AppTestCase):
    def open_dialog(self, app, job):
        before = set(app.root.winfo_children())
        app._open_job_dialog(job)
        (dialog,) = [child for child in app.root.winfo_children() if child not in before and child.winfo_class() == "Toplevel"]
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        return dialog

    def save(self, dialog):
        (button,) = [b for b in find_widgets(dialog, "TButton") if b.cget("text") == "Lưu"]
        button.invoke()

    def test_the_dialog_shows_and_saves_the_new_fields(self):
        self.write("state.shop2.json", SESSION)
        app = self.start_app()
        app.add_account("shop2")
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(days=1), "weekdays", account="shop2", jitter=10)
        dialog = self.open_dialog(app, job)
        boxes = find_widgets(dialog, "TCombobox")
        shown = [box.get() for box in boxes]
        self.assertIn("Thứ 2 đến Thứ 6", shown)
        self.assertIn("shop2", shown)
        spin = find_widgets(dialog, "TSpinbox")[0]
        self.assertEqual(spin.get(), "10")
        spin.delete(0, "end")
        spin.insert(0, "30")
        with mock.patch.object(app_module.messagebox, "askokcancel", return_value=True):
            self.save(dialog)
        self.assertEqual(job["jitter_minutes"], 30)
        self.assertEqual(job["account"], "shop2")
        self.assertEqual(job["repeat"], "weekdays")

    def test_clearing_account_and_jitter_removes_them(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(days=1), "none", jitter=10)
        job["account"] = "cu"
        dialog = self.open_dialog(app, job)
        boxes = find_widgets(dialog, "TCombobox")
        account_box = next(box for box in boxes if box.get() == "cu")
        account_box.set(app_module.DEFAULT_ACCOUNT)
        find_widgets(dialog, "TSpinbox")[0].delete(0, "end")
        find_widgets(dialog, "TSpinbox")[0].insert(0, "0")
        self.save(dialog)
        self.assertNotIn("account", job)
        self.assertNotIn("jitter_minutes", job)

    def test_editing_resets_the_retry_state(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(days=1), "none")
        job.update(attempts=2, not_before="2030-01-01T00:00:00", status="failed")
        dialog = self.open_dialog(app, job)
        self.save(dialog)
        self.assertEqual(job["status"], "pending")
        self.assertNotIn("attempts", job)
        self.assertNotIn("not_before", job)

    def test_a_bad_jitter_keeps_the_dialog_open(self):
        app = self.start_app()
        job = app.enqueue("Bài", "", "", datetime.now() + timedelta(days=1), "none")
        dialog = self.open_dialog(app, job)
        spin = find_widgets(dialog, "TSpinbox")[0]
        spin.delete(0, "end")
        spin.insert(0, "abc")
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            self.save(dialog)
        error.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertNotIn("jitter_minutes", job)


class BackupTest(AppTestCase):
    def test_export_then_import_into_the_app(self):
        app = self.start_app()
        app.add_page("Hi English", HI, "English (Mỹ)")
        app.save_template("Chào", "xin chào")
        app.add_account("shop2")
        app.enqueue("Bài hẹn", HI, "", datetime.now() + timedelta(days=1), "none")
        out = os.path.join(self.dir, "sao-luu.json")
        data = app.export_backup(out)
        self.assertEqual(len(data["queue"]), 1)

        app.remove_page("Hi English")
        app.remove_template("Chào")
        app.remove_account("shop2")
        app.remove_job(app.jobs[0]["id"])
        report = app.import_backup(out)
        self.assertEqual((report["jobs_added"], report["pages"], report["templates"]), (1, 1, 1))
        self.assertEqual([page["name"] for page in app.settings["pages"]], ["Hi English"])
        self.assertEqual(app.settings["accounts"], ["shop2"])
        self.assertEqual(len(app.jobs), 1)
        self.assertEqual(len(self.read_json("queue.json")), 1)
        self.assertIn("Hi English", app.page_list.get(0, "end")[0])
        self.assertIn("shop2", app.account_list.get(0, "end")[-1])

    def test_the_export_leaves_out_secrets_and_logins(self):
        app = self.start_app()
        app.set_notifications({"telegram_token": "BÍ-MẬT", "telegram_chat": "1", "events": ["done"]})
        out = os.path.join(self.dir, "sao-luu.json")
        app.export_backup(out)
        with open(out, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("BÍ-MẬT", text)
        self.assertNotIn("c_user", text)

    def test_importing_a_wrong_file_is_refused(self):
        app = self.start_app()
        self.write("x.json", '{"a": 1}')
        with self.assertRaises(ValueError):
            app.import_backup(os.path.join(self.dir, "x.json"))

    def test_importing_keeps_the_scheduler_and_headless_boxes_in_step(self):
        app = self.start_app()
        out = os.path.join(self.dir, "sao-luu.json")
        data = app.export_backup(out)
        data["config"]["headless"] = True
        data["config"]["page_gap_minutes"] = 11
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        app.import_backup(out, replace=True)
        self.assertTrue(app.headless_var.get())
        self.assertEqual(app.gap_var.get(), "11")


class MainEntryTest(unittest.TestCase):
    def test_daemon_flag_hands_over_to_the_background_runner(self):
        import daemon

        with mock.patch.object(daemon, "main", return_value=0) as run, mock.patch.object(app_module.tk, "Tk") as window:
            self.assertEqual(app_module.main(["--daemon", "--once"]), 0)
        run.assert_called_once_with(["run", "--once"])
        window.assert_not_called()

    def test_a_running_background_runner_is_named_in_the_message(self):
        locks = iter([None, None])  # app.lock and daemon.lock are both held
        with mock.patch.object(app_module.tk, "Tk"), \
                mock.patch.object(app_module.store, "lock_file", side_effect=lambda path: next(locks)), \
                mock.patch.object(app_module.messagebox, "showerror") as shown, \
                mock.patch.object(app_module, "App") as started:
            self.assertEqual(app_module.main([]), 3)
        started.assert_not_called()
        self.assertIn("Chương trình nền", shown.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
