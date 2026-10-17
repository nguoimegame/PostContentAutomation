import os
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

import ai
import app as app_module
import scheduler
import store
from tests.test_ai_features import FakeEngine
from tests.test_app import AppTestCase

HI = "https://facebook.com/hienglishoffical"
GROUP = "https://facebook.com/groups/jaecoo"


class AITestCase(AppTestCase):
    def start_ai_app(self, *answers):
        app = self.start_app()
        app.add_page("Hi English", HI, "English (Mỹ)", "vui vẻ")
        app.add_page("Nhóm Jaecoo", GROUP, "Tiếng Việt (Việt Nam)")
        self.engine = FakeEngine(*answers)
        self.engine.model = "claude-opus-5-5"
        app.ai_factory = lambda: self.engine
        return app

    def wait(self, app):
        self.pump(lambda: not app._ai_busy)

    def tomorrow(self, hour=8, minute=0):
        return (datetime.now() + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)


class RunAITest(AITestCase):
    def test_the_answer_comes_back_on_the_ui_thread(self):
        app = self.start_ai_app({"reply": "xin chào"})
        seen = {}

        def done(result, error):
            seen["thread"], seen["result"], seen["error"] = threading.current_thread(), result, error

        app.run_ai(lambda engine: engine.ask("s", "u", {}), done)
        self.wait(app)
        self.assertIs(seen["thread"], threading.main_thread())
        self.assertEqual((seen["result"], seen["error"]), ({"reply": "xin chào"}, None))

    def test_an_ai_error_is_passed_on_and_logged(self):
        app = self.start_ai_app(ai.AIError("mất mạng"))
        seen = {}
        app.run_ai(lambda engine: engine.ask("s", "u", {}), lambda result, error: seen.update(result=result, error=error))
        self.wait(app)
        self.assertEqual((seen["result"], seen["error"]), (None, "mất mạng"))
        self.assertIn("✖ AI: mất mạng", self.log_text())

    def test_an_unexpected_error_is_reported_not_swallowed(self):
        app = self.start_ai_app()
        seen = {}
        app.run_ai(lambda engine: 1 / 0, lambda result, error: seen.update(error=error))
        self.wait(app)
        self.assertIn("ZeroDivisionError", seen["error"])
        self.assertFalse(app._ai_busy)

    def test_only_one_request_at_a_time(self):
        app = self.start_ai_app({"reply": "a"})
        gate = threading.Event()
        app.run_ai(lambda engine: gate.wait(10) and engine.ask("s", "u", {}), lambda result, error: None)
        with self.assertRaises(RuntimeError):
            app.run_ai(lambda engine: {}, lambda result, error: None)
        self.assertIn("Đang hỏi AI", app.status_var.get())
        gate.set()
        self.wait(app)
        self.assertNotIn("Đang hỏi AI", app.status_var.get())

    def test_the_tokens_and_cost_are_logged(self):
        app = self.start_ai_app({"reply": "a"})
        self.engine.describe_usage = lambda: "AI: 10 token vào, 5 token ra, khoảng $0.0001"
        app.run_ai(lambda engine: engine.ask("s", "u", {}), lambda result, error: None)
        self.wait(app)
        self.assertIn("AI: 10 token vào", self.log_text())


class FanpageVoiceTest(AITestCase):
    def test_the_voice_is_saved_only_when_given(self):
        app = self.start_app()
        app.add_page("Có giọng", HI, "English (Mỹ)", "  thân mật  ")
        app.add_page("Không giọng", GROUP)
        pages = {page["name"]: page for page in self.read_json("config.json")["pages"]}
        self.assertEqual(pages["Có giọng"]["voice"], "thân mật")
        self.assertNotIn("voice", pages["Không giọng"])

    def test_selecting_a_page_shows_its_voice_and_saving_keeps_it(self):
        app = self.start_ai_app()
        app.page_list.selection_set(0)
        app._on_page_list_selected()
        self.assertEqual(app.page_voice_var.get(), "vui vẻ")
        app.page_voice_var.set("trang trọng")
        app._on_save_page_clicked()
        self.assertEqual(app.settings["pages"][0]["voice"], "trang trọng")

    def test_targets_carry_language_and_voice(self):
        app = self.start_ai_app()
        targets = app._ai_targets([app_module.PERSONAL, "Hi English"])
        self.assertEqual(targets[0]["name"], app_module.PERSONAL)
        self.assertEqual((targets[1]["language"], targets[1]["voice"]), ("English (Mỹ)", "vui vẻ"))
        with self.assertRaises(ValueError):
            app._ai_targets(["Trang lạ"])


