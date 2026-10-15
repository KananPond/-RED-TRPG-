"""战术网格与射程（《赛博朋克 RED》）。

单位是**米**：``METERS_PER_CELL = 2``，1 格 = 2 米；移动力 = 机动 × 2 米。
覆盖：``BattleGrid`` 纯几何（切比雪夫距离、米换算、视线、寻路、范围格）、
移动预算与奔跑/起身、贴身纠缠的同步、借机攻击与反应消耗、
按射程带查表的远程门禁（超程拒绝且不消耗动作）、掩体吸收、
以及由场景 ``map`` 装配遭遇时的布点约束。

``make_pc`` 从 ``test_combat`` 转出（保持原有的跨文件导入风格可用）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import ScriptRng, make_state                # noqa: E402
from test_combat import PISTOL, corridor, duel, make_foe, make_pc  # noqa: E402,F401

from trpg.rules import data as D                        # noqa: E402
from trpg.rules.character import (AIMED_SHOT_PENALTY,   # noqa: E402
                                   COND_PRONE, LOCATIONS)
from trpg.rules.combat import (METERS_PER_CELL,         # noqa: E402
                                BattleGrid, Encounter)


def grid_enc(**kw) -> Encounter:
    return duel(**kw)


# ================================================================ 几何
class TestBattleGrid(unittest.TestCase):
    """1 格 = 2 米；距离为切比雪夫（对角也算 1 格）。"""

    def test_metres_per_cell_constant(self):
        self.assertEqual(METERS_PER_CELL, 2)

    def test_chebyshev_distance(self):
        g = BattleGrid(10, 10)
        self.assertEqual(g.dist((0, 0), (3, 4)), 4)
        self.assertEqual(g.dist((2, 2), (2, 2)), 0)
        self.assertEqual(g.dist((0, 0), (4, 4)), 4)          # 对角 4 格

    def test_distance_in_metres(self):
        g = BattleGrid(10, 10)
        self.assertEqual(g.dist_m((0, 0), (3, 4)), 8)        # 4 格 × 2 米

    def test_inside_and_blocked(self):
        g = BattleGrid(5, 4, obstacles=[(2, 2)])
        self.assertTrue(g.inside((4, 3)))
        self.assertFalse(g.inside((5, 3)))
        self.assertTrue(g.blocked((2, 2)))
        self.assertTrue(g.blocked((-1, 0)))
        self.assertFalse(g.blocked((0, 0)))

    def test_line_of_sight_blocked_by_obstacle(self):
        g = BattleGrid(9, 5, obstacles=[(4, 2)])
        self.assertFalse(g.has_los((0, 2), (8, 2)))
        self.assertTrue(g.has_los((0, 2), (3, 2)))

    def test_line_of_sight_ignores_the_endpoints(self):
        """站在障碍格里不算「被障碍挡住」。"""
        g = BattleGrid(9, 5, obstacles=[(0, 2), (8, 2)])
        self.assertTrue(g.has_los((0, 2), (8, 2)))

    def test_path_avoids_blocked_cells(self):
        g = BattleGrid(9, 5, obstacles=[(2, 2)])
        path = g.path((0, 2), (4, 2))
        self.assertTrue(path)
        self.assertEqual(path[-1], (4, 2))
        self.assertNotIn((2, 2), path)

    def test_path_avoids_occupied_cells(self):
        g = BattleGrid(9, 5)
        path = g.path((0, 0), (2, 0), occupied={(1, 0)})
        self.assertTrue(path)
        self.assertNotIn((1, 0), path)
        self.assertEqual(path[-1], (2, 0))

    def test_path_to_an_unreachable_goal_is_empty(self):
        g = BattleGrid(9, 5, obstacles=[(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)])
        self.assertEqual(g.path((0, 0), (4, 4)), [])
        self.assertEqual(g.path((3, 3), (3, 3)), [])          # 起点即终点

    def test_neighbors_skip_blocked_and_outside(self):
        g = BattleGrid(3, 3, obstacles=[(1, 1)])
        self.assertEqual(sorted(g.neighbors((0, 0))), [(0, 1), (1, 0)])
        self.assertEqual(sorted(g.neighbors((1, 0))), [(0, 0), (0, 1), (2, 0), (2, 1)])

    def test_cells_within_is_square_and_clipped(self):
        g = BattleGrid(5, 5)
        cells = g.cells_within((0, 0), 1)
        self.assertIn((0, 0), cells)
        self.assertIn((1, 1), cells)
        self.assertNotIn((2, 0), cells)
        self.assertNotIn((-1, 0), cells)

    def test_blind_fire_through_obstacles_is_rejected(self):
        grid = BattleGrid(12, 4, obstacles=[(5, 1)])
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=grid, cells=9,
                   rng=ScriptRng([9]))
        res = enc.attack(pc, enc.enemies[0], PISTOL)
        self.assertFalse(res["ok"])
        self.assertIn("射击线", res["reason"])


# ================================================================ 移动
class TestGridMovement(unittest.TestCase):
    """移动不消耗动作，只花米数；1 格 = 2 米。"""

    def test_move_spends_metres_without_spending_the_action(self):
        pc = make_pc()
        enc = grid_enc(pc=pc, grid=corridor(8), cells=0)
        out = enc.move(pc, (3, 1))
        self.assertTrue(out["ok"])
        self.assertEqual(out["spent"], 3 * METERS_PER_CELL)
        self.assertEqual(out["cell"], (3, 1))
        self.assertTrue(enc.has_action(pc))

    def test_budget_truncates_beyond_the_move_value(self):
        pc = make_pc(stats={"move": 3, "body": 5, "will": 5})   # 6 米
        enc = grid_enc(pc=pc, grid=corridor(20), cells=0)
        out = enc.move(pc, (9, 1), spend_all=True)              # 只走预算内那一段
        self.assertTrue(out["ok"])
        self.assertEqual(out["spent"], 6)
        self.assertEqual(enc.grid.dist((0, 1), out["cell"]), 3)  # 3 格 = 6 米
        self.assertEqual(enc.speed_left[pc.name], 0)

    def test_move_onto_an_occupied_cell_is_refused(self):
        """生产缺陷（trpg/rules/combat.py:842 与 BattleGrid.path）：``path`` 把终点格
        排除在「已占用」检查之外（``nxt != goal``），``move`` 又只校验 ``blocked``，
        于是可以把棋子走到同伴/敌人身上——前端地图与战况板依赖的「一格一子」
        不变量会被打破。"""
        buddy = make_pc("同伴", role="tech")
        pc = make_pc("甲")
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(6), cells=5)
        enc.allies.append(buddy)
        enc.positions[buddy.name] = (2, 1)
        enc.speed_left[buddy.name] = buddy.move_value()
        out = enc.move(pc, (2, 1))
        self.assertFalse(out["ok"])

    def test_prone_must_stand_before_moving(self):
        pc = make_pc()
        enc = grid_enc(pc=pc, grid=corridor(6), cells=0)
        pc.conditions.add(COND_PRONE)
        self.assertFalse(enc.move(pc, (1, 1))["ok"])
        self.assertTrue(enc.stand(pc)["ok"])
        self.assertTrue(enc.move(pc, (1, 1))["ok"])

    def test_dash_doubles_the_budget(self):
        pc = make_pc(stats={"move": 3, "body": 5, "will": 5})   # 6 米
        enc = grid_enc(pc=pc, grid=corridor(20), cells=0)
        self.assertFalse(enc.dash(pc)["ok"])                    # 还没移动过
        enc.move(pc, (1, 1))
        enc.dash(pc)
        out = enc.move(pc, (5, 1), spend_all=True)              # 6 − 2 + 6 = 10 米
        self.assertTrue(out["ok"])
        self.assertEqual(enc.moved_this_turn[pc.name], 10)

    def test_begin_turn_resets_the_budget(self):
        enc = grid_enc(grid=corridor(10), cells=0)
        pc = enc.allies[0]
        enc.move(pc, (2, 1))
        self.assertLess(enc.speed_left[pc.name], pc.move_value())
        enc.begin_turn(pc)
        self.assertEqual(enc.speed_left[pc.name], pc.move_value())
        self.assertEqual(enc.moved_this_turn[pc.name], 0)

    def test_engagement_syncs_at_two_metres(self):
        enc = grid_enc(grid=corridor(6), cells=3)               # 6 米：不贴身
        pc, foe = enc.allies[0], enc.enemies[0]
        self.assertNotIn(foe.name, enc.engagement[pc.name])
        enc.move(pc, (2, 1))                                    # 走到 2 米
        self.assertIn(foe.name, enc.engagement[pc.name])

    def test_leaving_reach_provokes_an_opportunity_attack(self):
        pc = make_pc("甲")
        pc.hp_max = pc.hp_cur = 45
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(6), cells=1)
        foe.weapons = ["medium_pistol"]
        enc.positions[foe.name] = (1, 1)
        enc._sync_engagement()
        self.assertIn(foe.name, enc.engagement[pc.name])
        enc.rng = ScriptRng([8, 3, 3, 3])
        out = enc.move(pc, (4, 1))
        self.assertTrue(out["ok"])
        self.assertTrue(foe.reaction_used)                      # 反应被用掉
        self.assertIn("借机攻击", "\n".join(enc.log_lines))

    def test_no_provocation_when_the_reaction_is_gone(self):
        pc = make_pc("甲")
        foe = make_foe()
        enc = duel(pc=pc, foe=foe, grid=corridor(6), cells=1)
        foe.reaction_used = True
        enc.rng = ScriptRng([8, 3, 3])
        enc.move(pc, (4, 1))
        self.assertNotIn("借机攻击", "\n".join(enc.log_lines))

    def test_approach_moves_adjacent_to_the_target(self):
        pc = make_pc("甲")
        enc = grid_enc(pc=pc, grid=corridor(8), cells=5)
        out = enc.approach(pc, enc.enemies[0])
        self.assertTrue(out["ok"])
        self.assertLessEqual(enc.measure(pc, enc.enemies[0]), METERS_PER_CELL)
        self.assertIn(enc.enemies[0].name, enc.engagement[pc.name])


# ================================================================ 射程门禁
class TestRangeGates(unittest.TestCase):
    """射程带决定 DV；打不到就拒绝，而且不消耗动作。"""

    def _shot(self, cells, *, weapon=PISTOL, skill="handgun", dice=(9,)):
        pc = make_pc(weapons=[weapon], skills={skill: 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(120), cells=cells)
        return pc, enc, ScriptRng(list(dice))

    def test_pistol_bands_through_the_grid(self):
        expected = {3: 13, 6: 15, 12: 20, 25: 25, 50: 30}
        for cells, dv in expected.items():
            pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
            enc = duel(pc=pc, foe=make_foe(), grid=corridor(60), cells=cells)
            enc.rng = ScriptRng([9, 3, 3, 3])
            res = enc.attack(pc, enc.enemies[0], PISTOL)
            self.assertEqual(res["dv"], dv, f"{cells} 格 = {cells * 2} 米")
            self.assertEqual(res["meters"], cells * 2)

    def test_melee_out_of_reach_is_refused(self):
        """生产缺陷（trpg/rules/combat.py:506-515）：白刃战分支只做「攻击 vs 闪避」
        对抗检定，**不检查距离**；网格模式下隔着 8 米（4 格）也能挥刀命中。
        RED 要求近战武器必须贴身（相邻格）才能攻击。修复后本用例会变成
        unexpected success。"""
        pc = make_pc(weapons=["medium_melee"], skills={"melee_weapon": 8})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(5), cells=4)
        enc.rng = ScriptRng([9, 2, 5, 5])
        res = enc.attack(pc, enc.enemies[0], "medium_melee")
        self.assertFalse(res["ok"])
        self.assertEqual(enc.enemies[0].hp_cur, enc.enemies[0].hp_max)

    def test_beyond_the_weapon_range_is_refused_without_spending(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(120), cells=101)
        out = enc.attack_action(pc, enc.enemies[0], PISTOL)
        self.assertFalse(out["ok"])
        self.assertTrue(out["not_spent"])
        self.assertTrue(enc.has_action(pc))

    def test_a_rifle_reaches_where_a_pistol_cannot(self):
        pc = make_pc(weapons=["assault_rifle"], skills={"shoulder_arms": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(120), cells=101)
        enc.rng = ScriptRng([9, 3, 3, 3, 3, 3])
        res = enc.attack(pc, enc.enemies[0], "assault_rifle")
        self.assertTrue(res["ok"])
        self.assertEqual(res["dv"], D.RANGED_DV["rifle"][6])     # 201-400 米带

    def test_aimed_shot_raises_the_dv_by_eight(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        enc = duel(pc=pc, foe=make_foe(), grid=corridor(3), cells=3)
        enc.rng = ScriptRng([9, 3, 3, 3])
        plain = enc.attack(pc, enc.enemies[0], PISTOL)
        enc.refresh_actions(pc)
        enc.rng = ScriptRng([9, 3, 3, 3])
        aimed = enc.attack(pc, enc.enemies[0], PISTOL, aimed=True)
        self.assertEqual(AIMED_SHOT_PENALTY, -8)
        self.assertEqual(aimed["dv"], plain["dv"] + 8)
        self.assertEqual(aimed["location"], LOCATIONS[1])       # 瞄准射击打头部
        self.assertTrue(aimed["aimed"])

    def test_bullet_dodge_replaces_the_range_dv(self):
        pc = make_pc(weapons=[PISTOL], skills={"handgun": 6})
        quick = make_foe()
        quick.stats["ref"] = 8
        quick.skill_levels["evasion"] = 4
        enc = duel(pc=pc, foe=quick, grid=corridor(12), cells=12)   # 24 米 → DV 20
        enc.rng = ScriptRng([9, 2])                                 # 21 vs 11
        res = enc.attack(pc, quick, PISTOL)
        self.assertTrue(res["dodged"])
        self.assertEqual(res["dv"], 2 + quick.skill_total("evasion"))
        self.assertNotEqual(res["dv"], 20)


# ================================================================ 掩体
class TestGridCover(unittest.TestCase):
    """掩体有耐久，替目标吃伤害；耐久归零即被摧毁。"""

    def test_cover_material_table(self):
        enc = duel(grid=corridor(4), cells=1)
        for material in ("厚钢", "薄钢", "厚混凝土", "薄木"):
            enc.set_cover((1, 1), material)
            self.assertEqual(enc.cover_hp((1, 1)), D.COVER[material])

    def test_cover_absorbs_and_breaks(self):
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(4), cells=2)
        enc.set_cover((2, 1), "薄木", hp=3)
        enc.rng = ScriptRng([9, 3, 3, 3])
        res = enc.attack(pc, foe, PISTOL)
        self.assertEqual(res["damage"], 0)
        self.assertEqual(enc.cover_hp((2, 1)), 0)
        self.assertTrue(res["cover"]["destroyed"])
        self.assertEqual(foe.hp_cur, 40)

    def test_intact_cover_keeps_absorbing(self):
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(4), cells=2)
        enc.set_cover((2, 1), "厚钢")                       # 50 点耐久
        enc.rng = ScriptRng([9, 3, 3, 3])
        enc.attack(pc, foe, PISTOL)
        self.assertEqual(foe.hp_cur, 40)
        self.assertEqual(enc.cover_hp((2, 1)), 50 - 9)

    def test_no_cover_means_full_damage(self):
        pc = make_pc("甲", weapons=[PISTOL], skills={"handgun": 6})
        foe = make_foe()
        foe.hp_max = foe.hp_cur = 40
        enc = duel(pc=pc, foe=foe, grid=corridor(4), cells=2)
        enc.rng = ScriptRng([9, 3, 3, 3])
        self.assertGreater(enc.attack(pc, foe, PISTOL)["damage"], 0)


# ================================================================ 装配
class TestDeploymentFromSceneMap(unittest.TestCase):
    """场景 ``map`` 声明 → ``BattleGrid`` + 棋子布点。"""

    def test_deploy_from_state_uses_the_scene_map(self):
        from trpg.game import state as G
        st = make_state()
        enc = G.build_encounter("e1", st)
        self.assertEqual((enc.grid.w, enc.grid.h), (11, 8))
        enc.begin()
        cells = [enc.positions[c.name] for c in enc.combatants
                 if c.name in enc.positions]
        self.assertEqual(len(cells), len(set(cells)))
        for (x, y) in cells:
            self.assertTrue(enc.grid.inside((x, y)))
            self.assertFalse(enc.grid.blocked((x, y)))

    def test_deploy_respects_declared_enemy_spawns(self):
        from trpg.game import state as G
        st = make_state()
        enc = G.build_encounter("e1", st)
        enc.begin()
        cops = [c for c in enc.enemies if c.source_key == "corrupt_cop"]
        placed = {enc.positions[c.name] for c in cops}
        self.assertTrue(placed <= {(9, 2), (9, 5)})

    def test_allies_start_on_the_left_edge_when_undeclared(self):
        from trpg.game import state as G
        st = make_state()
        enc = G.build_encounter("e1", st)
        enc.begin()
        for ally in enc.allies:
            self.assertEqual(enc.positions[ally.name][0], 1)

    def test_theater_mode_when_the_scene_has_no_map(self):
        from trpg.game import state as G
        st = make_state()
        st.scene_id = "s2"                                  # 场景 s2 没有 map
        enc = G.build_encounter("e1", st)
        self.assertIsNone(enc.grid)
        self.assertEqual(enc.measure(enc.allies[0], enc.enemies[0]), 13)

    def test_map_state_meters_per_cell(self):
        from trpg.game import state as G
        st = make_state()
        st.map = G.build_map_state(st.module, st.module.scene_by_id("s1"), st.party())
        brief = st.map_brief()
        self.assertEqual(brief["meters_per_cell"], METERS_PER_CELL)
        self.assertEqual(brief["grid"], [11, 8])
        self.assertIn("1格=2米", st.map_digest())

    def test_no_two_tokens_share_a_cell(self):
        from trpg.game import state as G
        st = make_state()
        st.map = G.build_map_state(st.module, st.module.scene_by_id("s1"), st.party())
        cells = [tuple(t["cell"]) for t in st.map["tokens"].values()]
        self.assertEqual(len(cells), len(set(cells)))
        for token in st.map["tokens"].values():
            self.assertTrue(0 <= token["cell"][0] < 11)
            self.assertTrue(0 <= token["cell"][1] < 8)


if __name__ == "__main__":
    unittest.main()
