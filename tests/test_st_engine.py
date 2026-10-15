"""st 核心引擎测试：角色卡 / persona / 宏 / 世界书 / 提示词组装 / 预算裁剪。

纯离线（零 LLM、零 I/O 依赖），unittest 风格，随机性全部注入固定 seed。
覆盖 docs/aipc-design.md §5 中引擎侧测试表的对应条目：
test_macros_render / test_assemble_system_order / test_fit_to_budget_sacrifice /
test_worldinfo_scan 及分桶、递归、合并、V2 卡导入、引擎 WI 化与分层约束。
"""

import datetime
import os
import random
import subprocess
import sys
import unittest
from types import SimpleNamespace

from trpg.rules.character import Character
from trpg.st import (CharacterCard, Injection, MacroContext, Persona, WIResult,
                      WLEntry, WorldInfo, assemble_system, build_context_block,
                      card_from_companion, card_from_player, engine_worldinfo,
                      fit_to_budget, inject_at_depth, merge, render, scan,
                      split_examples, wi_from_book)


def make_player() -> Character:
    """RED 玩家角色卡（十项属性 + 技能等级；无出身/分类/背景概念）。"""
    return Character.create(
        name="Kyle", role="solo",
        stats={"int": 5, "ref": 6, "dex": 6, "tech": 4, "cool": 5,
               "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 6},
        skill_levels={"athletics": 4, "brawling": 5, "perception": 5})


def make_ctx(**kw) -> MacroContext:
    kw.setdefault("user", "Ash")
    kw.setdefault("char", "Kyle")
    return MacroContext(**kw)


# ---------------------------------------------------------------- 宏
class TestMacros(unittest.TestCase):
    """{{宏}} 渲染：名字/转义/嵌套/未识别/时间/骰子桥。"""

    def test_user_char_case_insensitive(self):
        ctx = make_ctx()
        self.assertEqual(render("{{user}}和{{CHAR}}", ctx), "Ash和Kyle")
        self.assertEqual(render("{{User}}/{{char}}", ctx), "Ash/Kyle")

    def test_old_tags(self):
        self.assertEqual(render("<USER>对<BOT>说，<CHAR>点头", make_ctx()),
                         "Ash对Kyle说，Kyle点头")

    def test_escape_keeps_literal(self):
        self.assertEqual(render(r"\{\{user\}\}", make_ctx()), "{{user}}")

    def test_unrecognized_kept_verbatim(self):
        self.assertEqual(render("日期是{{current_date}}，好。", make_ctx()),
                         "日期是{{current_date}}，好。")

    def test_nested_two_layers_inner_first(self):
        # 内层 {{char}} 先解析；外层 random 用解析结果作参数（同 seed 确定）
        ctx = make_ctx()
        a = render("{{random::{{char}}::甲}}", ctx, rng=random.Random(7))
        b = render("{{random::{{char}}::甲}}", ctx, rng=random.Random(7))
        self.assertEqual(a, b)
        self.assertIn(a, ("Kyle", "甲"))
        # 内层解析、外层未识别 → 外层按字面保留但内层已展开
        self.assertEqual(render("{{upper::{{char}}}}", ctx), "{{upper::Kyle}}")

    def test_nested_beyond_two_layers_kept_literal(self):
        out = render("{{a::{{b::{{char}}}}}}", make_ctx())
        self.assertEqual(out, "{{a::{{b::Kyle}}}}")

    def test_roll_same_seed_deterministic(self):
        ctx = make_ctx()
        a = render("伤害 {{roll::2d6+3}}", ctx, rng=random.Random(42))
        b = render("伤害 {{roll::2d6+3}}", ctx, rng=random.Random(42))
        self.assertEqual(a, b)
        total = int(a.split()[1])
        self.assertTrue(5 <= total <= 15)

    def test_roll_invalid_expr_kept(self):
        self.assertEqual(render("{{roll::不是骰子}}", make_ctx()),
                         "{{roll::不是骰子}}")

    def test_space_separated_args(self):
        a = render("{{roll 1d6}}", make_ctx(), rng=random.Random(1))
        self.assertTrue(1 <= int(a) <= 6)

    def test_time_macros(self):
        now = datetime.datetime(2026, 10, 2, 13, 45)
        out = render("{{isodate}} {{isotime}}/{{time}}", make_ctx(), now=now)
        self.assertEqual(out, "2026-10-02 13:45/13:45")

    def test_utility_macros(self):
        self.assertEqual(render("甲{{newline}}乙{{space}}丙{{noop}}", make_ctx()),
                         "甲\n乙 丙")

    def test_persona_and_snapshot_fields(self):
        ctx = make_ctx(persona="沉默的旅人", party="Kyle、Roland", scene="s1：酒馆",
                       flags={"met_boss": "yes"})
        out = render("{{persona}}|{{party}}|{{scene}}|{{flags}}", ctx)
        self.assertEqual(out, "沉默的旅人|Kyle、Roland|s1：酒馆|{'met_boss': 'yes'}")

    def test_card_field_macros_from_extra(self):
        ctx = make_ctx(extra={"description": "剑士", "personality": "沉稳",
                              "scenario": "护送中"})
        self.assertEqual(render("{{char}}：{{description}}/{{personality}}/{{scenario}}",
                                ctx), "Kyle：剑士/沉稳/护送中")

    def test_pick_stable_across_renders(self):
        ctx = make_ctx()
        a = render("{{pick::甲::乙::丙}}", ctx)
        b = render("{{pick::甲::乙::丙}}", ctx)
        self.assertEqual(a, b)
        self.assertIn(a, ("甲", "乙", "丙"))


# ---------------------------------------------------------------- 角色卡 / persona
class TestCard(unittest.TestCase):
    """CharacterCard / Persona 序列化与工厂。"""

    def test_card_roundtrip_with_book(self):
        card = CharacterCard(
            name="Kyle", description="剑士", personality="沉稳", scenario="护送",
            mes_example="<START>示例", system_prompt="覆盖", talkativeness=0.7,
            alternate_greetings=["你好"], depth_prompt="@D 注释",
            book=WorldInfo(entries=[WLEntry(keys=["国王"], content="设定",
                                            constant=False, order=7)]),
            extensions={"x_custom": "保留"})
        data = card.to_dict()
        back = CharacterCard.from_dict(data)
        self.assertEqual(back.name, "Kyle")
        self.assertEqual(back.description, "剑士")
        self.assertEqual(back.talkativeness, 0.7)
        self.assertEqual(back.alternate_greetings, ["你好"])
        self.assertEqual(back.extensions.get("x_custom"), "保留")
        self.assertIsNotNone(back.book)
        self.assertEqual(back.book.entries[0].keys, ["国王"])
        self.assertEqual(back.book.entries[0].content, "设定")
        self.assertEqual(back.book.entries[0].order, 7)

    def test_unknown_keys_preserved_in_extensions(self):
        back = CharacterCard.from_dict({"name": "Kyle", "creator_notes": "仅展示",
                                        "tags": ["ttrpg"]})
        self.assertEqual(back.extensions.get("creator_notes"), "仅展示")
        self.assertEqual(back.extensions.get("tags"), ["ttrpg"])

    def test_persona_from_dict_none_and_invalid(self):
        p = Persona.from_dict(None)
        self.assertEqual((p.name, p.description, p.position, p.depth, p.role),
                         ("", "", "in_prompt", 2, "system"))
        p2 = Persona.from_dict({"name": "Ash", "description": "旅人",
                                "position": "in_chat", "depth": "5", "role": "queen"})
        self.assertEqual((p2.position, p2.depth, p2.role),
                         ("in_prompt", 5, "system"))  # 非法枚举回退、depth 容错

    def test_card_from_companion_mapping(self):
        card = card_from_companion({"name": "Roland Northwind", "persona": "前卫戍兵",
                                    "voice": "低沉，句子很短。"})
        self.assertEqual(card.name, "Roland Northwind")
        self.assertEqual(card.description, "前卫戍兵")
        self.assertEqual(card.personality, "低沉，句子很短。")
        self.assertEqual(card.scenario, "")

    def test_card_from_player_fallback_text(self):
        """兜底文案走 RED 口径：职业名 + 十项属性行。"""
        card = card_from_player(make_player())
        self.assertEqual(card.name, "Kyle")
        self.assertIn("独行侠", card.description)          # D.ROLES["solo"]["name"]
        self.assertIn("体格7", card.description)           # 十项属性行
        self.assertTrue(card.personality)          # 通用兜底文案
        card2 = card_from_player(make_player(), description="表单简介",
                                 personality="表单语气")
        self.assertEqual(card2.description, "表单简介")   # 表单值优先于兜底
        self.assertEqual(card2.personality, "表单语气")
        card3 = card_from_player(make_player(), tactics="顶在最前。")
        self.assertEqual(card3.personality, "顶在最前。")  # 表单缺省时用 tactics


# ---------------------------------------------------------------- 组装与注入
class TestAssemble(unittest.TestCase):
    """assemble_system 顺序 / 覆盖语义 / 上下文块 / 深度注入。"""

    def test_split_examples(self):
        self.assertEqual(split_examples(""), [])
        self.assertEqual(split_examples("<START>甲\n<START>乙"), ["甲", "乙"])
        self.assertEqual(split_examples("整体一块"), ["整体一块"])
        self.assertEqual(split_examples("<START>"), [])

    def _card(self) -> CharacterCard:
        return CharacterCard(
            name="Kyle", description="描述内容", personality="性格内容",
            scenario="情境内容", mes_example="<START>示例甲\n<START>示例乙")

    def test_assemble_system_order(self):
        text = assemble_system(self._card(), Persona(name="Ash", description="命代言人设"),
                               wi_before="世界书前", wi_after="世界书后",
                               main_default="MAIN默认模板")
        marks = ["MAIN默认模板", "世界书前", "命代言人设", "描述内容", "性格内容",
                 "Scenario: 情境内容", "世界书后", "示例甲", "示例乙"]
        pos = [text.index(m) for m in marks]
        self.assertEqual(pos, sorted(pos), text)
        self.assertNotIn("<START>", text)

    def test_system_prompt_override_and_original(self):
        card = self._card()
        card.system_prompt = "X {{original}} Y"
        text = assemble_system(card, None, main_default="默认全文")
        self.assertIn("X 默认全文 Y", text)
        self.assertTrue(text.startswith("X"))     # 覆盖段在 main 位

    def test_template_vars_and_fail_fast(self):
        tpl = "你是{name}（{role_name}），由 AI 代打。"
        text = assemble_system(self._card(), None, main_default=tpl,
                               template_vars={"name": "Kyle",
                                              "role_name": "独行侠"})
        self.assertIn("你是Kyle（独行侠）", text)
        with self.assertRaises(KeyError):
            assemble_system(self._card(), None, main_default=tpl)  # 缺键 fail-fast

    def test_persona_position_gating(self):
        card = self._card()
        inp = assemble_system(card, Persona(name="Ash", description="进提示词",
                                            position="in_prompt"))
        self.assertIn("进提示词", inp)
        off = assemble_system(card, Persona(name="Ash", description="不进",
                                            position="none"))
        self.assertNotIn("不进", off)
        atd = assemble_system(card, Persona(name="Ash", description="走深度",
                                            position="at_depth"))
        self.assertNotIn("走深度", atd)

    def test_macros_resolved_in_assembled_system(self):
        card = self._card()
        card.description = "{{char}}是{{personality}}的剑士"
        text = assemble_system(card, Persona(name="Ash"))
        self.assertIn("Kyle是性格内容的剑士", text)

    def test_build_context_block_shape(self):
        injs = [Injection(depth=0, role="system", text="浅层注"),
                Injection(depth=2, role="system", text="深层注")]
        out = build_context_block(
            lead="以 Kyle 的身份行动", scene="s1：老巢", party="Kyle HP 10/10",
            flags="", table="甲：「你好」", last_narration="旁白一句。",
            injections=injs, persona_note="沉默寡言", wi="世界书条目",
            phi="PHI 最后指令")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1], {"role": "user", "content": "PHI 最后指令"})
        text = out[0]["content"]
        marks = ["【场景】s1：老巢", "【队伍】Kyle HP 10/10", "【旗标】无",
                 "【桌上插话】\n甲：「你好」", "【最近旁白】旁白一句。",
                 "【世界书】\n世界书条目", "【注入】", "【玩家】沉默寡言",
                 "以 Kyle 的身份行动"]
        pos = [text.index(m) for m in marks]
        self.assertEqual(pos, sorted(pos), text)
        self.assertLess(text.index("深层注"), text.index("浅层注"))  # depth 降序

    def test_inject_at_depth_positions(self):
        msgs = [{"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"}]
        out = inject_at_depth(msgs, [
            Injection(depth=0, role="system", text="D0"),
            Injection(depth=1, role="system", text="D1"),
            Injection(depth=2, role="system", text="D2")])
        self.assertEqual([m["content"] for m in out],
                         ["D2", "u1", "D1", "a1", "D0"])

    def test_inject_at_depth_role_order_and_clamp(self):
        msgs = [{"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"}]
        out = inject_at_depth(msgs, [
            Injection(depth=1, role="system", order=100, text="S"),
            Injection(depth=1, role="assistant", order=100, text="A"),
            Injection(depth=1, role="user", order=200, text="U2"),
            Injection(depth=1, role="user", order=100, text="U1"),
            Injection(depth=99, role="system", text="TOP")])   # 超深 → 顶部
        self.assertEqual([m["content"] for m in out],
                         ["TOP", "u1", "U1", "U2", "A", "S", "a1"])

    def test_inject_at_depth_empty_and_role_kept(self):
        msgs = [{"role": "user", "content": "u1"}]
        self.assertEqual(inject_at_depth(msgs, []), msgs)
        out = inject_at_depth(msgs, [Injection(depth=0, role="assistant", text="回声")])
        self.assertEqual(out[1], {"role": "assistant", "content": "回声"})