class WritingTest(AITestCase):
    ANSWER = {"posts": [{"name": "Hi English", "content": "Hello there!"}, {"name": "Nhóm Jaecoo", "content": "Chào cả nhà!"}]}

    def ask_for_drafts(self, app):
        app.select_targets(["Hi English", "Nhóm Jaecoo"])
        app.ai_idea_box.insert("1.0", "Chào hè")
        app.ai_length_var.set("Ngắn")
        app.ai_tone_var.set("vui")
        app._on_ai_write_clicked()
        self.wait(app)

    def test_drafts_fill_the_list_and_the_editor(self):
        app = self.start_ai_app(self.ANSWER)
        self.ask_for_drafts(app)
        self.assertEqual([app.ai_draft_table.set(i, "name") for i in app.ai_draft_table.get_children()], ["Hi English", "Nhóm Jaecoo"])
        self.assertEqual(app.ai_draft_box.get("1.0", "end-1c"), "Hello there!")
        self.assertIn("at most about 300", self.engine.last["user"])
        self.assertIn("Tone: vui", self.engine.last["user"])
        self.assertIn('voice="vui vẻ"', self.engine.last["user"])

    def test_the_writing_page_is_shown_after_localizing_from_the_menu(self):
        app = self.start_ai_app(self.ANSWER)
        app.select_targets(["Hi English", "Nhóm Jaecoo"])
        app.content_box.insert("1.0", "Xin chào")
        app._on_ai_menu("localize")
        self.wait(app)
        self.assertEqual(len(app.ai_drafts), 2)
        self.assertEqual(app.notebook.select(), str(app.ai_tab))

    def test_editing_a_draft_keeps_the_edit(self):
        app = self.start_ai_app(self.ANSWER)
        self.ask_for_drafts(app)
        app.ai_draft_box.delete("1.0", "end")
        app.ai_draft_box.insert("1.0", "Đã sửa tay")
        app._on_draft_edited()
        self.assertEqual(app.ai_drafts[0]["content"], "Đã sửa tay")
        self.assertIn("Đã sửa tay", app.ai_draft_table.set("0", "preview"))

    def test_a_draft_can_be_used_in_the_compose_tab(self):
        app = self.start_ai_app(self.ANSWER)
        self.ask_for_drafts(app)
        app.ai_draft_table.selection_set("1")
        app._on_use_draft_clicked()
        self.assertEqual(app._compose_content(), "Chào cả nhà!")
        self.assertEqual(app.selected_targets(), ["Nhóm Jaecoo"])
        self.assertEqual(app.notebook.index(app.notebook.select()), 0)

    def test_all_drafts_can_be_queued_each_to_its_own_page(self):
        app = self.start_ai_app(self.ANSWER)
        self.ask_for_drafts(app)
        when = self.tomorrow(9, 30)
        app.run_at_var.set(scheduler.format_run_at(when))
        with mock.patch.object(app_module.messagebox, "askyesno", return_value=True) as asked:
            app._on_queue_drafts_clicked()
        self.assertIn("đọc kỹ", asked.call_args.args[1])
        jobs = {job["page_url"]: job for job in app.jobs}
        self.assertEqual(jobs["https://facebook.com/hienglishoffical"]["content"], "Hello there!")
        self.assertEqual(jobs["https://facebook.com/groups/jaecoo"]["content"], "Chào cả nhà!")
        self.assertEqual(jobs["https://facebook.com/hienglishoffical"]["run_at"], scheduler.format_run_at(when))
        self.assertEqual(jobs["https://facebook.com/groups/jaecoo"]["run_at"], scheduler.format_run_at(when + timedelta(minutes=5)))

    def test_nothing_is_queued_when_the_user_says_no(self):
        app = self.start_ai_app(self.ANSWER)
        self.ask_for_drafts(app)
        app.run_at_var.set(scheduler.format_run_at(self.tomorrow()))
        with mock.patch.object(app_module.messagebox, "askyesno", return_value=False):
            app._on_queue_drafts_clicked()
        self.assertEqual(app.jobs, [])

    def test_missing_input_is_explained(self):
        app = self.start_ai_app()
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            app._on_ai_write_clicked()  # no idea typed
            app.select_targets([])
            app._on_ai_write_clicked()
            app._on_use_draft_clicked()
            app._on_queue_drafts_clicked()
        self.assertEqual(error.call_count, 4)
        self.assertEqual(self.engine.calls, [])

    def test_an_ai_failure_is_shown(self):
        app = self.start_ai_app(ai.AIError("mất mạng"))
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            app.select_targets(["Hi English"])
            app.ai_idea_box.insert("1.0", "ý")
            app._on_ai_write_clicked()
            self.wait(app)
        self.assertIn("mất mạng", error.call_args.args[1])


