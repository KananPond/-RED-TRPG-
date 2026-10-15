"""骰子系统测试（《赛博朋克 RED》）。

覆盖：
- ``parse_expr`` / ``roll`` 的通用骰式解析与可复现性（保留自旧版）；
- ``check_roll``：1d10 + 修正 对照难度值 DV，**严格高于** DV 才算成功（平手防守方胜）；
- **暴击成功**：d10 掷出 10 → 追加一颗 d10 并相加；
- **暴击失败**：d10 掷出 1 → 追加一颗 d10 并相减（各只追加一次，不连锁）；
- ``breakdown`` 审计明细：属性 / 技能 / 修正三段的可核对契约（前端检定卡依赖它）；
- ``opposed_roll`` 对抗检定与 ``count_faces``（致命伤判定的 6 点数）。
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import ScriptRng                      # noqa: E402

from trpg.rules import data as D                  # noqa: E402
from trpg.rules.dice import (DiceResult, check_roll, count_faces, d10,  # noqa: E402
                              damage_roll, opposed_roll, parse_expr, roll)


class TestParseAndRoll(unittest.TestCase):
    """骰式解析与通用掷骰（规则无关）。"""

    def test_parse_expr(self):
        self.assertEqual(parse_expr("2d6+3"), (2, 6, 3))
        self.assertEqual(parse_expr("d10"), (1, 10, 0))
        self.assertEqual(parse_expr("1d4-1"), (1, 4, -1))
        self.assertEqual(parse_expr(" 3D6 "), (3, 6, 0))
        with self.assertRaises(ValueError):
            parse_expr("2x6")
        with self.assertRaises(ValueError):
            parse_expr("100d6")
        with self.assertRaises(ValueError):
            parse_expr("1d0")

    def test_roll_range(self):
        rng = random.Random(42)
        for _ in range(200):
            r = roll(rng, "2d6+3")
            self.assertTrue(5 <= r.total <= 15)
            self.assertEqual(len(r.rolls), 2)
            self.assertEqual(r.kept, r.rolls)
            self.assertEqual(r.total, sum(r.rolls) + r.modifier)

    def test_determinism(self):
        a = [roll(random.Random(9), "3d6").total for _ in range(5)]
        b = [roll(random.Random(9), "3d6").total for _ in range(5)]
        self.assertEqual(a, b)

    def test_d10_is_single_die(self):
        rng = random.Random(3)
        for _ in range(100):
            self.assertTrue(1 <= d10(rng) <= 10)

    def test_count_faces_for_critical_injury(self):
        """致命伤判定靠"这次伤害骰里有几个 6"。"""
        res = DiceResult(expr="4d6", rolls=[6, 2, 6, 1], kept=[6, 2, 6, 1],
                         total=15)
        self.assertEqual(count_faces(res, 6), 2)
        self.assertEqual(count_faces(res, 1), 1)

    def test_damage_roll_does_not_alter_dice(self):
        rng = random.Random(5)
        res = damage_roll(rng, "3d6")
        self.assertEqual(len(res.rolls), 3)
        self.assertEqual(res.total, sum(res.rolls))
        self.assertIsNone(res.dv)
        self.assertIsNone(res.success)


class TestCheckRoll(unittest.TestCase):
    """技能检定：1d10 + 修正 vs DV（RED 第 16-17 页）。"""

    def test_total_and_strictly_greater_than_dv(self):
        res = check_roll(ScriptRng([7]), 8, dv=15)
        self.assertEqual(res.rolls, [7])
        self.assertEqual(res.total, 15)          # 7 + 8
        self.assertFalse(res.success)            # 平手 ⇒ 防守方胜
        res2 = check_roll(ScriptRng([8]), 8, dv=15)
        self.assertEqual(res2.total, 16)
        self.assertTrue(res2.success)

    def test_dv_none_skips_verdict(self):
        res = check_roll(ScriptRng([4]), 3)
        self.assertEqual(res.total, 7)
        self.assertIsNone(res.dv)
        self.assertIsNone(res.success)

    def test_crit_success_adds_extra_d10(self):
        """掷出 10 → 追加一颗 d10 并**相加**。"""
        res = check_roll(ScriptRng([10, 6]), 5, dv=15)
        self.assertTrue(res.crit_success)
        self.assertFalse(res.crit_failure)
        self.assertEqual(res.rolls, [10, 6])
        self.assertEqual(res.kept, [10, 6])
        self.assertEqual(res.total, 10 + 6 + 5)   # 21
        self.assertTrue(res.success)
        self.assertIn("暴击", " ".join(res.breakdown))

    def test_crit_failure_subtracts_extra_d10(self):
        """掷出 1 → 追加一颗 d10 并**相减**。"""
        res = check_roll(ScriptRng([1, 8]), 6, dv=13)
        self.assertTrue(res.crit_failure)
        self.assertFalse(res.crit_success)
        self.assertEqual(res.rolls, [1, 8])
        self.assertEqual(res.total, 1 - 8 + 6)    # -1
        self.assertFalse(res.success)
        self.assertIn("暴击失败", " ".join(res.breakdown))

    def test_crit_does_not_chain(self):
        """再掷出 10 / 1 都不再追加第三颗（各只追加一次）。"""
        up = check_roll(ScriptRng([10, 10, 3]), 0)
        self.assertEqual(up.rolls, [10, 10])
        self.assertEqual(len(ScriptRng([10, 10, 3]).values), 3)  # 队列里还剩一颗
        down = check_roll(ScriptRng([1, 1]), 0)
        self.assertEqual(down.rolls, [1, 1])
        self.assertEqual(down.total, 0)

    def test_crit_faces_follow_data_table(self):
        self.assertEqual(D.CHECK_CRIT_SUCCESS_FACES, 10)
        self.assertEqual(D.CHECK_CRIT_FAILURE_FACES, 1)

    def test_luck_adds_one_per_point(self):
        base = check_roll(ScriptRng([5]), 4, dv=11)
        lucky = check_roll(ScriptRng([5]), 4, dv=11, luck=3)
        self.assertEqual(lucky.total, base.total + 3)
        self.assertEqual(lucky.luck_spent, 3)
        self.assertTrue(lucky.success)            # 12 > 11
        self.assertFalse(base.success)            # 9 vs 11
        self.assertIn("幸运", " ".join(lucky.breakdown))

    def test_bonus_covers_complementary_and_extra_time(self):
        res = check_roll(ScriptRng([5]), 4, dv=13, bonus=2)
        self.assertEqual(res.total, 11)
        self.assertIn("辅助/加时", " ".join(res.breakdown))

    def test_difficulty_table_matches_easy_mode(self):
        self.assertEqual(D.DIFFICULTY[13]["name"], "日常")
        self.assertEqual(D.DIFFICULTY[15]["name"], "困难")
        self.assertEqual(D.DIFFICULTY[17]["name"], "专业")
        self.assertEqual(D.DIFFICULTY[21]["name"], "英雄")
        self.assertEqual(D.DIFFICULTY[24]["name"], "不可思议")
        self.assertEqual((D.DV_MIN, D.DV_MAX), (9, 30))


class TestBreakdownAudit(unittest.TestCase):
    """``breakdown`` 是前端检定卡的审计契约：属性 + 技能 + 修正 三段可核对。

    移植前 5e 版的 "mods 之和 == total − 骰值" 不变量在 RED 下的正确形式是：
    前两条是**加值构成**（属性值 + 技能等级），第三条是**净修正**（= 属性 + 技能
    + 伤势等），因此 ``数值(前两条之和) == 数值(第三条) == total − 掷骰``。
    """

    @staticmethod
    def _num(part: str) -> int:
        """从 '反应+6' / '+0' / '-2（伤势）' 里取出带符号整数。"""
        import re
        m = re.search(r"[+-]\d+", part)
        assert m, f"明细里没有可解析的数值：{part!r}"
        return int(m.group(0))

    def test_labels_name_stat_and_skill(self):
        from trpg.rules.character import Character
        c = Character.create(
            name="Kyle", role="solo",
            stats={"int": 5, "ref": 6, "dex": 6, "tech": 4, "cool": 5,
                   "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 6},
            skill_levels={"handgun": 6})
        res = c.check(ScriptRng([7]), "handgun", dv=15)
        self.assertGreaterEqual(len(res.breakdown), 3)
        info = D.SKILLS["handgun"]
        self.assertIn(D.STATS[info["stat"]]["name"], res.breakdown[0])
        self.assertEqual(self._num(res.breakdown[0]), c.stat(info["stat"]))
        self.assertIn(info["name"], res.breakdown[1])
        self.assertEqual(self._num(res.breakdown[1]), c.skill("handgun"))

    def test_flat_modifier_entry_equals_total_minus_die(self):
        from trpg.rules.character import Character
        c = Character.create(
            name="Kyle", role="solo",
            stats={"int": 5, "ref": 6, "dex": 6, "tech": 4, "cool": 5,
                   "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 6},
            skill_levels={"handgun": 6})
        for face in (3, 7, 10):
            res = c.check(ScriptRng([face, 4]), "handgun", dv=15)
            first = res.rolls[0]
            extra = res.total - first - self._num(res.breakdown[2])
            # 第三条（净修正）之后的追加项恰好等于暴击追加量
            self.assertEqual(sum(self._num(p) for p in res.breakdown[3:]),
                             extra if res.crit_success or res.crit_failure else 0)

    def test_breakdown_sums_to_total_without_crit(self):
        from trpg.rules.character import Character
        c = Character.create(
            name="Kyle", role="solo",
            stats={"int": 5, "ref": 6, "dex": 6, "tech": 4, "cool": 5,
                   "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 6},
            skill_levels={"handgun": 6})
        res = c.check(ScriptRng([7]), "handgun", dv=15)
        flat = self._num(res.breakdown[2])
        self.assertEqual(self._num(res.breakdown[0])
                         + self._num(res.breakdown[1]), flat)
        self.assertEqual(res.total - res.rolls[0], flat)

    def test_detail_renders_dv_verdict(self):
        res = check_roll(ScriptRng([7]), 8, dv=15)
        text = res.detail()
        self.assertIn("1d10(7)", text)
        self.assertIn("vs DV 15", text)
        self.assertIn("失败", text)
        ok = check_roll(ScriptRng([8]), 8, dv=15)
        self.assertIn("成功", ok.detail())

    def test_describe_marks_critical(self):
        res = check_roll(ScriptRng([10, 3]), 2)
        self.assertIn("暴击成功", res.describe())
        bad = check_roll(ScriptRng([1, 3]), 2)
        self.assertIn("暴击失败", bad.describe())


class TestOpposedRoll(unittest.TestCase):
    """对抗检定：双方各掷 1d10 + 修正，攻击方必须**高于**防守方。"""

    def test_defender_wins_ties(self):
        atk, dfn, win = opposed_roll(ScriptRng([5, 5]), 4, 4)
        self.assertEqual(atk.total, dfn.total)
        self.assertFalse(win)                     # 平手 ⇒ 防守方胜
        self.assertEqual(atk.dv, dfn.total)

    def test_attacker_wins_when_higher(self):
        atk, dfn, win = opposed_roll(ScriptRng([8, 3]), 4, 4)
        self.assertTrue(win)
        self.assertEqual(atk.total, 12)
        self.assertEqual(dfn.total, 7)

    def test_luck_only_for_attacker(self):
        atk, dfn, _win = opposed_roll(ScriptRng([2, 2]), 1, 1, luck=3)
        self.assertEqual(atk.total, 6)
        self.assertEqual(dfn.total, 3)


if __name__ == "__main__":
    unittest.main()
