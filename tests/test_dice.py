"""骰子系统测试。"""

import random
import unittest

from ctrcg.rules.dice import check_roll, d20, parse_expr, roll, roll_scores


class TestDice(unittest.TestCase):
    def test_parse_expr(self):
        self.assertEqual(parse_expr("2d6+3"), (2, 6, 3))
        self.assertEqual(parse_expr("d20"), (1, 20, 0))
        self.assertEqual(parse_expr("1d4-1"), (1, 4, -1))
        with self.assertRaises(ValueError):
            parse_expr("2x6")
        with self.assertRaises(ValueError):
            parse_expr("100d6")

    def test_roll_range(self):
        rng = random.Random(42)
        for _ in range(200):
            r = roll(rng, "2d6+3")
            self.assertTrue(5 <= r.total <= 15)
            self.assertEqual(len(r.rolls), 2)

    def test_d20_advantage_keeps_higher(self):
        rng = random.Random(7)
        for _ in range(100):
            r = d20(rng, 1)
            self.assertEqual(r.kept[0], max(r.rolls))
            self.assertEqual(len(r.rolls), 2)
        for _ in range(100):
            r = d20(rng, -1)
            self.assertEqual(r.kept[0], min(r.rolls))

    def test_check_roll_total(self):
        rng = random.Random(1)
        r = check_roll(rng, 5)
        self.assertEqual(r.total, r.kept[0] + 5)

    def test_determinism(self):
        a = [roll(random.Random(9), "3d6").total for _ in range(5)]
        b = [roll(random.Random(9), "3d6").total for _ in range(5)]
        self.assertEqual(a, b)

    def test_roll_scores(self):
        """4d6 去最低 × 6：范围 3-18、降序、可复现。"""
        rng = random.Random(42)
        for _ in range(200):
            vals = roll_scores(rng)
            self.assertEqual(len(vals), 6)
            self.assertEqual(vals, sorted(vals, reverse=True))
            self.assertTrue(all(3 <= v <= 18 for v in vals))
        a = roll_scores(random.Random(3))
        b = roll_scores(random.Random(3))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