class MenuTest(AITestCase):
    def test_a_polish_replaces_the_text_and_can_be_undone(self):
        app = self.start_ai_app({"text": "Bài đã có #hashtag", "notes": "thêm hashtag"})
        app.content_box.insert("1.0", "Bài gốc")
        app._on_ai_menu("hashtags")
        self.wait(app)
        self.assertEqual(app._compose_content(), "Bài đã có #hashtag")
        self.assertIn("thêm hashtag", self.log_text())
        app.content_box.edit_undo()
        self.assertEqual(app._compose_content(), "Bài gốc")

    def test_every_menu_entry_is_wired(self):
        app = self.start_ai_app()
        menu = app.root.nametowidget(str(app.ai_menu_button["menu"]))
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
        self.assertEqual(labels, [label for operation, label in app_module.AI_MENU if operation])

    def test_nothing_is_asked_for_an_empty_post(self):
        app = self.start_ai_app()
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            for operation in ("fix", "review", "localize", "variants", "card", "describe"):
                app._on_ai_menu(operation)
        self.assertEqual(error.call_count, 6)
        self.assertEqual(self.engine.calls, [])

    def test_the_review_offers_the_improved_text(self):
        review = {"verdict": "fix", "summary": "Có lỗi", "improved_text": "Bản đã sửa",
                  "issues": [{"kind": "spelling", "quote": "sai", "problem": "chính tả", "suggestion": "sửa"}]}
        app = self.start_ai_app(review)
        app.content_box.insert("1.0", "Bản sai")
        with mock.patch.object(app, "_show_text_dialog") as shown:
            app._on_ai_menu("review")
            self.wait(app)
        title, text, actions = shown.call_args.args
        self.assertEqual(title, "Kiểm tra bài")
        self.assertIn("Nên sửa vài chỗ", text)
        self.assertIn("“sai”: chính tả", text)
        actions[0][1]()
        self.assertEqual(app._compose_content(), "Bản đã sửa")

    def test_no_offer_when_the_text_is_already_fine(self):
        app = self.start_ai_app({"verdict": "ok", "summary": "tốt", "improved_text": "Bài", "issues": []})
        app.content_box.insert("1.0", "Bài")
        with mock.patch.object(app, "_show_text_dialog") as shown:
            app._on_ai_menu("review")
            self.wait(app)
        self.assertEqual(shown.call_args.args[2], [])

    def test_a_variant_can_be_picked(self):
        app = self.start_ai_app({"variants": ["Bản một", "Bản hai", "Bản ba"]})
        app.content_box.insert("1.0", "Bài gốc")
        app._on_ai_menu("variants")
        self.wait(app)
        self.addCleanup(lambda: app.variant_dialog.winfo_exists() and app.variant_dialog.destroy())
        self.assertEqual(app.variant_list.size(), 3)
        app.variant_list.selection_set(1)
        app.variant_list.event_generate("<<ListboxSelect>>")
        buttons = [b for b in app.variant_dialog.winfo_children()[-1].winfo_children() if b.cget("text") == "Dùng bản này"]
        buttons[0].invoke()
        self.assertEqual(app._compose_content(), "Bản hai")
        self.assertFalse(app.variant_dialog.winfo_exists())

    def test_a_picture_is_described_and_the_caption_can_be_added(self):
        self.write("anh.png", "x")
        image = os.path.join(self.dir, "anh.png")
        app = self.start_ai_app({"alt_text": "Một chiếc xe", "caption": "Xe mới về!"})
        app.media_var.set(f"{image}|{os.path.join(self.dir, 'khac.png')}")
        app.content_box.insert("1.0", "Bài")
        with mock.patch.object(app, "_show_text_dialog") as shown:
            app._on_ai_menu("describe")
            self.wait(app)
        self.assertEqual(self.engine.last["images"], [image])
        self.assertIn("Một chiếc xe", app.picture_result_var.get())
        shown.call_args.args[2][0][1]()
        self.assertEqual(app._compose_content(), "Bài\n\nXe mới về!")

    def test_describing_needs_a_picture(self):
        app = self.start_ai_app()
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            app._on_ai_menu("describe")
        self.assertIn("chọn một ảnh", error.call_args.args[1])

    def test_a_card_is_drawn_and_attached(self):
        app = self.start_ai_app({"headline": "5 mẹo", "subline": "", "points": [], "footer": "", "theme": "ocean"})
        app.media_var.set("a.png")
        app.content_box.insert("1.0", "5 mẹo học từ vựng")
        import cards

        drawn = {}

        def fake_render(spec, out_path, size=1080, browser=None):
            drawn["spec"], drawn["path"] = spec, out_path
            return out_path

        with mock.patch.object(cards, "render_card", fake_render):
            app._on_ai_menu("card")
            self.wait(app)
        self.assertEqual(drawn["spec"]["headline"], "5 mẹo")
        self.assertTrue(drawn["path"].startswith(os.path.join(self.dir, "cards")))
        self.assertEqual(app.media_var.get(), f"a.png|{drawn['path']}")


