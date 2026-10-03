"""AI PC（AI 代打玩家角色）主循环集成测试 —— ScriptedAdapter 模式。

沿用 tests/test_game.py 的 ScriptedAdapter 手法与 tests/test_social.py 的
按（角色 → 台词）发牌模式（这里按 system prompt 中的角色名分牌）。
覆盖 docs/aipc-design.md §5 测试表的主循环分支：
探索接管（intent/say/无工具/世界行动）、工具 schema 全量下发、战斗接管与
启发式兜底、掷骰门控、反应不弹菜单、短休/升级默认选择、社交波与辩论计入
AI PC、非交互购买、LLM 故障交还人类、开关关闭零行为回归、存档往返、
名字冲突降级、WebUI 旁听指令槽。

随机性说明：断言全部设计为不依赖具体骰值（治疗下限 ≥ 缺口、攻击必有日志行），
GameState 内部自建随机源即可，测试文件不持有随机数实例。
"""

import json
import tempfile
import unittest
from pathlib import Path

from ctrcg.campaign import module_data as M
from ctrcg.game import state as G
from ctrcg.game.agents import make_dm_agent, make_pc_agent
from ctrcg.game.aipc import AIPCController
from ctrcg.game.session import GameSession
from ctrcg.harness.adapter import ScriptedAdapter
from ctrcg.harness.loop import DefaultAgentLoop
from ctrcg.harness.models import ModelResponse, ToolCall
from ctrcg.rules.character import Character
from ctrcg.st import CharacterCard, Persona
from ctrcg.ui.cli import AutoUI
from ctrcg.ui.webui import WebUI

PLAYER = "凯尔"
ROLAND = "罗兰·北风"
VEILAN = "薇兰·叶语"
PIPPIN = "皮普·铜齿轮"
DM_MARK = "地下城主"


