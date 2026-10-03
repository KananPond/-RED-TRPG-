"""NPC 子智能体 与 同伴 ST 化 测试。

- NPC = DM 控制的 SubAgent：DM 的 npc_dialogue 工具按需拉起 NPC 子代理，
  子代理只拿 say 一件工具，台词渲染到桌面、进 NPC 持久会话、并入桌面账本
  （DM 下回合"听得见"）；LLM 不可达/未知 NPC/剧本模式时优雅降级。
- 非玩家 PC = 独立 Agent：同伴 agent 走 ctrcg.st 组装线（角色卡字段
  mes_example/book），桌边回合注入世界书上下文（SillyTavern World Info 通路）。
- 默认剧本《蓝望塔》(The Tower of the Blue Watch)：模组校验、内置保护、
  默认 id 与回退。

零 LLM、零网络：全部经 ScriptedAdapter 离线驱动。
"""

import json
import random
import tempfile
import unittest
from pathlib import Path

from ctrcg.campaign.loader import Module, validate_module
from ctrcg.game import state as G
from ctrcg.game.agents import make_dm_agent, make_npc_agent, make_pc_agent
from ctrcg.game.session import GameSession
from ctrcg.harness.adapter import ScriptedAdapter
from ctrcg.harness.loop import DefaultAgentLoop
from ctrcg.harness.models import ModelResponse, ToolCall
from ctrcg.rules.character import Character
from ctrcg.st import card_from_companion, card_from_npc
from ctrcg.ui.cli import AutoUI

DM_MARK = "地下城主"
DM_STRICT = "模组的地下城主"       # DM_SYSTEM_TEMPLATE 独有（NPC 模板只提"地下城主授权"）
NPC_MARK = "子智能体扮演"          # NPC_SYSTEM_TEMPLATE 独有标记
NPC_NAME = "盐官莫三"
NPC_ID = "salt_officer"


# ---------------------------------------------------------------- 模组夹具
def fixture_module_data() -> dict:
    """最小可过校验的模组：1 幕 3 场景、NPC 带 ST 扩展字段、同伴带书。"""
    return {
        "id": "fixture-watch", "title": "测试灯塔", "en_title": "Test Watch",
        "setting": "剑湾岸边的一座测试灯塔。",
        "synopsis": "调查熄灭的信标，重燃蓝焰。", "party_level_start": 1,
        "expected_end_level": 3, "act_titles": {"1": "第一幕"},
        "epilogue": "—— 冒险结束 ——",
        "npcs": {
            NPC_ID: {
                "name": NPC_NAME, "race": "人类",
                "desc": "灯塔镇的盐税官，见人三分笑。",
                "persona": "圆滑的税官，贪财但惜命，绝不硬碰冒险者。",
                "voice": "客客气气，句句留后路。",
                "goals": "保住私账，别被牵进熄灯案。",
                "secret": "他见过夜里卸货的白袍人，收了封口钱。",
            },
        },
        "companions": [
            {
                "id": "c1", "name": "贝拉", "race": "human", "klass": "fighter",
                "background": "soldier", "fighting_style": "defense",
                "scores": {"str": 15, "dex": 13, "con": 14, "int": 10,
                           "wis": 12, "cha": 8},
                "skills": ["athletics", "intimidation"],
                "persona": "务实的老兵。", "voice": "简短。",
                "mes_example": "<START>{{user}}：准备好了吗？\n{{char}}：{{char}}从不迟到，{{user}}。上一次迟到的人已经躺在教堂后院。",
            },
            {
                "id": "c2", "name": "奥兰", "race": "high_elf", "klass": "wizard",
                "background": "sage", "fighting_style": "",
                "scores": {"str": 8, "dex": 14, "con": 12, "int": 16,
                           "wis": 13, "cha": 10},
                "skills": ["arcana", "history"],
                "persona": "好奇的学者。", "voice": "引经据典。",
                "book": {"entries": [
                    {"keys": ["蓝焰"], "content": "蓝焰即守望之誓。",
                     "insertion_order": 90, "comment": "蓝焰"},
                ]},
            },
        ],
        "encounters": {
            "e1": {"title": "滩头拾荒者", "dark": False, "intro": "拾荒者围了上来。",
                   "monsters": [["bandit", 2]], "loot": {"gold": 10, "items": {}}},
        },
        "scenes": [
            {"id": "s1", "act": 1, "location": "灯塔镇码头",
             "intro": "码头的灯柱歪着。", "explore": "打听消息。",
             "skills": [{"skill": "persuasion", "dc": 10, "success": "打听到了。",
                         "fail": "没人搭理。"}],
             "encounter": "e1", "next": "s2", "milestone": 0},
            {"id": "s2", "act": 1, "location": "崖径",
             "intro": "崖径通向灯塔。", "explore": "攀上崖径。",
             "skills": [], "encounter": None, "next": None, "milestone": 100},
        ],
    }


