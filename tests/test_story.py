"""剧本模式（互动剧）：图校验、结局可达性、引擎演绎、结局图鉴、Web 端到端。

剧本数据与 Godot 版共享同一份 JSON（放在 ``modules/<id>/story_*.json``），
节点语义见 ``trpg/game/story.py`` 的模块文档。引擎本身与规则无关，只有
检定口径是 RED 的（``1d10 + 属性 + 技能`` vs 难度值 DV，必须**高于** DV）。

仓库内置的剧本是 ``modules/cpred-easy-mode/story_wyskakuj.json``（《抢钱行动》）；
本文件不依赖它，而是用 ``helpers.story_data()`` 造一份自包含的 RED 剧本夹具，
并把 Web 端到端跑在**临时 cordis 根**上（夹具固定 4 结局，与内置剧本解耦）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (ScriptedUI, WebServerCase, story_data,  # noqa: E402
                     story_service_data, temp_cordis_root)

from trpg.game.story import (StorySession, ending_ids, load_stories,  # noqa: E402
                              load_story_file, load_unlocks, reachable_endings,
                              record_unlock, validate_story)

STORY_ID = "red-fixture"


def ending_id_of(story: dict, ending: dict) -> str:
    """从引擎返回的结局节点反查它的 id（见下方 expectedFailure 用例的说明）。"""
    for nid, node in story.get("nodes", {}).items():
        if isinstance(node, dict) and node.get("ending") and node is ending:
            return str(nid)
    # 退回内容匹配
    for nid, node in story.get("nodes", {}).items():
        if isinstance(node, dict) and node.get("ending") \
                and node.get("title") == ending.get("title") \
                and node.get("text") == ending.get("text"):
            return str(nid)
    return ""


# ================================================================ 数据校验
class TestStoryData(unittest.TestCase):
    def test_fixture_validates_clean(self):
        story = story_data()
        self.assertEqual(validate_story(story), [])
        self.assertEqual(len(ending_ids(story)), 4)
        self.assertEqual(story["endings_total"], 4)
        self.assertEqual(story["death_ending"], "end_dead")

    def test_all_endings_reachable(self):
        story = story_data()
        self.assertEqual(sorted(reachable_endings(story)),
                         sorted(ending_ids(story)))

    def test_broken_story_is_rejected(self):
        errors = validate_story(story_service_data())
        self.assertTrue(any("start" in e for e in errors))
        self.assertTrue(any("悬空引用" in e for e in errors))

    def test_unknown_skill_and_bad_dv_rejected(self):
        story = story_data()
        story["nodes"]["n_start"]["choices"][2]["check"]["skill"] = "火球术"
        self.assertTrue(any("未知技能" in e for e in validate_story(story)))
        story = story_data()
        story["nodes"]["n_start"]["choices"][2]["check"]["dv"] = 99
        self.assertTrue(any("难度值" in e for e in validate_story(story)))

    def test_ending_count_mismatch_rejected(self):
        story = story_data()
        story["endings_total"] = 9
        self.assertTrue(any("结局数" in e for e in validate_story(story)))

    def test_load_story_file_and_load_stories(self):
        with temp_cordis_root(story_data()) as root:
            story, errors = load_story_file(
                root / "cpred-easy-mode" / "story_fixture.json")
            self.assertEqual(errors, [])
            self.assertEqual(story["id"], STORY_ID)
            stories, errors = load_stories(root)
            self.assertEqual(errors, [])
            self.assertEqual([s["id"] for s in stories], [STORY_ID])
            self.assertEqual(stories[0]["_module_id"], "cpred-easy-mode")

    def test_load_stories_reports_broken_files(self):
        with temp_cordis_root(story_service_data()) as root:
            stories, errors = load_stories(root)
            self.assertEqual(stories, [])
            self.assertTrue(errors)


# ================================================================ 引擎
class TestStoryEngine(unittest.TestCase):
    def _run(self, policy: str, hero_index: int = 0, seed: int = 42,
             story: dict | None = None):
        import random
        data = story or story_data()
        ui = ScriptedUI(policy=policy)
        session = StorySession(data, ui, rng=random.Random(seed))
        session.set_hero(hero_index)
        ending = session.run()
        return ending, session, ui

    def test_playthrough_reaches_an_ending(self):
        ending, session, ui = self._run("first")
        self.assertTrue(ending.get("ending"))
        self.assertIn(ending_id_of(story_data(), ending),
                      ending_ids(story_data()))
        self.assertLess(session.steps, 200)
        self.assertGreater(len(ui.check_lines), 0)           # 走过检定分支

    def test_checks_are_red_dice(self):
        _ending, _session, ui = self._run("first")
        for payload in ui.check_lines:
            self.assertEqual(payload["expr"], "1d10")
            self.assertIn("DV", payload["detail"])
            # mods 的前两条是挂钩属性与技能（RED 审计口径）
            self.assertGreaterEqual(len(payload["mods"]), 2)
            self.assertIn("+", payload["mods"][0])
            self.assertIn("+", payload["mods"][1])
            self.assertLessEqual(len(payload["rolls"]), 2)   # 暴击最多两颗

    def test_both_policies_and_heroes_produce_multiple_endings(self):
        story = story_data()
        seen = set()
        for policy in ("first", "last"):
            for hero in (0, 1):
                ending, _s, _u = self._run(policy, hero_index=hero, seed=7,
                                           story=story)
                seen.add(ending_id_of(story, ending))
        self.assertGreaterEqual(len(seen), 2)

    def test_same_seed_same_ending(self):
        story = story_data()
        a = self._run("first", seed=11, story=story)[0]
        b = self._run("first", seed=11, story=story)[0]
        self.assertEqual(ending_id_of(story, a), ending_id_of(story, b))
        self.assertEqual(a["title"], b["title"])

    def test_ending_carries_its_node_id(self):
        """生产缺陷（trpg/game/story.py:332-333）：``StorySession.run`` 在结局
        节点上直接 ``return node``，而 ``node`` 是 ``nodes[id]`` 的值本身、**不带
        自己的 id**。于是 ``web/server.py:390-399`` 里
        ``ending.get("id", "")`` 永远得到空串：
          * ``game_over.ending.id`` 恒为 ""（前端拿不到结局身份）；
          * ``record_unlock(story_id, "")`` 因空 id 直接 return（story.py:226），
            结局图鉴永远不解锁。
        最小复现：
            s = StorySession(story_data(), ScriptedUI()); s.set_hero(0)
            s.run().get("id")            # -> None，应为 "end_plain"
        修复后本用例会变成 unexpected success。"""
        ending, _session, _ui = self._run("first")
        self.assertIn(ending.get("id"), ending_ids(story_data()))

    def test_flag_gating_on_know_chest(self):
        """没看过箱子 → 走 end_plain；看过 → 走 end_wise。"""
        import random

        class WalkUI(ScriptedUI):
            """直接走 → 装作没看见：不碰箱子。"""

            def choose(self, prompt, options):
                for key in ("直接走", "装作没看见"):
                    for i, o in enumerate(options):
                        if key in o:
                            return i
                return 0

        class PeekUI(ScriptedUI):
            """先看箱子（写下 know_chest）→ 装作没看见。"""

            def choose(self, prompt, options):
                for key in ("先看箱子", "装作没看见"):
                    for i, o in enumerate(options):
                        if key in o:
                            return i
                return 0

        story = story_data()
        session = StorySession(story, WalkUI(), rng=random.Random(5))
        session.set_hero(0)
        self.assertEqual(ending_id_of(story, session.run()), "end_plain")

        story = story_data()
        session = StorySession(story, PeekUI(), rng=random.Random(5))
        session.set_hero(0)
        ending = session.run()
        self.assertEqual(session.flags.get("know_chest"), "1")
        self.assertEqual(ending_id_of(story, ending), "end_wise")

    def test_hp_zero_routes_to_the_death_ending(self):
        story = story_data()
        story["nodes"]["n_start"]["choices"] = [
            {"text": "硬闯", "damage": 999, "goto": "end_good"}]
        import random
        ui = ScriptedUI(policy="first")
        session = StorySession(story, ui, rng=random.Random(1))
        session.set_hero(0)
        ending = session.run()
        self.assertEqual(ending_id_of(story, ending), "end_dead")
        self.assertEqual(session.hp, 0)

    def test_hero_index_out_of_range(self):
        session = StorySession(story_data(), ScriptedUI(), rng=None)
        with self.assertRaises(ValueError):
            session.set_hero(9)

    def test_board_uses_pw_and_wound_terms(self):
        _ending, _session, ui = self._run("first")
        board = "\n".join(" ".join(lines) for lines in ui.boards)
        self.assertIn("PW", board)
        self.assertNotIn("HP ", board)
        self.assertNotIn("AC", board)


# ================================================================ 结局图鉴
class TestUnlocks(unittest.TestCase):
    def test_roundtrip_dedupe_and_empty_ids(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "endings.json"
            self.assertEqual(load_unlocks(path), {})
            record_unlock(STORY_ID, "end_good", path)
            record_unlock(STORY_ID, "end_good", path)
            record_unlock(STORY_ID, "end_wise", path)
            record_unlock(STORY_ID, "", path)                # 空 id 忽略
            record_unlock("", "end_good", path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data[STORY_ID], ["end_good", "end_wise"])
            self.assertEqual(load_unlocks(path)[STORY_ID],
                             ["end_good", "end_wise"])
            self.assertEqual(len(data), 1)

    def test_corrupt_file_is_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "endings.json"
            path.write_text("{ 不是 json", encoding="utf-8")
            self.assertEqual(load_unlocks(path), {})


# ================================================================ Web 端到端
class TestStoryWeb(WebServerCase):
    """真实 HTTP 服务 + 临时 cordis 根（含一份 RED 剧本）的整场演绎。"""

    story = None            # 由 setUp 之前注入（见 _make_story）

    def setUp(self):
        self.__class__.story = story_data()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.__class__.story = None

    def test_stories_payload(self):
        status, body = self.get("/api/stories")
        self.assertEqual(status, 200)
        self.assertEqual(body["errors"], [])
        self.assertEqual(len(body["stories"]), 1)
        entry = body["stories"][0]
        self.assertEqual(entry["story_id"], STORY_ID)
        self.assertEqual(entry["module_id"], "cpred-easy-mode")
        self.assertEqual(entry["endings_total"], 4)
        self.assertEqual(len(entry["endings"]), 4)
        self.assertEqual(len(entry["heroes"]), 2)
        self.assertEqual(entry["unlocked"], [])

    def test_unknown_story_id_rejected(self):
        status, body = self.post("/api/story/start", {"story_id": "查无此剧本"})
        self.assertEqual(status, 400)
        self.assertIn("不存在", body["error"])

    def test_state_exposes_pending_in_story_mode(self):
        """回归：剧本模式的 /api/state 也必须给 pending。

        快照此前只给跑团对局附带 pending，剧本模式恒为 None——刷新页面时
        前端就只能靠 SSE 历史重放重建选项。同时断言「作答后立刻推进到下一问」：
        剧本引擎是毫秒级的，前端据此（而非超时）清掉"DM 裁定中"。
        """
        status, body = self.post("/api/story/start",
                                 {"story_id": STORY_ID, "hero_index": 0, "seed": 7})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["state"]["mode"], "story")

        pending = None
        deadline = time.time() + 10
        while time.time() < deadline:
            status, state = self.get("/api/state")
            pending = state.get("pending")
            if pending:
                break
            time.sleep(0.02)
        self.assertIsNotNone(pending, "剧本模式应当有待答问题")
        self.assertEqual(pending["kind"], "choose")
        self.assertTrue(pending["options"])

        status, _ = self.post("/api/answer", {"index": 0})
        self.assertEqual(status, 200)
        deadline = time.time() + 10
        while time.time() < deadline:
            asks = [e for e in self.app.ui.history if e["t"] == "ask"]
            over = [e for e in self.app.ui.history if e["t"] == "game_over"]
            if len(asks) >= 2 or over:
                break
            time.sleep(0.02)
        asks = [e for e in self.app.ui.history if e["t"] == "ask"]
        over = [e for e in self.app.ui.history if e["t"] == "game_over"]
        self.assertTrue(len(asks) >= 2 or over,
                        "作答后应当推进到下一问或直接结束，而不是停在原地")

    def test_full_story_flow_over_http(self):
        status, body = self.post("/api/story/start",
                                 {"story_id": STORY_ID, "hero_index": 0,
                                  "seed": 42})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["state"]["started"])
        self.assertEqual(body["state"]["mode"], "story")
        self.assertEqual(body["state"]["story"]["hero"]["name"], "Kyle")
        # 同一时间只能有一局
        status, _ = self.post("/api/story/start", {"story_id": STORY_ID})
        self.assertEqual(status, 400)

        deadline = time.time() + 30
        steps = 0
        while time.time() < deadline and steps < 400:
            steps += 1
            over = [e for e in self.app.ui.history if e["t"] == "game_over"]
            if over:
                break
            status, pb = self.get("/api/pending")
            pending = pb.get("pending")
            if pending is None:
                if not (self.app.thread and self.app.thread.is_alive()):
                    break
                time.sleep(0.01)
                continue
            self.assertEqual(pending["kind"], "choose")
            idx = _pick(pending["options"])
            status, _ans = self.post("/api/answer", {"index": idx})
            self.assertEqual(status, 200)
        else:
            self.fail("剧本在时间/步数内未结束")

        over = next(e for e in self.app.ui.history if e["t"] == "game_over")
        self.assertEqual(over["reason"], "story_ended")
        self.assertEqual(over["endings_total"], 4)
        self.assertEqual(over["ending"]["title"], "路人")     # end_plain 的标题
        self.assertTrue(over["ending"]["text"])
        # 结束后 /api/state 仍停在剧本模式
        status, state = self.get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["mode"], "story")
        self.assertEqual(state["story"]["id"], STORY_ID)

    def test_ending_identity_and_unlock_persisted(self):
        """生产缺陷（trpg/game/story.py:332 + trpg/web/server.py:390-399）：
        引擎返回的结局节点不带 id → ``game_over.ending.id`` 恒为 ""，
        ``record_unlock(story_id, "")`` 又因空 id 早退（story.py:226），
        于是「结局图鉴」永远不解锁、``saves/story_endings.json`` 永远为空。
        修复后本用例会变成 unexpected success。"""
        self.post("/api/story/start", {"story_id": STORY_ID, "hero_index": 0,
                                       "seed": 42})
        deadline = time.time() + 30
        while time.time() < deadline:
            over = [e for e in self.app.ui.history if e["t"] == "game_over"]
            if over:
                break
            _s, pb = self.get("/api/pending")
            pending = pb.get("pending")
            if pending is None:
                time.sleep(0.01)
                continue
            self.post("/api/answer", {"index": _pick(pending["options"])})
        over = next(e for e in self.app.ui.history if e["t"] == "game_over")
        self.assertEqual(over["ending"]["id"], "end_plain")
        self.assertIn("end_plain", over["unlocked"])
        self.assertIn("end_plain", load_unlocks(self.unlocks_path)[STORY_ID])


def _pick(options: list[str]) -> int:
    """剧本代打策略：先「直接走」再「装作没看见」→ 稳定拿到 end_plain。"""
    for key in ("直接走", "装作没看见"):
        for i, o in enumerate(options):
            if key in o:
                return i
    for i, o in enumerate(options):          # 兜底：不选带检定的分支
        if "检定" not in o:
            return i
    return 0


if __name__ == "__main__":
    unittest.main()
