"""Save and restore your pages, templates, settings and queue as one JSON file, e.g. to move to another computer.

    python backup.py export sao-luu.json [--include-secrets] [--include-history]
    python backup.py import sao-luu.json [--replace] [--dry-run]

The Facebook logins (state.json, state.<account>.json) are never included: log in again on the new computer.
Tokens and passwords for notifications are left out unless you ask with --include-secrets, so the file
is safe to keep in a cloud folder. Importing keeps what you already have (pages and templates with the same
name are updated, queue entries with the same id are skipped); --replace overwrites instead. The old
config.json and queue.json are copied to *.bak-<time> first. Like enqueue.py it refuses to run while the app is open.
"""
import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime

import enqueue
import history
import scheduler
import store

FORMAT = "facebook-auto-poster-backup"
VERSION = 1
SECRET_FIELDS = (("notifications", "telegram", "bot_token"), ("notifications", "email", "password"))


class BackupError(Exception):
    pass


def _strip_secrets(config):
    config = copy.deepcopy(config)
    for section, group, key in SECRET_FIELDS:
        config[section][group][key] = ""
    return config


def export_data(base_dir, include_secrets=False, include_history=False, now=None):
    files = enqueue.paths(base_dir)
    config = store.load_config(files["config"])
    if not include_secrets:
        config = _strip_secrets(config)
    jobs, _ = scheduler.valid_jobs(store.load_queue(files["queue"]))
    data = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "includes_secrets": include_secrets,
        "config": config,
        "queue": jobs,
    }
    if include_history:
        data["history"] = history.load(files["history"])
    return data


def write_export(base_dir, out_path, **options):
    data = export_data(base_dir, **options)
    store.save_json(out_path, data)
    return data


