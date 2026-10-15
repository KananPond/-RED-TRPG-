"""战术地图（《赛博朋克 RED》）：状态模型 / 模组校验 / 存档 / map_update 工具 /
资产路由安全（路径穿越）。

单位口径：**1 格 = 2 米**（``rules.combat.METERS_PER_CELL``）。地图与规则无关，
移植时只需要换模组夹具、场景 id、棋子图标来源与 digest 文案。

``make_pc`` 仍从 ``test_combat`` 转出（保持原来的跨文件导入风格）。
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (MODULES_DIR, RED_MODULE_ID, ScriptRng,  # noqa: E402
                     fixture_module, fixture_module_data, make_state)
from test_combat import make_pc                           # noqa: E402,F401 (转出)

from trpg.campaign.loader import Module, validate_module  # noqa: E402
from trpg.game import state as G                          # noqa: E402
from trpg.rules.combat import METERS_PER_CELL             # noqa: E402


def demo_module() -> Module:
    """本文件的地图夹具模组（内存，含 ``s1`` 的 map 声明）。"""
    return fixture_module()


def party(n: int = 3) -> list:
    return [make_pc(f"角色{i}", role="solo") for i in range(n)]


def scene(mod: Module, sid: str) -> dict:
    return mod.scene_by_id(sid)


def scene_with_map(mod: Module) -> dict:
    return next(s for s in mod.scenes if s.get("map"))


# ================================================================ 状态模型
class TestMapState(unittest.TestCase):
    def setUp(self):
        self.module = demo_module()
        self.state = G.GameState(make_pc("Kyle"), party(2), rng=ScriptRng([]),
                                 module=self.module)
        self.state.scene_id = "s1"
        self.state.map = G.build_map_state(self.module, scene(self.module, "s1"),
                                           self.state.party())

    def test_build_map_for_the_declared_scene(self):
        brief = self.state.map_brief()
        self.assertEqual(brief["scene_id"], "s1")
        self.assertEqual(brief["grid"], [11, 8])
        self.assertEqual(brief["label"], "巷口")
        self.assertEqual(brief["obstacles"], [[5, 3]])
        self.assertEqual(brief["meters_per_cell"], METERS_PER_CELL)
        self.assertIn("npc:salt_officer", brief["tokens"])

    def test_every_token_in_bounds_and_never_shared(self):
        tokens = self.state.map["tokens"]
        w, h = self.state.map_grid()
        cells = []
        for tok in tokens.values():
            x, y = tok["cell"]
            self.assertTrue(0 <= x < w and 0 <= y < h)
            self.assertNotIn((x, y), self.state.map["obstacles"])
            cells.append((x, y))
        self.assertEqual(len(cells), len(set(cells)))

    def test_scene_without_map_is_none(self):
        self.assertIsNone(G.build_map_state(self.module,
                                            scene(self.module, "s2"),
                                            self.state.party()))
        empty = G.GameState(make_pc("Kyle"), [], rng=ScriptRng([]),
                            module=self.module)
        empty.scene_id = "s2"
        empty.map = G.build_map_state(self.module, scene(self.module, "s2"),
                                      empty.party())
        self.assertIsNone(empty.map)
        self.assertIsNone(empty.map_brief())
        self.assertEqual(empty.map_digest(), "")

    def test_move_place_remove(self):
        st = self.state
        self.assertFalse(st.map_move("查无此子", (1, 1))["ok"])
        self.assertTrue(st.map_move("pc:Kyle", (3, 4))["ok"])
        self.assertEqual(st.map["tokens"]["pc:Kyle"]["cell"], [3, 4])
        # 越界 / 障碍 / 已占用
        self.assertFalse(st.map_move("pc:Kyle", (99, 99))["ok"])
        self.assertFalse(st.map_move("pc:Kyle", (5, 3))["ok"])
        self.assertFalse(st.map_move("pc:Kyle", (8, 4))["ok"])   # Salter在那
        self.assertTrue(st.map_place("prop:箱子", "箱子", "prop", (0, 0))["ok"])
        self.assertFalse(st.map_place("prop:箱子2", "箱子2", "prop", (0, 0))["ok"])
        self.assertTrue(st.map_remove("prop:箱子")["ok"])
        self.assertFalse(st.map_remove("prop:箱子")["ok"])

    def test_operations_refused_without_a_map(self):
        st = self.state
        st.map = None
        self.assertFalse(st.map_move("x", (0, 0))["ok"])
        self.assertFalse(st.map_place("x", "x", "prop", (0, 0))["ok"])
        self.assertFalse(st.map_remove("x")["ok"])
        self.assertFalse(st.map_cell_free((0, 0)))

    def test_digest_and_brief(self):
        digest = self.state.map_digest()
        self.assertIn("巷口", digest)
        self.assertIn("1格=2米", digest)
        self.assertIn("Salter", digest)
        self.assertIn("Kyle", digest)
        brief = self.state.map_brief()
        brief["tokens"]["pc:Kyle"]["cell"] = [9, 9]
        self.assertNotEqual(self.state.map["tokens"]["pc:Kyle"]["cell"], [9, 9])

    def test_token_at_and_free_cell(self):
        self.assertEqual(self.state.map_token_at((8, 4)), "npc:salt_officer")
        self.assertIsNone(self.state.map_token_at((0, 7)))
        self.assertTrue(self.state.map_cell_free((0, 7)))
        self.assertFalse(self.state.map_cell_free((8, 4)))


# ================================================================ 存档
class TestMapPersistence(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        import tempfile
        st = make_state()
        sc = scene_with_map(st.module)
        st.scene_id = sc["id"]
        st.map = G.build_map_state(st.module, sc, st.party())
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "game.json"
            G.save_game(st, path)
            back = G.load_game(path, module=st.module)
        self.assertEqual(back.map["label"], st.map["label"])
        self.assertEqual(back.map["tokens"], st.map["tokens"])
        self.assertEqual(back.map["meters_per_cell"], METERS_PER_CELL)

    def test_legacy_save_without_map_loads_as_none(self):
        import tempfile
        st = make_state()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "game.json"
            G.save_game(st, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("map")
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            back = G.load_game(path, module=st.module)
        self.assertIsNone(back.map)

    def test_foreign_ruleset_rejected(self):
        """旧 D&D 5e 存档（没有 ruleset 或值不同）必须拒绝加载。"""
        import tempfile
        st = make_state()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "game.json"
            G.save_game(st, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["ruleset"] = "dnd-5e"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                G.load_game(path, module=st.module)


# ================================================================ 模组校验
class TestModuleValidation(unittest.TestCase):
    def _base(self) -> dict:
        return fixture_module_data()

    def test_fixture_passes_with_no_errors(self):
        errors, warnings = validate_module(self._base())
        self.assertEqual(errors, [])
        self.assertEqual([w for w in warnings if "改进点" in w], [])

    def test_missing_top_field_rejected(self):
        data = self._base()
        data.pop("epilogue")
        errors, _w = validate_module(data)
        self.assertTrue(any("epilogue" in e for e in errors))

    def test_broken_scene_chain_rejected(self):
        data = self._base()
        data["scenes"][0]["next"] = "查无此场"
        errors, _w = validate_module(data)
        self.assertTrue(any("场景链" in e for e in errors))

    def test_orphan_scene_rejected(self):
        data = self._base()
        data["scenes"].append({"id": "orphan", "act": 1, "location": "孤岛",
                               "intro": "", "next": None})
        errors, _w = validate_module(data)
        self.assertTrue(any("孤场景" in e for e in errors))

    def test_unknown_skill_or_out_of_range_dv_rejected(self):
        data = self._base()
        data["scenes"][0]["checks"] = [{"skill": "火球术", "dv": 15}]
        errors, _w = validate_module(data)
        self.assertTrue(any("未知技能" in e for e in errors))
        data = self._base()
        data["scenes"][0]["checks"] = [{"skill": "perception", "dv": 99}]
        errors, _w = validate_module(data)
        self.assertTrue(any("难度值" in e for e in errors))

    def test_unknown_enemy_rejected_and_custom_npc_accepted(self):
        data = self._base()
        data["encounters"]["e1"]["enemies"] = [["幽灵", 1]]
        errors, _w = validate_module(data)
        self.assertTrue(any("未定义的 NPC" in e for e in errors))
        data = self._base()
        data["encounters"]["e1"]["enemies"] = [["salt_ghoul", 1]]
        data["encounters"]["e1"]["spawns"] = {"salt_ghoul": [[8, 3]]}
        errors, _w = validate_module(data)
        self.assertEqual(errors, [])                       # custom_npcs 里的那个

    def test_custom_npc_field_validation(self):
        data = self._base()
        block = data["custom_npcs"]["salt_ghoul"]
        block["stats"]["火"] = 5
        errors, _w = validate_module(data)
        self.assertTrue(any("未定义" in e for e in errors))
        data = self._base()
        data["custom_npcs"]["salt_ghoul"]["weapons"] = ["光剑"]
        errors, _w = validate_module(data)
        self.assertTrue(any("未知武器" in e for e in errors))
        data = self._base()
        data["custom_npcs"]["salt_ghoul"]["armor"] = -3
        errors, _w = validate_module(data)
        self.assertTrue(any("护甲 SP" in e for e in errors))

    def test_pregens_and_default_companions(self):
        data = self._base()
        data["default_companions"] = ["查无此人"]
        errors, _w = validate_module(data)
        self.assertTrue(any("default_companions" in e for e in errors))
        data = self._base()
        data["pregens"]["solo_x"]["role"] = "圣骑士"
        errors, _w = validate_module(data)
        self.assertTrue(any("职业" in e for e in errors))
        data = self._base()
        data["pregens"]["solo_x"]["stats"].pop("emp")
        errors, _w = validate_module(data)
        self.assertTrue(any("十项属性" in e or "emp" in e for e in errors))

    def test_map_image_traversal_rejected(self):
        data = self._base()
        data["scenes"][0]["map"]["image"] = "../secret.jpg"
        errors, _w = validate_module(data)
        self.assertTrue(any("map.image" in e for e in errors))

    def test_map_out_of_bounds_rejected(self):
        data = self._base()
        data["scenes"][0]["map"]["obstacles"] = [[99, 99]]
        errors, _w = validate_module(data)
        self.assertTrue(any("越界" in e for e in errors))
        data = self._base()
        data["scenes"][0]["map"]["party_spawn"] = [99, 99]
        errors, _w = validate_module(data)
        self.assertTrue(any("party_spawn" in e for e in errors))

    def test_map_crop_overflow_rejected(self):
        data = self._base()
        data["scenes"][0]["map"]["crop"] = [0, 0, 99, 99]
        errors, _w = validate_module(data)
        self.assertTrue(any("crop" in e for e in errors))

    def test_unknown_spawn_rejected(self):
        data = self._base()
        data["encounters"]["e1"]["spawns"]["查无此敌"] = [[1, 1]]
        errors, _w = validate_module(data)
        self.assertTrue(any("spawns" in e for e in errors))

    def test_no_ip_warns(self):
        data = self._base()
        for s in data["scenes"]:
            s.pop("ip", None)
        _errors, warnings = validate_module(data)
        self.assertTrue(any("改进点" in w for w in warnings))

    def test_shipped_module_validates_clean(self):
        data = json.loads((MODULES_DIR / RED_MODULE_ID /
                           "module.json").read_text(encoding="utf-8"))
        errors, warnings = validate_module(data)
        self.assertEqual(errors, [])
        self.assertEqual([w for w in warnings if "改进点" in w or "遭遇" in w], [])


# ================================================================ map_update 工具
class TestMapUpdateTool(unittest.TestCase):
    def _tools(self):
        from trpg.game.tools import GameTools
        st = make_state()                                # 夹具模组：s1 有 map
        sc = scene_with_map(st.module)
        st.scene_id = sc["id"]
        st.map = G.build_map_state(st.module, sc, st.party())
        return st, GameTools(st, None)

    def test_place_move_remove_roundtrip(self):
        st, tools = self._tools()
        out = tools._tool_map_update([
            {"op": "place", "id": "npc:lazlo", "name": "Lazlo", "kind": "npc",
             "x": 0, "y": 7},
            {"op": "move", "id": "npc:lazlo", "x": 1, "y": 7},
            {"op": "remove", "id": "npc:lazlo"},
        ])
        self.assertTrue(out["ok"])
        self.assertTrue(all(r.get("ok") for r in out["results"]))
        self.assertNotIn("npc:lazlo", st.map["tokens"])

    def test_icon_comes_from_module_token_icons(self):
        st, tools = self._tools()
        st.module.token_icons = {"salt_officer": "assets/tokens/salt.png"}
        out = tools._tool_map_update([
            {"op": "place", "id": "npc:salt_officer", "name": "Salter",
             "kind": "npc", "x": 0, "y": 6}])
        self.assertTrue(out["ok"])
        self.assertEqual(st.map["tokens"]["npc:salt_officer"]["icon"],
                         "assets/tokens/salt.png")

    def test_label_op_and_unknown_op(self):
        st, tools = self._tools()
        self.assertTrue(tools._tool_map_update(
            [{"op": "label", "text": "雨夜巷口"}])["ok"])
        self.assertEqual(st.map["label"], "雨夜巷口")
        out = tools._tool_map_update([{"op": "传送到", "id": "x"}])
        self.assertFalse(out["ok"])
        self.assertIn("未知操作", out["results"][0]["error"])

    def test_bad_ops_shape(self):
        _st, tools = self._tools()
        self.assertFalse(tools._tool_map_update("移动")["ok"])
        self.assertFalse(tools._tool_map_update([{"op": "move", "id": "x"}])["ok"])

    def test_refused_during_combat(self):
        from trpg.game.tools import GameTools
        st, tools = self._tools()
        tools.encounter = object()
        out = tools._tool_map_update([{"op": "label", "text": "x"}])
        self.assertFalse(out["ok"])
        self.assertIn("战斗", out["error"])

    def test_refused_without_a_map(self):
        from trpg.game.tools import GameTools
        st = make_state()
        st.map = None
        tools = GameTools(st, None)
        out = tools._tool_map_update([{"op": "label", "text": "x"}])
        self.assertFalse(out["ok"])
        self.assertIn("地图", out["error"])


# ================================================================ 资产路由
class TestAssetRoute(unittest.TestCase):
    """``/api/asset/<module_id>/<rel>`` 只服务模组目录下的图片；穿越必须 404。"""

    def setUp(self):
        from trpg.web import server as web_server
        self._tmp = None
        # 为了不写仓库 modules/，把资产路由的根指向一个临时 modules/ 目录
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tmp.name)
        (Path("modules") / RED_MODULE_ID / "assets" / "maps").mkdir(parents=True)
        (Path("modules") / RED_MODULE_ID / "module.json").write_text(
            "{}", encoding="utf-8")
        (Path("modules") / RED_MODULE_ID / "assets" / "maps" /
         "road.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        self.server = web_server

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _handler(self, rel: str):
        answers: dict = {}

        class H(self.server.Handler):
            def __init__(self):                       # 绕过 socket 直接调路由
                self.wfile = type("W", (), {
                    "write": staticmethod(lambda b: answers.setdefault("body", b))})()

            def send_response(self, code, message=None):
                answers["status"] = code

            def send_header(self, *a):
                pass

            def end_headers(self):
                pass

            def _json(self, obj, status=200):
                answers["status"] = status
                answers["json"] = obj

        h = H()
        h._asset(rel)
        return answers

    def test_serves_module_image(self):
        out = self._handler(f"{RED_MODULE_ID}/assets/maps/road.jpg")
        self.assertEqual(out.get("status"), 200)
        self.assertTrue(out["body"].startswith(b"\xff\xd8\xff"))

    def test_traversal_and_bad_extension_blocked(self):
        payloads = [f"{RED_MODULE_ID}/../main.py",
                    f"{RED_MODULE_ID}/assets/../../main.py",
                    "..%2Fmain.py",
                    RED_MODULE_ID,
                    "bad mod/assets/maps/x.jpg",
                    f"{RED_MODULE_ID}/assets/maps/road.txt"]
        for rel in payloads:
            out = self._handler(rel)
            self.assertEqual(out.get("status"), 404, rel)


if __name__ == "__main__":
    unittest.main()
