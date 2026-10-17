"""Command-line access to the scheduled-post queue, for scripts and agents.

    python enqueue.py add --content-file bai.txt --page "Hi English" --page URL --at "YYYY-MM-DD HH:MM"
    python enqueue.py list [--status pending]
    python enqueue.py edit JOB_ID [--at ...] [--content-file ...] [--page ...] [--image ...] [--repeat ...]
    python enqueue.py remove JOB_ID
    python enqueue.py import bai.csv [--dry-run]        (python enqueue.py csv-template shows the columns)

Posts are published by the app (app.py) or the background runner (daemon.py) while it is running.
The app keeps its own copy of queue.json and would overwrite changes made here, so every command that
changes the queue refuses to run while the app is open (exit code 3). The background runner re-reads the
file for each step, so changes made here while it runs are picked up.

--page takes a page name saved in config.json, a Facebook URL, or "personal" for the profile. In the text,
{page} {language} {date} {time} {weekday} are filled in per page and {a|b|c} picks one at random (see
variants.py); give --content-file several times to let the versions take turns across the pages.
"""
import argparse
import contextlib
import csv
import json
import os
import sys
from datetime import datetime
from urllib.parse import urlsplit

import history
import scheduler
import spam
import store
import variants

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EXIT_APP_OPEN = 3
PERSONAL_NAMES = ("personal", "me", "profile")
CSV_COLUMNS = ("content", "content_file", "pages", "at", "image", "repeat", "account", "jitter")


class QueueError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.message = message
        self.code = code


def paths(base_dir):
    return {
        "queue": os.path.join(base_dir, "queue.json"),
        "config": os.path.join(base_dir, "config.json"),
        "history": os.path.join(base_dir, "history.jsonl"),
        "app_lock": os.path.join(base_dir, "app.lock"),
        "daemon_lock": os.path.join(base_dir, "daemon.lock"),
        "queue_lock": os.path.join(base_dir, "queue.lock"),
    }


def read_queue(queue_path):
    jobs, dropped = scheduler.valid_jobs(store.load_queue(queue_path))
    if dropped:
        # Same as the app: the unreadable entries are lost on the next save, so keep a copy
        store.keep_aside(queue_path)
    return jobs


def summary(job):
    keys = ("id", "status", "run_at", "repeat", "page_url", "image_path", "content", "last_message",
            "account", "post_url", "jitter_minutes", "attempts")
    return {key: job.get(key) for key in keys if key in job or key in ("id", "status", "run_at", "repeat", "page_url", "content")}


@contextlib.contextmanager
def editing(files):
    """Exclusive access to queue.json for a change. Raises QueueError(EXIT_APP_OPEN) while the app is open."""
    app = store.lock_file(files["app_lock"])
    if app is None:
        # Held by the app (which keeps the queue in memory) or by the background runner (which does not)
        probe = store.lock_file(files["daemon_lock"])
        if probe is not None:
            probe.close()
            raise QueueError("Ứng dụng (app.py) đang mở: hãy đóng nó hoặc thêm bài ngay trong ứng dụng.", EXIT_APP_OPEN)
    try:
        with store.locked(files["queue_lock"]):
            yield
    except TimeoutError as error:
        raise QueueError(str(error))
    finally:
        if app is not None:
            app.close()


# ---------------------------------------------------------------------------------------------------------
# Building jobs
# ---------------------------------------------------------------------------------------------------------


def resolve_page(value, config):
    """(url, name, language) for a page name, a Facebook URL or "personal"."""
    value = (value or "").strip()
    if value.lower() in PERSONAL_NAMES:
        return "", "", ""
    for page in config["pages"]:
        if page["name"].lower() == value.lower():
            return page["url"], page["name"], page.get("language", "")
    try:
        url = store.normalize_page_url(value)
    except ValueError:
        names = ", ".join(page["name"] for page in config["pages"]) or "(chưa có Fanpage nào trong config.json)"
        raise QueueError(f"Không hiểu nơi đăng “{value}”. Dùng tên đã lưu ({names}), URL facebook.com hoặc “personal”.")
    for page in config["pages"]:
        if same_address(page["url"], url):
            return page["url"], page["name"], page.get("language", "")
    return url, "", ""