class SeriesTest(AITestCase):
    POSTS = {"posts": [{"angle": "Mẹo", "content": f"Bài {i}"} for i in (1, 2, 3)]}

    def plan(self, app, **fields):
        for key, value in {"topic": "Từ vựng", "count": "3", "page": "Hi English", "start": self.tomorrow().strftime("%Y-%m-%d"),
                           "time": "8:30", "every": "2", **fields}.items():
            app.series_vars[key].set(value)
        app._on_ai_series_clicked()
        self.wait(app)

    def test_the_series_gets_times_from_the_settings_not_from_the_ai(self):
        app = self.start_ai_app(self.POSTS)
        self.plan(app)
        rows = [app.series_table.item(i, "values") for i in app.series_table.get_children()]
        first = self.tomorrow(8, 30)
        self.assertEqual([row[0] for row in rows],
                         [scheduler.format_run_at(first + timedelta(days=2 * n)) for n in range(3)])
        self.assertEqual(len(app.series_table.selection()), 3)
        self.assertIn("series of 3", self.engine.last["user"])

    def test_the_chosen_posts_are_queued_at_their_times(self):
        app = self.start_ai_app(self.POSTS)
        self.plan(app)
        with mock.patch.object(app_module.messagebox, "askyesno", return_value=True):
            app.series_table.selection_set("0", "2")
            app._on_queue_series_clicked()
        self.assertEqual([job["content"] for job in app.jobs], ["Bài 1", "Bài 3"])
        self.assertEqual({job["page_url"] for job in app.jobs}, {"https://facebook.com/hienglishoffical"})

    def test_a_post_can_be_edited_before_queueing(self):
        app = self.start_ai_app(self.POSTS)
        self.plan(app)
        app.series_table.focus("1")
        app._on_series_selected()
        app.series_box.delete("1.0", "end")
        app.series_box.insert("1.0", "Bài sửa tay")
        app._on_series_edited()
        jobs, skipped = app.queue_series([1])
        self.assertEqual((jobs[0]["content"], skipped), ("Bài sửa tay", 0))

    def test_posts_whose_time_has_passed_are_skipped(self):
        app = self.start_ai_app()
        app.series_posts = [{"at": datetime.now() - timedelta(hours=1), "angle": "a", "content": "Cũ", "name": "Hi English"},
                            {"at": datetime.now() + timedelta(hours=1), "angle": "b", "content": "Mới", "name": app_module.PERSONAL}]
        jobs, skipped = app.queue_series([0, 1])
        self.assertEqual(([job["content"] for job in jobs], skipped), (["Mới"], 1))
        self.assertEqual(jobs[0]["page_url"], "")

    def test_bad_settings_are_refused_before_asking(self):
        app = self.start_ai_app()
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            for fields in ({"count": "nhiều"}, {"start": "mai"}, {"time": "8h"}, {"every": "0"}):
                self.plan_no_wait(app, **fields)
        self.assertEqual(error.call_count, 4)
        self.assertEqual(self.engine.calls, [])

    def plan_no_wait(self, app, **fields):
        for key, value in {"topic": "x", "count": "3", "page": "Hi English", "start": "2030-01-01", "time": "08:30",
                           "every": "1", **fields}.items():
            app.series_vars[key].set(value)
        app._on_ai_series_clicked()

    def test_time_suggestions_are_shown_with_their_caveat(self):
        app = self.start_ai_app({"suggestions": [{"weekday": "mon", "time": "08:00", "reason": "đi làm"}], "caveat": "Chỉ là gợi ý chung."})
        app.series_vars["page"].set("Hi English")
        app._on_ai_times_clicked()
        self.wait(app)
        self.assertIn("mon 08:00: đi làm", app.series_hint_var.get())
        self.assertIn("Chỉ là gợi ý chung.", app.series_hint_var.get())

    def test_the_pages_offered_follow_the_saved_pages(self):
        app = self.start_ai_app()
        self.assertEqual(list(app.series_page_box["values"]), [app_module.PERSONAL, "Hi English", "Nhóm Jaecoo"])
        app.remove_page("Hi English")
        self.assertEqual(list(app.series_page_box["values"]), [app_module.PERSONAL, "Nhóm Jaecoo"])


