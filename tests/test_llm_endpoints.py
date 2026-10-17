"""LLM 接入层回归测试（无网络）。

覆盖 2026-10 核对过的三处现实约束：

1. **思维链必须回传**：DeepSeek V4 起思考模式默认开启，规则是「请求带 tools 时，
   历史轮次的 ``reasoning_content`` 均须回传，否则返回 400」。本项目每个 Agent
   都靠工具推进，所以 assistant 消息必须带 ``reasoning_content``——这正是
   「AI 模式一开局就失败」的根因。
2. **地址要能整形**：用户常把整段端点（``.../v1/chat/completions``）粘进「API 地址」，
   原样拼 /chat/completions 会 404。
3. **模型名不能写死**：真实可用型号由账号决定，界面用 ``GET/POST /api/models``
   现拉；预设只是默认值。
"""

import io
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (WebServerCase, strip_llm_env, restore_env)  # noqa: E402

from trpg.config import LLMConfig, LLM_PROVIDERS                  # noqa: E402
from trpg.harness import adapter as A                            # noqa: E402
from trpg.harness.adapter import (OpenAICompatibleAdapter,       # noqa: E402
                                  ScriptedAdapter, normalize_base_url,
                                  sanitize_tool_messages)
from trpg.harness.loop import TruncateCompactor                  # noqa: E402
from trpg.harness.events import Session                          # noqa: E402
from trpg.harness.loop import Agent, DefaultAgentLoop            # noqa: E402
from trpg.harness.models import ModelResponse, ToolCall, assistant  # noqa: E402
from trpg.harness.registry import ToolRegistry                   # noqa: E402


# ================================================================ 端点整形
class TestNormalizeBaseUrl(unittest.TestCase):
    def test_strips_spaces_and_trailing_slash(self):
        self.assertEqual(normalize_base_url("  https://api.deepseek.com/  "),
                         "https://api.deepseek.com")

    def test_strips_pasted_endpoint(self):
        self.assertEqual(
            normalize_base_url("https://api.deepseek.com/v1/chat/completions"),
            "https://api.deepseek.com/v1")
        self.assertEqual(
            normalize_base_url("https://open.bigmodel.cn/api/paas/v4/chat/completions"),
            "https://open.bigmodel.cn/api/paas/v4")

    def test_strips_pasted_responses_endpoint(self):
        # 用户可能把 Responses 端点整段粘进来：不能拼成 /responses/responses
        self.assertEqual(
            normalize_base_url("https://api.deepseek.com/v1/responses"),
            "https://api.deepseek.com/v1")
        self.assertEqual(
            normalize_base_url("https://api.deepseek.com/responses/"),
            "https://api.deepseek.com")

    def test_keeps_path_prefix(self):
        # /v1 与 /api/paas/v4 属于路径前缀，不能当端点尾巴切掉
        self.assertEqual(normalize_base_url("https://api.moonshot.cn/v1"),
                         "https://api.moonshot.cn/v1")
        self.assertEqual(normalize_base_url("https://api.deepseek.com"),
                         "https://api.deepseek.com")

    def test_adapter_normalizes_on_construction(self):
        ad = OpenAICompatibleAdapter("https://api.deepseek.com/v1/chat/completions/", "k")
        self.assertEqual(ad.base_url, "https://api.deepseek.com/v1")


# ================================================================ 思考模式
class TestThinkingPayload(unittest.TestCase):
    def test_thinking_off_sends_disabled(self):
        ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k", thinking="off")
        self.assertEqual(ad._extras(), {"thinking": {"type": "disabled"}})

    def test_thinking_on_with_effort(self):
        ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k",
                                     thinking="on", reasoning_effort="high")
        self.assertEqual(ad._extras(),
                         {"thinking": {"type": "enabled"}, "reasoning_effort": "high"})

    def test_no_key_when_unset(self):
        ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k")
        self.assertEqual(ad._extras(), {})


class _CapturingAdapter:
    """记录每次请求的消息，第二轮返回终结回答。"""

    def __init__(self, reasoning="我想了想：先观察再动手。"):
        self.reasoning = reasoning
        self.calls: list[list[dict]] = []

    def chat(self, model, messages, tools, temperature, max_tokens):
        self.calls.append([dict(m) for m in messages])
        if len(self.calls) == 1:
            return ModelResponse(content=None, tool_calls=[ToolCall("c1", "probe", {})],
                                 finish_reason="tool_calls", reasoning=self.reasoning)
        return ModelResponse(content="完成", finish_reason="stop",
                             reasoning=self.reasoning)


