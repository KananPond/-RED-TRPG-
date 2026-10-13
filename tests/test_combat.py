"""战斗引擎测试（《赛博朋克 RED》）。

覆盖：先攻（反应 + 1d10，独行侠 +4）、按射程带查表的远程难度值 DV、
攻击动作经济（1 移动 + 1 其他动作；射程外必须**不消耗动作**）、
护甲 SP 与磨损、白刃战无视一半护甲、斗殴两次攻击、闪避子弹、
致命伤（两个 6 → +5 无视护甲伤害 + 2d6 表）、擒抱/锁喉/投掷/挣脱、
装填/起身/移动、稳定与治疗、胜负判定、战况板渲染、敌人 AI。

术语一律 RED：难度值 DV / 生命值 PW / 护甲 SP / 移动（1 格 = 2 米）。
术语一律 RED；旧规则的法术与特殊战斗动作都不再出现。

``make_pc`` 由 ``tests/test_map.py`` 跨文件导入（``from test_combat import make_pc``），
故在此从 ``helpers`` 转出同名符号，保持两种导入风格都可用。
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (ScriptRng, fixture_module_data,  # noqa: E402
                     make_pc)                          # noqa: F401  (转出)

from ctrcg.rules import data as D                         # noqa: E402
from ctrcg.rules.character import (COND_CONTROLLED,        # noqa: E402
                                   COND_PRONE, COND_UNCONSCIOUS,
                                   Character, LOCATIONS)
from ctrcg.rules.combat import (METERS_PER_CELL, BattleGrid,  # noqa: E402
                                Encounter)

PISTOL = "heavy_pistol"        # 3d6，LA 2，pistol
RIFLE = "assault_rifle"        # 5d6，LA 1，rifle
SHOTGUN = "shotgun"            # 5d6，LA 1，shotgun
MELEE = "medium_melee"         # 2d6，LA 2，melee
FIST = "brawling"              # 1d6，LA 2，brawling


def make_foe(key: str = "corrupt_cop", tag: str = "") -> Character:
    """按内置 NPC 数据块造敌人。"""
    return Character.from_statblock(key, D.NPCS[key], tag=tag)


def corridor(cells: int) -> BattleGrid:
    """一条够长的走廊网格（1 格 = 2 米）。"""
    return BattleGrid(max(4, cells + 2), 4)


def duel(pc: Character | None = None, foe: Character | None = None, *,
         grid: BattleGrid | None = None, cells: int = 1,
         rng=None, seed: int = 1) -> Encounter:
    """单挑夹具：我方与敌人各一，可选网格与格距（省略网格 = 剧场模式）。"""
    pc = pc or make_pc()
    foe = foe or make_foe()
    enc = Encounter(title="决斗", entry={}, rng=rng or random.Random(seed),
                    allies=[pc], enemies=[foe], grid=grid)
    enc.order = [pc, foe]
    if grid is not None:
        enc.positions = {pc.name: (0, 1), foe.name: (cells, 1)}
        enc.speed_left = {pc.name: pc.move_value(), foe.name: foe.move_value()}
        enc._sync_engagement()
    return enc


def ranged(pc: Character, enc: Encounter, rng) -> dict:
    enc.rng = rng
    return enc.attack(pc, enc.enemies[0], PISTOL)


# ================================================================ 先攻
class TestInitiative(unittest.TestCase):
    """先攻 = 反应 + 1d10，从高到低排队；独行侠「战斗直觉」+4。"""

    def test_order_by_reflexes_plus_die(self):
        slow = make_pc("慢", role="fixer", stats={"ref": 4})
        fast = make_pc("快", role="fixer", stats={"ref": 8, "body": 5, "will": 5})
        enc = Encounter(entry={}, rng=ScriptRng([3, 6]),   # 慢 4+3=7，快 8+6=14
                        allies=[slow, fast], enemies=[])
        enc.begin()
        self.assertEqual([c.name for c in enc.order], ["快", "慢"])
        self.assertEqual(enc.round, 1)
        self.assertIs(enc.current, enc.order[0])

    def test_solo_gets_plus_four(self):
        solo = make_pc("独行侠", role="solo", stats={"ref": 5, "body": 5, "will": 5})
        other = make_pc("技工", role="tech", stats={"ref": 5, "body": 5, "will": 5})
        enc = Encounter(entry={}, rng=ScriptRng([5, 5]), allies=[solo, other],
                        enemies=[])
        enc.begin()
        self.assertEqual(solo.initiative_bonus(), 4)
        self.assertEqual(enc.order[0].name, "独行侠")
        log = "\n".join(enc.log_lines)
        self.assertIn("独行侠", log)
        self.assertIn("先攻 +9", log)                    # 反应 5 + 职业 +4
        self.assertIn("先攻 +5", log)                    # 技工没有先攻加值

    def test_begin_refreshes_luck_and_move_budget(self):
        pc = make_pc()
        pc.luck_cur = 0
        enc = duel(pc=pc, rng=ScriptRng([5, 3]))
        enc.begin()
        self.assertEqual(pc.luck_cur, pc.stat("luck"))
        self.assertEqual(enc.speed_left[pc.name], pc.move_value())

    def test_next_turn_cycles_and_counts_rounds(self):
        enc = duel(pc=make_pc("甲"), rng=ScriptRng([5, 3]))
        enc.begin()
        first = enc.current
        second = enc.next_turn()
        self.assertIsNot(second, first)
        self.assertEqual(enc.round, 1)
        enc.next_turn()
        self.assertEqual(enc.round, 2)

    def test_ties_broken_by_die_face(self):
        a = make_pc("甲", role="fixer", stats={"ref": 6, "body": 5, "will": 5})
        b = make_pc("乙", role="fixer", stats={"ref": 6, "body": 5, "will": 5})
        enc = Encounter(entry={}, rng=ScriptRng([4, 9]), allies=[a, b], enemies=[])
        enc.begin()
        self.assertEqual([c.name for c in enc.order], ["乙", "甲"])


# ================================================================ 射程带
class TestRangeBands(unittest.TestCase):
    """远程攻击按距离查 ``D.RANGED_DV``；超出武器射程直接拒绝。"""

    def test_pistol_dv_by_band(self):
        enc = duel(grid=corridor(60), cells=1)
        table = [(3, 13), (4, 15), (12, 20), (25, 25), (50, 30), (51, 30)]
        for cells, expected in table:
            _band, dv = enc.range_band("pistol", cells * METERS_PER_CELL)
            self.assertEqual(dv, expected, f"{cells} 格 = {cells * 2} 米")

    def test_rifle_falls_off_then_recovers(self):
        enc = duel(grid=corridor(400), cells=1)
        dvs = [enc.range_band("rifle", m)[1] for m in (6, 24, 50, 102, 300, 700)]
        self.assertEqual(dvs, [17, 15, 13, 20, 25, 30])

    def test_shotgun_extends_further(self):
        enc = duel(grid=corridor(120), cells=1)
        self.assertEqual(enc.range_band("shotgun", 6)[1], 13)
        self.assertEqual(enc.range_band("shotgun", 200)[1], 35)

    def test_pistol_beyond_its_effective_range_has_no_dv(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(120), cells=101)
        band, dv = enc.range_band("pistol", 202)
        self.assertEqual(band, 6)                       # 落在 201-400 米带
        self.assertIsNone(dv)                           # 手枪打不到
        gate = enc._range_gate(pc, enc.enemies[0], D.WEAPONS[PISTOL])
        self.assertFalse(gate["ok"])
        self.assertIn("打不到", gate["reason"])

    def test_measure_uses_metres(self):
        enc = duel(grid=corridor(10), cells=5)
        self.assertEqual(enc.measure(enc.allies[0], enc.enemies[0]), 10)

    def test_theater_mode_defaults_to_mid_band(self):
        enc = Encounter(entry={}, allies=[make_pc("甲")], enemies=[make_foe()])
        self.assertEqual(enc.measure(enc.allies[0], enc.enemies[0]), 13)

    def test_out_of_range_shot_rejected_without_damage(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(120), cells=101,
                   rng=ScriptRng([8]))
        res = enc.attack(pc, foe, PISTOL)
        self.assertFalse(res["ok"])
        self.assertFalse(res["hit"])
        self.assertEqual(res["damage"], 0)
        self.assertIn("打不到", res["reason"])
        self.assertEqual(foe.hp_cur, foe.hp_max)

    def test_line_of_sight_blocks_the_shot(self):
        grid = BattleGrid(12, 4, obstacles=[(3, 1)])
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=grid, cells=6, rng=ScriptRng([8]))
        res = enc.attack(pc, enc.enemies[0], PISTOL)
        self.assertFalse(res["ok"])
        self.assertIn("射击线", res["reason"])

    def test_live_cover_absorbs_damage(self):
        """掩体不挡视线，而是替目标吃伤害；被摧毁时本次剩余伤害作废。"""
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(6), cells=6)
        enc.set_cover((6, 1), "薄木")                    # 耐久 5
        enc.rng = ScriptRng([8, 4, 4, 4])                # 伤害 12
        res = enc.attack(pc, foe, PISTOL)
        self.assertTrue(res["hit"])
        self.assertEqual(res["damage"], 0)               # 全被掩体吃掉
        self.assertEqual(foe.hp_cur, 40)
        self.assertEqual(enc.cover_hp((6, 1)), 0)
        self.assertTrue(res["cover"]["destroyed"])

    def test_destroyed_cover_stops_absorbing(self):
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(6), cells=6)
        enc.rng = ScriptRng([8, 4, 4, 4])
        self.assertGreater(enc.attack(pc, foe, PISTOL)["damage"], 0)   # 没有掩体
        foe.hp_cur = 40
        enc.set_cover((6, 1), "薄木", hp=0)              # 掩体已被打烂
        enc.rng = ScriptRng([8, 4, 4, 4])
        self.assertGreater(enc.attack(pc, foe, PISTOL)["damage"], 0)

    def test_out_of_range_does_not_consume_the_action(self):
        """射程外的一枪被拒，且**不**消耗动作（调用方可以改移动或换武器）。"""
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(120), cells=101,
                   rng=ScriptRng([8]))
        out = enc.attack_action(pc, enc.enemies[0], PISTOL)
        self.assertFalse(out["ok"])
        self.assertTrue(out["not_spent"])
        self.assertIn("打不到", out["reason"])
        self.assertTrue(enc.has_action(pc))             # 动作还在
        self.assertEqual(pc.hp_cur, pc.hp_max)

    def test_line_of_sight_blocked_does_not_consume_the_action(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        grid = BattleGrid(12, 4, obstacles=[(3, 1)])
        enc = duel(pc=pc, foe=make_foe(), grid=grid, cells=6, rng=ScriptRng([8]))
        out = enc.attack_action(pc, enc.enemies[0], PISTOL)
        self.assertFalse(out["ok"])
        self.assertTrue(enc.has_action(pc))

    def test_attack_action_consumes_the_action(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3,
                   rng=ScriptRng([8, 4, 4, 4, 3, 3, 3]))
        self.assertTrue(enc.has_action(pc))
        enc.attack_action(pc, enc.enemies[0], PISTOL)
        self.assertFalse(enc.has_action(pc))
        again = enc.attack_action(pc, enc.enemies[0], PISTOL)
        self.assertFalse(again["ok"])
        self.assertIn("动作", again["reason"])


# ================================================================ 攻击结算
class TestAttackResolution(unittest.TestCase):
    """命中 = 1d10 + 属性 + 技能 > DV；暴击追加进总值；伤害走 SP → PW。"""

    def test_hit_against_band_dv(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})   # 反应6 + 6 = 12
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        res = ranged(pc, enc, ScriptRng([2, 3, 3, 3]))
        self.assertEqual(res["attack_total"], 2 + 12)
        self.assertEqual(res["dv"], 13)
        self.assertTrue(res["hit"])

    def test_attack_total_records_both_dice_on_a_crit(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        res = ranged(pc, enc, ScriptRng([10, 4, 3, 3, 3]))
        self.assertEqual(res["attack_total"], 10 + 4 + 12)
        self.assertEqual(len(res["attack_rolls"]), 2)
        self.assertTrue(res["hit"])

    def test_low_roll_misses(self):
        pc = make_pc(weapons=[RIFLE], skills={"shoulder_arms": 0})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(50), cells=50)   # 100 米 → DV 15
        res = ranged(pc, enc, ScriptRng([2]))
        self.assertFalse(res["hit"])
        self.assertEqual(res["damage"], 0)
        self.assertEqual(foe.hp_cur, foe.hp_max)

    def test_crit_failure_subtracts_extra_die(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 0})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        res = ranged(pc, enc, ScriptRng([1, 4]))                # 1 − 4 + 6 = 3
        self.assertEqual(res["attack_total"], 3)
        self.assertFalse(res["hit"])
        self.assertEqual(res["attack_rolls"], [1, 4])

    def test_damage_goes_through_armor_and_reports_ablation(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()                                        # 凯夫拉 SP 7
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(3), cells=3)
        # 命中(8) 伤害(4,4,4)=12；第二发命中(2) 伤害(2,2,2)=6
        enc.rng = ScriptRng([8, 4, 4, 4, 2, 2, 2, 2])
        out = enc.attack_action(pc, foe, PISTOL)
        first, second = out["attacks"]
        self.assertTrue(first["hit"])
        self.assertEqual(first["raw_damage"], 12)
        self.assertEqual(first["armor"], 7)
        self.assertEqual(first["damage"], 5)
        self.assertTrue(first["ablated"])
        self.assertEqual(first["armor_left"], 6)
        self.assertEqual(second["armor"], 6)                    # 第二发打到已磨损的甲
        self.assertEqual(second["raw_damage"], 6)
        self.assertEqual(second["damage"], 0)                   # 6 点全被 SP 6 吃掉
        self.assertFalse(second["ablated"])
        self.assertEqual(foe.hp_cur, 35)

    def test_weapon_with_rof_one_fires_once(self):
        pc = make_pc(weapons=[RIFLE], skills={"shoulder_arms": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([8, 5, 5, 5, 5, 5])
        out = enc.attack_action(pc, enc.enemies[0], RIFLE)
        self.assertEqual(len(out["attacks"]), 1)

    def test_brawling_attacks_twice(self):
        pc = make_pc(weapons=[], skills={"brawling": 6})
        # 斗殴必须贴身：cells=1 → 相邻 2 米
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9, 1, 3, 9, 1, 3])
        out = enc.attack_action(pc, enc.enemies[0], FIST)
        self.assertEqual(len(out["attacks"]), D.BRAWLING_ATTACKS)

    def test_melee_halves_armor(self):
        pc = make_pc(weapons=[MELEE], skills={"melee_weapon": 8})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        # 对抗：攻 9（敏捷6+近战8=14），守 2（敏捷5+闪避2=7）→ 命中；2d6 = 5+5
        enc.rng = ScriptRng([9, 2, 5, 5])
        res = enc.attack(pc, foe, MELEE)
        self.assertTrue(res["hit"])
        self.assertEqual(res["raw_damage"], 10)
        self.assertEqual(res["armor"], 3)                       # SP 7 // 2
        self.assertEqual(res["damage"], 7)
        self.assertEqual(foe.armor_at("torso"), 6)

    def test_bullet_dodge_opposes_and_spends_the_reaction(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        quick = make_foe()
        quick.stats["ref"] = 8                                  # 可以闪避子弹
        quick.skill_levels["evasion"] = 6
        enc = duel(pc=pc, foe=quick, grid=corridor(3), cells=3)
        res = ranged(pc, enc, ScriptRng([5, 9]))                # 攻 5+12=17，守 9+11=20
        self.assertTrue(res["dodged"])
        self.assertFalse(res["hit"])
        self.assertTrue(quick.reaction_used)
        enc.refresh_actions(pc)                                 # 反应已用完
        res2 = ranged(pc, enc, ScriptRng([8, 3, 3, 3]))
        self.assertNotIn("dodged", res2)
        self.assertEqual(res2["dv"], 13)

    def test_ref_under_eight_cannot_dodge(self):
        self.assertFalse(make_foe().can_evade_bullets())
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        self.assertNotIn("dodged", ranged(pc, enc, ScriptRng([8, 3, 3, 3])))

    def test_firing_spends_ammo(self):
        """生产缺陷（ctrcg/rules/combat.py:483-521）：``attack`` 结算命中与伤害，
        但从不扣减 ``Character.ammo``；``reload``（同文件 :636）却会把弹匣装满。
        结果弹匣只是叙事装饰——一把 8 发的手枪可以无限开火。"""
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        pc.ammo[PISTOL] = 2
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        ranged(pc, enc, ScriptRng([8, 3, 3, 3]))
        self.assertEqual(pc.ammo[PISTOL], 1)

    def test_unknown_weapon_rejected(self):
        enc = duel(grid=corridor(1), cells=1)
        res = enc.attack(enc.allies[0], enc.enemies[0], "光剑")
        self.assertFalse(res["ok"])
        self.assertIn("未知武器", res["reason"])

    def test_apply_resistance_shape(self):
        enc = duel(grid=corridor(1), cells=1)
        hp, notes = enc.apply_resistance(enc.enemies[0], 10)
        self.assertEqual(hp, 3)                                 # SP 7 挡下 7
        self.assertTrue(any("护甲 SP 7" in note for note in notes))
        self.assertTrue(any("磨损" in note for note in notes))

    def test_attack_log_mentions_red_terms(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        ranged(pc, enc, ScriptRng([8, 3, 3, 3]))
        joined = "\n".join(enc.log_lines)
        self.assertIn("护甲挡下", joined)
        self.assertIn("PW", joined)
        self.assertNotIn("AC", joined)


# ================================================================ 致命伤
class TestCriticalInjury(unittest.TestCase):
    """伤害骰出现两个及以上的 6：+5 无视护甲伤害 + 2d6 致命伤表。"""

    def test_two_sixes_trigger_bonus_and_table(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(3), cells=3)
        # 命中 8；3d6 = 6,6,3 = 15（两个 6）；2d6 表 = 8 → 腿部骨折
        res = ranged(pc, enc, ScriptRng([8, 6, 6, 3, 4, 4]))
        self.assertEqual(res["raw_damage"], 15)                 # 伤害骰本身
        self.assertEqual(res["armor"], 7)
        self.assertEqual(res["damage"], (15 - 7) + D.CRIT_INJURY_BONUS_DAMAGE)
        self.assertEqual(res["crit_injury"]["roll"], 8)
        self.assertEqual(res["crit_injury"]["name"], "腿部骨折")
        self.assertEqual(foe.crit_injuries[0]["roll"], 8)
        self.assertLess(foe.move_value(), foe.stat("move") * D.MOVE_FACTOR)

    def test_single_six_is_not_a_critical(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(3), cells=3)
        res = ranged(pc, enc, ScriptRng([8, 6, 3, 3]))
        self.assertNotIn("crit_injury", res)
        self.assertEqual(res["raw_damage"], 12)
        self.assertEqual(foe.crit_injuries, [])

    def test_single_die_weapon_cannot_crit(self):
        """徒手 1d6 只有一颗伤害骰，永远凑不出两个 6。"""
        pc = make_pc(weapons=[], skills={"brawling": 6})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)   # 贴身才能斗殴
        enc.rng = ScriptRng([9, 1, 6, 9, 1, 6])
        out = enc.attack_action(pc, foe, FIST)
        self.assertTrue(all("crit_injury" not in a for a in out["attacks"]))
        self.assertEqual(foe.crit_injuries, [])

    def test_crit_effects_persist(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(3), cells=3)
        # 伤害 6,6,1 = 13（两个 6）；2d6 = 5+6 = 11 → 手指粉碎（所有动作 −4）
        ranged(pc, enc, ScriptRng([8, 6, 6, 1, 5, 6]))
        self.assertIn("致命伤", "\n".join(enc.log_lines))
        self.assertEqual(foe.crit_injuries[0]["name"], D.CRIT_INJURIES[11]["name"])
        self.assertEqual(foe.crit_penalty(), -4)
        self.assertEqual(foe.check_penalty(), -4)

    def test_downed_target_is_logged(self):
        pc = make_pc(weapons=[SHOTGUN], skills={"shoulder_arms": 8})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 8
        foe.armor_sp = {loc: 0 for loc in LOCATIONS}
        enc = duel(pc=pc, foe=foe, grid=corridor(3), cells=3)
        enc.rng = ScriptRng([9, 6, 6, 2, 2, 2, 3, 3])
        enc.attack(pc, foe, SHOTGUN)
        self.assertLess(foe.hp_cur, 1)
        self.assertIn(COND_UNCONSCIOUS, foe.conditions)
        self.assertIn("倒地濒死", "\n".join(enc.log_lines))


# ================================================================ 特殊动作
class TestSpecialActions(unittest.TestCase):
    """擒抱 / 锁喉 / 投掷 / 挣脱 / 装填 / 起身 / 对峙 / 物品。"""

    def test_grab_controls_both_sides(self):
        pc = make_pc(skills={"brawling": 8})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9, 1])
        out = enc.grab(pc, foe)
        self.assertTrue(out["win"])
        self.assertIn(COND_CONTROLLED, foe.conditions)
        self.assertIn(COND_CONTROLLED, pc.conditions)
        self.assertEqual(foe.check_penalty(), D.GRAPPLE_PENALTY)
        self.assertFalse(enc.has_action(pc))

    def test_grab_take_item_does_not_control(self):
        pc = make_pc(skills={"brawling": 8})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9, 1])
        out = enc.grab(pc, foe, take_item=True)
        self.assertEqual(out["action"], "夺取物品")
        self.assertNotIn(COND_CONTROLLED, foe.conditions)

    def test_choke_needs_control_then_deals_body_damage_ignoring_armor(self):
        pc = make_pc(skills={"brawling": 8}, stats={"body": 8})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        self.assertFalse(enc.choke(pc, foe)["ok"])              # 还没控制住
        pc.conditions.add(COND_CONTROLLED)
        foe.conditions.add(COND_CONTROLLED)
        out = enc.choke(pc, foe)
        self.assertTrue(out["ok"])
        self.assertEqual(out["damage"], 8)
        self.assertEqual(foe.hp_cur, 32)                        # 无视 SP 7
        self.assertEqual(foe.armor_at("torso"), 7)               # 不磨损

    def test_choke_knockout_after_enough_rounds(self):
        pc = make_pc(skills={"brawling": 8})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        pc.conditions.add(COND_CONTROLLED)
        foe.conditions.add(COND_CONTROLLED)
        out = None
        for _ in range(D.CHOKE_KO_ROUNDS):
            enc.refresh_actions(pc)
            out = enc.choke(pc, foe)
        self.assertTrue(out["ko"])
        self.assertIn(COND_UNCONSCIOUS, foe.conditions)

    def test_throw_requires_control_and_prones(self):
        pc = make_pc(skills={"brawling": 8}, stats={"body": 7})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        self.assertFalse(enc.throw_target(pc, foe)["ok"])
        foe.conditions.add(COND_CONTROLLED)
        pc.conditions.add(COND_CONTROLLED)
        out = enc.throw_target(pc, foe)
        self.assertTrue(out["ok"])
        self.assertIn(COND_PRONE, foe.conditions)
        self.assertNotIn(COND_CONTROLLED, foe.conditions)
        self.assertNotIn(COND_CONTROLLED, pc.conditions)

    def test_escape_grab_clears_both(self):
        pc = make_pc(skills={"brawling": 8})
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        pc.conditions.add(COND_CONTROLLED)
        foe.conditions.add(COND_CONTROLLED)
        enc.rng = ScriptRng([9, 1])
        out = enc.escape_grab(pc, foe)
        self.assertTrue(out["win"])
        self.assertNotIn(COND_CONTROLLED, pc.conditions)
        self.assertNotIn(COND_CONTROLLED, foe.conditions)

    def test_reload_fills_magazine(self):
        pc = make_pc(weapons=[PISTOL])
        enc = duel(pc=pc, grid=corridor(1), cells=1)
        out = enc.reload(pc, PISTOL)
        self.assertTrue(out["ok"])
        self.assertEqual(pc.ammo[PISTOL], D.WEAPONS[PISTOL]["ammo"])
        self.assertFalse(enc.has_action(pc))

    def test_reload_refuses_weapons_without_magazine(self):
        pc = make_pc(weapons=[MELEE])
        enc = duel(pc=pc, grid=corridor(1), cells=1)
        out = enc.reload(pc, MELEE)
        self.assertFalse(out["ok"])
        self.assertTrue(enc.has_action(pc))                     # 没有被消耗

    def test_stand_costs_half_the_move_budget(self):
        pc = make_pc()
        enc = duel(pc=pc, grid=corridor(4), cells=1)
        self.assertFalse(enc.stand(pc)["ok"])                   # 并没倒地
        pc.conditions.add(COND_PRONE)
        budget = enc.speed_left[pc.name]
        out = enc.stand(pc)
        self.assertTrue(out["ok"])
        self.assertEqual(out["cost"], budget // 2)
        self.assertNotIn(COND_PRONE, pc.conditions)

    def test_confrontation_marks_a_loser(self):
        pc = make_pc(stats={"cool": 8, "body": 5, "will": 5})
        pc.reputation = 4
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9, 1])
        out = enc.confrontation(pc, foe)
        self.assertEqual(out["loser"], foe.name)
        self.assertEqual(foe.timers["facedown"]["penalty"], -2)

    def test_use_item_consumes_gear(self):
        pc = make_pc(gear={"medkit": 1})
        enc = duel(pc=pc, grid=corridor(1), cells=1)
        self.assertTrue(enc.use_item(pc, "medkit")["ok"])
        self.assertFalse(enc.use_item(pc, "medkit")["ok"])


# ================================================================ 移动
class TestMovement(unittest.TestCase):
    """移动 = 机动 × 2 米，不消耗动作；预算用完就走不动。"""

    def test_move_costs_metres_not_action(self):
        pc = make_pc()
        enc = duel(pc=pc, grid=corridor(6), cells=0)
        out = enc.move(pc, (2, 1))
        self.assertTrue(out["ok"])
        self.assertEqual(out["spent"], 2 * METERS_PER_CELL)
        self.assertEqual(enc.speed_left[pc.name], pc.move_value() - 4)
        self.assertTrue(enc.has_action(pc))                     # 动作还在

    def test_move_budget_caps_total_distance(self):
        pc = make_pc(stats={"move": 3, "body": 5, "will": 5})   # 6 米预算
        enc = duel(pc=pc, grid=corridor(10), cells=0)
        enc.move(pc, (1, 1))
        enc.move(pc, (2, 1))
        self.assertEqual(enc.moved_this_turn[pc.name], 4)
        out = enc.move(pc, (4, 1))                              # 还需 4 米 > 剩 2
        self.assertFalse(out["ok"])
        self.assertIn("移动力不足", out["reason"])

    def test_move_refused_when_budget_short(self):
        pc = make_pc(stats={"move": 3, "body": 5, "will": 5})
        enc = duel(pc=pc, grid=corridor(10), cells=0)
        self.assertFalse(enc.move(pc, (5, 1))["ok"])

    def test_prone_cannot_move(self):
        pc = make_pc()
        enc = duel(pc=pc, grid=corridor(4), cells=0)
        pc.conditions.add(COND_PRONE)
        self.assertFalse(enc.move(pc, (1, 1))["ok"])

    def test_blocked_cell_refused(self):
        grid = BattleGrid(8, 4, obstacles=[(1, 1)])
        pc = make_pc()
        enc = duel(pc=pc, grid=grid, cells=0)
        self.assertFalse(enc.move(pc, (1, 1))["ok"])

    def test_dash_requires_prior_movement(self):
        pc = make_pc()
        enc = duel(pc=pc, grid=corridor(8), cells=0)
        self.assertFalse(enc.dash(pc)["ok"])
        enc.move(pc, (1, 1))
        out = enc.dash(pc)
        self.assertTrue(out["ok"])
        self.assertEqual(out["extra"], pc.move_value())

    def test_approach_closes_to_melee_and_syncs_engagement(self):
        pc = make_pc()
        enc = duel(pc=pc, grid=corridor(4), cells=3)
        out = enc.approach(pc, enc.enemies[0])
        self.assertTrue(out["ok"])
        self.assertLessEqual(enc.measure(pc, enc.enemies[0]), METERS_PER_CELL)

    def test_delay_reorders_the_queue(self):
        a = make_pc("甲")
        b = make_foe()
        enc = duel(pc=a, foe=b, grid=corridor(2), cells=1)
        self.assertEqual([c.name for c in enc.order], [a.name, b.name])
        out = enc.delay(b, a.name)                      # 乙推迟到甲之前
        self.assertTrue(out["ok"])
        self.assertEqual(out["order"], [b.name, a.name])


# ================================================================ 稳定与治疗
class TestStabilizeAndTreat(unittest.TestCase):
    """稳定对抗按伤势等级定的 DV；急救固定当天消除致命伤；外科治疗不能自医。"""

    def test_stabilize_action_follows_wound_state_dv(self):
        medic = make_pc("医", skills={"first_aid": 8, "paramedic": 8})
        dying = make_foe()
        dying.hp_cur = 0
        enc = duel(pc=medic, foe=dying, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9])
        out = enc.stabilize_action(medic, dying)
        self.assertTrue(out["success"])
        self.assertEqual(out["dv"], D.STABILIZE_DV["mortal"])
        self.assertEqual(dying.hp_cur, 1)
        self.assertFalse(dying.is_dead)

    def test_stabilize_failure_leaves_target_dying(self):
        medic = make_pc("医", skills={"first_aid": 0, "paramedic": 0})
        dying = make_foe()
        dying.hp_cur = 0
        enc = duel(pc=medic, foe=dying, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([1, 4])                             # 暴击失败
        out = enc.stabilize_action(medic, dying)
        self.assertFalse(out["success"])
        self.assertEqual(dying.hp_cur, 0)
        self.assertFalse(dying.is_dead)

    def test_first_aid_fix_on_a_fixable_injury(self):
        medic = make_pc("医", skills={"first_aid": 8})
        hurt = make_foe()
        hurt.apply_crit_injury(5)                               # 肋骨骨折，急救 DV 13
        enc = duel(pc=medic, foe=hurt, grid=corridor(1), cells=1)
        enc.rng = ScriptRng([9])
        out = enc.first_aid_fix(medic, hurt)
        self.assertTrue(out["success"])
        self.assertTrue(hurt.crit_injuries[0]["stabilized"])

    def test_first_aid_fix_refuses_amputation(self):
        medic = make_pc("医", skills={"first_aid": 8})
        hurt = make_foe()
        hurt.apply_crit_injury(2)
        enc = duel(pc=medic, foe=hurt, grid=corridor(1), cells=1)
        out = enc.first_aid_fix(medic, hurt)
        self.assertFalse(out["ok"])
        self.assertIn("外科", out["reason"])

    def test_treat_refuses_self_and_needs_medtech_for_surgery(self):
        medic = make_pc("医", skills={"paramedic": 8})
        hurt = make_foe()
        hurt.apply_crit_injury(2)                               # 需要医疗师手术
        enc = duel(pc=medic, foe=hurt, grid=corridor(1), cells=1)
        self.assertFalse(enc.treat_crit_injury(hurt, hurt)["ok"])
        rookie = make_pc("新手", skills={"paramedic": 0})
        enc2 = duel(pc=rookie, foe=hurt, grid=corridor(1), cells=1)
        out = enc2.treat_crit_injury(rookie, hurt)
        self.assertFalse(out["ok"])
        self.assertIn("外科", out["reason"])
        enc.rng = ScriptRng([9])
        self.assertTrue(enc.treat_crit_injury(medic, hurt)["success"])
        self.assertEqual(hurt.crit_injuries, [])

    def test_treat_refuses_when_no_injury(self):
        medic = make_pc("医", skills={"paramedic": 8})
        other = make_foe()
        enc = duel(pc=medic, foe=other, grid=corridor(1), cells=1)
        self.assertFalse(enc.treat_crit_injury(medic, other)["ok"])


# ================================================================ 胜负与展示
class TestOutcomeAndDisplay(unittest.TestCase):
    def test_is_over_party_when_all_enemies_down(self):
        enc = duel(grid=corridor(1), cells=1)
        self.assertIsNone(enc.is_over())
        enc.enemies[0].hp_cur = 0
        self.assertEqual(enc.is_over(), "party")

    def test_is_over_tpk_when_all_allies_down(self):
        enc = duel(grid=corridor(1), cells=1)
        enc.allies[0].hp_cur = 0
        self.assertEqual(enc.is_over(), "tpk")

    def test_dead_enemy_does_not_count_as_alive(self):
        enc = duel(grid=corridor(1), cells=1)
        enc.enemies[0].conditions.add("dead")
        self.assertEqual(enc.alive("enemy"), [])
        self.assertEqual(enc.alive("party"), enc.allies)

    def test_defeated_ip_scales_with_body(self):
        weak = make_foe()
        strong = make_foe()
        strong.stats["body"] = 9
        enc = duel(foe=weak, grid=corridor(1), cells=1)
        base = enc.defeated_ip()
        enc.enemies.append(strong)
        self.assertGreater(enc.defeated_ip(), base)

    def test_board_renders_red_terms(self):
        pc = make_pc("甲")
        pc.hp_cur = 10
        pc.apply_crit_injury(8)
        enc = duel(pc=pc, grid=corridor(1), cells=1)
        enc.begin_turn(pc)
        board = enc.board()
        self.assertIn("甲: PW 10/45", board)
        self.assertIn("SP 11", board)
        self.assertIn("重伤", board)
        self.assertIn("致命伤×1", board)

    def test_board_marks_dying_and_dead(self):
        dying = make_foe()
        dying.hp_cur = 0
        dead = make_foe("houston", tag="2")
        dead.conditions.add("dead")
        enc = duel(foe=dying, grid=corridor(1), cells=1)
        enc.enemies.append(dead)
        enc.order = [dying, dead]
        board = enc.board()
        self.assertIn("濒死", board)
        self.assertIn("已死亡", board)

    def test_brief_and_status_lines(self):
        enc = duel(grid=corridor(1), cells=1)
        self.assertIn("第 1 轮", enc.brief())
        self.assertEqual(len(enc.status_lines()), 2)
        self.assertTrue(all("PW" in line for line in enc.status_lines()))

    def test_find_by_name_and_prefix(self):
        pc = make_pc("Kyle")
        enc = duel(pc=pc, grid=corridor(1), cells=1)
        self.assertIs(enc.find("Kyle"), pc)
        self.assertIs(enc.find("Ky"), pc)          # 前缀命中
        self.assertIsNone(enc.find("查无此人"))

    def test_rolls_ledger_records_mods(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        ranged(pc, enc, ScriptRng([8, 3, 3, 3]))
        self.assertTrue(enc.rolls)
        entry = enc.rolls[0]
        self.assertEqual(entry["kind"], "attack")
        self.assertIn("反应+6", entry["mods"])
        self.assertIn("手枪+6", entry["mods"])


# ================================================================ 敌人 AI
class TestNpcAI(unittest.TestCase):
    """敌人 AI：先脱困、血少就跑、否则打最近的对手。"""

    def test_moves_when_melee_out_of_reach_and_no_speed_left(self):
        enc = duel(foe=make_foe(), grid=corridor(6), cells=5)
        foe = enc.enemies[0]
        foe.weapons = [MELEE]
        enc.speed_left[foe.name] = 0
        plan = enc.npc_choose_action(foe)
        self.assertEqual(plan["action"], "move")
        self.assertEqual(plan["target"], enc.allies[0].name)

    def test_attacks_with_approach_flag_when_speed_remains(self):
        enc = duel(foe=make_foe(), grid=corridor(6), cells=5)
        foe = enc.enemies[0]
        foe.weapons = [MELEE]
        plan = enc.npc_choose_action(foe)
        self.assertEqual(plan["action"], "attack")
        self.assertTrue(plan["approach"])

    def test_attacks_with_its_ranged_weapon(self):
        enc = duel(foe=make_foe(), grid=corridor(6), cells=3)
        plan = enc.npc_choose_action(enc.enemies[0])
        self.assertEqual(plan["action"], "attack")
        self.assertEqual(plan["weapon"], "very_heavy_pistol")

    def test_escapes_when_controlled(self):
        enc = duel(foe=make_foe(), grid=corridor(2), cells=1)
        foe = enc.enemies[0]
        foe.conditions.add(COND_CONTROLLED)
        self.assertEqual(enc.npc_choose_action(foe)["action"], "escape_grapple")

    def test_flees_when_badly_wounded_and_unimpressive(self):
        enc = duel(foe=make_foe(), grid=corridor(2), cells=1)
        foe = enc.enemies[0]
        foe.stats["cool"] = 4
        foe.hp_cur = 5
        enc.rng = ScriptRng([], seed=1)                         # random() < 0.5
        self.assertEqual(enc.npc_choose_action(foe)["action"], "flee")

    def test_waits_when_no_targets(self):
        enc = duel(foe=make_foe(), grid=corridor(2), cells=1)
        enc.allies = []
        self.assertEqual(enc.npc_choose_action(enc.enemies[0])["action"], "wait")


# ================================================================ 状态与计时
class TestConditionsAndTimers(unittest.TestCase):
    def test_set_condition_with_timer(self):
        enc = duel(grid=corridor(1), cells=1)
        pc = enc.allies[0]
        enc.set_condition(pc, "blinded", rounds=2, save="concentration", dv=15)
        self.assertIn("blinded", pc.conditions)
        self.assertEqual(pc.timers["blinded"]["left"], 2)

    def test_timer_ticks_down_and_expires(self):
        enc = duel(grid=corridor(1), cells=1)
        pc = enc.allies[0]
        enc.add_timer(pc, "blinded", 1)
        pc.conditions.add("blinded")
        enc._tick_timers(pc)
        self.assertNotIn("blinded", pc.conditions)
        self.assertNotIn("blinded", pc.timers)

    def test_save_based_timer_clears_on_success(self):
        pc = make_pc(skills={"resist_torture_drugs": 8})
        enc = duel(pc=pc, grid=corridor(1), cells=1)
        enc.add_timer(pc, "毒", 3, save="resist_torture_drugs", dv=13)
        pc.conditions.add("毒")
        enc.rng = ScriptRng([9])
        enc._tick_timers(pc)
        self.assertNotIn("毒", pc.conditions)

    def test_remove_condition(self):
        enc = duel(grid=corridor(1), cells=1)
        pc = enc.allies[0]
        enc.set_condition(pc, "blinded", rounds=3)
        enc.remove_condition(pc, "blinded")
        self.assertNotIn("blinded", pc.conditions)
        self.assertNotIn("blinded", pc.timers)

    def test_cover_material_durability(self):
        enc = duel(grid=corridor(1), cells=1)
        enc.set_cover((3, 3), "厚钢")
        self.assertEqual(enc.cover_at((3, 3))[0], "厚钢")
        self.assertEqual(enc.cover_at((3, 3))[1], D.COVER["厚钢"])
        self.assertIsNone(enc.cover_at((0, 0)))


# ================================================================ 剧场模式
class TestTheaterMode(unittest.TestCase):
    """无网格：没有射程门，移动直接视为逼近。"""

    def test_move_consumes_budget_without_grid(self):
        enc = duel()
        pc = enc.allies[0]
        enc.speed_left[pc.name] = pc.move_value()
        out = enc.move(pc, (3, 3))
        self.assertTrue(out["ok"])
        self.assertEqual(enc.speed_left[pc.name], 0)

    def test_approach_engages(self):
        enc = duel()
        pc, foe = enc.allies[0], enc.enemies[0]
        self.assertTrue(enc.approach(pc, foe)["ok"])
        self.assertEqual(enc.measure(pc, foe), METERS_PER_CELL)

    def test_ranged_attack_uses_the_mid_band(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc)
        res = ranged(pc, enc, ScriptRng([9, 3, 3, 3]))          # 默认 13 米
        self.assertTrue(res["hit"])                             # 9 + 12 = 21 > 20
        self.assertEqual(res["dv"], D.RANGED_DV["pistol"][2])


# ================================================================ 装配
class TestEncounterAssembly(unittest.TestCase):
    """``state.build_encounter`` 用模组数据装配遭遇。"""

    def test_build_from_module_fixture(self):
        from ctrcg.game import state as G
        from helpers import make_state
        st = make_state()
        enc = G.build_encounter("e1", st)
        self.assertEqual(len(enc.enemies), 3)                   # 2 警察 + 1 食尸鬼
        self.assertEqual(len(enc.allies), len(st.party()))
        self.assertEqual(enc.title, "巷口伏击")
        self.assertIsNotNone(enc.grid)                          # 场景声明了 map
        self.assertEqual(enc.grid.w, 11)

    def test_unknown_encounter_rejected(self):
        from ctrcg.game import state as G
        from helpers import make_state
        with self.assertRaises(G.EncounterBuildError):
            G.build_encounter("不存在", make_state())

    def test_unknown_enemy_rejected(self):
        from ctrcg.game import state as G
        from helpers import make_state
        st = make_state()
        st.module.encounters["e_bad"] = {"enemies": [["幽灵", 1]]}
        with self.assertRaises(G.EncounterBuildError):
            G.build_encounter("e_bad", st)

    def test_multi_count_gets_tags_and_no_cell_collisions(self):
        from ctrcg.game import state as G
        from helpers import make_state
        enc = G.build_encounter("e1", make_state())
        names = [c.name for c in enc.enemies]
        self.assertEqual(len(names), len(set(names)))           # 同类多只加编号
        enc.begin()
        cells = [enc.positions[c.name] for c in enc.combatants
                 if c.name in enc.positions]
        self.assertEqual(len(cells), len(set(cells)))           # 不叠格

    def test_module_fixture_passes_validation(self):
        from ctrcg.campaign.loader import validate_module
        errors, _warnings = validate_module(fixture_module_data())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
