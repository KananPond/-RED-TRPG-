"""宏引擎（最小集）：{{name}} 大小写不敏感、:: 分参、先内后外两层嵌套。

语法遵 docs/sillytavern-architecture.md §6：
- {{macro}} 大小写不敏感；参数用 ::（{{random::a::b}}）；
- 嵌套宏先解析内层（最深 2 层，超出部分按字面保留）；
- \\{\\{text\\}\\} 转义为字面量 {{text}}；
- 未识别宏原样保留（不静默删除，便于发现问题）；
- 旧写法 <USER>/<BOT>/<CHAR> 一并替换。

支持宏（最小集）：{{user}} {{char}} {{persona}} {{description}} {{personality}}
{{scenario}} {{party}} {{scene}} {{flags}} {{time}} {{isotime}} {{isodate}}
{{random::…}} {{pick::…}} {{roll::XdY+Z}} {{newline}} {{space}} {{noop}}。

宏展开是纯字符串运算（零 token）；{{roll}} 桥接引擎骰（rules.dice.roll，
rng=对局 rng，同 seed 可复现）——宏骰只是叙事佐料，永不进入结算路径。
"""

from __future__ import annotations

import datetime
import hashlib
import random
import re
from dataclasses import dataclass, field

from ..rules.dice import roll as dice_roll

# 一层宏：花括号内不含任何花括号（嵌套时天然匹配最内层）
_MACRO_RE = re.compile(r"\{\{([^{}]*)\}\}")
# 旧写法（大小写不敏感）
_OLD_TAG_RE = re.compile(r"<(user|bot|char)>", re.IGNORECASE)
_ORIGINAL_RE = re.compile(r"\{\{original\}\}", re.IGNORECASE)
# 转义占位符（处理后回填）
_ESC_OPEN = "\x00ST_ESC_OPEN\x00"
_ESC_CLOSE = "\x00ST_ESC_CLOSE\x00"

# selectiveLogic 数字枚举 → 本包字符串枚举（worldinfo 消费）
LOGIC_BY_NUM = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}
WI_POSITION_BY_NUM = {0: "before_char", 4: "at_depth"}  # 其余数字 → after_char


@dataclass
class MacroContext:
    """宏取值上下文。user=玩家槽位名（{{user}}；AI PC 下=玩家角色名）；
    char=当前发言角色名（{{char}}）；description/personality/scenario 取 extra。"""

    user: str
    char: str
    persona: str = ""                # {{persona}}
    party: str = ""                  # 扩展：party_snapshot()（{{party}}）
    scene: str = ""                  # 扩展：场景 id:地点（{{scene}}）
    flags: str = ""                  # 扩展：旗标 dict 渲染（{{flags}}）
    extra: dict[str, str] = field(default_factory=dict)  # 任意补充键


def render(text: str, ctx: MacroContext, *, rng: random.Random | None = None,
           now: datetime.datetime | None = None) -> str:
    """渲染 text 中的宏。rng 缺省新建（{{random}}/{{roll}} 每次重摇）；
    now 缺省取当前时间。两层嵌套先内后外；未识别宏原样保留。"""
    if not text:
        return ""
    rng = rng or random.Random()
    now = now or datetime.datetime.now()
    out = str(text).replace("\\{\\{", _ESC_OPEN).replace("\\}\\}", _ESC_CLOSE)
    out = _OLD_TAG_RE.sub(
        lambda m: {"user": ctx.user, "bot": ctx.char, "char": ctx.char}[m.group(1).lower()],
        out)
    # 两层嵌套：每轮解析一层"不含花括号的宏"——第一轮解析最内层，
    # 第二轮把已展开的内容作为参数再解析外层；仍剩花括号者（三层+）按字面保留
    pick_seq = 0
    for _ in range(2):
        def _sub(m: re.Match) -> str:
            nonlocal pick_seq
            resolved, pick_seq = _resolve(m.group(1), ctx, rng, now, pick_seq)
            return m.group(0) if resolved is None else resolved
        out = _MACRO_RE.sub(_sub, out)
    return out.replace(_ESC_OPEN, "{{").replace(_ESC_CLOSE, "}}")


def expand_original(text: str, original: str) -> str:
    """把 {{original}}（大小写不敏感）展开为被覆盖前的默认内容（§3.4）。"""
    if not text:
        return ""
    return _ORIGINAL_RE.sub(lambda m: original, text)


def _resolve(body: str, ctx: MacroContext, rng: random.Random,
             now: datetime.datetime, pick_seq: int) -> tuple[str | None, int]:
    """解析单个宏体（不含花括号）。返回 (替换文本 | None=未识别, 新 pick 序号)。"""
    parts = body.split("::")
    name = parts[0].strip().lower()
    args = [p.strip() for p in parts[1:]]
    if name not in _KNOWN and " " in parts[0]:
        # 空格分参回退（{{roll 1d20}} / {{random a b}}，§6.1 允许空格或 ::）
        head = parts[0].split()
        name, args = head[0].strip().lower(), [p.strip() for p in head[1:]] + args
    if name == "user":
        return str(ctx.user), pick_seq
    if name == "char":
        return str(ctx.char), pick_seq
    if name == "persona":
        return str(ctx.persona), pick_seq
    if name in ("description", "personality", "scenario"):
        return str(ctx.extra.get(name, "") or ""), pick_seq
    if name == "party":
        return str(ctx.party), pick_seq
    if name == "scene":
        return str(ctx.scene), pick_seq
    if name == "flags":
        return str(ctx.flags), pick_seq
    if name == "time":
        return now.strftime("%H:%M"), pick_seq
    if name == "isotime":
        return now.strftime("%H:%M"), pick_seq
    if name == "isodate":
        return now.strftime("%Y-%m-%d"), pick_seq
    if name == "newline":
        return "\n", pick_seq
    if name == "space":
        return " ", pick_seq
    if name == "noop":
        return "", pick_seq
    if name == "random":
        if not args:
            return None, pick_seq
        return rng.choice(args), pick_seq          # 每次重摇
    if name == "pick":
        if not args:
            return None, pick_seq
        # 按 (user, char, 位置) 散列稳定：同一文本多次渲染结果一致
        digest = hashlib.sha256(
            f"{ctx.user}\x1f{ctx.char}\x1f{pick_seq}".encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % len(args)
        return args[idx], pick_seq + 1
    if name == "roll":
        expr = args[0] if args else ""
        if not expr:
            return None, pick_seq
        try:
            return str(dice_roll(rng, expr).total), pick_seq   # 引擎桥：同 seed 确定
        except ValueError:
            return None, pick_seq                  # 非法表达式原样保留
    return None, pick_seq                          # 未识别：原样保留


_KNOWN = {"user", "char", "persona", "description", "personality", "scenario",
          "party", "scene", "flags", "time", "isotime", "isodate", "newline",
          "space", "noop", "random", "pick", "roll"}