class TestFitToBudget(unittest.TestCase):
    """§7 牺牲顺序：示例块先整块挤 → 最旧上下文行 → 常驻字段永不裁。"""

    SECTIONS = [
        ("main", "常驻主提示词"),                      # 6 tokens
        ("description", "常驻描述字段"),                # 6 tokens
        ("examples", "<START>示例对话一整块甲\n<START>示例对话二整块乙\n"
                     "<START>示例对话三整块丙"),        # 3 块 × 8 tokens
        ("table", "旧桌上行甲\n旧桌上行乙\n新桌上行丙"),  # 3 行 × 5 tokens
    ]

    def test_within_budget_untouched(self):
        out = fit_to_budget(self.SECTIONS, 10_000)
        self.assertEqual(out, self.SECTIONS)

    def test_examples_sacrificed_first_block_by_block(self):
        out = dict(fit_to_budget(self.SECTIONS, 40))   # 需省 11 → 弹出后两块
        self.assertEqual(out["examples"], "示例对话一整块甲")
        self.assertEqual(out["main"], "常驻主提示词")
        self.assertEqual(out["table"], "旧桌上行甲\n旧桌上行乙\n新桌上行丙")

    def test_then_oldest_context_lines(self):
        out = dict(fit_to_budget(self.SECTIONS, 24))   # 示例全挤掉后还差 3
        self.assertNotIn("examples", out)
        self.assertEqual(out["table"], "旧桌上行乙\n新桌上行丙")  # 最旧行先丢
        self.assertEqual(out["main"], "常驻主提示词")

    def test_permanent_never_dropped(self):
        out = fit_to_budget(self.SECTIONS, 5)          # 全部可裁内容丢光仍超
        self.assertEqual([m for m, _ in out], ["main", "description"])
        self.assertEqual(dict(out)["main"], "常驻主提示词")

    def test_marker_classification(self):
        secs = [("main", "常驻"), ("nudge", "强制"), ("context", "甲\n乙")]
        out = dict(fit_to_budget(secs, 2))
        self.assertEqual(sorted(out), ["main", "nudge"])


