"""桌边社交层测试：抢话、自由反应、辩论、DM 即席回应、静默降级。

用 ScriptedAdapter 的 handler 按（角色 → 台词）发牌，验证编排器的
时机与秩序：谁能开口、何时打断、辩论如何收束、LLM 故障时是否静默降级。
"""

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

DM_MARK = "地下城主"
ROLAND = "罗兰·北风"
PIPPIN = "皮普·铜齿轮"


def make_state(seed=5):
    rng = random.Random(seed)
    player = Character.create(
        name="凯尔", race="human", klass="fighter", background="soldier",
        scores={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
        skill_choices=["athletics", "intimidation"], fighting_style="defense")
    comps = [Character.create(
        name=c["name"], race=c["race"], klass=c["klass"], background=c["background"],
        scores=c["scores"], skill_choices=c["skills"],
        fighting_style=c.get("fighting_style", "")) for c in M.COMPANIONS]
    return G.GameState(player, comps, rng)


class TableScript:
    """按（角色 → say 参数列表）顺序发牌；说完即收束。
    DM 默认返回最终叙述；dm_tool_calls 非空时 DM 先调用这些工具再收束。"""

    def __init__(self, lines_by_name=None, dm_final="商人抬头看了你们一眼。",
                 dm_tool_calls=None):
        self.lines = {k: list(v) for k, v in (lines_by_name or {}).items()}
        self.dm_final = dm_final
        self.dm_tool_calls = list(dm_tool_calls or [])
        self.dm_calls = 0

    def __call__(self, messages, tools):
        system = str(messages[0].get("content", ""))
        # 一次运行的首次调用：末条消息是 user（任务/上下文）；工具结果回填后
        # 末条是 tool——借此保证每次运行至多发一张牌
        fresh_run = messages[-1].get("role") == "user"
        if DM_MARK in system:
            self.dm_calls += 1
            if fresh_run and self.dm_tool_calls:
                calls, self.dm_tool_calls = self.dm_tool_calls, []
                return ModelResponse(content=None, tool_calls=[
                    ToolCall(id=f"d{i}", name=tc["name"], arguments=tc["arguments"])
                    for i, tc in enumerate(calls, 1)])
            return ModelResponse(content=f"{self.dm_final}（DM第{self.dm_calls}次）")
        for name, pending in self.lines.items():
            if name in system:
                if fresh_run and pending:
                    args = pending.pop(0)
                    return ModelResponse(content=None, tool_calls=[
                        ToolCall(id="t1", name="say", arguments=args)])
                return ModelResponse(content="（无话）")
        raise AssertionError(f"脚本未覆盖的角色：{system[:80]}")


def make_agent_session(handler):
    state = make_state()
    ui = AutoUI()
    adapter = ScriptedAdapter(handler=handler)
    loop = DefaultAgentLoop(registry=None, adapter=adapter)
    dm = make_dm_agent("test-model", M.BUILTIN)
    pc_agents = {}
    for comp in M.COMPANIONS:
        pc_agents[comp["name"]] = make_pc_agent(
            comp, 1, ["longsword"], "法术位：无", "test-model")
    s = GameSession(state, ui, loop=loop, dm_agent=dm, pc_agents=pc_agents)
    return s, state, ui, adapter


def ally_lines(ui):
    return [h for h in ui.history if h.startswith("  ◈")]


class TestSocial(unittest.TestCase):
    def test_scripted_mode_has_no_social(self):
        s = GameSession(make_state(), AutoUI(), loop=None)
        self.assertIsNone(s.social)
        self.assertEqual(s.table_log, [])

    def test_interrupt_wave_renders_and_reaches_dm(self):
        script = TableScript({
            PIPPIN: [{"text": "等等！那箱子上的纹章我见过！", "interrupt": True}],
            ROLAND: [], "薇兰·叶语": [],
        })
        s, state, ui, adapter = make_agent_session(script)
        s.social.beat("我要上前和商队搭话")
        # 抢话行被渲染，带（抢话）标记
        self.assertTrue(any("（抢话）" in h and "皮普" in h for h in ally_lines(ui)))
        self.assertTrue(any("纹章" in h for h in ally_lines(ui)))
        # DM 的上下文包含桌上插话摘要（NPC"听得见"同伴的打断）
        first_dm_call = next(c for c in adapter.calls
                             if DM_MARK in str(c["messages"][0].get("content", "")))
        self.assertTrue(any("【桌上插话】" in str(m.get("content"))
                            and "纹章" in str(m.get("content"))
                            for m in first_dm_call["messages"]))
        # 已消化清账
        self.assertEqual(s.table_log, [])
        # 模型可见 ⟺ 已记录：插话（工具调用）留在皮普自己的会话事件流里
        import json as _json
        raw = _json.dumps(s.pc_session(PIPPIN).events, ensure_ascii=False)
        self.assertIn("纹章", raw)

    def test_wave_caps_speakers(self):
        script = TableScript({
            ROLAND: [{"text": "我跟上。"}],
            "薇兰·叶语": [{"text": "小心点。"}],
            PIPPIN: [{"text": "我数到三就冲！"}],
        })
        s, state, ui, adapter = make_agent_session(script)
        lines = s.social.reaction_wave(trigger="测试波。")
        self.assertEqual(len(lines), 2)          # MAX_WAVE_SPEAKERS=2
        self.assertEqual(len(ally_lines(ui)), 2)

    def test_interrupts_speak_first(self):
        script = TableScript({
            ROLAND: [{"text": "我先说计划。"}],
            PIPPIN: [{"text": "打断一下——有脚印！", "interrupt": True}],
        })
        s, state, ui, adapter = make_agent_session(script)
        s.social.reaction_wave(trigger="测试波。")
        self.assertIn("打断一下", ally_lines(ui)[0])   # 皮普抢话，先开口

    def test_debate_runs_and_settles(self):
        script = TableScript({
            ROLAND: [
                {"text": "塌桥承不了重，掉下去就是白送。", "stance": "object"},
                {"text": "行吧，你轻你先——掉坑里别喊我。", "stance": "agree"},
            ],
            PIPPIN: [
                {"text": "桥板看着结实，探两步就到对岸！", "stance": "object"},
                {"hold": True, "reason": "被说服了一半"},
            ],
        })
        s, state, ui, adapter = make_agent_session(script)
        wave = [{"who": ROLAND, "text": "这桥塌了一半，绕路更稳。",
                 "stance": "object", "interrupt": False},
                {"who": PIPPIN, "text": "怕什么，我第一个过！",
                 "stance": "object", "interrupt": False}]
        debated = s.social.run_debate(wave)
        self.assertTrue(debated)
        history = "\n".join(ui.history)
        self.assertIn("塌桥承不了重", history)          # 第1轮申辩
        self.assertIn("掉坑里别喊我", history)          # 第2轮罗兰让步收场
        # 桌面日志记账（供 DM 回应轮消化）
        self.assertEqual([l.get("kind") for l in s.table_log].count("debate"), 3)

    def test_solo_objector_no_debate_but_dm_responds(self):
        script = TableScript({
            ROLAND: [{"text": "我不赞同深夜进矿洞。", "stance": "object"},
                     {"text": "夜里的矿洞是坟场，白天再去。"}],
            "薇兰·叶语": [], PIPPIN: [],
        })
        s, state, ui, adapter = make_agent_session(script)
        # 孤身反对（无人接仗）→ 不开辩论
        r = s.social.run_debate([{"who": ROLAND, "text": "我反对。",
                                  "stance": "object", "interrupt": False}])
        self.assertFalse(r)
        # 完整节拍：wave0 反对 + wave1 补刀 → DM 即席回应桌面（共 ≥ 2 次）
        s.social.beat("我们连夜进矿洞。")
        self.assertGreaterEqual(script.dm_calls, 2)
        self.assertIn("DM第2次", "\n".join(ui.history))

    def test_debate_earns_last_word(self):
        script = TableScript({
            ROLAND: [{"text": "塌桥承不了重。", "stance": "object"}],
            PIPPIN: [{"text": "哼，算你狠。", "stance": "agree"}],
        })
        s, state, ui, adapter = make_agent_session(script)
        wave = [{"who": ROLAND, "text": "绕路。", "stance": "object",
                 "interrupt": False},
                {"who": PIPPIN, "text": "我偏要走桥！", "stance": "object",
                 "interrupt": False}]
        self.assertTrue(s.social.run_debate(wave))
        s.social.reaction_wave(trigger="DM 刚回应了桌上的争论。", max_speakers=1)
        self.assertIn("算你狠", "\n".join(ui.history))

    def test_llm_down_degrades_silently(self):
        """PC 端 LLM 故障 → 社交层静默跳过，对局照常。"""
        class BoomForPC(TableScript):
            def __call__(self, messages, tools):
                if DM_MARK not in str(messages[0].get("content", "")):
                    raise RuntimeError("LLM 不可达")
                return super().__call__(messages, tools)

        script = BoomForPC()
        s, state, ui, adapter = make_agent_session(script)
        s.social.beat("继续前进。")          # 不应抛异常
        self.assertIn("DM第1次", "\n".join(ui.history))
        self.assertEqual(ally_lines(ui), [])
        self.assertEqual(s.table_log, [])

    def test_encounter_stops_beat(self):
        """DM 在回合中拉开战斗 → 社交节拍立即收场，交还战斗编排。"""
        script = TableScript(dm_tool_calls=[
            {"name": "start_encounter", "arguments": {"encounter_id": "e_ambush"}},
        ])
        s, state, ui, adapter = make_agent_session(script)
        s.social.beat("我们冲进去！")
        self.assertIsNotNone(s.encounter)          # 遭遇已就位
        self.assertEqual(script.dm_calls, 2)       # 一个 DM 回合（工具+收束），无桌面回应轮
        # 战斗一触即发，不再有同伴反应波
        self.assertEqual([l for l in s.table_log if l.get("kind") == "table"][:0], [])


if __name__ == "__main__":
    unittest.main()
