"""Command-line access to the scheduled-post queue, for scripts and agents (the app does the posting).

    python enqueue.py add --content-file bai.txt --page URL [--page URL ...] --at "YYYY-MM-DD HH:MM"
    python enqueue.py list
    python enqueue.py remove JOB_ID

Posts are only published while app.py is open. Changing queue.json while the app is
open would be overwritten by the app, so `add` and `remove` refuse to run then (exit code 3).
"""
import argparse
import json
import os
import sys
from datetime import datetime

import scheduler
import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EXIT_APP_OPEN = 3


class QueueError(Exception):
    pass


def paths(base_dir):
    return {
        "queue": os.path.join(base_dir, "queue.json"),
        "config": os.path.join(base_dir, "config.json"),
        "lock": os.path.join(base_dir, "app.lock"),
    }


def read_queue(queue_path):
    jobs, dropped = scheduler.valid_jobs(store.load_queue(queue_path))
    if dropped:
        # Same as the app: the unreadable entries are lost on the next save, so keep a copy
        store.keep_aside(queue_path)
    return jobs


def summary(job):
    keys = ("id", "status", "run_at", "repeat", "page_url", "image_path", "content", "last_message")
    return {key: job.get(key) for key in keys}


def add_jobs(args, files, now):
    try:
        content = resolve_content(args)
        run_at = scheduler.parse_run_at(args.at)
        page_urls = [store.normalize_page_url(url) for url in args.page]
    except (OSError, ValueError) as error:
        raise QueueError(str(error))
    if not content.strip():
        raise QueueError("Nội dung bài viết đang để trống.")
    if run_at < now - scheduler.GRACE:
        raise QueueError(f"Giờ hẹn {args.at} đã qua, bài sẽ bị đánh dấu Lỡ.")
    image_path = ""
    if args.image:
        image_path = os.path.abspath(args.image)
        if not os.path.isfile(image_path):
            raise QueueError(f"Không tìm thấy tệp ảnh/video: {image_path}")
    gap = args.gap if args.gap is not None else store.load_config(files["config"])["page_gap_minutes"]
    try:
        run_times = scheduler.stagger(run_at, len(page_urls), gap)
        jobs = [scheduler.new_job(content, url, image_path, moment, args.repeat) for url, moment in zip(page_urls, run_times)]
    except ValueError as error:
        raise QueueError(str(error))
    if len(jobs) > 1:
        scheduler.link_batch(jobs, gap)
    return jobs


def resolve_content(args):
    if args.content_file:
        with open(args.content_file, encoding="utf-8", newline="") as handle:
            return handle.read()
    if args.content is None:
        raise ValueError("Thiếu nội dung bài viết (truyền trực tiếp hoặc dùng --content-file).")
    return args.content


def build_parser():
    parser = argparse.ArgumentParser(description="Quản lý hàng đợi bài hẹn giờ (queue.json).")
    parser.add_argument("--json", action="store_true", help="In kết quả dưới dạng JSON.")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Thêm bài hẹn giờ, mỗi --page là một bài riêng.")
    add.add_argument("content", nargs="?", default=None, help="Nội dung bài viết.")
    add.add_argument("--content-file", default=None, help="Đọc nội dung từ tệp UTF-8.")
    add.add_argument("--page", action="append", required=True, help="URL Fanpage/nhóm; lặp lại để đăng nhiều nơi.")
    add.add_argument("--image", default=None, help="Ảnh hoặc video đính kèm.")
    add.add_argument("--at", required=True, help='Giờ đăng, dạng "YYYY-MM-DD HH:MM".')
    add.add_argument("--repeat", choices=list(scheduler.REPEAT_STEPS), default="none")
    add.add_argument("--gap", type=float, default=None, help="Giãn cách (phút) giữa các nơi; mặc định lấy từ config.json.")

    commands.add_parser("list", help="Liệt kê các bài trong hàng đợi.")

    remove = commands.add_parser("remove", help="Xóa một bài khỏi hàng đợi.")
    remove.add_argument("job_id")
    return parser


def run(args, files, now):
    """Carry out the command. Returns the data to print."""
    if args.command == "list":
        return {"jobs": [summary(job) for job in read_queue(files["queue"])]}

    # The app owns queue.json while it is open; holding the lock also stops it from starting mid-edit
    lock = store.lock_file(files["lock"])
    if lock is None:
        raise QueueError("Ứng dụng (app.py) đang mở: hãy đóng nó hoặc thêm bài ngay trong ứng dụng.", EXIT_APP_OPEN)
    try:
        jobs = read_queue(files["queue"])
        if args.command == "add":
            added = add_jobs(args, files, now)
            store.save_queue(files["queue"], jobs + added)
            return {"added": [summary(job) for job in added]}
        remaining = [job for job in jobs if job["id"] != args.job_id]
        if len(remaining) == len(jobs):
            raise QueueError(f"Không có bài nào có id {args.job_id}.")
        store.save_queue(files["queue"], remaining)
        return {"removed": args.job_id}
    finally:
        lock.close()


def print_result(args, result):
    if args.json:
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    elif "jobs" in result:
        for job in result["jobs"]:
            print(f"{job['id']}  {job['run_at']}  {job['status']:<8} {job['repeat']:<6} {job['page_url']}")
        if not result["jobs"]:
            print("Hàng đợi trống.")
    elif "added" in result:
        for job in result["added"]:
            print(f"Đã thêm {job['id']} lúc {job['run_at']} -> {job['page_url']}")
        print("Bài chỉ được đăng khi app.py đang mở.")
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
        code = error.args[1] if len(error.args) > 1 else 1
        message = str(error.args[0])
        if args.json:
            print(json.dumps({"ok": False, "exit_code": code, "message": message}, ensure_ascii=False))
        else:
            print(f"Lỗi: {message}", file=sys.stderr)
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
