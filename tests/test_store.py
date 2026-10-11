import os
import tempfile
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
    def test_none_when_there_is_no_session(self):
        self.assertIsNone(store.session_saved_at(self.path("state.json")))

    def test_time_of_the_saved_session(self):
        store.save_json(self.path("state.json"), {"cookies": []})
        saved = store.session_saved_at(self.path("state.json"))
        self.assertIsInstance(saved, datetime)
        self.assertLess(abs((datetime.now() - saved).total_seconds()), 60)


if __name__ == "__main__":
    unittest.main()
