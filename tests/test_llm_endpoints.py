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
import types
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (WebServerCase, strip_llm_env, restore_env)  # noqa: E402

from trpg.config import LLMConfig, LLM_PROVIDERS                  # noqa: E402
from trpg.harness import adapter as A                            # noqa: E402
from trpg.harness.adapter import (OpenAICompatibleAdapter,       # noqa: E402
                                  normalize_base_url)
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

    def test_tools_are_flattened(self):
        from trpg.harness.responses import to_tools
        tools = [{"type": "function",
                  "function": {"name": "narrate", "description": "叙述",
                               "parameters": {"type": "object", "properties": {}}}}]
        self.assertEqual(to_tools(tools), [{
            "type": "function", "name": "narrate", "description": "叙述",
            "parameters": {"type": "object", "properties": {}}}])


class TestResponsesParsing(unittest.TestCase):
    def _payload(self, status="completed", **over):
        data = {"id": "resp_1", "status": status, "output": [
            {"type": "reasoning",
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


if __name__ == "__main__":
    unittest.main()