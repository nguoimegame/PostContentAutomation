import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import ai
import ai_cli
import history
import scheduler
import store
from tests.test_ai_features import FakeEngine

HI = "https://www.facebook.com/hienglish"
GROUP = "https://www.facebook.com/groups/jaecoo"


class CliTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        store.save_config(
            self.path("config.json"),
            {"pages": [{"name": "Hi English", "url": HI, "language": "English (Mỹ)", "voice": "vui vẻ"},
                       {"name": "Nhóm Jaecoo", "url": GROUP, "language": "Tiếng Việt (Việt Nam)"}],
             "templates": [{"name": "Chào", "content": "xin chào", "media_path": ""}], "page_gap_minutes": 4},
        )

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return self.path(name)

    def run_cli(self, engine, *argv, as_json=True):
        out, err = io.StringIO(), io.StringIO()
        args = (["--json"] if as_json else []) + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ai_cli.main(args, base_dir=self.dir, engine=engine)
        text = out.getvalue()
        return code, (json.loads(text) if as_json and text else text), err.getvalue()

    def rows(self, path):
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))


class DraftTest(CliTestCase):
    ANSWER = {"posts": [{"name": "Hi English", "content": "Hello!"}, {"name": "Nhóm Jaecoo", "content": "Chào cả nhà!"}]}

    def test_drafts_use_the_saved_languages_and_voices(self):
        engine = FakeEngine(self.ANSWER)
        code, data, _ = self.run_cli(engine, "draft", "Mẹo", "học", "từ", "--page", "Hi English", "--page", "nhóm jaecoo")
        self.assertEqual(code, 0)
        self.assertEqual([p["content"] for p in data["posts"]], ["Hello!", "Chào cả nhà!"])
        self.assertIn('language="English (Mỹ)", voice="vui vẻ"', engine.last["user"])
        self.assertIn("Mẹo học từ", engine.last["user"])

    def test_a_sheet_for_import_is_written_with_staggered_times(self):
        engine = FakeEngine(self.ANSWER)
        out = self.path("bai.csv")
        code, data, _ = self.run_cli(engine, "draft", "ý", "--page", "Hi English", "--page", "Nhóm Jaecoo",
                                     "--csv", out, "--at", "2026-10-20 08:30")
        self.assertEqual(code, 0)
        rows = self.rows(out)
        self.assertEqual([(r["content"], r["pages"], r["at"]) for r in rows],
                         [("Hello!", "Hi English", "2026-10-20 08:30"), ("Chào cả nhà!", "Nhóm Jaecoo", "2026-10-20 08:34")])
        self.assertEqual(list(rows[0]), list(__import__("enqueue").CSV_COLUMNS))

    def test_the_sheet_really_imports(self):
        import enqueue

        engine = FakeEngine(self.ANSWER)
        out = self.path("bai.csv")
        when = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d 08:30")
        self.run_cli(engine, "draft", "ý", "--page", "Hi English", "--page", "Nhóm Jaecoo", "--csv", out, "--at", when)
        with contextlib.redirect_stdout(io.StringIO()):
            code = enqueue.main(["--json", "import", out], base_dir=self.dir)
        self.assertEqual(code, 0)
        jobs = store.load_queue(self.path("queue.json"))
        self.assertEqual(sorted(job["content"] for job in jobs), ["Chào cả nhà!", "Hello!"])
        self.assertEqual({job["page_url"] for job in jobs}, {HI, GROUP})

    def test_a_sheet_needs_a_time_and_the_personal_profile_is_possible(self):
        code, data, _ = self.run_cli(FakeEngine(self.ANSWER), "draft", "ý", "--page", "Hi English", "--csv", self.path("x.csv"))
        self.assertEqual(code, 1)
        self.assertIn("--at", data["message"])
        engine = FakeEngine({"posts": [{"name": "Trang cá nhân", "content": "Của tôi"}]})
        out = self.path("x.csv")
        self.run_cli(engine, "draft", "ý", "--page", "personal", "--csv", out, "--at", "2026-10-20 08:30")
        self.assertEqual(self.rows(out)[0]["pages"], "personal")

    def test_unknown_pages_and_missing_pages_are_explained(self):
        code, data, _ = self.run_cli(FakeEngine(), "draft", "ý", "--page", "Chỗ lạ")
        self.assertEqual(code, 1)
        self.assertIn("Hi English", data["message"])
        code, data, _ = self.run_cli(FakeEngine(), "draft", "ý")
        self.assertEqual(code, 1)
        self.assertIn("--page", data["message"])

    def test_facts_can_come_from_a_file(self):
        engine = FakeEngine({"posts": [{"name": "Hi English", "content": "x"}]})
        facts = self.write("facts.txt", "Giá 99k, khai giảng 1/11")
        self.run_cli(engine, "draft", "ý", "--page", "Hi English", "--details-file", facts)
        self.assertIn("Giá 99k, khai giảng 1/11", engine.last["user"])

    def test_the_text_mode_points_to_the_next_step(self):
        out = self.path("bai.csv")
        code, text, _ = self.run_cli(FakeEngine(self.ANSWER), "draft", "ý", "--page", "Hi English", "--page", "Nhóm Jaecoo",
                                     "--csv", out, "--at", "2026-10-20 08:30", as_json=False)
        self.assertIn("Hello!", text)
        self.assertIn("python enqueue.py import", text)


