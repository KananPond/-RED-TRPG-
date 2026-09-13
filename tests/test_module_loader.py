"""模组原子化加载与 Cordis 注册表测试。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ctrcg.campaign import module_data as M
from ctrcg.campaign.cordis import Cordis
from ctrcg.campaign.loader import Module, validate_module
from ctrcg.harness.usage import TokenMeter


def builtin_json() -> dict:
    return json.loads((Path(__file__).resolve().parent.parent
                       / "modules/starfall/module.json").read_text(encoding="utf-8"))


class TestLoader(unittest.TestCase):
    def test_builtin_json_loads(self):
        mod, errors, _warns = Module.from_file(
            Path(__file__).resolve().parent.parent / "modules/starfall/module.json")
        self.assertIsNone(mod is None and errors or None)
        self.assertIsNotNone(mod)
        self.assertEqual(mod.title, "星坠矿脉的阴影")
        self.assertEqual(len(mod.scenes), 11)
        self.assertEqual(len(mod.companions), 3)

    def test_builtin_data_matches_json(self):
        """代码内置数据与原子 JSON 产物保持同构。"""
        data = builtin_json()
        errors, _ = validate_module(data)
        self.assertEqual(errors, [])
        self.assertEqual(data["scenes"][0]["id"], M.SCENES[0]["id"])

    def test_missing_top_field_rejected(self):
        data = builtin_json()
        del data["encounters"]
        errors, _ = validate_module(data)
        self.assertTrue(any("encounters" in e for e in errors))

    def test_broken_scene_chain_rejected(self):
        data = builtin_json()
        data["scenes"][0]["next"] = "不存在的场景"
        errors, _ = validate_module(data)
        self.assertTrue(any("断裂" in e or "孤场景" in e for e in errors))

    def test_unknown_monster_rejected_and_custom_accepted(self):
        data = builtin_json()
        data["encounters"]["e_ambush"]["monsters"] = [["my_ogre_king", 1]]
        errors, _ = validate_module(data)
        self.assertTrue(any("my_ogre_king" in e for e in errors))
        data["custom_monsters"] = {"my_ogre_king": {
            "name": "食人魔王", "xp": 450, "hp": "10d10+20", "ac": 14,
            "speed": 40,
            "abilities": {"str": 19, "dex": 8, "con": 16, "int": 5,
                          "wis": 7, "cha": 7},
            "traits": ["特性"],
            "attacks": [{"weapon": "greatclub", "bonus": 6, "dmg": "2d8",
                         "extra": "+4"}],
            "darkvision": 60}}
        errors, _ = validate_module(data)
        self.assertEqual(errors, [])

    def test_xp_budget_warning(self):
        data = builtin_json()
        for enc in data["encounters"].values():
            enc["monsters"] = [["worg", 1]]
        errors, warns = validate_module(data)
        self.assertEqual(errors, [])
        self.assertTrue(any("经验预算" in w for w in warns))

    def test_module_accessors(self):
        mod = M.BUILTIN
        self.assertEqual(mod.scene_by_id("road_ambush")["encounter"], "e_ambush")
        self.assertIn("哥布林", mod.handbook())
        self.assertIn("第一幕", mod.act_titles.get(1, ""))


class TestCordis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cordis = Cordis(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_finds_builtin_copy(self):
        data = builtin_json()
        data["id"] = "mini"
        mod, errors, _ = self.cordis.install(data, source="imported")
        self.assertIsNotNone(mod, errors)
        self.assertIn("mini", [e["id"] for e in self.cordis.scan()])
        self.assertIn("mini", self.cordis.active_ids())

    def test_install_invalid_rejected_no_side_effects(self):
        data = builtin_json()
        data["id"] = "broken"
        data["encounters"] = {}
        mod, errors, _ = self.cordis.install(data, source="imported")
        self.assertIsNone(mod)
        self.assertFalse((Path(self.tmp.name) / "broken").exists())

    def test_cannot_overwrite_builtin_id(self):
        data = builtin_json()
        mod, errors, _ = self.cordis.install(data, source="imported")
        self.assertIsNone(mod)
        self.assertIn("内置", errors[0])

    def test_delete_removes_dir_and_deactivates(self):
        data = builtin_json()
        data["id"] = "tempmod"
        self.cordis.install(data, source="imported")
        ok, err = self.cordis.delete("tempmod")
        self.assertTrue(ok, err)
        self.assertNotIn("tempmod", self.cordis.active_ids())
        self.assertFalse((Path(self.tmp.name) / "tempmod").exists())

    def test_cannot_delete_starfall(self):
        ok, err = self.cordis.delete("starfall")
        self.assertFalse(ok)
        self.assertIn("内置", err or "")

    # ---- Cordis 插件生命周期：requires 依赖
    def _mini(self, mid, requires=None):
        data = builtin_json()
        data["id"] = mid
        if requires is not None:
            data["requires"] = requires
        return data

    def test_requires_pending_then_auto_boot(self):
        # 先装依赖方（dep 尚不存在）→ PENDING，大声失败
        mod, errors, _ = self.cordis.install(
            self._mini("down", requires=["dep"]), source="imported")
        self.assertIsNone(mod)
        self.assertEqual(self.cordis.fiber_states().get("down"), "pending")
        self.assertNotIn("down", self.cordis.active_ids())
        # 依赖被安装 → 等待中的插件自动激活（PENDING → ACTIVE）
        self.cordis.install(self._mini("dep"), source="imported")
        self.assertEqual(self.cordis.fiber_states().get("down"), "active")
        self.assertIn("down", self.cordis.active_ids())

    def test_dependent_disposed_when_service_unloads(self):
        self.cordis.install(self._mini("dep"), source="imported")
        self.cordis.install(self._mini("down", requires=["dep"]), source="imported")
        self.assertIn("down", self.cordis.active_ids())
        ok, err = self.cordis.unload("dep")
        self.assertTrue(ok, err)
        # 服务消失 → 依赖方被自动处置（Cordis 连坐语义）
        self.assertNotIn("down", self.cordis.active_ids())
        self.assertEqual(self.cordis.fiber_states().get("down"), "disposed")

    def test_self_require_rejected(self):
        mod, errors, _ = self.cordis.install(
            self._mini("selfreq", requires=["selfreq"]), source="imported")
        self.assertIsNone(mod)
        self.assertTrue(any("自身" in e for e in errors))

    def test_ensure_reloads_on_mtime_change(self):
        data = builtin_json()
        data["id"] = "hotmod"
        self.cordis.install(data, source="imported")
        mod1, _, _ = self.cordis.ensure("hotmod")
        # 修改文件（未来时间戳由文件系统保证）
        import os, time as _t
        path = Path(self.tmp.name) / "hotmod" / "module.json"
        d2 = json.loads(path.read_text(encoding="utf-8"))
        d2["title"] = "热更新标题"
        path.write_text(json.dumps(d2, ensure_ascii=False), encoding="utf-8")
        os.utime(path, (_t.time() + 5, _t.time() + 5))
        mod2, _, _ = self.cordis.ensure("hotmod")
        self.assertEqual(mod2.title, "热更新标题")
        self.assertIsNot(mod1, mod2)


class TestTokenMeter(unittest.TestCase):
    def test_record_and_summary(self):
        with tempfile.TemporaryDirectory() as d:
            meter = TokenMeter(Path(d) / "usage.json")
            meter.record("dm", "deepseek-chat",
                         {"prompt_tokens": 100, "completion_tokens": 50,
                          "total_tokens": 150}, steps=2)
            meter.record("pc:roland", "deepseek-chat",
                         {"prompt_tokens": 80, "completion_tokens": 20,
                          "total_tokens": 100})
            s = meter.summary()
            self.assertEqual(s["totals"]["records"], 2)
            self.assertEqual(s["totals"]["total_tokens"], 250)
            self.assertEqual(s["by_agent"]["dm"], 150)
            # 持久化往返
            meter2 = TokenMeter(Path(d) / "usage.json")
            self.assertEqual(len(meter2.records), 2)

    def test_clear(self):
        with tempfile.TemporaryDirectory() as d:
            meter = TokenMeter(Path(d) / "usage.json")
            meter.record("dm", "m", {"total_tokens": 5})
            meter.clear()
            self.assertEqual(meter.summary()["totals"]["records"], 0)
            self.assertEqual(len(TokenMeter(Path(d) / "usage.json").records), 0)


class TestCustomMonsterInCombat(unittest.TestCase):
    def test_custom_monster_fights(self):
        import random
        from ctrcg.rules.combat import Encounter, Monster, monster_choose_action
        from tests.test_game import make_state

        state = make_state()
        custom = {"ogre_king": {
            "name": "食人魔王", "xp": 450, "hp": "10d10+20", "ac": 14,
            "speed": 40,
            "abilities": {"str": 19, "dex": 8, "con": 16, "int": 5, "wis": 7, "cha": 7},
            "traits": ["施法者"], "attacks": [{"weapon": "greatclub", "bonus": 6,
                                               "dmg": "2d8", "extra": "+4"}],
            "darkvision": 60}}
        rng = random.Random(3)
        king = Monster("ogre_king", rng, table=custom)
        self.assertGreater(king.hp_max, 10)
        enc = Encounter("t")
        enc.rng = rng
        enc.add(state.player, "party")
        enc.add(king, "enemy")
        act = monster_choose_action(enc, king)
        self.assertEqual(act["action"], "attack")
        r = enc.attack(king, state.player, "greatclub", rng)
        self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main()
