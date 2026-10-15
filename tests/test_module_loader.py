"""模组加载与 Cordis 注册表（《赛博朋克 RED》）。

覆盖：模组 JSON 校验（缺字段 / 场景链断裂 / 未知敌人 / 自定义 NPC / 改进点预算）、
``Module`` 访问器、Cordis 插件生命周期（install → scan → active_ids → delete /
requires 的 PENDING→ACTIVE / 连坐处置 / 内置保护）、Token 计量，
以及"自定义 RED NPC 数据块能真的进战斗"。

规则无关：唯一随 RED 变化的是内置模组 id 与 NPC 数据块的字段形状
（``stats``/``skills``/``hp``/``armor``/``weapons``，无护甲等级、无阶级与经验表）。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (MODULES_DIR, RED_MODULE_ID, fixture_module_data,  # noqa: E402
                     red_module, red_module_data)

from trpg.campaign import module_data as M               # noqa: E402
from trpg.campaign.cordis import (ACTIVE, DISPOSED,      # noqa: E402
                                   PENDING, BUILTIN_IDS, Cordis)
from trpg.campaign.loader import (BUILTIN_MODULE_IDS, Module,  # noqa: E402
                                   validate_module)
from trpg.harness.usage import TokenMeter                # noqa: E402


def builtin_json() -> dict:
    """随包发行的 RED 模组 JSON（锚定 MODULES_DIR，不依赖 cwd）。"""
    return red_module_data()


# ================================================================ 加载与校验
class TestLoader(unittest.TestCase):
    def test_shipped_module_loads(self):
        mod, errors, _w = Module.from_file(
            MODULES_DIR / RED_MODULE_ID / "module.json")
        self.assertEqual(errors, [])
        self.assertIsNotNone(mod)
        self.assertEqual(mod.id, RED_MODULE_ID)
        self.assertEqual(mod.title, "赛博朋克 RED：轻松模式")
        self.assertEqual(mod.en_title, "Cyberpunk RED: Easy Mode")
        self.assertEqual(mod.source, "builtin")
        self.assertEqual(len(mod.scenes), 6)
        self.assertEqual(len(mod.encounters), 2)
        self.assertEqual(len(mod.pregens), 5)
        self.assertEqual(mod.default_companions,
                         red_module_data()["default_companions"])
        self.assertIn("Lazlo", [n["name"] for n in mod.npcs.values()])

    def test_person_names_are_english(self):
        """人名口径：预设角色与专名 NPC 的名字不含汉字（中文只用于叙述与类型标签）。"""
        import re
        han = re.compile(r"[\u4e00-\u9fff]")
        mod, errors, _w = Module.from_file(
            MODULES_DIR / RED_MODULE_ID / "module.json")
        self.assertEqual(errors, [])
        for key, pg in mod.pregens.items():
            self.assertFalse(han.search(str(pg["name"])),
                             f"预设角色 {key} 的名字应为英文：{pg['name']}")
        for key in ("lazlo", "houston"):
            self.assertFalse(han.search(str(mod.npc_table[key]["name"])))
        self.assertEqual([pg["name"] for pg in mod.pregens.values()],
                         ["Forty", "Mover", "Torch", "Redtail", "24/7"])

    def test_code_level_builtin_is_isomorphic_in_shape(self):
        """代码内置兜底数据与 JSON 模组同构（同一规范的两份实现）。"""
        self.assertEqual(M.BUILTIN.id, RED_MODULE_ID)
        self.assertTrue(M.SCENES and M.ENCOUNTERS and M.PREGENS)
        self.assertEqual(M.SCENES[0]["id"], M.BUILTIN.first_scene_id())
        errors, _w = validate_module(M.builtin_data())
        self.assertEqual(errors, [])

    def test_missing_top_field_rejected(self):
        data = builtin_json()
        data.pop("epilogue")
        mod, errors, _w = Module.from_dict(data)
        self.assertIsNone(mod)
        self.assertTrue(any("epilogue" in e for e in errors))

    def test_broken_scene_chain_rejected(self):
        data = builtin_json()
        data["scenes"][0]["next"] = "查无此场"
        mod, errors, _w = Module.from_dict(data)
        self.assertIsNone(mod)
        self.assertTrue(any("场景链" in e for e in errors))

    def test_unknown_npc_rejected_and_custom_npc_accepted(self):
        data = builtin_json()
        data["encounters"]["e_alley_ambush"]["enemies"] = [["幽灵", 1]]
        _mod, errors, _w = Module.from_dict(data)
        self.assertTrue(any("未定义的 NPC" in e for e in errors))
        data["encounters"]["e_alley_ambush"]["enemies"] = [["salt_ghoul", 1]]
        data["custom_npcs"] = {
            "salt_ghoul": {"name": "盐渍食尸鬼",
                           "stats": {"int": 3, "ref": 5, "dex": 5, "tech": 2,
                                     "cool": 3, "will": 6, "luck": 3, "move": 4,
                                     "body": 7, "emp": 2},
                           "skills": {"brawling": 6}, "hp": 35, "armor": 7,
                           "armor_name": "凯夫拉", "weapons": ["heavy_melee"]}}
        data["encounters"]["e_alley_ambush"]["spawns"] = {"salt_ghoul": [[8, 3]]}
        mod, errors, _w = Module.from_dict(data)
        self.assertEqual(errors, [])
        self.assertIn("salt_ghoul", mod.npc_table)

    def test_ip_budget_warning(self):
        data = builtin_json()
        for s in data["scenes"]:
            s["ip"] = 0
        _mod, errors, warnings = Module.from_dict(data)
        self.assertEqual(errors, [])
        self.assertTrue(any("改进点" in w for w in warnings))

    def test_module_accessors(self):
        mod = red_module()
        first = mod.scenes[0]["id"]
        self.assertEqual(mod.first_scene_id(), first)
        self.assertEqual(mod.scene_by_id(first)["id"], first)
        self.assertIsNone(mod.scene_by_id("查无此场"))
        self.assertIn("e_alley_ambush", mod.encounters)
        self.assertIsNone(mod.encounter("查无此战"))
        handbook = mod.handbook()
        self.assertIn("赛博朋克 RED", handbook)
        self.assertIn("预设角色", handbook)
        self.assertIn("Lazlo", handbook)
        summary = mod.summary()
        self.assertEqual(summary["pregens"], 5)
        self.assertEqual(summary["source"], "builtin")
        self.assertEqual(len(mod.act_titles), 3)

    def test_builtin_ids_single_source_of_truth(self):
        self.assertEqual(set(BUILTIN_MODULE_IDS), {RED_MODULE_ID})
        self.assertEqual(BUILTIN_IDS, set(BUILTIN_MODULE_IDS))


# ================================================================ Cordis
class TestCordis(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "modules"
        self.root.mkdir(parents=True)
        self.cordis = Cordis(self.root)

    def tearDown(self):
        self._td.cleanup()

    def _mini(self, mid: str, requires=None) -> dict:
        data = fixture_module_data()
        data["id"] = mid
        if requires is not None:
            data["requires"] = requires
        return data

    def test_install_and_scan_and_active_ids(self):
        mod, errors, _w = self.cordis.install(self._mini("mini"))
        self.assertEqual(errors, [])
        self.assertIsNotNone(mod)
        self.assertTrue((self.root / "mini" / "module.json").exists())
        ids = [e["id"] for e in self.cordis.scan()]
        self.assertIn("mini", ids)
        self.assertIn("mini", self.cordis.active_ids())
        self.assertIsNotNone(self.cordis.ensure("mini")[0])

    def test_install_invalid_rejected_without_side_effects(self):
        data = self._mini("bad")
        data.pop("scenes")
        mod, errors, _w = self.cordis.install(data)
        self.assertIsNone(mod)
        self.assertTrue(errors)
        self.assertFalse((self.root / "bad").exists())

    def test_cannot_overwrite_builtin_id(self):
        data = self._mini(RED_MODULE_ID)
        mod, errors, _w = self.cordis.install(data, source="imported")
        self.assertIsNone(mod)
        self.assertTrue(any("内置" in e for e in errors))

    def test_cannot_delete_builtin_id(self):
        ok, err = self.cordis.delete(RED_MODULE_ID)
        self.assertFalse(ok)
        self.assertIn("内置", err)

    def test_delete_removes_dir_and_deactivates(self):
        self.cordis.install(self._mini("mini"))
        ok, err = self.cordis.delete("mini")
        self.assertTrue(ok, err)
        self.assertFalse((self.root / "mini").exists())
        self.assertNotIn("mini", self.cordis.active_ids())
        self.assertEqual(self.cordis.fiber_states().get("mini"), DISPOSED)

    def test_requires_pending_then_auto_boot(self):
        self.cordis.install(self._mini("dep"))
        self.cordis.unload("dep")
        mod, errors, _w = self.cordis.install(
            self._mini("waiter", requires=["dep"]))
        self.assertIsNone(mod)
        self.assertTrue(any("PENDING" in e or "等待依赖" in e for e in errors))
        self.assertEqual(self.cordis.fiber_states()["waiter"], PENDING)
        self.cordis.load("dep")
        self.assertEqual(self.cordis.fiber_states()["waiter"], ACTIVE)

    def test_dependent_disposed_when_service_unloads(self):
        self.cordis.install(self._mini("dep"))
        self.cordis.install(self._mini("waiter", requires=["dep"]))
        self.assertEqual(self.cordis.fiber_states()["waiter"], ACTIVE)
        self.cordis.unload("dep")
        self.assertNotIn("waiter", self.cordis.active_ids())

    def test_self_require_rejected(self):
        data = self._mini("selfish", requires=["selfish"])
        mod, errors, _w = self.cordis.install(data)
        self.assertIsNone(mod)
        self.assertTrue(any("自身" in e for e in errors))

    def test_scan_source_labels(self):
        self.cordis.install(self._mini("mini"))
        self.cordis.install(self._mini("gen_thing"), source="generated")
        sources = {e["id"]: e["source"] for e in self.cordis.scan()}
        self.assertEqual(sources["mini"], "imported")
        self.assertEqual(sources["generated-gen_thing"], "generated")

    def test_ensure_builds_module_from_disk(self):
        self.cordis.install(self._mini("mini"))
        mod, errors, _w = self.cordis.ensure("mini")
        self.assertEqual(errors, [])
        self.assertEqual(mod.id, "mini")
        bad, errors, _w = self.cordis.ensure("查无此模组")
        self.assertIsNone(bad)
        self.assertTrue(errors)


# ================================================================ Token 计量
class TestTokenMeter(unittest.TestCase):
    def test_record_summary_and_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "usage.json"
            meter = TokenMeter(path)
            meter.record("dm", "deepseek-chat",
                         {"prompt_tokens": 10, "completion_tokens": 20,
                          "total_tokens": 30})
            meter.record("dm", "deepseek-chat",
                         {"prompt_tokens": 1, "completion_tokens": 2,
                          "total_tokens": 3})
            meter.record("generator", "deepseek-chat",
                         {"prompt_tokens": 5, "completion_tokens": 5,
                          "total_tokens": 10})
            summary = meter.summary()
            self.assertEqual(summary["totals"]["total_tokens"], 43)
            self.assertEqual(summary["totals"]["records"], 3)
            self.assertEqual(summary["by_agent"]["dm"], 33)
            self.assertEqual(summary["by_agent"]["generator"], 10)
            self.assertTrue(path.exists())
            again = TokenMeter(path).summary()
            self.assertEqual(again["totals"]["total_tokens"], 43)

    def test_clear(self):
        with tempfile.TemporaryDirectory() as td:
            meter = TokenMeter(Path(td) / "u.json")
            meter.record("dm", "m", {"total_tokens": 7})
            meter.clear()
            self.assertEqual(meter.summary()["totals"]["total_tokens"], 0)
            self.assertEqual(meter.summary()["by_agent"], {})


# ================================================================ 自定义 NPC
class TestCustomNpcInCombat(unittest.TestCase):
    """自定义 ``custom_npcs`` 数据块要能真的进战斗（RED 的"自定义怪物"）。"""

    def _module(self):
        data = fixture_module_data()
        data["custom_npcs"]["ogre_king"] = {
            "name": "食人魔王",
            "stats": {"int": 4, "ref": 5, "dex": 5, "tech": 2, "cool": 5,
                      "will": 8, "luck": 4, "move": 4, "body": 10, "emp": 3},
            "skills": {"brawling": 8, "athletics": 6, "evasion": 3},
            "hp": 60, "armor": 11, "armor_name": "轻型防弹夹克",
            "weapons": ["very_heavy_melee", "brawling"],
            "notes": "帮派的打手头目，专挑最硬的对手。",
        }
        data["encounters"]["e1"]["enemies"] = [["ogre_king", 1]]
        data["encounters"]["e1"]["spawns"] = {"ogre_king": [[8, 3]]}
        mod, errors, _w = Module.from_dict(data)
        self.assertEqual(errors, [])
        return mod

    def test_custom_npc_fights(self):
        from trpg.game import state as G
        from helpers import make_state
        mod = self._module()
        st = make_state(module=mod)
        st.scene_id = "s1"
        enc = G.build_encounter("e1", st)
        self.assertEqual(len(enc.enemies), 1)
        boss = enc.enemies[0]
        self.assertEqual(boss.name, "食人魔王")
        self.assertEqual(boss.hp_max, 60)
        self.assertEqual(boss.armor_at("torso"), 11)
        self.assertEqual(boss.skill_total("brawling"), 5 + 8)
        enc.begin()
        pc = enc.allies[0]
        enc.attack(pc, boss, "heavy_pistol")
        self.assertLessEqual(boss.hp_cur, 60)

    def test_custom_npc_statblock_shape(self):
        from trpg.rules.character import Character
        block = self._module().custom_npcs["ogre_king"]
        c = Character.from_statblock("ogre_king", block)
        self.assertEqual(c.kind, "npc")
        self.assertEqual(c.hp_max, 60)
        self.assertEqual(c.stat("body"), 10)
        self.assertIn("very_heavy_melee", c.weapons)
        self.assertTrue(c.desc)


if __name__ == "__main__":
    unittest.main()
