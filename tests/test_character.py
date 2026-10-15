"""角色模型测试（《赛博朋克 RED》）。

覆盖：派生值（PW / 人性 / 幸运池 / 护甲 SP）、伤势等级与修正、护甲磨损、
义体与人性、幸运投入、技能检定的审计明细、伤害结算、生存检定与致命伤、
改进点（IP）成长、序列化往返。

术语一律 RED；旧规则的检定、防御、法术、成长与货币词汇都不再出现。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import ScriptRng, make_pc                    # noqa: E402

from trpg.rules import data as D                          # noqa: E402
from trpg.rules.character import (COND_CONTROLLED, COND_DEAD,  # noqa: E402
                                   COND_PRONE, COND_STABILIZED,
                                   COND_UNCONSCIOUS, Character, LOCATIONS)

BASE_STATS = {"int": 5, "ref": 6, "dex": 6, "tech": 4, "cool": 5,
              "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 6}


def make_character(name="Kyle", **kw):
    """本文件的角色工厂：``hp`` / ``armor`` 是便捷别名（映射到 hp_max、armor_key）。"""
    hp = kw.pop("hp", None)
    armor = kw.pop("armor", None)
    if armor is not None:
        kw["armor_key"] = armor
    kw.setdefault("stats", dict(BASE_STATS))
    kw.setdefault("skill_levels", {"handgun": 6, "evasion": 4, "perception": 5})
    c = Character.create(name=name, role=kw.pop("role", "solo"), **kw)
    if hp is not None:
        c.hp_max = int(hp)
        c.hp_cur = int(hp)
    return c


# ================================================================ 派生值
class TestDerivedStats(unittest.TestCase):
    """PW = 10 + 5×⌈(体格+意志力)/2⌉；人性 = 共情×10；幸运池 = 幸运。"""

    def test_hp_formula(self):
        self.assertEqual(Character.hp_for(7, 6), 45)
        self.assertEqual(Character.hp_for(3, 8), 40)      # 轻松模式预设角色
        self.assertEqual(Character.hp_for(6, 3), 35)
        self.assertEqual(Character.hp_for(5), 10 + 5 * 5)  # 缺省按体格自算

    def test_create_derives_everything_from_stats(self):
        c = make_character()
        self.assertEqual(c.hp_max, 45)
        self.assertEqual(c.hp_cur, 45)
        self.assertEqual(c.humanity_max, 60)               # 共情 6 × 10
        self.assertEqual(c.humanity_cur, 60)
        self.assertEqual(c.luck_cur, 5)
        self.assertEqual(c.stat("body"), 7)
        self.assertEqual(c.role_name, "独行侠")
        self.assertEqual(c.reputation, D.REPUTATION_START)
        self.assertEqual(D.CURRENCY_ABBR, "eb")

    def test_skill_total_is_stat_plus_level(self):
        c = make_character()
        self.assertEqual(c.skill_total("handgun"), 6 + 6)
        self.assertEqual(c.skill("handgun"), 6)
        self.assertEqual(c.skill("stealth"), 0)
        self.assertEqual(c.skill_total("stealth"), c.stat("dex"))  # 未掌握只靠属性
        self.assertEqual(c.skill_name("handgun"), "手枪")
        self.assertEqual(c.skill_total("不存在"), 0)

    def test_all_ten_stats_present_even_if_partial(self):
        c = Character.create(name="半张卡", role="tech", stats={"body": 4})
        for key in D.STAT_ORDER:
            self.assertIn(key, c.stats)
        self.assertEqual(c.stat("body"), 4)
        self.assertEqual(c.stat("int"), D.STAT_MIN)

    def test_armor_sp_per_location(self):
        c = make_character(armor_key="light_armorjack")
        for loc in LOCATIONS:
            self.assertEqual(c.armor_at(loc), 11)
        self.assertEqual(c.armor_name(), "轻型防弹夹克")
        bare = make_character(armor_key=None)
        self.assertEqual(bare.armor_at("torso"), D.ARMOR_NONE)
        self.assertEqual(bare.armor_name(), "无护甲")

    def test_weapon_helpers(self):
        c = make_character(weapons=["medium_pistol", "heavy_melee"])
        self.assertEqual(c.best_weapon("ranged"), "medium_pistol")
        self.assertEqual(c.best_weapon(), "heavy_melee")
        self.assertEqual(c.weapon_damage("medium_pistol"), "2d6")
        self.assertEqual(c.weapon_damage("brawling"), "1d6")
        self.assertEqual(make_character(weapons=[]).best_weapon(), None)


# ================================================================ 伤势
class TestWoundStates(unittest.TestCase):
    """重伤阈值 = ⌈PW上限/2⌉，重伤 −2，濒死 −4 / 移动 −6。"""

    def test_threshold_and_states(self):
        c = make_character()
        self.assertEqual(c.seriously_wounded_threshold, 23)   # ⌈45/2⌉
        self.assertEqual(c.wound_state(), "healthy")
        c.hp_cur = 44
        self.assertEqual(c.wound_state(), "light")
        self.assertEqual(c.check_penalty(), 0)                # 轻伤不罚
        c.hp_cur = 23
        self.assertEqual(c.wound_state(), "serious")
        self.assertEqual(c.wound_name(), "重伤")
        self.assertEqual(c.check_penalty(), -2)
        c.hp_cur = 0
        self.assertEqual(c.wound_state(), "mortal")
        self.assertEqual(c.wound_name(), "濒死")
        self.assertEqual(c.check_penalty(), -4)
        c.conditions.add(COND_DEAD)
        self.assertEqual(c.wound_state(), "mortal")
        self.assertTrue(c.is_dead)
        self.assertTrue(c.is_downed)

    def test_seriously_wounded_penalty_reappears_after_healing(self):
        c = make_character()
        c.take_damage(25, ignore_armor=True)                  # → 20 PW
        self.assertEqual(c.check_penalty(), -2)
        c.heal(5)                                             # → 25 PW（> 23）
        self.assertEqual(c.wound_state(), "light")
        self.assertEqual(c.check_penalty(), 0)

    def test_pain_editor_ignores_serious_wound_penalty(self):
        """赛博痛觉编辑器切断痛觉：无视重伤的 −2。"""
        c = make_character(cyberware=["pain_editor"])
        self.assertTrue(c.has_cyber_effect("ignore_serious_penalty"))
        c.hp_cur = 10
        self.assertEqual(c.wound_state(), "serious")
        self.assertEqual(c.check_penalty(), 0)
        # 濒死的 −4 不受影响
        c.hp_cur = 0
        self.assertEqual(c.check_penalty(), -4)

    def test_controlled_penalty(self):
        c = make_character()
        c.conditions.add(COND_CONTROLLED)
        self.assertEqual(c.check_penalty(), D.GRAPPLE_PENALTY)

    def test_move_value_and_mortal_penalty(self):
        c = make_character()
        self.assertEqual(c.move_value(), 5 * D.MOVE_FACTOR)   # 机动 5 × 2 米
        c.hp_cur = 0
        self.assertEqual(c.move_value(), max(1, 10 - 6))

    def test_bullet_evasion_needs_ref_8_and_no_leg_cut(self):
        self.assertFalse(make_character().can_evade_bullets())   # REF 6
        quick = make_character(stats={**BASE_STATS, "ref": 8})
        self.assertTrue(quick.can_evade_bullets())
        quick.apply_crit_injury(12)                              # 腿部切断：no_evade
        self.assertFalse(quick.can_evade_bullets())

    def test_downed_and_dead_flags(self):
        c = make_character()
        self.assertFalse(c.is_downed)
        c.hp_cur = 0
        self.assertTrue(c.is_downed)
        self.assertFalse(c.is_dead)


# ================================================================ 护甲
class TestArmorAndAblation(unittest.TestCase):
    """伤害先减 SP 再减 PW；挡下伤害后该部位 SP −1（磨损）。"""

    def test_sp_absorbs_damage_completely(self):
        c = make_character(armor_key="light_armorjack")     # SP 11
        res = c.take_damage(8)
        self.assertEqual(res["hp"], 0)                      # 全被护甲吃掉
        self.assertEqual(res["sp"], 8)
        self.assertFalse(res["ablate"])                     # 没有扣血 ⇒ 不磨损
        self.assertEqual(c.armor_at("torso"), 11)
        self.assertEqual(c.hp_cur, 45)

    def test_penetrating_hit_ablates_one_point(self):
        c = make_character(armor_key="light_armorjack")
        res = c.take_damage(15)
        self.assertEqual(res["sp"], 11)
        self.assertEqual(res["hp"], 4)
        self.assertTrue(res["ablate"])
        self.assertEqual(res["sp_left"], 10)
        self.assertEqual(c.armor_at("torso"), 10)
        self.assertEqual(c.hp_cur, 41)

    def test_ablation_does_not_stack_per_hit_beyond_one(self):
        c = make_character(armor_key="kevlar")              # SP 7
        c.take_damage(30)
        self.assertEqual(c.armor_at("torso"), 6)            # 只掉 1
        c.take_damage(30)
        self.assertEqual(c.armor_at("torso"), 5)

    def test_armor_wears_down_to_zero(self):
        c = make_character(armor_key="leather")             # SP 4
        for _ in range(10):
            c.take_damage(10)
        self.assertEqual(c.armor_at("torso"), 0)
        # 护甲磨光后：全额扣血，且 PW 下限被钳在 −PW上限
        bare = make_character(armor_key=None)
        before = bare.hp_cur
        bare.take_damage(5)
        self.assertEqual(bare.hp_cur, before - 5)
        bare.take_damage(999)
        self.assertEqual(bare.hp_cur, -bare.hp_max)

    def test_melee_halves_armor(self):
        """白刃战无视一半护甲（ap=True）。"""
        c = make_character(armor_key="light_armorjack")     # SP 11 → 5
        res = c.take_damage(10, ap=True)
        self.assertEqual(res["sp"], 5)
        self.assertEqual(res["hp"], 5)

    def test_ignore_armor_bypasses_and_does_not_ablate(self):
        c = make_character(armor_key="heavy_armorjack")
        res = c.take_damage(6, ignore_armor=True)
        self.assertEqual(res["hp"], 6)
        self.assertEqual(res["sp"], 0)
        self.assertFalse(res["ablate"])
        self.assertEqual(c.armor_at("torso"), 13)

    def test_location_specific_armor(self):
        c = make_character(armor_key="kevlar")
        c.armor_sp["head"] = 0
        self.assertEqual(c.take_damage(5, location="head")["hp"], 5)
        self.assertEqual(c.armor_at("torso"), 7)

    def test_repair_armor_restores_sp(self):
        c = make_character(armor_key="kevlar")
        c.take_damage(20)
        self.assertEqual(c.armor_at("torso"), 6)
        c.repair_armor()
        self.assertEqual(c.armor_at("torso"), 7)

    def test_subdermal_armor_shares_slot_with_worn_armor(self):
        c = make_character(armor_key="leather", cyberware=["subdermal_armor"])
        self.assertEqual(c.cyber_subdermal_sp(), 7)
        self.assertEqual(c.armor_at("torso"), 7)            # 取高者
        self.assertEqual(c.armor_at("leg"), 7)

    def test_overkill_clamps_at_negative_hp_max(self):
        c = make_character(hp=45)
        c.take_damage(999, ignore_armor=True)
        self.assertEqual(c.hp_cur, -45)
        self.assertIn(COND_UNCONSCIOUS, c.conditions)

    def test_downed_flag_only_on_crossing_zero(self):
        c = make_character(hp=10, armor=None)
        self.assertFalse(c.take_damage(3, ignore_armor=True)["downed"])
        self.assertTrue(c.take_damage(20, ignore_armor=True)["downed"])
        self.assertFalse(c.take_damage(3, ignore_armor=True)["downed"])


# ================================================================ 义体 / 人性
class TestCyberwareAndHumanity(unittest.TestCase):
    """每件义体扣人性；共情随人性下降；同一件不重复收费。"""

    def test_install_charges_humanity_once(self):
        c = make_character()
        loss = c.install_cyberware("internal_agent")
        self.assertEqual(loss, D.CYBERWARE["internal_agent"]["humanity"])
        self.assertEqual(c.humanity_cur, 60 - 7)
        again = c.install_cyberware("internal_agent")
        self.assertEqual(again, 0)                          # 不重复收费
        self.assertEqual(c.humanity_cur, 53)
        self.assertEqual(len(c.cyberware), 1)

    def test_emp_derives_from_humanity(self):
        c = make_character()
        c.install_cyberware("muscle_enhance")               # 人性 14
        self.assertEqual(c.humanity_cur, 46)
        self.assertEqual(c.stat("emp"), 5)                  # ⌈46/10⌉
        c.install_cyberware("cyberarm")                     # 再 14
        self.assertEqual(c.humanity_cur, 32)
        self.assertEqual(c.stat("emp"), 4)

    def test_cyberpsychosis_boundary(self):
        c = make_character()
        c.humanity_cur = 1
        self.assertFalse(c.is_cyberpsycho())
        c.humanity_cur = D.CYBERPSYCHOSIS_HUMANITY
        self.assertTrue(c.is_cyberpsycho())
        # 义体把人性压到 0：共情随之归零
        drained = make_character(stats={**BASE_STATS, "emp": 1})
        drained.humanity_cur = 3
        drained.install_cyberware("internal_agent")         # 人性 7 → 0
        self.assertEqual(drained.humanity_cur, 0)
        self.assertEqual(drained.stat("emp"), 0)
        self.assertTrue(drained.is_cyberpsycho())

    def test_unknown_cyberware_rejected(self):
        with self.assertRaises(ValueError):
            make_character().install_cyberware("没有这件义体")

    def test_effect_helpers(self):
        c = make_character(cyberware=["cyberaudio", "cyberoptics", "tool_hand"])
        self.assertTrue(c.has_cyberware("cyberaudio"))
        self.assertFalse(c.has_cyberware("cyberarm"))
        self.assertEqual(c.cyber_check_bonus("perception"), 4)   # 听觉 + 光学各 +2
        self.assertEqual(c.cyber_check_bonus("handgun"), 0)

    def test_stat_bonus_respects_cap(self):
        c = make_character(cyberware=["muscle_enhance"])     # 体格 +2（上限 10）
        self.assertEqual(c.stat("body"), 9)
        c.install_cyberware("muscle_enhance")
        self.assertEqual(c.stat("body"), 9)                  # 已装过，不再叠加
        strong = make_character(stats={**BASE_STATS, "body": 10},
                                cyberware=["muscle_enhance"])
        self.assertEqual(strong.stat("body"), 10)            # 撞到上限 10

    def test_brief_mentions_red_terms(self):
        c = make_character(armor_key="kevlar", cyberware=["internal_agent"])
        brief = c.brief()
        self.assertIn("PW 45/45", brief)
        self.assertIn("重伤阈值 23", brief)
        self.assertIn("SP 7", brief)
        self.assertIn("人性 53/60", brief)
        self.assertIn("凯夫拉", brief)
        self.assertNotIn("AC", brief)


# ================================================================ 幸运
class TestLuck(unittest.TestCase):
    """幸运池 = 幸运属性；掷骰前投入，每点 +1；每轮恢复满。"""

    def test_spend_and_refresh(self):
        c = make_character()                                 # 幸运 5
        self.assertEqual(c.spend_luck(2), 2)
        self.assertEqual(c.luck_cur, 3)
        c.refresh_luck()
        self.assertEqual(c.luck_cur, 5)

    def test_overspend_is_clamped_to_pool(self):
        c = make_character()
        self.assertEqual(c.spend_luck(99), 5)
        self.assertEqual(c.luck_cur, 0)
        self.assertEqual(c.spend_luck(1), 0)

    def test_check_spends_luck_and_records_it(self):
        c = make_character(skill_levels={"persuasion": 0})
        before = c.luck_cur
        res = c.check(ScriptRng([4]), "persuasion", dv=13, luck=3)
        self.assertEqual(c.luck_cur, before - 3)
        self.assertEqual(res.luck_spent, 3)
        self.assertEqual(res.total, 4 + c.skill_total("persuasion") + 3)
        self.assertIn("幸运", " ".join(res.breakdown))

    def test_negative_luck_is_ignored(self):
        c = make_character()
        self.assertEqual(c.spend_luck(-3), 0)
        self.assertEqual(c.luck_cur, 5)


# ================================================================ 检定
class TestChecks(unittest.TestCase):
    """1d10 + 属性 + 技能 vs DV；伤势/义体/幸运/加时都进审计明细。"""

    def test_check_mods_match_stat_plus_skill(self):
        c = make_character()
        res = c.check(ScriptRng([7]), "handgun", dv=15)
        self.assertEqual(res.total, 7 + c.skill_total("handgun"))
        self.assertEqual(res.modifier, c.skill_total("handgun"))
        self.assertIn("反应+6", res.breakdown)
        self.assertIn("手枪+6", res.breakdown)

    def test_seriously_wounded_penalty_in_breakdown(self):
        c = make_character()
        c.hp_cur = 10
        res = c.check(ScriptRng([7]), "handgun", dv=15)
        self.assertEqual(res.total, 7 + c.skill_total("handgun") - 2)
        self.assertIn("伤势/状态-2", res.breakdown)

    def test_no_penalty_entry_when_healthy(self):
        res = make_character().check(ScriptRng([7]), "handgun", dv=15)
        self.assertFalse(any("伤势" in part for part in res.breakdown))

    def test_extra_time_and_complementary_bonuses(self):
        c = make_character()
        plain = c.check(ScriptRng([5]), "perception", dv=15)
        buffed = c.check(ScriptRng([5]), "perception", dv=15,
                         extra_time=True, complementary=True)
        self.assertEqual(buffed.total,
                         plain.total + D.CHECK_EXTRA_TIME_BONUS
                         + D.CHECK_COMPLEMENTARY_BONUS)

    def test_cyberware_check_bonus_applies(self):
        c = make_character(cyberware=["cyberoptics"])
        res = c.check(ScriptRng([5]), "perception", dv=15)
        self.assertEqual(res.total, 5 + c.skill_total("perception") + 2)

    def test_rockerboy_charisma_bonus_only_on_cool_emp_skills(self):
        """摇滚客「魅力效应」：基于魅力/共情的检定 +2。"""
        rock = make_character(role="rockerboy")
        self.assertTrue(rock.has_role_bonus("persuasion"))         # COOL
        self.assertTrue(rock.has_role_bonus("human_perception"))   # EMP
        self.assertFalse(rock.has_role_bonus("handgun"))           # REF
        res = rock.check(ScriptRng([5]), "persuasion", dv=15)
        self.assertEqual(res.total, 5 + rock.skill_total("persuasion") + 2)
        self.assertIn("魅力效应+2", res.breakdown)

    def test_unknown_skill_raises(self):
        with self.assertRaises(ValueError):
            make_character().check(ScriptRng([5]), "火球术")

    def test_opposed_check_defender_wins_ties(self):
        a = make_character(name="甲", skill_levels={"persuasion": 0})
        b = make_character(name="乙", skill_levels={"human_perception": 0})
        _ra, _rb, win = a.opposed_check(ScriptRng([5, 5]), "persuasion", b,
                                        "human_perception")
        self.assertFalse(win)

    def test_skill_attack_mod_includes_penalty(self):
        c = make_character()
        c.hp_cur = 0
        self.assertEqual(c.skill_attack_mod("handgun"),
                         c.skill_total("handgun") - 4)

    def test_solo_initiative_bonus(self):
        self.assertEqual(make_character(role="solo").initiative_bonus(), 4)
        self.assertEqual(make_character(role="fixer").initiative_bonus(), 0)


# ================================================================ 生存检定
class TestDeathSave(unittest.TestCase):
    """PW < 1：每回合掷 1d10，**低于**门槛才活；掷出 10 自动失败；失败即死。"""

    def test_threshold_is_ten_plus_cumulative(self):
        c = make_character()
        self.assertEqual(c.death_save_dv(), 10)
        c.death_save_mod = 3
        self.assertEqual(c.death_save_dv(), 13)

    def test_roll_under_survives(self):
        c = make_character()
        res = c.death_save(ScriptRng([5]))
        self.assertTrue(res["ok"])
        self.assertFalse(res["dead"])
        self.assertNotIn(COND_DEAD, c.conditions)
        self.assertEqual(c.death_save_mod, 1)          # 之后每次 +1（越来越难）

    def test_natural_ten_auto_fails(self):
        c = make_character()
        c.death_save_mod = -100                        # 门槛再低也没用
        res = c.death_save(ScriptRng([D.DEATH_SAVE["auto_fail_face"]]))
        self.assertTrue(res["dead"])
        self.assertFalse(res["ok"])
        self.assertIn(COND_DEAD, c.conditions)

    def test_any_failure_kills(self):
        """门槛是「低于」：掷值 ≥ 门槛即死（这里把门槛压到 2 制造失败）。"""
        c = make_character()
        c.death_save_mod = -8                               # 门槛 2
        res = c.death_save(ScriptRng([5]))
        self.assertFalse(res["ok"])
        self.assertTrue(res["dead"])
        self.assertTrue(c.is_dead)

    def test_face_equal_to_threshold_fails(self):
        """门槛是「低于」，平手不算活下来。"""
        c = make_character()
        c.death_save_mod = -6                               # 门槛 4
        res = c.death_save(ScriptRng([4]))
        self.assertFalse(res["ok"])
        self.assertTrue(res["dead"])

    def test_threshold_escalates_with_each_attempt(self):
        c = make_character()
        seen = []
        rng = ScriptRng([2, 3, 4, 5])
        for _ in range(3):
            seen.append(c.death_save_dv())
            c.death_save(rng)
        self.assertEqual(seen, [10, 11, 12])

    def test_critical_injury_raises_threshold(self):
        c = make_character()
        c.apply_crit_injury(2)                          # 手臂撕脱：生存检定 +1
        self.assertEqual(c.death_save_dv(), 11)
        c.first_aid_fix(0)                              # 急救固定后当天不再生效
        self.assertEqual(c.death_save_dv(), 10)

    def test_stabilize_resets_to_one_hp(self):
        c = make_character()
        c.hp_cur = -20
        c.conditions.add(COND_DEAD)
        c.death_save_mod = 4
        c.stabilize()
        self.assertFalse(c.is_dead)
        self.assertEqual(c.hp_cur, D.DEATH_SAVE["stabilized_hp"])
        self.assertEqual(c.death_save_mod, 0)
        self.assertNotIn(COND_UNCONSCIOUS, c.conditions)
        self.assertIn(COND_STABILIZED, c.conditions)

    def test_stabilize_dv_follows_wound_state(self):
        c = make_character()
        c.hp_cur = 44
        self.assertEqual(c.stabilize_dv(), D.STABILIZE_DV["light"])
        c.hp_cur = 20
        self.assertEqual(c.stabilize_dv(), D.STABILIZE_DV["serious"])
        c.hp_cur = 0
        self.assertEqual(c.stabilize_dv(), D.STABILIZE_DV["mortal"])

    def test_heal_and_revive(self):
        c = make_character()
        c.hp_cur = 0
        c.conditions.add(COND_UNCONSCIOUS)
        self.assertEqual(c.heal(5), 5)
        self.assertEqual(c.hp_cur, 5)
        self.assertNotIn(COND_UNCONSCIOUS, c.conditions)
        self.assertEqual(c.heal(999), 40)               # 上限封顶
        self.assertEqual(c.hp_cur, 45)

    def test_revive_ignores_the_dead(self):
        c = make_character()
        c.hp_cur = 0
        c.revive(3)
        self.assertEqual(c.hp_cur, 3)
        dead = make_character()
        dead.conditions.add(COND_DEAD)
        dead.hp_cur = 0
        dead.revive(3)
        self.assertEqual(dead.hp_cur, 0)                # 死人不会被照料回来

    def test_rest_day_needs_stabilized_then_heals_body(self):
        c = make_character()                            # 体格 7
        c.hp_cur = 0                                    # 濒死：必须先被稳定
        self.assertEqual(c.rest_day(), 0)
        c.stabilize()
        self.assertEqual(c.rest_day(), 7)
        self.assertEqual(c.hp_cur, 8)
        # 走动伤员（PW ≥ 1）睡一整天同样恢复体格点 PW
        wounded = make_character()
        wounded.hp_cur = 20
        self.assertEqual(wounded.rest_day(), 7)

    def test_rest_day_clears_crit_and_repairs_armor_when_full(self):
        c = make_character(armor_key="kevlar")
        c.stabilize()
        c.apply_crit_injury(5)
        c.take_damage(20)                               # 护甲磨损
        self.assertLess(c.armor_at("torso"), 7)
        c.hp_cur = c.hp_max - 1
        c.rest_day()
        self.assertEqual(c.hp_cur, c.hp_max)
        self.assertEqual(c.crit_injuries, [])
        self.assertEqual(c.armor_at("torso"), 7)
        self.assertNotIn(COND_STABILIZED, c.conditions)


# ================================================================ 致命伤
class TestCriticalInjuries(unittest.TestCase):
    """2d6 致命伤表：长期后果 + 急救固定 + 外科治疗。"""

    def test_apply_and_read_effects(self):
        c = make_character()
        inj = c.apply_crit_injury(9)                    # 肌肉拉伤：近战 −2
        self.assertEqual(inj["name"], "肌肉拉伤")
        self.assertTrue(c.has_crit_effect("melee_minus"))
        self.assertEqual(c.melee_penalty(), -2)
        self.assertEqual(c.crit_penalty(), 0)           # 不是 hand_minus
        self.assertEqual(len(c.crit_injuries), 1)

    def test_unknown_roll_falls_back_to_default(self):
        c = make_character()
        inj = c.apply_crit_injury(99)
        self.assertEqual(inj["roll"], 99)
        self.assertEqual(inj["name"], D.CRIT_INJURIES[7]["name"])

    def test_hand_minus_hits_all_checks(self):
        c = make_character()
        c.apply_crit_injury(11)                         # 手指粉碎：所有动作 −4
        self.assertEqual(c.crit_penalty(), -4)
        res = c.check(ScriptRng([5]), "handgun", dv=15)
        self.assertEqual(res.total, 5 + c.skill_total("handgun") - 4)

    def test_move_penalties(self):
        c = make_character()
        base = c.move_value()
        c.apply_crit_injury(8)                          # 腿部骨折 −4
        self.assertEqual(c.move_value(), max(1, base - 4))
        c2 = make_character()
        c2.apply_crit_injury(12)                        # 腿部切断 −6
        self.assertEqual(c2.move_value(), max(1, base - 6))

    def test_first_aid_fix_suppresses_until_permanent(self):
        c = make_character()
        c.apply_crit_injury(11)                         # 手指粉碎（急救 DV 13）
        self.assertEqual(c.crit_first_aid_dv(0), 13)
        c.first_aid_fix(0)
        self.assertEqual(c.crit_penalty(), 0)           # 当天不再受影响
        self.assertEqual(c.active_crit_effects(), [])

    def test_first_aid_cannot_fix_amputation(self):
        c = make_character()
        c.apply_crit_injury(2)                          # 手臂撕脱：只能手术
        self.assertIsNone(c.crit_first_aid_dv(0))
        self.assertEqual(c.crit_treatment_skill(2),
                         D.CRIT_INJURIES[2]["treatment"])

    def test_treat_removes_permanently(self):
        c = make_character()
        c.apply_crit_injury(5)
        out = c.treat_crit_injury(0)
        self.assertTrue(out["permanent"])
        self.assertEqual(c.crit_injuries, [])
        self.assertIsNone(c.treat_crit_injury(0))

    def test_out_of_range_index_is_safe(self):
        c = make_character()
        self.assertIsNone(c.first_aid_fix(3))
        self.assertIsNone(c.crit_first_aid_dv(3))
        c.apply_crit_injury(5)
        self.assertIsNone(c.treat_crit_injury(9))

    def test_brief_lists_critical_injuries(self):
        c = make_character()
        c.apply_crit_injury(8)
        self.assertIn("致命伤：腿部骨折", c.brief())


# ================================================================ 改进点 IP
class TestImprovementPoints(unittest.TestCase):
    """技能 = 20 × 新等级（×2 技能翻倍）；职业能力 = 60 × 新等级。"""

    def test_skill_cost_increments_with_new_rank(self):
        c = make_character()
        c.skill_levels["handgun"] = 3
        self.assertEqual(c.ip_cost_skill("handgun"), D.IP_SKILL_BASE * 4)  # 80
        self.assertEqual(c.ip_cost_skill("handgun", 6), 20 * 6)

    def test_x2_skills_double_the_cost(self):
        for key in ("electronics_security", "paramedic"):
            self.assertTrue(D.SKILLS[key]["x2"])
            c = make_character(skill_levels={key: 2})
            self.assertEqual(c.ip_cost_skill(key), 20 * 3 * 2)

    def test_role_cost_is_sixty_times_new_rank(self):
        c = make_character()
        c.role_rank = 4
        self.assertEqual(c.ip_cost_role(), D.IP_ROLE_BASE * 5)             # 300

    def test_stat_cost(self):
        c = make_character()
        self.assertEqual(c.ip_cost_stat(7), D.IP_STAT_BASE * 7)

    def test_raise_skill_refuses_when_short_and_keeps_state(self):
        c = make_character(ip=10)
        ok, msg = c.raise_skill("handgun")
        self.assertFalse(ok)
        self.assertIn("改进点不足", msg)
        self.assertEqual(c.skill("handgun"), 6)
        self.assertEqual(c.ip, 10)

    def test_raise_skill_spends_and_levels(self):
        c = make_character(ip=200)
        c.skill_levels["handgun"] = 3
        ok, msg = c.raise_skill("handgun")
        self.assertTrue(ok)
        self.assertEqual(c.skill("handgun"), 4)
        self.assertEqual(c.ip, 200 - 80)
        self.assertIn("手枪", msg)

    def test_skill_cap(self):
        c = make_character(ip=9999)
        c.skill_levels["handgun"] = D.SKILL_MAX
        ok, msg = c.raise_skill("handgun")
        self.assertFalse(ok)
        self.assertIn("上限", msg)

    def test_unknown_skill_refused(self):
        ok, msg = make_character(ip=999).raise_skill("火球术")
        self.assertFalse(ok)
        self.assertIn("未知技能", msg)

    def test_raise_role(self):
        c = make_character(ip=60 * 5)
        ok, _msg = c.raise_role()
        self.assertTrue(ok)
        self.assertEqual(c.role_rank, 5)
        self.assertEqual(c.ip, 0)
        c.role_rank = D.ROLE_ABILITY_MAX
        ok, msg = c.raise_role()
        self.assertFalse(ok)
        self.assertIn("满级", msg)

    def test_raise_stat_recomputes_derived_values(self):
        c = make_character(ip=60 * 8)
        ok, _msg = c.raise_stat("body")                 # 7 → 8
        self.assertTrue(ok)
        self.assertEqual(c.stat("body"), 8)
        # 引擎口径：BODY 每点 +5 PW（hp_for 只按体格重算）
        self.assertEqual(c.hp_max, 45 + 5)
        self.assertGreaterEqual(c.hp_cur, 0)
        luck = make_character(ip=60 * 6)
        luck.spend_luck(3)
        luck.raise_stat("luck")                         # 幸运提升 → 池补满
        self.assertEqual(luck.luck_cur, luck.stat("luck"))
        emp = make_character(ip=60 * 7)
        emp.raise_stat("emp")                           # 共情提升 → 人性上限重算
        self.assertEqual(emp.humanity_max, emp.stat("emp") * D.HUMANITY_PER_EMP)

    def test_gain_ip(self):
        c = make_character()
        self.assertEqual(c.gain_ip(40), 40)
        self.assertEqual(c.gain_ip(-5), 40)             # 负数被吃掉


# ================================================================ 物品
class TestItems(unittest.TestCase):
    """武器 / 护甲 / 义体 / 装备走各自的表。"""

    def test_give_routes_by_table(self):
        c = make_character(weapons=[], armor_key=None)
        c.give("heavy_pistol")
        self.assertIn("heavy_pistol", c.weapons)
        c.give("heavy_pistol")                          # 重复给不叠加
        self.assertEqual(c.weapons.count("heavy_pistol"), 1)
        c.give("kevlar")
        self.assertEqual(c.armor_key, "kevlar")
        c.give("internal_agent")
        self.assertIn("internal_agent", c.cyberware)
        c.give("ammo", 20)
        c.give("ammo", 5)
        self.assertEqual(c.gear["ammo"], 25)

    def test_has_and_use_item(self):
        c = make_character(gear={"medkit": 2})
        self.assertTrue(c.has_item("medkit"))
        self.assertTrue(c.use_item("medkit"))
        self.assertEqual(c.gear["medkit"], 1)
        self.assertTrue(c.use_item("medkit"))
        self.assertFalse(c.has_item("medkit"))
        self.assertFalse(c.use_item("medkit"))

    def test_equip_and_repair(self):
        c = make_character(armor_key=None)
        c.equip_armor("medium_armorjack")
        self.assertEqual(c.armor_at("torso"), 12)
        c.equip_armor("没有这件护甲")                   # 非法 key 不改变现状
        self.assertEqual(c.armor_key, "medium_armorjack")


# ================================================================ 序列化
class TestSerialization(unittest.TestCase):
    def test_roundtrip_preserves_red_state(self):
        c = make_character(armor_key="kevlar", cyberware=["pain_editor"],
                           gear={"ammo": 12}, eurobucks=340, ip=25)
        c.take_damage(20)
        c.spend_luck(2)
        c.apply_crit_injury(4)
        c.conditions.add(COND_PRONE)
        c.timers["毒"] = {"left": 2, "save": "resist_torture_drugs", "dv": 15}
        c.ammo["heavy_pistol"] = 5
        data = c.to_dict()
        self.assertEqual(data["ruleset"], "cyberpunk-red")
        back = Character.from_dict(data)
        for field in ("name", "role", "role_rank", "hp_cur", "hp_max", "luck_cur",
                      "humanity_cur", "humanity_max", "eurobucks", "ip",
                      "armor_key", "death_save_mod", "reputation"):
            self.assertEqual(getattr(back, field), getattr(c, field), field)
        self.assertEqual(back.stats, c.stats)
        self.assertEqual(back.skill_levels, c.skill_levels)
        self.assertEqual(back.armor_sp, c.armor_sp)
        self.assertEqual(back.cyberware, c.cyberware)
        self.assertEqual(back.weapons, c.weapons)
        self.assertEqual(back.gear, c.gear)
        self.assertEqual(back.ammo, c.ammo)
        self.assertEqual(back.conditions, c.conditions)
        self.assertEqual(back.timers, c.timers)
        self.assertEqual([i["name"] for i in back.crit_injuries],
                         [i["name"] for i in c.crit_injuries])
        self.assertEqual(back.to_dict(), data)

    def test_from_dict_defaults(self):
        c = Character.from_dict({"name": "空白"})
        self.assertEqual(c.role, "solo")
        self.assertEqual(c.kind, "pc")
        self.assertEqual(c.humanity_max, c.stat("emp") * D.HUMANITY_PER_EMP)

    def test_clone_is_deep(self):
        c = make_character(gear={"ammo": 3})
        dup = c.clone("替身")
        dup.gear["ammo"] = 99
        dup.take_damage(10, ignore_armor=True)
        self.assertEqual(c.gear["ammo"], 3)
        self.assertEqual(c.hp_cur, 45)
        self.assertEqual(dup.name, "替身")


# ================================================================ 构造器
class TestConstructors(unittest.TestCase):
    def test_from_pregen_does_not_double_charge_cyberware(self):
        """预设角色卡上的共情/人性已经反映了义体，不能再扣一次。"""
        from helpers import red_pregen, red_pregen_keys
        key = red_pregen_keys()[0]
        c = Character.from_pregen(key, red_pregen(key), kind="companion")
        pre = red_pregen(key)
        self.assertEqual(c.stats["emp"], pre["stats"]["emp"])
        self.assertEqual(c.humanity_cur, pre["stats"]["emp"] * D.HUMANITY_PER_EMP)
        self.assertEqual(c.hp_max, pre["hp"])
        self.assertEqual(c.role, pre["role"])
        self.assertEqual(c.handle, pre.get("handle", ""))
        self.assertEqual(c.kind, "companion")

    def test_all_shipped_pregens_build(self):
        from helpers import red_module
        mod = red_module()
        for key, pre in mod.pregens.items():
            c = Character.from_pregen(key, pre, player=True)
            self.assertEqual(c.kind, "pc", key)
            self.assertGreater(c.hp_max, 5, key)
            for wk in pre.get("weapons", []):
                self.assertIn(wk, D.WEAPONS, key)

    def test_from_statblock_hp_uses_body_and_will(self):
        """PW 必须按 ``10 + 5×⌈(体格+意志力)/2⌉`` 算（与 ``Character.create``
        同一口径）。回归：``from_statblock`` 曾经只传体格、漏掉意志力，
        让「腐败警察」（体格6/意志力4，数据表注释与《轻松模式》第 40 页都写
        PW 35 / 重伤阈值 18）多拿了 5 点 PW。"""
        c = Character.from_statblock("corrupt_cop", D.NPCS["corrupt_cop"])
        self.assertEqual(c.hp_max, 35)
        self.assertEqual(c.seriously_wounded_threshold, 18)
        # 同一份数据块走 create 口径也必须是 35
        self.assertEqual(Character.hp_for(c.stat("body"), c.stat("will")), 35)

    def test_from_statblock_armor_name_resolves(self):
        """生产缺陷（trpg/rules/character.py:775）：``from_statblock`` 把
        ``block["armor_name"]``（显示名「凯夫拉」/「轻型防弹夹克」）直接塞进
        ``armor_key``，而 ``armor_name()`` 是拿 ``armor_key`` 查 ``D.ARMORS``。
        结果：NPC 的 SP 是对的，但战况板/简报永远显示「护甲 无护甲 SP 7」。"""
        c = Character.from_statblock("corrupt_cop", D.NPCS["corrupt_cop"])
        self.assertEqual(c.armor_name(), "凯夫拉")

    def test_from_statblock(self):
        c = Character.from_statblock("corrupt_cop", D.NPCS["corrupt_cop"])
        self.assertEqual(c.kind, "npc")
        self.assertTrue(c.is_npc)
        self.assertEqual(c.armor_at("torso"), 7)
        self.assertIn("very_heavy_pistol", c.weapons)
        self.assertEqual(c.skill_total("handgun"), 6 + 6)
        tagged = Character.from_statblock("corrupt_cop", D.NPCS["corrupt_cop"],
                                          tag="2")
        self.assertTrue(tagged.name.endswith("2"))

    def test_from_statblock_defaults(self):
        c = Character.from_statblock("无名", {"name": "无名", "skills": {},
                                             "weapons": []})
        self.assertEqual(c.hp_max, Character.hp_for(5))
        self.assertEqual(c.armor_at("torso"), 0)

    def test_make_pc_helper_is_red(self):
        c = make_pc()
        self.assertEqual(c.name, "Kyle")
        self.assertEqual(c.kind, "pc")
        self.assertGreater(c.hp_max, 0)


if __name__ == "__main__":
    unittest.main()
