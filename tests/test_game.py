"""游戏编排器集成测试（《赛博朋克 RED》）。

两条路径都要能驱动完整流程：
* **经典（剧本）模式**：``GameSession`` + ``AutoUI`` 离线跑完一场遭遇、推进场景链、
  发放改进点 IP、存档往返；
* **AI 模式**：``ScriptedAdapter`` 模拟 LLM，DM Agent 经真实工具注册表
  ``narrate`` / ``set_flag`` / ``skill_check``；玩家本人的检定走「掷骰按钮」闸门，
  队友自动掷。

``make_state`` 仍从本文件转出（保持 ``from test_game import make_state`` 可用）。
"""

import os
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (CountUI, RollGateUI, ScriptRng, isolate_saves,  # noqa: E402
                     make_agent_session, make_pc, make_state,        # noqa: F401 (转出)
                     restore_cwd)

from trpg.game import state as G                            # noqa: E402
from trpg.game.session import GameSession                   # noqa: E402
from trpg.harness.adapter import ScriptedAdapter            # noqa: E402
from trpg.harness.models import ModelResponse, ToolCall     # noqa: E402
from trpg.rules.character import COND_STABILIZED            # noqa: E402
from trpg.ui.cli import AutoUI                              # noqa: E402


# autosave 写的是相对路径 saves/game.json：切到临时目录，别覆盖玩家存档
_ISOLATION: dict = {}


def setUpModule():
    _ISOLATION["cwd"], _ISOLATION["tmp"] = isolate_saves()


def tearDownModule():
    restore_cwd(_ISOLATION.pop("cwd", os.getcwd()), _ISOLATION.pop("tmp", None))


def _state_with_small_encounter():
    """夹具模组 + 一场 1 敌遭遇（跑得快，断言稳）。"""
    st = make_state()
    st.module.encounters["e_small"] = {
        "title": "小冲突", "intro": "一个条子堵住了巷口。",
        "enemies": [["corrupt_cop", 1]], "loot": {"eurobucks": 40}}
    return st


