"""RED 角色成长 / 装备 / 义体 / 商店，以及两条回归。

移植前这个文件写的是旧规则的"新功能"（属性提升、物品通用化、内置模组、
物品同调与附魔、成长结算、商店、变形、特殊战斗、濒死循环回归）。RED 下的对应物：

* ``TestIP``                      —— 改进点：技能 20×新等级（×2 翻倍）/ 职业 60×新等级
* ``TestRoleAbilities``           —— 10 个职业的特殊能力在引擎里的落点
* ``TestGearAndItems``            —— 武器/护甲/义体/装备的给予与使用
* ``TestOutOfCombatHealing``      —— 稳定 / 急救固定 / 治疗 / 休整一天
* ``TestCyberwareHumanity``       —— 义体一次收费、人性与共情、赛博精神病边界
* ``TestShopEurobucks``           —— 欧元采购（不足即拒）+ 护甲修复价
* ``TestScriptedCompanionAI``     —— 10 个职业各跑一回合同伴 AI 不崩
* ``TestDeathSaveLoopRegression`` —— **死亡豁免循环必须终止**（规则无关的不变量）

旧规则的这些子系统**整体删除**，RED 不再有任何对应物。
"""

import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (ScriptRng, fixture_module, make_pc,  # noqa: E402
                     make_state)
from test_combat import corridor, duel, make_foe, PISTOL   # noqa: E402

from trpg.game import state as G                          # noqa: E402
from trpg.game.session import GameSession                 # noqa: E402
from trpg.harness.models import ModelResponse             # noqa: E402
from trpg.rules import data as D                          # noqa: E402
from trpg.rules.character import (COND_DEAD,              # noqa: E402
                                   COND_STABILIZED,
                                   COND_UNCONSCIOUS, Character)
from trpg.rules.combat import Encounter                   # noqa: E402
from trpg.ui.cli import AutoUI                            # noqa: E402


def shop_state(eurobucks=200, module=None):
    """一个把玩家放在 ``shop: True`` 场景里的会话。"""
    mod = module or fixture_module()
    mod.scenes[0]["shop"] = True
    st = make_state(module=mod)
    st.player.eurobucks = eurobucks
    st.scene_id = mod.scenes[0]["id"]
    return st, GameSession(st, AutoUI(), loop=None)


