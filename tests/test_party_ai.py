"""全 AI 跑团（PartyDirector）测试。

目标不是“台词好不好”，而是架构：
- 所有 PC（包括主角）都是独立 Agent，没有 Human free_input 节点；
- 每个 PC 只拿到自我范围的工具（say / explore_intent / explore_act / hold），
  没有 DM 世界工具，也没有 NPC 子智能体入口；
- DM 仍是唯一裁定者；NPC 仍只由 DM 的 npc_dialogue 子智能体扮演；
- PC 之间共享桌面最近的话（table_archive），不是各说各的。
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (DM_MARK, is_fresh_run, isolate_saves,  # noqa: E402
                     make_agent_session, make_state, message_system,
                     restore_cwd)

from trpg.game.aipc import AIPCController                 # noqa: E402
from trpg.harness.models import ModelResponse, ToolCall   # noqa: E402
from trpg.harness.registry import ToolRegistry             # noqa: E402
from trpg.st.card import CharacterCard, Persona           # noqa: E402
from trpg.web.cinema import CinemaUI                      # noqa: E402
from trpg.config import LLMConfig                         # noqa: E402
from trpg.harness.adapter import ScriptedAdapter          # noqa: E402
from trpg.web.server import build_session                 # noqa: E402

_ISO: dict = {}
_PACE0 = {k: 0 for k in
          ("scene", "narration", "prose", "npc", "companion", "dice",
           "roll", "crit", "system", "board", "map", "inner",
           "cinema_choice", "cinema_action", "agent_state", "whisper",
           "choice_made", "game_over")}


def setUpModule():
    _ISO["cwd"], _ISO["tmp"] = isolate_saves()


def tearDownModule():
    restore_cwd(_ISO.pop("cwd", os.getcwd()), _ISO.pop("tmp", None))


class PartyScript:
    """每个 PC 第一拍做一个动作，第二拍收束；DM 只叙述，不推进。

    PartyDirector 的场景预算会兜底推进，因此测试不依赖 DM 的 advance_scene。
    """

    def __init__(self):
        self.pc_names: set[str] = set()
        self.pc_calls = 0
        self.dm_calls = 0
        self.player = ""

    def name_of(self, system: str) -> str:
        prefix = "你是《赛博朋克 RED》跑团队伍中的玩家角色 "
        i = system.find(prefix)
        if i < 0:
            return ""
        return system[i + len(prefix):].split("（")[0].strip()

    def __call__(self, messages, tools):
        system = message_system(messages)
        fresh = is_fresh_run(messages)
        names = {t["function"]["name"] for t in (tools or [])}
        if DM_MARK in system:
            self.dm_calls += 1
            return ModelResponse(content="DM：前面的路继续延伸，雨没有停。")
        if "explore_intent" in names:
            self.pc_calls += 1
            who = self.name_of(system)
            if who:
                self.pc_names.add(who)
            if fresh:
                if who == self.player:
                    return ModelResponse(tool_calls=[ToolCall(
                        "intent", "explore_intent",
                        {"text": "我检查前面的门，听门后的动静。"})])
                return ModelResponse(tool_calls=[ToolCall(
                    "say", "say", {"text": "我掩护你。", "to": self.player})])
            return ModelResponse(content="（这一拍结束。）")
        return ModelResponse(content="（等待）")


def party_session():
    state = make_state(seed=7, red=True)
    ui = CinemaUI(state.player, state.module, llm_active=True, speed=999)
    ui._speed = 999
    script = PartyScript()
    script.player = state.player.name
    session, state, ui, _adapter = make_agent_session(
        state=state, ui=ui, handler=script)
    controller = AIPCController.attach(
        session, session.loop, "test-model",
        CharacterCard(name=state.player.name, description="老兵。"),
        Persona(name=state.player.name))
    assert controller is not None
    session.party_ai = True
    return session, state, ui, script


class CombatScript:
    """战斗里每个 PC 都交还回合；验证主角也走 Agent 战斗链。"""

    def __call__(self, messages, tools):
        system = message_system(messages)
        names = {t["function"]["name"] for t in (tools or [])}
        if DM_MARK in system:
            return ModelResponse(content="DM：枪声在巷子里炸开。")
        if "combat_action" in names:
            return ModelResponse(tool_calls=[ToolCall(
                "combat", "combat_action", {"action": "end_turn"})])
        return ModelResponse(content="（等待）")


class TestPartyArchitecture(unittest.TestCase):
    def test_party_tools_are_self_scope_only(self):
        session, state, _ui, _script = party_session()
        reg = ToolRegistry()
        session.tools.register_party_tools(reg, state.player)
        names = {t["function"]["name"] for t in reg.schemas()}
        self.assertEqual(names, {"say", "explore_intent", "explore_act", "hold"})
        self.assertNotIn("npc_dialogue", names)
        self.assertNotIn("advance_scene", names)
        say_schema = next(t["function"] for t in reg.schemas()
                          if t["function"]["name"] == "say")
        self.assertNotIn("ooc", say_schema["parameters"]["properties"])

    def test_all_pcs_act_without_human_free_input(self):
        session, state, ui, script = party_session()
        self.assertTrue(session.party_ai)
        with mock.patch.dict("trpg.web.cinema._PACE", _PACE0), \
                mock.patch.object(ui, "free_input",
                                  side_effect=AssertionError("party 模式不应等待人类输入")):
            session.run()
        self.assertEqual(state.scene_id, "aftermath")
        self.assertTrue(script.pc_names, "Agent 没有被调用")
        self.assertIn(state.player.name, script.pc_names)
        self.assertIn("Redtail", script.pc_names)
        npc_names = {str(n.get("name")) for n in (state.module.npcs or {}).values()
                     if isinstance(n, dict)}
        for c in state.companions:
            self.assertIn(c.name, script.pc_names)
            self.assertNotIn(c.name, npc_names)
        self.assertGreater(script.pc_calls, 0)
        self.assertGreater(script.dm_calls, 0)
        # 主角的自主行动、同伴的 IC 台词都进入 UI；没有 OOC 前缀
        text = "\n".join(str(e.get("text", "")) for e in ui.history)
        self.assertIn(f"{state.player.name}：我检查前面的门", text)
        self.assertIn("我掩护你。", text)
        self.assertNotIn("（桌边）", text)
        # PC 之间共享最近桌面：后说话的同伴能看到先说话的人
        self.assertTrue(session.table_archive)
        self.assertTrue(all("ooc" not in l or not l["ooc"]
                            for l in session.table_archive))
        # 终章确实演出，而不是卡在某个场景
        self.assertTrue(any("最终队伍" in str(e.get("text", ""))
                            for e in ui.history))

    def test_build_session_enables_party_ai_for_cinema(self):
        state = make_state(seed=7, red=True)
        state.aipc_enabled = True
        ui = CinemaUI(state.player, state.module, llm_active=True, speed=999)
        cfg = LLMConfig(base_url="http://example.invalid", api_key="k",
                        model="test-model")
        adapter = ScriptedAdapter(
            handler=lambda messages, tools: ModelResponse(content="（等待）"))
        with mock.patch("trpg.web.server._adapter_for", return_value=adapter):
            session = build_session(state, ui, cfg, module=state.module)
        self.assertTrue(session.party_ai)
        self.assertIsNotNone(session.aipc)
        self.assertIn(state.player.name, session.pc_agents)

    def test_player_combat_uses_agent_not_human_turn(self):
        state = make_state(seed=7, red=True)
        ui = CinemaUI(state.player, state.module, llm_active=True, speed=999)
        ui._speed = 999
        session, state, ui, _adapter = make_agent_session(
            state=state, ui=ui, handler=CombatScript())
        self.assertIsNotNone(AIPCController.attach(
            session, session.loop, "test-model",
            CharacterCard(name=state.player.name, description="老兵。"),
            Persona(name=state.player.name)))
        session.party_ai = True
        session.player_combat_turn = lambda enc: (_ for _ in ()).throw(
            AssertionError("全 AI 跑团不应进入人类战斗菜单"))
        session.launch_encounter("e_alley_ambush")
        outcome = session.run_combat()
        self.assertIn(outcome, ("win", "tpk", "retreat"))


if __name__ == "__main__":
    unittest.main()
