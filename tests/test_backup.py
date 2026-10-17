import contextlib
import io
import json
import os
import tempfile
import unittest

import backup
import enqueue
import scheduler
import store
from datetime import datetime

HI = "https://www.facebook.com/hienglish"
GROUP = "https://www.facebook.com/groups/jaecoo"


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self.source = self.folder()
        self.target = self.folder()
        store.save_config(
            os.path.join(self.source, "config.json"),
            {
                "pages": [{"name": "Hi English", "url": HI, "language": "English (Mỹ)"}],
                "templates": [{"name": "Chào", "content": "xin chào", "media_path": ""}],
                "accounts": ["shop2"],
                "page_gap_minutes": 9,
                "notifications": {"telegram": {"bot_token": "BÍ-MẬT", "chat_id": "42"}, "email": {"password": "mk"}},
            },
        )
        job = scheduler.new_job("Bài hẹn", HI, "", datetime(2030, 1, 1, 9, 0))
        self.job_id = job["id"]
        store.save_queue(os.path.join(self.source, "queue.json"), [job])
        store.save_json(os.path.join(self.source, "state.json"), {"cookies": [{"name": "c_user"}]})

    def folder(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def config(self, folder):
        return store.load_config(os.path.join(folder, "config.json"))

    def queue(self, folder):
        return store.load_queue(os.path.join(folder, "queue.json"))


class ExportTest(BackupTestCase):
    def test_pages_templates_and_queue_are_included(self):
        data = backup.export_data(self.source)
        self.assertEqual(data["format"], backup.FORMAT)
        self.assertEqual([page["name"] for page in data["config"]["pages"]], ["Hi English"])
        self.assertEqual([template["name"] for template in data["config"]["templates"]], ["Chào"])
        self.assertEqual([job["content"] for job in data["queue"]], ["Bài hẹn"])

    def test_secrets_are_left_out_unless_asked_for(self):
        plain = backup.export_data(self.source)
        self.assertEqual(plain["config"]["notifications"]["telegram"], {"bot_token": "", "chat_id": "42"})
        self.assertEqual(plain["config"]["notifications"]["email"]["password"], "")
        self.assertFalse(plain["includes_secrets"])
        with_secrets = backup.export_data(self.source, include_secrets=True)
        self.assertEqual(with_secrets["config"]["notifications"]["telegram"]["bot_token"], "BÍ-MẬT")

    def test_the_login_is_never_included(self):
        out = os.path.join(self.source, "sao-luu.json")
        backup.write_export(self.source, out, include_secrets=True, include_history=True)
        with open(out, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("c_user", text)
        self.assertNotIn("cookies", text)

    def test_history_is_optional(self):
        self.assertNotIn("history", backup.export_data(self.source))
        self.assertIn("history", backup.export_data(self.source, include_history=True))


class ImportTest(BackupTestCase):
    def data(self, **options):
        return backup.export_data(self.source, **options)

    def test_importing_into_an_empty_folder_restores_everything(self):
        report = backup.import_data(self.target, self.data())
        config = self.config(self.target)
        self.assertEqual([page["name"] for page in config["pages"]], ["Hi English"])
        self.assertEqual(config["accounts"], ["shop2"])
        self.assertEqual([job["id"] for job in self.queue(self.target)], [self.job_id])
        self.assertEqual((report["jobs_added"], report["jobs_skipped"]), (1, 0))

    def test_merging_keeps_local_settings_and_updates_same_named_items(self):
        store.save_config(
            os.path.join(self.target, "config.json"),
            {
                "pages": [{"name": "Hi English", "url": "https://www.facebook.com/cu", "language": ""},
                          {"name": "Riêng", "url": GROUP, "language": ""}],
                "page_gap_minutes": 2,
                "accounts": ["khac"],
            },
        )
        report = backup.import_data(self.target, self.data())
        config = self.config(self.target)
        self.assertEqual({page["name"]: page["url"] for page in config["pages"]}, {"Hi English": HI, "Riêng": GROUP})
        self.assertEqual(config["page_gap_minutes"], 2)
        self.assertEqual(config["accounts"], ["khac", "shop2"])
        self.assertEqual(report["pages_updated"], 1)

    def test_queue_entries_already_there_are_skipped(self):
        backup.import_data(self.target, self.data())
        report = backup.import_data(self.target, self.data())
        self.assertEqual(len(self.queue(self.target)), 1)
        self.assertEqual((report["jobs_added"], report["jobs_skipped"]), (0, 1))

    def test_replace_overwrites_the_queue_and_settings(self):
        store.save_config(os.path.join(self.target, "config.json"), {"page_gap_minutes": 2, "pages": [{"name": "Cũ", "url": GROUP, "language": ""}]})
        store.save_queue(os.path.join(self.target, "queue.json"), [scheduler.new_job("Bài cũ", "", "", datetime(2030, 2, 1, 9, 0))])
        backup.import_data(self.target, self.data(), replace=True)
        self.assertEqual([page["name"] for page in self.config(self.target)["pages"]], ["Hi English"])
        self.assertEqual(self.config(self.target)["page_gap_minutes"], 9)
        self.assertEqual([job["content"] for job in self.queue(self.target)], ["Bài hẹn"])

    def test_local_secrets_survive_an_import_that_has_none(self):
        store.save_config(
            os.path.join(self.target, "config.json"),
            {"notifications": {"telegram": {"bot_token": "CỦA-TÔI", "chat_id": "1"}}},
        )
        backup.import_data(self.target, self.data(), replace=True)
        self.assertEqual(self.config(self.target)["notifications"]["telegram"]["bot_token"], "CỦA-TÔI")

    def test_the_old_files_are_copied_first(self):
        store.save_config(os.path.join(self.target, "config.json"), {"page_gap_minutes": 2})
        store.save_queue(os.path.join(self.target, "queue.json"), [])
        report = backup.import_data(self.target, self.data(), now=datetime(2026, 10, 17, 9, 0, 0))
        self.assertEqual(sorted(report["backups"]), ["config.json.bak-20261017-090000", "queue.json.bak-20261017-090000"])
        for name in report["backups"]:
            self.assertTrue(os.path.exists(os.path.join(self.target, name)))

    def test_a_dry_run_changes_nothing(self):
        report = backup.import_data(self.target, self.data(), dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["jobs_added"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.target, "config.json")))
        self.assertFalse(os.path.exists(os.path.join(self.target, "queue.json")))

    def test_files_missing_on_this_computer_are_listed(self):
        data = self.data()
        data["config"]["templates"][0]["media_path"] = os.path.join(self.target, "khong-co.png")
        data["queue"][0]["image_path"] = f"{os.path.join(self.target, 'a.png')}|{os.path.join(self.target, 'b.png')}"
        with open(os.path.join(self.target, "a.png"), "wb") as handle:
            handle.write(b"x")
        report = backup.import_data(self.target, data, dry_run=True)
        self.assertEqual(
            report["missing_files"], sorted([os.path.join(self.target, "khong-co.png"), os.path.join(self.target, "b.png")])
        )

    def test_a_job_that_was_running_becomes_pending(self):
        data = self.data()
        data["queue"][0]["status"] = "running"
        backup.import_data(self.target, data)
        self.assertEqual(self.queue(self.target)[0]["status"], "pending")

    def test_damaged_queue_entries_are_dropped_and_counted(self):
        data = self.data()
        data["queue"].append({"content": "không có giờ"})
        report = backup.import_data(self.target, data, dry_run=True)
        self.assertEqual(report["dropped_jobs"], 1)


class ReadBackupTest(BackupTestCase):
    def write(self, content):
        path = os.path.join(self.source, "x.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def test_other_files_are_refused(self):
        for content in ("không phải json", {"a": 1}, {"format": backup.FORMAT, "version": 99, "config": {}, "queue": []},
                        {"format": backup.FORMAT, "version": 1, "config": [], "queue": []}):
            with self.assertRaises(backup.BackupError):
                backup.read_backup(self.write(content))

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(backup.BackupError):
            backup.read_backup(os.path.join(self.source, "khong-co.json"))


class CommandLineTest(BackupTestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = backup.main(["--json", *argv], base_dir=self.target)
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def test_export_then_import_round_trip(self):
        out = os.path.join(self.source, "sao-luu.json")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(backup.main(["export", out], base_dir=self.source), 0)
        code, result = self.run_cli("import", out)
        self.assertEqual((code, result["ok"], result["jobs_added"]), (0, True, 1))
        self.assertEqual(len(self.queue(self.target)), 1)

    def test_import_is_refused_while_the_app_is_open(self):
        out = os.path.join(self.source, "sao-luu.json")
        backup.write_export(self.source, out)
        lock = store.lock_file(os.path.join(self.target, "app.lock"))
        self.addCleanup(lock.close)
        code, result = self.run_cli("import", out)
        self.assertEqual((code, result["ok"]), (enqueue.EXIT_APP_OPEN, False))
        self.assertFalse(os.path.exists(os.path.join(self.target, "queue.json")))

    def test_a_dry_run_works_while_the_app_is_open(self):
        out = os.path.join(self.source, "sao-luu.json")
        backup.write_export(self.source, out)
        lock = store.lock_file(os.path.join(self.target, "app.lock"))
        self.addCleanup(lock.close)
        code, result = self.run_cli("import", out, "--dry-run")
        self.assertEqual((code, result["dry_run"]), (0, True))

    def test_a_bad_file_is_reported(self):
        code, result = self.run_cli("import", os.path.join(self.source, "khong-co.json"))
        self.assertEqual((code, result["ok"]), (1, False))


if __name__ == "__main__":
    unittest.main()
