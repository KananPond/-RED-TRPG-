"""Web 层测试：WebUI 阻塞应答、HTTP 端点、HTTP 驱动的完整剧本通关。"""

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ctrcg.campaign.module_data import COMPANIONS as M_COMPANIONS
from ctrcg.web.server import AppState, make_server
from ctrcg.ui.webui import AbortGame, WebUI

HOST = "127.0.0.1"  # 本地回环上的测试服务


def builtin_json_text() -> dict:
    return json.loads((Path(__file__).resolve().parent.parent
                       / "modules/starfall/module.json").read_text(encoding="utf-8"))


def http_post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(HOST, port, timeout=15)
    try:
        conn.request("POST", path, json.dumps(payload),
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def http_get(port: int, path: str) -> tuple[int, dict | bytes]:
    conn = http.client.HTTPConnection(HOST, port, timeout=15)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        raw = resp.read()
        ctype = resp.getheader("Content-Type", "")
        if "json" in ctype:
            return resp.status, json.loads(raw.decode("utf-8"))
        return resp.status, raw
    finally:
        conn.close()


class TestWebUI(unittest.TestCase):
    def test_choose_blocks_until_resolved(self):
        ui = WebUI()
        result = {}

        def ask():
            result["idx"] = ui.choose("选择", ["甲", "乙", "丙"])
        t = threading.Thread(target=ask, daemon=True)
        t.start()
        time.sleep(0.05)
        self.assertEqual(ui.pending_view()["kind"], "choose")
        self.assertEqual(len(ui.pending_view()["options"]), 3)
        ok, err = ui.resolve(index=1)
        self.assertTrue(ok)
        t.join(timeout=2)
        self.assertEqual(result.get("idx"), 1)
        self.assertIsNone(ui.pending_view())

    def test_resolve_validation(self):
        ui = WebUI()
        self.assertFalse(ui.resolve(index=0)[0])  # 无等待
        t = threading.Thread(target=lambda: ui.free_input("说什么"), daemon=True)
        t.start()
        time.sleep(0.05)
        self.assertFalse(ui.resolve(text="   ")[0])     # 空文本
        self.assertFalse(ui.resolve(index=0)[0])        # 输入请求不接受编号
        ok, _ = ui.resolve(text="前进")
        self.assertTrue(ok)
        t.join(timeout=2)

    def test_abort_raises_in_waiter(self):
        ui = WebUI()
        result = {}

        def ask():
            try:
                ui.choose("x", ["a"])
            except AbortGame:
                result["aborted"] = True
        t = threading.Thread(target=ask, daemon=True)
        t.start()
        time.sleep(0.05)
        ui.abort()
        t.join(timeout=2)
        self.assertTrue(result.get("aborted"))

    def test_free_input_returns_text(self):
        ui = WebUI()
        result = {}

        def ask():
            result["text"] = ui.free_input("你> ")
        t = threading.Thread(target=ask, daemon=True)
        t.start()
        time.sleep(0.05)
        ui.resolve(text="我拔剑前进")
        t.join(timeout=2)
        self.assertEqual(result["text"], "我拔剑前进")


class TestWebServer(unittest.TestCase):
    """起真实 HTTP 服务做端到端验证。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp.name)     # 存档写入临时目录，避免污染真实存档
        self.app = AppState(cordis_root=str(
            Path(__file__).resolve().parent.parent / "modules"))
        self.server = make_server(self.app, port=0)
        self.port = self.server.server_address[1]
        self.th = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.th.start()

    def tearDown(self):
        self.app.reset()
        self.server.shutdown()
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    # ------------------------------------------------------------ 端点
    def test_static_and_meta(self):
        status, body = http_get(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn("星坠矿脉的阴影".encode(), body)
        status, meta = http_get(self.port, "/api/meta")
        self.assertEqual(status, 200)
        self.assertFalse(meta["llm"]["llm_active"])
        self.assertTrue(any(p["key"] == "deepseek" for p in meta["providers"]))
        self.assertTrue(any(m["id"] == "starfall" for m in meta["modules"]))
        self.assertEqual(len(meta["races"]), 31)  # BG3 全种族（含亚种）
        status, css = http_get(self.port, "/static/app.css")
        self.assertEqual(status, 200)
        self.assertIn("跑团手记".encode(), css)
        status, _ = http_get(self.port, "/static/../server.py")
        self.assertEqual(status, 404)

    def test_answer_without_pending_conflict(self):
        status, body = http_post(self.port, "/api/answer", {"index": 0})
        self.assertEqual(status, 409)

    def test_start_rejects_invalid_character(self):
        status, body = http_post(self.port, "/api/start", {
            "name": "测试", "race": "human", "klass": "fighter",
            "background": "soldier", "ability_mode": "recommended",
            "skills": [], "fighting_style": "defense"})
        self.assertEqual(status, 400)
        self.assertIn("技能", body["error"])

    def test_ai_mode_dm_turn_over_http(self):
        """DeepSeek 配置就绪时，Web 服务应以 AI 模式开团：
        DM Agent 经 harness 循环调用工具并输出叙述（用脚本适配器模拟端点）。"""
        from ctrcg.config import LLMConfig
        from ctrcg.harness import adapter as adapter_mod
        from ctrcg.harness.models import ModelResponse, ToolCall

        def handler(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return ModelResponse(content=None, tool_calls=[
                    ToolCall(id="1", name="narrate",
                             arguments={"text": "夜风掠过丘陵，远处传来乌鸦的啼叫。"})])
            return ModelResponse(content="队伍在暮色中继续前进。")

        class FakeDeepSeekAdapter(adapter_mod.ScriptedAdapter):
            def __init__(self, **kw):
                super().__init__(handler=handler)

        original = adapter_mod.OpenAICompatibleAdapter
        adapter_mod.OpenAICompatibleAdapter = FakeDeepSeekAdapter
        try:
            self.app.llm_cfg = LLMConfig(
                base_url="https://api.deepseek.com/v1",
                api_key=os.environ.get("CTRCG_LLM_API_KEY", ""),
                model="deepseek-chat")
            status, body = http_post(self.port, "/api/start", {
                "name": "凯尔", "race": "human", "klass": "fighter",
                "background": "soldier", "ability_mode": "recommended",
                "skills": ["athletics", "intimidation"],
                "fighting_style": "defense", "seed": 3})
            self.assertEqual(status, 200, body)
            self.assertTrue(body["state"]["llm_active"])
            # AI 探索模式：DM 等待玩家自由输入
            for _ in range(60):
                p = self._pending()
                if p and p["kind"] == "input":
                    break
                time.sleep(0.05)
            else:
                self.fail("未等到 AI 模式的自由输入请求")
            status, _ = http_post(self.port, "/api/answer", {"text": "我观察四周。"})
            self.assertEqual(status, 200)
            for _ in range(40):
                types = [e["t"] for e in self.app.ui.history]
                if "narration" in types:
                    break
                time.sleep(0.05)
            joined = json.dumps(self.app.ui.history, ensure_ascii=False)
            self.assertIn("夜风掠过丘陵", joined)     # DM Agent 的工具叙述
            self.assertIn("队伍在暮色中继续前进", joined)  # 最终输出
        finally:
            adapter_mod.OpenAICompatibleAdapter = original

    def test_settings_llm_validation_and_usage(self):
        # 自定义供应商必须填地址
        status, body = http_post(self.port, "/api/settings/llm",
                                 {"provider": "custom"})
        self.assertEqual(status, 400)
        self.assertIn("地址", body["error"])
        # 未知供应商
        status, body = http_post(self.port, "/api/settings/llm",
                                 {"provider": "nope"})
        self.assertEqual(status, 400)
        # 离线状态下生成模组 → 明确报未配置
        status, body = http_post(self.port, "/api/modules/generate", {"theme": "x"})
        self.assertEqual(status, 400)
        self.assertIn("未配置", body["errors"][0])
        # 用量端点可用
        status, usage = http_get(self.port, "/api/usage")
        self.assertEqual(status, 200)
        self.assertIn("totals", usage)
        status, _ = http_post(self.port, "/api/usage/clear", {})
        self.assertEqual(status, 200)

    def test_generate_module_with_mocked_llm(self):
        """模拟 DeepSeek 返回合法模组 JSON → 生成并安装成功。"""
        from ctrcg.config import LLMConfig
        from ctrcg.harness import adapter as adapter_mod
        from ctrcg.web.server import generate_module
        module_json = builtin_json_text()
        module_json["id"] = "gen_test"
        module_json["title"] = "生成的模组"

        class FakeAdapter(adapter_mod.ScriptedAdapter):
            def __init__(self, **kw):
                super().__init__(handler=lambda m, t: adapter_mod.ModelResponse(
                    content="```json\n" + json.dumps(module_json, ensure_ascii=False)
                    + "\n```", usage={"prompt_tokens": 10, "completion_tokens": 20,
                                      "total_tokens": 30}))

        original = adapter_mod.OpenAICompatibleAdapter
        adapter_mod.OpenAICompatibleAdapter = FakeAdapter
        self.app.llm_cfg = LLMConfig(base_url="https://api.deepseek.com/v1",
                                     api_key=os.environ.get("CTRCG_LLM_API_KEY", ""),
                                     model="deepseek-chat")
        try:
            mod, errors, _warns = generate_module(self.app, {"theme": "测试主题"})
            self.assertIsNone(mod is None and errors or None)
            self.assertIsNotNone(mod)
            self.assertEqual(mod.source, "generated")
            self.assertEqual(mod.title, "生成的模组")
            # 生成调用也计入 Token 用量
            self.assertEqual(self.app.meter.summary()["by_agent"].get("generator"), 30)
            # 清理生成的模组，不污染仓库
            self.app.cordis.delete(mod.id)
        finally:
            adapter_mod.OpenAICompatibleAdapter = original

    def test_sse_replays_history(self):
        # 先跑一小局产生事件
        bot = _Bot(self.port)
        self._start_game()
        for _ in range(6):
            p = self._pending()
            if p:
                self._answer(bot, p)
                break
        else:
            self.fail("没有等到选择请求")
        time.sleep(0.2)
        # 连接 SSE，应重放历史并以 ready 结束
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        conn.request("GET", "/api/stream")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.getheader("Content-Type"))
        chunk = b""
        for _ in range(40):
            part = resp.read1(65536)
            if not part:
                break
            chunk += part
            if b"ready" in chunk:
                break
        self.assertIn(b"ready", chunk)
        self.assertIn(b"prose", chunk)      # DM 场景叙述
        self.assertIn(b"ask", chunk)        # 等待选择
        conn.close()

    # ------------------------------------------------------------ HTTP 全流程
    def test_full_playthrough_over_http(self):
        bot = _Bot(self.port)
        self._start_game()
        deadline = time.time() + 120
        guard = 0
        while time.time() < deadline and guard < 600:
            guard += 1
            history = self.app.ui.history
            if any(e["t"] == "game_over" and e.get("reason") == "ended" for e in history):
                break
            pending = self._pending()
            if pending is None:
                if not self.app.thread.is_alive():
                    break
                time.sleep(0.05)
                continue
            self._answer(bot, pending)
        else:
            self.fail("对局在时间/步数内未结束")

        texts = [json.dumps(e, ensure_ascii=False) for e in self.app.ui.history]
        joined = "".join(texts)
        self.assertIn("冒险结束", joined)          # 打到结局
        self.assertNotIn("队伍全灭", joined)
        # 种子固定 → 里程碑升级应发生
        self.assertIn("升到 3 级", joined)
        # SSE 广播有订阅事件（选择请求发出过）
        self.assertTrue(any(e["t"] == "ask" for e in self.app.ui.history))

    # ------------------------------------------------------------ 助手
    def _start_game(self):
        status, body = http_post(self.port, "/api/start", {
            "name": "凯尔", "race": "human", "klass": "fighter",
            "background": "soldier", "ability_mode": "recommended",
            "skills": ["athletics", "intimidation"],
            "fighting_style": "defense", "seed": 7})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["state"]["started"])

    def _pending(self):
        _, body = http_get(self.port, "/api/pending")
        return body.get("pending")

    def _answer(self, bot, pending):
        if pending["kind"] == "choose":
            idx = bot.choose(pending["prompt"], pending["options"])
            status, body = http_post(self.port, "/api/answer", {"index": idx})
        else:
            status, body = http_post(self.port, "/api/answer", {"text": "继续前进，留意四周。"})
        self.assertEqual(status, 200, body)


class _Bot:
    """HTTP 驱动的代打机器人：策略与 AutoUI 一致
    （每场景长休、残血回气/喝药、按关键词推进）。"""

    def __init__(self, port: int):
        self.port = port
        self.rested_at: str | None = None
        self.sw_scene: str | None = None

    def choose(self, prompt: str, options: list[str]) -> int:
        if "队伍长休" in options and self.rested_at != prompt:
            self.rested_at = prompt
            return next(i for i, o in enumerate(options) if "队伍长休" in o)
        _, st = http_get(self.port, "/api/state")
        me = next((p for p in st["party"] if p.get("is_player")), None)
        hurt = bool(me and me["hp"] < me["hp_max"] * 0.5)
        if hurt and self.sw_scene != st.get("encounter_title"):
            for i, o in enumerate(options):
                if "回气" in o:
                    self.sw_scene = st.get("encounter_title")
                    return i
        if hurt:
            for i, o in enumerate(options):
                if "喝" in o:
                    return i
        # 队友倒地：优先喂药救起，其次稳定伤势
        for i, o in enumerate(options):
            if "喂药" in o:
                return i
        for i, o in enumerate(options):
            if "稳定" in o:
                return i
        for key in ("攻击", "前进", "前往", "调查", "施展", "帮助", "喝", "继续"):
            for i, o in enumerate(options):
                if key in o:
                    return i
        return 0


class TestBuildCharacter(unittest.TestCase):
    def test_manual_scores_must_use_standard_array(self):
        from ctrcg.web.charbuild import build_character
        spec = {"name": "甲", "race": "human", "klass": "fighter",
                "background": "soldier", "ability_mode": "manual",
                "scores": {"str": 18, "dex": 14, "con": 12, "int": 10,
                           "wis": 8, "cha": 10},
                "skills": ["athletics"], "fighting_style": "defense"}
        with self.assertRaises(ValueError):
            build_character(spec)

    def test_fighter_requires_style(self):
        from ctrcg.web.charbuild import build_character
        spec = {"name": "甲", "race": "human", "klass": "fighter",
                "background": "soldier", "ability_mode": "recommended",
                "skills": ["athletics", "intimidation"]}
        with self.assertRaises(ValueError):
            build_character(spec)

    def test_build_session_llm_mode(self):
        """DeepSeek 配置就绪时应构建出 AI 模式会话（不发任何网络请求）。"""
        from ctrcg.config import LLMConfig
        from ctrcg.web.server import build_session
        from ctrcg.ui.webui import WebUI
        cfg = LLMConfig(base_url="https://api.deepseek.com/v1",
                        api_key=os.environ.get("CTRCG_LLM_API_KEY", ""),
                        model="deepseek-chat")
        session = build_session(G_state_for_test(), WebUI(), cfg)
        self.assertTrue(session.ai_mode)
        self.assertIsNotNone(session.dm_agent)
        self.assertEqual(len(session.pc_agents), 3)


def G_state_for_test():
    import random
    from ctrcg.game import state as G
    from ctrcg.rules.character import Character
    player = Character.create(
        name="测试", race="human", klass="fighter", background="soldier",
        scores={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
        skill_choices=["athletics", "intimidation"])
    companions = []
    for comp in M_COMPANIONS:
        companions.append(Character.create(
            name=comp["name"], race=comp["race"], klass=comp["klass"],
            background=comp["background"], scores=comp["scores"],
            skill_choices=comp["skills"]))
    return G.GameState(player, companions, random.Random(1))


if __name__ == "__main__":
    unittest.main()