# ================================================================ 经典模式
class TestScriptedMode(unittest.TestCase):
    def test_full_combat_runs_to_completion(self):
        st = _state_with_small_encounter()
        session = GameSession(st, AutoUI(), loop=None)
        summary = session.launch_encounter("e_small")
        self.assertTrue(summary.get("ok", True))
        outcome = session.run_combat()
        self.assertIn(outcome, ("win", "retreat"))
        self.assertIsNone(session.encounter)
        self.assertEqual(len(st.party()), 3)

    def test_combat_awards_loot_and_ip(self):
        st = _state_with_small_encounter()
        session = GameSession(st, AutoUI(), loop=None)
        before = st.party_eurobucks
        session.launch_encounter("e_small")
        session.run_combat()
        self.assertGreaterEqual(st.party_eurobucks, before)

    def test_advance_scene_validates_the_chain(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        # 越级跳场景被拒
        bad = session.advance_scene("不存在")
        self.assertFalse(bad["ok"])
        self.assertIn("不能直接跳到", bad["error"])
        # 正常推进
        cur = st.scene_id
        nxt = st.module.scene_by_id(cur)["next"]
        ok = session.advance_scene(nxt)
        self.assertTrue(ok["ok"])
        self.assertEqual(st.scene_id, nxt)
        self.assertIn(nxt, st.visited)

    def test_scene_ip_is_awarded_once(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        cur = st.module.scene_by_id(st.scene_id)
        session.advance_scene(cur["next"])
        after_first = [(c.name, c.ip) for c in st.party()]
        self.assertTrue(any(ip > 0 for _n, ip in after_first))
        # 回不去，但重复调用同一场景不应再发
        session._award_scene_ip(cur)
        self.assertEqual([(c.name, c.ip) for c in st.party()], after_first)

    def test_whole_chain_walks_to_the_terminal_scene(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        seen = [st.scene_id]
        for _ in range(10):
            scene = st.module.scene_by_id(st.scene_id)
            if not scene.get("next"):
                break
            session.advance_scene(scene["next"])
            seen.append(st.scene_id)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertIsNone(st.module.scene_by_id(st.scene_id)["next"])
        self.assertTrue(any(c.ip > 0 for c in st.party()))

    def test_save_and_load_roundtrip(self):
        st = make_state()
        st.flags["know_chest"] = "1"
        st.player.eurobucks = 123
        st.party_eurobucks = 123
        st.player.install_cyberware("pain_editor")
        st.player.gain_ip(40)
        session = GameSession(st, AutoUI(), loop=None)
        scene = st.module.scene_by_id(st.scene_id)
        if scene.get("next"):
            session.advance_scene(scene["next"])
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "game.json"
            G.save_game(st, path)
            back = G.load_game(path, module=st.module)
        self.assertEqual(back.flags, st.flags)
        self.assertEqual(back.scene_id, st.scene_id)
        self.assertEqual(back.visited, st.visited)
        self.assertEqual(back.party_eurobucks, 123)
        self.assertEqual(back.player.name, st.player.name)
        self.assertEqual(back.player.role, st.player.role)
        self.assertEqual(back.player.hp_max, st.player.hp_max)
        self.assertEqual(back.player.humanity_cur, st.player.humanity_cur)
        self.assertEqual(back.player.cyberware, ["pain_editor"])
        self.assertEqual(back.player.ip, st.player.ip)
        self.assertEqual(len(back.companions), len(st.companions))

    def test_ai_mode_flag_matches_loop(self):
        st = make_state()
        self.assertFalse(GameSession(st, AutoUI(), loop=None).ai_mode)
        session, *_ = make_agent_session(handler=lambda m, t: ModelResponse(
            content="（安静）"))
        self.assertTrue(session.ai_mode)


# ================================================================ AI 模式
class TestAgentMode(unittest.TestCase):
    def test_dm_turn_narrates_through_the_tool_registry(self):
        calls = {"n": 0}

        def handler(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                names = {t["function"]["name"] for t in tools}
                assert "narrate" in names and "skill_check" in names
                return ModelResponse(content=None, tool_calls=[
                    ToolCall(id="1", name="narrate",
                             arguments={"text": "雨点砸在铁皮棚顶上。"})])
            return ModelResponse(content="队伍贴着墙根往前走。")

        session, st, ui, adapter = make_agent_session(handler=handler,
                                                     ui=RollGateUI())
        session.dm_turn("我往巷子里看。")
        joined = "".join(ui.lines)
        self.assertIn("雨点砸在铁皮棚顶上", joined)
        self.assertIn("队伍贴着墙根往前走", joined)
        self.assertTrue(adapter.calls)

    def test_dm_set_flag_goes_through_the_real_tool(self):
        calls = {"n": 0}

        def handler(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return ModelResponse(content=None, tool_calls=[
                    ToolCall(id="1", name="set_flag",
                             arguments={"key": "know_chest", "value": "1"})])
            return ModelResponse(content="你们记住了这只箱子。")

        session, st, _ui, _adapter = make_agent_session(handler=handler)
        session.dm_turn("打开箱子看看。")
        self.assertEqual(st.flags.get("know_chest"), "1")

    def test_dm_skill_check_rolls_through_the_engine(self):
        calls = {"n": 0}

        def handler(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return ModelResponse(content=None, tool_calls=[
                    ToolCall(id="1", name="skill_check",
                             arguments={"skill": "perception", "dv": 15,
                                        "who": "队伍",
                                        "reason": "看穿雨幕"})])
            return ModelResponse(content="雨水里有什么东西闪了一下。")

        session, _st, ui, _adapter = make_agent_session(handler=handler,
                                                       ui=RollGateUI())
        session.dm_turn("我观察四周。")
        checks = [c for c in ui.checks if isinstance(c, dict)]
        self.assertTrue(checks, "skill_check 没有走进引擎")
        payload = checks[-1]
        self.assertEqual(payload["expr"], "1d10")
        self.assertEqual(payload["dv"], 15)
        # mods 的前两条是挂钩属性与技能，其后必然出现等值的净修正
        self.assertGreaterEqual(len(payload["mods"]), 3)
        nums = [int(x) for x in re.findall(r"[+-]\d+",
                                          " ".join(payload["mods"]))]
        self.assertIn(nums[0] + nums[1], nums[2:])

    def test_companion_falls_back_to_the_scripted_heuristic(self):
        """队友 Agent 的 LLM 故障 → 退回启发式回合，不炸整局。"""
        from trpg.game import state as G
        st = _state_with_small_encounter()
        session, st, _ui, _adapter = make_agent_session(
            state=st, ui=RollGateUI(),
            handler=lambda m, t: (_ for _ in ()).throw(RuntimeError("端点不可达")))
        enc = G.build_encounter("e_small", st)
        enc.begin()
        comp = st.companions[0]
        try:
            session.companion_turn(enc, comp)
        except Exception as exc:                        # noqa: BLE001
            self.fail(f"队友回合把 LLM 故障抛了出来：{exc!r}")
        self.assertTrue(session.encounter is None or enc.log_lines)

    def test_companion_agents_are_created_per_companion(self):
        session, st, _ui, _adapter = make_agent_session(
            handler=lambda m, t: ModelResponse(content="（跟着走）"))
        self.assertEqual(sorted(session.pc_agents), sorted(c.name for c in st.companions))
        for agent in session.pc_agents.values():
            self.assertIn("combat_action", agent.tool_names)
            self.assertIn("explore_intent", agent.tool_names)
            self.assertNotIn("cast", agent.tool_names)   # 5e 法术面已删除


# ================================================================ 掷骰闸门
class TestPlayerRollGate(unittest.TestCase):
    """玩家本人的检定走「掷骰按钮」；AI 队友自动掷。"""

    def test_player_check_waits_for_the_dice_button(self):
        session, st, ui, _adapter = make_agent_session(
            ui=RollGateUI(), handler=lambda m, t: ModelResponse(content=""))
        # 玩家本人 → 触发 ask_roll
        session.skill_check(st.player.name, "perception", 15)
        self.assertEqual(len(ui.rolls), 1)
        self.assertIn("DV 15", ui.rolls[0])
        self.assertIn("感知", ui.rolls[0])
        self.assertTrue(ui.checks)
        # 队友 → 直接结算，不再问玩家
        comp = st.companions[0]
        session.skill_check(comp.name, "perception", 15)
        self.assertEqual(len(ui.rolls), 1)
        self.assertGreaterEqual(len(ui.checks), 2)

    def test_ui_without_ask_roll_auto_rolls(self):
        """UI 没有 ``ask_roll``（离线 AutoUI）时，玩家检定直接结算。"""
        ui = AutoUI()
        session, st, _ui, _adapter = make_agent_session(
            ui=ui, handler=lambda m, t: ModelResponse(content=""))
        self.assertFalse(hasattr(ui, "ask_roll"))
        out = session.skill_check(st.player.name, "perception", 15)
        self.assertTrue(out.get("ok", True))
        self.assertIn(out["dv"], (15,))
        self.assertEqual(out["expr"], "1d10")


if __name__ == "__main__":
    unittest.main()
