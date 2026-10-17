import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import history
import notify
import spam
import store
import variants

NOW = datetime(2026, 10, 17, 9, 0)
PAGE = "https://www.facebook.com/hienglish"
OTHER = "https://www.facebook.com/groups/jaecoo"


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, name):
        return os.path.join(self.dir, name)


class HistoryTest(TempDirTestCase):
    def test_records_come_back_in_order_with_a_timestamp(self):
        path = self.path("history.jsonl")
        history.append(path, {"status": "posted", "content": "một"}, NOW)
        history.append(path, {"status": "failed", "content": "hai"}, NOW + timedelta(minutes=1))
        records = history.load(path)
        self.assertEqual([r["content"] for r in records], ["một", "hai"])
        self.assertEqual(records[0]["ts"], "2026-10-17T09:00:00")

    def test_a_missing_file_is_an_empty_history(self):
        self.assertEqual(history.load(self.path("khong-co.jsonl")), [])

    def test_damaged_lines_are_skipped(self):
        path = self.path("history.jsonl")
        history.append(path, {"status": "posted"}, NOW)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('không phải json\n{"khong": "co ts"}\n')
        history.append(path, {"status": "failed"}, NOW)
        self.assertEqual([r["status"] for r in history.load(path)], ["posted", "failed"])

    def test_vietnamese_text_is_stored_readable(self):
        path = self.path("history.jsonl")
        history.append(path, {"content": "Đăng bài"}, NOW)
        with open(path, encoding="utf-8") as handle:
            self.assertIn("Đăng bài", handle.read())

    def test_an_unwritable_file_does_not_raise(self):
        self.assertFalse(history.append(self.path("khong-co-thu-muc/history.jsonl"), {"status": "posted"}))


def posted(minutes_ago, content, page=PAGE, status="posted"):
    return {
        "ts": history.now_text(NOW - timedelta(minutes=minutes_ago)),
        "status": status,
        "content": content,
        "page_url": page,
    }


class SpamTest(unittest.TestCase):
    def codes(self, content, records, page=PAGE, **kwargs):
        settings = kwargs.pop("settings", None)
        return [p.code for p in spam.check(content, page, records, NOW, settings, **kwargs)]

    def test_nothing_in_the_history_is_fine(self):
        self.assertEqual(self.codes("xin chào", []), [])

    def test_the_same_text_on_the_same_page_is_a_duplicate(self):
        self.assertEqual(self.codes("Xin chào cả nhà", [posted(60, "xin  chào cả nhà")]), ["duplicate"])

    def test_a_slightly_edited_text_is_still_a_duplicate(self):
        old = "Hôm nay Hi English chia sẻ ba mẹo học từ vựng rất hay cho mọi người nhé"
        new = "Hôm nay Hi English chia sẻ ba mẹo học từ vựng rất hay cho mọi người nha"
        self.assertEqual(self.codes(new, [posted(60, old)]), ["duplicate"])

    def test_a_different_text_is_fine(self):
        self.assertEqual(self.codes("Chủ đề mới hoàn toàn về ngữ pháp", [posted(60, "Ba mẹo học từ vựng")]), [])

    def test_the_same_text_on_another_page_is_fine(self):
        self.assertEqual(self.codes("xin chào", [posted(60, "xin chào", page=OTHER)]), [])

    def test_an_old_post_no_longer_counts(self):
        self.assertEqual(self.codes("xin chào", [posted(60 * 24 * 8, "xin chào")]), [])

    def test_failed_posts_do_not_count(self):
        self.assertEqual(self.codes("xin chào", [posted(60, "xin chào", status="failed")]), [])

    def test_a_repeating_post_can_skip_the_duplicate_check(self):
        self.assertEqual(self.codes("xin chào", [posted(60, "xin chào")], allow_duplicate=True), [])

    def test_a_post_with_only_media_has_no_text_to_compare(self):
        self.assertEqual(self.codes("", [posted(60, "")]), [])

    def test_the_daily_limit_per_page(self):
        records = [posted(30 * i, f"bài số {i} nói về chủ đề khác nhau {i * 7}") for i in range(1, 4)]
        self.assertEqual(self.codes("nội dung mới khác hẳn", records), ["page_limit"])
        self.assertEqual(self.codes("nội dung mới khác hẳn", records, page=OTHER), [])

    def test_the_daily_limit_over_all_pages(self):
        records = [
            posted(10 * i, f"chủ đề {i} hoàn toàn riêng biệt {i * 11}", page=f"https://www.facebook.com/p{i}")
            for i in range(1, 4)
        ]
        self.assertEqual(self.codes("nội dung mới", records, settings={"max_per_day": 3}), ["daily_limit"])

    def test_posts_older_than_a_day_are_not_counted_in_the_limit(self):
        records = [posted(60 * 25 + i, f"chủ đề {i} hoàn toàn riêng biệt {i * 11}") for i in range(5)]
        self.assertEqual(self.codes("nội dung mới", records), [])

    def test_zero_turns_a_check_off(self):
        records = [posted(30 * i, f"bài số {i} khác nhau {i * 7}") for i in range(1, 5)]
        settings = {"max_per_page_per_day": 0, "max_per_day": 0}
        self.assertEqual(self.codes("nội dung mới", records, settings=settings), [])

    def test_settings_come_from_the_config_with_defaults(self):
        self.assertEqual(spam.settings_from({"spam": {"max_per_day": 2}})["similarity"], 0.9)
        self.assertEqual(spam.settings_from({"spam": {"max_per_day": 2}})["max_per_day"], 2)


