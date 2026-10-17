"""Claude as a helper: one place that talks to the Anthropic API for every AI feature.

The features themselves (writing, translating, reviewing, planning, explaining errors...) are in
ai_features.py; this module only sends a request and returns the answer as data.

    python ai.py check             # is the API key set up and does a request work?

The key comes from ANTHROPIC_API_KEY (or an `ant auth login` profile), or from "ai" > "api_key" in config.json.
What you send leaves your computer: the text of posts, drafts and any image you ask about are sent to Anthropic.
Nothing is sent unless you press an AI button or run an `ai_cli.py` command (or turn on "ai" > "explain_failures").
"""
import base64
import json
import os
import sys

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
# Models that accept the server-side `fallbacks` parameter (the others would reject it)
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# US dollars per million tokens (input, output) for prompts up to 100K tokens
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


class AIError(Exception):
    """Something the user can act on: no key, no network, a refused request, an unusable answer."""


def schema(properties, required=None):
    """A JSON schema object for structured output: every property required, nothing else allowed."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else list(required),
        "additionalProperties": False,
    }


def text_field(description=""):
    return {"type": "string", "description": description} if description else {"type": "string"}


def list_of(item):
    return {"type": "array", "items": item}


def estimate_cost(model, input_tokens, output_tokens):
    """Rough cost in US dollars, or None for a model without a known price."""
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def image_block(path):
    """A message content block holding the image at `path`."""
    extension = os.path.splitext(path)[1].lower()
    if extension not in IMAGE_TYPES:
        raise AIError(f"AI chỉ đọc được ảnh .png, .jpg, .gif, .webp (không đọc được video hay {extension or 'tệp này'}).")
    try:
        if os.path.getsize(path) > MAX_IMAGE_BYTES:
            raise AIError("Ảnh lớn hơn 5 MB nên không gửi cho AI được. Hãy dùng ảnh nhỏ hơn.")
        with open(path, "rb") as handle:
            data = base64.standard_b64encode(handle.read()).decode("ascii")
    except OSError as error:
        raise AIError(f"Không đọc được ảnh {path}: {error.strerror or error}")
    return {"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[extension], "data": data}}


def translate_error(error):
    """An AIError with a message the user can act on, for an exception raised by the SDK."""
    import anthropic

    # With no key at all the SDK raises a plain TypeError before sending anything
    no_key = isinstance(error, TypeError) and "authentication method" in str(error).lower()
    if no_key or isinstance(error, (anthropic.AuthenticationError, anthropic.CredentialsError)):
        return AIError("Khóa API Anthropic chưa có hoặc không hợp lệ. Đặt biến môi trường ANTHROPIC_API_KEY, hoặc nhập khóa ở "
                       "Nâng cao → Trợ lý AI (mục ai.api_key trong config.json).")
    if isinstance(error, anthropic.PermissionDeniedError):
        return AIError("Khóa API không có quyền dùng mô hình này.")
    if isinstance(error, anthropic.RateLimitError):
        retry = None
        try:
            retry = error.response.headers.get("retry-after")
        except AttributeError:
            pass
        return AIError("Đang bị giới hạn số yêu cầu" + (f"; thử lại sau {retry} giây." if retry else ", hãy thử lại sau ít phút."))
    if isinstance(error, anthropic.APITimeoutError):
        return AIError("Anthropic trả lời quá lâu. Hãy thử lại.")
    if isinstance(error, anthropic.APIConnectionError):
        return AIError("Không kết nối được tới Anthropic. Kiểm tra mạng rồi thử lại.")
    if isinstance(error, anthropic.NotFoundError):
        return AIError("Không tìm thấy mô hình đã chọn. Chọn lại mô hình ở Nâng cao → Trợ lý AI.")
    if isinstance(error, anthropic.BadRequestError):
        return AIError(f"Anthropic từ chối yêu cầu: {getattr(error, 'message', error)}")
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return AIError(f"Máy chủ Anthropic đang lỗi hoặc quá tải (mã {error.status_code}). Thử lại sau.")
        return AIError(f"Anthropic báo lỗi (mã {error.status_code}): {getattr(error, 'message', error)}")
    return AIError(f"Lỗi khi gọi Anthropic: {error}")


class AI:
    """Sends requests to Claude. `config` is the settings dict (config.json); `client` can replace the SDK client (tests)."""

    DEFAULTS = {
        "api_key": "",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "fallbacks": True,
        "voice": "",
        "explain_failures": False,
    }

    def __init__(self, config=None, client=None):
        self.settings = dict(self.DEFAULTS)
        self.settings.update((config or {}).get("ai", {}) or {})
        self._client = client
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.last_usage = None

    # -- setup --------------------------------------------------------------------------------------------

    @property
    def model(self):
        return self.settings["model"]

    @property
    def voice(self):
        return (self.settings.get("voice") or "").strip()

    def has_credentials(self):
        env = os.environ
        return bool(self.settings.get("api_key") or env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"))

    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise AIError("Chưa cài thư viện anthropic. Chạy: pip install -r requirements.txt")
            try:
                # Without an explicit key the SDK looks at ANTHROPIC_API_KEY and `ant auth login` profiles
                self._client = anthropic.Anthropic(api_key=self.settings.get("api_key") or None, max_retries=2, timeout=180.0)
            except anthropic.AnthropicError as error:
                raise translate_error(error)
        return self._client

    # -- requests -----------------------------------------------------------------------------------------

    def ask(self, system, user, output_schema, images=(), effort=None, max_tokens=16000, history=()):
        """Ask for an answer that fits `output_schema`; returns it as a dict.

        `images` are file paths shown to Claude before the text. `history` is earlier turns as
        [{"role": "user"|"assistant", "content": text}], oldest first.
        """
        effort = effort or self.settings["effort"]
        if effort not in EFFORTS:
            raise AIError(f"Mức effort không hợp lệ: {effort}.")
        content = [image_block(path) for path in images] + [{"type": "text", "text": user}]
        messages = [dict(turn) for turn in history] + [{"role": "user", "content": content if images else user}]
        request = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": output_schema}},
        }
        client = self.client()
        try:
            if self.settings.get("fallbacks") and self.model in FALLBACK_MODELS:
                # If a safeguard declines the request, the API retries it on another model inside the same call
                response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
            else:
                response = client.messages.create(**request)
        except AIError:
            raise
        except Exception as error:  # noqa: BLE001 - every SDK error is turned into a readable AIError
            raise translate_error(error)
        self._count(response)
        return self._read(response)

    def _count(self, response):
        usage = getattr(response, "usage", None)
        used_in = int(getattr(usage, "input_tokens", 0) or 0)
        used_out = int(getattr(usage, "output_tokens", 0) or 0)
        self.calls += 1
        self.input_tokens += used_in
        self.output_tokens += used_out
        self.last_usage = {
            "input_tokens": used_in,
            "output_tokens": used_out,
            "cost_usd": estimate_cost(getattr(response, "model", None) or self.model, used_in, used_out),
        }

    @staticmethod
    def _read(response):
        reason = getattr(response, "stop_reason", None)
        if reason == "refusal":
            raise AIError("Claude từ chối yêu cầu này. Hãy sửa lại nội dung rồi thử lại.")
        if reason == "max_tokens":
            raise AIError("Câu trả lời của AI bị cắt giữa chừng. Hãy chia nhỏ yêu cầu (ít nơi đăng hơn, ngắn hơn).")
        text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), None)
        if text is None:
            raise AIError("AI không trả về nội dung nào.")
        try:
            data = json.loads(text)
        except ValueError:
            raise AIError("AI trả về câu trả lời không đọc được. Hãy thử lại.")
        if not isinstance(data, dict):
            raise AIError("AI trả về câu trả lời sai dạng. Hãy thử lại.")
        return data

    def total_cost(self):
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)

    def describe_usage(self):
        """A short line about the last request: tokens and rough cost."""
        usage = self.last_usage
        if not usage:
            return ""
        cost = f", khoảng ${usage['cost_usd']:.4f}" if usage["cost_usd"] is not None else ""
        return f"AI: {usage['input_tokens']} token vào, {usage['output_tokens']} token ra{cost}"


def check(ai):
    """Make one small request. Returns the answer's text; raises AIError."""
    answer = ai.ask(
        "You answer in one short sentence.",
        "Reply with the single word OK in the field `reply`.",
        schema({"reply": text_field()}),
        effort="low",
        max_tokens=2000,
    )
    return answer["reply"]


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    import store

    config = store.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    ai = AI(config)
    try:
        reply = check(ai)
    except AIError as error:
        print(f"Lỗi: {error}")
        return 1
    print(f"Kết nối được với {ai.model}: {reply}")
    print(ai.describe_usage())
    return 0


if __name__ == "__main__":
    sys.exit(main())