# ---------------------------------------------------------------- 世界书
class TestWorldInfoScan(unittest.TestCase):
    """激活算法：constant 先行 / 关键词与逻辑 / 全词与子串 / 概率 / 预算即停。"""

    def test_constant_first_then_order_desc_budget_stop(self):
        wi = WorldInfo(entries=[
            WLEntry(keys=["哥布林"], content=" selective 条目甲内容 ", order=200),
            WLEntry(keys=["哥布林"], content="条目乙内容", order=100),
            WLEntry(keys=[], content="场景条目内容", constant=True, order=1000),
        ])
        # 计量：场景 6、甲 8、乙 5 tokens；预算 15：constant(6) → 甲(6+8=14)
        # → 乙 19>15 溢出即停（乙不激活、已激活不回删）
        r = scan(wi, "哥布林出现了", budget_tokens=15, rng=random.Random(1))
        self.assertEqual(r.activated, ["constant", "哥布林"])
        self.assertTrue(r.overflowed)
        self.assertIn("场景条目内容", r.before)
        self.assertIn("条目甲内容", r.before)
        self.assertNotIn("条目乙内容", r.before)

    def test_before_bucket_concat_order_ascending(self):
        wi = WorldInfo(entries=[
            WLEntry(keys=["哥布林"], content="低序条目", order=100),
            WLEntry(keys=["哥布林"], content="高序条目", order=900),
        ])
        r = scan(wi, "哥布林", budget_tokens=1024, rng=random.Random(1))
        self.assertLess(r.before.index("低序条目"), r.before.index("高序条目"))

    def test_after_and_at_depth_buckets(self):
        wi = WorldInfo(entries=[
            WLEntry(keys=["国王"], content="在角色后", position="after_char"),
            WLEntry(keys=["国王"], content="深处注", position="at_depth",
                    depth=2, role="user", order=50),
        ])
        r = scan(wi, "国王驾到", budget_tokens=1024, rng=random.Random(1))
        self.assertEqual(r.after, "在角色后")
        self.assertEqual(len(r.at_depth), 1)
        inj = r.at_depth[0]
        self.assertEqual((inj.depth, inj.role, inj.order, inj.text),
                         (2, "user", 50, "深处注"))

    def test_logic_and_any(self):
        e = lambda logic, sec: WLEntry(keys=["国王"], secondary_keys=sec,
                                       logic=logic, content="x")
        win = "国王与哥布林"
        self.assertTrue(scan(WorldInfo(entries=[e("and_any", ["哥布林"])]),
                             win, rng=random.Random(1)).activated)
        self.assertFalse(scan(WorldInfo(entries=[e("and_any", ["巨龙"])]),
                              win, rng=random.Random(1)).activated)

    def test_logic_and_all(self):
        e = lambda logic, sec: WLEntry(keys=["国王"], secondary_keys=sec,
                                       logic=logic, content="x")
        win = "国王与哥布林"
        self.assertTrue(scan(WorldInfo(entries=[e("and_all", ["哥布林", "国王"])]),
                             win, rng=random.Random(1)).activated)
        self.assertFalse(scan(WorldInfo(entries=[e("and_all", ["哥布林", "巨龙"])]),
                              win, rng=random.Random(1)).activated)

    def test_logic_not_any(self):
        e = lambda logic, sec: WLEntry(keys=["国王"], secondary_keys=sec,
                                       logic=logic, content="x")
        win = "国王与哥布林"
        self.assertTrue(scan(WorldInfo(entries=[e("not_any", ["巨龙"])]),
                             win, rng=random.Random(1)).activated)
        self.assertFalse(scan(WorldInfo(entries=[e("not_any", ["哥布林"])]),
                              win, rng=random.Random(1)).activated)

    def test_logic_not_all(self):
        e = lambda logic, sec: WLEntry(keys=["国王"], secondary_keys=sec,
                                       logic=logic, content="x")
        win = "国王与哥布林"
        self.assertTrue(scan(WorldInfo(entries=[e("not_all", ["国王", "巨龙"])]),
                             win, rng=random.Random(1)).activated)
        self.assertFalse(scan(WorldInfo(entries=[e("not_all", ["国王"])]),
                              win, rng=random.Random(1)).activated)

    def test_empty_secondary_ignored_for_any_logic(self):
        e = WLEntry(keys=["国王"], secondary_keys=[], logic="not_any", content="x")
        self.assertTrue(scan(WorldInfo(entries=[e]), "国王",
                             rng=random.Random(1)).activated)

    def test_ascii_whole_word_not_substring(self):
        wi = WorldInfo(entries=[WLEntry(keys=["king"], content="王设定")])
        self.assertTrue(scan(wi, "the king spoke", rng=random.Random(1)).activated)
        self.assertFalse(scan(wi, "he is liking it", rng=random.Random(1)).activated)
        self.assertFalse(scan(wi, "kingdom", rng=random.Random(1)).activated)

    def test_cjk_substring_match(self):
        wi = WorldInfo(entries=[WLEntry(keys=["看守"], content="看守设定")])
        self.assertTrue(scan(wi, "威吓了看守一顿", rng=random.Random(1)).activated)
        # ASCII 键嵌在中文里：以 ASCII 词字符界定（对齐 JS \b 语义）
        wi2 = WorldInfo(entries=[WLEntry(keys=["gate"], content="闸门")])
        self.assertTrue(scan(wi2, "打开了gate的门", rng=random.Random(1)).activated)

    def test_case_sensitivity(self):
        lo = WorldInfo(entries=[WLEntry(keys=["rose"], content="玫瑰")])
        self.assertTrue(scan(lo, "Rose", rng=random.Random(1)).activated)
        hi = WorldInfo(entries=[WLEntry(keys=["rose"], content="玫瑰",
                                        case_sensitive=True)])
        self.assertFalse(scan(hi, "Rose", rng=random.Random(1)).activated)
        self.assertTrue(scan(hi, "a rose", rng=random.Random(1)).activated)

    def test_probability_gate(self):
        always = WorldInfo(entries=[WLEntry(keys=["哥布林"], content="x",
                                            probability=100)])
        self.assertTrue(scan(always, "哥布林", rng=random.Random(1)).activated)
        never = WorldInfo(entries=[WLEntry(keys=["哥布林"], content="x",
                                           probability=0)])
        self.assertFalse(scan(never, "哥布林", rng=random.Random(1)).activated)

    def test_disabled_entry_never_matches(self):
        wi = WorldInfo(entries=[WLEntry(keys=["哥布林"], content="x",
                                        enabled=False)])
        r = scan(wi, "哥布林", rng=random.Random(1))
        self.assertEqual(r.activated, [])
        self.assertFalse(r.overflowed)

    def test_recursive_scanning_one_round(self):
        a = WLEntry(keys=["哥布林"], content="他们听命于地精王")
        b = WLEntry(keys=["地精王"], content="地精王设定")
        win = "哥布林出现了"
        off = scan(WorldInfo(entries=[a, b]), win, rng=random.Random(1))
        self.assertEqual(off.activated, ["哥布林"])
        on = scan(WorldInfo(entries=[a, b], recursive_scanning=True), win,
                  rng=random.Random(1))
        self.assertEqual(sorted(on.activated), ["哥布林", "地精王"])

    def test_window_scoped_by_caller(self):
        wi = WorldInfo(entries=[WLEntry(keys=["哥布林"], content="x")])
        self.assertTrue(scan(wi, "哥布林在洞口张望", rng=random.Random(1)).activated)
        self.assertFalse(scan(wi, "只有风声", rng=random.Random(1)).activated)