class AssistantTest(AITestCase):
    def proposal(self, **extra):
        item = {"content": "Chào hè!", "pages": ["Hi English", "Nhóm Jaecoo"], "at": self.tomorrow().strftime("%Y-%m-%d %H:%M"),
                "repeat": "weekdays", "note": ""}
        item.update(extra)
        return item

    def say(self, app, message, answer):
        self.engine.answers.append(answer)
        app.assistant_var.set(message)
        app._on_assistant_send_clicked()
        self.wait(app)

    def text(self, app):
        return app.assistant_log.get("1.0", "end")

    def test_the_conversation_and_the_proposals_are_shown(self):
        app = self.start_ai_app()
        self.say(app, "đăng bài chào hè", {"reply": "Đây là đề xuất", "questions": ["Có kèm ảnh không?"], "proposals": [self.proposal()]})
        text = self.text(app)
        self.assertIn("Bạn: đăng bài chào hè", text)
        self.assertIn("Trợ lý: Đây là đề xuất", text)
        self.assertIn("? Có kèm ảnh không?", text)
        (row,) = [app.proposal_table.item(i, "values") for i in app.proposal_table.get_children()]
        self.assertEqual((row[1], row[2]), ("Hi English, Nhóm Jaecoo", "Thứ 2 đến Thứ 6"))
        self.assertEqual(app.jobs, [])
        self.assertEqual(app.assistant_var.get(), "")

    def test_the_next_turn_carries_the_conversation(self):
        app = self.start_ai_app()
        self.say(app, "một", {"reply": "a", "questions": [], "proposals": []})
        self.say(app, "hai", {"reply": "b", "questions": [], "proposals": []})
        self.assertEqual([turn["content"] for turn in self.engine.last["history"]][0], "một")
        self.assertEqual(len(self.engine.last["history"]), 2)

    def test_confirming_queues_the_posts_on_their_pages_with_the_repeat(self):
        app = self.start_ai_app()
        self.say(app, "đăng", {"reply": "ok", "questions": [], "proposals": [self.proposal()]})
        with mock.patch.object(app_module.messagebox, "askyesno", return_value=True):
            app._on_apply_proposals_clicked()
        self.assertEqual(sorted(job["page_url"] for job in app.jobs), [GROUP, HI])
        self.assertEqual({job["repeat"] for job in app.jobs}, {"weekdays"})
        by_page = {job["page_url"]: job["run_at"] for job in app.jobs}
        start = self.tomorrow()
        self.assertEqual(by_page[HI], scheduler.format_run_at(start))
        self.assertEqual(by_page[GROUP], scheduler.format_run_at(start + timedelta(minutes=5)))
        self.assertEqual(app.proposal_table.get_children(), ())
        self.assertIn("Đã thêm 2 bài", self.text(app))

    def test_declining_queues_nothing(self):
        app = self.start_ai_app()
        self.say(app, "đăng", {"reply": "ok", "questions": [], "proposals": [self.proposal()]})
        with mock.patch.object(app_module.messagebox, "askyesno", return_value=False):
            app._on_apply_proposals_clicked()
        self.assertEqual(app.jobs, [])

    def test_only_the_selected_proposals_are_queued(self):
        app = self.start_ai_app()
        self.say(app, "đăng", {"reply": "ok", "questions": [], "proposals": [self.proposal(content="Một", pages=["Hi English"]),
                                                                              self.proposal(content="Hai", pages=["Hi English"])]})
        app.proposal_table.selection_set("1")
        with mock.patch.object(app_module.messagebox, "askyesno", return_value=True):
            app._on_apply_proposals_clicked()
        self.assertEqual([job["content"] for job in app.jobs], ["Hai"])

    def test_an_error_goes_to_the_conversation(self):
        app = self.start_ai_app()
        self.say(app, "đăng", ai.AIError("mất mạng"))
        self.assertIn("Lỗi: mất mạng", self.text(app))
        self.assertEqual(app.assistant_history, [])

    def test_starting_over(self):
        app = self.start_ai_app()
        self.say(app, "đăng", {"reply": "ok", "questions": [], "proposals": [self.proposal()]})
        app._on_assistant_reset_clicked()
        self.assertEqual((app.assistant_history, app.assistant_proposals, self.text(app).strip()), ([], [], ""))
        self.assertEqual(app.proposal_table.get_children(), ())

    def test_pages_and_the_clock_are_given_to_the_assistant(self):
        app = self.start_ai_app()
        self.say(app, "đăng", {"reply": "ok", "questions": [], "proposals": []})
        self.assertIn("name=\"Hi English\", language=\"English (Mỹ)\", voice=\"vui vẻ\"", self.engine.last["user"])
        self.assertIn(app_module.PERSONAL, self.engine.last["user"])
        self.assertIn(f"Now: {datetime.now():%Y-%m-%d}", self.engine.last["user"])

    def test_an_empty_message_does_nothing(self):
        app = self.start_ai_app()
        app.assistant_var.set("   ")
        app._on_assistant_send_clicked()
        self.assertEqual(self.engine.calls, [])


