import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime

import store


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, name):
        return os.path.join(self.dir, name)


class ConfigTest(TempDirTest):
    def test_missing_file_gives_defaults(self):
        config = store.load_config(self.path("config.json"))
        self.assertEqual(config, store.DEFAULT_CONFIG)

    def test_defaults_are_not_shared_between_loads(self):
        first = store.load_config(self.path("config.json"))
        first["pages"].append({"name": "A", "url": "https://www.facebook.com/a"})
        second = store.load_config(self.path("config.json"))
        self.assertEqual(second["pages"], [])

    def test_round_trips_vietnamese_and_emoji(self):
        config = store.load_config(self.path("config.json"))
        config["templates"].append({"name": "Mẹo", "content": "✨ Chào buổi sáng 🌟"})
        config["headless"] = True
        store.save_config(self.path("config.json"), config)
        self.assertEqual(store.load_config(self.path("config.json")), config)

    def test_saved_file_is_readable_utf8(self):
        store.save_config(self.path("config.json"), {"templates": [{"name": "Mẹo"}]})
        with open(self.path("config.json"), encoding="utf-8") as handle:
            self.assertIn("Mẹo", handle.read())

    def test_older_file_gets_new_default_keys(self):
        store.save_json(self.path("config.json"), {"headless": True})
        config = store.load_config(self.path("config.json"))
        self.assertTrue(config["headless"])
        self.assertEqual(config["pages"], [])

    def test_corrupt_file_gives_defaults_and_is_kept_aside(self):
        with open(self.path("config.json"), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        config = store.load_config(self.path("config.json"))
        self.assertEqual(config, store.DEFAULT_CONFIG)
        with open(self.path("config.json.corrupt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{ not json")

    def test_values_of_the_wrong_type_fall_back_to_defaults(self):
        store.save_json(
            self.path("config.json"),
            {
                "headless": "yes",
                "scheduler_enabled": False,
                "pages": None,
                "templates": {"name": "Mẹo"},
                "last_pages": "Nhóm A",
                "page_gap_minutes": True,
            },
        )
        config = store.load_config(self.path("config.json"))
        self.assertEqual(config, dict(store.DEFAULT_CONFIG, scheduler_enabled=False))

    def test_broken_pages_and_templates_are_left_out(self):
        page = {"name": "Nhóm A", "url": "https://www.facebook.com/groups/a"}
        template = {"name": "Mẹo", "content": "✨"}
        store.save_json(
            self.path("config.json"),
            {
                "pages": [page, "nhóm b", {"name": "Thiếu URL"}, {"name": 5, "url": "x"}],
                "templates": [template, None, {"name": "Thiếu nội dung"}],
                "last_pages": ["Nhóm A", 7],
                "page_gap_minutes": -3,
            },
        )
        config = store.load_config(self.path("config.json"))
        self.assertEqual(config["pages"], [page])
        self.assertEqual(config["templates"], [template])
        self.assertEqual(config["last_pages"], ["Nhóm A"])
        self.assertEqual(config["page_gap_minutes"], 5)

    def test_keys_the_app_does_not_know_are_kept(self):
        store.save_json(self.path("config.json"), {"last_page": "Nhóm A"})
        self.assertEqual(store.load_config(self.path("config.json"))["last_page"], "Nhóm A")


class QueueTest(TempDirTest):
    def test_missing_queue_is_empty(self):
        self.assertEqual(store.load_queue(self.path("queue.json")), [])

    def test_queue_round_trips(self):
        jobs = [{"id": "1", "content": "Bài 1 🎉", "status": "pending"}]
        store.save_queue(self.path("queue.json"), jobs)
        self.assertEqual(store.load_queue(self.path("queue.json")), jobs)

    def test_file_with_wrong_shape_is_treated_as_empty(self):
        store.save_json(self.path("queue.json"), {"oops": 1})
        self.assertEqual(store.load_queue(self.path("queue.json")), [])


class NormalizePageUrlTest(unittest.TestCase):
    def test_keeps_a_full_url(self):
        self.assertEqual(
            store.normalize_page_url("https://www.facebook.com/hienglishoffical"),
            "https://www.facebook.com/hienglishoffical",
        )

    def test_adds_the_scheme(self):
        self.assertEqual(
            store.normalize_page_url(" facebook.com/hienglishoffical "),
            "https://facebook.com/hienglishoffical",
        )

    def test_upgrades_http(self):
        self.assertEqual(
            store.normalize_page_url("http://www.facebook.com/abc"),
            "https://www.facebook.com/abc",
        )

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            store.normalize_page_url("   ")

    def test_rejects_other_sites(self):
        for url in (
            "https://example.com/page",
            "https://facebook.com.evil.com/page",
            "https://notfacebook.com/page",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                store.normalize_page_url(url)

    def test_rejects_a_url_without_a_page(self):
        with self.assertRaises(ValueError):
            store.normalize_page_url("https://www.facebook.com/")


class SessionSavedAtTest(TempDirTest):
    def save_state(self, *cookies):
        store.save_json(self.path("state.json"), {"cookies": list(cookies), "origins": []})
        return store.session_saved_at(self.path("state.json"))

    @staticmethod
    def login_cookie(expires=-1, domain=".facebook.com"):
        return {"name": "c_user", "value": "1", "domain": domain, "path": "/", "expires": expires}

    def test_none_when_there_is_no_session(self):
        self.assertIsNone(store.session_saved_at(self.path("state.json")))

    def test_time_of_the_saved_session(self):
        saved = self.save_state(self.login_cookie(expires=time.time() + 3600))
        self.assertIsInstance(saved, datetime)
        self.assertLess(abs((datetime.now() - saved).total_seconds()), 60)

    def test_login_cookie_without_an_expiry_counts(self):
        self.assertIsNotNone(self.save_state(self.login_cookie(expires=-1)))

    def test_none_for_a_browser_that_never_logged_in(self):
        visitor = {"name": "datr", "value": "x", "domain": ".facebook.com", "path": "/", "expires": -1}
        self.assertIsNone(self.save_state(visitor))
        self.assertIsNone(self.save_state())

    def test_none_when_the_login_cookie_has_expired(self):
        self.assertIsNone(self.save_state(self.login_cookie(expires=time.time() - 60)))

    def test_none_for_a_login_cookie_of_another_site(self):
        self.assertIsNone(self.save_state(self.login_cookie(domain=".example.com")))

    def test_none_for_an_unreadable_state_file(self):
        for text in ("{ not json", "[]", '{"cookies": [1]}', "{}"):
            with self.subTest(text=text):
                with open(self.path("state.json"), "w", encoding="utf-8") as handle:
                    handle.write(text)
                self.assertIsNone(store.session_saved_at(self.path("state.json")))
        # The session file is the user's login: unlike config.json it is never moved aside
        self.assertTrue(os.path.exists(self.path("state.json")))


class LockFileTest(TempDirTest):
    def test_second_lock_on_the_same_file_is_refused(self):
        first = store.lock_file(self.path("app.lock"))
        self.addCleanup(first.close)
        self.assertIsNotNone(first)
        self.assertIsNone(store.lock_file(self.path("app.lock")))

    def test_lock_is_free_again_once_released(self):
        store.lock_file(self.path("app.lock")).close()
        again = store.lock_file(self.path("app.lock"))
        self.assertIsNotNone(again)
        again.close()

    def test_another_process_cannot_take_the_lock(self):
        held = store.lock_file(self.path("app.lock"))
        self.addCleanup(held.close)
        probe = "import store, sys; sys.exit(0 if store.lock_file(sys.argv[1]) is None else 1)"
        result = subprocess.run(
            [sys.executable, "-c", probe, self.path("app.lock")],
            cwd=os.path.dirname(os.path.abspath(store.__file__)),
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