def same_address(first, second):
    """Whether two Facebook URLs name the same place (www., a trailing slash and case do not matter)."""

    def key(url):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return host[4:] if host.startswith("www.") else host, parts.path.rstrip("/").lower(), parts.query

    return key(first) == key(second)


def read_text_file(path):
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as error:
        raise QueueError(f"Không đọc được tệp nội dung {path}: {error.strerror or error}")


def repeat_from(repeat, days):
    if days:
        names = [name.strip().lower() for name in days.split(",") if name.strip()]
        repeat = "days:" + ",".join(names)
    if not scheduler.is_valid_repeat(repeat):
        raise QueueError(
            f"Kiểu lặp không hợp lệ: {repeat}. Dùng none, daily, weekly, weekdays, monthly hoặc --days mon,wed,fri."
        )
    return repeat


def media_from(images):
    joined = store.join_media(images)
    for path in store.split_media(joined):
        if not os.path.isfile(path):
            raise QueueError(f"Không tìm thấy tệp ảnh/video: {os.path.abspath(path)}")
    return store.join_media([os.path.abspath(path) for path in store.split_media(joined)])


def parse_when(text, now):
    try:
        run_at = scheduler.parse_run_at(text)
    except (ValueError, AttributeError):
        raise QueueError(f"Giờ hẹn phải có dạng YYYY-MM-DD HH:MM, nhận được: {text!r}")
    if run_at < now - scheduler.GRACE:
        raise QueueError(f"Giờ hẹn {text} đã qua, bài sẽ bị đánh dấu Lỡ.")
    return run_at


def build_jobs(contents, pages, run_at, config, now, image="", repeat="none", account="", jitter=0, gap=None,
               allow_duplicate=False, render=True):
    """The jobs for one post going to each of `pages`, `gap` minutes apart."""
    if not contents or not any(text.strip() for text in contents) and not image:
        raise QueueError("Nội dung bài viết đang để trống.")
    if not pages:
        raise QueueError("Chưa có nơi đăng (--page).")
    if account and account != "default" and account not in config["accounts"]:
        raise QueueError(f"Chưa có tài khoản “{account}” trong config.json (mục accounts).")
    gap = config["page_gap_minutes"] if gap is None else gap
    try:
        moments = scheduler.stagger(run_at, len(pages), gap)
        jobs = []
        for index, (page, moment) in enumerate(zip(pages, moments)):
            url, name, language = resolve_page(page, config)
            text = variants.pick(contents, index)
            if render:
                text = variants.render(text, name, language, moment)
            jobs.append(scheduler.new_job(text, url, image, moment, repeat, account if account != "default" else "",
                                          allow_duplicate, jitter))
    except ValueError as error:
        raise QueueError(str(error))
    if len(jobs) > 1:
        scheduler.link_batch(jobs, gap)
    return jobs


def warnings_for(new_jobs, existing, files, config, now):
    """Reasons to think twice about `new_jobs`: they look like posts already made or already waiting."""
    settings = spam.settings_from(config)
    records = history.load(files["history"])
    found = []
    for job in new_jobs:
        where = job["page_url"] or "trang cá nhân"
        allow = job.get("allow_duplicate") or job["repeat"] != "none"
        for problem in spam.check(job["content"], job["page_url"], records, now, settings, allow_duplicate=allow):
            found.append(f"{where}: {problem.message}")
        if not allow:
            for other in existing + [j for j in new_jobs if j is not job]:
                if other["status"] == "pending" and other["page_url"] == job["page_url"] and \
                        spam.similarity(job["content"], other["content"]) >= settings["similarity"]:
                    found.append(f"{where}: giống bài hẹn lúc {other['run_at']}.")
                    break
    return found


# ---------------------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------------------


def contents_from(args):
    if args.content_file:
        return [read_text_file(path) for path in args.content_file]
    if args.content is None:
        raise QueueError("Thiếu nội dung bài viết (truyền trực tiếp hoặc dùng --content-file).")
    return [args.content]