class TextToolsTest(CliTestCase):
    def test_localize(self):
        engine = FakeEngine({"posts": [{"name": "Hi English", "content": "Hello"}]})
        code, data, _ = self.run_cli(engine, "localize", "Xin chào", "--page", "Hi English")
        self.assertEqual((code, data["posts"][0]["content"]), (0, "Hello"))

    def test_variants_polish_review_from_a_file(self):
        bai = self.write("bai.txt", "Nội dung\ngốc")
        code, data, _ = self.run_cli(FakeEngine({"variants": ["a", "b", "c"]}), "variants", "--content-file", bai, "--count", "2")
        self.assertEqual(data["variants"], ["a", "b"])
        engine = FakeEngine({"text": "Nội dung #tag", "notes": "thêm tag"})
        code, data, _ = self.run_cli(engine, "polish", "hashtags", "--content-file", bai)
        self.assertEqual(data["text"], "Nội dung #tag")
        self.assertIn("<post>\nNội dung\ngốc\n</post>", engine.last["user"])
        review = {"verdict": "ok", "summary": "tốt", "issues": [], "improved_text": "Nội dung gốc"}
        code, data, _ = self.run_cli(FakeEngine(review), "review", "--content-file", bai)
        self.assertEqual((code, data["verdict"]), (0, "ok"))

    def test_polish_only_accepts_the_known_operations(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli(FakeEngine(), "polish", "bay", "xin")

    def test_text_comes_from_the_argument_or_a_file_but_not_neither(self):
        code, data, _ = self.run_cli(FakeEngine(), "review")
        self.assertEqual(code, 1)
        self.assertIn("Thiếu nội dung", data["message"])
        code, data, _ = self.run_cli(FakeEngine(), "review", "--content-file", self.path("khong-co.txt"))
        self.assertEqual(code, 1)

    def test_text_mode_for_each_tool(self):
        self.write("bai.txt", "Bài")
        samples = [
            (["variants", "Bài"], {"variants": ["Bản một"]}, "Bản một"),
            (["polish", "fix", "Bài"], {"text": "Bài sửa", "notes": "đã sửa"}, "Bài sửa"),
            (["review", "Bài"], {"verdict": "fix", "summary": "tóm tắt", "improved_text": "Bản tốt hơn",
                                 "issues": [{"kind": "spelling", "quote": "Bài", "problem": "lỗi", "suggestion": "Bài viết"}]}, "Bản tốt hơn"),
            (["reply", "Giá", "bao", "nhiêu"], {"replies": [{"style": "ngắn", "text": "Dạ 99k ạ"}], "flags": ["spam?"]}, "Dạ 99k ạ"),
        ]
        for argv, answer, expected in samples:
            with self.subTest(argv=argv):
                code, text, _ = self.run_cli(FakeEngine(answer), *argv, as_json=False)
                self.assertEqual(code, 0)
                self.assertIn(expected, text)

    def test_the_cost_goes_to_stderr_in_text_mode(self):
        engine = FakeEngine({"variants": ["a"]})
        engine.describe_usage = lambda: "AI: 10 token vào, 5 token ra"
        code, text, err = self.run_cli(engine, "variants", "Bài", as_json=False)
        self.assertIn("AI: 10 token", err)
        self.assertNotIn("AI: 10 token", text)

    def test_the_usage_is_in_the_json(self):
        engine = FakeEngine({"variants": ["a"]})
        engine.last_usage = {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001}
        code, data, _ = self.run_cli(engine, "variants", "Bài")
        self.assertEqual(data["usage"]["output_tokens"], 5)

    def test_an_ai_error_is_a_clean_failure(self):
        code, data, _ = self.run_cli(FakeEngine(ai.AIError("mất mạng")), "variants", "Bài")
        self.assertEqual((code, data["ok"], data["message"]), (1, False, "mất mạng"))
        code, text, err = self.run_cli(FakeEngine(ai.AIError("mất mạng")), "variants", "Bài", as_json=False)
        self.assertEqual(code, 1)
        self.assertIn("mất mạng", err)


class SeriesTest(CliTestCase):
    POSTS = {"posts": [{"angle": "Mẹo", "content": f"Bài {i}"} for i in range(1, 5)]}

    def test_each_post_gets_a_time(self):
        code, data, _ = self.run_cli(FakeEngine(self.POSTS), "series", "Từ", "vựng", "--count", "4", "--page", "Hi English",
                                     "--start", "2026-10-20", "--time", "7:45", "--every", "2")
        self.assertEqual(code, 0)
        self.assertEqual([item["at"] for item in data["posts"]],
                         ["2026-10-20 07:45", "2026-10-22 07:45", "2026-10-24 07:45", "2026-10-26 07:45"])
        self.assertEqual({item["page"] for item in data["posts"]}, {"Hi English"})

    def test_chosen_weekdays(self):
        code, data, _ = self.run_cli(FakeEngine(self.POSTS), "series", "chủ đề", "--count", "4", "--page", "Hi English",
                                     "--start", "2026-10-20", "--days", "mon,wed")
        self.assertEqual([item["at"][:10] for item in data["posts"]], ["2026-10-21", "2026-10-26", "2026-10-28", "2026-11-02"])

    def test_the_series_can_be_written_for_import(self):
        out = self.path("chuoi.csv")
        self.run_cli(FakeEngine(self.POSTS), "series", "chủ đề", "--count", "4", "--page", "Hi English", "--start", "2026-10-20",
                     "--csv", out)
        rows = self.rows(out)
        self.assertEqual([r["content"] for r in rows], ["Bài 1", "Bài 2", "Bài 3", "Bài 4"])
        self.assertEqual({r["pages"] for r in rows}, {"Hi English"})
        self.assertEqual(rows[0]["at"], "2026-10-20 08:30")

    def test_bad_times_are_refused(self):
        for extra in (["--time", "8h"], ["--start", "mai"], ["--days", "funday"], ["--every", "0"]):
            with self.subTest(extra=extra):
                code, data, _ = self.run_cli(FakeEngine(self.POSTS), "series", "chủ đề", "--page", "Hi English", *extra)
                self.assertEqual(code, 1)


class TimesAndReplyTest(CliTestCase):
    def test_times_use_this_pages_history(self):
        history.append(self.path("history.jsonl"), {"status": "posted", "page_url": HI, "content": "x"}, datetime(2026, 10, 10, 9, 0))
        engine = FakeEngine({"suggestions": [{"weekday": "mon", "time": "08:00", "reason": "đi làm"}], "caveat": ""})
        code, data, _ = self.run_cli(engine, "times", "--page", "Hi English")
        self.assertEqual((code, data["basis"]), (0, "general"))
        self.assertIn("2026-10-10T09:00:00", engine.last["user"])

    def test_reply_reads_the_post_from_a_file(self):
        post = self.write("bai.txt", "Khóa học tiếng Anh")
        engine = FakeEngine({"replies": [{"style": "a", "text": "Dạ ok"}], "flags": []})
        code, data, _ = self.run_cli(engine, "reply", "Giá", "bao", "nhiêu", "--post-file", post)
        self.assertEqual(data["replies"][0]["text"], "Dạ ok")
        self.assertIn("Khóa học tiếng Anh", engine.last["user"])
        self.assertIn("Giá bao nhiêu", engine.last["user"])


class DescribeAndCardTest(CliTestCase):
    def test_an_image_is_sent_to_the_ai(self):
        image = self.write("anh.png", "x")
        engine = FakeEngine({"alt_text": "Một chiếc xe", "caption": "Xe mới"})
        code, data, _ = self.run_cli(engine, "describe", image, "--context", "bài về xe")
        self.assertEqual((code, data["alt_text"]), (0, "Một chiếc xe"))
        self.assertEqual(engine.last["images"], [image])

    def test_a_card_is_drawn_from_the_posts_text(self):
        bai = self.write("bai.txt", "5 mẹo học từ vựng")
        out = self.path("the.png")
        engine = FakeEngine({"headline": "5 mẹo", "subline": "", "points": ["a"], "footer": "#hi", "theme": "paper"})
        code, data, _ = self.run_cli(engine, "card", "--content-file", bai, "--out", out, "--size", "300", "--theme", "berry")
        self.assertEqual(code, 0)
        self.assertEqual(data["card"]["theme"], "berry")
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")


class ExplainTest(CliTestCase):
    ANSWER = {"summary": "Không thấy nút", "cause": "Facebook đổi giao diện", "steps": ["Chạy selftest"],
              "safe_to_retry": True, "check_facebook": False}

    def queue(self, *jobs):
        store.save_queue(self.path("queue.json"), list(jobs))

    def failed_job(self, message="Thất bại (mã thoát 4), xem nhật ký", run="2026-10-17 09:00"):
        job = scheduler.new_job("Bài", HI, "", datetime(2026, 10, 17, 9, 0))
        job.update(status="failed", last_message=message, last_run=run)
        return job

    def test_the_newest_failed_post_is_explained_with_the_log_and_screenshot(self):
        older = self.failed_job("Thất bại (mã thoát 1)", "2026-10-16 09:00")
        newer = self.failed_job("Thất bại (mã thoát 4), xem nhật ký", "2026-10-17 09:00")
        self.queue(older, newer)
        self.write("daemon.log", "\n".join(f"dòng {i}" for i in range(200)))
        os.makedirs(self.path("debug"))
        with open(self.path("debug/latest_post_button.png"), "wb") as handle:
            handle.write(b"x")
        engine = FakeEngine(dict(self.ANSWER))
        code, data, _ = self.run_cli(engine, "explain")
        self.assertEqual((code, data["job_id"]), (0, newer["id"]))
        user = engine.last["user"]
        self.assertIn("Exit code: 4", user)
        self.assertIn("dòng 199", user)
        self.assertNotIn("dòng 100\n", user)
        self.assertEqual([os.path.normpath(p) for p in engine.last["images"]], [os.path.normpath(self.path("debug/latest_post_button.png"))])

    def test_a_job_can_be_named(self):
        job = self.failed_job("Thất bại (mã thoát 1)")
        self.queue(job, self.failed_job("khác", "2026-10-18 09:00"))
        engine = FakeEngine(dict(self.ANSWER))
        code, data, _ = self.run_cli(engine, "explain", "--job", job["id"])
        self.assertEqual(data["job_id"], job["id"])
        code, data, _ = self.run_cli(FakeEngine(), "explain", "--job", "khong-co")
        self.assertEqual(code, 1)

    def test_without_the_queue_the_history_is_used(self):
        history.append(self.path("history.jsonl"), {"status": "failed", "message": "Hộp soạn bài vẫn mở"}, datetime(2026, 10, 17, 9, 0))
        engine = FakeEngine(dict(self.ANSWER))
        code, data, _ = self.run_cli(engine, "explain")
        self.assertEqual(code, 0)
        self.assertIn("Hộp soạn bài vẫn mở", engine.last["user"])

    def test_nothing_failed_nothing_to_explain(self):
        code, data, _ = self.run_cli(FakeEngine(), "explain")
        self.assertEqual(code, 1)
        self.assertIn("Không thấy bài lỗi", data["message"])


class SelectorsTest(CliTestCase):
    def test_suggestions_that_match_nothing_are_dropped(self):
        dom = self.write("dom.html", '<div role="dialog"><div role="textbox" aria-label="Viết gì đó" contenteditable="true"></div>'
                                     '<div role="button" aria-label="Đăng">Đăng</div></div>')
        engine = FakeEngine({"summary": "Đổi nhãn", "suggestions": [
            {"list": "TEXTBOX_SELECTORS", "value": 'div[aria-label="Viết gì đó"]', "reason": "đúng"},
            {"list": "TEXTBOX_SELECTORS", "value": "div.bia-ra", "reason": "bịa"},
            {"list": "PUBLISH_WORDS", "value": "Đăng", "reason": "đúng"}]})
        code, data, _ = self.run_cli(engine, "selectors", "--from-file", dom)
        self.assertEqual(code, 0)
        self.assertEqual([s["value"] for s in data["suggestions"]], ['div[aria-label="Viết gì đó"]', "Đăng"])
        self.assertEqual(data["dropped"], 1)

    def test_text_mode_says_nothing_is_changed_automatically(self):
        dom = self.write("dom.html", '<div role="button" aria-label="Đăng">Đăng</div>')
        engine = FakeEngine({"summary": "ok", "suggestions": [{"list": "PUBLISH_WORDS", "value": "Đăng", "reason": "r"}]})
        code, text, _ = self.run_cli(engine, "selectors", "--from-file", dom, as_json=False)
        self.assertIn("không tự sửa", text)


class AssistantTest(CliTestCase):
    def proposal(self):
        day = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return {"content": "Chào hè!", "pages": ["Hi English"], "at": f"{day} 08:00", "repeat": "none", "note": ""}

    def test_proposals_come_back_and_nothing_is_queued(self):
        engine = FakeEngine({"reply": "Đây là đề xuất", "questions": [], "proposals": [self.proposal()]})
        code, data, _ = self.run_cli(engine, "assistant", "đăng", "bài", "chào", "hè")
        self.assertEqual(code, 0)
        self.assertEqual(data["proposals"][0]["pages"], ["Hi English"])
        self.assertFalse(os.path.exists(self.path("queue.json")))
        self.assertIn("Hi English", engine.last["user"])
        self.assertIn("Chào", engine.last["user"])

    def test_the_conversation_is_kept_in_a_file_between_calls(self):
        history_file = self.path("chat.json")
        answer = {"reply": "Mấy giờ?", "questions": ["Mấy giờ?"], "proposals": []}
        self.run_cli(FakeEngine(answer), "assistant", "đăng bài", "--history-file", history_file)
        with open(history_file, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual([t["role"] for t in saved], ["user", "assistant"])
        engine = FakeEngine({"reply": "ok", "questions": [], "proposals": []})
        self.run_cli(engine, "assistant", "8 giờ sáng", "--history-file", history_file)
        self.assertEqual(engine.last["history"], saved)

    def test_proposals_can_be_written_for_import(self):
        out = self.path("de-xuat.csv")
        engine = FakeEngine({"reply": "ok", "questions": [], "proposals": [self.proposal()]})
        code, data, _ = self.run_cli(engine, "assistant", "đăng", "--csv", out)
        self.assertEqual(data["csv"], out)
        self.assertEqual([r["content"] for r in self.rows(out)], ["Chào hè!"])

    def test_no_csv_when_there_is_nothing_to_propose(self):
        out = self.path("de-xuat.csv")
        engine = FakeEngine({"reply": "Cần thêm thông tin", "questions": ["Lúc nào?"], "proposals": []})
        code, data, _ = self.run_cli(engine, "assistant", "đăng", "--csv", out)
        self.assertIsNone(data["csv"])
        self.assertFalse(os.path.exists(out))

    def test_a_damaged_history_file_is_reported(self):
        history_file = self.write("chat.json", "không phải json")
        code, data, _ = self.run_cli(FakeEngine(), "assistant", "đăng", "--history-file", history_file)
        self.assertEqual(code, 1)


class CheckTest(CliTestCase):
    def test_check(self):
        engine = FakeEngine({"reply": "OK"})
        engine.model = "claude-opus-5-5"
        code, data, _ = self.run_cli(engine, "check")
        self.assertEqual((code, data["reply"]), (0, "OK"))


if __name__ == "__main__":
    unittest.main()
