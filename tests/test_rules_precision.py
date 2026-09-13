"""5e 规则精确性回归测试：本次"完全规则化"修复的每一项都对应一个用例。"""

import random
import unittest

from ctrcg.rules import data as D
from ctrcg.rules.character import (
    Character, COND_GRAPPLED, COND_PARALYZED, COND_SLEEP, COND_STABLE,
    COND_UNCONSCIOUS,
)
from ctrcg.rules.combat import Encounter, Monster


class ScriptRng:
    """按脚本吐数的确定性 RNG（randint 依次取值）。"""

    def __init__(self, values):
        self.values = list(values)
        self.i = 0

    def randint(self, a, b):
        v = self.values[self.i % len(self.values)]
        self.i += 1
        return max(a, min(b, v))


def make_pc(name="罗兰", klass="fighter", race="human", scores=None, style="defense",
            level=1):
    base = {"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8}
    if scores:
        base.update(scores)
    skills = {"fighter": ["athletics", "intimidation"],
              "wizard": ["arcana", "history"],
              "bard": ["performance", "persuasion"],
              "druid": ["nature", "medicine"], "paladin": ["athletics", "insight"],
              "rogue": ["stealth", "acrobatics", "deception", "perception"],
              "monk": ["acrobatics", "religion"]}[klass]
    return Character.create(
        name=name, race=race, klass=klass, background="soldier",
        scores=base, skill_choices=skills, fighting_style=style, level=level)


def duel(pc, seed=1):
    rng = random.Random(seed)
    enc = Encounter("t", rng=rng)
    foe = Monster("goblin", rng)
    enc.add(pc, "party")
    enc.add(foe, "enemy")
    return enc, foe, rng


class TestAttackAdvantageDice(unittest.TestCase):
    def test_monster_advantage_rolls_two_d20(self):
        """怪物优势必须掷两次 d20（而非 +1 加值）。"""
        m = Monster("goblin", random.Random(1))
        m.ac_base = 15
        enc = Encounter("t")
        # [5, 20]：劣势取 5；优势取 20
        r = enc._attack_d20(m, ScriptRng([5, 20]), -1)
        self.assertEqual(r.kept[0], 5)
        r2 = enc._attack_d20(m, ScriptRng([5, 20]), 1)
        self.assertEqual(r2.kept[0], 20)

    def test_monster_attack_disadvantage_misses_high_ac(self):
        enc = Encounter("t")
        gob = Monster("goblin", random.Random(1))
        pc = make_pc()
        pc.armor = "chain_mail"     # AC 16
        enc.add(pc, "party")
        enc.add(gob, "enemy")
        enc.rng = ScriptRng([20, 3])   # 劣势：min(20,3)=3 → 必 miss
        pc.conditions.add(COND_UNCONSCIOUS := __import__(
            "ctrcg.rules.character", fromlist=["COND_UNCONSCIOUS"]).COND_UNCONSCIOUS)
        # 去掉昏迷（避免必暴干扰），只测劣势本身：用高等AC + 无自动命中
        pc.conditions.discard(COND_UNCONSCIOUS)
        gob.attacks = [{"weapon": "scimitar", "bonus": 0, "extra": "+0"}]
        enc.surprised.discard(pc.name)
        # 制造劣势：目标不可见之外的因素——直接给 PC 隐形
        pc.conditions.add("invisible")
        r = enc._monster_attack(gob, pc, "scimitar", enc.rng)
        self.assertFalse(r["hit"], "劣势骰 3 对 AC16 应未命中（证明掷了两次取低）")

    def test_spell_crit_doubles_dice_not_total(self):
        """法术暴击 = 骰数翻倍（2d10），不是 1d10 总额×2。"""
        w = make_pc("法师", klass="wizard", level=1)
        enc, foe, _rng = duel(w, seed=3)
        # 攻击骰 20 暴击；伤害骰 3、7 → 2d10 合计 10（若总额翻倍只会是 6）
        out = enc.cast(w, "fire_bolt", [foe.name], ScriptRng([20, 3, 7]))
        self.assertIn("暴击", out["detail"])
        self.assertIn("10 点", out["detail"])


class TestMirrorImage(unittest.TestCase):
    def test_high_roll_hits_duplicate(self):
        """3 分身：d20≥6 → 分身被击碎，攻击落空。"""
        w = make_pc("法师", klass="wizard")
        enc, foe, _ = duel(w, seed=4)
        w.mirror_images = 3
        r = enc._monster_attack(foe, w, "scimitar", ScriptRng([6, 20]))
        self.assertFalse(r["hit"])
        self.assertEqual(w.mirror_images, 2)

    def test_low_roll_hits_real_body(self):
        w = make_pc("法师", klass="wizard")
        enc, foe, _ = duel(w, seed=4)
        w.mirror_images = 3
        # d20=2 <6 → 攻击照常进行（AC 命中与否由后续骰决定：9 必 miss AC16?）
        r = enc._monster_attack(foe, w, "scimitar", ScriptRng([2, 9, 9]))
        self.assertEqual(w.mirror_images, 3, "未达阈值不应消耗分身")

    def test_thresholds_by_image_count(self):
        w = make_pc("法师", klass="wizard")
        enc, foe, _ = duel(w, seed=4)
        for images, threshold in ((3, 6), (2, 8), (1, 11)):
            w.mirror_images = images
            r = enc._monster_attack(foe, w, "scimitar",
                                    ScriptRng([threshold - 1, 2, 2]))
            self.assertEqual(w.mirror_images, images, f"{images}分身时 d20={threshold-1} 不应击碎")


class TestHelpAndDodge(unittest.TestCase):
    def test_help_consumed_after_one_attack(self):
        pc = make_pc()
        ally = make_pc("艾拉", klass="monk")
        enc, foe, _ = duel(pc, seed=5)
        enc.do_help(pc, foe)          # pc 助攻
        adv1, notes1 = enc._advantage_for(ally, foe, ranged=False)
        self.assertIn("队友助攻", notes1)
        adv2, notes2 = enc._advantage_for(ally, foe, ranged=False)
        self.assertNotIn("队友助攻", notes2, "助攻优势只对下一次攻击有效")

    def test_dodge_expires_at_own_turn_start(self):
        pc = make_pc()
        enc, foe, _ = duel(pc, seed=6)
        enc.do_dodge(pc)
        self.assertIn(pc.name, enc.dodging)
        enc.begin_turn(pc)
        self.assertNotIn(pc.name, enc.dodging, "防御姿态只到下回合开始")

    def test_reckless_gives_enemies_advantage(self):
        barb = make_pc("蛮子", klass="fighter", race="human", level=2)
        barb.feature_choices = {}   # 无关
        # 用野蛮人测：需 2 级 reckless
        from ctrcg.rules.character import Character as C
        barb.klass = "barbarian"
        enc, foe, _ = duel(barb, seed=7)
        enc._advantage_for(barb, foe, ranged=False, reckless=True)
        self.assertIn(barb.name, enc.reckless_until, "鲁莽攻击应留下破绽")
        adv, notes = enc._advantage_for(foe, barb, ranged=False)
        self.assertEqual(adv, 1, "敌人对鲁莽中的野蛮人应有优势")
        self.assertIn("目标鲁莽", notes)
        enc.begin_turn(barb)
        self.assertNotIn(barb.name, enc.reckless_until)


class TestHelplessAndDeath(unittest.TestCase):
    def test_unconscious_melee_hit_auto_crits(self):
        pc = make_pc()
        enc, foe, _ = duel(pc, seed=8)
        pc.take_damage(pc.hp_cur)   # 打到 0 → 昏迷
        self.assertIn(COND_UNCONSCIOUS, pc.conditions)
        foe.attacks = [{"weapon": "scimitar", "bonus": 99, "extra": "+0"}]
        r = enc._monster_attack(foe, pc, "scimitar", random.Random(2))
        self.assertTrue(r.get("crit"), "对昏迷目标的近战命中必须暴击")

    def test_stable_does_not_roll_death_saves(self):
        pc = make_pc()
        pc.hp_cur = 0
        pc.conditions.add(COND_UNCONSCIOUS)
        pc.conditions.add(COND_STABLE)
        self.assertEqual(pc.death_save_turn(random.Random(1)), {})

    def test_damage_while_down_removes_stable(self):
        pc = make_pc()
        pc.hp_cur = 0
        pc.conditions |= {COND_UNCONSCIOUS, COND_STABLE}
        enc, foe, _ = duel(pc, seed=9)
        enc._post_hit_effects(foe, pc, False, 3, {"damage": 3}, random.Random(1))
        self.assertNotIn(COND_STABLE, pc.conditions, "受创后失去稳定")

    def test_massive_damage_beats_relentless(self):
        """大伤害即死优先于不屈坚韧（"not killed outright" 限制）。"""
        pc = make_pc("半兽人", race="half_orc")
        self.assertTrue(pc.mech().get("relentless_endurance"))
        info = pc.take_damage(pc.hp_max + 25)
        self.assertTrue(info.get("massive_death"), "溢出≥生命上限应立即死亡")
        self.assertFalse(info.get("relentless"), "不屈坚韧救不了被当场杀死者")
        self.assertIn("dead", pc.conditions)

    def test_damage_at_zero_hp_instant_death(self):
        """0HP 时再受 ≥ 生命上限的伤害 → 立即死亡（PHB Instant Death）。"""
        pc = make_pc()
        pc.hp_cur = 0
        pc.conditions.add(COND_UNCONSCIOUS)
        info = pc.take_damage(pc.hp_max + 2)
        self.assertTrue(info.get("massive_death"))
        self.assertIn("dead", pc.conditions)


class TestRestAndResources(unittest.TestCase):
    def test_long_rest_recovers_half_hit_dice_min_one(self):
        f1 = make_pc("新人", level=1)
        f1.hit_dice_used = 1
        f1.long_rest()
        self.assertEqual(f1.hit_dice_used, 0, "1级长休至少恢复1颗生命骰")
        f5 = make_pc("老兵", level=5)
        f5.hit_dice_used = 5
        f5.long_rest()
        self.assertEqual(f5.hit_dice_used, 3, "5级长休恢复一半（向下取整）=2颗")

    def test_bard_font_of_inspiration_short_rest(self):
        b5 = make_pc("诗人", klass="bard", level=5)
        b5.bardic_inspiration_used = b5.bardic_total()
        b5.short_rest(random.Random(1))
        self.assertEqual(b5.bardic_inspiration_used, 0, "5级泉涌激励短休恢复")
        b2 = make_pc("见习诗人", klass="bard", level=2)
        b2.bardic_inspiration_used = b2.bardic_total()
        b2.short_rest(random.Random(1))
        self.assertEqual(b2.bardic_inspiration_used, b2.bardic_total(),
                         "5级前激励仅长休恢复")

    def test_bardic_die_tracks_granter_level(self):
        enc = Encounter("t", rng=random.Random(1))
        b1 = make_pc("新人诗人", klass="bard", level=1)
        b5 = make_pc("老诗人", klass="bard", level=5)
        ally = make_pc("战士")
        enc.add(b1, "party"); enc.add(b5, "party"); enc.add(ally, "party")
        enc.bardic_inspiration(b1, ally)
        self.assertEqual(ally.bardic_die, 6)
        ally.bardic_dice = 0
        enc.bardic_inspiration(b5, ally)
        self.assertEqual(ally.bardic_die, 8, "5级诗人的激励骰是d8")

    def test_druid_natural_recovery_rounds_up(self):
        d3 = make_pc("德鲁伊", klass="druid", level=3)
        d3.slots_used = {1: 2, 2: 1}
        d3.short_rest(random.Random(1))
        # 等级3：预算 (3+1)//2 = 2 → 恰好恢复一个2环，1环预算不足保持已用
        self.assertEqual(d3.slots_used.get(2, 0), 0, "2环槽位应被自然回复")
        self.assertEqual(d3.slots_used.get(1, 0), 2, "预算不足以再恢复1环")


class TestConditionsAndSaves(unittest.TestCase):
    def test_paralyzed_auto_fails_str_dex_saves(self):
        pc = make_pc()
        pc.conditions.add(COND_PARALYZED)
        for abil in ("str", "dex"):
            r = pc.save(random.Random(1), abil, 5)
            self.assertFalse(r["success"], f"麻痹者{abil}豁免自动失败")
        r = pc.save(random.Random(1), "con", 5)
        self.assertTrue("自动失败" not in r["detail"], "体质豁免照常掷骰")

    def test_bless_not_added_to_ability_checks(self):
        pc = make_pc()
        pc.conditions.add("blessed")
        pc.blessing_d4 = 4
        r = pc.check(random.Random(3), "athletics", 10)
        self.assertNotIn("祝福", r["detail"], "祝福术不加属性检定")

    def test_bless_added_to_saves(self):
        pc = make_pc()
        pc.conditions.add("blessed")
        pc.blessing_d4 = 4
        results = {pc.save(random.Random(i), "wis", 25)["detail"] for i in range(30)}
        self.assertTrue(any("祝福" in d for d in results), "祝福术加豁免")

    def test_monster_save_proficiency(self):
        duergar = Monster("duergar", random.Random(1))
        self.assertEqual(duergar.save_bonus("con"), 4, "灰矮人体质豁免+4（含熟练）")
        self.assertEqual(duergar.save_bonus("str"), 2, "未熟练豁免只有属性调整值")

    def test_hold_person_affects_undead_humanoids(self):
        """骷髅/丧尸类型为 humanoid，定身人类有效。"""
        enc = Encounter("t", rng=random.Random(1))
        sk = Monster("skeleton", random.Random(1))
        self.assertTrue(sk.is_humanoid())


class TestAttacksAndActions(unittest.TestCase):
    def test_opportunity_attack_is_single_attack(self):
        """借机攻击只打一次，不触发多重攻击。"""
        pc = make_pc()
        enc, foe, _ = duel(pc, seed=10)
        enc.begin_turn(pc)
        enc.begin_turn(foe)
        foe.attacks = [{"weapon": "warhammer", "bonus": 99, "extra": "+0",
                        "multiattack": 2}]
        enc._register_engagement(foe, pc, ranged=False)
        r = enc.opportunity_attack(pc, foe)
        self.assertTrue(r["hit"])
        detail = r["detail"]
        self.assertEqual(detail.count("命中"), 1, "借机攻击应只有一次攻击")

    def test_follow_attack_chain(self):
        """组合攻击（矛→噬咬）：一次动作两段攻击。"""
        pc = make_pc()
        enc, _, _ = duel(pc, seed=11)
        gnoll = Monster("gnoll", random.Random(2))
        gnoll.attacks = [{"weapon": "spear", "bonus": 99, "extra": "+0",
                          "follow": "bite"},
                         {"weapon": "bite", "bonus": 99, "dmg": "1d4", "extra": "+0"}]
        enc.add(gnoll, "enemy")
        r = enc._monster_attack(gnoll, pc, "spear", random.Random(3))
        self.assertIn("噬咬", r["detail"], "follow 攻击应接续噬咬")

    def test_potion_feed_consumes_inventory(self):
        pc = make_pc()
        ally = make_pc("伤员")
        ally.hp_cur = 0
        ally.conditions.add(COND_UNCONSCIOUS)
        enc, _, _ = duel(pc, seed=12)
        enc.rng = random.Random(1)
        pc.inventory["healing_potion"] = 2
        r = enc.use_item(pc, "healing_potion", ally)
        self.assertTrue(r["ok"])
        self.assertEqual(pc.inventory["healing_potion"], 1, "喂药应消耗药水")

    def test_grapple_blocks_and_escape(self):
        pc = make_pc()
        enc, foe, _ = duel(pc, seed=13)
        enc.rng = ScriptRng([20, 1])   # 怪物必胜的擒抱对抗
        enc.do_grapple(foe, pc)
        self.assertIn(COND_GRAPPLED, pc.conditions)
        r = enc.move_away(pc)
        self.assertFalse(r["ok"], "被擒抱者不能移动")
        # 必定成功的挣脱：对抗骰 [20] vs [1]
        enc.rng = ScriptRng([20, 1, 20, 1])
        r2 = enc.do_escape_grapple(pc)
        self.assertTrue(r2["ok"])
        self.assertNotIn(COND_GRAPPLED, pc.conditions)

    def test_divine_smite_vs_fiend_and_crit(self):
        p2 = make_pc("圣武", klass="paladin", level=2)
        enc, foe, _ = duel(p2, seed=14)
        imp = Monster("imp", random.Random(1))
        r = enc.divine_smite(p2, imp, 1, random.Random(1), crit=True)
        self.assertTrue(r["ok"])
        self.assertIn("6d8", r["detail"], "1环对邪类暴击：(2+1)×2=6d8")


if __name__ == "__main__":
    unittest.main()
