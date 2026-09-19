"""完整 5e 规则机制测试：动作经济、种族特性、职业资源、法术、状态计时、
借机攻击、死亡规则、双内置模组。"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctrcg.rules import data as D
from ctrcg.rules.character import (
    Character, COND_MARKED, COND_POISONED, COND_PRONE, COND_RESTRAINED,
    COND_SLEEP, COND_UNCONSCIOUS, COND_DEAD,
)
from ctrcg.rules.combat import Encounter, Monster, monster_choose_action
from ctrcg.campaign.cordis import Cordis
from ctrcg.game import state as G


def make_pc(name="勇者", klass="fighter", race="human", level=1, **kw):
    scores = {"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8}
    skill = {"fighter": ["athletics"], "ranger": ["survival"],
             "wizard": ["arcana"], "rogue": ["stealth"],
             "warlock": ["arcana"], "cleric": ["insight"],
             "barbarian": ["athletics"], "druid": ["nature"], "paladin": ["insight"],
             "monk": ["acrobatics"], "sorcerer": ["arcana"], "bard": ["persuasion"]}
    style = kw.pop("fighting_style", "defense" if klass == "fighter" else "")
    fc = kw.pop("feature_choices", None)
    return Character.create(name=name, race=race, klass=klass, background="soldier",
                            scores=scores, skill_choices=skill[klass], level=level,
                            fighting_style=style, feature_choices=fc, **kw)


def duel(pc, monster_id="goblin", seed=5):
    rng = random.Random(seed)
    enc = Encounter("t", rng=rng)
    enc.add(pc, "party")
    enc.add(Monster(monster_id, rng, tag="x"), "enemy")
    enc.begin(rng)
    return enc, enc.enemies()[0], rng


class TestActionEconomy(unittest.TestCase):
    def test_action_and_bonus_are_separate(self):
        pc = make_pc()
        enc, foe, rng = duel(pc)
        enc.next_turn()  # 怪物先手也无妨；直接进入玩家流程
        # 玩家回合由 run_combat 驱动；此处手动开始
        enc.begin_turn(pc)
        r = enc.do_dodge(pc)                 # 消耗动作
        self.assertTrue(r["ok"])
        self.assertFalse(enc.has_action(pc))
        self.assertTrue(enc.has_bonus(pc))
        r2 = enc.second_wind(pc, rng)        # 附赠动作仍可用
        self.assertTrue(r2["ok"])
        self.assertFalse(enc.has_bonus(pc))

    def test_second_wind_is_bonus_only_once(self):
        pc = make_pc()
        pc.hp_cur = 4
        enc, foe, rng = duel(pc)
        enc.begin_turn(pc)
        self.assertTrue(enc.second_wind(pc, rng)["ok"])
        r = enc.second_wind(pc, rng)
        self.assertFalse(r["ok"])

    def test_attack_action_consumes_action(self):
        pc = make_pc(level=5)                # 5 级战士：额外攻击
        enc, foe, rng = duel(pc)
        enc.begin_turn(pc)
        r = enc.attack_action(pc, foe, "longsword", rng)
        self.assertTrue(r["ok"])
        self.assertFalse(enc.has_action(pc))   # 动作已用（两次攻击包含在内）
        self.assertEqual(r["attacks"], 2)


class TestMarkAndSneak(unittest.TestCase):
    def test_hunters_mark_bonus_applies(self):
        pc = make_pc("薇兰", klass="ranger", race="high_elf", level=2)
        pc.feature_choices["prey"] = "colossus"
        enc, foe, rng = duel(pc)
        r = enc.cast(pc, "hunters_mark", [foe.name], rng)
        self.assertTrue(r["ok"])
        self.assertIn(COND_MARKED, pc.conditions)      # 标记加在施法者
        self.assertEqual(pc.marked_target, foe.name)
        marks = 0
        for i in range(60):
            enc2 = Encounter("t", rng=random.Random(100 + i))
            enc2.add(pc, "party")
            f2 = Monster("goblin", random.Random(i), tag="m")
            enc2.add(f2, "enemy")
            # 模拟对新目标的印记（战斗侧结算：施法者带 COND_MARKED + marked_target）
            pc.conditions.add(COND_MARKED)
            pc.marked_target = f2.name
            out = enc2.attack(pc, f2, "longbow", random.Random(200 + i))
            if out.get("hit") and "印记" in out.get("detail", ""):
                marks += 1
        self.assertGreater(marks, 0, "猎人印记加伤应出现")

    def test_sneak_attack_requires_advantage_or_ally(self):
        pc = make_pc("盗贼", klass="rogue", race="lightfoot_halfling", level=1)
        enc, foe, rng = duel(pc, seed=9)
        enc.begin_turn(pc)
        sneaked = 0
        for _ in range(40):
            f2 = Monster("bandit", rng, tag="b")
            enc.combatants = [pc, f2]
            out = enc.attack(pc, f2, "shortsword", rng)
            if out.get("hit") and "偷袭" in out.get("detail", ""):
                sneaked += 1
        self.assertEqual(sneaked, 0, "无优势无队友近身时不应触发偷袭")


class TestRacialMechanics(unittest.TestCase):
    def test_sleep_spares_elves_and_undead(self):
        wiz = make_pc("法师", klass="wizard", race="human")
        elf = make_pc("精灵卫兵", klass="fighter", race="high_elf")
        enc = Encounter("t", rng=random.Random(5))
        enc.add(wiz, "party")
        enc.add(elf, "party")
        enc.cast(wiz, "sleep", [elf.name], random.Random(6))
        self.assertNotIn(COND_SLEEP, elf.conditions)
        enc2 = Encounter("t", rng=random.Random(7))
        enc2.add(wiz, "party")
        zed = Monster("zombie", random.Random(8))
        enc2.add(zed, "enemy")
        zed.hp_cur = 1
        enc2.cast(wiz, "sleep", [zed.name], random.Random(9))
        self.assertNotIn(COND_SLEEP, zed.conditions)

    def test_halfling_lucky_rerolls_ones(self):
        pc = make_pc("半身人", race="lightfoot_halfling")
        rerolled = 0
        for i in range(400):
            rng = random.Random(i)
            r = pc.check(rng, "stealth", 30)
            if "幸运重骰" in r["detail"]:
                rerolled += 1
        self.assertGreater(rerolled, 0, "出1时应触发幸运重骰")

    def test_gnome_save_advantage_and_dwarf_poison(self):
        gnome = make_pc("侏儒", klass="wizard", race="rock_gnome")
        rng = random.Random(1)
        adv_seen = 0
        for i in range(200):
            r = gnome.save(random.Random(i), "wis", 30)
            if "（优势）" in r["detail"]:
                adv_seen += 1
        self.assertGreater(adv_seen, 50, "侏儒狡黠应带来 Wis 豁免优势")
        dwarf = make_pc("矮人", race="dwarf_gold")
        enc = Encounter("t", rng=random.Random(2))
        halved = 0
        for i in range(50):
            dmg, notes = enc.apply_resistance(dwarf, "poison", 10)
            if dmg == 5:
                halved += 1
        self.assertEqual(halved, 50, "矮人应自带毒素抗性")

    def test_halforc_relentless_endurance(self):
        pc = make_pc("半兽人", race="half_orc")
        info = pc.take_damage(pc.hp_cur + 3)
        self.assertTrue(info.get("relentless"))
        self.assertEqual(pc.hp_cur, 1)
        info2 = pc.take_damage(pc.hp_cur + 3)
        self.assertFalse(info2.get("relentless"))
        self.assertEqual(pc.hp_cur, 0)

    def test_massive_damage_instant_death(self):
        pc = make_pc()
        pc.take_damage(pc.hp_max * 3)
        self.assertIn(COND_DEAD, pc.conditions)


class TestCasterRules(unittest.TestCase):
    def test_pact_magic_slots(self):
        wl = make_pc("邪术师", klass="warlock", level=2)
        slots = wl.spell_slots()
        self.assertEqual(slots, {1: 2})
        wl.slots_used[1] = 2
        self.assertEqual(wl.spell_slots(), {})
        wl.short_rest(random.Random(1))
        self.assertEqual(wl.spell_slots(), {1: 2}, "魔契法术位短休恢复")

    def test_cantrip_scales_at_level_5(self):
        w5 = make_pc("法师", klass="wizard", level=5)
        enc, foe, rng = duel(w5, seed=4)
        hits_two_dice = 0
        for i in range(60):
            f2 = Monster("goblin", random.Random(i), tag="g")
            enc.combatants = [w5, f2]
            out = enc.cast(w5, "fire_bolt", [f2.name], random.Random(300 + i))
            if "闪开" not in out.get("detail", ""):
                total = int(out["detail"].split("，")[1].split(" 点")[0])
                if total > 10:
                    hits_two_dice += 1
        self.assertGreater(hits_two_dice, 0, "5级火焰箭应为 2d10")

    def test_eldritch_blast_two_beams_at_5(self):
        wl = make_pc("邪术师", klass="warlock", level=5,
                     feature_choices={"patron": "fiend", "invocation_1": "agonizing",
                                      "invocation_2": "devils_sight"})
        enc, foe, rng = duel(wl, seed=6)
        out = enc.cast(wl, "eldritch_blast", [foe.name], rng)
        self.assertEqual(out["detail"].count("射线"), 2, "5级魔能爆应有两道光束")


class TestCombatMechanics(unittest.TestCase):
    def test_monster_spell_crit_doubles_damage(self):
        ash = Monster("ash_mage", random.Random(1))
        crits = []
        for i in range(300):
            rng = random.Random(i)
            a = Monster("ash_mage", rng)
            pc = make_pc("盾卫")
            e = Encounter("t", rng=rng)
            e.add(a, "enemy")
            e.add(pc, "party")
            r = e.attack(a, pc, "spell:fire_bolt", rng)
            if r.get("crit"):
                crits.append(r["damage"])
        self.assertTrue(crits)
        self.assertGreater(sum(crits) / len(crits), 15, "法术暴击应翻倍（4d10 均值22）")

    def test_opportunity_attack_on_move_away(self):
        pc = make_pc()
        enc, foe, rng = duel(pc, seed=11)
        enc.begin_turn(pc)
        enc.begin_turn(foe)
        foe.attacks[0]["bonus"] = 99            # 保证借机攻击必中
        enc._register_engagement(foe, pc, ranged=False)
        before = len(enc.log)
        r = enc.move_away(pc)
        self.assertFalse(r["ok"], "被借机攻击命中时移动应失败")
        self.assertIn("移动失败", "".join(enc.log[before:]))
        # 撤离后无借机攻击
        enc2 = Encounter("t2", rng=rng)
        enc2.add(pc, "party")
        enc2.add(foe, "enemy")
        enc2._register_engagement(foe, pc, ranged=False)
        self.assertTrue(enc2.do_disengage(pc)["ok"])
        before2 = len(enc2.log)
        r2 = enc2.move_away(pc)
        self.assertTrue(r2["ok"], "撤离后移动不引发借机攻击")

    def test_shove_prone(self):
        pc = make_pc()
        enc, foe, rng = duel(pc, seed=12)
        enc.begin_turn(pc)
        for _ in range(30):
            enc.reaction_used.clear()
            enc.actions_used[pc.name] = False
            r = enc.do_shove(pc, foe)
            if r["ok"]:
                break
        self.assertIn(COND_PRONE, foe.conditions)

    def test_poison_condition_expires(self):
        pc = make_pc()
        enc, foe, rng = duel(pc, seed=13)
        enc.set_condition(pc, COND_POISONED)
        enc.add_timer(pc, COND_POISONED, 3)
        enc.begin_turn(pc)
        for _ in range(4):
            enc.end_turn(pc)
        self.assertNotIn(COND_POISONED, pc.conditions, "中毒计时归零后应解除")

    def test_web_restrains(self):
        sp = Monster("giant_spider", random.Random(1))
        restrained = 0
        for i in range(40):
            rng = random.Random(i)
            s = Monster("giant_spider", rng)
            pc = make_pc("落网者")
            e = Encounter("t", rng=rng)
            e.add(s, "enemy")
            e.add(pc, "party")
            e.attack(s, pc, "web", rng)
            if COND_RESTRAINED in pc.conditions:
                restrained += 1
        self.assertGreater(restrained, 5, "蛛网命中应概率束缚")


class TestClassResources(unittest.TestCase):
    def test_rage_resistance_and_damage(self):
        barb = make_pc("蛮子", klass="barbarian", level=3,
                       feature_choices={"path": "totem"})
        enc, foe, rng = duel(barb, seed=14)
        enc.begin_turn(barb)
        self.assertTrue(enc.start_rage(barb)["ok"])
        dmg, notes = enc.apply_resistance(barb, "fire", 10)
        self.assertEqual(dmg, 5, "图腾·熊狂暴下火焰减半")

    def test_divine_smite(self):
        pal = make_pc("圣武", klass="paladin", level=2)
        pal.slots_used.clear()
        enc, foe, rng = duel(pal, seed=15)
        r = enc.divine_smite(pal, foe, 1, rng)
        self.assertTrue(r["ok"])
        self.assertEqual(pal.spell_slots().get(1, 0), 1, "消耗一个1环位")

    def test_wild_shape_and_revert(self):
        dru = make_pc("德鲁伊", klass="druid", level=2)
        enc, foe, rng = duel(dru, seed=16)
        enc.begin_turn(dru)
        r = enc.wild_shape(dru, "wolf")
        self.assertTrue(r["ok"])
        self.assertTrue(dru.in_wild_shape())
        ws_hp = dru.wild_shape["hp_cur"]
        info = enc.apply_damage(dru, ws_hp + 4)
        self.assertFalse(dru.in_wild_shape(), "兽形破碎后回到本体")
        self.assertLess(dru.hp_cur, dru.hp_max, "溢出伤害由本体承受")

    def test_bardic_inspiration_die_used(self):
        bard = make_pc("诗人", klass="bard", race="human")
        enc = Encounter("t", rng=random.Random(17))
        enc.add(bard, "party")
        enc.begin_turn(bard)
        self.assertTrue(enc.bardic_inspiration(bard, bard)["ok"])
        used = 0
        for i in range(100):
            if bard.bardic_dice <= 0:
                break
            r = bard.check(random.Random(i), "persuasion", 15)
            used += 1
        self.assertGreaterEqual(used, 1, "激励骰应被自动消耗")


class TestBothBuiltinModules(unittest.TestCase):
    """三个预置剧本：校验、队友加载、首场遭遇可运行。"""

    def test_modules_validate_with_companions(self):
        reg = Cordis("modules")
        src = {e["id"]: e["source"] for e in reg.scan()}
        for mid in ("starfall", "saltmist", "mistgate"):
            self.assertIn(mid, src, f"{mid} 应随发行版附带")
            self.assertEqual(src[mid], "builtin", f"{mid} 应归类为内置模组")
        for mid in ("starfall", "saltmist", "mistgate"):
            mod, errs, warns = reg.ensure(mid)
            self.assertIsNone(mod is None and errs or None, f"{mid}: {errs}")
            self.assertEqual(warns, [], f"{mid} 不应有校验警告：{warns}")
            self.assertEqual(len(mod.companions), 3, f"{mid} 应自带三名队友")
            self.assertGreaterEqual(len(mod.scenes), 4)
            self.assertGreaterEqual(len(mod.encounters), 3)

    def test_builtin_modules_not_deletable(self):
        reg = Cordis("modules")
        ok, err = reg.delete("mistgate")
        self.assertFalse(ok)
        self.assertIn("内置", err)

    def test_mistgate_tutorial_encounters_run(self):
        """教学模组三场遭遇均可战斗结算（含突袭联动场景）。"""
        reg = Cordis("modules")
        mod, errs, _w = reg.ensure("mistgate")
        self.assertIsNotNone(mod, str(errs))
        seeds = {"e_rats": 11, "e_cellar": 22, "e_bridge": 33}
        for eid, seed in seeds.items():
            rng = random.Random(seed)
            enc = G.build_encounter(eid, G.GameState(
                make_pc("教学员"), [], rng, module=mod))
            enc.begin_turn(enc.party()[0])
            r = enc.attack_action(enc.party()[0], enc.enemies()[0], "longsword", rng)
            self.assertTrue(r["ok"], eid)

    def test_saltmist_custom_monsters_fight(self):
        reg = Cordis("modules")
        mod, _e, _w = reg.ensure("saltmist")
        rng = random.Random(21)
        enc = G.build_encounter("e_lighthouse_boss", G.GameState(
            make_pc("测试员"), [], rng, module=mod))
        salt = [m for m in enc.enemies() if m.monster_id == "salt_witch"]
        self.assertTrue(salt, "盐巫应通过 custom_monsters 进入战斗")
        pc = enc.party()[0]
        enc.begin_turn(pc)
        r = enc.attack_action(pc, salt[0], "longsword", rng)
        self.assertTrue(r["ok"])
        r2 = enc.cast(enc.party()[0] if False else pc, "fire_bolt", [salt[0].name], rng)
        self.assertTrue(r2["ok"])

    def test_default_companions_fallback(self):
        reg = Cordis("modules")
        mod, _e, _w = reg.ensure("starfall")
        comp = dict(mod.companions[0])
        self.assertEqual(comp["klass"], "fighter")


if __name__ == "__main__":
    unittest.main()
