"""规则精确性回归（《赛博朋克 RED》）。

每一个用例都用 :class:`helpers.ScriptRng` 把骰子钉死，断言**精确数值**而不是
「大致能过」。覆盖移植后容易悄悄走样的地方：瞄准射击 −8、闪避子弹的反应经济、
生存检定门槛的累积、致命伤长期后果的叠加、状态计时的到期与再检定、
掩体耐久的精确扣减、幸运对总值的精确贡献、暴击追加的精确算术、
伤害/护甲/磨损的三段式结算，以及 ``rolls`` 台账的审计字段。

``ScriptRng`` 已从本文件上提到 ``tests/helpers.py``（旧版在这里定义），
本文件改为从共享夹具导入。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import ScriptRng, make_pc                    # noqa: E402
from test_combat import (MELEE, PISTOL, corridor, duel,   # noqa: E402
                         make_foe)

from ctrcg.rules import data as D                          # noqa: E402
from ctrcg.rules.character import (AIMED_SHOT_PENALTY,     # noqa: E402
                                   COND_CONTROLLED, COND_DEAD,
                                   COND_STABILIZED, COND_UNCONSCIOUS,
                                   Character, LOCATIONS)
from ctrcg.rules.combat import METERS_PER_CELL, Encounter  # noqa: E402


def num(part: str) -> int:
    """从 '反应+6' / '+0' / '-2（伤势）' 里取出带符号整数。"""
    m = re.search(r"[+-]\d+", part)
    assert m, f"审计明细里没有可解析的数值：{part!r}"
    return int(m.group(0))


# ================================================================ 攻击算术
class TestAttackArithmetic(unittest.TestCase):
    """命中的精确加法与审计明细。"""

    def test_aimed_shot_penalty_is_minus_eight(self):
        self.assertEqual(AIMED_SHOT_PENALTY, -8)
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([9, 3, 3, 3])
        plain = enc.attack(pc, enc.enemies[0], PISTOL)
        enc.refresh_actions(pc)
        enc.rng = ScriptRng([9, 3, 3, 3])
        aimed = enc.attack(pc, enc.enemies[0], PISTOL, aimed=True)
        self.assertEqual(plain["attack_total"], 9 + pc.skill_total("handgun"))
        self.assertEqual(aimed["attack_total"], plain["attack_total"] - 8)
        self.assertEqual(aimed["dv"], plain["dv"] + 8)

    def test_melee_penalty_applies_to_melee_attacks(self):
        """生产缺陷（ctrcg/rules/combat.py:497-499 与 506-515）：``attack`` 把
        ``melee_penalty()`` 累加进局部变量 ``penalty``，但白刃战分支走的是
        ``att.opposed_check()``（只读 ``check_penalty()``），``penalty`` 在近战
        路径上从不被使用。于是致命伤「肌肉拉伤：近战武器攻击 −2」永远不生效，
        而 ``Character.melee_penalty()`` / ``active_crit_effects()`` 都声称它生效。"""
        pc = make_pc(weapons=[MELEE], skills={"melee_weapon": 8})
        pc.apply_crit_injury(9)                             # 肌肉拉伤：近战 −2
        self.assertEqual(pc.melee_penalty(), -2)
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9, 2])
        melee = enc.attack(pc, enc.enemies[0], MELEE)
        self.assertEqual(melee["attack_total"],
                         9 + pc.skill_total("melee_weapon") - 2)

    def test_melee_penalty_does_not_touch_ranged_attacks(self):
        pc = make_pc(weapons=[MELEE, PISTOL], skills={"melee_weapon": 8,
                                                     "handgun": 6})
        pc.apply_crit_injury(9)                             # 肌肉拉伤：只影响近战
        self.assertEqual(pc.melee_penalty(), -2)
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([9, 3, 3, 3])
        shot = enc.attack(pc, enc.enemies[0], PISTOL)
        self.assertEqual(shot["attack_total"],
                         9 + pc.skill_total("handgun"))     # 枪不受肌肉拉伤影响

    def test_luck_raises_the_total_by_exactly_n(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})   # 幸运 5
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([5, 3, 3, 3])
        base = enc.attack(pc, enc.enemies[0], PISTOL)
        enc.refresh_actions(pc)
        enc.rng = ScriptRng([5, 3, 3, 3])
        lucky = enc.attack(pc, enc.enemies[0], PISTOL, luck=3)
        self.assertEqual(lucky["attack_total"], base["attack_total"] + 3)
        self.assertEqual(pc.luck_cur, 2)
        entry = [r for r in enc.rolls if r["kind"] == "attack"][-1]
        self.assertIn("+3（幸运）", entry["mods"])

    def test_overspending_luck_is_capped_by_the_pool(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6}, stats={"luck": 2})
        pc.refresh_luck()
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([5, 3, 3, 3])
        res = enc.attack(pc, enc.enemies[0], PISTOL, luck=99)
        self.assertEqual(res["attack_total"], 5 + pc.skill_total("handgun") + 2)
        self.assertEqual(pc.luck_cur, 0)

    def test_crit_failure_arithmetic(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([1, 6, 3, 3, 3])
        res = enc.attack(pc, enc.enemies[0], PISTOL)
        self.assertEqual(res["attack_rolls"], [1, 6])
        self.assertEqual(res["attack_total"], 1 - 6 + 12)   # 7 < DV 13
        self.assertFalse(res["hit"])

    def test_crit_success_arithmetic(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([10, 10, 3, 3, 3])
        res = enc.attack(pc, enc.enemies[0], PISTOL)
        self.assertEqual(res["attack_rolls"], [10, 10])     # 只追加一次
        self.assertEqual(res["attack_total"], 10 + 10 + 12)

    def test_roll_ledger_fields_and_mods_are_auditable(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([8, 3, 3, 3])
        enc.attack(pc, enc.enemies[0], PISTOL)
        entry = enc.rolls[-1]
        for key in ("kind", "who", "target", "weapon", "total", "rolls", "dv",
                    "mods", "crit_success", "crit_failure"):
            self.assertIn(key, entry)
        mods = entry["mods"]
        self.assertIn("反应+6", mods)
        self.assertIn("手枪+6", mods)
        self.assertEqual(num(mods[0]) + num(mods[1]), num(mods[2]))
        self.assertEqual(num(mods[2]), entry["total"] - entry["rolls"][0])

    def test_weapon_damage_expressions(self):
        pc = make_pc(weapons=["medium_pistol", "medium_melee", "shotgun",
                              "assault_rifle"])
        self.assertEqual(pc.weapon_damage("medium_pistol"), "2d6")
        self.assertEqual(pc.weapon_damage("medium_melee"), "2d6")
        self.assertEqual(pc.weapon_damage("shotgun"), "5d6")
        self.assertEqual(pc.weapon_damage("assault_rifle"), "5d6")
        self.assertEqual(pc.weapon_damage("brawling"), "1d6")   # 徒手

    def test_rof_second_shot_is_the_bonus_slot(self):
        enc = duel(grid=corridor(3), cells=3)
        pc = enc.allies[0]
        self.assertTrue(enc.has_bonus(pc))
        enc.use_bonus(pc)
        self.assertFalse(enc.has_bonus(pc))
        enc.refresh_actions(pc)
        self.assertTrue(enc.has_bonus(pc))

    def test_refresh_actions_resets_all_three_slots(self):
        enc = duel(grid=corridor(3), cells=3)
        pc = enc.allies[0]
        pc.action_used = pc.bonus_used = pc.reaction_used = True
        pc.turn_damage_taken = 7
        enc.refresh_actions(pc)
        self.assertFalse(pc.action_used or pc.bonus_used or pc.reaction_used)
        self.assertEqual(pc.turn_damage_taken, 0)


# ================================================================ 反应经济
class TestReactionEconomy(unittest.TestCase):
    """闪避子弹每轮一次；用掉之后只能硬吃。"""

    def test_only_one_dodge_per_round_then_refresh(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        quick = make_foe()
        quick.stats["ref"] = 8
        quick.skill_levels["evasion"] = 6
        enc = duel(pc=pc, foe=quick, grid=corridor(3), cells=3)
        enc.rng = ScriptRng([5, 9])
        self.assertTrue(enc.attack(pc, quick, PISTOL)["dodged"])
        enc.rng = ScriptRng([8, 3, 3, 3])
        second = enc.attack(pc, quick, PISTOL)
        self.assertNotIn("dodged", second)
        self.assertEqual(second["dv"], 13)
        enc.refresh_actions(quick)                          # 新一轮：反应回来了
        quick.reaction_used = False
        enc.rng = ScriptRng([5, 9])
        self.assertTrue(enc.attack(pc, quick, PISTOL)["dodged"])

    def test_use_reaction_returns_false_when_spent(self):
        enc = duel(grid=corridor(3), cells=3)
        foe = enc.enemies[0]
        self.assertTrue(enc.use_reaction(foe))
        self.assertFalse(enc.use_reaction(foe))
        enc.refresh_actions(foe)
        self.assertTrue(enc.use_reaction(foe))

    def test_dodging_is_impossible_without_ref_8(self):
        slow = make_foe()
        slow.stats["ref"] = 7
        slow.skill_levels["evasion"] = 10
        self.assertFalse(slow.can_evade_bullets())


# ================================================================ 生存检定
class TestDeathSavePrecision(unittest.TestCase):
    """门槛 10 + 累积 + 致命伤；掷出 10 自动失败；失败即死。"""

    def test_threshold_accumulates_exactly(self):
        c = make_pc()
        rng = ScriptRng([1, 2, 3, 4, 5])
        seen = []
        for _ in range(5):
            seen.append(c.death_save_dv())
            c.death_save(rng)
        self.assertEqual(seen, [10, 11, 12, 13, 14])

    def test_critical_injury_bonus_is_folded_in(self):
        c = make_pc()
        c.apply_crit_injury(2)                              # 生存检定 +1
        c.apply_crit_injury(4)                              # 生存检定 +1
        self.assertEqual(c.death_save_dv(), 12)
        c.death_save_mod = 3
        self.assertEqual(c.death_save_dv(), 15)

    def test_fixating_an_injury_removes_its_bonus(self):
        c = make_pc()
        c.apply_crit_injury(2)
        self.assertEqual(c.death_save_dv(), 11)
        c.first_aid_fix(0)
        self.assertEqual(c.death_save_dv(), 10)

    def test_success_keeps_the_character_alive(self):
        c = make_pc()
        c.hp_cur = 0
        res = c.death_save(ScriptRng([7]))
        self.assertTrue(res["ok"])
        self.assertFalse(c.is_dead)
        self.assertEqual(c.hp_cur, 0)                       # 门槛过了不代表回血
        self.assertEqual(res["dv"], 10)

    def test_massive_damage_deepens_the_hole_without_instant_death(self):
        """RED「伤害超过 PW 上限」不直接判死，但生存检定的起点更难。"""
        c = make_pc(hp=45, armor=None)
        c.take_damage(200, ignore_armor=True)
        self.assertEqual(c.hp_cur, -45)
        self.assertFalse(c.is_dead)
        self.assertIn(COND_UNCONSCIOUS, c.conditions)

    def test_unconscious_and_dead_are_distinct(self):
        c = make_pc()
        c.hp_cur = 0
        self.assertTrue(c.is_downed)
        self.assertFalse(c.is_dead)
        c.conditions.add(COND_DEAD)
        self.assertTrue(c.is_dead)


# ================================================================ 伤势/状态
class TestConditionPrecision(unittest.TestCase):
    """伤势与状态的修正必须**相加**并进审计明细。"""

    def test_serious_and_controlled_stack(self):
        c = make_pc()
        c.hp_cur = 10                                       # 重伤 −2
        c.conditions.add(COND_CONTROLLED)                    # 被控制 −2
        self.assertEqual(c.check_penalty(), -4)
        res = c.check(ScriptRng([7]), "handgun", dv=15)
        self.assertEqual(res.total, 7 + c.skill_total("handgun") - 4)
        self.assertIn("伤势/状态-4", res.breakdown)

    def test_pain_editor_cancels_only_the_serious_penalty(self):
        c = make_pc(cyberware=["pain_editor"])
        c.hp_cur = 10
        c.conditions.add(COND_CONTROLLED)
        self.assertEqual(c.check_penalty(), -2)             # 只剩被控制的 −2

    def test_crit_penalties_stack(self):
        c = make_pc()
        c.apply_crit_injury(11)                             # 手指粉碎 −4
        c.apply_crit_injury(11)                             # 再来一处 −4
        self.assertEqual(c.crit_penalty(), -8)
        res = c.check(ScriptRng([5]), "handgun", dv=15)
        self.assertEqual(res.total, 5 + c.skill_total("handgun") - 8)

    def test_move_penalties_stack_across_injuries(self):
        c = make_pc()
        base = c.move_value()
        c.apply_crit_injury(8)                              # 腿部骨折 −4
        c.apply_crit_injury(4)                              # 肺萎陷 −2
        self.assertEqual(c.move_value(), max(1, base - 6))

    def test_timer_ticks_down_one_per_turn(self):
        enc = duel(grid=corridor(1), cells=1)
        pc = enc.allies[0]
        enc.set_condition(pc, "blinded", rounds=3)
        enc._tick_timers(pc)
        self.assertEqual(pc.timers["blinded"]["left"], 2)
        enc._tick_timers(pc)
        self.assertEqual(pc.timers["blinded"]["left"], 1)
        enc._tick_timers(pc)
        self.assertNotIn("blinded", pc.conditions)
        self.assertNotIn("blinded", pc.timers)

    def test_save_based_timer_rerolls_each_turn_until_it_passes(self):
        pc = make_pc(skills={"resist_torture_drugs": 0})     # 意志6 + 0 = 6
        enc = duel(pc=pc, grid=corridor(1), cells=1)
        enc.add_timer(pc, "毒", 3, save="resist_torture_drugs", dv=17)
        pc.conditions.add("毒")
        enc.rng = ScriptRng([3])                            # 3 + 6 = 9 < 17
        enc._tick_timers(pc)
        self.assertIn("毒", pc.conditions)
        self.assertEqual(pc.timers["毒"]["left"], 2)
        enc.rng = ScriptRng([10, 4])                        # 暴击成功：10+4+6=20
        enc._tick_timers(pc)
        self.assertNotIn("毒", pc.conditions)

    def test_stabilized_is_cleared_by_new_damage(self):
        enc = duel(grid=corridor(1), cells=1)
        pc = enc.allies[0]
        pc.hp_cur = 1
        pc.conditions.add(COND_STABILIZED)
        enc.apply_damage(pc, 5, ignore_armor=True)
        self.assertNotIn(COND_STABILIZED, pc.conditions)

    def test_rest_day_requires_stabilized(self):
        c = make_pc()
        c.hp_cur = 0                                    # 濒死：未稳定不恢复
        self.assertEqual(c.rest_day(), 0)
        c.stabilize()
        self.assertEqual(c.rest_day(), c.stat(D.REST_HEAL_PER_DAY_STAT))
        walking = make_pc()
        walking.hp_cur = 20                             # 走动伤员可直接休整
        self.assertEqual(walking.rest_day(),
                         walking.stat(D.REST_HEAL_PER_DAY_STAT))


# ================================================================ 致命伤
class TestCriticalPrecision(unittest.TestCase):
    """致命伤表的机制关键字逐条对应到角色的派生值。"""

    def test_drop_items_injuries_do_not_touch_checks(self):
        c = make_pc(weapons=["medium_pistol"])
        c.apply_crit_injury(3)                              # 手掌切断：只是掉东西
        self.assertTrue(c.has_crit_effect("drop_items"))
        self.assertEqual(c.check_penalty(), 0)
        self.assertEqual(c.move_value(), c.stat("move") * D.MOVE_FACTOR)

    def test_move_hurt_injury_damages_after_a_long_move(self):
        pc = make_pc()
        pc.hp_max = pc.hp_cur = 45
        pc.apply_crit_injury(5)                             # 肋骨骨折：移动 >4 米再受伤
        enc = duel(pc=pc, grid=corridor(14), cells=12)      # 敌人离得远，不会借机
        enc.rng = ScriptRng([6])
        out = enc.move(pc, (3, 1))                          # 6 米
        self.assertTrue(out["ok"])
        self.assertEqual(pc.hp_cur, 45 - 6)                 # 1d6=6，无视护甲
        self.assertIn("额外受到", "\n".join(enc.log_lines))

    def test_move_hurt_does_not_trigger_on_a_short_move(self):
        pc = make_pc()
        pc.hp_max = pc.hp_cur = 45
        pc.apply_crit_injury(5)
        enc = duel(pc=pc, grid=corridor(14), cells=12)
        enc.rng = ScriptRng([6])
        enc.move(pc, (1, 1))                                # 只走 2 米
        self.assertEqual(pc.hp_cur, 45)

    def test_stabilized_move_hurt_is_suppressed(self):
        pc = make_pc()
        pc.hp_max = pc.hp_cur = 45
        pc.apply_crit_injury(5)
        pc.first_aid_fix(0)
        enc = duel(pc=pc, grid=corridor(14), cells=12)
        enc.rng = ScriptRng([6])
        enc.move(pc, (3, 1))
        self.assertEqual(pc.hp_cur, 45)

    def test_no_action_next_and_no_evade_are_visible_in_effects(self):
        c = make_pc()
        c.apply_crit_injury(10)                             # 脊柱损伤：下回合不能行动
        self.assertTrue(c.has_crit_effect("no_action_next"))
        leg = make_pc(stats={"ref": 8, "body": 6, "will": 6})
        leg.apply_crit_injury(12)                           # 腿部切断：无法闪避
        self.assertTrue(leg.has_crit_effect("no_evade"))
        self.assertFalse(leg.can_evade_bullets())

    def test_treatment_skills_match_the_table(self):
        for roll, info in D.CRIT_INJURIES.items():
            if "treatment" in info:
                self.assertIn(info["treatment"]["skill"], D.SKILLS, roll)
                self.assertTrue(D.DV_MIN <= info["treatment"]["dv"] <= D.DV_MAX,
                                roll)
            if info.get("first_aid_stabilize_dv") is not None:
                self.assertTrue(D.DV_MIN <= info["first_aid_stabilize_dv"]
                                <= D.DV_MAX, roll)


# ================================================================ 掩体 / 擒抱
class TestCoverAndGrapplePrecision(unittest.TestCase):
    def test_cover_durability_is_exact(self):
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(4), cells=2)
        enc.set_cover((2, 1), "厚混凝土")                   # 25
        enc.rng = ScriptRng([9, 4, 4, 4])                   # 伤害 12
        res = enc.attack(pc, foe, PISTOL)
        self.assertEqual(res["cover"]["before"], 25)
        self.assertEqual(res["cover"]["absorbed"], 12)
        self.assertEqual(res["cover"]["left"], 13)
        self.assertFalse(res["cover"]["destroyed"])
        self.assertEqual(foe.hp_cur, 40)

    def test_cover_overflow_is_discarded_on_destruction(self):
        pc = make_pc("甲", weapons=["shotgun"], skills={"shoulder_arms": 8})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(4), cells=2)
        enc.set_cover((2, 1), "薄木", hp=3)
        enc.rng = ScriptRng([9, 6, 6, 6, 6, 6])             # 30 点，掩体只有 3
        res = enc.attack(pc, foe, "shotgun")
        self.assertTrue(res["cover"]["destroyed"])
        self.assertEqual(res["damage"], 0)                  # 剩余伤害作废
        self.assertEqual(foe.hp_cur, 40)

    def test_grapple_penalty_is_minus_two_for_both_sides(self):
        pc = make_pc(skills={"brawling": 8})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9, 2])
        enc.grab(pc, foe)
        self.assertEqual(D.GRAPPLE_PENALTY, -2)
        self.assertEqual(pc.check_penalty(), -2)
        self.assertEqual(foe.check_penalty(), -2)

    def test_choke_damage_equals_body_and_never_ablates(self):
        pc = make_pc(skills={"brawling": 8}, stats={"body": 9})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        pc.conditions.add(COND_CONTROLLED)
        foe.conditions.add(COND_CONTROLLED)
        out = enc.choke(pc, foe)
        self.assertEqual(out["damage"], 9)
        self.assertEqual(foe.armor_at("torso"), 7)
        self.assertEqual(foe.hp_cur, 31)

    def test_choke_ko_on_the_exact_round(self):
        pc = make_pc(skills={"brawling": 8})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 99
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        pc.conditions.add(COND_CONTROLLED)
        foe.conditions.add(COND_CONTROLLED)
        for i in range(1, D.CHOKE_KO_ROUNDS + 1):
            enc.refresh_actions(pc)
            out = enc.choke(pc, foe)
            self.assertEqual(out["rounds"], i)
        self.assertTrue(out["ko"])
        self.assertIn(COND_UNCONSCIOUS, foe.conditions)

    def test_throw_damage_equals_body_and_prones(self):
        pc = make_pc(skills={"brawling": 8}, stats={"body": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        foe.conditions.add(COND_CONTROLLED)
        out = enc.throw_target(pc, foe)
        self.assertEqual(out["damage"], 6)
        self.assertEqual(foe.hp_cur, 34)
        self.assertEqual(foe.armor_at("torso"), 7)          # 不磨损


# ================================================================ 对峙
class TestConfrontationPrecision(unittest.TestCase):
    def test_loser_takes_the_facedown_timer(self):
        a = make_pc("甲", stats={"cool": 8, "body": 5, "will": 5})
        b = make_foe()
        enc = duel(pc=a, foe=b, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([6, 5])                         # 6+8=14 vs 5+4=9
        out = enc.confrontation(a, b)
        self.assertEqual(out["winner"], "甲")
        self.assertEqual(out["loser"], b.name)
        self.assertEqual(b.timers["facedown"],
                         {"against": "甲", "penalty": -2})

    def test_a_tie_means_business(self):
        a = make_pc("甲", stats={"cool": 5, "body": 5, "will": 5})
        b = make_foe()
        b.stats["cool"] = 5
        b.reputation = 0
        enc = duel(pc=a, foe=b, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([4, 4])
        out = enc.confrontation(a, b)
        self.assertTrue(out["tie"])
        self.assertNotIn("facedown", b.timers)

    def test_reputation_is_added_to_the_roll(self):
        a = make_pc("甲", stats={"cool": 5, "body": 5, "will": 5})
        a.reputation = 6
        b = make_foe()
        enc = duel(pc=a, foe=b, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([4, 4])
        out = enc.confrontation(a, b)
        self.assertEqual(out["attack_total"], 4 + 5 + 6)
        self.assertEqual(out["defense_total"], 4 + b.stat("cool"))


# ================================================================ 距离
class TestDistancePrecision(unittest.TestCase):
    def test_grid_measure_is_cells_times_two(self):
        enc = duel(grid=corridor(20), cells=7)
        self.assertEqual(enc.measure(enc.allies[0], enc.enemies[0]),
                         7 * METERS_PER_CELL)

    def test_theater_measure_returns_the_mid_band(self):
        enc = Encounter(entry={}, allies=[make_pc("甲")], enemies=[make_foe()])
        self.assertEqual(enc.measure(enc.allies[0], enc.enemies[0]), 13)

    def test_engagement_collapses_distance_to_one_cell(self):
        enc = duel(grid=corridor(20), cells=7)
        pc, foe = enc.allies[0], enc.enemies[0]
        enc.engagement[pc.name].add(foe.name)
        enc.engagement[foe.name].add(pc.name)
        enc.positions.pop(foe.name)                         # 没坐标时按纠缠算
        self.assertEqual(enc.measure(pc, foe), METERS_PER_CELL)

    def test_move_value_is_body_of_movement_times_two(self):
        c = make_pc(stats={"move": 6, "body": 5, "will": 5})
        self.assertEqual(c.move_value(), 12)
        c.hp_cur = 0
        self.assertEqual(c.move_value(), max(1, 12 - 6))

    def test_meters_per_cell_matches_the_spec(self):
        self.assertEqual(METERS_PER_CELL, D.MOVE_FACTOR)


if __name__ == "__main__":
    unittest.main()