def make_state():
    player = Character.create(
        name=PLAYER, race="human", klass="fighter", background="soldier",
        scores={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
        skill_choices=["athletics", "intimidation"], fighting_style="defense")
    comps = [Character.create(
        name=c["name"], race=c["race"], klass=c["klass"], background=c["background"],
        scores=c["scores"], skill_choices=c["skills"],
        fighting_style=c.get("fighting_style", "")) for c in M.COMPANIONS]
    return G.GameState(player, comps)


def make_card(**kw):
    kw.setdefault("name", PLAYER)
    kw.setdefault("description", "谨慎的老兵，先观察再行动。")
    kw.setdefault("personality", "低沉，简洁，讨厌废话。")
    kw.setdefault("post_history_instructions", "回合结束前用一句话总结你的意图。")
    return CharacterCard(**kw)


class CountUI(AutoUI):
    """AutoUI + choose/free_input 计数（断言 AI PC 模式不再触达人类交互点）。"""

    def __init__(self):
        super().__init__()
        self.choose_calls = 0
        self.free_inputs = 0

    def choose(self, prompt, options):
        self.choose_calls += 1
        return super().choose(prompt, options)

    def free_input(self, prompt):
        self.free_inputs += 1
        return super().free_input(prompt)


class AipcScript:
    """按 system prompt 中的角色名发牌：dm=DM 牌队列、aipc=玩家牌队列、
    lines=同伴（反应波/辩论）按名发牌。牌是 (tool_name, arguments) 简写或
    ModelResponse；队列耗尽后回落到惰性收束（无工具的纯文本）。"""

    def __init__(self, dm=None, aipc=None, lines=None,
                 dm_final="商人抬头看了你们一眼。"):
        self.dm = list(dm or [])
        self.aipc = list(aipc or [])
        self.lines = {k: list(v) for k, v in (lines or {}).items()}
        self.dm_final = dm_final
        self.dm_inputs: list[str] = []      # DM 收到的完整消息文本（审计）
        self.aipc_tools: list[list[str]] = []  # 玩家 agent 每次调用的工具名单
        self.player_calls = 0

    def __call__(self, messages, tools):
        system = str(messages[0].get("content", ""))
        names = [t["function"]["name"] for t in (tools or [])]
        if DM_MARK in system:
            self.dm_inputs.append("\n".join(str(m.get("content")) for m in messages))
            return self._draw(self.dm) or ModelResponse(content=self.dm_final)
        if PLAYER in system:
            self.player_calls += 1
            self.aipc_tools.append(names)
            return self._draw(self.aipc) or ModelResponse(content="（观望）")
        for name, pending in self.lines.items():
            if name in system:
                return self._draw(pending) or ModelResponse(content="（无话）")
        raise AssertionError(f"脚本未覆盖的角色：{system[:80]}")

    @staticmethod
    def _draw(queue):
        if not queue:
            return None
        item = queue.pop(0)
        if isinstance(item, ModelResponse):
            return item
        name, args = item
        return ModelResponse(content=None, tool_calls=[
            ToolCall(id="t1", name=name, arguments=args)])


def make_agent_session(handler=None, script=None, adapter=None, attach=True):
    """AI 模式会话：默认三人组同伴 agent + 可选 AI PC 挂载。"""
    state = make_state()
    ui = CountUI()
    adapter = adapter or ScriptedAdapter(script=list(script or []), handler=handler)
    loop = DefaultAgentLoop(registry=None, adapter=adapter)
    dm = make_dm_agent("test-model", M.BUILTIN)
    pc_agents = {}
    for comp in M.COMPANIONS:
        pc_agents[comp["name"]] = make_pc_agent(
            comp, 1, ["longsword"], "法术位：无", "test-model")
    s = GameSession(state, ui, loop=loop, dm_agent=dm, pc_agents=pc_agents)
    if attach:
        aipc = AIPCController.attach(
            s, loop, "test-model", make_card(),
            Persona(name=PLAYER, description="队伍信赖的老兵。"))
        assert aipc is not None
    return s, state, ui, adapter


def _session_for(state, handler=None):
    """给定 state 的 AI 模式会话（升级测试用非默认玩家时复用）。"""
    ui = CountUI()
    loop = DefaultAgentLoop(registry=None,
                            adapter=ScriptedAdapter(handler=handler or AipcScript()))
    dm = make_dm_agent("test-model", M.BUILTIN)
    pc_agents = {c["name"]: make_pc_agent(c, 1, ["longsword"], "法术位：无", "test-model")
                 for c in M.COMPANIONS}
    s = GameSession(state, ui, loop=loop, dm_agent=dm, pc_agents=pc_agents)
    AIPCController.attach(s, loop, "test-model", make_card(),
                          Persona(name=state.player.name))
    return s, ui, loop.adapter


def begin_ambush(s, state):
    s.launch_encounter("e_ambush")
    enc = s.encounter
    enc.begin(state.rng)
    return enc


# ---------------------------------------------------------------- 开关关闭
class TestAipcOff(unittest.TestCase):
    """开关关闭（不 attach）：所有新分支短路，行为与基线一致。"""

    def test_off_zero_change(self):
        script = AipcScript(lines={ROLAND: [("say", {"text": "我跟上。"})]})
        s, state, ui, _adapter = make_agent_session(handler=script, attach=False)
        self.assertIsNone(s.aipc)
        self.assertNotIn(PLAYER, s.pc_agents)
        # 抢话波只含同伴（玩家不在 pc_agents，被 party() ∩ pc_agents 剔除）
        lines = s.social.reaction_wave(trigger="测试波。")
        self.assertEqual([l["who"] for l in lines], [ROLAND])
        self.assertEqual(script.player_calls, 0)      # 玩家 agent 从未被咨询
        # 玩家战斗回合仍走 ui.choose 菜单流
        enc = begin_ambush(s, state)
        s.player_combat_turn(enc)
        self.assertGreater(ui.choose_calls, 0)
        # 玩家本人检定仍触发掷骰按钮
        gate = _RollGateUI()
        s2 = GameSession(make_state(), gate, loop=None)
        r = s2.tools._tool_skill_check(PLAYER, "athletics", 10)
        self.assertEqual(len(gate.rolls_asked), 1)
        self.assertIn("total", r)                     # 引擎结算不变
        # 旧档（无 aipc 键）读档 → 开关关闭
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "save.json"
            G.save_game(state, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(raw["aipc"]["enabled"])
            raw.pop("aipc")
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            st2 = G.load_game(path)
        self.assertFalse(st2.aipc_enabled)
        self.assertIsNone(st2.persona)
        self.assertIsNone(st2.aipc_card)


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


# ---------------------------------------------------------------- 探索接管
class TestAipcExplore(unittest.TestCase):
    def test_explore_takeover_intent(self):
        script = AipcScript(aipc=[("explore_intent", {"text": "检查箱子"})])
        s, state, ui, _ = make_agent_session(handler=script)
        self.assertTrue(s.aipc.explore_beat())
        # DM 收到意图输入（走完整社交节拍），人类输入从未被触达
        self.assertTrue(any("检查箱子" in x and "【玩家输入】" in x
                            for x in script.dm_inputs))
        self.assertEqual(ui.free_inputs, 0)
        self.assertEqual(s.aipc._last_narration, "检查箱子")
        # 工具面全量下发：探索 beat 首调用的 registry 含 say/explore_intent/
        # explore_act（若误用同伴的 tool_names 列表，explore_act 会被 schema
        # 过滤静默吞掉；后续调用是社交波的 say/hold registry，不在此断言内）
        self.assertTrue(script.aipc_tools)
        self.assertLessEqual({"say", "explore_intent", "explore_act"},
                             set(script.aipc_tools[0]))

    def test_say_reaches_table_log_and_dm(self):
        script = AipcScript(aipc=[("say", {"text": "这纹章我哪见过……", "to": "商人"})])
        s, state, ui, _ = make_agent_session(handler=script)
        self.assertTrue(s.aipc.explore_beat())
        # say-only 节拍触发 social.beat（"说话即输入"），不是"按兵不动"兜底
        self.assertFalse(any("按兵不动" in x for x in script.dm_inputs))
        # AI PC 的话进了桌面账本并被 DM 消化（模型可见 ⟺ 已记录）
        self.assertTrue(any("这纹章我哪见过" in x and "【桌上插话】" in x
                            for x in script.dm_inputs))
        self.assertEqual(s.table_log, [])             # _digest_table 已清账
        self.assertTrue(any("这纹章我哪见过" in h for h in ui.history))
        raw = json.dumps(s.pc_session(PLAYER).events, ensure_ascii=False)
        self.assertIn("这纹章我哪见过", raw)

    def test_no_tool_fallback_keeps_moving(self):
        script = AipcScript(aipc=[])                  # 纯文本收束
        s, state, ui, _ = make_agent_session(handler=script)
        self.assertTrue(s.aipc.explore_beat())
        self.assertTrue(any("（凯尔 按兵不动。）" in x for x in script.dm_inputs))

    def test_explore_act_rest_short_no_ui(self):
        script = AipcScript(aipc=[("explore_act", {"kind": "rest_short"})])
        s, state, ui, _ = make_agent_session(handler=script)
        player = state.player
        player.hp_cur = player.hp_max - 2             # 缺口 ≤ 生命骰回复下限(1d10+2)
        before_dice = player.hit_dice_used
        self.assertTrue(s.aipc.explore_beat())
        # 短休由引擎结算（spend_dice=None 自动花骰，零 UI、零 LLM 数值）
        self.assertEqual(player.hp_cur, player.hp_max)
        self.assertEqual(player.hit_dice_used, before_dice + 1)
        self.assertEqual(ui.choose_calls, 0)
        self.assertTrue(any("短休" in x for x in script.dm_inputs))  # 世界回应

    def test_llm_down_explore_hands_back(self):
        class BoomAdapter(ScriptedAdapter):
            def chat(self, *a, **kw):
                raise RuntimeError("网络不可达")

        class QuitUI(CountUI):
            def free_input(self, prompt):
                self.free_inputs += 1
                return "/退出"

        s, state, ui, _ = make_agent_session(
            adapter=BoomAdapter(handler=AipcScript()))
        self.assertFalse(s.aipc.explore_beat())
        # 循环级：降级提示后交还人类输入；/退出 可退出
        s.ui = ui = QuitUI()
        result = s.ai_explore_turns(state.module.scene_by_id(state.scene_id))
        self.assertEqual(result, "quit")
        self.assertTrue(any("AI 代打暂不可用" in h for h in ui.history))
        self.assertEqual(ui.free_inputs, 1)


# ---------------------------------------------------------------- 战斗接管
class TestAipcCombat(unittest.TestCase):
    def test_combat_takeover(self):
        script = AipcScript(aipc=[("combat_action",
                                   {"action": "attack", "weapon": "longsword"})])
        s, state, ui, _ = make_agent_session(handler=script)
        enc = begin_ambush(s, state)
        before = len(enc.log)
        s.player_combat_turn(enc)
        # combat_action 确实出手（长剑攻击入账），全程零人类菜单
        self.assertGreater(len(enc.log), before)
        self.assertTrue(any("长剑" in line for line in enc.log[before:]))
        self.assertEqual(ui.choose_calls, 0)
        # 战斗 registry 同样全量下发（say/combat_action/explore_intent）
        self.assertTrue(script.aipc_tools)
        for names in script.aipc_tools:
            self.assertLessEqual({"say", "combat_action", "explore_intent"},
                                 set(names))

    def test_combat_fallback_heuristic_when_llm_down(self):
        class BoomAdapter(ScriptedAdapter):
            def chat(self, *a, **kw):
                raise RuntimeError("网络不可达")

        s, state, ui, _ = make_agent_session(
            adapter=BoomAdapter(handler=AipcScript()))
        enc = begin_ambush(s, state)
        before = len(enc.log)
        s.player_combat_turn(enc)          # RuntimeError → _scripted_pc_turn 兜底
        self.assertGreater(len(enc.log), before)
        self.assertEqual(ui.choose_calls, 0)


# ---------------------------------------------------------------- 掷骰 / 反应 / 休整 / 升级
class TestAipcEngines(unittest.TestCase):
    def test_roll_gate_bypassed_when_aipc(self):
        gate = _RollGateUI()
        s, state, ui, _ = make_agent_session(handler=AipcScript())
        s.ui = gate                        # 换上有掷骰按钮的 UI 桩，验证门控条件本身
        s.tools.ui = gate
        r = s.tools._tool_skill_check(PLAYER, "athletics", 10)
        self.assertEqual(gate.rolls_asked, [])        # AI 模式不弹人类按钮
        self.assertIn("total", r)                     # 引擎结算不变
        r2 = s.tools._tool_save(PLAYER, "dex", 12)
        self.assertEqual(gate.rolls_asked, [])
        self.assertIn("total", r2)

    def test_reaction_no_menu_when_aipc(self):
        s, state, ui, _ = make_agent_session(handler=AipcScript())
        self.assertIsNone(s._reaction_prompt(state.player, {"reaction": "shield"}))
        self.assertEqual(ui.choose_calls, 0)          # 恒不弹人类反应菜单

    def test_short_rest_no_prompt_when_aipc(self):
        s, state, ui, _ = make_agent_session(handler=AipcScript())
        player = state.player
        player.hp_cur = player.hp_max - 2             # 缺口 ≤ 生命骰回复下限
        s.party_short_rest()
        self.assertEqual(ui.choose_calls, 0)          # 不问"消耗几颗生命骰"
        self.assertEqual(player.hp_cur, player.hp_max)
        self.assertEqual(player.hit_dice_used, 1)

    def test_level_up_defaults_no_prompt(self):
        s, state, ui, _ = make_agent_session(handler=AipcScript())
        state.player.level_up_to(4)
        self.assertTrue(state.player.pending_feature_choices())
        s.resolve_pending_choices()
        fc = state.player.feature_choices
        self.assertTrue(str(fc.get("asi", "")).startswith("feat:"))
        self.assertEqual(ui.choose_calls, 0)

    def test_level_up_draconic_hp_fixup(self):
        """术士玩家走默认选择：龙脉血统补齐每级 +1 HP（与交互路径共用 fixup）。"""
        player = Character.create(
            name="岚", race="human", klass="sorcerer", background="sage",
            scores={"str": 8, "dex": 13, "con": 14, "int": 12, "wis": 10, "cha": 15},
            skill_choices=["arcana"])
        comps = [Character.create(
            name=c["name"], race=c["race"], klass=c["klass"],
            background=c["background"], scores=c["scores"],
            skill_choices=c["skills"],
            fighting_style=c.get("fighting_style", "")) for c in M.COMPANIONS]
        state = G.GameState(player, comps)
        s, ui, _adapter = _session_for(state)
        hp_before = player.hp_max
        s.resolve_pending_choices()
        self.assertEqual(player.feature_choices.get("origin"), "draconic")
        self.assertEqual(player.hp_max, hp_before + player.level)
        self.assertEqual(player.hp_cur, player.hp_max)
        self.assertEqual(ui.choose_calls, 0)


# ---------------------------------------------------------------- 桌边社交
class TestAipcSocial(unittest.TestCase):
    def test_player_in_reaction_wave(self):
        script = AipcScript(aipc=[("say", {"text": "都让让，我来看这具尸体。",
                                           "interrupt": True})],
                            lines={ROLAND: [("say", {"text": "我跟上。"})]})
        s, state, ui, _ = make_agent_session(handler=script)
        lines = s.social.reaction_wave(trigger="测试波。")
        # 玩家计入抢话波（party ∩ pc_agents），抢话者先开口
        self.assertEqual([l["who"] for l in lines], [PLAYER, ROLAND])
        self.assertTrue(any(l["who"] == PLAYER and l.get("kind") == "table"
                            for l in s.table_log))
        self.assertIn("都让让，我来看这具尸体", "\n".join(ui.history))

    def test_debate_player_can_speak(self):
        """by_name = party ∩ pc_agents 的回归：玩家被邀请进辩论后能真实发言，
        不会首轮即被"淘汰"（修复前 _debate_round 只映射同伴、对玩家不发牌）。"""
        script = AipcScript(
            aipc=[("say", {"text": "塌桥承不了重，掉下去白送。", "stance": "object"}),
                  ("say", {"text": "行吧，绕路走。", "stance": "agree"})],
            lines={ROLAND: [("say", {"text": "怕什么，我第一个过！", "stance": "object"}),
                            ("hold", {"reason": "被说服了"})]})
        s, state, ui, _ = make_agent_session(handler=script)
        wave = [{"who": PLAYER, "text": "这桥塌了一半，绕路更稳。",
                 "stance": "object", "interrupt": False},
                {"who": ROLAND, "text": "我偏要走桥！",
                 "stance": "object", "interrupt": False}]
        debated = s.social.run_debate(wave)
        self.assertTrue(debated)
        self.assertTrue(any(l["who"] == PLAYER and l.get("kind") == "debate"
                            for l in s.table_log))     # 玩家拿到了辩论发言
        self.assertIn("塌桥承不了重", "\n".join(ui.history))


# ---------------------------------------------------------------- 非交互购买
class _ShopModule:
    """最小场景桩：当前场景带商店（buy_good/autosave 只依赖 scene_by_id 与 id）。"""

    id = "shop_stub"

    def __init__(self, shop=True):
        self.shop = shop
        self.npcs = {}

    def scene_by_id(self, scene_id):
        return {"id": scene_id, "act": 1, "location": "商队营地", "intro": "",
                "shop": self.shop, "next": None}


class TestAipcBuy(unittest.TestCase):
    def test_buy_good_noninteractive(self):
        s, state, ui, _ = make_agent_session(handler=AipcScript())
        s.module = s.state.module = _ShopModule(shop=True)
        state.player.gold = 200
        potion_before = state.player.inventory.get("healing_potion", 0)
        r = s.buy_good("healing_potion")
        self.assertTrue(r["ok"])
        self.assertEqual(state.player.gold, 150)
        self.assertEqual(state.player.inventory.get("healing_potion", 0),
                         potion_before + 1)
        self.assertEqual(ui.choose_calls, 0)          # 全程零菜单
        # 医疗包按整包计 10 次用次
        self.assertTrue(s.buy_good("healer_kit")["ok"])
        self.assertEqual(state.player.inventory.get("healer_kit"), 10)
        self.assertEqual(state.player.gold, 50)
        # 金币不足 / 非商店场景 / 未知货品
        state.player.gold = 1
        self.assertFalse(s.buy_good("torch")["ok"])
        s.module = s.state.module = _ShopModule(shop=False)
        state.player.gold = 999
        self.assertFalse(s.buy_good("torch")["ok"])
        s.module = s.state.module = _ShopModule(shop=True)
        self.assertFalse(s.buy_good("sword_of_doom")["ok"])

    def test_explore_act_buy_via_tool(self):
        script = AipcScript(aipc=[("explore_act", {"kind": "buy", "item": "torch"})])
        s, state, ui, _ = make_agent_session(handler=script)
        s.module = s.state.module = _ShopModule(shop=True)
        state.player.gold = 20
        torch_before = state.player.inventory.get("torch", 0)
        self.assertTrue(s.aipc.explore_beat())
        self.assertEqual(state.player.gold, 15)
        self.assertEqual(state.player.inventory.get("torch", 0), torch_before + 1)
        self.assertEqual(ui.choose_calls, 0)
        self.assertTrue(any("采购" in x for x in script.dm_inputs))  # 世界回应分支


# ---------------------------------------------------------------- 基础设施
class TestAipcInfra(unittest.TestCase):
    def test_name_collision_refused(self):
        state = make_state()
        twin = Character.create(
            name=PLAYER, race="human", klass="cleric", background="acolyte",
            scores={"str": 8, "dex": 10, "con": 14, "int": 12, "wis": 15, "cha": 13},
            skill_choices=["medicine"])
        state.companions.append(twin)
        ui = CountUI()
        loop = DefaultAgentLoop(registry=None,
                                adapter=ScriptedAdapter(handler=AipcScript()))
        s = GameSession(state, ui, loop=loop,
                        dm_agent=make_dm_agent("test-model", M.BUILTIN))
        aipc = AIPCController.attach(s, loop, "test-model", make_card(),
                                     Persona(name=PLAYER))
        self.assertIsNone(aipc)                       # 冲突 → 不抛异常，返回 None
        self.assertIsNone(s.aipc)

    def test_save_roundtrip_keeps_aipc_block(self):
        state = make_state()
        state.aipc_enabled = True
        state.persona = Persona(name=PLAYER, description="队伍信赖的老兵。")
        state.aipc_card = make_card()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "save.json"
            G.save_game(state, path)
            st2 = G.load_game(path)
            raw = path.read_text(encoding="utf-8")
        self.assertTrue(st2.aipc_enabled)
        self.assertIsInstance(st2.persona, Persona)
        self.assertEqual(st2.persona.description, "队伍信赖的老兵。")
        self.assertIsInstance(st2.aipc_card, CharacterCard)
        self.assertEqual(st2.aipc_card.personality, make_card().personality)
        # aipc 块内无密钥面（全文只有开关与叙事文本）
        self.assertNotIn("api_key", raw)

    def test_enter_scene_refreshes_scenario(self):
        s, state, ui, _ = make_agent_session(handler=AipcScript())
        old = s.aipc.agent
        nxt = state.module.scene_by_id(state.scene_id)["next"] or state.scene_id
        s.enter_scene(nxt)
        self.assertIsNot(s.aipc.agent, old)           # agent 已重建
        self.assertIn("Scenario:", s.aipc.agent.system_prompt)
        self.assertIs(s.pc_agents[PLAYER], s.aipc.agent)

    def test_webui_command_slot(self):
        ui = WebUI()
        self.assertIsNone(ui.poll_command(0))
        ui.post_command("/状态")
        self.assertEqual(ui.poll_command(0), "/状态")  # 取走即清
        self.assertIsNone(ui.poll_command(0))
        ui.post_command("   ")                        # 空白不入槽
        self.assertIsNone(ui.poll_command(0.01))
        ui.post_command("/保存")
        self.assertEqual(ui.poll_command(0.5), "/保存")
        self.assertIsNone(ui.poll_command(0.01))      # 无指令快速超时返回


if __name__ == "__main__":
    unittest.main()
