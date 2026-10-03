"""角色卡与用户 persona（chara_card V2/V3 字段子集，docs/aipc-design.md §1.1）。

字段子集 = 〔ST 规格 §2.2〕中"进提示词"的字段 + 存放扩展字段的 extensions；
不引入 creator_notes/tags/creator/character_version/assets 等不进 prompt 的字段。

分层约束：本包不得 import ctrcg.game.*（纯机制、可单测）——
card_from_player 只鸭子引用 rules.character.Character 的属性；
TACTICS 风格摘要由调用方（game 层）经 tactics 参数注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING

from ..rules import data as D
from ..rules.character import Character

if TYPE_CHECKING:
    from .worldinfo import WorldInfo

# Persona 注入位置枚举（〔ST 规格 §4〕三种：禁用 / 进提示词 / 按深度注入）
PERSONA_POSITIONS = ("none", "in_prompt", "at_depth")
PERSONA_ROLES = ("system", "user", "assistant")

# 角色卡已知字段（from_dict 之外的键一律收进 extensions，spec：不得销毁未知键）
_CARD_FIELDS = ("name", "description", "personality", "scenario", "first_mes",
                "mes_example", "system_prompt", "post_history_instructions",
                "alternate_greetings", "talkativeness", "depth_prompt",
                "book", "extensions")


@dataclass
class Persona:
    """用户 persona（〔ST 规格 §4〕）。AI PC 模式下 DM/同伴上下文中的
    "玩家侧"注入由它承担；{{user}} 取 persona.name。"""

    name: str
    description: str = ""
    position: str = "in_prompt"      # none / in_prompt / at_depth
    depth: int = 2                   # at_depth 默认（personas.js DEFAULT_DEPTH=2）
    role: str = "system"             # at_depth 时的消息角色

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "position": self.position, "depth": self.depth, "role": self.role}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Persona":
        """None / 空输入 → 匿名空 persona；非法枚举值回退默认，不抛异常。"""
        d = d if isinstance(d, dict) else {}
        position = str(d.get("position", "in_prompt"))
        if position not in PERSONA_POSITIONS:
            position = "in_prompt"
        role = str(d.get("role", "system"))
        if role not in PERSONA_ROLES:
            role = "system"
        try:
            depth = int(d.get("depth", 2))
        except (TypeError, ValueError):
            depth = 2
        return cls(name=str(d.get("name", "") or ""),
                   description=str(d.get("description", "") or ""),
                   position=position, depth=depth, role=role)


@dataclass
class CharacterCard:
    """玩家/同伴角色卡。常驻字段 description/personality/scenario 每次进提示词；
    first_mes 一次性；mes_example 可挤出（<START> 分块）；system_prompt /
    post_history_instructions 为 Main Prompt / PHI 覆盖（""=用默认）。"""

    name: str
    description: str = ""            # 常驻：设定/背景/外观
    personality: str = ""            # 常驻：性格摘要
    scenario: str = ""               # 常驻：情境；AI PC 下由引擎每场景重写
    first_mes: str = ""              # 一次性：开场白（P2 消费）
    mes_example: str = ""            # 可挤出：以 <START> 分块
    system_prompt: str = ""          # Main Prompt 覆盖；""=用默认；支持 {{original}}
    post_history_instructions: str = ""  # PHI 覆盖；""=用默认；位于历史之后
    alternate_greetings: list[str] = field(default_factory=list)  # 开场备选（P2 消费）
    talkativeness: float = 0.5       # 新角色默认 0.5；P2 概率激活用
    depth_prompt: str = ""           # 角色注释 @D（P1 经 Injection 消费）
    book: "WorldInfo | None" = None  # character_book（P1 消费）
    extensions: dict = field(default_factory=dict)  # 未知键保存区（spec：不得销毁）

    def to_dict(self) -> dict:
        d = {"name": self.name, "description": self.description,
             "personality": self.personality, "scenario": self.scenario,
             "first_mes": self.first_mes, "mes_example": self.mes_example,
             "system_prompt": self.system_prompt,
             "post_history_instructions": self.post_history_instructions,
             "alternate_greetings": list(self.alternate_greetings),
             "talkativeness": self.talkativeness, "depth_prompt": self.depth_prompt,
             "extensions": dict(self.extensions)}
        if self.book is not None:
            d["book"] = self.book.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterCard":
        d = d if isinstance(d, dict) else {}
        known = {f.name for f in fields(cls)}
        ext = dict(d.get("extensions") or {})
        for k, v in d.items():          # 未知键进 extensions 保存区
            if k not in known:
                ext[k] = v
        book = None
        raw_book = d.get("book")
        if isinstance(raw_book, dict) and raw_book:
            from .worldinfo import WorldInfo  # 局部导入：避免 card↔worldinfo 模块环
            book = WorldInfo.from_dict(raw_book)
        try:
            talk = float(d.get("talkativeness", 0.5))
        except (TypeError, ValueError):
            talk = 0.5
        return cls(
            name=str(d.get("name", "") or ""),
            description=str(d.get("description", "") or ""),
            personality=str(d.get("personality", "") or ""),
            scenario=str(d.get("scenario", "") or ""),
            first_mes=str(d.get("first_mes", "") or ""),
            mes_example=str(d.get("mes_example", "") or ""),
            system_prompt=str(d.get("system_prompt", "") or ""),
            post_history_instructions=str(d.get("post_history_instructions", "") or ""),
            alternate_greetings=[str(x) for x in (d.get("alternate_greetings") or [])],
            talkativeness=talk,
            depth_prompt=str(d.get("depth_prompt", "") or ""),
            book=book,
            extensions=ext,
        )


def _book_from(d: dict) -> "WorldInfo | None":
    """V2 世界书 dict → WorldInfo；非法输入返回 None。"""
    if isinstance(d, dict) and d:
        from .worldinfo import wi_from_book  # 局部导入：避免 card↔worldinfo 模块环
        return wi_from_book(d)
    return None


def card_from_companion(comp: dict) -> CharacterCard:
    """模组同伴 dict → 卡（非玩家 PC 的独立 Agent 用，docs/companion-npc-agents.md）。

    基础映射：persona→description、voice→personality、name→name
    （module_data.py 同伴字段形状）；ST 扩展字段可选：mes_example /
    first_mes / depth_prompt / system_prompt / talkativeness /
    book（V2 世界书 dict）。未提字段留空。"""
    comp = comp if isinstance(comp, dict) else {}
    try:
        talk = float(comp.get("talkativeness", 0.5))
    except (TypeError, ValueError):
        talk = 0.5
    return CharacterCard(
        name=str(comp.get("name", "") or ""),
        description=str(comp.get("persona", "") or ""),
        personality=str(comp.get("voice", "") or ""),
        first_mes=str(comp.get("first_mes", "") or ""),
        mes_example=str(comp.get("mes_example", "") or ""),
        system_prompt=str(comp.get("system_prompt", "") or ""),
        depth_prompt=str(comp.get("depth_prompt", "") or ""),
        talkativeness=talk,
        book=_book_from(comp.get("book")),
    )


def card_from_npc(n: dict) -> CharacterCard:
    """模组 NPC dict → 卡（DM 控制的子智能体用，docs/companion-npc-agents.md）。

    必有字段 name/race/desc（module_data NPC 形状）；description 兜底为
    persona（若有）否则"种族。desc"。ST 扩展字段可选：persona（性格，进
    description）以外的 goals / secret（收进 extensions 供 NPC 系统模板的
    template_vars 槽位消费）、mes_example / first_mes / depth_prompt /
    talkativeness / book（V2 世界书 dict）。"""
    n = n if isinstance(n, dict) else {}
    race = str(n.get("race", "") or "")
    desc = str(n.get("desc", "") or "")
    description = str(n.get("persona", "") or "") or (
        f"{race}。{desc}" if race else desc)
    try:
        talk = float(n.get("talkativeness", 0.5))
    except (TypeError, ValueError):
        talk = 0.5
    ext = {k: n[k] for k in ("goals", "secret") if n.get(k)}
    return CharacterCard(
        name=str(n.get("name", "") or ""),
        description=description,
        personality=str(n.get("voice", "") or ""),
        first_mes=str(n.get("first_mes", "") or ""),
        mes_example=str(n.get("mes_example", "") or ""),
        depth_prompt=str(n.get("depth_prompt", "") or ""),
        talkativeness=talk,
        book=_book_from(n.get("book")),
        extensions=ext,
    )


def card_from_player(ch: Character, *, description: str = "", personality: str = "",
                     system_prompt: str = "", post_history_instructions: str = "",
                     mes_example: str = "", talkativeness: float = 0.5,
                     tactics: str = "") -> CharacterCard:
    """玩家 Character → 卡。name 取 ch.name；description/personality 来自建卡表单，
    空则用引擎兜底文案：description=种族·职业·背景，personality=TACTICS 风格摘要。

    tactics 由调用方传入（game.agents.TACTICS.get(klass)）——st 层不得 import
    ctrcg.game.*，故不在此直接引用；缺省时给一行按职业行事的通用文案。
    """
    race_cn = D.RACES.get(ch.race, {}).get("name", ch.race)
    klass_cn = D.CLASSES.get(ch.klass, {}).get("name", ch.klass)
    bg_cn = D.BACKGROUNDS.get(ch.background, {}).get("name", ch.background)
    if not description:
        description = f"{ch.name}，{race_cn}·{klass_cn}，{bg_cn}出身。"
    if not personality:
        personality = tactics or f"以{klass_cn}的方式行事：尽忠职守，与队伍同进退。"
    return CharacterCard(
        name=ch.name, description=description, personality=personality,
        system_prompt=system_prompt,
        post_history_instructions=post_history_instructions,
        mes_example=mes_example, talkativeness=talkativeness,
    )
