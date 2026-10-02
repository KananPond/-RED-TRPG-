"""游戏编排器集成测试：验证 AI Agent 路径（ScriptedAdapter 模拟 LLM）
与剧本模式路径都能驱动完整游戏流程。"""

import random
import unittest

from ctrcg.campaign import module_data as M
from ctrcg.game import state as G
from ctrcg.game.agents import make_dm_agent, make_pc_agent
from ctrcg.game.session import GameSession
from ctrcg.harness.adapter import ScriptedAdapter
from ctrcg.harness.loop import DefaultAgentLoop
from ctrcg.harness.models import ModelResponse, ToolCall
from ctrcg.rules.character import Character
from ctrcg.ui.cli import AutoUI


def make_state(seed=5):
    rng = random.Random(seed)
    player = Character.create(
        name="凯尔", race="human", klass="fighter", background="soldier",
        scores={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
        skill_choices=["athletics", "intimidation"], fighting_style="defense")
    comps = []
    for comp in M.COMPANIONS:
        comps.append(Character.create(
            name=comp["name"], race=comp["race"], klass=comp["klass"],
            background=comp["background"], scores=comp["scores"],
            skill_choices=comp["skills"], fighting_style=comp["fighting_style"]))
    return G.GameState(player, comps, rng)


class TestScriptedMode(unittest.TestCase):
    def _session(self, seed=5):
        state = make_state(seed)
        ui = AutoUI()
        s = GameSession(state, ui, loop=None)
        return s, state, ui

    def test_full_combat_runs_to_completion(self):
        s, state, ui = self._session()
        s.launch_encounter("e_ambush")
        outcome = s.run_combat()
        self.assertIn(outcome, ("win", "retreat"))
        self.assertIsNone(s.encounter)
        # XP 已发放
        self.assertGreater(state.player.xp, 0)

    def test_advance_scene_validates(self):
        s, state, ui = self._session()
        r = s.advance_scene("forge_hall")  # 跳幕应被拒绝
        self.assertFalse(r["ok"])
        cur = M.scene_by_id(state.scene_id)
        r = s.advance_scene(cur["next"])
        self.assertTrue(r["ok"])
        self.assertEqual(state.scene_id, cur["next"])

    def test_milestone_level_up(self):
        s, state, ui = self._session()
        # 沿合法链路推进（advance_scene 拒绝跳幕）
        for sid in ["road_ambush", "cave_entrance", "wolf_den"]:
            r = s.advance_scene(sid)
            self.assertTrue(r["ok"], f"推进到 {sid} 失败: {r}")
        self.assertEqual(state.player.level, 2)  # wolf_den 入场升2
        self.assertEqual(state.player.hp_cur, state.player.hp_max)
        for sid in ["boss_den", "town_arrive", "inn_rumors", "well_hideout"]:
            r = s.advance_scene(sid)
            self.assertTrue(r["ok"], f"推进到 {sid} 失败: {r}")
        self.assertEqual(state.player.level, 3)  # well_hideout 入场升3
        self.assertEqual(state.player.hp_cur, state.player.hp_max)

    def test_save_and_load_roundtrip(self):
        import tempfile, os
        s, state, ui = self._session()
        state.flags["test"] = "1"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "save.json")
            G.save_game(state, path)
            state2 = G.load_game(path)
        self.assertEqual(state2.flags.get("test"), "1")
        self.assertEqual(state2.player.name, state.player.name)
        self.assertEqual(state2.player.abilities, state.player.abilities)
        self.assertEqual(len(state2.companions), 3)


