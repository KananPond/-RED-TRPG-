"""Harness 测试：注册表、循环、guards、压缩、session 日志不变量、端点校验。"""

import json
import random
import unittest

from trpg.harness.adapter import OpenAICompatibleAdapter, ScriptedAdapter
from trpg.harness.events import Session, safe_filename
from trpg.harness.loop import (
    Agent, DefaultAgentLoop, DuplicateCallGuard, StepBudgetGuard,
    ToolTimeoutGuard, TruncateCompactor, estimate_tokens,
)
from trpg.harness.models import ModelResponse, ToolCall
from trpg.harness.registry import ToolRegistry


def make_registry():
    reg = ToolRegistry()
    reg.register("add", "求和", {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    }, fn=lambda a, b: {"ok": True, "sum": a + b})
    reg.register("boom", "总是失败", {"type": "object", "properties": {}},
                 fn=lambda: 1 / 0)
    return reg


class TestRegistry(unittest.TestCase):
    def test_invoke_ok(self):
        reg = make_registry()
        r = reg.invoke("add", {"a": 2, "b": 3})
        self.assertEqual(r, {"ok": True, "sum": 5})

    def test_validation(self):
        reg = make_registry()
        self.assertFalse(reg.invoke("add", {"a": 2})["ok"])
        self.assertFalse(reg.invoke("add", {"a": "x", "b": 1})["ok"])
        self.assertFalse(reg.invoke("nope", {})["ok"])

    def test_error_backfilled(self):
        reg = make_registry()
        r = reg.invoke("boom", {})
        self.assertFalse(r["ok"])
        self.assertIn("ZeroDivisionError", r["error"])

    def test_disposer(self):
        reg = make_registry()
        dispose = reg.register("tmp", "t", {"type": "object", "properties": {}}, fn=lambda: {"ok": True})
        self.assertIn("tmp", reg)
        dispose()
        self.assertNotIn("tmp", reg)

    def test_non_dict_result_wrapped(self):
        reg = ToolRegistry()
        reg.register("five", "t", {"type": "object", "properties": {}}, fn=lambda: 5)
        self.assertEqual(reg.invoke("five", {}), {"ok": True, "result": 5})


