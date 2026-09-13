"""战斗引擎测试。"""

import random
import unittest

from ctrcg.rules.character import (
    Character, COND_POISONED, COND_PRONE, COND_SLEEP, COND_UNCONSCIOUS,
)
from ctrcg.rules.combat import Encounter, Monster, monster_choose_action


def make_pc(name="罗兰", klass="fighter", race="human", scores=None, style="defense", level=1):
    base = {"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8}
    if scores:
        base.update(scores)
    skills = {"fighter": ["athletics", "intimidation"], "wizard": ["arcana", "history"],
              "cleric": ["insight", "medicine"], "rogue": ["stealth", "acrobatics", "deception", "perception"],
              "ranger": ["stealth", "perception", "survival"]}[klass]
    return Character.create(
        name=name, race=race, klass=klass, background="soldier",
        scores=base, skill_choices=skills, fighting_style=style, level=level)


class TestInitiative(unittest.TestCase):
    def test_order_and_round_start(self):
        rng = random.Random(11)
        enc = Encounter("test")
        pc = make_pc()
        gob = Monster("goblin", rng)
        enc.add(pc, "party")
        enc.add(gob, "enemy")
        enc.begin(rng)
        self.assertEqual(len(enc.order), 2)
        self.assertEqual(enc.round, 1)
        self.assertIsNone(enc.is_over())

    def test_over_when_side_wiped(self):
        rng = random.Random(11)
        enc = Encounter("test")
        enc.add(make_pc(), "party")
        g = Monster("goblin", rng)
        enc.add(g, "enemy")
        g.take_damage(99)
        enc.begin(rng)
        self.assertEqual(enc.is_over(), "party")


class TestAttacks(unittest.TestCase):
    def _enc(self):
        rng = random.Random(23)
        enc = Encounter("t")
        enc.rng = rng
        return enc, rng

    def test_hit_reduces_hp(self):
        enc, rng = self._enc()
        pc = make_pc()
        gob = Monster("goblin", rng)
        gob.ac_base = 10
        enc.add(pc, "party")
        enc.add(gob, "enemy")
        hp0 = gob.hp_cur
        hit_any = False
        for _ in range(30):
            res = enc.attack(pc, gob, "longsword", rng)
            if res["hit"]:
                hit_any = True
                self.assertGreater(res["damage"], 0)
                self.assertEqual(gob.hp_cur, max(0, hp0 - res["damage"]))  # 超杀溢出
                break
        self.assertTrue(hit_any, "30 次对 AC10 未命中过一次（概率不可能）")

    def test_nat1_misses_and_low_ac_hits(self):
        enc, rng = self._enc()
        pc = make_pc()
        tanky = Monster("goblin", rng)
        tanky.ac_base = 26  # +5 攻击加值最多 25：仅 nat20 自动命中（暴击）
        enc.add(pc, "party")
        enc.add(tanky, "enemy")
        results = [enc.attack(pc, tanky, "longsword", rng) for _ in range(50)]
        self.assertTrue(all((not r["hit"]) or r["crit"] for r in results))
        self.assertTrue(any(r["crit"] for r in results), "50 次应出现 nat20")

    def test_crit_doubles_dice(self):
        enc, rng = self._enc()
        pc = make_pc()
        gob = Monster("goblin", rng)
        gob.ac_base = 5
        enc.add(pc, "party")
        enc.add(gob, "enemy")
        dmg = [enc.attack(pc, gob, "longsword", rng) for _ in range(80)]
        crits = [r for r in dmg if r["crit"]]
        self.assertTrue(crits, "80 次攻击对 AC5 应出现暴击")
        # 暴击伤害骰翻倍：最小 2d8+3=5，大于非暴击最小 1d8+3=4
        for c in crits:
            self.assertGreaterEqual(c["damage"], 5)

    def test_finesse_uses_dex(self):
        enc, rng = self._enc()
        rogue = make_pc("夜影", "rogue", scores={"str": 8, "dex": 15, "con": 12, "int": 14, "wis": 10, "cha": 13})
        gob = Monster("goblin", rng)
        gob.ac_base = 10
        enc.add(rogue, "party")
        enc.add(gob, "enemy")
        dmg = None
        for _ in range(30):
            r = enc.attack(rogue, gob, "shortsword", rng)
            if r["hit"]:
                dmg = r["damage"]
                break
        self.assertIsNotNone(dmg)
        # 短剑 1d6 + dex(+3) + 偷袭 1d6（近战游荡者）→ 最小 1+3+1=5
        self.assertGreaterEqual(dmg, 5)

    def test_monster_attack_and_multiattack(self):
        enc, rng = self._enc()
        pc = make_pc()
        cap = Monster("bandit_captain", rng)
        cap.ac_base = 10
        enc.add(pc, "party")
        enc.add(cap, "enemy")
        r = enc.attack(cap, pc, "scimitar", rng)
        self.assertTrue(r["hit"])

    def test_help_grants_advantage_note(self):
        enc, rng = self._enc()
        pc, ally = make_pc(), make_pc("薇兰")
        gob = Monster("goblin", rng)
        enc.add(pc, "party")
        enc.add(ally, "party")
        enc.add(gob, "enemy")
        enc.do_help(ally, gob)
        adv, notes = enc._advantage_for(pc, gob, ranged=False)
        self.assertGreaterEqual(adv, 1)
        self.assertTrue(any("助攻" in n for n in notes))


class TestSpells(unittest.TestCase):
    def _enc(self):
        rng = random.Random(31)
        enc = Encounter("t")
        enc.rng = rng
        return enc, rng

    def test_magic_missile_auto_hit(self):
        enc, rng = self._enc()
        wiz = make_pc("皮普", "wizard", race="rock_gnome",
                      scores={"str": 8, "dex": 14, "con": 12, "int": 15, "wis": 10, "cha": 13})
        gob = Monster("goblin", rng)
        enc.add(wiz, "party")
        enc.add(gob, "enemy")
        hp0 = gob.hp_cur
        r = enc.cast(wiz, "magic_missile", [gob.name], rng)
        self.assertTrue(r["ok"])
        lost = hp0 - gob.hp_cur
        # 3 枚飞弹每枚 1d4+1(2~5)，目标倒下后剩余飞弹不再造成伤害
        self.assertGreaterEqual(lost, min(3 * 2, hp0))
        self.assertLessEqual(lost, 3 * 5)

    def test_fireball_save_half(self):
        enc, rng = self._enc()
        wiz = make_pc("皮普", "wizard", race="rock_gnome", level=5,
                      scores={"str": 8, "dex": 14, "con": 12, "int": 15, "wis": 10, "cha": 13})
        gob = Monster("goblin", rng)
        enc.add(wiz, "party")
        enc.add(gob, "enemy")
        r = enc.cast(wiz, "fireball", [gob.name], rng)
        self.assertTrue(r["ok"])
        self.assertLess(gob.hp_cur, gob.hp_max)

    def test_slots_consumed(self):
        enc, rng = self._enc()
        wiz = make_pc("皮普", "wizard", race="rock_gnome",
                      scores={"str": 8, "dex": 14, "con": 12, "int": 15, "wis": 10, "cha": 13})
        gob = Monster("goblin", rng)
        enc.add(wiz, "party")
        enc.add(gob, "enemy")
        enc.cast(wiz, "magic_missile", [gob.name], rng)
        self.assertEqual(wiz.spell_slots().get(1, 0), 1)
        enc.cast(wiz, "magic_missile", [gob.name], rng)
        r = enc.cast(wiz, "magic_missile", [gob.name], rng)
        self.assertFalse(r["ok"])  # 法术位耗尽

    def test_cure_wounds_heals(self):
        enc, rng = self._enc()
        cle = make_pc("安娜", "cleric",
                      scores={"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 12})
        cle.take_damage(8)
        enc.add(cle, "party")
        r = enc.cast(cle, "cure_wounds", [cle.name], rng)
        self.assertGreater(r["healed"], 0)
        self.assertGreater(cle.hp_cur, cle.hp_max - 8)  # 确实回复了
        self.assertLessEqual(cle.hp_cur, cle.hp_max)    # 满血封顶

    def test_sleep_applies_condition(self):
        enc, rng = self._enc()
        wiz = make_pc("皮普", "wizard", race="rock_gnome", level=5,
                      scores={"str": 8, "dex": 14, "con": 12, "int": 15, "wis": 10, "cha": 13})
        gob = Monster("goblin", rng)
        enc.add(wiz, "party")
        enc.add(gob, "enemy")
        r = enc.cast(wiz, "sleep", [gob.name], rng)
        self.assertTrue(r["ok"])
        # 5d8（均 22）对 7HP 哥布林；骰运极差时可能失败，重试直至验证机制
        gob2 = Monster("goblin", rng, tag="2")  # 同类多只要编号，避免重名
        enc.add(gob2, "enemy")
        for _ in range(3):
            enc.cast(wiz, "sleep", [gob2.name], rng)
            if COND_SLEEP in gob2.conditions:
                break
        self.assertIn(COND_SLEEP, gob2.conditions)
        self.assertFalse(enc.alive(gob2))  # 沉睡目标不再是有效战斗者

    def test_death_save_flow(self):
        c = make_pc()
        c.take_damage(c.hp_cur)   # 打到 0 但余伤不足即死（大伤害即死另测）
        self.assertIn(COND_UNCONSCIOUS, c.conditions)
        rng = random.Random(2)
        for _ in range(40):
            ds = c.death_save_turn(rng)
            if ds.get("result") in ("died", "stable", "revived"):
                break
        self.assertIn(ds.get("result"), ("died", "stable", "revived"))


class TestMonsterAI(unittest.TestCase):
    def test_ai_targets_weakest(self):
        rng = random.Random(77)
        enc = Encounter("t")
        enc.rng = rng
        strong = make_pc("罗兰")
        weak = make_pc("皮普", "wizard", race="rock_gnome")
        weak.take_damage(weak.hp_cur - 3)
        enc.add(strong, "party")
        enc.add(weak, "party")
        gob = Monster("goblin", rng)
        enc.add(gob, "enemy")
        act = monster_choose_action(enc, gob)
        self.assertEqual(act["target"], weak.name)

    def test_ai_ranged_when_not_engaged(self):
        rng = random.Random(78)
        enc = Encounter("t")
        enc.rng = rng
        pc = make_pc()
        enc.add(pc, "party")
        gob = Monster("goblin", rng)
        enc.add(gob, "enemy")
        act = monster_choose_action(enc, gob)
        self.assertEqual(act["weapon"], "shortbow")


if __name__ == "__main__":
    unittest.main()
