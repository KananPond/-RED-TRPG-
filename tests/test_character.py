"""角色系统测试。"""

import random
import unittest

from ctrcg.rules import data as D
from ctrcg.rules.character import Character


def make_fighter(name="罗兰"):
    return Character.create(
        name=name, race="human", klass="fighter", background="soldier",
        scores={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
        skill_choices=["athletics", "intimidation"], fighting_style="defense",
    )


class TestCharacterCreation(unittest.TestCase):
    def test_racial_bonuses_applied(self):
        c = make_fighter()
        # BG3 种族加值：默认取法 human → 力+2 / 体+1
        self.assertEqual(c.abilities["str"], 17)
        self.assertEqual(c.abilities["con"], 15)
        self.assertEqual(c.abilities["cha"], 8)

    def test_bg3_bonus_allocation(self):
        """BG3 规则：+2/+1 任意两项属性，可自选。"""
        c = Character.create(
            name="札瑞", race="tiefling_zariel", klass="paladin", background="soldier",
            scores={"str": 15, "dex": 10, "con": 13, "int": 8, "wis": 12, "cha": 14},
            skill_choices=["athletics", "intimidation"],
            bonus2="cha", bonus1="str")
        self.assertEqual(c.abilities["cha"], 16)
        self.assertEqual(c.abilities["str"], 16)
        with self.assertRaises(ValueError):
            D.resolve_bonuses("tiefling_zariel", "str", "str")

    def test_gold_dwarf_toughness(self):
        """黄金矮人：矮人坚韧每级生命 +1。"""
        c = Character.create(
            name="铁砧", race="dwarf_gold", klass="barbarian", background="soldier",
            scores={"str": 15, "dex": 13, "con": 14, "int": 8, "wis": 12, "cha": 10},
            skill_choices=["athletics", "survival"])
        # d12: 12 + con(+3，黄金矮人默认体+2) + 1（坚韧）
        self.assertEqual(c.hp_max, 16)

    def test_unarmored_defense(self):
        """野蛮人/武僧的无甲防御（AC=10+敏捷+体质/感知）。"""
        barb = Character.create(
            name="怒风", race="half_orc", klass="barbarian", background="outlander",
            scores={"str": 15, "dex": 14, "con": 13, "int": 8, "wis": 12, "cha": 10},
            skill_choices=["athletics", "survival"])
        # 10 + dex(+2，半兽人默认+2敏) + con(+2，半兽人默认+1体→14→+2)
        self.assertEqual(barb.ac(), 10 + 2 + barb.mod("con"))
        self.assertGreaterEqual(barb.ac(), 14)

    def test_hp_chain_mail_ac(self):
        c = make_fighter()
        self.assertEqual(c.level, 1)
        # d10: 10 + con(+2) = 12；链甲16+1防御风格+盾2=19
        self.assertEqual(c.hp_max, 12)
        self.assertEqual(c.ac(), 19)

    def test_prof_and_skills(self):
        c = make_fighter()
        self.assertEqual(c.prof_bonus(), 2)
        self.assertEqual(c.skill_mod("athletics"), 3 + 2)  # str16=3 + 熟练2
        self.assertEqual(c.skill_mod("arcana"), 0)         # int10 未熟练

    def test_check_mods_breakdown_matches_total(self):
        """检定过程明细：mods 之和 = 总值 − d20（前端检定卡依赖此恒等式）。"""
        c = make_fighter()
        rng = random.Random(11)
        for skill in ("athletics", "arcana", "intimidation"):
            r = c.check(rng, skill, 10)
            mod_sum = sum(v for _, v in r["mods"])
            self.assertEqual(r["d20"] + mod_sum, r["total"],
                             f"{skill}: {r['mods']} 与总值不符")
            labels = [k for k, _ in r["mods"]]
            self.assertEqual(labels[0], D.ABILITIES[D.SKILLS[skill]["ability"]])
        # 熟练技能带"熟练"条目，未熟练不带
        self.assertIn("熟练", [k for k, _ in c.check(rng, "athletics", 10)["mods"]])
        self.assertNotIn("熟练", [k for k, _ in c.check(rng, "arcana", 10)["mods"]])

    def test_save_mods_breakdown_matches_total(self):
        c = make_fighter()
        rng = random.Random(3)
        r = c.save(rng, "con", 10)
        mod_sum = sum(v for _, v in r["mods"])
        self.assertEqual(r["d20"] + mod_sum, r["total"])
        # 战士体质豁免熟练
        self.assertIn("熟练", [k for k, _ in r["mods"]])

    def test_ranger_slots_start_empty(self):
        c = Character.create(
            name="薇兰", race="high_elf", klass="ranger", background="folk_hero",
            scores={"str": 13, "dex": 15, "con": 12, "int": 10, "wis": 14, "cha": 8},
            skill_choices=["stealth", "perception", "survival"],
        )
        self.assertEqual(c.spell_slots(), {})  # 1级半施法者无法术位
        # 精灵+1int，察觉熟练来自种族
        self.assertEqual(c.abilities["dex"], 17)

    def test_wizard_slots(self):
        c = Character.create(
            name="皮普", race="rock_gnome", klass="wizard", background="sage",
            scores={"str": 8, "dex": 14, "con": 12, "int": 15, "wis": 10, "cha": 13},
            skill_choices=["arcana", "history"],
        )
        self.assertEqual(c.spell_slots(), {1: 2})
        self.assertEqual(c.ac(), 12)  # 无甲 10 + dex(+2)
        self.assertEqual(c.cast_ability(), "int")


class TestChecksAndSaves(unittest.TestCase):
    def test_check_math_and_dc(self):
        c = make_fighter()
        rng = random.Random(5)
        for _ in range(50):
            r = c.check(rng, "athletics", 10)
            expect_total = r["detail"]
            self.assertIsInstance(r["success"], bool)

    def test_save_proficiency(self):
        c = make_fighter()
        self.assertEqual(c.save_mod("str"), 3 + 2)
        self.assertEqual(c.save_mod("dex"), 1)  # dex13（无加值）→ +1，未熟练

    def test_passive_perception(self):
        c = make_fighter()
        self.assertEqual(c.passive_perception(), 10 + 1)  # wis12 → +1


class TestDamageRest(unittest.TestCase):
    def test_take_damage_down_and_heal(self):
        c = make_fighter()
        info = c.take_damage(12)
        self.assertTrue(info["down"])
        self.assertEqual(c.hp_cur, 0)
        self.assertIn("unconscious", c.conditions)
        healed = c.heal(5)
        self.assertEqual(healed, 5)
        self.assertNotIn("unconscious", c.conditions)

    def test_death_saves_three_fails_die(self):
        c = make_fighter()
        c.take_damage(99)
        c.death_fails = 2
        rng = random.Random(0)
        for _ in range(30):
            c.death_fails = 2
            c.death_successes = 0
            ds = c.death_save_turn(rng)
            if ds.get("result") == "died":
                break
        else:
            self.fail("30 次内未出现死亡（概率不可能）")

    def test_short_rest_uses_hit_dice(self):
        c = make_fighter()
        c.take_damage(6)
        msg = c.short_rest(random.Random(3))
        self.assertLessEqual(c.hit_dice_used, c.level)
        self.assertEqual(c.hp_cur, c.hp_max)

    def test_long_rest_restores(self):
        c = make_fighter()
        c.slots_used[1] = 2
        c.hit_dice_used = 1
        c.long_rest()
        self.assertEqual(c.slots_used, {})
        self.assertEqual(c.hp_cur, c.hp_max)
        self.assertEqual(c.hit_dice_used, 0)

    def test_level_up(self):
        c = make_fighter()
        msg = c.gain_xp(300)
        self.assertEqual(c.level, 2)
        self.assertIn("2 级", msg)
        c.gain_xp(600)  # 900 → 3级
        self.assertEqual(c.level, 3)
        self.assertEqual(c.prof_bonus(), 2)

    def test_serialization_roundtrip(self):
        c = make_fighter()
        c2 = Character.from_dict(c.to_dict())
        self.assertEqual(c2.abilities, c.abilities)
        self.assertEqual(c2.skills, c.skills)
        self.assertEqual(c2.hp_max, c.hp_max)


if __name__ == "__main__":
    unittest.main()