class TestLoop(unittest.TestCase):
    def test_tool_call_then_final(self):
        reg = make_registry()
        adapter = ScriptedAdapter(script=[
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="c1", name="add", arguments={"a": 2, "b": 3})]),
            ModelResponse(content="答案是 5。"),
        ])
        loop = DefaultAgentLoop(reg, adapter)
        agent = Agent("a1", "算师", "你是算术助手。")
        session = Session("s1")
        result = loop.run(agent, session, "计算 2+3")
        self.assertEqual(result.content, "答案是 5。")
        self.assertEqual(result.steps, 1)
        self.assertEqual(result.tool_trace[0]["result"]["sum"], 5)
        # 模型可见 ⟺ 已记录：重放重建消息
        msgs = session.replay_messages()
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        self.assertEqual(json.loads(msgs[2]["content"])["sum"], 5)

    def test_step_budget_guard(self):
        reg = make_registry()
        adapter = ScriptedAdapter(handler=lambda m, t: ModelResponse(
            content=None, tool_calls=[ToolCall(id="x", name="add",
                                               arguments={"a": 1, "b": 2})]))
        loop = DefaultAgentLoop(reg, adapter, guard_step=StepBudgetGuard(max_steps=3))
        result = loop.run(Agent("a", "n", "s"), Session("s2"), "无限加")
        self.assertEqual(result.steps, 3)
        self.assertIn("上限", result.content)

    def test_duplicate_guard_short_circuits(self):
        reg = make_registry()
        adapter = ScriptedAdapter(handler=lambda m, t: ModelResponse(
            content=None, tool_calls=[ToolCall(id="x", name="add",
                                               arguments={"a": 1, "b": 1})]))
        dup = DuplicateCallGuard(max_repeats=2)
        loop = DefaultAgentLoop(reg, adapter, guard_dup=dup)
        result = loop.run(Agent("a", "n", "s"), Session("s3"), "重复调用")
        blocked = [t for t in result.tool_trace if not t["result"].get("ok")]
        self.assertTrue(blocked, "重复调用应被短路")

    def test_compaction_trims(self):
        reg = make_registry()
        big = "x" * 30000
        adapter = ScriptedAdapter(script=[ModelResponse(content="done")])
        loop = DefaultAgentLoop(reg, adapter,
                                compactor=TruncateCompactor(token_limit=100))
        session = Session("s4")
        # 预填长上下文
        ctx = [{"role": "user", "content": big}]
        result = loop.run(Agent("a", "n", "s"), session, "t", context_messages=ctx)
        self.assertEqual(result.content, "done")
        # 送入模型的请求应被压缩
        sent = adapter.calls[0]["messages"]
        self.assertLess(estimate_tokens("".join(str(m.get("content")) for m in sent)), 5000)

    def test_session_jsonl_persist(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            s = Session("persist_test", log_dir=d)
            s.log_message({"role": "user", "content": "你好"})
            path = os.path.join(d, "persist_test.jsonl")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                events = [json.loads(line) for line in f]
            self.assertEqual(events[0]["type"], "message")

    def test_session_log_filename_is_path_safe(self):
        """会话 id 来自角色名（如 `pc:24/7`）：落盘前必须洗掉路径非法字符。"""
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            s = Session("pc:24/7", log_dir=d)
            s.log_message({"role": "user", "content": "hi"})
            self.assertEqual(safe_filename("pc:24/7"), "pc_24_7")
            files = os.listdir(d)
            self.assertEqual(files, ["pc_24_7.jsonl"])

    def test_guard_timeout(self):
        import time
        reg = ToolRegistry()
        reg.register("slow", "t", {"type": "object", "properties": {}},
                     fn=lambda: time.sleep(3) or {"ok": True})
        loop = DefaultAgentLoop(reg, ScriptedAdapter())
        loop.guard_timeout.timeout = 0.2
        s = Session("s5")
        r = loop.guard_timeout.dispatch(reg, ToolCall(id="1", name="slow", arguments={}), s)
        self.assertFalse(r["ok"])
        self.assertIn("超时", r["error"])

    def test_interactive_tool_exceeds_normal_timeout(self):
        """等待玩家点掷骰按钮的工具不受常规超时约束（skill_check 在交互名单）。"""
        import time
        reg = ToolRegistry()
        reg.register("skill_check", "t", {"type": "object", "properties": {}},
                     fn=lambda: time.sleep(0.4) or {"ok": True})
        reg.register("slow", "t", {"type": "object", "properties": {}},
                     fn=lambda: time.sleep(0.4) or {"ok": True})
        guard = ToolTimeoutGuard(timeout=0.1)
        s = Session("s6")
        r = guard.dispatch(reg, ToolCall(id="1", name="slow", arguments={}), s)
        self.assertFalse(r["ok"])                      # 普通工具 0.1s 超时
        r = guard.dispatch(reg, ToolCall(id="2", name="skill_check", arguments={}), s)
        self.assertTrue(r["ok"])                       # 交互工具等满 0.4s


class TestEndpointValidation(unittest.TestCase):
    def test_scheme_must_be_http(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleAdapter("ftp://api.example.com", "k")

    def test_private_blocked_by_default(self):
        old = dict(__import__("os").environ)
        __import__("os").environ.pop("TRPG_LLM_ALLOW_PRIVATE", None)
        try:
            with self.assertRaises(ValueError):
                OpenAICompatibleAdapter("http://127.0.0.1:11434/v1", "k")
            with self.assertRaises(ValueError):
                OpenAICompatibleAdapter("http://192.168.1.10/v1", "k")
        finally:
            __import__("os").environ.clear()
            __import__("os").environ.update(old)

    def test_private_allowed_with_opt_in(self):
        import os
        os.environ["TRPG_LLM_ALLOW_PRIVATE"] = "1"
        try:
            a = OpenAICompatibleAdapter("http://127.0.0.1:11434/v1", "k")
            self.assertTrue(a.base_url.endswith("/v1"))
        finally:
            os.environ.pop("TRPG_LLM_ALLOW_PRIVATE", None)

    def test_public_ok(self):
        a = OpenAICompatibleAdapter("https://api.deepseek.com/v1", "k")
        self.assertEqual(a.name, "openai_compatible")


if __name__ == "__main__":
    unittest.main()