class ToolsTest(AITestCase):
    def test_replies_are_listed_and_can_be_copied(self):
        app = self.start_ai_app({"replies": [{"style": "ngắn", "text": "Dạ 99k ạ"}, {"style": "dài", "text": "Cảm ơn bạn đã hỏi"}],
                                 "flags": ["Có vẻ là spam"]})
        app.reply_comment_box.insert("1.0", "Giá bao nhiêu?")
        app.content_box.insert("1.0", "Bài về khóa học")
        app._on_ai_reply_clicked()
        self.wait(app)
        self.assertEqual(app.reply_list.get(0, "end"), ("(ngắn) Dạ 99k ạ", "(dài) Cảm ơn bạn đã hỏi"))
        self.assertIn("Có vẻ là spam", app.reply_flags_var.get())
        self.assertIn("Bài về khóa học", self.engine.last["user"])
        app.reply_list.selection_set(1)
        app._on_copy_reply_clicked()
        self.assertEqual(app.root.clipboard_get(), "Cảm ơn bạn đã hỏi")

    def test_copying_needs_a_choice(self):
        app = self.start_ai_app()
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            app._on_copy_reply_clicked()
        error.assert_called_once()

    def test_a_failed_post_is_explained_with_the_log_and_the_newest_screenshot(self):
        answer = {"summary": "Facebook đổi giao diện", "cause": "Không thấy nút", "steps": ["Chạy selftest"],
                  "safe_to_retry": True, "check_facebook": False}
        app = self.start_ai_app(answer)
        os.makedirs(os.path.join(self.dir, "debug"))
        shot = os.path.join(self.dir, "debug", "latest_post_button.png")
        with open(shot, "wb") as handle:
            handle.write(b"x")
        job = app.enqueue("Bài", "", "", self.tomorrow(), "none")
        job.update(status="failed", last_message="Thất bại (mã thoát 4), xem nhật ký")
        app.log("Dang mo trinh duyet...")
        app.queue_table.selection_set(job["id"])
        with mock.patch.object(app, "_show_text_dialog") as shown:
            app._on_explain_clicked()
            self.wait(app)
        user = self.engine.last["user"]
        self.assertIn("Exit code: 4", user)
        self.assertIn("Dang mo trinh duyet", user)
        self.assertEqual(self.engine.last["images"], [shot])
        title, text = shown.call_args.args[:2]
        self.assertEqual(title, "Vì sao bài này lỗi?")
        self.assertIn("Facebook đổi giao diện", text)
        self.assertIn("1. Chạy selftest", text)
        self.assertIn("Có thể thử lại", text)

    def test_a_post_without_a_message_has_nothing_to_explain(self):
        app = self.start_ai_app()
        job = app.enqueue("Bài", "", "", self.tomorrow(), "none")
        app.queue_table.selection_set(job["id"])
        with mock.patch.object(app_module.messagebox, "showinfo") as info:
            app._on_explain_clicked()
        info.assert_called_once()
        self.assertEqual(self.engine.calls, [])

    def test_the_explanation_text_warns_about_a_post_that_may_be_live(self):
        text = app_module.format_explanation(
            {"summary": "s", "cause": "c", "steps": ["a", "b"], "safe_to_retry": False, "check_facebook": True}, "ghi chú")
        self.assertIn("Đừng đăng lại ngay", text)
        self.assertIn("kiểm tra xem bài đã đăng chưa", text)
        self.assertIn("(Ghi chú của bài: ghi chú)", text)

    def test_the_dialog_is_a_real_window_with_buttons(self):
        app = self.start_app()
        called = []
        dialog = app._show_text_dialog("Tiêu đề", "Nội dung dài", [("Làm gì đó", lambda: called.append(1))])
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        texts = [w.cget("text") for w in dialog.winfo_children()[1].winfo_children()]
        self.assertEqual(texts, ["Làm gì đó", "Đóng"])
        dialog.winfo_children()[1].winfo_children()[0].invoke()
        self.assertEqual(called, [1])
        self.assertFalse(dialog.winfo_exists())


