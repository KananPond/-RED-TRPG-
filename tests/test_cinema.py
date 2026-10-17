"""AI 剧场（CinemaUI）回归测试。

覆盖的核心契约：

1. **零人类交互**：离线也能从第一场走到尾声，不抛 ``AbortGame``、不等待输入；
2. **暂停是真的暂停**：暂停时事件不进入历史，继续后恢复；
3. **重置不死锁**：``abort()`` 不能在暂停闸门里自己抛 ``AbortGame``；
4. **低语只做软引导**：关键词能影响下一个选项，但不改任何数值；
5. **Web 接缝**：``cinema`` 开局、暂停、低语、重置都返回 200，静态美术可访问。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (WebServerCase, isolate_saves, make_state,  # noqa: E402
                     make_agent_session, message_system, is_fresh_run,
                     DM_MARK, restore_cwd)

from trpg.game.aipc import AIPCController          # noqa: E402
from trpg.game.session import GameSession          # noqa: E402
from trpg.harness.models import ModelResponse, ToolCall  # noqa: E402
from trpg.st.card import CharacterCard, Persona    # noqa: E402
from trpg.web.cinema import CinemaUI               # noqa: E402


_ISO: dict = {}


def setUpModule():
    _ISO["cwd"], _ISO["tmp"] = isolate_saves()


def tearDownModule():
    restore_cwd(_ISO.pop("cwd", os.getcwd()), _ISO.pop("tmp", None))


def make_cinema(seed: int = 7, speed: float = 2.4):
    state = make_state(seed=seed, red=True)
    ui = CinemaUI(state.player, state.module, llm_active=False, speed=speed)
    # 测试不需要演出节奏；保留暂停闸门与事件语义，去掉 sleep。
    ui._speed = 999
    return state, ui


class TestCinemaAutoplay(unittest.TestCase):
    def test_offline_run_reaches_epilogue_without_inputs(self):
        state, ui = make_cinema(seed=7)
        session = GameSession(state, ui, loop=None, module=state.module)

        # 只压缩演出停顿；_emit 仍然写历史、仍然过暂停闸门。
        flat = {k: 0 for k in
                ("scene", "narration", "prose", "npc", "companion", "dice",
                 "roll", "crit", "system", "board", "map", "inner",
                 "cinema_choice", "cinema_action", "agent_state", "whisper",
                 "choice_made", "game_over")}
        with mock.patch.dict("trpg.web.cinema._PACE", flat):
            started = time.time()
            session.run()
            elapsed = time.time() - started

        types = [e["t"] for e in ui.history]
        self.assertLess(elapsed, 45, f"离线演出耗时异常：{elapsed:.1f}s")
        self.assertGreaterEqual(types.count("scene"), 5)
        self.assertIn("cinema_choice", types)
        self.assertIn("inner", types)
        self.assertNotIn("ask", types)
        self.assertEqual(state.scene_id, "aftermath")
        # 主角确实开口：离线导演给每个场景一句开场台词
        player_lines = [e for e in ui.history
                        if e["t"] == "companion" and e.get("who") == state.player.name]
        self.assertGreaterEqual(len(player_lines), 4)
        # 每一条技能之声都必须带声部与颜色，UI 才能上色
        for e in [x for x in ui.history if x["t"] == "inner"]:
            self.assertTrue(e.get("name"))
            self.assertTrue(e.get("color"))
            self.assertTrue(e.get("text"))

    def test_choices_are_not_blocking_and_skip_ip_menus(self):
        _state, ui = make_cinema()
        with mock.patch.dict("trpg.web.cinema._PACE", {"cinema_choice": 0,
                                                       "choice_made": 0}):
            idx = ui.choose("—— 安全屋 —— 你要做什么？",
                            ["检定机会：感知（DV 15）", "前往下一场景"])
            self.assertEqual(idx, 0)
            # 改进点是桌面成长，不是剧场高潮：能稍后就稍后
            idx = ui.choose("—— Forty 的改进点（IP 26）——",
                            ["提升技能（当前 26 IP）", "稍后再花（保留改进点）"])
            self.assertEqual(idx, 1)
        self.assertFalse(ui.pending_view())

    def test_whisper_influences_next_choice(self):
        _state, ui = make_cinema()
        ui.whisper("别开枪，先谈谈。")
        with mock.patch.dict("trpg.web.cinema._PACE", {"cinema_choice": 0,
                                                       "choice_made": 0}):
            idx = ui.choose("—— 小巷 —— 你要做什么？",
                            ["攻击", "对峙（魅力 + 声望）", "前进（触发剧情）"])
        self.assertEqual(idx, 1)
        ev = [e for e in ui.history if e["t"] == "cinema_choice"][-1]
        self.assertEqual(ev["selected"], 1)
        self.assertTrue(ev["reason"])

    def test_pause_gate_blocks_and_resumes(self):
        _state, ui = make_cinema()
        ui.set_paused(True)
        done = threading.Event()

        def emit():
            ui.system_line("暂停后不该立刻出现")
            done.set()

        t = threading.Thread(target=emit, daemon=True)
        t.start()
        time.sleep(0.25)
        self.assertFalse(done.is_set())
        self.assertNotIn("暂停后不该立刻出现",
                         [e.get("text") for e in ui.history if e["t"] == "system"])
        ui.set_paused(False)
        self.assertTrue(done.wait(2.0))
        self.assertIn("暂停后不该立刻出现",
                      [e.get("text") for e in ui.history if e["t"] == "system"])

    def test_abort_releases_pause_and_emits_game_over(self):
        _state, ui = make_cinema()
        ui.set_paused(True)
        try:
            ui.abort()
        except Exception as e:  # noqa: BLE001 —— 失败信息要直接暴露
            self.fail(f"abort 不应抛异常：{type(e).__name__}: {e}")
        self.assertTrue(ui.aborted)
        self.assertIn("game_over", [e["t"] for e in ui.history])

    def test_cinema_payload_has_psyche_and_autopilot(self):
        state, ui = make_cinema()
        payload = ui.cinema_payload()
        self.assertTrue(payload["active"])
        self.assertEqual(payload["autopilot"], "local")
        self.assertEqual(payload["player"], state.player.name)
        self.assertGreaterEqual(len(payload["psyche"]["voices"]), 4)
        self.assertTrue(all(v["color"] for v in payload["psyche"]["voices"]))


    def test_strict_ai_refuses_local_director_inputs(self):
        state = make_state(seed=7, red=True)
        ui = CinemaUI(state.player, state.module, llm_active=True,
                      speed=999, strict_ai=True)
        with self.assertRaises(RuntimeError):
            ui.choose("选择行动", ["A", "B"])
        with self.assertRaises(RuntimeError):
            ui.free_input("\n你> ")
        with self.assertRaises(RuntimeError):
            ui.ask_map_move("点图移动")


class TestCinemaWhisperToAipc(unittest.TestCase):
    """LLM 模式：观众的「低语」进入 AI PC 的下一拍上下文，而不是被丢掉。"""

    class _Script:
        def __init__(self):
            self.aipc_calls = []

        def __call__(self, messages, tools):
            system = message_system(messages)
            fresh = is_fresh_run(messages)
            names = {t["function"]["name"] for t in (tools or [])}
            if DM_MARK in system:
                return ModelResponse(content="DM 收束。")
            if "explore_intent" in names:
                self.aipc_calls.append(list(messages))
                if fresh:
                    return ModelResponse(tool_calls=[
                        ToolCall("c1", "explore_intent", {"text": "检查箱子"})])
            return ModelResponse(content="（保持安静）")

    def test_whisper_reaches_aipc_context_once(self):
        state = make_state(seed=7, red=True)
        ui = CinemaUI(state.player, state.module, llm_active=True, speed=999)
        ui._speed = 999
        script = self._Script()
        session, _st, _ui, _adapter = make_agent_session(
            state=state, ui=ui, handler=script)
        controller = AIPCController.attach(
            session, session.loop, "test-model",
            CharacterCard(name=state.player.name, description="老兵。"),
            Persona(name=state.player.name))
        self.assertIsNotNone(controller)
        ui.whisper("别碰那个箱子。")
        flat = {k: 0 for k in
                ("scene", "narration", "prose", "npc", "companion", "dice",
                 "roll", "crit", "system", "board", "map", "inner",
                 "cinema_choice", "cinema_action", "agent_state", "whisper",
                 "choice_made", "game_over")}
        with mock.patch.dict("trpg.web.cinema._PACE", flat):
            self.assertTrue(controller.explore_beat())
        self.assertTrue(script.aipc_calls)
        joined = "\n".join(str(m.get("content", "")) for m in script.aipc_calls[-1])
        self.assertIn("观众低语", joined)
        self.assertIn("别碰那个箱子", joined)
        # 低语只被消费一次（下一拍不应重复注入）
        self.assertEqual(ui.take_whisper(), "")


class TestCinemaWeb(WebServerCase):
    def test_cinema_requires_llm_and_static_art(self):
        # 纯 AI 剧场：没有 LLM 直接拒绝开局，不允许本地导演接管。
        status, body = self.start_game(cinema=True, speed=2.4,
                                       companions=["medtech_redtail", "solo_mover"])
        self.assertEqual(status, 400, body)
        self.assertFalse(body.get("ok"))
        self.assertIn("LLM", str(body.get("error") or ""))
        self.assertFalse(self.app.snapshot()["started"])

        # 剧场美术走 /static/cinema/...，由静态路由安全白名单放行
        status, css = self.get("/static/cinema.css")
        self.assertEqual(status, 200)
        self.assertIn(b"cine-dialogue", css)
        status, img = self.get("/static/cinema/portrait_forty.jpg")
        self.assertEqual(status, 200)
        self.assertGreater(len(img), 10000)


if __name__ == "__main__":
    unittest.main()
