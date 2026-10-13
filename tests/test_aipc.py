"""AI PC（AI 代打玩家角色）测试（《赛博朋克 RED》）。

最核心的一条契约：**零人类交互**——AI 代打时无论探索还是战斗，
``ui.choose`` / ``ui.free_input`` 一次都不该被调用（``CountUI`` 计数为 0）；
并且模型看到的工具面就是产品声明的那个（``say`` / ``explore_intent`` /
``explore_act``，战斗期再加 ``combat_action``）。

其余覆盖：LLM 故障时交还人类控制、玩家本人的掷骰闸门被绕过、
名字冲突拒绝挂载、存档往返保留 ``aipc`` 块且不泄漏密钥、
WebUI 的旁听指令槽。

零 LLM、零网络：全部经 ``ScriptedAdapter`` 离线驱动。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (CountUI, DM_MARK, is_fresh_run,  # noqa: E402
                     make_agent_session, make_state, message_system)

from ctrcg.game import state as G                        # noqa: E402
from ctrcg.game.aipc import AIPCController               # noqa: E402
from ctrcg.harness.models import ModelResponse, ToolCall  # noqa: E402
from ctrcg.st import CharacterCard, Persona              # noqa: E402


class AipcScript:
    """按角色发牌：DM 与 AI PC 分开排牌；记录每次调用的工具面。

    判据：带上 ``explore_intent`` / ``combat_action`` / ``explore_act``
    任一工具的就是 AI PC 的 agent（DM 的工具面里没有这三件）。
    """

    _AIPC_TOOLS = ("explore_intent", "combat_action", "explore_act")

    def __init__(self, aipc=None, dm=None):
        self.aipc = list(aipc or [])
        self.dm = list(dm or [])
        self.aipc_calls: list[list[dict]] = []
        self.dm_calls: list[list[dict]] = []
        self.last_tools: list[dict] = []
        self.aipc_tools: list[dict] = []          # 最近一次 AI PC 调用的工具面

    def __call__(self, messages, tools):
        system = message_system(messages)
        fresh = is_fresh_run(messages)
        names = {t["function"]["name"] for t in (tools or [])}
        self.last_tools = list(tools or [])
        if DM_MARK in system:
            self.dm_calls.append(messages)
            if fresh and self.dm:
                return self.dm.pop(0)
            return ModelResponse(content="DM 收束叙述。")
        if any(n in names for n in self._AIPC_TOOLS):
            self.aipc_calls.append(messages)
            self.aipc_tools = list(tools or [])
            if fresh and self.aipc:
                return self.aipc.pop(0)
            return ModelResponse(content="（AI 代打保持安静）")
        return ModelResponse(content="（保持安静，跟随队伍）")


def make_aipc_session(aipc=None, dm=None, ui=None, name="Kyle", attach=True):
    state = make_state(red=True)
    state.player.name = name
    script = AipcScript(aipc=aipc, dm=dm)
    session, state, ui, adapter = make_agent_session(
        state=state, ui=ui or CountUI(), handler=script)
    controller = None
    if attach:
        controller = AIPCController.attach(
            session, session.loop, "test-model",
            CharacterCard(name=name, description="夜之城的老兵。",
                          personality="话少。"),
            Persona(name=name, description="来自费城的转会工。"))
    return session, state, ui, adapter, controller


def card_for(name="Kyle"):
    return CharacterCard(name=name, description="老兵。", personality="话少。")


def small_encounter(session, state):
    state.module.encounters["e_small"] = {
        "title": "小冲突", "enemies": [["corrupt_cop", 1]]}
    session.launch_encounter("e_small")
    enc = session.encounter
    enc.begin()
    return enc


# ================================================================ 开关
class TestAipcOff(unittest.TestCase):
    def test_off_disables_everything(self):
        session, _st, _ui, _adapter, _c = make_aipc_session(attach=False)
        self.assertIsNone(session.aipc)
        agent = session.pc_agents[list(session.pc_agents)[0]]
        self.assertNotIn("explore_act", agent.tool_names)   # AI PC 专属工具面

    def test_legacy_save_without_aipc_key(self):
        st = make_state(red=True)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "game.json"
            G.save_game(st, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("aipc")
            path.write_text(json.dumps(raw, ensure_ascii=False),
                            encoding="utf-8")
            back = G.load_game(path, module=st.module)
        self.assertFalse(back.aipc_enabled)
        self.assertIsNone(back.persona)
        self.assertIsNone(back.aipc_card)

    def test_name_collision_refuses_attachment(self):
        st = make_state(red=True)
        session, st, _ui, _adapter = make_agent_session(state=st, ui=CountUI())
        st.player.name = st.companions[0].name
        out = AIPCController.attach(session, session.loop, "m",
                                    card_for(st.player.name), None)
        self.assertIsNone(out)
        self.assertIsNone(session.aipc)

    def test_attachment_registers_the_player_agent(self):
        session, st, _ui, _adapter, controller = make_aipc_session()
        self.assertIsNotNone(controller)
        self.assertIn(st.player.name, session.pc_agents)
        agent = session.pc_agents[st.player.name]
        self.assertIn("夜之城的老兵", agent.system_prompt)
        self.assertIn("来自费城", agent.system_prompt)


# ================================================================ 探索代打
class TestAipcExplore(unittest.TestCase):
    def test_explore_beat_uses_no_menus(self):
        session, _st, ui, _adapter, controller = make_aipc_session(aipc=[
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="1", name="say",
                         arguments={"text": "这地方不对劲。"})])])
        controller.explore_beat()
        self.assertEqual(ui.choose_calls, 0)
        self.assertEqual(ui.free_inputs, 0)
        self.assertIn("这地方不对劲", "\n".join(ui.lines))

    def test_ai_pc_tool_surface_is_the_declared_one(self):
        session, _st, _ui, adapter, controller = make_aipc_session(aipc=[
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="1", name="explore_intent",
                         arguments={"text": "沿着巷子往前走"})])])
        controller.explore_beat()
        names = {t["function"]["name"] for t in adapter.handler.aipc_tools}
        self.assertTrue({"say", "explore_intent", "explore_act"} <= names, names)
        for gone in ("cast", "drink_potion", "rest_long", "rest_short",
                     "autofire", "shove"):
            self.assertNotIn(gone, names, gone)

    def test_no_tool_fallback_still_reports_back(self):
        session, _st, ui, _adapter, controller = make_aipc_session(aipc=[
            ModelResponse(content="（沉默，跟着队伍。）")])
        self.assertTrue(controller.explore_beat())          # True = 本轮有产出
        self.assertEqual(ui.choose_calls, 0)

    def test_roll_gate_is_bypassed_for_the_ai_pc(self):
        session, st, ui, _adapter, controller = make_aipc_session()
        session.skill_check(st.player.name, "perception", 15)
        self.assertEqual(ui.rolls, [])

    def test_llm_failure_hands_control_back(self):
        def handler(messages, tools):
            if DM_MARK in message_system(messages):
                return ModelResponse(content="DM 收束叙述。")
            raise RuntimeError("端点不可达")

        state = make_state(red=True)
        session, state, ui, adapter = make_agent_session(
            state=state, ui=CountUI(), handler=handler)
        controller = AIPCController.attach(
            session, session.loop, "m", card_for(state.player.name),
            Persona(name=state.player.name))
        self.assertIsNotNone(controller)
        self.assertFalse(controller.explore_beat())         # False = 交还人类
        self.assertEqual(ui.choose_calls, 0)


# ================================================================ 战斗代打
class TestAipcCombat(unittest.TestCase):
    def test_combat_turn_acts_with_zero_menus(self):
        session, st, ui, _adapter, controller = make_aipc_session(aipc=[
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="1", name="combat_action",
                         arguments={"action": "attack"})])])
        enc = small_encounter(session, st)
        controller.combat_turn(enc)
        self.assertEqual(ui.choose_calls, 0)
        self.assertEqual(ui.free_inputs, 0)

    def test_combat_tool_surface(self):
        session, st, _ui, adapter, controller = make_aipc_session(aipc=[
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="1", name="combat_action",
                         arguments={"action": "attack"})])])
        enc = small_encounter(session, st)
        controller.combat_turn(enc)
        names = {t["function"]["name"] for t in adapter.handler.aipc_tools}
        for want in ("combat_action", "say", "explore_intent"):
            self.assertIn(want, names)
        for gone in ("cast", "autofire", "dodge", "help", "shove",
                     "second_wind", "wild_shape"):
            self.assertNotIn(gone, names, gone)

    def test_adapter_failure_falls_back_to_the_heuristic(self):
        def handler(messages, tools):
            system = message_system(messages)
            names = {t["function"]["name"] for t in (tools or [])}
            if "combat_action" in names and DM_MARK not in system:
                raise RuntimeError("端点不可达")
            return ModelResponse(content="DM 收束叙述。")

        state = make_state(red=True)
        session, state, ui, _adapter = make_agent_session(
            state=state, ui=CountUI(), handler=handler)
        controller = AIPCController.attach(
            session, session.loop, "m", card_for(state.player.name), None)
        enc = small_encounter(session, state)
        try:
            controller.combat_turn(enc)
        except Exception as exc:                            # noqa: BLE001
            self.fail(f"AI PC 战斗回合把 LLM 故障抛了出来：{exc!r}")
        self.assertEqual(ui.choose_calls, 0)


# ================================================================ 存档 / 指令
class TestAipcInfra(unittest.TestCase):
    def test_save_roundtrip_keeps_aipc_and_hides_the_key(self):
        session, st, _ui, _adapter, _c = make_aipc_session()
        st.aipc_enabled = True
        st.persona = Persona(name=st.player.name, description="来自费城。")
        st.aipc_card = card_for(st.player.name)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "game.json"
            G.save_game(st, path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("api_key", text)
            self.assertNotIn("sk-", text)
            back = G.load_game(path, module=st.module)
        self.assertTrue(back.aipc_enabled)
        self.assertIsNotNone(back.aipc_card)
        self.assertEqual(getattr(back.persona, "description", ""), "来自费城。")

    def test_command_slot_is_shared_with_the_ui(self):
        """旁听指令槽：UI 投递 → 控制器轮询（WebUI 才有 post_command）。"""
        from ctrcg.ui.webui import WebUI
        session, _st, _ui, _adapter, controller = make_aipc_session(ui=WebUI())
        ui = session.ui
        self.assertIsNone(controller.poll_command(0.01))
        ui.post_command("/状态")
        self.assertEqual(controller.poll_command(0.1), "/状态")
        ui.post_command("/背包")
        self.assertEqual(controller.poll_command(0.1), "/背包")

    def test_enter_scene_rebuilds_the_agent(self):
        session, st, _ui, _adapter, _c = make_aipc_session()
        nxt = st.module.scene_by_id(st.scene_id)["next"]
        session.enter_scene(nxt)
        agent = session.pc_agents.get(st.player.name)
        self.assertIsNotNone(agent)
        self.assertIn("夜之城", agent.system_prompt)
        self.assertIn("Scenario:", agent.system_prompt)


if __name__ == "__main__":
    unittest.main()