class AISettingsTest(AITestCase):
    def values(self, **extra):
        values = {"api_key": " sk-thử ", "model": "claude-sonnet-5-5", "effort": "high", "fallbacks": False, "voice": " thân thiện ",
                  "explain_failures": True}
        values.update(extra)
        return values

    def test_settings_are_saved_where_ai_py_reads_them(self):
        app = self.start_app()
        app.set_ai_settings(self.values())
        saved = store.load_config(os.path.join(self.dir, "config.json"))["ai"]
        self.assertEqual(saved, {"api_key": "sk-thử", "model": "claude-sonnet-5-5", "effort": "high", "fallbacks": False,
                                 "voice": "thân thiện", "explain_failures": True})
        engine = ai.AI(store.load_config(os.path.join(self.dir, "config.json")))
        self.assertEqual((engine.model, engine.voice), ("claude-sonnet-5-5", "thân thiện"))

    def test_the_boxes_show_the_saved_values(self):
        app = self.start_app()
        app.set_ai_settings(self.values())
        app._sync_advanced_widgets()
        self.assertEqual(app.ai_vars["model"].get(), "claude-sonnet-5-5")
        self.assertFalse(app.ai_vars["fallbacks"].get())
        self.assertEqual(app.ai_voice_box.get("1.0", "end-1c"), "thân thiện")

    def test_defaults_are_shown_at_first(self):
        app = self.start_app()
        self.assertEqual((app.ai_vars["model"].get(), app.ai_vars["effort"].get()), ("claude-opus-5-5", "medium"))
        self.assertTrue(app.ai_vars["fallbacks"].get())
        self.assertFalse(app.ai_vars["explain_failures"].get())

    def test_bad_values_are_refused(self):
        app = self.start_app()
        for bad in ({"effort": "siêu"}, {"model": "  "}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.set_ai_settings(self.values(**bad))
        self.assertEqual(app.settings["ai"]["model"], "claude-opus-5-5")

    def test_the_connection_test_asks_for_one_small_answer(self):
        app = self.start_ai_app({"reply": "OK"})
        app.ai_vars["model"].set("claude-opus-5-5")
        app.ai_vars["effort"].set("medium")
        app._on_test_ai_clicked()
        self.wait(app)
        self.assertIn("✔ Kết nối được với claude-opus-5-5", self.log_text())

    def test_a_failed_connection_test_is_shown(self):
        app = self.start_ai_app(ai.AIError("Khóa API sai"))
        with mock.patch.object(app_module.messagebox, "showerror") as error:
            app._on_test_ai_clicked()
            self.wait(app)
        self.assertIn("Khóa API sai", error.call_args.args[1])

    def test_the_tab_and_its_settings_page_exist(self):
        app = self.start_app()
        tabs = [app.notebook.tab(tab, "text").strip() for tab in app.notebook.tabs()]
        self.assertIn("Trợ lý AI", tabs)
        self.assertEqual([app.ai_inner.tab(tab, "text").strip() for tab in app.ai_inner.tabs()],
                         ["Soạn bài", "Chuỗi bài", "Trợ lý hẹn giờ", "Công cụ"])


if __name__ == "__main__":
    unittest.main()