def make_state(seed=5):
    data = fixture_module_data()
    module = Module(data)
    rng = random.Random(seed)
    player = Character.create(
        name="凯尔", race="human", klass="fighter", background="soldier",
        scores={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
        skill_choices=["athletics", "intimidation"], fighting_style="defense")
    comps = [Character.create(
        name=c["name"], race=c["race"], klass=c["klass"], background=c["background"],
        scores=c["scores"], skill_choices=c["skills"],
        fighting_style=c.get("fighting_style", "")) for c in module.companions]
    return G.GameState(player, comps, rng, module=module)


class NpcScript:
    """按系统提示发牌：NPC 子代理先 say 再收束；DM 输出最终叙述；
    同伴返回 inert 文本。记录每次调用的 messages 供断言。"""

    def __init__(self, npc_args=None):
        self.npc_args = npc_args          # say 参数队列
        self.calls: list[list[dict]] = []
        self.npc_runs = 0

    def __call__(self, messages, tools):
        self.calls.append(messages)
        system = str(messages[0].get("content", ""))
        fresh = messages[-1].get("role") == "user"
        if NPC_MARK in system:
            if fresh:                     # 只计 fresh 轮（工具结果回填不计）
                self.npc_runs += 1
            if fresh and self.npc_args:
                args = self.npc_args.pop(0)
                return ModelResponse(content=None, tool_calls=[
                    ToolCall(id="n1", name="say", arguments=args)])
            if fresh:
                return ModelResponse(content="（沉默地打量你们。）")
            return ModelResponse(content="（无话）")
        if DM_MARK in system:
            return ModelResponse(content="DM 收束叙述。")
        return ModelResponse(content="（保持安静，跟随队伍）")


def make_agent_session(handler):
    state = make_state()
    ui = AutoUI()
    adapter = ScriptedAdapter(handler=handler)
    loop = DefaultAgentLoop(registry=None, adapter=adapter)
    module = state.module
    dm = make_dm_agent("test-model", module)
    pc_agents = {}
    for comp in module.companions:
        pc_agents[comp["name"]] = make_pc_agent(
            comp, 1, ["longsword"], "法术位：无", "test-model")
    s = GameSession(state, ui, loop=loop, dm_agent=dm, pc_agents=pc_agents,
                    module=module)
    return s, state, ui, adapter


# ================================================================ NPC 子智能体
class TestNpcSubagent(unittest.TestCase):
    def test_dm_tool_registered(self):
        s, *_ = make_agent_session(NpcScript())
        # DM 的工具面与 agent 工具清单都含 npc_dialogue
        self.assertIn("npc_dialogue", s.dm_agent.tool_names)
        self.assertIn("npc_dialogue", s.registry)

    def test_dialogue_delegates_renders_and_logs(self):
        script = NpcScript(npc_args=[
            {"text": "哎呀，贵客。塔上的事……小人一概不知。"}])
        s, state, ui, _ = make_agent_session(script)
        r = s.registry.invoke("npc_dialogue",
                              {"npc": NPC_NAME, "instruction": "打听熄灯夜。"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["lines"], ["哎呀，贵客。塔上的事……小人一概不知。"])
        # 台词以 NPC 名义渲染到桌面
        self.assertTrue(any(NPC_NAME in h and "小人一概不知" in h
                            for h in ui.history))
        # 并入桌面账本（kind=npc）——同伴的 _recent 与 DM 的 _digest_table 都听得见
        self.assertEqual(len(s.table_log), 1)
        line = s.table_log[0]
        self.assertEqual((line["who"], line["kind"]), (NPC_NAME, "npc"))
        # 记入 NPC 持久会话（模型可见 ⟺ 已记录）
        raw = json.dumps(s.npc_session(NPC_ID).events, ensure_ascii=False)
        self.assertIn("小人一概不知", raw)
        # 用量记账挂在 npc:<id> 名下（NPC 子代理以专属系统提示运行）
        self.assertTrue(any(str(c[0].get("content", "")).startswith("你是跑团桌上")
                            for c in script.calls))

    def test_subagent_memory_persists_across_calls(self):
        script = NpcScript(npc_args=[{"text": "第一句。"}, {"text": "第二句。"}])
        s, *_ = make_agent_session(script)
        s.registry.invoke("npc_dialogue", {"npc": NPC_ID})
        s.registry.invoke("npc_dialogue", {"npc": NPC_ID})
        sess = s.npc_session(NPC_ID)
        self.assertIs(sess, s.npc_session(NPC_ID))     # 同一份持久会话
        raw = json.dumps(sess.events, ensure_ascii=False)
        self.assertIn("第一句。", raw)
        self.assertIn("第二句。", raw)
        self.assertEqual(script.npc_runs, 2)

    def test_unknown_npc_reports_available(self):
        s, *_ = make_agent_session(NpcScript())
        r = s.registry.invoke("npc_dialogue", {"npc": "不存在的人"})
        self.assertFalse(r["ok"])
        self.assertIn(NPC_NAME, r["error"])

    def test_llm_down_degrades_to_dm(self):
        def broken(messages, tools):
            raise RuntimeError("connection refused")

        s, _, ui, _ = make_agent_session(broken)
        r = s.registry.invoke("npc_dialogue", {"npc": NPC_ID})
        self.assertFalse(r["ok"])
        self.assertIn("speak_as_npc", r["error"])      # DM 有明确回退路径
        self.assertEqual(s.table_log, [])               # 没有半个字入账

    def test_scripted_mode_unavailable(self):
        s = GameSession(make_state(), AutoUI(), loop=None)
        self.assertIsNone(s.npc_director)
        r = s.npc_dialogue(NPC_ID)
        self.assertFalse(r["ok"])
        self.assertIn("AI 模式", r["error"])

    def test_npc_system_prompt_st_fields(self):
        data = fixture_module_data()
        npc = {"id": NPC_ID, **data["npcs"][NPC_ID]}
        agent = make_npc_agent(npc, "test-model")
        self.assertEqual(agent.agent_id, f"npc:{NPC_ID}")
        self.assertEqual(agent.temperature, 0.9)
        # persona→角色设定、goals/secret 进各自槽位、宏渲染后无残留
        self.assertIn("圆滑的税官", agent.system_prompt)
        self.assertIn("保住私账", agent.system_prompt)
        self.assertIn("封口钱", agent.system_prompt)
        self.assertIn("地下城主授权的子智能体", agent.system_prompt)

    def test_npc_lines_reach_dm_context(self):
        script = NpcScript(npc_args=[{"text": "白袍人？没见过，没见过。"}])
        s, *_ = make_agent_session(script)
        s.registry.invoke("npc_dialogue", {"npc": NPC_ID})
        s.dm_turn("继续推进。")
        dm_call = next(c for c in script.calls
                       if DM_STRICT in str(c[0].get("content", "")))
        self.assertTrue(any("白袍人" in str(m.get("content"))
                            for m in dm_call[1:]))


# ================================================================ 同伴 ST 化
class TestCompanionStAgents(unittest.TestCase):
    def test_pc_agent_assembles_card_fields(self):
        comp = fixture_module_data()["companions"][0]
        agent = make_pc_agent(comp, 1, ["longsword"], "法术位：无", "test-model",
                              user="凯尔")
        # mes_example 经 <START> 分块进提示词，{{char}}/{{user}} 宏已渲染
        self.assertIn("贝拉从不迟到，凯尔。", agent.system_prompt)
        self.assertNotIn("{{char}}", agent.system_prompt)
        self.assertNotIn("{{user}}", agent.system_prompt)

    def test_card_from_companion_book_roundtrip(self):
        comp = fixture_module_data()["companions"][1]
        card = card_from_companion(comp)
        self.assertIsNotNone(card.book)
        self.assertEqual(card.book.entries[0].keys, ["蓝焰"])
        # to_dict/from_dict 往返不丢书
        rev = type(card).from_dict(card.to_dict())
        self.assertEqual(rev.book.entries[0].content, "蓝焰即守望之誓。")

    def test_card_from_npc_extension_shape(self):
        n = fixture_module_data()["npcs"][NPC_ID]
        card = card_from_npc(n)
        self.assertEqual(card.description, n["persona"])   # persona 优先
        self.assertEqual(card.extensions["goals"], n["goals"])
        self.assertEqual(card.extensions["secret"], n["secret"])
        # 最简形状（module_data NPC：name/race/desc）兜底
        bare = card_from_npc({"name": "甲", "race": "人类", "desc": "路人。"})
        self.assertEqual(bare.description, "人类。路人。")
        self.assertEqual(bare.extensions, {})

    def test_social_wi_context_injected(self):
        script = NpcScript()
        s, state, *_ = make_agent_session(script)
        comp = next(c for c in state.companions if c.name == "奥兰")
        ctx = s.social._wi_context(comp)
        self.assertEqual(len(ctx), 1)
        block = ctx[0]["content"]
        self.assertIn("【世界书】", block)
        self.assertIn("当前场景", block)          # 引擎 constant 场景条目
        # 桌面提到"蓝焰"→ 同伴角色书条目激活
        s.table_log.append({"who": "凯尔", "text": "蓝焰熄灭了……", "to": "",
                            "interrupt": False, "kind": "table"})
        ctx2 = s.social._wi_context(comp)
        self.assertIn("守望之誓", ctx2[0]["content"])
        # 无角色书的同伴同样拿到引擎世界书
        bella = next(c for c in state.companions if c.name == "贝拉")
        self.assertIn("【世界书】", s.social._wi_context(bella)[0]["content"])

    def test_social_turn_carries_wi_to_model(self):
        script = NpcScript()
        s, state, *_ = make_agent_session(script)
        comp = next(c for c in state.companions if c.name == "贝拉")
        s.social._pc_social_turn(comp, "测试触发", "（桌面安静。）")
        run = next(c for c in script.calls
                   if "贝拉" in str(c[0].get("content", "")))
        self.assertTrue(any("【世界书】" in str(m.get("content"))
                            for m in run[1:]))


# ================================================================ 默认剧本蓝望塔
class TestBlueWatchDefault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(
            (Path(__file__).resolve().parent.parent
             / "modules/blue-watch/module.json").read_text(encoding="utf-8"))

    def test_module_validates_clean(self):
        errs, warns = validate_module(self.data)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])            # 经验预算达标（人均 ≥900）

    def test_title_and_builtin_protection(self):
        import main as entry
        from ctrcg.campaign.cordis import BUILTIN_IDS
        self.assertEqual(self.data["title"], "蓝望塔")
        self.assertEqual(self.data["en_title"], "The Tower of the Blue Watch")
        self.assertEqual(entry.DEFAULT_MODULE_ID, "blue-watch")
        self.assertIn("blue-watch", BUILTIN_IDS)

    def test_source_material_adaptation(self):
        """剧本改编自 InternalRockStudio《The Blue Watch Tower》素材：
        三幕（影林/夏叶旅店/蓝望塔）、四种原版怪物、指挥官信件的故事线。"""
        module = Module(self.data)
        # 原版四怪物全部转为引擎自定义怪物，CR/XP 与 SRD 一致
        cm = self.data["custom_monsters"]
        self.assertEqual(
            {k: (v["cr"], v["xp"]) for k, v in cm.items()},
            {"fengling": (0.125, 25), "blue_watcher_skeleton": (0.25, 50),
             "blue_watcher_captain": (0.5, 200), "die_kreatur": (4, 1100)})
        # 指挥官信件的关键事实进 NPC：拉库斯的立场与压下的真相（ST 扩展字段）
        larkus = module.npcs["larkus"]
        card = card_from_npc(larkus)
        self.assertIn("箱子", card.extensions["secret"])
        self.assertEqual(larkus["name"], "指挥官拉库斯")
        # 剧本同伴缺省 → 构队时回退引擎默认三人组（本模组不含自创同伴）
        self.assertEqual(module.companions, [])
        # 三幕结构
        self.assertEqual(len(self.data["scenes"]), 5)
        self.assertEqual(
            {s["act"] for s in self.data["scenes"]}, {1, 2, 3})

    def test_cli_fallback_when_default_missing(self):
        """默认剧本目录缺失时回退到代码内置剧本（游戏永不硬失败）。"""
        import main as entry
        with tempfile.TemporaryDirectory() as td:
            mod = entry.load_module(td, None)
            self.assertIsNotNone(mod)
            self.assertNotEqual(mod.id, entry.DEFAULT_MODULE_ID)  # 回退非蓝望塔
            self.assertEqual(mod.source, "builtin")


if __name__ == "__main__":
    unittest.main()