def cmd_add(args, files, now):
    config = store.load_config(files["config"])
    contents = contents_from(args)
    run_at = parse_when(args.at, now)
    jobs = build_jobs(
        contents, args.page, run_at, config, now,
        image=media_from(args.image), repeat=repeat_from(args.repeat, args.days), account=args.account or "",
        jitter=args.jitter, gap=args.gap, allow_duplicate=args.allow_duplicate, render=not args.no_render,
    )
    existing = read_queue(files["queue"])
    warnings = warnings_for(jobs, existing, files, config, now)
    if warnings and args.strict:
        raise QueueError("Không thêm vì: " + " ".join(warnings))
    store.save_queue(files["queue"], existing + jobs)
    return {"added": [summary(job) for job in jobs], "warnings": warnings}


def cmd_list(args, files, now):
    jobs = read_queue(files["queue"])
    if args.status:
        jobs = [job for job in jobs if job["status"] == args.status]
    return {"jobs": [summary(job) for job in sorted(jobs, key=scheduler.ready_at)]}


def cmd_remove(args, files, now):
    jobs = read_queue(files["queue"])
    remaining = [job for job in jobs if job["id"] != args.job_id]
    if len(remaining) == len(jobs):
        raise QueueError(f"Không có bài nào có id {args.job_id}.")
    store.save_queue(files["queue"], remaining)
    return {"removed": args.job_id}


def cmd_edit(args, files, now):
    config = store.load_config(files["config"])
    jobs = read_queue(files["queue"])
    job = next((job for job in jobs if job["id"] == args.job_id), None)
    if job is None:
        raise QueueError(f"Không có bài nào có id {args.job_id}.")
    if job["status"] == "running":
        raise QueueError("Bài này đang chạy nên chưa sửa được.")
    changes = {}
    if args.content is not None or args.content_file:
        changes["content"] = contents_from(args)[0]
    if args.page:
        if len(args.page) > 1:
            raise QueueError("Sửa bài chỉ chọn được một nơi đăng. Hãy thêm bài mới cho nơi khác.")
        changes["page_url"] = resolve_page(args.page[0], config)[0]
    if args.clear_image:
        changes["image_path"] = ""
    elif args.image:
        changes["image_path"] = media_from(args.image)
    if args.repeat or args.days:
        changes["repeat"] = repeat_from(args.repeat or "none", args.days)
    if args.account is not None:
        if args.account not in ("", "default") and args.account not in config["accounts"]:
            raise QueueError(f"Chưa có tài khoản “{args.account}” trong config.json (mục accounts).")
        changes["account"] = "" if args.account == "default" else args.account
    if args.at:
        changes["run_at"] = scheduler.format_run_at(parse_when(args.at, now))
    if not changes and args.jitter is None and not args.allow_duplicate:
        raise QueueError("Không có gì để sửa: hãy cho ít nhất một tùy chọn như --at, --content-file, --page.")
    job.update(changes)
    for key in ("account",):
        if job.get(key) == "":
            job.pop(key)
    if args.jitter is not None:
        if args.jitter < 0:
            raise QueueError("Độ lệch giờ không được âm.")
        job["jitter_minutes"] = args.jitter
        if not args.jitter:
            job.pop("jitter_minutes")
    if args.allow_duplicate:
        job["allow_duplicate"] = True
    if job["repeat"] == "monthly":
        job["anchor_day"] = scheduler.parse_run_at(job["run_at"]).day
    # Like editing in the app: the post is waiting for its (new) time again
    job.update(status="pending", last_message="")
    for key in ("not_before", "attempts", "base_run_at"):
        job.pop(key, None)
    store.save_queue(files["queue"], jobs)
    return {"edited": summary(job)}


