"""完整 RED 规则机制（走真实的 Encounter / GameSession 入口）。

移植前这个文件按旧规则的多块（动作经济 / 出身 / 施法 / 战斗 / 职业资源 /
状态计时 / 内置模组）组织；RED 下同构的七块是：

1. ``TestActionEconomy``        —— 1 移动 + 1 其他动作；「附赠」槽 = 武器第二发
2. ``TestRoleAbilities``        —— 独行侠先攻 +4 / 摇滚客魅力效应 / 医疗师外科
3. ``TestArmorAndPenetration``  —— SP 吸收 vs 穿透 + 磨损 + 白刃半甲 + 皮下护甲
4. ``TestRangeBands``           —— 按射程带查 DV：超程拒绝且不消耗动作
5. ``TestDamageAndWoundStates`` —— 轻伤/重伤 −2/濒死 −4 与移动 −6
6. ``TestDeathAndCriticalInjury`` —— 生存检定门槛升级、掷 10 自动失败、2d6 表
7. ``TestCyberwareAndHumanity`` —— 义体人性损失、共情派生、赛博精神病边界

旧规则的出身 / 法术 / 天赋 / 等级面**整体删除**，在 RED 里不再有任何对应物。
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import ScriptRng, make_npc, make_pc, make_state   # noqa: E402
from test_combat import (MELEE, PISTOL, corridor, duel,        # noqa: E402
                         make_foe)

from trpg.game import state as G                              # noqa: E402
from trpg.game.session import GameSession                     # noqa: E402
from trpg.rules import data as D                              # noqa: E402
from trpg.rules.character import (COND_CONTROLLED, COND_DEAD,  # noqa: E402
                                   COND_STABILIZED, COND_UNCONSCIOUS,
                                   Character, LOCATIONS)
from trpg.rules.combat import METERS_PER_CELL, Encounter       # noqa: E402
from trpg.ui.cli import AutoUI                                # noqa: E402


# ================================================================ 1 动作经济
class TestActionEconomy(unittest.TestCase):
    def test_one_move_plus_one_action(self):
        enc = duel(grid=corridor(8), cells=0)
        pc = enc.allies[0]
        self.assertTrue(enc.has_action(pc))
        self.assertTrue(enc.move(pc, (1, 1))["ok"])          # 移动不花动作
        self.assertTrue(enc.has_action(pc))
        enc.use_action(pc)
        self.assertFalse(enc.has_action(pc))
        self.assertEqual(enc.attack_action(pc, enc.enemies[0], PISTOL)["ok"], False)

    def test_bonus_slot_is_the_second_shot(self):
        enc = duel(grid=corridor(3), cells=3)
        pc = enc.allies[0]
        self.assertTrue(enc.has_bonus(pc))
        enc.use_bonus(pc)
        self.assertFalse(enc.has_bonus(pc))
        enc.refresh_actions(pc)
        self.assertTrue(enc.has_bonus(pc))
        self.assertTrue(enc.has_action(pc))

    def test_no_five_e_actions_exist(self):
        for gone in ("cast", "shove", "help", "dodge", "hide", "disengage",
                     "second_wind", "action_surge", "rage", "flurry",
                     "stunning_strike", "wild_shape", "lay_on_hands"):
            self.assertFalse(hasattr(Encounter, gone), gone)

    def test_round_advances_and_actions_refresh(self):
        enc = duel(grid=corridor(3), cells=3, rng=ScriptRng([5, 3]))
        enc.begin()
        pc = enc.current
        enc.use_action(pc)
        enc.next_turn()
        enc.next_turn()
        self.assertEqual(enc.round, 2)
        self.assertTrue(enc.has_action(pc))


# ================================================================ 2 职业能力
class TestRoleAbilities(unittest.TestCase):
    def test_solo_combat_awareness(self):
        self.assertEqual(make_pc(role="solo").initiative_bonus(), 4)
        self.assertEqual(make_pc(role="netrunner").initiative_bonus(), 0)
        self.assertEqual(D.ROLES["solo"]["ability"]["name"], "战斗直觉")

    def test_rockerboy_charismatic_impact(self):
        rock = make_pc(role="rockerboy")
        self.assertTrue(rock.has_role_bonus("persuasion"))        # 魅力
        self.assertTrue(rock.has_role_bonus("human_perception"))  # 共情
        self.assertFalse(rock.has_role_bonus("handgun"))
        res = rock.check(ScriptRng([5]), "persuasion", dv=15)
        self.assertIn("魅力效应+2", res.breakdown)

    def test_medtech_is_required_for_surgery(self):
        hurt = make_foe()
        hurt.apply_crit_injury(2)                                 # 手臂撕脱：parmedic 17
        rookie = make_pc("新手", skills={"paramedic": 0})
        medic = make_pc("医", skills={"paramedic": 8})
        enc = duel(pc=rookie, foe=hurt, grid=corridor(1), cells=1)
        self.assertFalse(enc.treat_crit_injury(rookie, hurt)["ok"])
        enc.rng = ScriptRng([9])
        self.assertTrue(enc.treat_crit_injury(medic, hurt)["success"])

    def test_role_ability_rank_bounds(self):
        self.assertEqual(D.ROLE_ABILITY_MAX, 10)
        c = make_pc(ip=10 ** 6)
        for _ in range(20):
            c.raise_role()
        self.assertEqual(c.role_rank, D.ROLE_ABILITY_MAX)

    def test_all_ten_roles_are_defined(self):
        self.assertEqual(len(D.ROLES), 10)
        for key, info in D.ROLES.items():
            self.assertTrue(info["name"], key)
            self.assertTrue(info["ability"]["name"], key)
            self.assertIn(key, {"solo", "netrunner", "tech", "medtech", "media",
                                "cop", "corporate", "fixer", "nomad", "rockerboy"})


# ================================================================ 3 护甲
class TestArmorAndPenetration(unittest.TestCase):
    def test_sp_absorbs_then_penetrates(self):
        c = make_pc(armor="medium_armorjack")                     # SP 12
        res = c.take_damage(10)
        self.assertEqual((res["hp"], res["sp"], res["ablate"]), (0, 10, False))
        res = c.take_damage(20)
        self.assertEqual(res["hp"], 20 - 12)
        self.assertTrue(res["ablate"])
        self.assertEqual(c.armor_at("torso"), 11)

    def test_melee_pierces_half(self):
        c = make_pc(armor="heavy_armorjack")                      # SP 13 → 6
        res = c.take_damage(10, ap=True)
        self.assertEqual(res["sp"], 6)
        self.assertEqual(res["hp"], 4)

    def test_combat_melee_uses_ap(self):
        pc = make_pc(weapons=[MELEE], skills={"melee_weapon": 8})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9, 2, 5, 5])
        res = enc.attack(pc, foe, MELEE)
        self.assertEqual(res["armor"], 3)                         # 7 // 2
        self.assertEqual(res["armor_left"], 6)

    def test_subdermal_armor_is_not_cumulative(self):
        c = make_pc(armor="leather", cyberware=["subdermal_armor"])
        self.assertEqual(c.armor_at("torso"), 7)                  # 取高者
        self.assertEqual(D.CYBERWARE["subdermal_armor"]["effects"][0]["value"], 7)

    def test_cover_absorbs_before_armor(self):
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(4), cells=2)
        enc.set_cover((2, 1), "薄木", hp=3)
        enc.rng = ScriptRng([9, 3, 3, 3])
        res = enc.attack(pc, foe, PISTOL)
        self.assertEqual(res["damage"], 0)
        self.assertTrue(res["cover"]["destroyed"])
        self.assertEqual(foe.armor_at("torso"), 7)                # 护甲没动


# ================================================================ 4 射程
class TestRangeBands(unittest.TestCase):
    def test_every_weapon_kind_has_a_table(self):
        for kind in ("pistol", "shotgun", "rifle"):
            self.assertEqual(len(D.RANGED_DV[kind]), len(D.RANGE_BANDS), kind)

    def test_bands_are_contiguous_in_metres(self):
        for (lo, hi), (nlo, _nhi) in zip(D.RANGE_BANDS, D.RANGE_BANDS[1:]):
            self.assertEqual(hi + 1, nlo)

    def test_out_of_range_does_not_spend_the_action(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(120), cells=101)
        out = enc.attack_action(pc, enc.enemies[0], PISTOL)
        self.assertFalse(out["ok"])
        self.assertTrue(out["not_spent"])
        self.assertTrue(enc.has_action(pc))

    def test_aimed_shot_penalty_and_location(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([10, 5, 3, 3, 3])
        res = enc.attack(pc, enc.enemies[0], PISTOL, aimed=True)
        self.assertEqual(res["location"], "head")
        self.assertEqual(res["dv"], D.RANGED_DV["pistol"][0] + 8)

    def test_bullet_dodge_consumes_the_reaction(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        quick = make_foe()
        quick.stats["ref"] = 8
        enc = duel(pc=pc, foe=quick, grid=corridor(3), cells=3)
        enc.rng = ScriptRng([5, 9])
        self.assertTrue(enc.attack(pc, quick, PISTOL)["dodged"])
        self.assertTrue(quick.reaction_used)


# ================================================================ 5 伤势
class TestDamageAndWoundStates(unittest.TestCase):
    def test_wound_thresholds(self):
        c = make_pc(hp=40, armor=None)
        self.assertEqual(c.seriously_wounded_threshold, 20)
        c.hp_cur = 40
        self.assertEqual(c.wound_state(), "healthy")
        c.hp_cur = 39
        self.assertEqual(c.wound_state(), "light")
        c.hp_cur = 20
        self.assertEqual(c.wound_state(), "serious")
        self.assertEqual(c.check_penalty(), D.WOUND_STATES["serious"]["check_penalty"])
        c.hp_cur = 0
        self.assertEqual(c.wound_state(), "mortal")
        self.assertEqual(c.check_penalty(), D.WOUND_STATES["mortal"]["check_penalty"])
        self.assertEqual(c.move_value(), max(1, c.stat("move") * METERS_PER_CELL - 6))

    def test_serious_wound_hurts_attacks(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        pc.hp_cur = 10
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([8, 3, 3, 3])
        res = enc.attack(pc, enc.enemies[0], PISTOL)
        self.assertEqual(res["attack_total"],
                         8 + pc.skill_total("handgun")
                         + D.WOUND_STATES["serious"]["check_penalty"])

    def test_pain_editor_cancels_the_serious_penalty_only(self):
        c = make_pc(cyberware=["pain_editor"])
        c.hp_cur = 10
        self.assertEqual(c.check_penalty(), 0)
        c.hp_cur = 0
        self.assertEqual(c.check_penalty(),
                         D.WOUND_STATES["mortal"]["check_penalty"])

    def test_downed_target_is_unconscious(self):
        c = make_pc(hp=10, armor=None)
        c.take_damage(50, ignore_armor=True)
        self.assertIn(COND_UNCONSCIOUS, c.conditions)
        self.assertTrue(c.is_downed)
        self.assertFalse(c.is_dead)


# ================================================================ 6 生存检定
class TestDeathAndCriticalInjury(unittest.TestCase):
    def test_threshold_escalates(self):
        c = make_pc()
        seen = []
        rng = ScriptRng([1, 2, 3, 4])
        for _ in range(3):
            seen.append(c.death_save_dv())
            c.death_save(rng)
        self.assertEqual(seen, [10, 11, 12])
        self.assertEqual(D.DEATH_SAVE["cumulative"], 1)

    def test_natural_ten_auto_fails(self):
        c = make_pc()
        res = c.death_save(ScriptRng([10]))
        self.assertTrue(res["dead"])
        self.assertIn(COND_DEAD, c.conditions)

    def test_stabilize_resets(self):
        c = make_pc()
        c.hp_cur = -5
        c.conditions.add(COND_DEAD)
        c.death_save_mod = 3
        c.stabilize()
        self.assertEqual(c.hp_cur, D.DEATH_SAVE["stabilized_hp"])
        self.assertEqual(c.death_save_mod, 0)
        self.assertIn(COND_STABILIZED, c.conditions)

    def test_critical_table_is_two_d6_with_twelve_entries(self):
        self.assertEqual(sorted(D.CRIT_INJURIES), list(range(2, 13)))
        for roll, info in D.CRIT_INJURIES.items():
            self.assertTrue(info["text"], roll)
            self.assertTrue(info["effects"], roll)
            self.assertTrue(info["name"], roll)

    def test_two_sixes_in_combat_add_five_and_a_table_roll(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(3), cells=3)
        enc.rng = ScriptRng([8, 6, 6, 3, 4, 4])
        res = enc.attack(pc, foe, PISTOL)
        self.assertEqual(res["damage"], (15 - 7) + D.CRIT_INJURY_BONUS_DAMAGE)
        self.assertEqual(res["crit_injury"]["name"], D.CRIT_INJURIES[8]["name"])
        self.assertEqual(len(foe.crit_injuries), 1)

    def test_crit_injury_spikes_the_death_save_threshold(self):
        c = make_pc()
        base = c.death_save_dv()
        c.apply_crit_injury(12)                                   # 腿部切断 +1
        self.assertEqual(c.death_save_dv(), base + 1)
        self.assertGreater(len(c.crit_injuries), 0)


# ================================================================ 7 义体 / 人性
class TestCyberwareAndHumanity(unittest.TestCase):
    def test_humanity_and_emp_are_linked(self):
        c = make_pc()
        self.assertEqual(c.humanity_max, c.stat("emp") * D.HUMANITY_PER_EMP)
        self.assertEqual(c.humanity_cur, c.humanity_max)
        c.install_cyberware("muscle_enhance")
        self.assertEqual(c.humanity_cur, c.humanity_max - 14)
        self.assertEqual(c.stat("emp"), -(-c.humanity_cur // D.HUMANITY_PER_EMP))

    def test_install_once(self):
        c = make_pc()
        self.assertGreater(c.install_cyberware("internal_agent"), 0)
        self.assertEqual(c.install_cyberware("internal_agent"), 0)

    def test_cyberpsychosis_boundary(self):
        c = make_pc(stats={"emp": 1, "body": 5, "will": 5})
        c.humanity_cur = 5
        c.install_cyberware("internal_agent")                     # 人性 5 → 0
        self.assertTrue(c.is_cyberpsycho())
        self.assertEqual(c.stat("emp"), 0)

    def test_cyberware_check_bonus_reaches_the_audit_string(self):
        c = make_pc(cyberware=["cyberoptics"])
        res = c.check(ScriptRng([5]), "perception", dv=15)
        self.assertEqual(res.total, 5 + c.skill_total("perception") + 2)

    def test_save_roundtrip_keeps_cyberware_and_humanity(self):
        from trpg.game import state as G
        st = make_state()
        st.player.install_cyberware("cyberoptics")
        st.player.gain_ip(30)
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "g.json"
            G.save_game(st, path)
            back = G.load_game(path, module=st.module)
        self.assertEqual(back.player.cyberware, ["cyberoptics"])
        self.assertEqual(back.player.humanity_cur, st.player.humanity_cur)
        self.assertEqual(back.player.ip, 30)

    def test_encounter_built_from_state_uses_red_statblocks(self):
        st = make_state()
        enc = G.build_encounter("e1", st)
        for foe in enc.enemies:
            self.assertEqual(foe.kind, "npc")
            self.assertEqual(len(foe.stats), 10)
            self.assertGreater(foe.hp_max, 0)
            self.assertGreaterEqual(foe.armor_at("torso"), 0)


if __name__ == "__main__":
    unittest.main()