class TestReasoningRoundTrip(unittest.TestCase):
    """DeepSeek 的硬性要求：带 tools 的后续请求必须回传思维链，否则 400。"""

    def _run(self):
        registry = ToolRegistry()
        registry.register("probe", "空工具", {"type": "object", "properties": {}},
                          lambda **kw: {"ok": True})
        adapter = _CapturingAdapter()
        loop = DefaultAgentLoop(registry=registry, adapter=adapter)
        agent = Agent(agent_id="dm", name="DM", system_prompt="你是 DM",
                      tool_names=["probe"])
        session = Session("t")
        result = loop.run(agent, session, "开工")
        return adapter, session, result

    def test_second_request_carries_reasoning_content(self):
        adapter, session, result = self._run()
        self.assertEqual(result.content, "完成")
        self.assertEqual(len(adapter.calls), 2)
        second = adapter.calls[1]
        assistants = [m for m in second if m["role"] == "assistant"]
        self.assertTrue(assistants, "第二轮必须包含上一轮的 assistant 消息")
        self.assertEqual(assistants[0].get("reasoning_content"),
                         "我想了想：先观察再动手。")
        # 事件日志同口径：模型可见 ⟺ 已记录
        logged = [e["message"] for e in session.events
                  if e["type"] == "message" and e["message"]["role"] == "assistant"]
        self.assertEqual(logged[0].get("reasoning_content"), "我想了想：先观察再动手。")

    def test_assistant_helper_omits_empty_reasoning(self):
        msg = assistant("正文", None)
        self.assertNotIn("reasoning_content", msg)
        msg2 = assistant("正文", None, "")
        self.assertNotIn("reasoning_content", msg2)
        msg3 = assistant("正文", None, "思考")
        self.assertEqual(msg3["reasoning_content"], "思考")
        raw = [{"id": "rs_1", "type": "reasoning",
                "content": [{"type": "reasoning_text", "text": "思考"}]}]
        msg4 = assistant("正文", None, "思考", raw)
        self.assertEqual(msg4["reasoning_items"], raw)


# ================================================================ 错误可读性
class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code, body: dict):
        payload = json.dumps(body).encode()
        super().__init__("https://x/chat/completions", code, "err", {},
                         io.BytesIO(payload))


class TestErrorMessages(unittest.TestCase):
    def setUp(self):
        self._orig = A._OPENER

    def tearDown(self):
        A._OPENER = self._orig

    def test_http_error_carries_supplier_message_and_hint(self):
        class _Opener:
            def open(self, req, timeout=None):
                raise _FakeHTTPError(400, {"error": {"message": "Model Not Exist"}})
        A._OPENER = _Opener()
        ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k", max_retries=1)
        with self.assertRaises(RuntimeError) as ctx:
            ad.chat("nope", [{"role": "user", "content": "hi"}], None, 0.0, 16)
        text = str(ctx.exception)
        self.assertIn("HTTP 400", text)
        self.assertIn("Model Not Exist", text)
        self.assertIn("模型名", text)          # 400 的可操作提示

    def test_auth_error_hint(self):
        class _Opener:
            def open(self, req, timeout=None):
                raise _FakeHTTPError(401, {"error": {"message": "invalid key"}})
        A._OPENER = _Opener()
        ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k", max_retries=1)
        with self.assertRaises(RuntimeError) as ctx:
            ad.chat("deepseek-flash", [{"role": "user", "content": "hi"}], None, 0, 8)
        self.assertIn("认证失败", str(ctx.exception))