class VariantsTest(unittest.TestCase):
    MOMENT = datetime(2026, 10, 19, 8, 30)  # a Monday

    def test_placeholders_are_filled_in(self):
        text = variants.render("{page} ({language}) - {date} {time} - {weekday}", "Hi English", "English (Mỹ)", self.MOMENT)
        self.assertEqual(text, "Hi English (English (Mỹ)) - 19/10/2026 08:30 - Thứ hai")

    def test_vietnamese_placeholder_names(self):
        self.assertEqual(
            variants.render("Chào {tên_trang}, {ngày}", "Hi English", "", self.MOMENT), "Chào Hi English, 19/10/2026"
        )

    def test_unknown_braces_are_left_alone(self):
        self.assertEqual(variants.render("giá {price} và {x}", "A", "", self.MOMENT), "giá {price} và {x}")

    def test_one_option_is_chosen(self):
        rng = random.Random(1)
        results = {variants.render("{Xin chào|Chào bạn|Hello}!", rng=rng) for _ in range(40)}
        self.assertEqual(results, {"Xin chào!", "Chào bạn!", "Hello!"})

    def test_the_choice_follows_the_random_generator(self):
        first = variants.render("{a|b|c|d|e}", rng=random.Random(5))
        self.assertEqual(first, variants.render("{a|b|c|d|e}", rng=random.Random(5)))

    def test_text_without_markup_is_unchanged(self):
        text = "Không có gì đặc biệt: 100% miễn phí."
        self.assertEqual(variants.render(text, "A", "B", self.MOMENT), text)
        self.assertFalse(variants.has_markup(text))
        self.assertTrue(variants.has_markup("chào {page}"))
        self.assertTrue(variants.has_markup("{a|b}"))

    def test_versions_take_turns(self):
        self.assertEqual([variants.pick(["a", "b"], i) for i in range(5)], ["a", "b", "a", "b", "a"])