class TestWorldInfoImportMerge(unittest.TestCase):
    """V2 卡导入方向换算 / 引擎状态 WI 化 / 多来源合并。"""

    def test_wi_from_book_v2_shape(self):
        book = {"scan_depth": 3, "token_budget": 512, "recursive_scanning": True,
                "entries": [
                    {"keys": ["国王"], "content": "V2 条目",
                     "insertion_order": 10,          # spec 语义：小=靠前 → 取反
                     "secondary_keys": ["王座"], "selective": True,
                     "constant": False, "enabled": True, "position": "before_char",
                     "priority": 60, "case_sensitive": False,
                     "extensions": {"selectiveLogic": 2, "probability": 55,
                                    "depth": 6, "role": "assistant"},
                     "comment": "王座厅"},
                    {"key": "a, b", "content": "ST 导出形状", "disable": True,
                     "order": 250, "constant": True, "useProbability": False,
                     "probability": 20, "selective": False,
                     "keysecondary": ["x"], "extensions": {}},
                    {"keys": ["深位"], "content": "at 深度", "position": 4},
                    {"keys": ["旁位"], "content": "after", "position": 1},
                ]}
        wi = wi_from_book(book)
        self.assertEqual((wi.scan_depth, wi.token_budget, wi.recursive_scanning),
                         (3, 512, True))
        e0, e1, e2, e3 = wi.entries
        self.assertEqual(e0.order, -10)              # insertion_order 取反
        self.assertEqual((e0.logic, e0.probability), ("not_any", 55))
        self.assertEqual((e0.position, e0.depth, e0.role),
                         ("before_char", 6, "assistant"))
        self.assertEqual(e0.comment, "王座厅")
        self.assertFalse(e1.enabled)                 # disable=true
        self.assertEqual(e1.order, 250)              # ST 内部 order 原样
        self.assertEqual(e1.probability, 100)        # useProbability=false
        self.assertEqual(e1.secondary_keys, [])      # selective=false 副键忽略
        self.assertEqual((e2.position, e3.position), ("at_depth", "after_char"))

    def test_wi_roundtrip(self):
        wi = WorldInfo(entries=[WLEntry(keys=["国王"], content="设定", order=-5)],
                       scan_depth=2, token_budget=64, recursive_scanning=True)
        back = WorldInfo.from_dict(wi.to_dict())
        self.assertEqual(back.to_dict(), wi.to_dict())

    def test_engine_worldinfo(self):
        module = SimpleNamespace(
            npcs={"vessk": {"name": "Vessk", "role": "黑鸦帮头目",
                            "desc": "黑鸦帮头目。"}},
            scene_by_id=lambda sid: {
                "id": "s1", "location": "黑鸦帮老巢",
                "intro": "第一行\n第二行",
                "checks": [{"skill": "perception", "dv": 15,
                            "success": "你发现了暗门。"}]},
        )
        state = SimpleNamespace(module=module, scene_id="s1",
                                flags={"met_boss": "yes"})
        wi = engine_worldinfo(state)
        self.assertEqual(len(wi.entries), 4)         # 场景 / 旗标 / NPC / checks=1
        r0 = scan(wi, "", budget_tokens=1024, rng=random.Random(1))
        self.assertEqual(r0.activated, ["scene:s1"])  # 空窗口仅 constant 入选
        self.assertIn("黑鸦帮老巢", r0.before)
        # 窗口文本用 RED 技能名（感知），难度值以 DV 呈现
        r1 = scan(wi, "Vessk现身，需要做感知检定", budget_tokens=1024,
                  rng=random.Random(1))
        self.assertEqual(sorted(r1.activated),
                         ["npc:vessk", "scene:s1", "skill:perception"])
        self.assertIn("DV 15", r1.before)
        r2 = scan(wi, "met_boss 被提及", budget_tokens=1024, rng=random.Random(1))
        self.assertIn("flag:met_boss", r2.activated)

    def test_merge_same_keys_first_wins(self):
        char_book = WorldInfo(entries=[
            WLEntry(keys=["国王"], content="角色书版本"),
            WLEntry(keys=[], content="常驻甲", constant=True)],
            scan_depth=2, token_budget=64)
        global_book = WorldInfo(entries=[
            WLEntry(keys=["国王"], content="全局版本"),
            WLEntry(keys=[], content="常驻乙", constant=True),
            WLEntry(keys=["巨龙"], content="龙设定")],
            scan_depth=9, token_budget=77, recursive_scanning=True)
        merged = merge(char_book, global_book)
        kings = [e for e in merged.entries if "国王" in e.keys]
        self.assertEqual(len(kings), 1)
        self.assertEqual(kings[0].content, "角色书版本")   # 先传入者完全优先
        consts = [e for e in merged.entries if e.constant]
        self.assertEqual(len(consts), 2)                   # 无键条目不去重
        self.assertEqual((merged.scan_depth, merged.token_budget), (9, 77))
        self.assertTrue(merged.recursive_scanning)
        self.assertTrue(any("巨龙" in e.keys for e in merged.entries))


# ---------------------------------------------------------------- 包约束
class TestPackage(unittest.TestCase):
    """再导出完整性与分层约束（st 不得依赖 game 层）。"""

    def test_reexports_complete(self):
        import trpg.st as st_pkg
        for name in st_pkg.__all__:
            self.assertTrue(hasattr(st_pkg, name), name)

    def test_st_does_not_import_game_layer(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import sys; import trpg.st; "
                "assert 'trpg.game' not in sys.modules, 'st 依赖了 game 层'; "
                "print('layering-ok')")
        proc = subprocess.run([sys.executable, "-c", code], cwd=root,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0,
                         f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("layering-ok", proc.stdout)

    def test_wi_result_defaults(self):
        r = WIResult()
        self.assertEqual((r.before, r.after, r.at_depth, r.overflowed,
                          r.activated), ("", "", [], False, []))


if __name__ == "__main__":
    unittest.main()
