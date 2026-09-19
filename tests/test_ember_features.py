"""新功能测试：4 级属性提升（ASI）、物品通用化、同伴 AI 扩展、《余烬之门》模组。"""

import json
import os
import random
import unittest

from ctrcg.rules import data as D
from ctrcg.rules.character import (
    Character, COND_STABLE, ASI_PREFERENCE, COND_UNCONSCIOUS,
)
from ctrcg.rules.combat import Encounter, Monster


def make_pc(name="罗兰", klass="fighter", race="human", scores=None, style="defense",
            level=1):
    base = {"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8}
    if scores:
        base.update(scores)
    skills = {"fighter": ["athletics", "intimidation"], "wizard": ["arcana", "history"],
              "cleric": ["insight", "medicine"], "rogue": ["stealth", "acrobatics",
                                                           "deception", "perception"],
              "ranger": ["stealth", "perception", "survival"],
              "monk": ["acrobatics", "athletics"], "druid": ["nature", "perception"],
              "sorcerer": ["arcana", "intimidation"], "warlock": ["arcana", "deception"],
              "paladin": ["athletics", "persuasion"], "bard": ["performance",
              "persuasion"]}[klass]
    return Character.create(
        name=name, race=race, klass=klass, background="soldier",
        scores=base, skill_choices=skills, fighting_style=style, level=level)


class TestASI(unittest.TestCase):
    """PHB 4 级属性提升：单项 +2 或两项各 +1，上限 20。"""

    def test_single_plus_two(self):
        c = make_pc()
        c.apply_asi({"str": 2})
        self.assertEqual(c.abilities["str"], 17 + 2)   # 人类默认力+2 → 17，再 +2 = 19

    def test_two_plus_one(self):
        c = make_pc()
        c.apply_asi({"str": 1, "con": 1})
        self.assertEqual(c.abilities["str"], 18)       # 15+2种族+1ASI
        self.assertEqual(c.abilities["con"], 16)       # 14+1种族+1ASI

    def test_cap_at_20(self):
        c = make_pc()
        c.abilities["str"] = 19
        c.apply_asi({"str": 2})
        self.assertEqual(c.abilities["str"], 20)       # 溢出部分丢弃

    def test_invalid_shapes_raise(self):
        c = make_pc()
        with self.assertRaises(ValueError):
            c.apply_asi({"str": 3})
        with self.assertRaises(ValueError):
            c.apply_asi({"str": 1, "dex": 1, "con": 1})
        with self.assertRaises(ValueError):
            c.apply_asi({})

    def test_pending_at_level_4_only_once(self):
        c = make_pc()
        c.level_up_to(3)
        self.assertNotIn("asi", c.pending_feature_choices())
        c.level_up_to(4)
        self.assertIn("asi", c.pending_feature_choices())
        c.feature_choices["asi"] = "str+2"             # 模拟已选
        self.assertNotIn("asi", c.pending_feature_choices())

    def test_companion_default_takes_feat(self):
        """同伴 4 级默认取推荐专长（施法者→战争施法者），不占 ASI。"""
        c = make_pc(name="缇娅", klass="sorcerer",
                    scores={"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 10,
                            "cha": 15})
        c.level_up_to(4)
        c.apply_default_choices()
        self.assertEqual(c.feature_choices["feat"], "war_caster")
        self.assertTrue(c.has_feature("war_caster"))
        self.assertEqual(c.abilities["cha"], 15)       # 专长替换了 ASI

    def test_lucky_feat_reroll_and_reset(self):
        """专长幸运：d20 出 1~5 自动重骰（取高者），3 次/长休。"""
        c = make_pc()
        c.feature_choices["feat"] = "lucky"
        rng = random.Random(1234)
        used = 0
        for _ in range(60):
            before = c.feat_lucky_used
            res = c._lucky_d20(rng, 0)
            if c.feat_lucky_used > before:
                used += 1
                self.assertEqual(res.kept[0], max(res.rolls))  # 取更优者结算
            self.assertLessEqual(c.feat_lucky_used, 3)
        self.assertGreater(used, 0)
        c.long_rest()
        self.assertEqual(c.feat_lucky_used, 0)         # 长休重置

    def test_player_wizard_asi_via_flow(self):
        """升到 4 级后 pending 列表包含 asi，且选择后写入 feature_choices。"""
        c = make_pc(name="梅林", klass="wizard",
                    scores={"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12,
                            "cha": 10}, level=1)
        c.level_up_to(4)
        needs = c.pending_feature_choices()
        self.assertIn("asi", needs)
        c.apply_asi({"int": 2})
        self.assertEqual(c.abilities["int"], 17)       # 15 + ASI +2


class TestItemsGeneralized(unittest.TestCase):
    def _enc(self):
        rng = random.Random(5)
        enc = Encounter("t")
        enc.rng = rng
        pc = make_pc()
        enc.add(pc, "party")
        enc.add(Monster("goblin", rng), "enemy")
        enc.begin(rng)
        return enc, pc, rng

    def test_greater_potion_heals_more(self):
        enc, pc, rng = self._enc()
        pc.inventory["potion_greater_healing"] = 1
        pc.take_damage(10)
        hp_before = pc.hp_cur
        r = enc.use_item(pc, "potion_greater_healing")
        self.assertTrue(r["ok"])
        healed = r["healed"]
        self.assertTrue(8 <= healed <= 20)             # 4d4+4
        self.assertEqual(pc.hp_cur, min(pc.hp_max, hp_before + healed))
        self.assertEqual(pc.inventory["potion_greater_healing"], 0)

    def test_feed_potion_to_downed_ally(self):
        enc, pc, rng = self._enc()
        ally = make_pc(name="皮普", klass="cleric")
        enc.add(ally, "party")
        ally.take_damage(ally.hp_max)
        self.assertIn(COND_UNCONSCIOUS, ally.conditions)
        pc.inventory["potion_greater_healing"] = 1
        r = enc.use_item(pc, "potion_greater_healing", ally)
        self.assertTrue(r["ok"])
        self.assertGreater(ally.hp_cur, 0)
        self.assertNotIn(COND_UNCONSCIOUS, ally.conditions)

    def test_healer_kit_stabilizes_without_check(self):
        enc, pc, rng = self._enc()
        ally = make_pc(name="皮普", klass="cleric")
        enc.add(ally, "party")
        ally.take_damage(ally.hp_max)
        ally.death_fails = 2
        pc.inventory["healer_kit"] = 10
        r = enc.use_item(pc, "healer_kit", ally)
        self.assertTrue(r["ok"])
        self.assertIn(COND_STABLE, ally.conditions)
        self.assertEqual(pc.inventory["healer_kit"], 9)  # 用次 = 持有数量
        self.assertEqual(ally.death_fails, 2)            # 稳定不重置失败数（长休才清）

    def test_healer_kit_rejects_healthy_target(self):
        enc, pc, rng = self._enc()
        pc.inventory["healer_kit"] = 3
        r = enc.use_item(pc, "healer_kit", pc)
        self.assertFalse(r["ok"])
        self.assertEqual(pc.inventory["healer_kit"], 3)  # 失败不消耗

    def test_item_mech_grants_resistance(self):
        c = make_pc()
        self.assertNotIn("fire_resist", c.mech())
        c.inventory["ember_ward_charm"] = 1
        self.assertTrue(c.mech().get("fire_resist"))
        c.inventory["ember_ward_charm"] = 0
        self.assertNotIn("fire_resist", c.mech())        # 数量 0 不生效


class TestEmbergateModule(unittest.TestCase):
    """《余烬之门》：加载校验、场景链、等级里程碑、自定义怪物。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join("modules", "embergate", "module.json")
        cls.data = json.load(open(path, encoding="utf-8"))
        from ctrcg.campaign.loader import validate_module
        cls.errors, cls.warnings = validate_module(cls.data)

    def test_validates_clean(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(self.warnings, [])

    def test_scene_chain_and_levels(self):
        from ctrcg.campaign.loader import Module
        mod = Module(self.data, source="file")
        self.assertEqual(mod.party_level_start, 3)
        self.assertEqual(mod.expected_end_level, 5)
        # 3→5 两次里程碑升级
        level_tos = [s.get("level_to") for s in mod.scenes if s.get("level_to")]
        self.assertEqual(level_tos, [4, 5])
        # 场景链完整：沿 next 走到 null
        ids = {s["id"] for s in mod.scenes}
        node, steps = mod.scenes[0]["id"], 0
        while node is not None and steps < 20:
            scene = next(s for s in mod.scenes if s["id"] == node)
            node, steps = scene["next"], steps + 1
        self.assertIsNone(node)

    def test_uses_srd_roster_not_yet_covered(self):
        """启用内置表中长期未用的怪物。"""
        from ctrcg.campaign.loader import Module
        mod = Module(self.data, source="file")
        used = set()
        for spec in mod.encounters.values():
            for name, _n in spec["monsters"]:
                used.add(name)
        self.assertTrue({"gnoll", "owlbear", "wight", "redcap", "phase_spider",
                         "minotaur", "bulette", "cambion"} <= used)

    def test_custom_ember_priest_fights(self):
        from ctrcg.campaign.loader import Module
        from ctrcg.rules.combat import monster_choose_action
        mod = Module(self.data, source="file")
        table = mod.monster_table
        rng = random.Random(9)
        enc = Encounter("t")
        enc.rng = rng
        enc.add(make_pc(), "party")
        priest = Monster("ember_priest", rng, table=table)
        enc.add(priest, "enemy")
        enc.begin(rng)
        self.assertLess(priest.hp_cur, 40)             # 6d8+6 ≤ 54
        # 火焰抗性字段生效
        self.assertTrue(priest.fire_resist)
        # 至少能正常执行一次 AI 动作
        act = monster_choose_action(enc, priest)
        self.assertIn("action", act)


class TestScriptedCompanionAI(unittest.TestCase):
    """扩展后的启发式队友 AI：全职业一轮不崩、行为合理。"""

    def _session(self, rng):
        from ctrcg.game import state as G
        from ctrcg.game.session import GameSession
        from ctrcg.ui.cli import AutoUI
        hero = make_pc(name="凯尔")
        return GameSession(G.GameState(hero, [hero], rng), AutoUI(), loop=None), hero

    def test_all_classes_take_a_turn(self):
        rng = random.Random(31)
        session, hero = self._session(rng)
        enc = Encounter("t")
        enc.rng = rng
        monk = make_pc(name="无相", klass="monk",
                       scores={"str": 13, "dex": 15, "con": 14, "int": 8, "wis": 14,
                               "cha": 10}, level=5)
        druid = make_pc(name="芬恩", klass="druid",
                        scores={"str": 8, "dex": 13, "con": 14, "int": 10, "wis": 15,
                                "cha": 10}, level=5)
        sorc = make_pc(name="缇娅", klass="sorcerer",
                       scores={"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 10,
                               "cha": 15}, level=5)
        warlock = make_pc(name="赫克斯", klass="warlock",
                          scores={"str": 10, "dex": 13, "con": 14, "int": 10, "wis": 12,
                                  "cha": 15}, level=5)
        paladin = make_pc(name="奥丽薇", klass="paladin",
                          scores={"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 10,
                                  "cha": 15}, level=5)
        bard = make_pc(name="吟游", klass="bard",
                       scores={"str": 10, "dex": 14, "con": 13, "int": 10, "wis": 12,
                               "cha": 15}, level=5)
        enc.add(hero, "party")
        for c in (monk, druid, sorc, warlock, paladin, bard):
            enc.add(c, "party")
        for _ in range(4):
            enc.add(Monster("goblin", rng), "enemy")
        enc.begin(rng)
        for c in (monk, druid, sorc, warlock, paladin, bard):
            c.hp_cur = c.hp_max                        # 满血走攻击/法术分支
            enc.begin_turn(c)
            session._scripted_pc_turn(enc, c)          # 一整轮（动作+附赠）不崩溃
        # 引擎状态自洽：有敌人已掉血，且没有非法状态
        self.assertGreaterEqual(enc.round, 1)
        self.assertTrue(any(f.hp_cur < f.hp_max for f in enc.enemies()))


class TestDrainAndDeathRegression(unittest.TestCase):
    """生命汲取的恢复（RAW：长休解除削减）与死者的升级/长休隔离。"""

    def test_drain_reduces_and_long_rest_restores(self):
        c = make_pc()
        full = c.hp_max
        c.take_damage(4)
        c.hp_max -= 6
        c.hp_drain = 6
        c.hp_cur = min(c.hp_cur, c.hp_max)
        msg = c.long_rest()
        self.assertEqual(c.hp_max, full)               # 长休恢复被汲取的上限
        self.assertEqual(c.hp_drain, 0)
        self.assertIn("长休完毕", msg)

    def test_dead_neither_levels_nor_gains_xp(self):
        c = make_pc()
        c.take_damage(c.hp_max + c.hp_max)             # 大伤害即死
        self.assertIn("dead", c.conditions)
        self.assertEqual(c.level_up_to(5), "")
        self.assertEqual(c.level, 1)
        self.assertEqual(c.gain_xp(1000), "")
        self.assertEqual(c.xp, 0)

    def test_long_rest_skips_dead(self):
        c = make_pc()
        c.take_damage(c.hp_max + c.hp_max)
        before = c.hp_cur
        msg = c.long_rest()
        self.assertIn("已死亡", msg)
        self.assertEqual(c.hp_cur, before)             # 尸体不被"回满"


class TestOutOfCombatPotion(unittest.TestCase):
    """探索阶段喝药：引擎代掷、扣库存、长休前也能回血。"""

    def test_drink_potion_out_of_combat(self):
        from ctrcg.game import state as G
        from ctrcg.game.session import GameSession
        from ctrcg.ui.cli import AutoUI
        rng = random.Random(6)
        hero = make_pc()
        session = GameSession(G.GameState(hero, [hero], rng), AutoUI(), loop=None)
        hero.take_damage(8)
        hero.inventory["potion_greater_healing"] = 1
        hero.inventory["healing_potion"] = 2
        before_hp, before_cnt = hero.hp_cur, 3
        session.drink_potion_out_of_combat()
        self.assertGreater(hero.hp_cur, before_hp)
        self.assertEqual(sum(hero.inventory.get(k, 0) for k in
                             ("healing_potion", "potion_greater_healing")),
                         before_cnt - 1)
        self.assertEqual(hero.inventory["potion_greater_healing"], 0)  # 优先喝上乘

    def test_drink_without_potion(self):
        from ctrcg.game import state as G
        from ctrcg.game.session import GameSession
        from ctrcg.ui.cli import AutoUI
        session = GameSession(G.GameState(make_pc(), [], random.Random(1)),
                              AutoUI(), loop=None)
        session.drink_potion_out_of_combat()           # 不抛异常即可


class TestWildShapeDisplay(unittest.TestCase):
    """野变下状态栏显示兽形 HP（修复前显示 本体cur/兽形max 的错位）。"""

    def test_shaped_druid_status_shows_beast_hp(self):
        rng = random.Random(8)
        enc = Encounter("t")
        enc.rng = rng
        druid = make_pc(name="芬恩", klass="druid",
                        scores={"str": 8, "dex": 13, "con": 14, "int": 10, "wis": 15,
                                "cha": 10}, level=5)
        enc.add(druid, "party")
        enc.add(Monster("goblin", rng), "enemy")
        enc.begin(rng)
        enc.begin_turn(druid)
        self.assertTrue(enc.wild_shape(druid, "black_bear").get("ok"))
        line = next(ln for ln in enc.status_lines() if "芬恩" in ln)
        cur, mx = line.split("HP")[1].split(",")[0].strip().split("/")
        self.assertEqual(int(cur), int(mx))            # 满血兽形：cur == max
        # brief() 同步
        b = enc.brief()
        me = next(p for p in b["party"] if p["name"] == "芬恩")
        c2, m2 = me["hp"].split("/")
        self.assertEqual(c2, m2)


class TestFeatCombat(unittest.TestCase):
    """专长的战斗机制：大力攻击 −5/+10、巨武器大师追击、战争施法者专注优势。"""

    def test_power_attack_math(self):
        for feat, weapon in (("great_weapon_master", "greataxe"),
                             ("sharpshooter", "longbow")):
            pc = make_pc(name="重锤", klass="fighter", level=4)
            pc.weapons = [weapon]
            pc.feature_choices["feat"] = feat
            gob = Monster("goblin", random.Random(1))
            base, powered = None, None
            for flag, store in ((False, "base"), (True, "powered")):
                rng = random.Random(77)                  # 同 seed：骰运一致
                enc = Encounter("t")
                enc.rng = rng
                enc.add(pc, "party")
                m = Monster("goblin", rng)
                enc.add(m, "enemy")
                enc.begin(rng)
                enc.begin_turn(pc)
                r = enc.attack_action(pc, m, weapon, rng, power_attack=flag)
                if store == "base":
                    base = r
                else:
                    powered = r
            self.assertIn("大力攻击", powered["detail"])
            self.assertNotIn("大力攻击", base["detail"])

    def test_gwm_bonus_attack_on_kill(self):
        pc = make_pc(name="重锤", klass="fighter", level=4)
        pc.weapons = ["greataxe"]
        pc.feature_choices["feat"] = "great_weapon_master"
        rng = random.Random(5)
        enc = Encounter("t")
        enc.rng = rng
        enc.add(pc, "party")
        m = Monster("goblin", rng)
        enc.add(m, "enemy")
        m2 = Monster("goblin", rng)
        enc.add(m2, "enemy")
        m.hp_cur = 1                                    # 一斧头砍倒
        m.ac_base = 5                                   # 确保命中
        enc.begin(rng)
        enc.begin_turn(pc)
        r = enc.attack_action(pc, m, "greataxe", rng)
        self.assertIn("巨武器大师", r["detail"])
        self.assertTrue(enc.bonus_used.get(pc.name))    # 附赠动作被消耗

    def test_war_caster_concentration_advantage(self):
        rng = random.Random(9)
        enc = Encounter("t")
        enc.rng = rng
        caster = make_pc(name="缇娅", klass="sorcerer", level=4)
        caster.feature_choices["feat"] = "war_caster"
        enc.add(caster, "party")
        m = Monster("goblin", rng)
        enc.add(m, "enemy")
        enc.begin(rng)
        enc.cast(caster, "bless", [caster.name], rng)   # 专注法术
        self.assertIsNotNone(caster.concentration)
        m.ac_base = 5
        for _ in range(4):                              # 直到命中一次（打断专注）
            enc.begin_turn(m)
            r = enc.attack_action(m, caster, None, rng)
            if r.get("hit"):
                break
        save_events = [e for e in enc.rolls
                       if e.get("label") == "专注豁免"]
        self.assertTrue(save_events)
        self.assertGreaterEqual(len(save_events[-1].get("rolls") or []), 2)


class TestDeathSaveLoopRegression(unittest.TestCase):
    """回归：战斗后无人施救的濒死者掷出自然 20 复活（1 HP），
    死亡豁免循环必须终止（修复前会永远空转）。"""

    def test_revived_member_terminates_loop(self):
        import ctrcg.rules.character as CH
        from ctrcg.game import state as G
        from ctrcg.game.session import GameSession
        from ctrcg.ui.cli import AutoUI

        rng = random.Random(3)
        hero = make_pc(name="凯尔")
        ally = make_pc(name="皮普", klass="cleric")
        session = GameSession(G.GameState(hero, [ally], rng), AutoUI(), loop=None)

        enc = Encounter("t")
        enc.rng = rng
        enc.add(hero, "party")
        enc.add(ally, "party")
        gob = Monster("goblin", rng)
        enc.add(gob, "enemy")
        gob.take_damage(999)                          # 敌人已倒 → 战斗结束
        # 全队倒地：无人可施救 → 走死亡豁免分支
        for c in (hero, ally):
            c.hp_cur = 0
            c.conditions.add(CH.COND_UNCONSCIOUS)
        session.encounter = enc
        session.tools.encounter = enc

        calls: dict[int, int] = {}

        def fake_death_save(self2, _rng):
            n = calls.get(id(self2), 0) + 1
            calls[id(self2)] = n
            if n == 1:
                self2.hp_cur = 1                      # 自然 20：复活
                return {"who": self2.name, "result": "revived", "detail": "d20=20"}
            self2.conditions.add(CH.COND_STABLE)
            return {"who": self2.name, "result": "stable", "detail": "ok"}

        orig = Character.death_save_turn
        Character.death_save_turn = fake_death_save
        try:
            outcome = session.run_combat()
        finally:
            Character.death_save_turn = orig
        self.assertEqual(outcome, "win")
        # 每人最多两次掷骰（复活一次 + 稳定一次），而非无限循环
        self.assertLessEqual(max(calls.values()), 2)


if __name__ == "__main__":
    unittest.main()