class StoreHelpersTest(TempDirTestCase):
    def test_default_account_uses_state_json(self):
        self.assertEqual(store.state_path_for(self.dir), self.path("state.json"))
        self.assertEqual(store.state_path_for(self.dir, "default"), self.path("state.json"))

    def test_named_account_has_its_own_file(self):
        self.assertEqual(store.state_path_for(self.dir, "shop-2"), self.path("state.shop-2.json"))

    def test_account_names_cannot_escape_the_folder(self):
        for name in ("../x", "a/b", "a b", "x" * 41):
            with self.assertRaises(ValueError):
                store.state_path_for(self.dir, name)

    def test_media_lists(self):
        self.assertEqual(store.split_media("a.jpg|b.mp4"), ["a.jpg", "b.mp4"])
        self.assertEqual(store.split_media(""), [])
        self.assertEqual(store.split_media(["a", " ", "b"]), ["a", "b"])
        self.assertEqual(store.join_media(["a", "b"]), "a|b")

    def test_partial_nested_settings_keep_their_defaults(self):
        store.save_json(
            self.path("config.json"),
            {"spam": {"max_per_day": 4}, "notifications": {"telegram": {"chat_id": "7"}}},
        )
        config = store.load_config(self.path("config.json"))
        self.assertEqual(
            config["spam"], {"duplicate_days": 7, "similarity": 0.9, "max_per_day": 4, "max_per_page_per_day": 3}
        )
        self.assertEqual(config["notifications"]["telegram"], {"bot_token": "", "chat_id": "7"})
        self.assertEqual(config["notifications"]["events"], ["done", "failed", "session_expired"])

    def test_wrongly_typed_settings_fall_back(self):
        store.save_json(
            self.path("config.json"),
            {"spam": {"max_per_day": "nhiều"}, "retry_attempts": True, "grace_minutes": -3, "accounts": ["ok", "x y", 5]},
        )
        config = store.load_config(self.path("config.json"))
        self.assertEqual(config["spam"]["max_per_day"], 15)
        self.assertEqual(config["retry_attempts"], 2)
        self.assertEqual(config["grace_minutes"], 5)
        self.assertEqual(config["accounts"], ["ok"])

    def test_a_whole_number_is_accepted_for_the_similarity(self):
        store.save_json(self.path("config.json"), {"spam": {"similarity": 1}})
        self.assertEqual(store.load_config(self.path("config.json"))["spam"]["similarity"], 1)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class NotifyTest(unittest.TestCase):
    def config(self, **extra):
        notifications = {
            "events": ["done", "failed"],
            "telegram": {"bot_token": "T", "chat_id": "42"},
            "webhook_url": "",
            "email": {},
        }
        notifications.update(extra)
        return {"notifications": notifications}

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NOTIFY_WEBHOOK_URL", "SMTP_PASSWORD"):
            os.environ.pop(name, None)

    def test_no_channel_means_nothing_is_sent(self):
        self.assertEqual(notify.notify({}, "done", "x", senders={}), [])
        self.assertEqual(notify.channels(notify.settings_from({})), [])

    def test_an_event_that_is_not_wanted_is_not_sent(self):
        sent = []
        senders = {"telegram": lambda *args: sent.append(args)}
        self.assertEqual(notify.notify(self.config(), "missed", "x", senders=senders), [])
        self.assertEqual(sent, [])

    def test_sends_to_every_configured_channel(self):
        sent = []
        senders = {name: (lambda s, e, t, j, name=name: sent.append((name, e, t))) for name in notify.SENDERS}
        config = self.config(webhook_url="https://hook.example/x", email={"host": "smtp.example", "to": "a@b.c"})
        results = notify.notify(config, "done", "xong", {"id": "1", "content": "c"}, senders=senders)
        self.assertEqual(sorted(name for name, _ in results), ["email", "telegram", "webhook"])
        self.assertTrue(all(error is None for _, error in results))
        self.assertEqual(len(sent), 3)

    def test_one_failing_channel_does_not_stop_the_others_or_raise(self):
        def broken(*args):
            raise OSError("mất mạng")

        sent = []
        senders = {"telegram": broken, "webhook": lambda *a: sent.append(1)}
        results = notify.notify(self.config(webhook_url="https://hook.example/x"), "done", "x", senders=senders)
        self.assertIsNone(dict(results)["webhook"])
        self.assertIn("mất mạng", dict(results)["telegram"])
        self.assertEqual(sent, [1])

    def test_a_test_message_is_always_sent(self):
        sent = []
        notify.notify(self.config(events=[]), "test", "thử", senders={"telegram": lambda *a: sent.append(1)})
        self.assertEqual(sent, [1])

    def test_secrets_can_come_from_the_environment(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "ENV"
        os.environ["TELEGRAM_CHAT_ID"] = "9"
        settings = notify.settings_from({})
        self.assertEqual((settings["telegram"]["bot_token"], settings["telegram"]["chat_id"]), ("ENV", "9"))
        self.assertEqual(notify.channels(settings), ["telegram"])

    def test_telegram_request(self):
        seen = {}

        def opener(request, timeout):
            seen["url"], seen["data"] = request.full_url, request.data.decode("utf-8")
            return FakeResponse()

        notify.send_telegram(notify.settings_from(self.config()), "done", "xong ✅", None, opener=opener)
        self.assertEqual(seen["url"], "https://api.telegram.org/botT/sendMessage")
        self.assertIn("chat_id=42", seen["data"])

    def test_webhook_request_carries_the_job(self):
        seen = {}

        def opener(request, timeout):
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        settings = notify.settings_from(self.config(webhook_url="https://hook.example/x"))
        notify.send_webhook(settings, "failed", "lỗi", {"id": "1"}, opener=opener)
        self.assertEqual(seen["body"], {"event": "failed", "text": "lỗi", "job": {"id": "1"}})

    def test_email_is_sent_through_smtp(self):
        calls = []

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                calls.append(("connect", host, port))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def starttls(self):
                calls.append(("starttls",))

            def login(self, user, password):
                calls.append(("login", user, password))

            def send_message(self, message):
                calls.append(("send", message["To"], message["Subject"]))

        email = {"host": "smtp.example", "port": 587, "user": "u", "password": "p", "to": "a@b.c"}
        settings = notify.settings_from(self.config(email=email))
        notify.send_email(settings, "done", "Đã đăng bài\nchi tiết", None, smtp_class=FakeSMTP)
        self.assertEqual(
            calls, [("connect", "smtp.example", 587), ("starttls",), ("login", "u", "p"), ("send", "a@b.c", "Đã đăng bài")]
        )

    def test_message_text_describes_the_job(self):
        job = {"content": "Nội dung bài", "page_url": PAGE, "post_url": "https://www.facebook.com/x/posts/1"}
        text = notify.describe("done", job, "Hi English", "xong")
        self.assertIn("Hi English", text)
        self.assertIn("Nội dung bài", text)
        self.assertIn("https://www.facebook.com/x/posts/1", text)

    def test_background_sending_reports_back(self):
        out = []
        thread = notify.notify_in_background(
            self.config(), "done", "x", None, on_result=out.append, senders={"telegram": lambda *a: None}
        )
        thread.join(5)
        self.assertEqual(out, [[("telegram", None)]])


if __name__ == "__main__":
    unittest.main()
