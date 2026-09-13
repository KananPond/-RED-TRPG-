"""模组数据完整性测试。"""

import unittest

from ctrcg.campaign import module_data as M
from ctrcg.rules import data as D


class TestModuleIntegrity(unittest.TestCase):
    def test_scene_chain_connected(self):
        ids = {s["id"] for s in M.SCENES}
        self.assertEqual(len(ids), len(M.SCENES), "场景 id 重复")
        # 链条连续且终止于 None
        for s in M.SCENES:
            if s["next"] is not None:
                self.assertIn(s["next"], ids)
        self.assertIsNone(M.SCENES[-1]["next"])

    def test_encounters_reference_valid_monsters(self):
        for eid, e in M.ENCOUNTERS.items():
            self.assertTrue(e["monsters"], f"{eid} 无怪物")
            for mid, count in e["monsters"]:
                self.assertIn(mid, D.MONSTERS, f"{eid} 引用未知怪物 {mid}")
                self.assertGreaterEqual(count, 1)
            self.assertIn("gold", e["loot"])

    def test_scenes_reference_valid_encounters(self):
        for s in M.SCENES:
            if s["encounter"]:
                self.assertIn(s["encounter"], M.ENCOUNTERS, f"场景 {s['id']} 引用未知遭遇")

    def test_skills_valid(self):
        for s in M.SCENES:
            for sk in s["skills"]:
                self.assertIn(sk["skill"], D.SKILLS)
                self.assertTrue(5 <= sk["dc"] <= 25)

    def test_xp_budget_reaches_level3(self):
        """全程 XP 应足以让单人抵达 3 级（900）。"""
        total = sum(m["xp"] for e in M.ENCOUNTERS.values() for _, c in e["monsters"] for m in [{"xp": 0}])
        total = 0
        for e in M.ENCOUNTERS.values():
            for mid, count in e["monsters"]:
                total += D.MONSTERS[mid]["xp"] * count
        milestones = sum(s["milestone"] for s in M.SCENES)
        per_solo = (total + milestones) // 4  # 4人队平分
        self.assertGreaterEqual(per_solo, 900, f"单人经验不足: {per_solo}")

    def test_companions_buildable(self):
        from ctrcg.rules.character import Character
        for comp in M.COMPANIONS:
            c = Character.create(
                name=comp["name"], race=comp["race"], klass=comp["klass"],
                background=comp["background"], scores=comp["scores"],
                skill_choices=comp["skills"], fighting_style=comp["fighting_style"])
            self.assertGreater(c.hp_max, 5)

    def test_handbook_renders(self):
        text = M.module_handbook()
        self.assertIn("星坠矿脉", text)
        self.assertIn("灰烬之主", text)


if __name__ == "__main__":
    unittest.main()
