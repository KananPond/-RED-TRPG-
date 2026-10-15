"""NPC 子智能体 与 同伴 ST 化（《赛博朋克 RED》）。

* **NPC = DM 控制的 SubAgent**：DM 的 ``npc_dialogue`` 工具按需拉起 NPC 子代理，
  子代理只拿 ``say`` 一件工具；台词渲染到桌面、进 NPC 持久会话、并入桌面账本
  （DM 下回合"听得见"）；未知 NPC / LLM 不可达 / 剧本模式时优雅降级。
* **非玩家 PC = 独立 Agent**：同伴 agent 走 ``trpg.st`` 组装线（角色卡字段
  ``mes_example`` / ``book``），桌边回合注入世界书上下文。
* **默认剧本**：``main.DEFAULT_MODULE_ID`` 与 ``BUILTIN_IDS`` 都指向随包发行的
  RED 模组；modules 目录缺失时仍能回退到代码内置数据。

零 LLM、零网络：全部经 ``ScriptedAdapter`` 离线驱动。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (DM_MARK, NPC_MARK, fixture_module_data,  # noqa: E402
                     is_fresh_run, make_agent_session, make_state,
                     message_system)

from trpg.campaign.loader import Module, validate_module  # noqa: E402
from trpg.harness.models import ModelResponse, ToolCall   # noqa: E402
from trpg.rules import data as D                          # noqa: E402
from trpg.st import card_from_companion, card_from_npc    # noqa: E402
from trpg.ui.cli import AutoUI                            # noqa: E402

NPC_ID = "salt_officer"
NPC_NAME = "Salter"


class NpcScript:
    """按系统标记发牌：NPC 子代理先 ``say`` 再收束；DM 输出最终叙述。"""

    def __init__(self, npc_lines=None):
        self.npc_lines = list(npc_lines or [])
        self.npc_runs = 0
        self.calls: list[list[dict]] = []

    def __call__(self, messages, tools):
        self.calls.append(messages)
        system = message_system(messages)
        fresh = is_fresh_run(messages)
        if NPC_MARK in system:
            if fresh:
                self.npc_runs += 1
                if self.npc_lines:
                    return ModelResponse(content=None, tool_calls=[
                        ToolCall(id="n1", name="say",
                                 arguments={"text": self.npc_lines.pop(0)})])
                return ModelResponse(content="（沉默地打量你们。）")
            return ModelResponse(content="（无话）")
        if DM_MARK in system:
            return ModelResponse(content="DM 收束叙述。")
        return ModelResponse(content="（保持安静，跟随队伍）")


def npc_session(lines=None):
    script = NpcScript(lines)
    session, state, ui, adapter = make_agent_session(ui=AutoUI(), handler=script)
    return session, state, ui, script


# ================================================================ NPC 子智能体
class TestNpcSubagent(unittest.TestCase):
    def test_npc_dialogue_tool_is_registered(self):
        session, *_ = npc_session()
        self.assertIn("npc_dialogue", session.dm_agent.tool_names)
        self.assertIn("npc_dialogue", session.registry)

    def test_dialogue_delegates_renders_and_logs(self):
        session, _st, ui, script = npc_session(lines=["这条巷子归我管。"])
        out = session.npc_director.dialogue(NPC_ID, "警告他们别多管闲事")
        self.assertTrue(out["ok"])
        self.assertEqual(out["lines"], ["这条巷子归我管。"])
        joined = "\n".join(ui.history)
        self.assertIn(NPC_NAME, joined)
        self.assertIn("这条巷子归我管", joined)
        self.assertEqual([l["who"] for l in session.table_log], [NPC_NAME])
        self.assertEqual(script.npc_runs, 1)

    def test_npc_session_keeps_memory_across_calls(self):
        session, _st, _ui, script = npc_session(lines=["第一次。", "第二次。"])
        session.npc_director.dialogue(NPC_ID, "第一次说话")
        session.npc_director.dialogue(NPC_ID, "第二次说话")
        self.assertEqual(script.npc_runs, 2)
        # 第二次调用的上下文里应当带着第一次的台词（同一份持久会话）
        last = "\n".join(str(m.get("content", "")) for m in script.calls[-1])
        self.assertIn("第一次。", last)

    def test_npc_lines_reach_the_dm_context(self):
        session, _st, _ui, script = npc_session(lines=["我不认识他们。"])
        session.npc_director.dialogue(NPC_ID, "被质问")
        session.dm_turn("我继续问。")
        ctx = "\n".join(str(m.get("content", ""))
                        for m in script.calls[-1])
        self.assertIn(NPC_NAME, ctx)
        self.assertIn("我不认识他们", ctx)

    def test_unknown_npc_lists_the_available_ones(self):
        session, _st, _ui, _script = npc_session()
        out = session.npc_director.dialogue("查无此人")
        self.assertFalse(out["ok"])
        self.assertIn("可用", out["error"])
        self.assertIn(NPC_NAME, out["error"])

    def test_npc_resolved_by_display_name(self):
        session, _st, _ui, _script = npc_session(lines=["嗯。"])
        out = session.npc_director.dialogue(NPC_NAME)
        self.assertTrue(out["ok"])

    def test_llm_failure_degrades_to_speak_as_npc(self):
        def handler(messages, tools):
            system = message_system(messages)
            if NPC_MARK in system:
                raise RuntimeError("端点不可达")
            return ModelResponse(content="（旁白）")

        session, _st, _ui, _adapter = make_agent_session(
            ui=AutoUI(), handler=handler)
        out = session.npc_director.dialogue(NPC_ID)
        self.assertFalse(out["ok"])
        self.assertIn("speak_as_npc", out["error"])
        self.assertEqual(session.table_log, [])

    def test_scripted_mode_has_no_npc_director(self):
        from trpg.game.session import GameSession
        session = GameSession(make_state(), AutoUI(), loop=None)
        self.assertIsNone(session.npc_director)
        res = session.tools._tool_npc_dialogue(NPC_ID, "说话")
        self.assertFalse(res["ok"])
        self.assertIn("AI", res["error"])

    def test_npc_system_prompt_carries_persona_goals_and_secret(self):
        session, st, _ui, script = npc_session(lines=["……"])
        npc = st.module.npcs[NPC_ID]
        session.npc_director.dialogue(NPC_ID, "聚焦")
        system = message_system(script.calls[-1])
        for field in ("persona", "voice", "goals", "secret"):
            value = str(npc.get(field) or "")
            if value:
                self.assertIn(value[:12], system, field)

    def test_npc_prompt_has_no_five_e_vocabulary(self):
        session, st, _ui, script = npc_session(lines=["……"])
        session.npc_director.dialogue(NPC_ID)
        system = message_system(script.calls[-1])
        for gone in ("法术位", "AC ", "d20", "经验值", "等级"):
            self.assertNotIn(gone, system, gone)


# ================================================================ 同伴 ST 卡
class TestCompanionStAgents(unittest.TestCase):
    def test_mes_example_split_and_macros(self):
        card = card_from_companion({
            "name": "Bella", "persona": "务实的老技工。", "voice": "简短。",
            "mes_example": ("<START>{{user}}：准备好了吗？\n"
                            "{{char}}：{{char}}从不迟到，{{user}}。")})
        self.assertEqual(card.name, "Bella")
        self.assertEqual(card.description, "务实的老技工。")
        self.assertIn("{{char}}", card.mes_example)

    def test_companion_card_book_roundtrip(self):
        book = {"entries": [{"keys": ["假钞"], "content": "纸张来自旧印刷厂。",
                             "insertion_order": 90, "comment": "假钞"}]}
        card = card_from_companion({"name": "Oran", "book": book})
        self.assertTrue(card.book)
        back = type(card).from_dict(card.to_dict())
        self.assertEqual(back.to_dict(), card.to_dict())

    def test_card_from_npc_extension_shape(self):
        card = card_from_npc({"name": NPC_NAME, "persona": "圆滑的掮客。",
                              "voice": "客气。", "desc": "见人三分笑。",
                              "goals": "保住私账。", "secret": "他见过夜里卸货的人。"})
        self.assertEqual(card.name, NPC_NAME)
        self.assertIn("保住私账", str(card.to_dict()))

    def test_companion_agents_use_pregens_not_the_deleted_companions_field(self):
        from helpers import red_module
        mod = red_module()
        self.assertFalse(hasattr(mod, "companions"))       # 5e 字段已删除
        self.assertTrue(mod.pregens)
        state = make_state(red=True)
        session, state, _ui, _adapter = make_agent_session(
            state=state, ui=AutoUI(),
            handler=lambda m, t: ModelResponse(content="（跟着走）"))
        self.assertTrue(state.companions)
        for comp in state.companions:
            self.assertIn(comp.name, [p.get("name") for p in mod.pregens.values()])
            agent = session.pc_agents[comp.name]
            self.assertIn(comp.role_name, agent.system_prompt)
            self.assertIn("combat_action", agent.tool_names)


# ================================================================ 默认模组
class TestCyberpunkRedDefault(unittest.TestCase):
    """默认剧本 = 随包发行的 RED 模组（不再是已删除的 blue-watch）。"""

    def test_default_module_id_and_builtin_ids(self):
        import main as entry
        from trpg.campaign.cordis import BUILTIN_IDS
        self.assertEqual(entry.DEFAULT_MODULE_ID, "cpred-easy-mode")
        self.assertIn("cpred-easy-mode", BUILTIN_IDS)
        self.assertEqual(BUILTIN_IDS, {"cpred-easy-mode"})
        for gone in ("blue-watch", "starfall", "saltmist", "mistgate",
                     "embergate"):
            self.assertNotIn(gone, BUILTIN_IDS)

    def test_cli_fallback_when_default_missing(self):
        """modules 目录为空时仍能开工：回退到代码内置的 RED 数据。"""
        import main as entry
        with tempfile.TemporaryDirectory() as td:
            mod = entry.load_module(td, None)
            self.assertIsNotNone(mod)
            self.assertEqual(mod.source, "builtin")
            self.assertEqual(mod.id, entry.DEFAULT_MODULE_ID)
            # 兜底走的是代码内置数据（与磁盘 JSON 是同一规范的两份实现）
            from trpg.campaign import module_data as M
            self.assertEqual([s["id"] for s in mod.scenes],
                             [s["id"] for s in M.SCENES])

    def test_code_builtin_data_is_red_valid(self):
        from trpg.campaign import module_data as M
        self.assertIn("赛博朋克 RED", M.MODULE["title"])
        self.assertNotIn("蓝望塔", M.MODULE["title"])
        self.assertEqual(M.BUILTIN.id, "cpred-easy-mode")
        errors, _w = validate_module(M.builtin_data())
        self.assertEqual(errors, [])
        for s in M.SCENES:
            self.assertTrue(s.get("id"))
        self.assertTrue(M.PREGENS)
        for _key, pre in M.PREGENS.items():
            self.assertEqual(len(pre["stats"]), 10)
            self.assertIn(pre["role"], D.ROLES)
        # 默认队友必须是已定义的预设角色（兜底数据的硬性不变量）
        for key in M.DEFAULT_COMPANIONS:
            self.assertIn(key, M.PREGENS)

    def test_fixture_module_npc_shape_is_red(self):
        data = fixture_module_data()
        npc = data["npcs"][NPC_ID]
        self.assertNotIn("race", npc)
        self.assertIn("persona", npc)
        mod = Module(data)
        self.assertEqual(mod.npcs[NPC_ID]["name"], NPC_NAME)


if __name__ == "__main__":
    unittest.main()