# ================================================================ 模型列表
class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class TestListModels(unittest.TestCase):
    def setUp(self):
        self._orig = A._OPENER

    def tearDown(self):
        A._OPENER = self._orig

    def _serve(self, payload):
        class _Opener:
            def open(self, req, timeout=None):
                assert req.full_url.endswith("/models"), req.full_url
                return _FakeResponse(json.dumps(payload).encode())
        A._OPENER = _Opener()

    def test_openai_style(self):
        self._serve({"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]})
        ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k")
        self.assertEqual(ad.list_models(), ["deepseek-flash", "deepseek-v4-pro"])

    def test_gateway_style_and_dedup(self):
        self._serve({"models": ["glm-5.3", "glm-5.3", {"model": "glm-5.2"}]})
        ad = OpenAICompatibleAdapter("https://open.bigmodel.cn/api/paas/v4", "k")
        self.assertEqual(ad.list_models(), ["glm-5.2", "glm-5.3"])


# ================================================================ 预设真实性
class TestProviderPresets(unittest.TestCase):
    """把 2026-10 官方文档核对过的地址/模型钉住，防止悄悄退回旧值。"""

    def test_deepseek(self):
        preset = LLM_PROVIDERS["deepseek"]
        self.assertEqual(preset["base_url"], "https://api.deepseek.com")
        self.assertIn("deepseek-flash", preset["models"])
        self.assertIn("deepseek-v4-pro", preset["models"])
        self.assertNotIn("deepseek-chat", preset["models"])   # 旧名已非官方推荐
        self.assertTrue(preset["supports_thinking"])
        self.assertEqual(preset["thinking_default"], "off")

    def test_glm(self):
        preset = LLM_PROVIDERS["glm"]
        self.assertEqual(preset["base_url"], "https://open.bigmodel.cn/api/paas/v4")
        self.assertTrue(any(m.startswith("glm-5.") for m in preset["models"]))

    def test_kimi(self):
        preset = LLM_PROVIDERS["kimi"]
        self.assertIn("/v1", preset["base_url"])
        self.assertIn("kimi-k3", preset["models"])
        self.assertFalse(any(m.startswith("moonshot-v1") for m in preset["models"]))

    def test_every_provider_has_models_path(self):
        for key, preset in LLM_PROVIDERS.items():
            self.assertTrue(preset.get("models_path", "/models").startswith("/"), key)


# ================================================================ HTTP
class TestModelsEndpoint(WebServerCase):
    def setUp(self):
        self._env = strip_llm_env()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        restore_env(self._env)

    def test_unknown_provider(self):
        status, body = self.post("/api/models", {"provider": "no-such"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("未知供应商", body["error"])

    def test_missing_key_is_actionable(self):
        status, body = self.post("/api/models", {"provider": "deepseek"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("密钥", body["error"])
        self.assertTrue(body["hint"])

    def test_custom_without_base_url(self):
        status, body = self.post("/api/models", {"provider": "custom", "api_key": "k"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("API 地址", body["error"])

    def test_settings_view_exposes_model_metadata(self):
        status, body = self.get("/api/settings/llm")
        self.assertEqual(status, 200)
        keys = {p["key"] for p in body["providers"]}
        self.assertIn("deepseek", keys)
        deepseek = next(p for p in body["providers"] if p["key"] == "deepseek")
        self.assertTrue(deepseek["supports_thinking"])
        self.assertIn("docs", deepseek)
        self.assertIn("thinking", body)
        self.assertIn("models_path", deepseek)

    def test_save_normalizes_base_url_and_persists_thinking(self):
        status, body = self.post("/api/settings/llm", {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1/chat/completions",
            "model": "deepseek-flash",
            "api_key": "sk-test-not-real",
            "thinking": "on",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["settings"]["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(body["settings"]["thinking"], "on")
        self.assertEqual(body["settings"]["model"], "deepseek-flash")
        # 落盘的是非敏感字段（无密钥）
        raw = json.loads(open("saves/llm_config.json", encoding="utf-8").read())
        self.assertNotIn("api_key", raw)
        self.assertEqual(raw["thinking"], "on")


# ================================================================ Responses API
class TestResponsesConversion(unittest.TestCase):
    """chat 风格消息 → Responses 的 instructions + input items。"""

    def test_instructions_takes_first_system(self):
        from trpg.harness.responses import split_instructions
        messages = [{"role": "system", "content": "你是 DM"},
                    {"role": "user", "content": "开工"}]
        instructions, rest = split_instructions(messages)
        self.assertEqual(instructions, "你是 DM")
        self.assertEqual(rest, [{"role": "user", "content": "开工"}])

    def test_user_and_assistant_items(self):
        from trpg.harness.responses import to_input_items
        items = to_input_items([
            {"role": "user", "content": "开门"},
            {"role": "assistant", "content": "门开了"},
        ])
        self.assertEqual(items, [{"role": "user", "content": "开门"},
                                 {"role": "assistant", "content": "门开了"}])

    def test_tool_call_and_output_pairing(self):
        from trpg.harness.responses import to_input_items
        items = to_input_items([
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "narrate",
                                          "arguments": '{"text": "雨"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
        ])
        self.assertEqual(items[0], {"type": "function_call", "call_id": "call_1",
                                   "name": "narrate", "arguments": '{"text": "雨"}'})
        self.assertEqual(items[1], {"type": "function_call_output",
                                   "call_id": "call_1", "output": '{"ok": true}'})
        # 空的 assistant 正文不产生空 message item
        self.assertFalse(any(i.get("role") == "assistant" for i in items))

    def test_reasoning_item_precedes_message(self):
        from trpg.harness.responses import to_input_items
        items = to_input_items([{"role": "assistant", "content": "结论",
                                 "reasoning_content": "推理过程"}])
        self.assertEqual(items[0]["type"], "reasoning")
        self.assertEqual(items[0]["content"][0]["text"], "推理过程")
        self.assertEqual(items[1], {"role": "assistant", "content": "结论"})

    def test_raw_reasoning_item_roundtrips_with_id(self):
        from trpg.harness.responses import to_input_items
        raw = {"id": "rs_1", "type": "reasoning",
               "content": [{"type": "reasoning_text", "text": "推理过程"}]}
        items = to_input_items([{"role": "assistant", "content": "结论",
                                 "reasoning_items": [raw]}])
        self.assertEqual(items[0], raw)
        self.assertEqual(items[1], {"role": "assistant", "content": "结论"})

    def test_tools_are_flattened(self):
        from trpg.harness.responses import to_tools
        tools = [{"type": "function",
                  "function": {"name": "narrate", "description": "叙述",
                               "parameters": {"type": "object", "properties": {}}}}]
        self.assertEqual(to_tools(tools), [{
            "type": "function", "name": "narrate", "description": "叙述",
            "parameters": {"type": "object", "properties": {}}}])

    def test_reasoning_roundtrip_400_falls_back_to_chat(self):
        from trpg.harness.responses import OpenAIResponsesAdapter
        from trpg.harness.models import ModelResponse

        class ChatFallback:
            def __init__(self):
                self.called = 0

            def chat(self, model, messages, tools, temperature, max_tokens):
                self.called += 1
                return ModelResponse(content="chat 通道成功")

        detail = ("The `reasoning_text` in the thinking mode must be passed "
                  "back to the API.")
        chat = ChatFallback()
        adapter = OpenAIResponsesAdapter(
            "https://api.deepseek.com", "k", fallback_chat=chat)

        def boom(body):
            raise _FakeHTTPError(400, {"error": {"message": detail}})

        adapter._post = boom
        out = adapter.chat("deepseek-flash",
                           [{"role": "user", "content": "开门"}],
                           None, 0.7, 200)
        self.assertEqual(out.content, "chat 通道成功")
        self.assertEqual(chat.called, 1)
        self.assertTrue(adapter.fell_back)


class TestResponsesParsing(unittest.TestCase):
    def test_reasoning_roundtrip_error_detected(self):
        from trpg.harness.responses import _reasoning_roundtrip_error
        detail = ("The `reasoning_text` in the thinking mode must be passed "
                  "back to the API.")
        self.assertTrue(_reasoning_roundtrip_error(detail))
        self.assertFalse(_reasoning_roundtrip_error("model not found"))
    def _payload(self, status="completed", **over):
        data = {"id": "resp_1", "status": status, "output": [
            {"type": "reasoning", "id": "rs_1",
             "content": [{"type": "reasoning_text", "text": "先想"}]},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "雨停了。"}]},
            {"type": "function_call", "call_id": "call_7", "name": "skill_check",
             "arguments": '{"skill": "perception"}'},
        ], "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
        data.update(over)
        return data

    def test_parses_text_tools_reasoning_usage(self):
        from trpg.harness.responses import parse_response
        resp = parse_response(self._payload())
        self.assertEqual(resp.content, "雨停了。")
        self.assertEqual([(c.id, c.name, c.arguments) for c in resp.tool_calls],
                         [("call_7", "skill_check", {"skill": "perception"})])
        self.assertEqual(resp.reasoning, "先想")
        self.assertEqual(resp.reasoning_items[0]["id"], "rs_1")
        self.assertEqual(resp.finish_reason, "tool_calls")
        self.assertEqual(resp.usage["prompt_tokens"], 100)
        self.assertEqual(resp.usage["completion_tokens"], 20)
        self.assertEqual(resp.usage["total_tokens"], 120)

    def test_output_text_shortcut(self):
        from trpg.harness.responses import parse_response
        resp = parse_response({"status": "completed", "output": [],
                               "output_text": "直接给的正文"})
        self.assertEqual(resp.content, "直接给的正文")
        self.assertEqual(resp.finish_reason, "stop")

    def test_incomplete_without_content_is_actionable(self):
        from trpg.harness.responses import parse_response
        with self.assertRaises(RuntimeError) as ctx:
            parse_response({"status": "incomplete", "output": [],
                            "incomplete_details": {"reason": "max_output_tokens"}})
        self.assertIn("max_output_tokens", str(ctx.exception))

    def test_error_object_raises(self):
        from trpg.harness.responses import parse_response
        with self.assertRaises(RuntimeError):
            parse_response({"error": {"message": "boom"}})


class TestResponsesAdapter(unittest.TestCase):
    def test_payload_shape(self):
        from trpg.harness.responses import OpenAIResponsesAdapter
        ad = OpenAIResponsesAdapter("https://api.deepseek.com", "k")
        payload = ad.build_payload(
            "deepseek-flash",
            [{"role": "system", "content": "你是 DM"},
             {"role": "user", "content": "开工"}],
            [{"type": "function",
              "function": {"name": "narrate", "parameters": {"type": "object",
                                                            "properties": {}}}}],
            temperature=0.8, max_tokens=900)
        self.assertEqual(payload["instructions"], "你是 DM")
        self.assertEqual(payload["input"], [{"role": "user", "content": "开工"}])
        self.assertEqual(payload["max_output_tokens"], 900)
        self.assertEqual(payload["temperature"], 0.8)
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertEqual(payload["tools"][0]["name"], "narrate")
        self.assertNotIn("max_tokens", payload)      # chat 口径的名称不能混用

    def test_thinking_maps_to_reasoning_effort(self):
        from trpg.harness.responses import OpenAIResponsesAdapter
        ad = OpenAIResponsesAdapter("https://api.deepseek.com", "k",
                                    thinking="on", reasoning_effort="high")
        payload = ad.build_payload("deepseek-flash",
                                   [{"role": "user", "content": "hi"}], None, 0.5, 100)
        self.assertEqual(payload["reasoning"], {"effort": "high"})

    def test_posts_to_responses_endpoint(self):
        from trpg.harness import responses as R
        seen = {}

        class _Opener:
            def __call__(self, req, timeout=None):
                seen["url"] = req.full_url
                seen["body"] = json.loads(req.data.decode())
                return _FakeResponse(json.dumps({
                    "status": "completed", "output_text": "OK",
                    "output": [{"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": "OK"}]}],
                    "usage": {"input_tokens": 3, "output_tokens": 1}}).encode())

            def open(self, req, timeout=None):
                return self(req, timeout)

        orig = R.open_url
        R.open_url = _Opener()
        try:
            ad = R.OpenAIResponsesAdapter("https://api.deepseek.com/", "k", max_retries=1)
            resp = ad.chat("deepseek-flash", [{"role": "user", "content": "hi"}],
                           None, 0.0, 8)
        finally:
            R.open_url = orig
        self.assertEqual(seen["url"], "https://api.deepseek.com/responses")
        self.assertEqual(seen["body"]["input"], [{"role": "user", "content": "hi"}])
        self.assertEqual(resp.content, "OK")

    def test_404_falls_back_to_chat_in_auto_mode(self):
        from trpg.harness import responses as R
        from trpg.harness.adapter import OpenAICompatibleAdapter

        class _Chat:
            called = 0

            def chat(self, model, messages, tools, temperature, max_tokens):
                _Chat.called += 1
                return ModelResponse(content="chat 兜底")

        orig = R.open_url

        def _boom(req, timeout=None):
            raise _FakeHTTPError(404, {"error": {"message": "Not Found"}})

        R.open_url = _boom
        try:
            ad = R.OpenAIResponsesAdapter("https://api.deepseek.com/v1", "k",
                                          max_retries=1, fallback_chat=_Chat())
            resp = ad.chat("x", [{"role": "user", "content": "hi"}], None, 0.0, 8)
        finally:
            R.open_url = orig
        self.assertEqual(resp.content, "chat 兜底")
        self.assertEqual(_Chat.called, 1)
        self.assertTrue(ad.fell_back)

    def test_404_without_fallback_raises(self):
        from trpg.harness import responses as R

        orig = R.open_url

        def _boom(req, timeout=None):
            raise _FakeHTTPError(404, {"error": {"message": "Not Found"}})

        R.open_url = _boom
        try:
            ad = R.OpenAIResponsesAdapter("https://api.deepseek.com/v1", "k",
                                          max_retries=1)
            with self.assertRaises(RuntimeError) as ctx:
                ad.chat("x", [{"role": "user", "content": "hi"}], None, 0.0, 8)
        finally:
            R.open_url = orig
        self.assertIn("404", str(ctx.exception))


class TestProtocolSelection(unittest.TestCase):
    def test_provider_defaults(self):
        def cfg(provider, style="auto"):
            preset = LLM_PROVIDERS[provider]
            return LLMConfig(base_url=preset["base_url"] or "https://x/v1",
                             api_key="k", model="m", provider=provider,
                             api_style=style)

        for provider in ("deepseek", "glm", "kimi"):
            self.assertTrue(cfg(provider).uses_responses, provider)
        for provider in ("minimax", "custom"):
            self.assertFalse(cfg(provider).uses_responses, provider)
        self.assertFalse(cfg("deepseek", "chat").uses_responses)
        self.assertTrue(cfg("minimax", "responses").uses_responses)

    def test_settings_roundtrip_api_style(self):
        case = TestModelsEndpoint("test_settings_view_exposes_model_metadata")
        case.setUp()
        try:
            status, body = case.post("/api/settings/llm", {
                "provider": "deepseek", "base_url": "https://api.deepseek.com",
                "model": "deepseek-flash", "api_key": "sk-not-real",
                "thinking": "off", "api_style": "responses"})
            self.assertEqual(status, 200, body)
            self.assertEqual(body["settings"]["api_style"], "responses")
            self.assertTrue(body["settings"]["use_responses"])
            raw = json.loads(open("saves/llm_config.json", encoding="utf-8").read())
            self.assertEqual(raw["api_style"], "responses")
            self.assertNotIn("api_key", raw)
        finally:
            case.tearDown()


# ================================================================ 测试连接探针
# 2026-10 的真实故障：思考模式开着点「测试连接」必失败——
#   模型输出被 max_output_tokens 截断且没有正文
# 因为探针只给 16 个输出 token，思维链先把它吃光了。探针要按「通不通」来判，
# 不能按「预算够不够」来判：给够预算，被吃光再翻倍重试一次。
TRUNCATION = ("模型输出被 max_output_tokens 截断且没有正文：思考模式会先消耗输出预算。"
              "请在设置里关闭思考模式，或调大 max_tokens。")


class TestConnectionProbe(unittest.TestCase):
    def _cfg(self):
        return LLMConfig(base_url="https://api.deepseek.com", api_key="k",
                         model="deepseek-flash", thinking="on")

    def test_probe_budget_is_generous(self):
        from trpg.web.server import test_llm_connection
        seen = []

        class _Ad:
            def chat(self, model, messages, tools, temperature, max_tokens):
                seen.append(max_tokens)
                return ModelResponse(content="OK", finish_reason="stop")

        resp, budget, boosted = test_llm_connection(_Ad(), self._cfg())
        self.assertEqual(seen, [1024])          # 不再是 16
        self.assertEqual((budget, boosted), (1024, False))
        self.assertEqual(resp.content, "OK")

    def test_truncation_retries_with_double_budget(self):
        from trpg.web.server import test_llm_connection
        seen = []

        class _Ad:
            def chat(self, model, messages, tools, temperature, max_tokens):
                seen.append(max_tokens)
                if len(seen) == 1:
                    raise RuntimeError(TRUNCATION)
                return ModelResponse(content="OK", finish_reason="stop", reasoning="想了想")

        resp, budget, boosted = test_llm_connection(_Ad(), self._cfg())
        self.assertEqual(seen, [1024, 2048])
        self.assertEqual((budget, boosted), (2048, True))
        self.assertEqual(resp.reasoning, "想了想")

    def test_real_errors_are_not_retried(self):
        from trpg.web.server import test_llm_connection
        seen = []

        class _Ad:
            def chat(self, *a, **kw):
                seen.append(a[-1])
                raise RuntimeError("LLM 请求失败（HTTP 401）：invalid key")

        with self.assertRaises(RuntimeError):
            test_llm_connection(_Ad(), self._cfg())
        self.assertEqual(seen, [1024])


class TestConnectionEndpoint(WebServerCase):
    """``POST /api/llm/test``：预算加倍后要说清「加过」，两次都截断才报错。"""

    def setUp(self):
        super().setUp()
        self._saved_validate = A._assert_host_allowed
        A._assert_host_allowed = lambda host: None          # 离线：跳过 DNS 边界校验
        status, body = self.post("/api/settings/llm", {
            "provider": "deepseek", "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash", "api_key": "sk-not-real",
            "thinking": "on", "api_style": "chat"})
        self.assertEqual(status, 200, body)

    def tearDown(self):
        A._assert_host_allowed = self._saved_validate
        super().tearDown()

    def _patch_adapter(self, adapter):
        from unittest import mock
        from trpg.web import server as server_mod
        return mock.patch.object(server_mod, "_adapter_for", lambda *a, **kw: adapter)

    def test_boosted_probe_succeeds(self):
        seen = []

        class _Ad:
            def chat(self, model, messages, tools, temperature, max_tokens):
                seen.append(max_tokens)
                if len(seen) == 1:
                    raise RuntimeError(TRUNCATION)
                return ModelResponse(content="OK", finish_reason="stop", reasoning="想")

        with self._patch_adapter(_Ad()):
            status, body = self.post("/api/llm/test", {})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body["budget_boosted"])
        self.assertEqual(body["budget"], 2048)
        self.assertEqual(seen, [1024, 2048])

    def test_double_truncation_explains_thinking_budget(self):
        class _Ad:
            def chat(self, *a, **kw):
                raise RuntimeError(TRUNCATION)

        with self._patch_adapter(_Ad()):
            status, body = self.post("/api/llm/test", {})
        self.assertEqual(status, 400, body)
        self.assertIn("思考模式", body["hint"])


# ================================================================ 思考模式的输出预算
class TestThinkingBudgetFloor(unittest.TestCase):
    """思维链也算输出 token：NPC 台词 300 的 cap 一开思考就被吃光。

    ``max_tokens`` 是上限不是账单，思考模式下抬到 ``THINKING_MIN_TOKENS``
    只允许模型把正文写完，不会凭空多花。
    """

    def test_floor_only_when_thinking_on(self):
        self.assertEqual(A.effective_max_tokens(300, "on"), 1024)
        self.assertEqual(A.effective_max_tokens(300, "enabled"), 1024)
        self.assertEqual(A.effective_max_tokens(1200, "on"), 1200)   # 本来就够大
        self.assertEqual(A.effective_max_tokens(300, "off"), 300)
        self.assertEqual(A.effective_max_tokens(300, ""), 300)

    def _chat_payload(self, adapter, budget=300):
        seen = {}

        def _post(body):
            seen.update(json.loads(body))
            return ModelResponse(content="OK", finish_reason="stop")

        adapter._post = _post
        adapter.chat("m", [{"role": "user", "content": "hi"}], None, 0.0, budget)
        return seen

    def test_chat_completions_carries_floor(self):
        from unittest import mock
        with mock.patch.object(A, "_assert_host_allowed", lambda host: None):
            ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k",
                                         thinking="on", max_retries=1)
        self.assertEqual(self._chat_payload(ad)["max_tokens"], 1024)

    def test_responses_carries_floor(self):
        from unittest import mock
        from trpg.harness.responses import OpenAIResponsesAdapter
        with mock.patch.object(A, "_assert_host_allowed", lambda host: None):
            ad = OpenAIResponsesAdapter("https://api.deepseek.com", "k",
                                        thinking="on", max_retries=1)
        self.assertEqual(self._chat_payload(ad)["max_output_tokens"], 1024)


# ================================================================ .env 免填密钥
class TestEnvFile(unittest.TestCase):
    """密钥写进 .env 一次，以后每次启动自动读取（.env 已 gitignore）。"""

    KEYS = ("DEEPSEEK_API_KEY", "TRPG_LLM_MODEL")

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_reads_values_and_strips_quotes(self):
        from trpg.config import load_env_file
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".env"
            p.write_text('# 我的密钥\nexport DEEPSEEK_API_KEY="sk-from-file"\n'
                         "TRPG_LLM_MODEL='deepseek-flash'\n", encoding="utf-8")
            self.assertEqual(load_env_file(p), 2)
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "sk-from-file")
        self.assertEqual(os.environ["TRPG_LLM_MODEL"], "deepseek-flash")

    def test_existing_env_wins(self):
        from trpg.config import load_env_file
        os.environ["TRPG_LLM_MODEL"] = "deepseek-v4-pro"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".env"
            p.write_text("TRPG_LLM_MODEL=deepseek-flash\n", encoding="utf-8")
            self.assertEqual(load_env_file(p), 0)
        self.assertEqual(os.environ["TRPG_LLM_MODEL"], "deepseek-v4-pro")

    def test_missing_file_is_quiet(self):
        from trpg.config import load_env_file
        self.assertEqual(load_env_file("no-such-file.env"), 0)


# ================================================================ 工具调用配对
# 2026-10 的真实故障（真机 400）：
#   拉取/测试都正常，但一开局推进剧情就报
#   LLM 请求失败（HTTP 400）：No tool output found for tool call call_01_….
# 原因：DeepSeek 的 Responses 端点按「output 必须紧跟自己的 call」校验，而并行
# 工具调用按 [call A, call B, out A, out B] 平铺时，B 夹在 A 和 A 的结果之间，
# 它判定第二个调用没有结果。OpenAI 自己只按 call_id 配、不要求相邻。
# 修法：交错输出 [call A, out A, call B, out B]，并丢弃半截工具链。
def _call(call_id: str, name: str, args: str = "{}") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": args}}


def _tool(call_id: str, name: str, payload: str = '{"ok": true}') -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": payload}


class TestResponsesToolPairing(unittest.TestCase):
    def _types(self, items):
        return [i.get("type") or i.get("role") for i in items]

    def test_parallel_calls_are_interleaved(self):
        from trpg.harness.responses import to_input_items
        items = to_input_items([
            {"role": "assistant", "content": None,
             "tool_calls": [_call("call_A", "get_state"), _call("call_B", "npc_dialogue")]},
            _tool("call_A", "get_state", '{"a": 1}'),
            _tool("call_B", "npc_dialogue", '{"b": 2}'),
        ])
        self.assertEqual(self._types(items),
                         ["function_call", "function_call_output",
                          "function_call", "function_call_output"])
        self.assertEqual(items[1]["call_id"], "call_A")
        self.assertEqual(items[3]["call_id"], "call_B")

    def test_every_call_is_immediately_followed_by_its_output(self):
        # 真机 400 的形态：两轮「思考 + 并行调用」的历史
        from trpg.harness.responses import to_input_items
        items = to_input_items([
            {"role": "user", "content": "推进剧情"},
            {"role": "assistant", "content": None, "reasoning_content": "先查状态再问话",
             "tool_calls": [_call("call_A0", "get_state"),
                            _call("call_A1", "npc_dialogue")]},
            _tool("call_A0", "get_state"), _tool("call_A1", "npc_dialogue"),
            {"role": "assistant", "content": None, "reasoning_content": "该叙述了",
             "tool_calls": [_call("call_B0", "narrate"), _call("call_B1", "skill_check")]},
            _tool("call_B0", "narrate"), _tool("call_B1", "skill_check"),
        ])
        for idx, item in enumerate(items):
            if item.get("type") == "function_call":
                nxt = items[idx + 1]
                self.assertEqual(nxt["type"], "function_call_output", item)
                self.assertEqual(nxt["call_id"], item["call_id"], item)

    def test_orphan_call_is_not_sent(self):
        # 工具执行中断：调用进了历史、结果没写。这种半截调用发出去必被 400
        from trpg.harness.responses import to_input_items
        items = to_input_items([
            {"role": "assistant", "content": "我看看",
             "tool_calls": [_call("call_X", "say")]},
            {"role": "user", "content": "继续"},
        ])
        self.assertNotIn("function_call", self._types(items))
        self.assertEqual(items[0], {"role": "assistant", "content": "我看看"})

    def test_orphan_output_is_not_sent(self):
        from trpg.harness.responses import to_input_items
        items = to_input_items([
            {"role": "user", "content": "hi"},
            _tool("call_Y", "say"),
        ])
        self.assertEqual(self._types(items), ["user"])


class TestToolPairFallback(unittest.TestCase):
    """兜底：工具配对 400 不能让整局游戏死掉——切 chat/completions 重发。"""

    class _Chat:
        def __init__(self):
            self.calls = 0

        def chat(self, model, messages, tools, temperature, max_tokens):
            self.calls += 1
            return ModelResponse(content="chat 兜底", finish_reason="stop")

    def _adapter(self, code: int, message: str, fallback):
        from trpg.harness.responses import OpenAIResponsesAdapter
        ad = OpenAIResponsesAdapter("https://api.deepseek.com/v1", "k",
                                    max_retries=1, fallback_chat=fallback,
                                    fallback_on_404=False)
        from trpg.harness import responses as R
        orig = R.open_url

        def _boom(req, timeout=None):
            raise _FakeHTTPError(code, {"error": {"message": message}})

        R.open_url = _boom
        return ad, lambda: setattr(R, "open_url", orig)

    def test_pair_400_falls_back_even_with_explicit_protocol(self):
        chat = self._Chat()
        ad, restore = self._adapter(
            400, "No tool output found for tool call call_01_abc.", chat)
        try:
            resp = ad.chat("x", [{"role": "user", "content": "hi"}], None, 0.0, 8)
        finally:
            restore()
        self.assertEqual(resp.content, "chat 兜底")
        self.assertTrue(ad.fell_back)
        self.assertEqual(chat.calls, 1)

    def test_404_still_raises_when_protocol_explicit(self):
        ad, restore = self._adapter(404, "Not Found", self._Chat())
        try:
            with self.assertRaises(RuntimeError) as ctx:
                ad.chat("x", [{"role": "user", "content": "hi"}], None, 0.0, 8)
        finally:
            restore()
        self.assertIn("404", str(ctx.exception))


class TestToolPairSanitizer(unittest.TestCase):
    """chat/completions 也要求 tool 消息紧跟 tool_calls：同一套修形两边都用。"""

    def test_drops_orphan_call(self):
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [_call("c1", "say")]},
                {"role": "user", "content": "继续"}]
        self.assertEqual(sanitize_tool_messages(msgs),
                         [{"role": "user", "content": "继续"}])

    def test_keeps_text_when_calls_have_no_results(self):
        msgs = [{"role": "assistant", "content": "我先看看",
                 "tool_calls": [_call("c1", "say")]}]
        self.assertEqual(sanitize_tool_messages(msgs),
                         [{"role": "assistant", "content": "我先看看"}])

    def test_drops_orphan_result_and_keeps_pairs(self):
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [_call("c1", "say")]},
                _tool("c1", "say"),
                _tool("c9", "say")]           # 没有对应调用
        out = sanitize_tool_messages(msgs)
        self.assertEqual([m.get("role") for m in out], ["assistant", "tool"])
        self.assertEqual(out[0]["tool_calls"][0]["id"], "c1")
        self.assertEqual(out[1]["tool_call_id"], "c1")