def read_backup(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        raise BackupError(f"Không đọc được tệp sao lưu {path}: {error}")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("Đây không phải tệp sao lưu của chương trình này.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise BackupError("Tệp sao lưu được tạo bởi một phiên bản mới hơn; hãy cập nhật chương trình.")
    if not isinstance(data.get("config"), dict) or not isinstance(data.get("queue"), list):
        raise BackupError("Tệp sao lưu bị thiếu phần cấu hình hoặc hàng đợi.")
    return data


def _merge_by(local, incoming, key):
    merged = {item[key]: item for item in local}
    updated = 0
    for item in incoming:
        updated += item[key] in merged
        merged[item[key]] = item
    return list(merged.values()), updated


def _keep_local_secrets(incoming, local):
    for section, group, key in SECRET_FIELDS:
        if not incoming[section][group][key] and local[section][group][key]:
            incoming[section][group][key] = local[section][group][key]


def merge_data(base_dir, data, replace=False):
    """(config, queue, report) after importing `data`; nothing is written."""
    files = enqueue.paths(base_dir)
    local = store.load_config(files["config"])
    incoming_config = store.load_config_from_dict(data["config"])
    local_jobs, _ = scheduler.valid_jobs(store.load_queue(files["queue"]))
    incoming_jobs, dropped = scheduler.valid_jobs(data["queue"])
    for job in incoming_jobs:
        if job["status"] == "running":
            job["status"] = "pending"
    report = {"dropped_jobs": dropped}
    if replace:
        config = incoming_config
        _keep_local_secrets(config, local)
        queue = incoming_jobs
        report.update(pages=len(config["pages"]), templates=len(config["templates"]), jobs_added=len(queue), jobs_skipped=0)
    else:
        config = copy.deepcopy(local)
        config["pages"], updated_pages = _merge_by(local["pages"], incoming_config["pages"], "name")
        config["templates"], updated_templates = _merge_by(local["templates"], incoming_config["templates"], "name")
        config["accounts"] = local["accounts"] + [name for name in incoming_config["accounts"] if name not in local["accounts"]]
        known = {job["id"] for job in local_jobs}
        fresh = [job for job in incoming_jobs if job["id"] not in known]
        queue = local_jobs + fresh
        report.update(
            pages=len(incoming_config["pages"]), pages_updated=updated_pages,
            templates=len(incoming_config["templates"]), templates_updated=updated_templates,
            jobs_added=len(fresh), jobs_skipped=len(incoming_jobs) - len(fresh),
        )
    wanted = [template.get("media_path", "") for template in config["templates"]]
    wanted += [job["image_path"] for job in queue if job["status"] == "pending"]
    report["missing_files"] = sorted({path for value in wanted for path in store.split_media(value) if not os.path.isfile(path)})
    return config, queue, report


def import_data(base_dir, data, replace=False, dry_run=False, now=None):
    """Import a backup into the folder. Returns a report of what changed (or would change)."""
    config, queue, report = merge_data(base_dir, data, replace)
    report["dry_run"] = dry_run
    if dry_run:
        return report
    files = enqueue.paths(base_dir)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    report["backups"] = []
    for key in ("config", "queue"):
        if os.path.exists(files[key]):
            target = f"{files[key]}.bak-{stamp}"
            shutil.copyfile(files[key], target)
            report["backups"].append(os.path.basename(target))
    store.save_config(files["config"], config)
    store.save_queue(files["queue"], queue)
    return report


def main(argv=None, base_dir=enqueue.BASE_DIR):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Sao lưu và khôi phục cấu hình, mẫu, Fanpage và hàng đợi.")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Ghi ra một tệp sao lưu.")
    export.add_argument("file")
    export.add_argument("--include-secrets", action="store_true", help="Kèm token Telegram và mật khẩu email.")
    export.add_argument("--include-history", action="store_true", help="Kèm lịch sử đăng bài.")
    imp = commands.add_parser("import", help="Nhập từ một tệp sao lưu.")
    imp.add_argument("file")
    imp.add_argument("--replace", action="store_true", help="Ghi đè thay vì gộp.")
    imp.add_argument("--dry-run", action="store_true", help="Chỉ cho biết sẽ thay đổi gì.")
    args = parser.parse_args(argv)

    try:
        if args.command == "export":
            data = write_export(base_dir, args.file, include_secrets=args.include_secrets, include_history=args.include_history)
            result = {"file": os.path.abspath(args.file), "pages": len(data["config"]["pages"]),
                      "templates": len(data["config"]["templates"]), "jobs": len(data["queue"]),
                      "includes_secrets": data["includes_secrets"]}
            text = (f"Đã ghi {result['file']}: {result['pages']} Fanpage, {result['templates']} mẫu, {result['jobs']} bài hẹn."
                    + ("" if data["includes_secrets"] else " Token và mật khẩu đã được bỏ ra."))
        else:
            data = read_backup(args.file)
            files = enqueue.paths(base_dir)
            if args.dry_run:
                result = import_data(base_dir, data, args.replace, dry_run=True)
            else:
                with enqueue.editing(files):
                    result = import_data(base_dir, data, args.replace)
            text = (f"{'Sẽ nhập' if args.dry_run else 'Đã nhập'}: {result['jobs_added']} bài hẹn (bỏ qua {result['jobs_skipped']}), "
                    f"{result['pages']} Fanpage, {result['templates']} mẫu.")
            if result["missing_files"]:
                text += "\nChưa có các tệp đính kèm sau trên máy này:\n  " + "\n  ".join(result["missing_files"])
    except (BackupError, enqueue.QueueError, OSError) as error:
        message = error.message if isinstance(error, enqueue.QueueError) else str(error)
        code = error.code if isinstance(error, enqueue.QueueError) else 1
        if args.json:
            print(json.dumps({"ok": False, "exit_code": code, "message": message}, ensure_ascii=False))
        else:
            print(f"Lỗi: {message}", file=sys.stderr)
        return code
    print(json.dumps({"ok": True, **result}, ensure_ascii=False) if args.json else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