def read_csv_rows(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise QueueError("Tệp CSV trống.")
            columns = {name.strip().lower(): name for name in reader.fieldnames if name}
            unknown = [name for name in columns if name not in CSV_COLUMNS]
            if unknown:
                raise QueueError(f"Cột không hiểu: {', '.join(unknown)}. Các cột hợp lệ: {', '.join(CSV_COLUMNS)}.")
            if "at" not in columns or "pages" not in columns or not ("content" in columns or "content_file" in columns):
                raise QueueError("Tệp CSV cần ít nhất các cột: pages, at và content (hoặc content_file).")
            rows = []
            for line, raw in enumerate(reader, start=2):
                row = {key: (raw.get(original) or "").strip() for key, original in columns.items()}
                if any(row.values()):
                    rows.append((line, row))
            return rows
    except OSError as error:
        raise QueueError(f"Không đọc được tệp CSV {path}: {error.strerror or error}")
    except csv.Error as error:
        raise QueueError(f"Tệp CSV bị lỗi: {error}")


def cmd_import(args, files, now):
    config = store.load_config(files["config"])
    rows = read_csv_rows(args.csv_file)
    base = os.path.dirname(os.path.abspath(args.csv_file))
    new_jobs, errors = [], []
    for line, row in rows:
        try:
            if row.get("content_file"):
                content = read_text_file(row["content_file"] if os.path.isabs(row["content_file"])
                                         else os.path.join(base, row["content_file"]))
            else:
                content = row.get("content", "")
            pages = [part.strip() for part in row["pages"].replace(";", "|").split("|") if part.strip()]
            image = [part if os.path.isabs(part) else os.path.join(base, part) for part in store.split_media(row.get("image", ""))]
            jitter = int(row["jitter"]) if row.get("jitter") else 0
            new_jobs += build_jobs(
                [content], pages, parse_when(row["at"], now), config, now,
                image=media_from(image), repeat=repeat_from(row.get("repeat") or "none", None),
                account=row.get("account", ""), jitter=jitter, gap=args.gap, render=not args.no_render,
            )
        except (QueueError, ValueError) as error:
            errors.append(f"Dòng {line}: {error.message if isinstance(error, QueueError) else error}")
    if errors:
        # All or nothing: a half-imported sheet is hard to put right
        raise QueueError("Không nhập vì có lỗi:\n" + "\n".join(errors))
    if not new_jobs:
        raise QueueError("Tệp CSV không có dòng dữ liệu nào.")
    existing = read_queue(files["queue"])
    warnings = warnings_for(new_jobs, existing, files, config, now)
    if not args.dry_run:
        store.save_queue(files["queue"], existing + new_jobs)
    return {"added": [summary(job) for job in new_jobs], "warnings": warnings, "dry_run": args.dry_run}


def cmd_csv_template(args, files, now):
    sample = [
        ["Xin chào {page}!", "", "Hi English|Trang khác", "2026-12-01 08:30", "", "none", "", "0"],
        ["", "bai-dai.txt", "personal", "2026-12-02 20:00", "anh1.jpg|anh2.jpg", "weekdays", "", "20"],
    ]
    return {"csv": [list(CSV_COLUMNS)] + sample}


READ_ONLY = {"list", "csv-template"}
COMMANDS = {
    "add": cmd_add,
    "list": cmd_list,
    "remove": cmd_remove,
    "edit": cmd_edit,
    "import": cmd_import,
    "csv-template": cmd_csv_template,
}


def build_parser():
    parser = argparse.ArgumentParser(description="Quản lý hàng đợi bài hẹn giờ (queue.json).")
    parser.add_argument("--json", action="store_true", help="In kết quả dưới dạng JSON.")
    commands = parser.add_subparsers(dest="command", required=True)

    def post_options(command, adding):
        command.add_argument("content", nargs="?", default=None, help="Nội dung bài viết.")
        command.add_argument("--content-file", action="append", default=None,
                             help="Đọc nội dung từ tệp UTF-8; lặp lại để có nhiều bản thay phiên nhau.")
        command.add_argument("--image", action="append", default=None, help="Ảnh/video đính kèm; lặp lại để đăng nhiều tệp.")
        command.add_argument("--repeat", default=None if not adding else "none",
                             help="none, daily, weekly, weekdays, monthly hoặc days:mon,wed,fri.")
        command.add_argument("--days", default=None, help="Các thứ trong tuần để lặp, ví dụ mon,wed,fri.")
        command.add_argument("--jitter", type=int, default=None if not adding else 0,
                             help="Lệch giờ ngẫu nhiên tối đa ± số phút mỗi lần lặp.")
        command.add_argument("--account", default=None if not adding else "", help="Tên tài khoản Facebook (config.json).")
        command.add_argument("--allow-duplicate", action="store_true", help="Cho phép nội dung giống bài đã đăng.")
        command.add_argument("--no-render", action="store_true", help="Không thay {page}, {date}… và {a|b} trong nội dung.")

    add = commands.add_parser("add", help="Thêm bài hẹn giờ, mỗi --page là một bài riêng.")
    post_options(add, True)
    add.add_argument("--page", action="append", required=True, help="Tên Fanpage đã lưu, URL, hoặc personal; lặp lại để đăng nhiều nơi.")
    add.add_argument("--at", required=True, help='Giờ đăng, dạng "YYYY-MM-DD HH:MM".')
    add.add_argument("--gap", type=float, default=None, help="Giãn cách (phút) giữa các nơi; mặc định lấy từ config.json.")
    add.add_argument("--strict", action="store_true", help="Không thêm nếu có cảnh báo trùng lặp hoặc quá giới hạn.")

    lister = commands.add_parser("list", help="Liệt kê các bài trong hàng đợi.")
    lister.add_argument("--status", choices=list(scheduler.STATUSES), default=None)

    remove = commands.add_parser("remove", help="Xóa một bài khỏi hàng đợi.")
    remove.add_argument("job_id")

    edit = commands.add_parser("edit", help="Sửa một bài trong hàng đợi (bài sẽ chờ tới giờ mới).")
    edit.add_argument("job_id")
    post_options(edit, False)
    edit.add_argument("--page", action="append", default=None, help="Nơi đăng mới (một nơi).")
    edit.add_argument("--at", default=None, help='Giờ đăng mới, dạng "YYYY-MM-DD HH:MM".')
    edit.add_argument("--clear-image", action="store_true", help="Bỏ ảnh/video đính kèm.")

    imp = commands.add_parser("import", help="Nhập nhiều bài từ tệp CSV (xem csv-template).")
    imp.add_argument("csv_file")
    imp.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra, không thêm vào hàng đợi.")
    imp.add_argument("--gap", type=float, default=None, help="Giãn cách (phút) giữa các nơi của một dòng.")
    imp.add_argument("--no-render", action="store_true", help="Không thay {page}, {date}… và {a|b} trong nội dung.")

    commands.add_parser("csv-template", help="In tệp CSV mẫu.")
    return parser


def run(args, files, now):
    """Carry out the command. Returns the data to print."""
    command = COMMANDS[args.command]
    if args.command in READ_ONLY:
        return command(args, files, now)
    with editing(files):
        return command(args, files, now)


def print_result(args, result):
    if args.json:
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return
    if "csv" in result:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerows(result["csv"])
    elif "jobs" in result:
        for job in result["jobs"]:
            link = f"  {job['post_url']}" if job.get("post_url") else ""
            print(f"{job['id']}  {job['run_at']}  {job['status']:<8} {job['repeat']:<8} {job['page_url'] or '(trang cá nhân)'}{link}")
        if not result["jobs"]:
            print("Hàng đợi trống.")
    elif "added" in result:
        prefix = "Hợp lệ (chưa thêm)" if result.get("dry_run") else "Đã thêm"
        for job in result["added"]:
            print(f"{prefix} {job['id']} lúc {job['run_at']} -> {job['page_url'] or '(trang cá nhân)'}")
        for warning in result["warnings"]:
            print(f"Cảnh báo: {warning}")
        print("Bài chỉ được đăng khi app.py hoặc daemon.py đang chạy.")
    elif "edited" in result:
        print(f"Đã sửa {result['edited']['id']}, chờ lúc {result['edited']['run_at']}.")
    else:
        print(f"Đã xóa {result['removed']}.")


def main(argv=None, base_dir=BASE_DIR, now=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        print_result(args, run(args, paths(base_dir), now or datetime.now()))
    except QueueError as error:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": error.code, "message": error.message}, ensure_ascii=False))
        else:
            print(f"Lỗi: {error.message}", file=sys.stderr)
        return error.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