class TestToolFailureStillPairs(unittest.TestCase):
    """工具抛异常也必须回填结果，否则历史里留下半截工具链。"""

    def test_exception_is_reported_as_tool_result(self):
        registry = ToolRegistry()

        def boom(**kw):
            raise ValueError("模拟工具故障")

        registry.register("boom", "会抛异常的工具", {"type": "object", "properties": {}}, boom)
        adapter = ScriptedAdapter([
            ModelResponse(content=None, tool_calls=[ToolCall("call_boom", "boom", {})],
                          finish_reason="tool_calls"),
            ModelResponse(content="收束", finish_reason="stop"),
        ])
        loop = DefaultAgentLoop(registry=registry, adapter=adapter)
        agent = Agent(agent_id="dm", name="DM", system_prompt="你是 DM",
                      tool_names=["boom"])
        session = Session("t")
        result = loop.run(agent, session, "开工")

        msgs = session.replay_messages()
        call_msg = next(m for m in msgs if m.get("tool_calls"))
        idx = msgs.index(call_msg)
        self.assertEqual(msgs[idx + 1]["role"], "tool")
        self.assertEqual(msgs[idx + 1]["tool_call_id"], "call_boom")
        self.assertIn("模拟工具故障", msgs[idx + 1]["content"])
        # 修形不该再删掉任何东西
        self.assertEqual(len(sanitize_tool_messages(msgs)), len(msgs))
        self.assertEqual(result.content, "收束")


class TestCompactionKeepsPairs(unittest.TestCase):
    def test_cut_does_not_split_call_and_result(self):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "u1" * 400},
                {"role": "assistant", "content": None,
                 "tool_calls": [_call("c1", "say")]},
                _tool("c1", "say"),
                {"role": "user", "content": "u2" * 400}]
        compactor = TruncateCompactor(token_limit=1, keep_recent=3)
        out = compactor.compact(msgs)
        tail = out[2:]            # head + 系统注 + tail
        self.assertNotEqual(tail[0].get("role"), "tool")
        self.assertEqual(len(sanitize_tool_messages(out)), len(out))


if __name__ == "__main__":
    unittest.main()