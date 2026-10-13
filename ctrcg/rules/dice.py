"""骰子系统：《赛博朋克 RED》技能检定与伤害掷骰。

所有随机性经由注入的 :class:`random.Random`，保证可复现、可测试。

核心口径（轻松模式第 16-17 页）：

* 技能检定 = 属性 + 技能等级 + 1d10，与难度值（PT/DV）比较；
  **高于** DV 才成功（平手时防守方获胜）。
* 暴击成功：1d10 掷出 10，追加一个 1d10 并相加；再掷出 10 则不再追加。
* 暴击失败：1d10 掷出 1，追加一个 1d10 并从总和中减去；再掷出 1 则不再追加。
* 幸运：掷骰前可投入幸运池点数，每点 +1。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from . import data as D

_CRIT_FACES = D.CHECK_CRIT_SUCCESS_FACES
_FUMBLE_FACES = D.CHECK_CRIT_FAILURE_FACES


@dataclass
class DiceResult:
    """一次掷骰的完整记录（审计用：模型可见 ⟺ 已记录）。"""

    expr: str
    rolls: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0
    # --- 检定专用（伤害掷骰保持默认 None） ---
    dv: int | None = None
    success: bool | None = None
    crit_success: bool = False
    crit_failure: bool = False
    luck_spent: int = 0
    breakdown: list[str] = field(default_factory=list)

    def describe(self) -> str:
        rolls = ",".join(str(r) for r in self.rolls)
        kept = "+".join(str(r) for r in self.kept) if self.kept else "0"
        sign = f"{self.modifier:+d}" if self.modifier else ""
        tag = ""
        if self.crit_success:
            tag = "（暴击成功）"
        elif self.crit_failure:
            tag = "（暴击失败）"
        return f"[{self.expr}: {rolls}{tag} → {kept}{sign} = {self.total}]"

    def detail(self) -> str:
        """人类可读的加值拆解，如 ``1d10 + 7 = 16 vs DV 15 → 成功``。"""
        parts = " ".join(self.breakdown) if self.breakdown else f"{self.modifier:+d}"
        out = f"1d10({self.rolls[0] if self.rolls else '?'}) {parts} = {self.total}"
        if self.dv is not None:
            out += f" vs DV {self.dv} → {'成功' if self.success else '失败'}"
        return out


def parse_expr(expr: str) -> tuple[int, int, int]:
    """解析 '2d6+3' / 'd8' / '1d4-1' → (count, sides, modifier)。"""
    expr = expr.strip().replace(" ", "").lower()
    m = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", expr)
    if not m:
        raise ValueError(f"非法骰子表达式: {expr!r}")
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3) or 0)
    if not (1 <= count <= 20 and 1 <= sides <= 100):
        raise ValueError(f"骰子规模超出限制: {expr!r}")
    return count, sides, mod


def roll(rng: random.Random, expr: str) -> DiceResult:
    """掷 'XdY+Z'，返回明细（伤害骰用）。"""
    count, sides, mod = parse_expr(expr)
    rolls = [rng.randint(1, sides) for _ in range(count)]
    return DiceResult(expr=expr, rolls=rolls, kept=list(rolls), modifier=mod,
                      total=sum(rolls) + mod)


def count_faces(res: DiceResult, face: int) -> int:
    """数出这次掷骰里出现了多少个指定点数的骰子（致命伤判定用）。"""
    return sum(1 for r in res.rolls if r == face)


def d10(rng: random.Random) -> int:
    """单颗 d10。"""
    return rng.randint(1, 10)


def check_roll(rng: random.Random, modifier: int, dv: int | None = None,
               luck: int = 0, bonus: int = 0, expr: str = "1d10",
               labels: list[str] | None = None) -> DiceResult:
    """技能检定：1d10 + 属性 + 技能 + 修正，对照难度值 DV。

    ``luck`` 为投入的幸运点（掷骰前声明，每点 +1）；``dv`` 为 None 时只算总和不判定。
    暴击成功/失败按轻松模式规则追加一颗 d10（各只追加一次）。
    """
    first = d10(rng)
    rolls = [first]
    kept = [first]
    crit_success = crit_failure = False
    extra = 0
    if first == _CRIT_FACES:
        crit_success = True
        again = d10(rng)
        rolls.append(again)
        kept.append(again)
        extra = again
    elif first == _FUMBLE_FACES:
        crit_failure = True
        again = d10(rng)
        rolls.append(again)
        kept.append(again)
        extra = -again
    total = first + extra + modifier + bonus + luck
    breakdown: list[str] = []
    if labels:
        breakdown.extend(labels)
    breakdown.append(f"{modifier:+d}")
    if bonus:
        breakdown.append(f"{bonus:+d}（辅助/加时）")
    if luck:
        breakdown.append(f"{luck:+d}（幸运）")
    if extra:
        breakdown.append(f"{extra:+d}（{'暴击' if crit_success else '暴击失败'}追加）")
    res = DiceResult(expr=expr, rolls=rolls, kept=kept,
                     modifier=modifier + bonus + luck, total=total,
                     crit_success=crit_success, crit_failure=crit_failure,
                     luck_spent=luck, breakdown=breakdown)
    if dv is not None:
        res.dv = dv
        # 平手时防守方获胜：必须严格高于 DV
        res.success = total > dv
    return res


def opposed_roll(rng: random.Random, atk_mod: int, def_mod: int,
                 luck: int = 0) -> tuple[DiceResult, DiceResult, bool]:
    """对抗检定：双方各掷 1d10 + 修正，攻击方必须**高于**防守方才算赢。

    返回 ``(攻击方结果, 防守方结果, 攻击方是否获胜)``。
    """
    atk = check_roll(rng, atk_mod, dv=None, luck=luck)
    # 防守方掷骰的结果就是难度值
    de = check_roll(rng, def_mod, dv=None)
    atk.dv = de.total
    atk.success = atk.total > de.total
    return atk, de, bool(atk.success)


def damage_roll(rng: random.Random, expr: str, crit: bool = False) -> DiceResult:
    """伤害掷骰。暴击（致命伤）不改伤害骰本身的点数，由调用方结算。"""
    return roll(rng, expr)