class TestAgentMode(unittest.TestCase):
    """用 ScriptedAdapter 模拟 LLM，验证 harness 循环能驱动 DM/PC 决策。"""

    def _agent_session(self, script):
        state = make_state()
        ui = AutoUI()
        adapter = ScriptedAdapter(script=script)
        loop = DefaultAgentLoop(registry=None, adapter=adapter)
        dm = make_dm_agent("test-model", M.BUILTIN)
        s = GameSession(state, ui, loop=loop, dm_agent=dm)
        return s, state, ui, adapter

    def test_dm_turn_narrates_and_sets_flag(self):
        script = [
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="1", name="get_state", arguments={}),
                ToolCall(id="2", name="set_flag",
                         arguments={"key": "mood", "value": "阴沉"}),
                ToolCall(id="3", name="narrate",
                         arguments={"text": "风卷起沙尘。"}),
            ]),
            ModelResponse(content="队伍继续上路。"),
        ]
        s, state, ui, adapter = self._agent_session(script)
        s.dm_turn("我观察四周")
        self.assertEqual(state.flags.get("mood"), "阴沉")
        joined = "\n".join(ui.history)
        self.assertIn("风卷起沙尘", joined)      # narrate 工具的叙述
        self.assertIn("队伍继续上路", joined)    # DM 最终收束叙述
        # 模型收到 get_state 的结构化结果
        last_call = adapter.calls[-1]
        contents = [str(m.get("content")) for m in last_call["messages"]]
        self.assertTrue(any("party" in c for c in contents))

    def test_dm_skill_check_uses_engine(self):
        script = [
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="1", name="skill_check",
                         arguments={"who": "凯尔", "skill": "athletics", "dc": 10}),
            ]),
            ModelResponse(content="检定完成。"),
        ]
        s, state, ui, adapter = self._agent_session(script)
        s.dm_turn("我攀上土坡")
        self.assertTrue(any("🎲" in h for h in ui.history))

    def test_pc_agent_combat_action(self):
        comp = M.COMPANIONS[0]  # 罗兰 战士
        pc = Character.create(
            name=comp["name"], race=comp["race"], klass=comp["klass"],
            background=comp["background"], scores=comp["scores"],
            skill_choices=comp["skills"])
        agent = make_pc_agent(comp, pc.level, pc.weapons, "无", "test-model")
        script = [
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="1", name="combat_action",
                         arguments={"action": "attack", "weapon": "longsword"}),
            ]),
            ModelResponse(content="（挥剑而上）"),
        ]
        state = make_state()
        ui = AutoUI()
        loop = DefaultAgentLoop(registry=None, adapter=ScriptedAdapter(script=script))
        s = GameSession(state, ui, loop=loop)
        s.launch_encounter("e_ambush")
        enc = s.encounter
        enc.begin(state.rng)
        s.tools.encounter = enc
        acted = s._pc_agent_turn(enc, pc, agent)
        self.assertTrue(acted)
        # 长剑确实砍了人：有敌人掉血或至少出现攻击日志
        self.assertTrue(any("长剑" in line for line in enc.log))

    def test_pc_agent_falls_back_when_llm_down(self):
        comp = M.COMPANIONS[0]
        pc = Character.create(
            name=comp["name"], race=comp["race"], klass=comp["klass"],
            background=comp["background"], scores=comp["scores"],
            skill_choices=comp["skills"])
        agent = make_pc_agent(comp, pc.level, pc.weapons, "无", "test-model")
        state = make_state()
        ui = AutoUI()

        class BoomAdapter(ScriptedAdapter):
            def chat(self, *a, **kw):
                raise RuntimeError("网络不可达")

        loop = DefaultAgentLoop(registry=None, adapter=BoomAdapter())
        s = GameSession(state, ui, loop=loop)
        s.launch_encounter("e_ambush")
        enc = s.encounter
        enc.begin(state.rng)
        s.tools.encounter = enc
        acted = s._pc_agent_turn(enc, pc, agent)
        self.assertFalse(acted)  # 兜底标记
        before = len(enc.log)
        s._scripted_pc_turn(enc, pc)  # 启发式接管
        self.assertGreater(len(enc.log), before)


class _RollGateUI:
    """记录 ask_roll 调用的最小 UI 桩（模拟 WebUI 的掷骰按钮）。"""

    def __init__(self):
        self.rolls_asked: list[str] = []
        self.dice: list[dict] = []

    def ask_roll(self, prompt: str) -> bool:
        self.rolls_asked.append(prompt)
        return True

    def check_line(self, result: dict, context: str = "") -> dict:
        self.dice.append(result)
        return result

    def save_line(self, result: dict) -> dict:
        self.dice.append(result)
        return result

    def system_line(self, text: str) -> dict:
        return {"ok": True}


class TestPlayerRollGate(unittest.TestCase):
    """玩家本人的检定/豁免要经掷骰按钮；同伴自动落骰。"""

    def test_player_gated_companion_auto(self):
        state = make_state()
        ui = _RollGateUI()
        s = GameSession(state, ui, loop=None)
        r = s.tools._tool_skill_check("凯尔", "athletics", 10)
        self.assertEqual(len(ui.rolls_asked), 1)
        self.assertIn("DC 10", ui.rolls_asked[0])
        self.assertTrue(ui.dice and ui.dice[-1].get("mods"))
        # 同伴自动掷，不经按钮
        comp = state.companions[0].name
        s.tools._tool_skill_check(comp, "perception", 10)
        self.assertEqual(len(ui.rolls_asked), 1)
        # 玩家豁免同样门控
        s.tools._tool_save("凯尔", "dex", 12)
        self.assertEqual(len(ui.rolls_asked), 2)
        self.assertIn("豁免", ui.rolls_asked[-1])
        # 掷骰结果仍然由引擎结算
        self.assertIn("total", r)


if __name__ == "__main__":
    unittest.main()
