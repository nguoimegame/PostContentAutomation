"""MCP server: lets an AI agent list pages, preview, post and schedule through this project's tools.

    python mcp_server.py [--base-dir FOLDER]

Speaks the Model Context Protocol over stdin/stdout (one JSON message per line); no extra packages needed.
To use it from Claude Code the project's .mcp.json already points here.

Anything that publishes (post_now, schedule_post, edit_job) needs `confirm: true`, and the tool descriptions
tell the agent to ask the user first. Posting goes through post.py, so the duplicate and rate limits of
spam.py, the history and the debug screenshots work exactly as from the command line. The Facebook login is
never readable through these tools: only whether a session exists.
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime

import daemon
import enqueue
import history
import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_NAME = "facebook-auto-poster"
SERVER_VERSION = "1.0.0"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
POST_TIMEOUT_SECONDS = 20 * 60

INSTRUCTIONS = (
    "Tools for publishing to Facebook pages and groups. Posting is public and cannot be undone with these tools: "
    "always show the user the text, the destination and any attachments, try dry_run_post first, and call post_now, "
    "schedule_post or edit_job with confirm=true only after the user clearly agrees. If a result says the session "
    "expired, ask the user to log in again (python login.py); never try to log in for them. A post can end up "
    "published even when a tool reports a failure after the Post button was pressed: check the page or the history "
    "before trying again."
)

CONFIRM = {"type": "boolean", "description": "Must be true. Set it only after the user has approved this exact action."}
IMAGES = {"type": "array", "items": {"type": "string"}, "description": "Full paths of images or videos to attach."}
PAGE = {"type": "string", "description": 'A page name from list_pages, a facebook.com URL, or "personal" for the profile.'}
ACCOUNT = {"type": "string", "description": "Extra account name from list_pages (empty: the default login)."}


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


class ToolError(Exception):
    """A problem the agent can read and act on (shown as a tool result with isError)."""


class Server:
    def __init__(self, base_dir=BASE_DIR, python_exe=None, run_process=subprocess.run, now=datetime.now):
        self.base_dir = base_dir
        self.python_exe = python_exe or sys.executable
        self.run_process = run_process
        self.now = now
        self.tools = self._tools()

    # -- tool table ---------------------------------------------------------------------------------------

    def _tools(self):
        def tool(name, title, description, properties, required, handler, read_only=False, destructive=False):
            return {
                "name": name,
                "title": title,
                "description": description,
                "inputSchema": schema(properties, required),
                "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive, "openWorldHint": not read_only},
                "handler": handler,
            }

        return {item["name"]: item for item in [
            tool("list_pages", "List pages and logins",
                 "The Facebook pages/groups saved in config.json, the saved templates and whether each account is logged in.",
                 {}, [], self.list_pages, read_only=True),
            tool("dry_run_post", "Preview a post (does not publish)",
                 "Opens Facebook, writes the post and takes a screenshot, but does not press the Post button. "
                 "Reports warnings about duplicates and limits. Use it to check a post before post_now.",
                 {"content": {"type": "string"}, "page": PAGE, "images": IMAGES, "account": ACCOUNT}, ["page"],
                 self.dry_run_post),
            tool("post_now", "Publish a post now",
                 "PUBLISHES a public post immediately. Ask the user to approve the text, destination and files first, "
                 "then pass confirm=true. Refused (status blocked) when the text duplicates a recent post or a daily limit "
                 "is reached, unless allow_duplicate is set.",
                 {"content": {"type": "string"}, "page": PAGE, "images": IMAGES, "account": ACCOUNT,
                  "allow_duplicate": {"type": "boolean"}, "confirm": CONFIRM}, ["page", "confirm"],
                 self.post_now, destructive=True),
            tool("schedule_post", "Schedule a post",
                 "Adds a post to the queue; it is published at that time while app.py or daemon.py is running. "
                 "Publishes publicly later, so ask the user first and pass confirm=true. In the text, {page}, {language}, "
                 "{date}, {time}, {weekday} are filled in per page and {a|b|c} picks one at random.",
                 {"content": {"type": "string"}, "pages": {"type": "array", "items": PAGE, "minItems": 1},
                  "at": {"type": "string", "description": "YYYY-MM-DD HH:MM, local time"}, "images": IMAGES,
                  "repeat": {"type": "string", "description": "none, daily, weekly, weekdays, monthly or days:mon,wed,fri"},
                  "jitter_minutes": {"type": "integer", "minimum": 0}, "gap_minutes": {"type": "number", "minimum": 0},
                  "account": ACCOUNT, "allow_duplicate": {"type": "boolean"}, "confirm": CONFIRM},
                 ["content", "pages", "at", "confirm"], self.schedule_post, destructive=True),
            tool("list_queue", "List scheduled posts", "The posts in the queue with status, time, destination and link.",
                 {"status": {"type": "string", "enum": ["pending", "running", "done", "failed", "missed"]}}, [],
                 self.list_queue, read_only=True),
            tool("edit_job", "Edit a scheduled post",
                 "Changes a queued post (time, text, page, files, repeat). It then waits for its time again. "
                 "Changes what will be published, so ask the user first and pass confirm=true.",
                 {"job_id": {"type": "string"}, "content": {"type": "string"}, "page": PAGE, "at": {"type": "string"},
                  "images": IMAGES, "repeat": {"type": "string"}, "jitter_minutes": {"type": "integer", "minimum": 0},
                  "account": ACCOUNT, "confirm": CONFIRM}, ["job_id", "confirm"], self.edit_job, destructive=True),
            tool("remove_job", "Remove a scheduled post", "Deletes a post from the queue.",
                 {"job_id": {"type": "string"}}, ["job_id"], self.remove_job, destructive=True),
            tool("get_history", "Recent post attempts",
                 "What was posted, failed or blocked recently, with links. Check it before posting again after a failure.",
                 {"limit": {"type": "integer", "minimum": 1, "maximum": 200}, "status": {"type": "string"}}, [],
                 self.get_history, read_only=True),
            tool("selftest", "Check that Facebook's page still matches the selectors",
                 "Opens Facebook and reports which selectors still work, without posting. Run it when posts keep failing.",
                 {"page": PAGE, "account": ACCOUNT}, [], self.selftest),
            tool("daemon_status", "Is the background runner running?",
                 "Whether daemon.py is running and how many posts wait.", {}, [], self.daemon_status, read_only=True),
        ]}

    # -- helpers ------------------------------------------------------------------------------------------

    def files(self):
        return enqueue.paths(self.base_dir)

    def config(self):
        return store.load_config(self.files()["config"])

    def page_url(self, value):
        try:
            return enqueue.resolve_page(value, self.config())[0]
        except enqueue.QueueError as error:
            raise ToolError(error.message)

    @staticmethod
    def require_confirm(args, what):
        if args.get("confirm") is not True:
            raise ToolError(
                f"Chưa được xác nhận: {what} là việc công khai. Hãy cho người dùng xem nội dung và nơi đăng, hỏi họ đồng ý, "
                "rồi gọi lại với confirm=true. (Not confirmed: ask the user first, then call again with confirm=true.)"
            )

    def run_post(self, extra, content=None, timeout=POST_TIMEOUT_SECONDS):
        """Run post.py with --json and return its parsed result."""
        command = [self.python_exe, "-u", os.path.join(BASE_DIR, "post.py"), "--json"] + extra
        content_file = None
        try:
            if content is not None:
                handle, content_file = tempfile.mkstemp(prefix="fbpost_", suffix=".txt")
                with os.fdopen(handle, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                command += ["--content-file", content_file]
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            try:
                done = self.run_process(command, cwd=BASE_DIR, env=env, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=timeout)
            except subprocess.TimeoutExpired:
                raise ToolError("post.py chạy quá lâu và đã bị dừng. Bài có thể đã được đăng: hãy kiểm tra get_history.")
            except OSError as error:
                raise ToolError(f"Không chạy được post.py: {error}")
        finally:
            if content_file:
                with contextlib.suppress(OSError):
                    os.remove(content_file)
        lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
        try:
            result = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise ToolError(f"post.py không trả về kết quả (mã thoát {done.returncode}). {(done.stderr or '')[-600:]}")
        result["tail_of_log"] = (done.stderr or "").strip().splitlines()[-8:]
        return result

    def post_args(self, args):
        url = self.page_url(args["page"])
        extra = ["--page", url] if url else []
        for path in args.get("images") or []:
            extra += ["--image", path]
        if args.get("account"):
            extra += ["--account", args["account"]]
        return extra

    def call_enqueue(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            enqueue.main(["--json"] + argv, base_dir=self.base_dir, now=self.now())
        try:
            result = json.loads(out.getvalue())
        except ValueError:
            raise ToolError("enqueue.py không trả về kết quả hợp lệ.")
        if not result.get("ok"):
            raise ToolError(result.get("message", "Lỗi không rõ."))
        return result

    # -- tools --------------------------------------------------------------------------------------------

    def list_pages(self, args):
        config = self.config()
        accounts = [{"name": "default", "logged_in": store.session_saved_at(store.state_path_for(self.base_dir)) is not None}]
        for name in config["accounts"]:
            accounts.append({"name": name, "logged_in": store.session_saved_at(store.state_path_for(self.base_dir, name)) is not None})
        return {"pages": config["pages"], "templates": [t["name"] for t in config["templates"]], "accounts": accounts,
                "page_gap_minutes": config["page_gap_minutes"]}

    def dry_run_post(self, args):
        if not (args.get("content") or args.get("images")):
            raise ToolError("Cần content hoặc images.")
        return self.run_post(["--dry-run"] + self.post_args(args), args.get("content") or "")

    def post_now(self, args):
        self.require_confirm(args, "đăng bài ngay")
        if not (args.get("content") or args.get("images")):
            raise ToolError("Cần content hoặc images.")
        extra = self.post_args(args) + (["--allow-duplicate"] if args.get("allow_duplicate") else [])
        return self.run_post(extra, args.get("content") or "")

    def schedule_post(self, args):
        self.require_confirm(args, "hẹn giờ đăng bài")
        argv = ["add", args["content"], "--at", args["at"]]
        for page in args["pages"]:
            argv += ["--page", page]
        for path in args.get("images") or []:
            argv += ["--image", path]
        for flag, key in (("--repeat", "repeat"), ("--account", "account")):
            if args.get(key):
                argv += [flag, args[key]]
        if args.get("jitter_minutes"):
            argv += ["--jitter", str(args["jitter_minutes"])]
        if args.get("gap_minutes") is not None:
            argv += ["--gap", str(args["gap_minutes"])]
        if args.get("allow_duplicate"):
            argv.append("--allow-duplicate")
        result = self.call_enqueue(argv)
        result["note"] = "Bài chỉ được đăng khi app.py hoặc daemon.py đang chạy (xem daemon_status)."
        return result

    def list_queue(self, args):
        argv = ["list"] + (["--status", args["status"]] if args.get("status") else [])
        return self.call_enqueue(argv)

    def edit_job(self, args):
        self.require_confirm(args, "sửa bài hẹn giờ")
        argv = ["edit", args["job_id"], "--no-render"]
        if args.get("content") is not None:
            argv.insert(2, args["content"])
        for flag, key in (("--page", "page"), ("--at", "at"), ("--repeat", "repeat"), ("--account", "account")):
            if args.get(key):
                argv += [flag, args[key]]
        for path in args.get("images") or []:
            argv += ["--image", path]
        if args.get("jitter_minutes") is not None:
            argv += ["--jitter", str(args["jitter_minutes"])]
        return self.call_enqueue(argv)

    def remove_job(self, args):
        return self.call_enqueue(["remove", args["job_id"]])

    def get_history(self, args):
        records = history.load(self.files()["history"])
        if args.get("status"):
            records = [record for record in records if record.get("status") == args["status"]]
        return {"records": records[-int(args.get("limit") or 20):]}

    def selftest(self, args):
        extra = ["--selftest"]
        if args.get("page"):
            url = self.page_url(args["page"])
            extra += ["--page", url] if url else []
        if args.get("account"):
            extra += ["--account", args["account"]]
        return self.run_post(extra, timeout=300)

    def daemon_status(self, args):
        return daemon.status(self.base_dir)

    # -- protocol -----------------------------------------------------------------------------------------

    def tool_list(self):
        return [{key: value for key, value in tool.items() if key != "handler"} for tool in self.tools.values()]

    def call_tool(self, name, arguments):
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"Không có công cụ “{name}”.")
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            raise ToolError("arguments phải là một object.")
        if "confirm" in tool["inputSchema"]["required"] and arguments.get("confirm") is not True:
            self.require_confirm(arguments, tool["title"].lower())
        self.validate(tool["inputSchema"], arguments)
        return tool["handler"](arguments)

    @staticmethod
    def validate(spec, arguments):
        properties = spec["properties"]
        for key in spec["required"]:
            if key not in arguments:
                raise ToolError(f"Thiếu tham số bắt buộc: {key}.")
        for key, value in arguments.items():
            if key not in properties:
                raise ToolError(f"Tham số không hợp lệ: {key}.")
            kind = properties[key].get("type")
            ok = {
                "string": isinstance(value, str),
                "boolean": isinstance(value, bool),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "array": isinstance(value, list) and all(isinstance(item, str) for item in value),
            }.get(kind, True)
            if not ok:
                raise ToolError(f"Tham số {key} phải có kiểu {kind}.")
            if "enum" in properties[key] and value not in properties[key]["enum"]:
                raise ToolError(f"Tham số {key} phải là một trong: {', '.join(properties[key]['enum'])}.")
            if kind in ("integer", "number") and value < properties[key].get("minimum", value):
                raise ToolError(f"Tham số {key} không được nhỏ hơn {properties[key]['minimum']}.")
            if kind == "array" and len(value) < properties[key].get("minItems", 0):
                raise ToolError(f"Tham số {key} cần ít nhất {properties[key]['minItems']} phần tử.")

    def handle(self, message):
        """The response to one JSON-RPC message, or None for a notification."""
        if isinstance(message, list):
            responses = [response for response in (self.handle(item) for item in message) if response is not None]
            return responses or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return self.error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
        method, request_id = message["method"], message.get("id")
        params = message.get("params") or {}
        if "id" not in message:
            return None  # a notification (e.g. notifications/initialized): nothing to answer
        try:
            if method == "initialize":
                wanted = params.get("protocolVersion")
                return self.result(request_id, {
                    "protocolVersion": wanted if wanted in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": INSTRUCTIONS,
                })
            if method == "ping":
                return self.result(request_id, {})
            if method == "tools/list":
                return self.result(request_id, {"tools": self.tool_list()})
            if method == "tools/call":
                if not isinstance(params.get("name"), str):
                    return self.error(request_id, -32602, "Missing tool name")
                try:
                    data = self.call_tool(params["name"], params.get("arguments"))
                    ok = data.get("ok", True) is not False if isinstance(data, dict) else True
                    return self.result(request_id, self.content(data, is_error=not ok))
                except ToolError as error:
                    return self.result(request_id, self.content({"ok": False, "message": str(error)}, is_error=True))
            return self.error(request_id, -32601, f"Method not found: {method}")
        except Exception as error:  # noqa: BLE001 - a bug in a tool must not take the server down
            return self.result(request_id, self.content({"ok": False, "message": f"Lỗi nội bộ: {type(error).__name__}: {error}"}, is_error=True))

    @staticmethod
    def content(data, is_error=False):
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}], "isError": is_error}

    @staticmethod
    def result(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def serve(self, stdin=None, stdout=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                response = self.error(None, -32700, "Parse error")
            else:
                response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="MCP server cho Facebook Auto Poster (stdio).")
    parser.add_argument("--base-dir", default=BASE_DIR, help="Thư mục chứa config.json, queue.json… (mặc định: thư mục dự án).")
    args = parser.parse_args(argv)
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    Server(base_dir=os.path.abspath(args.base_dir)).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
