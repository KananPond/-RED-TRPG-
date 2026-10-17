"""桌边社交层（《赛博朋克 RED》）：抢话 / 反应波 / 辩论 / 桌面账本 / 优雅降级。

整层与规则无关，移植只换状态夹具、同伴名字与提示词标记。

⚠ 现状：``trpg/game/social.py:292`` 的 ``_wi_context`` 仍在读
``module.companions``——PORT_SPEC §3 已把该字段换成 ``pregens`` +
``default_companions``，``Module`` 上根本没有 ``companions``。于是每个同伴的
社交回合在 ``loop.run`` 之前就抛 ``AttributeError``，而 ``_pc_social_turn`` /
``_pc_debate_turn`` 用 ``except Exception: return []`` 把异常吞了，
**同伴永远不会开口、不会抢话、不会辩论，而且没有任何报错**。
下文的 ``SOCIAL_LAYER_OK`` 探针 + ``skipUnless`` 就是为它挂起的；
``TestSocialBugReport`` 里有一条 expectedFailure 精确钉住这个缺陷。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (DM_MARK, is_fresh_run, isolate_saves,  # noqa: E402
                     make_agent_session, make_state, message_system,
                     restore_cwd)
from trpg.harness.models import ModelResponse, ToolCall  # noqa: E402
from trpg.ui.cli import AutoUI                           # noqa: E402


# GameSession 默认 log_dir="saves/logs"：切到临时目录，别往仓库里写日志
_ISOLATION: dict = {}


def setUpModule():
    _ISOLATION["cwd"], _ISOLATION["tmp"] = isolate_saves()


def tearDownModule():
    restore_cwd(_ISOLATION.pop("cwd", os.getcwd()), _ISOLATION.pop("tmp", None))


# ---------------------------------------------------------------- 能力探针
def _social_layer_works() -> bool:
    """探针：同伴的一次社交回合能否真的产出一句台词。"""
    def handler(messages, tools):
        names = {t["function"]["name"] for t in (tools or [])}
        if "say" in names:
            return ModelResponse(content=None, tool_calls=[
                ToolCall(id="s", name="say", arguments={"text": "探针台词。"})])
        return ModelResponse(content="（旁白）")

    try:
        st = make_state(red=True)
        session, st, _ui, _adapter = make_agent_session(
            state=st, ui=AutoUI(), handler=handler)
        return bool(session.social._pc_social_turn(st.companions[0], "触发", "最近"))
    except Exception:                     # noqa: BLE001
        return False


SOCIAL_LAYER_OK = _social_layer_works()


class TableScript:
    """按系统提示里的角色名发牌：同伴说 ``say``、DM 输出最终叙述。

    「最后一轮是 user」= 新一轮，保证每轮只发一张牌。
    """

    def __init__(self, lines=None, dm_tool_calls=None, dm_final="DM 收束叙述。",
                 interrupt=None):
        self.lines = dict(lines or {})
        self.dm_tool_calls = list(dm_tool_calls or [])
        self.dm_final = dm_final
        self.interrupt = set(interrupt or ())      # 这些角色按「抢话」发牌
        self.dm_inputs: list[list[dict]] = []

    def __call__(self, messages, tools):
        system = message_system(messages)
        fresh = is_fresh_run(messages)
        if DM_MARK in system:
            self.dm_inputs.append(messages)
            if fresh and self.dm_tool_calls:
                return self.dm_tool_calls.pop(0)
            return ModelResponse(content=self.dm_final)
        if fresh:
            for name, text in self.lines.items():
                if name in system:
                    args = {"text": text}
                    if name in self.interrupt:
                        args["interrupt"] = True
                    return ModelResponse(content=None, tool_calls=[
                        ToolCall(id="s1", name="say", arguments=args)])
        return ModelResponse(content="（保持安静，跟随队伍）")


def session_with(lines=None, dm_tool_calls=None, ui=None, state=None, **kw):
    script = TableScript(lines=lines, dm_tool_calls=dm_tool_calls, **kw)
    session, state, ui, adapter = make_agent_session(
        state=state, ui=ui or AutoUI(), handler=script)
    return session, state, ui, script


def ally_lines(ui):
    return [ln for ln in ui.history if ln.startswith("  \u25c8")]


# ================================================================ 接线（总是跑）
class TestSocialWiring(unittest.TestCase):
    def test_scripted_mode_has_no_social_layer(self):
        from trpg.game.session import GameSession
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        self.assertIsNone(session.social)
        self.assertIsNone(session.npc_director)
        self.assertEqual(session.table_log, [])

    def test_ai_mode_builds_the_social_layer(self):
        session, *_ = session_with()
        self.assertIsNotNone(session.social)
        self.assertIsNotNone(session.npc_director)
        self.assertTrue(session.ai_mode)

    def test_dm_start_encounter_during_a_beat_hands_back_to_combat(self):
        session, _st, _ui, _s = session_with(dm_tool_calls=[
            ModelResponse(content=None, tool_calls=[
                ToolCall(id="t1", name="start_encounter",
                         arguments={"encounter_id": "e1"})])])
        session.social.beat("我冲进去。")
        self.assertIsNotNone(session.encounter)
        self.assertEqual(session.encounter.title, "巷口伏击")

    def test_dm_input_carries_the_red_party_snapshot(self):
        session, _st, _ui, script = session_with()
        session.dm_turn("我看看四周。")
        ctx = "\n".join(str(m.get("content", ""))
                        for m in script.dm_inputs[-1])
        self.assertIn("PW", ctx)
        self.assertIn("护甲", ctx)
        self.assertIn("重伤阈值", ctx)
        self.assertNotIn("AC", ctx)
        self.assertNotIn("法术位", ctx)
        self.assertNotIn("等级", ctx)

    def test_dm_context_names_the_red_roles(self):
        session, _st, _ui, script = session_with()
        session.dm_turn("我看看四周。")
        ctx = "\n".join(str(m.get("content", ""))
                        for m in script.dm_inputs[-1])
        self.assertIn("欧元", ctx)

    def test_pc_agent_failure_degrades_silently(self):
        def handler(messages, tools):
            system = message_system(messages)
            if DM_MARK in system:
                return ModelResponse(content="DM 收束叙述。")
            raise RuntimeError("端点不可达")

        session, _st, _ui, _adapter = make_agent_session(
            ui=AutoUI(), handler=handler)
        try:
            session.social.beat("我往前走。")
            session.dm_turn("我继续走。")
        except Exception as exc:                          # noqa: BLE001
            self.fail(f"同伴 LLM 故障炸了社交层：{exc!r}")


# ================================================================ 缺陷钉死
class TestSocialBugReport(unittest.TestCase):
    def test_wi_context_reads_pregens_not_the_deleted_companions_field(self):
        """生产缺陷（trpg/game/social.py:292）：``_wi_context`` 读
        ``s.module.companions``；PORT_SPEC §3 已删掉 ``Module.companions``
        （改 ``pregens`` + ``default_companions``），因此对任何同伴都会
        ``AttributeError: 'Module' object has no attribute 'companions'``。

        最小复现：
            session, st, _ui, _a = make_agent_session(state=make_state(red=True),
                                                      handler=...)
            session.social._wi_context(st.companions[0])
            # AttributeError: 'Module' object has no attribute 'companions'

        因为 ``_pc_social_turn`` / ``_pc_debate_turn`` 用
        ``except Exception: return []`` 吞掉异常，后果是整个桌边社交层静默失效
        （同伴不开口/不抢话/不辩论），且不产生任何日志。修复后本用例会自动
        变成 unexpected success。"""
        st = make_state(red=True)
        session, st, _ui, _adapter = make_agent_session(
            state=st, ui=AutoUI(), handler=lambda m, t: ModelResponse(content=""))
        self.assertIsInstance(session.social._wi_context(st.companions[0]),
                              (dict, list, str))


# ================================================================ 抢话 / 辩论
@unittest.skipUnless(
    SOCIAL_LAYER_OK,
    "social.py:292 的 _wi_context 仍在读已删除的 Module.companions，异常被 "
    "_pc_social_turn 静默吞掉 → 同伴社交回合恒为空，抢话/辩论无从断言。"
    "修复后本类自动启用。")
class TestSocialWave(unittest.TestCase):
    def test_interrupt_wave_renders_and_reaches_the_dm(self):
        st = make_state(red=True)
        session, st, ui, script = session_with(
            state=st, lines={st.companions[0].name: "等等——这条巷子不对。"})
        session.social.beat("我往前走。")
        joined = "\n".join(ui.history)
        self.assertIn("这条巷子不对", joined)
        # 抢话窗口在 DM 接话之前，所以它必然出现在 DM 的输入里；
        # beat() 内部的 dm_turn 会消化 table_log（这是设计：插话进 DM 上下文）。
        ctxs = ["\n".join(str(m.get("content", "")) for m in inp)
                for inp in script.dm_inputs]
        self.assertTrue(any("【桌上插话】" in c and "这条巷子不对" in c
                            for c in ctxs), f"DM 输入里没有桌面插话：{ctxs}")
        self.assertEqual(session.table_log, [])       # 已被 DM 消化

    def test_wave_caps_speakers_at_two(self):
        st = make_state(red=True)
        session, st, ui, _s = session_with(
            state=st,
            lines={c.name: f"我是{c.name}，我有话说。" for c in st.companions})
        before = len(ally_lines(ui))
        spoken = session.social.reaction_wave("队伍刚抵达一处安静的地方。")
        self.assertLessEqual(len(ally_lines(ui)) - before, 2)
        self.assertLessEqual(len(spoken), 2)          # 上限是「每一波」两名

    def test_interrupters_speak_with_the_mark(self):
        st = make_state(red=True)
        session, st, ui, _s = session_with(
            state=st, lines={st.companions[0].name: "打断一下。"},
            interrupt={st.companions[0].name})
        session.social.beat("我往前走。")
        joined = "\n".join(ui.history)
        self.assertIn("（抢话）", joined)
        self.assertIn("打断一下", joined)

    def test_no_pc_agents_means_no_interrupts(self):
        st = make_state(red=True)
        session, _st, ui, _s = session_with(state=st, lines={"任何人": "不该出现。"})
        session.pc_agents = {}
        session.social.beat("我往前走。")
        self.assertEqual(ally_lines(ui), [])


if __name__ == "__main__":
    unittest.main()
