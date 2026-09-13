"""骰子系统：表达式解析、d20 优势/劣势、检定。所有随机性经由注入的 RNG，
保证可复现、可测试。"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

_DIE_RE = re.compile(r"(\d*)d(\d+)")


@dataclass
class DiceResult:
    """一次掷骰的完整记录（审计用：模型可见 ⟺ 已记录）。"""

    expr: str
    rolls: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0

    def describe(self) -> str:
        rolls = ",".join(str(r) for r in self.rolls)
        kept = "+".join(str(r) for r in self.kept) if self.kept else "0"
        sign = f"{self.modifier:+d}" if self.modifier else ""
        adv = ""
        if len(self.rolls) > len(self.kept):
            adv = "（优势）" if self.kept and self.kept[0] == max(self.rolls[:2]) else "（劣势）"
        return f"[{self.expr}: {rolls}{adv} → {kept}{sign} = {self.total}]"


def parse_expr(expr: str) -> tuple[int, int, int]:
    """解析 '2d6+3' / 'd8' / '1d4-1' → (count, sides, modifier)。"""
    expr = expr.strip().replace(" ", "").lower()
    m = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", expr)
    if not m:
        raise ValueError(f"非法骰子表达式: {expr!r}")
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3) or 0)
    if not (1 <= count <= 20 and 2 <= sides <= 100):
        raise ValueError(f"骰子规模超出限制: {expr!r}")
    return count, sides, mod


def roll(rng: random.Random, expr: str) -> DiceResult:
    """掷 'XdY+Z'，返回明细。"""
    count, sides, mod = parse_expr(expr)
    rolls = [rng.randint(1, sides) for _ in range(count)]
    return DiceResult(expr=expr, rolls=rolls, kept=rolls, modifier=mod,
                      total=sum(rolls) + mod)


ADVANTAGE_NONE, ADVANTAGE, DISADVANTAGE = 0, 1, -1


def d20(rng: random.Random, advantage: int = ADVANTAGE_NONE) -> DiceResult:
    """d20 检定。优势/劣势同时存在时互相抵消（5e 规则）。"""
    if advantage == ADVANTAGE:
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        kept, rolls = max(a, b), [a, b]
    elif advantage == DISADVANTAGE:
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        kept, rolls = min(a, b), [a, b]
    else:
        kept = rng.randint(1, 20)
        rolls = [kept]
    return DiceResult(expr="d20", rolls=rolls, kept=[kept], modifier=0, total=kept)


def check_roll(rng: random.Random, modifier: int, advantage: int = ADVANTAGE_NONE) -> DiceResult:
    """d20 + 调整值的检定（属性/技能/豁免/死亡豁免共用）。"""
    res = d20(rng, advantage)
    res.modifier = modifier
    res.total = res.kept[0] + modifier
    return res


def roll_scores(rng: random.Random, count: int = 6) -> list[int]:
    """4d6 去最低 × count（D&D/BG3 式掷骰法）。降序返回。"""
    out = []
    for _ in range(count):
        ks = sorted((rng.randint(1, 6) for _ in range(4)), reverse=True)
        out.append(sum(ks[:3]))
    return sorted(out, reverse=True)
