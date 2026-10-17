"""AI helpers from the command line (the same ones the app's "Trợ lý AI" tab uses). Needs an Anthropic API key:
ANTHROPIC_API_KEY, or "ai" > "api_key" in config.json (check with `python ai.py`).

    python ai_cli.py draft "Mẹo học từ vựng" --page "Hi English" --page "Nhóm A" [--csv bai.csv --at "2026-10-20 08:30"]
    python ai_cli.py localize --content-file bai.txt --page "Hi English"
    python ai_cli.py variants --content-file bai.txt --count 3
    python ai_cli.py polish hashtags --content-file bai.txt           (fix, shorten, expand, hashtags, title, cta, emoji)
    python ai_cli.py review --content-file bai.txt
    python ai_cli.py series "Từ vựng IELTS" --count 7 --page "Hi English" --start 2026-10-20 --time 08:30 [--csv bai.csv]
    python ai_cli.py times --page "Hi English"
    python ai_cli.py reply "Giá bao nhiêu vậy ạ?" [--post-file bai.txt]
    python ai_cli.py describe anh.png
    python ai_cli.py card --content-file bai.txt --out anh.png
    python ai_cli.py explain [--job ID]                                (why did the last post fail?)
    python ai_cli.py selectors                                         (Facebook changed its page: propose new selectors)
    python ai_cli.py assistant "đăng bài chào hè lên Hi English 8h sáng mai" [--history-file chat.json] [--csv bai.csv]

Nothing here posts or schedules anything. Texts are printed, or written to a CSV that `enqueue.py import` can read after
you have looked at it. What you ask about is sent to Anthropic.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

import ai as ai_module
import ai_features as features
import enqueue
import history
import scheduler
import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_LINES = 80


class CliError(Exception):
    pass


def read_text(path):
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as error:
        raise CliError(f"Không đọc được tệp {path}: {error.strerror or error}")


def content_from(args):
    if getattr(args, "content_file", None):
        return read_text(args.content_file)
    if getattr(args, "content", None):
        return args.content
    raise CliError("Thiếu nội dung: truyền trực tiếp hoặc dùng --content-file.")


def find_targets(config, values):
    """The page entries named on the command line, as the AI features expect them."""
    if not values:
        raise CliError("Chưa có nơi đăng: dùng --page một hoặc nhiều lần.")
    targets = []
    for value in values:
        if value.strip().lower() in enqueue.PERSONAL_NAMES:
            targets.append({"name": "Trang cá nhân", "language": features.DEFAULT_LANGUAGE, "url": "", "ref": "personal"})
            continue
        try:
            url, name, language = enqueue.resolve_page(value, config)
        except enqueue.QueueError as error:
            raise CliError(error.message)
        page = next((item for item in config["pages"] if item["url"] == url), {})
        targets.append({"name": name or url, "language": language, "voice": page.get("voice", ""), "url": url,
                        "ref": name or url})
    return targets


def write_csv(path, rows):
    """A sheet for `enqueue.py import`: rows are (content, pages_reference, at)."""
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(enqueue.CSV_COLUMNS)
            for content, page, moment in rows:
                writer.writerow([content, "", page, moment, "", "none", "", "0"])
    except OSError as error:
        raise CliError(f"Không ghi được {path}: {error.strerror or error}")


def staggered(start_text, count, gap):
    try:
        start = scheduler.parse_run_at(start_text)
    except ValueError:
        raise CliError("Giờ đăng phải có dạng YYYY-MM-DD HH:MM.")
    return [scheduler.format_run_at(moment) for moment in scheduler.stagger(start, count, gap)]


# ---------------------------------------------------------------------------------------------------------
# Commands: each returns the data to print
# ---------------------------------------------------------------------------------------------------------


def cmd_check(engine, args, ctx):
    return {"reply": ai_module.check(engine), "model": engine.model}


def cmd_draft(engine, args, ctx):
    targets = find_targets(ctx["config"], args.page)
    details = read_text(args.details_file) if args.details_file else ""
    posts = features.draft_posts(engine, " ".join(args.idea), targets, args.tone, args.length, details)
    if args.csv:
        if not args.at:
            raise CliError("Muốn ghi CSV cần thêm --at \"YYYY-MM-DD HH:MM\".")
        moments = staggered(args.at, len(posts), ctx["config"]["page_gap_minutes"])
        write_csv(args.csv, [(post["content"], target["ref"], moment) for post, target, moment in zip(posts, targets, moments)])
    return {"posts": posts, "csv": args.csv}


def cmd_localize(engine, args, ctx):
    targets = find_targets(ctx["config"], args.page)
    return {"posts": features.localize(engine, content_from(args), targets)}


def cmd_variants(engine, args, ctx):
    return {"variants": features.variants(engine, content_from(args), args.count, args.language)}


def cmd_polish(engine, args, ctx):
    return features.polish(engine, content_from(args), args.operation, args.language)


def cmd_review(engine, args, ctx):
    return features.review(engine, content_from(args), args.language)


def cmd_series(engine, args, ctx):
    targets = find_targets(ctx["config"], args.page or ["personal"])
    target = targets[0]
    details = read_text(args.details_file) if args.details_file else ""
    posts = features.plan_series(engine, " ".join(args.topic), args.count, target, args.tone, details)
    start = date.today() + timedelta(days=1)
    if args.start:
        try:
            start = datetime.strptime(args.start, "%Y-%m-%d").date()
        except ValueError:
            raise CliError("Ngày bắt đầu phải có dạng YYYY-MM-DD.")
    weekdays = None
    if args.days:
        names = [name.strip().lower() for name in args.days.split(",") if name.strip()]
        if not names or any(name not in scheduler.WEEKDAY_CODES for name in names):
            raise CliError("--days dùng mon,tue,wed,thu,fri,sat,sun.")
        weekdays = {scheduler.WEEKDAY_CODES.index(name) for name in names}
    try:
        times = features.series_times(start, args.time, len(posts), args.every, weekdays)
    except ValueError as error:
        raise CliError(str(error))
    items = [{**post, "at": scheduler.format_run_at(moment), "page": target["name"]} for post, moment in zip(posts, times)]
    if args.csv:
        write_csv(args.csv, [(item["content"], target["ref"], item["at"]) for item in items])
    return {"posts": items, "csv": args.csv}


def cmd_times(engine, args, ctx):
    (target,) = find_targets(ctx["config"], [args.page])
    return features.suggest_times(engine, target, history.load(ctx["files"]["history"]), datetime.now())


def cmd_reply(engine, args, ctx):
    post_text = read_text(args.post_file) if args.post_file else ""
    return features.suggest_replies(engine, " ".join(args.comment), post_text, args.tone, args.language)


def cmd_describe(engine, args, ctx):
    return features.describe_image(engine, args.image, args.language, args.context)


def cmd_card(engine, args, ctx):
    import cards

    spec = features.design_card(engine, content_from(args), args.language, args.style)
    if args.theme:
        spec["theme"] = args.theme
    out = cards.render_card(spec, args.out, args.size)
    return {"card": spec, "file": os.path.abspath(out)}


def newest(folder, pattern_start="latest_"):
    try:
        names = [name for name in os.listdir(folder) if name.startswith(pattern_start) and name.endswith(".png")]
    except OSError:
        return None
    paths = [os.path.join(folder, name) for name in names]
    return max(paths, key=os.path.getmtime) if paths else None


def cmd_explain(engine, args, ctx):
    jobs, _ = scheduler.valid_jobs(store.load_queue(ctx["files"]["queue"]))
    job = None
    if args.job:
        job = next((item for item in jobs if item["id"] == args.job), None)
        if job is None:
            raise CliError(f"Không có bài nào có id {args.job}.")
    else:
        bad = [item for item in jobs if item["status"] in ("failed", "missed")]
        job = max(bad, key=lambda item: item.get("last_run") or "") if bad else None
    if job is not None:
        message = job["last_message"]
    else:
        records = [r for r in history.load(ctx["files"]["history"]) if r.get("status") in ("failed", "session_expired", "blocked")]
        if not records:
            raise CliError("Không thấy bài lỗi nào để giải thích.")
        message = records[-1].get("message", "")
    log_path = os.path.join(ctx["base_dir"], "daemon.log")
    lines = read_text(log_path).splitlines()[-LOG_LINES:] if os.path.exists(log_path) else []
    found = re.search(r"mã thoát (\d+)", message)
    exit_code = int(found.group(1)) if found else None
    result = features.explain_failure(engine, message, lines, exit_code, newest(os.path.join(ctx["base_dir"], "debug")))
    result["job_id"] = job["id"] if job else None
    result["message"] = message
    return result


def cmd_selectors(engine, args, ctx):
    screenshot = None
    problems = ["(không rõ: dùng --from-file)"]
    if args.from_file:
        dom_file = args.from_file
    else:
        command = [sys.executable, "-u", os.path.join(BASE_DIR, "post.py"), "--selftest", "--json"]
        if args.page:
            command += ["--page", find_targets(ctx["config"], [args.page])[0]["url"]]
        if args.account:
            command += ["--account", args.account]
        done = subprocess.run(command, cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                              env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        try:
            outcome = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise CliError(f"post.py --selftest không trả về kết quả (mã thoát {done.returncode}).")
        report = outcome.get("report")
        if report is None:
            raise CliError(outcome.get("message", "Không chạy được selftest."))
        if report["ok"]:
            return {"ok_already": True, "summary": "Các bộ chọn hiện vẫn hoạt động, không cần sửa.", "suggestions": []}
        if not report.get("dom_file"):
            raise CliError("Selftest không lưu được cấu trúc trang, không có gì để gửi cho AI.")
        dom_file, problems = report["dom_file"], report["problems"]
        shots = [path for path in outcome.get("debug_images", []) if path.endswith("_selftest.png")]
        screenshot = shots[-1] if shots else None
    dom = read_text(dom_file)
    answer = features.suggest_selectors(engine, {"problems": problems}, dom, screenshot)
    checked = features.verify_selectors(dom, answer["suggestions"])
    return {"summary": answer["summary"], "suggestions": checked, "dropped": len(answer["suggestions"]) - len(checked),
            "dom_file": dom_file}


def cmd_assistant(engine, args, ctx):
    turns = []
    if args.history_file and os.path.exists(args.history_file):
        try:
            turns = json.loads(read_text(args.history_file))
        except ValueError:
            raise CliError("Tệp lịch sử hội thoại bị hỏng.")
    pages = [{"name": page["name"], "language": page.get("language", ""), "voice": page.get("voice", "")}
             for page in ctx["config"]["pages"]]
    result = features.assistant_turn(engine, " ".join(args.message), turns, pages, datetime.now(),
                                     [template["name"] for template in ctx["config"]["templates"]])
    if args.history_file:
        try:
            store.save_json(args.history_file, result["history"])
        except OSError as error:
            raise CliError(f"Không ghi được {args.history_file}: {error.strerror or error}")
    if args.csv and result["proposals"]:
        rows = []
        for proposal in result["proposals"]:
            rows.append((proposal["content"], "|".join(proposal["pages"]), proposal["at"]))
        write_csv(args.csv, rows)
    result.pop("history")
    result["csv"] = args.csv if args.csv and result["proposals"] else None
    return result


COMMANDS = {
    "check": cmd_check, "draft": cmd_draft, "localize": cmd_localize, "variants": cmd_variants, "polish": cmd_polish,
    "review": cmd_review, "series": cmd_series, "times": cmd_times, "reply": cmd_reply, "describe": cmd_describe,
    "card": cmd_card, "explain": cmd_explain, "selectors": cmd_selectors, "assistant": cmd_assistant,
}


def build_parser():
    parser = argparse.ArgumentParser(description="Trợ lý AI (Claude) cho việc soạn và quản lý bài đăng.")
    parser.add_argument("--json", action="store_true", help="In kết quả dạng JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    def content_args(command, positional=True):
        if positional:
            command.add_argument("content", nargs="?", default=None, help="Nội dung bài viết.")
        command.add_argument("--content-file", default=None, help="Đọc nội dung từ tệp UTF-8.")

    sub.add_parser("check", help="Kiểm tra khóa API và kết nối.")

    draft = sub.add_parser("draft", help="Soạn bài từ ý tưởng, mỗi nơi đăng một bản.")
    draft.add_argument("idea", nargs="+")
    draft.add_argument("--page", action="append", default=[], help="Tên Fanpage, URL hoặc personal (lặp lại được).")
    draft.add_argument("--tone", default="")
    draft.add_argument("--length", choices=list(features.LENGTHS), default="medium")
    draft.add_argument("--details-file", default=None, help="Tệp chứa thông tin có thật được phép dùng (giá, ngày, địa chỉ…).")
    draft.add_argument("--csv", default=None, help="Ghi các bài ra CSV để `enqueue.py import`.")
    draft.add_argument("--at", default=None, help='Giờ đăng bài đầu tiên cho CSV, "YYYY-MM-DD HH:MM".')

    localize = sub.add_parser("localize", help="Dịch và địa phương hóa một bài cho từng nơi.")
    content_args(localize)
    localize.add_argument("--page", action="append", default=[])

    variant = sub.add_parser("variants", help="Viết lại một bài thành vài bản khác nhau.")
    content_args(variant)
    variant.add_argument("--count", type=int, default=3)
    variant.add_argument("--language", default="")

    polish = sub.add_parser("polish", help="Chỉnh một bài.")
    polish.add_argument("operation", choices=list(features.POLISH_OPERATIONS))
    content_args(polish)
    polish.add_argument("--language", default="")

    review = sub.add_parser("review", help="Kiểm tra bài trước khi đăng.")
    content_args(review)
    review.add_argument("--language", default="")

    series = sub.add_parser("series", help="Lập một chuỗi bài về một chủ đề, kèm giờ đăng.")
    series.add_argument("topic", nargs="+")
    series.add_argument("--count", type=int, default=7)
    series.add_argument("--page", action="append", default=[])
    series.add_argument("--tone", default="")
    series.add_argument("--details-file", default=None)
    series.add_argument("--start", default=None, help="Ngày đăng bài đầu tiên (YYYY-MM-DD, mặc định ngày mai).")
    series.add_argument("--time", default="08:30", help="Giờ đăng mỗi ngày (HH:MM).")
    series.add_argument("--every", type=int, default=1, help="Cách nhau mấy ngày.")
    series.add_argument("--days", default=None, help="Hoặc chỉ các thứ này: mon,wed,fri.")
    series.add_argument("--csv", default=None, help="Ghi ra CSV để `enqueue.py import`.")

    times = sub.add_parser("times", help="Gợi ý giờ đăng (gợi ý chung, không dựa trên số liệu tương tác).")
    times.add_argument("--page", required=True)

    reply = sub.add_parser("reply", help="Gợi ý câu trả lời cho một bình luận bạn dán vào.")
    reply.add_argument("comment", nargs="+")
    reply.add_argument("--post-file", default=None)
    reply.add_argument("--tone", default="")
    reply.add_argument("--language", default="")

    describe = sub.add_parser("describe", help="Viết mô tả (alt text) và chú thích cho một ảnh.")
    describe.add_argument("image")
    describe.add_argument("--language", default=features.DEFAULT_LANGUAGE)
    describe.add_argument("--context", default="")

    card = sub.add_parser("card", help="Tạo ảnh chữ cho bài viết.")
    content_args(card)
    card.add_argument("--out", required=True)
    card.add_argument("--language", default="")
    card.add_argument("--style", default="")
    card.add_argument("--theme", choices=list(features.CARD_THEMES), default=None)
    card.add_argument("--size", type=int, default=1080)

    explain = sub.add_parser("explain", help="Giải thích vì sao bài gần nhất bị lỗi.")
    explain.add_argument("--job", default=None, help="Id bài trong hàng đợi (mặc định: bài lỗi mới nhất).")

    selectors = sub.add_parser("selectors", help="Facebook đổi giao diện? Chạy selftest và đề xuất bộ chọn mới.")
    selectors.add_argument("--page", default=None)
    selectors.add_argument("--account", default=None)
    selectors.add_argument("--from-file", default=None, help="Dùng tệp cấu trúc trang đã lưu thay vì chạy selftest.")

    assistant = sub.add_parser("assistant", help="Trợ lý hẹn giờ: đề xuất bài từ một câu nói.")
    assistant.add_argument("message", nargs="+")
    assistant.add_argument("--history-file", default=None, help="Lưu/đọc hội thoại để hỏi tiếp.")
    assistant.add_argument("--csv", default=None, help="Ghi các đề xuất ra CSV để `enqueue.py import`.")
    return parser


def print_text(command, data):
    def show_posts(items):
        for item in items:
            head = item.get("name") or item.get("page") or ""
            when = f" ({item['at']})" if item.get("at") else ""
            print(f"--- {head}{when}")
            print(item["content"])

    if command in ("draft", "localize", "series"):
        show_posts(data["posts"])
        if data.get("csv"):
            print(f"\nĐã ghi {data['csv']}. Xem lại rồi: python enqueue.py import {data['csv']}")
    elif command == "variants":
        for index, text in enumerate(data["variants"], start=1):
            print(f"--- Bản {index}\n{text}")
    elif command == "polish":
        print(data["text"])
        if data["notes"]:
            print(f"\n({data['notes']})")
    elif command == "review":
        print(f"Kết luận: {data['verdict']} — {data['summary']}")
        for issue in data["issues"]:
            print(f"- [{issue['kind']}] “{issue['quote']}”: {issue['problem']} → {issue['suggestion']}")
        print("\nBản đã sửa:\n" + data["improved_text"])
    elif command == "times":
        for item in data["suggestions"]:
            print(f"{item['weekday']:>4} {item['time']}  {item['reason']}")
        print(data["caveat"])
    elif command == "reply":
        for item in data["replies"]:
            print(f"- ({item['style']}) {item['text']}")
        for flag in data["flags"]:
            print(f"! {flag}")
    elif command == "describe":
        print(f"Alt text: {data['alt_text']}\nChú thích: {data['caption']}")
    elif command == "card":
        print(f"Đã vẽ {data['file']}")
    elif command == "explain":
        print(f"{data['summary']}\nNguyên nhân: {data['cause']}")
        for step in data["steps"]:
            print(f"• {step}")
        print("Thử lại được." if data["safe_to_retry"] else "Đừng đăng lại ngay.")
        if data["check_facebook"]:
            print("Hãy kiểm tra trên Facebook xem bài đã lên chưa.")
    elif command == "selectors":
        print(data["summary"])
        for item in data["suggestions"]:
            print(f"- {item['list']}: {item['value']!r}  ({item['reason']}; khớp {item['matches']} phần tử)")
        if data.get("dropped"):
            print(f"(Đã bỏ {data['dropped']} đề xuất không khớp cấu trúc trang.)")
        if data["suggestions"]:
            print("Hãy tự kiểm tra rồi thêm vào post.py; chương trình không tự sửa.")
    elif command == "assistant":
        print(data["reply"])
        for question in data["questions"]:
            print(f"? {question}")
        show_posts([{"name": ", ".join(p["pages"]), "at": p["at"], "content": p["content"]} for p in data["proposals"]])
        for warning in data["warnings"]:
            print(f"! {warning}")
        if data.get("csv"):
            print(f"\nĐã ghi {data['csv']}. Xem lại rồi: python enqueue.py import {data['csv']}")
    else:
        print(data)


def main(argv=None, base_dir=BASE_DIR, engine=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        files = enqueue.paths(base_dir)
        config = store.load_config(files["config"])
        # A missing key is reported by the first request, which also covers keys that come from an `ant auth login` profile
        engine = engine or ai_module.AI(config)
        data = COMMANDS[args.command](engine, args, {"config": config, "files": files, "base_dir": base_dir})
    except (CliError, ai_module.AIError, ValueError) as error:
        if args.json:
            print(json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False))
        else:
            print(f"Lỗi: {error}", file=sys.stderr)
        return 1
    usage = getattr(engine, "last_usage", None)
    if args.json:
        print(json.dumps({"ok": True, **data, "usage": usage}, ensure_ascii=False))
    else:
        print_text(args.command, data)
        line = engine.describe_usage() if hasattr(engine, "describe_usage") else ""
        if line:
            print(f"\n{line}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
