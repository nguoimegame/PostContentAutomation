import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2 as httpx

import ai

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def reply(data, stop_reason="end_turn", input_tokens=1000, output_tokens=500, model="claude-opus-5-5", text=None):
    block = SimpleNamespace(type="text", text=json.dumps(data) if text is None else text)
    return SimpleNamespace(
        content=[block], stop_reason=stop_reason, model=model,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeMessages:
    def __init__(self):
        self.calls = []
        self.queue = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0) if self.queue else reply({"reply": "OK"})
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()
        self.beta = SimpleNamespace(messages=FakeMessages())

    def reply_with(self, *items, beta=False):
        (self.beta.messages if beta else self.messages).queue.extend(items)


SCHEMA = ai.schema({"reply": ai.text_field()})


def error(cls, status=400, headers=None):
    return cls("lỗi thử", response=httpx.Response(status, request=REQUEST, headers=headers or {}), body=None)


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.ai = ai.AI({"ai": {"model": "claude-haiku-5-5"}}, client=self.client)

    def last(self):
        return self.client.messages.calls[-1]

    def test_the_answer_is_returned_as_data(self):
        self.client.reply_with(reply({"reply": "xin chào"}))
        self.assertEqual(self.ai.ask("hệ thống", "câu hỏi", SCHEMA), {"reply": "xin chào"})

    def test_the_request_asks_for_a_schema_and_an_effort_and_nothing_removed(self):
        self.ai.ask("hệ thống", "câu hỏi", SCHEMA, effort="low")
        sent = self.last()
        self.assertEqual(sent["model"], "claude-haiku-5-5")
        self.assertEqual(sent["system"], "hệ thống")
        self.assertEqual(sent["messages"], [{"role": "user", "content": "câu hỏi"}])
        self.assertEqual(sent["output_config"], {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
        # Sampling settings and a thinking budget are rejected by current models
        for name in ("temperature", "top_p", "top_k", "thinking", "budget_tokens"):
            self.assertNotIn(name, sent)
        self.assertGreaterEqual(sent["max_tokens"], 4000)

    def test_the_default_effort_comes_from_the_settings(self):
        self.ai.settings["effort"] = "high"
        self.ai.ask("s", "u", SCHEMA)
        self.assertEqual(self.last()["output_config"]["effort"], "high")

    def test_an_unknown_effort_is_refused_before_anything_is_sent(self):
        with self.assertRaises(ai.AIError):
            self.ai.ask("s", "u", SCHEMA, effort="siêu")
        self.assertEqual(self.client.messages.calls, [])

    def test_earlier_turns_come_first(self):
        history = [{"role": "user", "content": "một"}, {"role": "assistant", "content": "hai"}]
        self.ai.ask("s", "ba", SCHEMA, history=history)
        self.assertEqual([m["content"] for m in self.last()["messages"]], ["một", "hai", "ba"])
        self.assertEqual([m["role"] for m in self.last()["messages"]], ["user", "assistant", "user"])

    def test_images_are_sent_before_the_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "anh.png")
            with open(path, "wb") as handle:
                handle.write(b"\x89PNG-gia")
            self.ai.ask("s", "mô tả", SCHEMA, images=[path])
        content = self.last()["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(content[0]["source"]["data"]), b"\x89PNG-gia")
        self.assertEqual(content[1], {"type": "text", "text": "mô tả"})


class FallbackTest(unittest.TestCase):
    def ask(self, model, **settings):
        client = FakeClient()
        client.reply_with(reply({"reply": "OK"}), beta=True)
        client.reply_with(reply({"reply": "OK"}))
        ai.AI({"ai": {"model": model, **settings}}, client=client).ask("s", "u", SCHEMA)
        return client

    def test_models_that_support_it_use_the_beta_with_fallbacks(self):
        client = self.ask("claude-opus-5-5")
        self.assertEqual(client.messages.calls, [])
        sent = client.beta.messages.calls[0]
        self.assertEqual(sent["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(sent["fallbacks"], "default")
        self.assertEqual(sent["output_config"]["format"]["type"], "json_schema")

    def test_it_can_be_turned_off(self):
        client = self.ask("claude-opus-5-5", fallbacks=False)
        self.assertEqual(client.beta.messages.calls, [])
        self.assertEqual(len(client.messages.calls), 1)
        self.assertNotIn("fallbacks", client.messages.calls[0])

    def test_models_without_it_use_the_plain_endpoint(self):
        client = self.ask("claude-haiku-5-5")
        self.assertEqual(client.beta.messages.calls, [])
        self.assertEqual(len(client.messages.calls), 1)


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.ai = ai.AI({"ai": {"model": "claude-haiku-5-5"}}, client=self.client)

    def ask(self, response):
        self.client.reply_with(response)
        return self.ai.ask("s", "u", SCHEMA)

    def test_a_refusal_is_explained(self):
        with self.assertRaises(ai.AIError) as caught:
            self.ask(reply({}, stop_reason="refusal"))
        self.assertIn("từ chối", str(caught.exception))

    def test_a_cut_off_answer_is_explained(self):
        with self.assertRaises(ai.AIError) as caught:
            self.ask(reply({"reply": "dở"}, stop_reason="max_tokens"))
        self.assertIn("bị cắt", str(caught.exception))

    def test_unreadable_answers_are_errors(self):
        for response in (reply(None, text="không phải json"), reply(None, text="[1, 2]"),
                         SimpleNamespace(content=[], stop_reason="end_turn", usage=None, model="x")):
            with self.subTest(response=response), self.assertRaises(ai.AIError):
                self.ask(response)

    def test_a_block_that_is_not_text_is_skipped(self):
        response = reply({"reply": "ổn"})
        response.content.insert(0, SimpleNamespace(type="thinking", thinking=""))
        self.assertEqual(self.ask(response), {"reply": "ổn"})


class UsageTest(unittest.TestCase):
    def test_tokens_are_added_up_and_priced(self):
        client = FakeClient()
        client.reply_with(reply({"reply": "a"}, input_tokens=1_000_000, output_tokens=100_000, model="claude-opus-5-5"),
                          beta=True)
        client.reply_with(reply({"reply": "b"}, input_tokens=2000, output_tokens=1000, model="claude-opus-5-5"), beta=True)
        engine = ai.AI({}, client=client)
        engine.ask("s", "u", SCHEMA)
        self.assertEqual(engine.last_usage["cost_usd"], 4.0 + 2.0)
        engine.ask("s", "u", SCHEMA)
        self.assertEqual((engine.calls, engine.input_tokens, engine.output_tokens), (2, 1_002_000, 101_000))
        self.assertIn("2000 token vào, 1000 token ra", engine.describe_usage())

    def test_prices_of_the_models_offered(self):
        self.assertAlmostEqual(ai.estimate_cost("claude-haiku-5-5", 1_000_000, 1_000_000), 0.6)
        self.assertAlmostEqual(ai.estimate_cost("claude-sonnet-5-5", 500_000, 100_000), 2.0)
        self.assertIsNone(ai.estimate_cost("mô-hình-lạ", 1, 1))

    def test_an_unknown_model_has_no_cost_in_the_summary(self):
        client = FakeClient()
        client.reply_with(reply({"reply": "a"}, model="mô-hình-lạ"))
        engine = ai.AI({"ai": {"model": "mô-hình-lạ", "fallbacks": False}}, client=client)
        engine.ask("s", "u", SCHEMA)
        self.assertNotIn("$", engine.describe_usage())

    def test_nothing_to_describe_before_a_request(self):
        self.assertEqual(ai.AI({}, client=FakeClient()).describe_usage(), "")


class ErrorTest(unittest.TestCase):
    def failing(self, exception):
        client = FakeClient()
        client.reply_with(exception, beta=True)
        client.reply_with(exception)
        with self.assertRaises(ai.AIError) as caught:
            ai.AI({}, client=client).ask("s", "u", SCHEMA)
        return str(caught.exception)

    def test_each_kind_of_failure_is_explained(self):
        cases = [
            (error(anthropic.AuthenticationError, 401), "Khóa API"),
            (error(anthropic.PermissionDeniedError, 403), "quyền"),
            (error(anthropic.RateLimitError, 429, {"retry-after": "7"}), "7 giây"),
            (error(anthropic.RateLimitError, 429), "giới hạn"),
            (error(anthropic.NotFoundError, 404), "mô hình"),
            (error(anthropic.BadRequestError, 400), "từ chối yêu cầu"),
            (error(anthropic.InternalServerError, 500), "quá tải"),
            (error(anthropic.APIStatusError, 418), "418"),
            (anthropic.APITimeoutError(request=REQUEST), "quá lâu"),
            (anthropic.APIConnectionError(request=REQUEST), "kết nối"),
            (anthropic.AnthropicError("lạ"), "lạ"),
            (TypeError('"Could not resolve authentication method. Expected one of api_key..."'), "Khóa API"),
        ]
        for exception, expected in cases:
            with self.subTest(exception=type(exception).__name__, expected=expected):
                self.assertIn(expected, self.failing(exception))

    def test_a_missing_key_is_explained_before_a_request(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                os.environ.pop(name, None)
            self.assertFalse(ai.AI({}).has_credentials())
            self.assertTrue(ai.AI({"ai": {"api_key": "sk-x"}}).has_credentials())
            os.environ["ANTHROPIC_API_KEY"] = "sk-env"
            self.assertTrue(ai.AI({}).has_credentials())

    def test_the_key_from_the_settings_reaches_the_sdk(self):
        with mock.patch.object(anthropic, "Anthropic") as sdk:
            ai.AI({"ai": {"api_key": "sk-cấu-hình"}}).client()
        self.assertEqual(sdk.call_args.kwargs["api_key"], "sk-cấu-hình")

    def test_without_a_key_in_the_settings_the_sdk_looks_in_the_environment(self):
        with mock.patch.object(anthropic, "Anthropic") as sdk:
            ai.AI({}).client()
        self.assertIsNone(sdk.call_args.kwargs["api_key"])


class ImageTest(unittest.TestCase):
    def test_supported_types_are_labelled(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, media in (("a.png", "image/png"), ("b.JPG", "image/jpeg"), ("c.jpeg", "image/jpeg"),
                                ("d.gif", "image/gif"), ("e.webp", "image/webp")):
                path = os.path.join(folder, name)
                with open(path, "wb") as handle:
                    handle.write(b"x")
                self.assertEqual(ai.image_block(path)["source"]["media_type"], media)

    def test_videos_and_other_files_are_refused(self):
        for name in ("clip.mp4", "tai-lieu.pdf", "khong-duoi"):
            with self.subTest(name=name), self.assertRaises(ai.AIError):
                ai.image_block(name)

    def test_big_and_missing_images_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            big = os.path.join(folder, "lon.png")
            with open(big, "wb") as handle:
                handle.write(b"x" * (ai.MAX_IMAGE_BYTES + 1))
            with self.assertRaises(ai.AIError) as caught:
                ai.image_block(big)
            self.assertIn("5 MB", str(caught.exception))
            with self.assertRaises(ai.AIError):
                ai.image_block(os.path.join(folder, "khong-co.png"))


class SchemaTest(unittest.TestCase):
    def test_every_property_is_required_and_nothing_else_is_allowed(self):
        built = ai.schema({"a": ai.text_field("mô tả"), "b": ai.list_of(ai.text_field())})
        self.assertEqual(built["required"], ["a", "b"])
        self.assertIs(built["additionalProperties"], False)
        self.assertEqual(built["properties"]["a"], {"type": "string", "description": "mô tả"})
        self.assertEqual(built["properties"]["b"], {"type": "array", "items": {"type": "string"}})

    def test_some_properties_can_be_left_optional(self):
        self.assertEqual(ai.schema({"a": ai.text_field(), "b": ai.text_field()}, required=["a"])["required"], ["a"])


class CheckTest(unittest.TestCase):
    def test_check_makes_one_small_request(self):
        client = FakeClient()
        client.reply_with(reply({"reply": "OK"}), beta=True)
        engine = ai.AI({}, client=client)
        self.assertEqual(ai.check(engine), "OK")
        self.assertEqual(engine.calls, 1)
        self.assertEqual(client.beta.messages.calls[0]["output_config"]["effort"], "low")

    def test_main_explains_a_missing_key(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False), contextlib.redirect_stdout(out):
            for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                os.environ.pop(name, None)
            with mock.patch("store.load_config", return_value={"ai": {}}):
                self.assertEqual(ai.main([]), 1)
        self.assertIn("ANTHROPIC_API_KEY", out.getvalue())

    def test_a_missing_key_is_found_out_by_the_sdk_before_anything_is_sent(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                os.environ.pop(name, None)
            with self.assertRaises(ai.AIError) as caught:
                ai.check(ai.AI({"ai": {"model": "claude-haiku-5-5"}}))
        self.assertIn("ANTHROPIC_API_KEY", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