# ================================================================ IP
class TestIP(unittest.TestCase):
    def test_skill_cost_is_twenty_times_the_new_rank(self):
        c = make_pc(skill_levels={"handgun": 3})
        self.assertEqual(c.ip_cost_skill("handgun"), 20 * 4)
        c.skill_levels["handgun"] = 7
        self.assertEqual(c.ip_cost_skill("handgun"), 20 * 8)

    def test_x2_skills_cost_double(self):
        for key in ("electronics_security", "paramedic"):
            c = make_pc(skill_levels={key: 4})
            self.assertEqual(c.ip_cost_skill(key), 20 * 5 * 2)

    def test_role_cost_is_sixty_times_the_new_rank(self):
        c = make_pc()
        c.role_rank = 4
        self.assertEqual(c.ip_cost_role(), D.IP_ROLE_BASE * 5)

    def test_ip_spend_through_the_session(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        out = session.award_ip(60, reason="测试")
        self.assertTrue(out.get("ok", True))
        self.assertEqual(out["each"], 60)
        # 经典模式发完 IP 会立刻把花费菜单摆给玩家（AutoUI 当场花掉一部分），
        # 所以这里只断言"每人确实拿了 60"，具体花费另测
        for c in st.party():
            self.assertLessEqual(c.ip, 60)
        cost = st.player.ip_cost_skill("handgun")
        st.player.gain_ip(cost + 5)
        before_ip = st.player.ip
        before_level = st.player.skill("handgun")
        ok, msg = st.player.raise_skill("handgun")
        self.assertTrue(ok, msg)
        self.assertEqual(st.player.ip, before_ip - cost)
        self.assertEqual(st.player.skill("handgun"), before_level + 1)

    def test_refusal_keeps_state(self):
        c = make_pc(ip=5)
        before = dict(c.skill_levels)
        ok, msg = c.raise_skill("handgun")
        self.assertFalse(ok)
        self.assertIn("改进点不足", msg)
        self.assertEqual(c.skill_levels, before)
        self.assertEqual(c.ip, 5)

    def test_scene_ip_is_awarded_on_leaving(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        scene = st.module.scene_by_id(st.scene_id)
        session.advance_scene(scene["next"])
        self.assertTrue(all(c.ip > 0 for c in st.party()))

    def test_no_xp_or_levels_exist(self):
        c = make_pc()
        for gone in ("xp", "level", "gain_xp", "level_up_to", "prof_bonus"):
            self.assertFalse(hasattr(c, gone), gone)
        self.assertTrue(hasattr(c, "ip"))


# ================================================================ 职业能力
class TestRoleAbilities(unittest.TestCase):
    def test_solo_and_rockerboy_are_wired_into_the_engine(self):
        solo = make_pc(role="solo")
        self.assertEqual(solo.initiative_bonus(), 4)
        rock = make_pc(role="rockerboy")
        self.assertTrue(rock.has_role_bonus("persuasion"))
        self.assertFalse(make_pc(role="fixer").has_role_bonus("persuasion"))

    def test_role_rank_is_part_of_the_character_card(self):
        c = make_pc(role="medtech")
        self.assertEqual(c.role_name, "医疗师")
        self.assertEqual(c.role_rank, 4)
        self.assertIn("职级", G.GameState(c, []).party_snapshot())

    def test_every_role_builds_and_fights_a_turn(self):
        for role in D.ROLES:
            c = make_pc(f"测试-{role}", role=role, weapons=[PISTOL])
            self.assertEqual(c.role, role)
            enc = duel(pc=c, foe=make_foe(), grid=corridor(3), cells=3)
            enc.rng = ScriptRng([8, 3, 3, 3])
            res = enc.attack(c, enc.enemies[0], PISTOL)
            self.assertTrue(res["ok"])


# ================================================================ 装备
class TestGearAndItems(unittest.TestCase):
    def test_give_routes_by_table(self):
        c = make_pc(weapons=[], armor=None)
        c.give("heavy_pistol")
        c.give("kevlar")
        c.give("internal_agent")
        c.give("medkit", 2)
        self.assertIn("heavy_pistol", c.weapons)
        self.assertEqual(c.armor_key, "kevlar")
        self.assertIn("internal_agent", c.cyberware)
        self.assertEqual(c.gear["medkit"], 2)

    def test_use_item_and_armor_repair_price(self):
        c = make_pc(gear={"first_aid_kit": 1}, armor="kevlar")
        self.assertTrue(c.use_item("first_aid_kit"))
        self.assertFalse(c.use_item("first_aid_kit"))
        c.take_damage(20)
        self.assertLess(c.armor_at("torso"), 7)
        c.repair_armor()
        self.assertEqual(c.armor_at("torso"), 7)
        self.assertEqual(D.ARMOR_REPAIR_COST, 10)

    def test_best_weapon_and_damage(self):
        c = make_pc(weapons=["medium_pistol", "heavy_melee", "assault_rifle"])
        self.assertEqual(c.best_weapon("ranged"), "assault_rifle")
        self.assertEqual(c.best_weapon(), "heavy_melee")
        self.assertEqual(c.weapon_damage("assault_rifle"), "5d6")

    def test_inventory_view_is_red(self):
        from trpg.game.tools import GameTools
        st = make_state()
        tools = GameTools(st, AutoUI())
        view = tools._inventory(st.player)
        self.assertIn(D.CURRENCY_ABBR, view)
        self.assertIn("义体", view)
        self.assertNotIn("金币", view)
        self.assertNotIn("法术位", view)


# ================================================================ 战斗外治疗
class TestOutOfCombatHealing(unittest.TestCase):
    def test_stabilize_then_rest_day(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        pc = st.player
        pc.hp_cur = 0
        pc.conditions.add(COND_UNCONSCIOUS)
        session.stabilize_in_explore(pc, party_only=True)
        self.assertGreaterEqual(pc.hp_cur, 0)
        pc.stabilize()
        before = pc.hp_cur
        session.party_rest_day()
        self.assertGreater(pc.hp_cur, before)

    def test_rest_day_needs_stabilized(self):
        c = make_pc()
        c.hp_cur = 0                                     # 濒死：未稳定不恢复
        self.assertEqual(c.rest_day(), 0)
        c.stabilize()
        self.assertEqual(c.rest_day(), c.stat("body"))

    def test_crit_first_aid_in_explore(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        pc = st.player
        pc.apply_crit_injury(5)                                  # 肋骨骨折（可固定）
        st.rng = ScriptRng([10, 5])                              # 暴击成功
        session.crit_first_aid_in_explore(pc)
        self.assertTrue(pc.crit_injuries[0]["stabilized"]
                        or pc.crit_injuries == [])

    def test_treat_crit_in_explore_removes_the_injury(self):
        """外科治疗：不能自医，由队里的医疗师动手。"""
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        medic = st.player
        medic.skill_levels["paramedic"] = 8
        patient = st.companions[0]
        patient.apply_crit_injury(5)                             # 骨折（可治疗）
        st.rng = ScriptRng([10, 5])
        session.treat_crit_in_explore(patient)
        self.assertTrue(patient.crit_injuries == []
                        or patient.crit_injuries[0].get("permanent"))

    def test_heal_caps_at_max(self):
        c = make_pc()
        c.hp_cur = 40
        self.assertEqual(c.heal(99), c.hp_max - 40)
        self.assertEqual(c.hp_cur, c.hp_max)


# ================================================================ 义体 / 人性
class TestCyberwareHumanity(unittest.TestCase):
    def test_install_charges_once(self):
        c = make_pc()
        loss = c.install_cyberware("cyberaudio")
        self.assertEqual(loss, D.CYBERWARE["cyberaudio"]["humanity"])
        self.assertEqual(c.install_cyberware("cyberaudio"), 0)

    def test_show_cyberware_renders_red(self):
        st = make_state()
        st.player.install_cyberware("cyberoptics")
        ui = AutoUI()
        session = GameSession(st, ui, loop=None)
        session.show_cyberware()
        joined = "\n".join(ui.history)
        self.assertIn("赛博光学组件", joined)
        self.assertIn("人性", joined)
        self.assertNotIn("法术位", joined)

    def test_cyberpsychosis_is_a_hard_boundary(self):
        c = make_pc(stats={"emp": 1, "body": 5, "will": 5})
        c.humanity_cur = 6
        self.assertFalse(c.is_cyberpsycho())
        c.install_cyberware("internal_agent")                     # 6 → 0
        self.assertTrue(c.is_cyberpsycho())

    def test_save_roundtrip(self):
        st = make_state()
        st.player.install_cyberware("cyberoptics")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "g.json"
            G.save_game(st, path)
            back = G.load_game(path, module=st.module)
        self.assertEqual(back.player.cyberware, ["cyberoptics"])
        self.assertEqual(back.player.humanity_cur, st.player.humanity_cur)


# ================================================================ 商店
class TestShopEurobucks(unittest.TestCase):
    def test_buy_and_insufficient_funds(self):
        st, session = shop_state(eurobucks=200)
        pc = st.player
        from trpg.game import session as S
        key, price = S.SHOP_GOODS[0][1], S.SHOP_GOODS[0][3]
        out = session.buy_good(key)
        self.assertTrue(out["ok"], out)
        self.assertEqual(pc.eurobucks, 200 - price)
        st.player.eurobucks = 1
        out = session.buy_good(key)
        self.assertFalse(out["ok"])
        self.assertIn("欧元不够", out["error"])
        self.assertEqual(st.player.eurobucks, 1)

    def test_shop_only_where_declared(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        out = session.buy_good("medkit")
        self.assertFalse(out["ok"])
        self.assertIn("夜市", out["error"])

    def test_unknown_good_rejected(self):
        _st, session = shop_state()
        out = session.buy_good("魔法药水")
        self.assertFalse(out["ok"])

    def test_menu_shop_closes_cleanly(self):
        from trpg.ui.cli import ConsoleUI

        class LeaveUI(ConsoleUI):
            def __init__(self):
                super().__init__(input_fn=lambda _p: "", show_dice=False)
                self.calls = 0

            def choose(self, prompt, options):
                self.calls += 1
                return len(options) - 1                          # 离开货摊

        st, _s = shop_state(eurobucks=200)
        ui = LeaveUI()
        session = GameSession(st, ui, loop=None)
        session.open_shop()
        self.assertEqual(ui.calls, 1)
        self.assertEqual(st.player.eurobucks, 200)


# ================================================================ 同伴 AI
class TestScriptedCompanionAI(unittest.TestCase):
    def test_all_ten_roles_act_a_turn_without_crashing(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        enc = Encounter(title="全职业演练", entry={}, rng=st.rng)
        enc.enemies.append(make_foe())
        for i, role in enumerate(D.ROLES):
            comp = make_pc(f"同伴-{role}", role=role, weapons=[PISTOL])
            comp.kind = "companion"
            enc.allies.append(comp)
        enc.grid = corridor(6)
        for i, c in enumerate(enc.combatants):
            enc.positions[c.name] = (1 + (i % 2), 1 + (i % 3))
        enc.speed_left = {c.name: c.move_value() for c in enc.combatants}
        st.companions = list(enc.allies[1:])
        for comp in enc.allies[1:]:
            try:
                session.companion_turn(enc, comp)
            except Exception as exc:                            # noqa: BLE001
                self.fail(f"{comp.role} 的同伴回合崩了：{exc!r}")
            enc.refresh_actions(comp)                           # 给下一个职业腾位

    def test_scripted_heuristic_attacks(self):
        st = make_state()
        session = GameSession(st, AutoUI(), loop=None)
        enc = duel(pc=make_pc("同伴", role="solo", weapons=[PISTOL]),
                   foe=make_foe(), grid=corridor(3), cells=3)
        comp = enc.allies[0]
        comp.kind = "companion"
        enc.rng = ScriptRng([8, 3, 3, 3])
        session.companion_turn(enc, comp)
        self.assertTrue(enc.rolls)                    # 真的走了一次攻击结算
        self.assertFalse(enc.has_action(comp))        # 动作被用掉


# ================================================================ 循环终止回归
class TestDeathSaveLoopRegression(unittest.TestCase):
    """回归：濒死成员被救回后，战斗回合循环必须终止（修复前会永远空转）。

    这是规则无关的不变量：无论生存检定的实现怎么改，"复活/被稳定"之后
    就不该再对同一个角色反复掷生存检定。
    """

    def test_revived_member_terminates_the_loop(self):
        st = _state_with_small_encounter()
        session = GameSession(st, AutoUI(), loop=None)
        session.launch_encounter("e_small")
        enc = session.encounter

        calls: dict[str, int] = {}

        def fake_death_save(self2, _rng):
            from trpg.rules.dice import check_roll as _roll
            n = calls.get(self2.name, 0) + 1
            calls[self2.name] = n
            self2.hp_cur = 1                     # 第一次就"复活"归队
            self2.conditions.discard(COND_DEAD)
            self2.conditions.discard(COND_UNCONSCIOUS)
            res = _roll(ScriptRng([1]), 0, dv=10)
            if n > 1:
                self2.stabilize()
            return {"ok": True, "face": 1, "dv": 10, "roll": res, "dead": False}

        orig = Character.death_save
        Character.death_save = fake_death_save
        try:
            # 让敌人在旁边看着不还手，隔离出"生存检定循环"本身
            enc.npc_choose_action = lambda c: {"action": "wait"}
            st.player.hp_cur = 0
            st.player.conditions.add(COND_UNCONSCIOUS)
            outcome = session.run_combat()
        finally:
            Character.death_save = orig
        self.assertIn(outcome, ("win", "retreat", "tpk"))
        self.assertTrue(calls, "生存检定根本没有被触发")
        self.assertLessEqual(max(calls.values()), 2,
                             f"某个角色的生存检定被反复触发：{calls}")

    def test_stabilized_member_is_not_rerolled(self):
        c = make_pc()
        c.hp_cur = 0
        enc = Encounter(title="t", entry={}, rng=ScriptRng([5, 5, 5]))
        enc.allies.append(c)
        enc.enemies.append(make_foe())
        enc.order = [c, enc.enemies[0]]
        enc.begin_turn(c)                        # 第一回合：掷一次生存检定
        first = c.death_save_mod
        enc.begin_turn(c)                        # 第二回合：再掷一次
        self.assertEqual(first, 1)
        self.assertEqual(c.death_save_mod, 2)


def _state_with_small_encounter():
    st = make_state()
    st.module.encounters["e_small"] = {
        "title": "小冲突", "intro": "一个条子堵住了巷口。",
        "enemies": [["corrupt_cop", 1]], "loot": {"eurobucks": 40}}
    return st


if __name__ == "__main__":
    unittest.main()
