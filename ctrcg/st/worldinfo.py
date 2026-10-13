"""世界书（World Info / Lorebook）：数据模型、激活算法、多来源合并。

语义遵 docs/sillytavern-architecture.md §5 与 docs/aipc-design.md §1.3：
- constant（蓝圈）恒触发，预算内最先获得插入权；
- 主键任一命中（OR），再按 logic 结合副键：and_any / and_all / not_any / not_all
  （副键为空 = 过滤器忽略，任何逻辑都放行）；
- ASCII 键全词匹配（"king" 不中 "liking"）；**CJK 键一律子串匹配**
  （全词匹配对中文有害，中文实现强制关闭全词）；
- probability% 概率激活（rng 注入，同 seed 可复现；0=等效禁用）；
- token 预算即停：超限停止扫描、溢出条目不激活、已激活条目不回删；
- order 越大越靠近上下文末尾（保留优先）；before/after 桶按 order 升序拼接；
- recursive_scanning=True 时支持一轮递归（已激活内容参与下一轮扫描，
  直接命中条目优先于递归命中条目；P1 范围 0/1 轮）。

分层约束：本包不得 import ctrcg.game.*——engine_worldinfo 只鸭子引用
state.module / state.scene_id / state.flags。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from ..harness.loop import estimate_tokens
from ..rules import data as D
from .assemble import Injection
from .macros import LOGIC_BY_NUM, WI_POSITION_BY_NUM

WI_POSITIONS = ("before_char", "after_char", "at_depth")
WI_LOGICS = ("and_any", "and_all", "not_any", "not_all")
DEFAULT_DEPTH = 4          # 〔ST 规格 §12〕WI 条目 @D 默认深度
DEFAULT_BUDGET = 1024      # WI 独立预算（§7 第 5 条：与主历史分账）
DEFAULT_SCAN_DEPTH = 4     # 扫描最近 N 条"桌面文本"


@dataclass
class WLEntry:
    """世界书条目（V2/V3 卡 entry 字段子集 + ST 内部语义字段）。"""

    keys: list[str]
    content: str
    secondary_keys: list[str] = field(default_factory=list)
    logic: str = "and_any"           # and_any / and_all / not_any / not_all
    constant: bool = False           # 🔵 恒触发，预算内最先获得插入权
    enabled: bool = True
    position: str = "before_char"    # before_char / after_char / at_depth
    depth: int = DEFAULT_DEPTH       # 仅 at_depth
    role: str = "system"             # 仅 at_depth：system / user / assistant
    order: int = 100                 # 越大越靠近上下文末尾（ST 语义，保留优先）
    priority: int = 100              # 预算吃紧时数值小者先丢弃（V2 卡字段）
    case_sensitive: bool = False
    probability: int = 100
    comment: str = ""

    def to_dict(self) -> dict:
        return {"keys": list(self.keys), "secondary_keys": list(self.secondary_keys),
                "content": self.content, "logic": self.logic,
                "constant": self.constant, "enabled": self.enabled,
                "position": self.position, "depth": self.depth, "role": self.role,
                "order": self.order, "priority": self.priority,
                "case_sensitive": self.case_sensitive,
                "probability": self.probability, "comment": self.comment}

    @classmethod
    def from_dict(cls, d: dict) -> "WLEntry":
        d = d if isinstance(d, dict) else {}
        logic = str(d.get("logic", "and_any"))
        position = str(d.get("position", "before_char"))
        role = str(d.get("role", "system"))
        return cls(
            keys=_as_key_list(d.get("keys")),
            content=str(d.get("content", "") or ""),
            secondary_keys=_as_key_list(d.get("secondary_keys")),
            logic=logic if logic in WI_LOGICS else "and_any",
            constant=bool(d.get("constant", False)),
            enabled=bool(d.get("enabled", True)),
            position=position if position in WI_POSITIONS else "before_char",
            depth=_as_int(d.get("depth"), DEFAULT_DEPTH),
            role=role if role in ("system", "user", "assistant") else "system",
            order=_as_int(d.get("order"), 100),
            priority=_as_int(d.get("priority"), 100),
            case_sensitive=bool(d.get("case_sensitive", False)),
            probability=_as_int(d.get("probability"), 100),
            comment=str(d.get("comment", "") or ""),
        )


@dataclass
class WorldInfo:
    """一本世界书。token_budget 是 WI 独立预算，与主历史分账（§7）。"""

    entries: list[WLEntry] = field(default_factory=list)
    scan_depth: int = DEFAULT_SCAN_DEPTH   # 扫描最近 N 条"桌面文本"
    token_budget: int = DEFAULT_BUDGET
    recursive_scanning: bool = False       # P1 先支持 0/1 轮

    def to_dict(self) -> dict:
        return {"scan_depth": self.scan_depth, "token_budget": self.token_budget,
                "recursive_scanning": self.recursive_scanning,
                "entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, d: dict) -> "WorldInfo":
        d = d if isinstance(d, dict) else {}
        raw = d.get("entries") or []
        if isinstance(raw, dict):        # ST 世界书导出形状 {uid: entry}
            raw = list(raw.values())
        entries = [e if isinstance(e, WLEntry) else WLEntry.from_dict(e)
                   for e in raw if isinstance(e, dict)]
        return cls(entries=entries,
                   scan_depth=_as_int(d.get("scan_depth"), DEFAULT_SCAN_DEPTH),
                   token_budget=_as_int(d.get("token_budget"), DEFAULT_BUDGET),
                   recursive_scanning=bool(d.get("recursive_scanning", False)))


@dataclass
class WIResult:
    """一次扫描的激活结果。before/after 按 order 升序拼接；
    at_depth 转为 assemble.Injection（order 直传，越大越靠后语义一致）。"""

    before: str = ""
    after: str = ""
    at_depth: list[Injection] = field(default_factory=list)
    overflowed: bool = False
    activated: list[str] = field(default_factory=list)   # 命中的 comment（UI 可展示）


def wi_from_book(book: dict) -> WorldInfo:
    """character_book / 世界书 JSON（V2 卡 shapes，〔ST 规格 §2.3〕）→ WorldInfo。

    **insertion_order 方向换算**：V2 spec 语义"数值越小越靠前"（lower = higher），
    本包固定采用 ST 内部方向"数值越大越靠后 = 保留优先"——故导入 V2 卡时把
    insertion_order 取反（-order）；若条目带的是 ST 内部 `order` 字段则原样使用。
    兼容字段：key/keysecondary（逗号分隔字符串）、disable、selective=false 时
    忽略副键、extensions.selectiveLogic 数字枚举、position 数字（0=before、
    4=at_depth、其余=after）、entries 为 dict 的 ST 导出形状。"""
    book = book if isinstance(book, dict) else {}
    raw = book.get("entries") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    entries: list[WLEntry] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        ext = e.get("extensions") or {}
        if not isinstance(ext, dict):
            ext = {}
        keys = _as_key_list(e.get("keys") or e.get("key"))
        secondary = _as_key_list(e.get("secondary_keys") or e.get("keysecondary"))
        if e.get("selective") is False:
            secondary = []               # selective=false：副键被忽略
        # order 方向：insertion_order（V2 spec，小=靠前）取反；order（ST 内部）原样
        if e.get("insertion_order") is not None:
            order = -_as_int(e.get("insertion_order"), 100)
        else:
            order = _as_int(e.get("order"), 100)
        logic_raw = e.get("logic", ext.get("selectiveLogic", e.get("selectiveLogic")))
        logic = _as_logic(logic_raw)
        position_raw = e.get("position", ext.get("position"))
        position = _as_position(position_raw)
        depth = _as_int(e.get("depth", ext.get("depth")), DEFAULT_DEPTH)
        role = str(e.get("role", ext.get("role")) or "system")
        prob = _as_int(e.get("probability", ext.get("probability")), 100)
        use_prob = e.get("useProbability", ext.get("useProbability"))
        if use_prob is False:
            prob = 100                   # ST 导出形状：未启用概率判定
        enabled = bool(e.get("enabled", True))
        if e.get("disable"):
            enabled = False              # ST 导出形状：disable=true = 停用
        entries.append(WLEntry(
            keys=keys, content=str(e.get("content", "") or ""),
            secondary_keys=secondary, logic=logic,
            constant=bool(e.get("constant", False)), enabled=enabled,
            position=position, depth=depth,
            role=role if role in ("system", "user", "assistant") else "system",
            order=order, priority=_as_int(e.get("priority"), 100),
            case_sensitive=bool(e.get("case_sensitive",
                                      ext.get("case_sensitive",
                                              e.get("caseSensitive",
                                                    ext.get("caseSensitive", False))))),
            probability=prob,
            comment=str(e.get("comment") or e.get("name") or ""),
        ))
    return WorldInfo(
        entries=entries,
        scan_depth=_as_int(book.get("scan_depth"), DEFAULT_SCAN_DEPTH),
        token_budget=_as_int(book.get("token_budget"), DEFAULT_BUDGET),
        recursive_scanning=bool(book.get("recursive_scanning", False)),
    )


def engine_worldinfo(state) -> WorldInfo:
    """引擎状态 WI 化（〔aipc 设计 §1.3/§11 #6〕）。state 鸭子引用
    （module / scene_id / flags），不 import ctrcg.game.*：

    - 当前场景：constant 条目（id/地点/intro 摘要，order 高）；
    - 剧情旗标：每条旗标一个 selective 条目（键=旗标键与其值），内容=值；
    - 模组 NPC：键=NPC 名，content=desc（module.npcs，RED 形状 name/role/desc）；
    - 场景 checks 检定项：键=技能中文名（D.SKILLS 的 RED 名），content=难度值 DV
      与成功描述摘要（旧字段 skills/dc 仍兼容读取）。

    预算内 constant 场景条目最先入选（§5.2 丢弃顺序第 1 条）；
    其余按 order 降序尝试：旗标(500) → 场景检定(400) → NPC(300)。
    """
    entries: list[WLEntry] = []
    module = getattr(state, "module", None)
    scene = module.scene_by_id(getattr(state, "scene_id", "")) if module else None
    if scene is not None:
        intro = str(scene.get("intro", "")).replace("\n", " ")[:160]
        entries.append(WLEntry(
            keys=[],
            content=f"【当前场景】{scene.get('id', '')}：{scene.get('location', '')}\n{intro}",
            constant=True, order=1000, comment=f"scene:{scene.get('id', '')}"))
        for sk in (scene.get("checks") or scene.get("skills") or []):
            if not isinstance(sk, dict):
                continue
            skill_key = str(sk.get("skill", ""))
            name = D.SKILLS.get(skill_key, {}).get("name", skill_key)
            success = str(sk.get("success", "")).replace("\n", " ")[:80]
            dv = sk.get("dv", sk.get("dc", "?"))
            entries.append(WLEntry(
                keys=[str(name)],
                content=f"本场景可进行{name}检定（DV {dv}）：{success}",
                order=400, comment=f"skill:{skill_key}"))
    for k, v in (getattr(state, "flags", {}) or {}).items():
        entries.append(WLEntry(
            keys=[str(k), str(v)], content=f"剧情进展：{k}={v}。",
            order=500, comment=f"flag:{k}"))
    npcs = getattr(module, "npcs", {}) or {}
    for nid, n in npcs.items():
        n = n if isinstance(n, dict) else {}
        name = str(n.get("name", nid))
        desc = str(n.get("desc", ""))
        role = str(n.get("role", "") or n.get("affiliation", "") or "?")
        entries.append(WLEntry(
            keys=[name], content=f"{name}（{role}）：{desc}",
            order=300, comment=f"npc:{nid}"))
    return WorldInfo(entries=entries)


def scan(wi: WorldInfo, window: str, *, budget_tokens: int | None = None,
         rng: random.Random | None = None) -> WIResult:
    """激活算法（〔ST 规格 §5.2〕）：constant → 关键词（logic 结合副键）→
    probability → 预算（estimate_tokens 计量，超限即停、不回删）→ 分桶。

    window 为扫描窗口（桌面最近文本，调用方按 wi.scan_depth 截取）。
    budget_tokens 缺省用 wi.token_budget；rng 缺省新建（概率激活可复现需显式传入）。
    recursive_scanning=True 时做一轮递归：已激活内容并入扫描窗口再试未激活条目。
    """
    rng = rng or random.Random()
    budget = wi.token_budget if budget_tokens is None else budget_tokens
    window = str(window or "")
    # 处理顺序：constant 先 → order 降序（大者保留优先）→ priority 降序 → 原序稳定
    ordered = sorted(enumerate(wi.entries),
                     key=lambda p: (0 if p[1].constant else 1, -p[1].order,
                                    -p[1].priority, p[0]))
    used = 0
    activated: list[WLEntry] = []
    done: set[int] = set()
    overflowed = False

    def budget_exceeded(entry: WLEntry) -> bool:
        return used + estimate_tokens(entry.content) > budget

    for idx, e in ordered:
        if not e.enabled or idx in done:
            continue
        if not e.constant and not _match_entry(e, window):
            continue
        if _skip_by_probability(e, rng):
            continue
        if budget_exceeded(e):
            overflowed = True
            break                        # 预算即停：不回删、不再尝试更小的条目
        used += estimate_tokens(e.content)
        activated.append(e)
        done.add(idx)
    if wi.recursive_scanning and not overflowed and activated:
        rec_window = window + "\n" + "\n".join(e.content for e in activated)
        for idx, e in ordered:
            if not e.enabled or idx in done or e.constant:
                continue
            if not _match_entry(e, rec_window):
                continue
            if _skip_by_probability(e, rng):
                continue
            if budget_exceeded(e):
                overflowed = True
                break
            used += estimate_tokens(e.content)
            activated.append(e)
            done.add(idx)
    # 分桶：before/after 按 order 升序拼接（稳定排序：同 order 保持激活序）
    before = "\n".join(e.content for e in
                       sorted((e for e in activated if e.position == "before_char"),
                              key=lambda e: e.order))
    after = "\n".join(e.content for e in
                      sorted((e for e in activated if e.position == "after_char"),
                             key=lambda e: e.order))
    at_depth = [Injection(depth=e.depth, role=e.role, order=e.order, text=e.content)
                for e in activated if e.position == "at_depth"]
    names = [e.comment or ("/".join(e.keys) if e.keys else "constant")
             for e in activated]
    return WIResult(before=before, after=after, at_depth=at_depth,
                    overflowed=overflowed, activated=names)


def merge(*books: WorldInfo) -> WorldInfo:
    """多来源合并（〔ST 规格 §4〕次序：Chat → Persona → Character/Global）。
    同键（keys 集合相等）时先传入者（角色书）完全优先（§5.3）；
    无键条目（constant）不参与去重。书级参数就宽：scan_depth / token_budget
    取各来源最大值，recursive_scanning 任一来源开启即开启。"""
    books = [b for b in books if b is not None]
    if not books:
        return WorldInfo()
    entries: list[WLEntry] = []
    seen: set[frozenset] = set()
    for book in books:
        for e in book.entries:
            key = frozenset(e.keys)
            if e.keys and key in seen:
                continue                 # 同键：先传入者完全优先
            if e.keys:
                seen.add(key)
            entries.append(e)
    return WorldInfo(entries=entries,
                     scan_depth=max(b.scan_depth for b in books),
                     token_budget=max(b.token_budget for b in books),
                     recursive_scanning=any(b.recursive_scanning for b in books))


# ---------------------------------------------------------------- 匹配内部
def _match_entry(entry: WLEntry, window: str) -> bool:
    """主键任一命中（OR），副键为空 = 忽略过滤器；否则按 logic 结合副键。"""
    if not entry.keys:
        return False                     # 无键且非 constant：永不匹配
    if not any(_match_one(k, window, entry.case_sensitive) for k in entry.keys):
        return False
    if not entry.secondary_keys:
        return True
    hits = [_match_one(k, window, entry.case_sensitive)
            for k in entry.secondary_keys]
    if entry.logic == "and_any":
        return any(hits)
    if entry.logic == "and_all":
        return all(hits)
    if entry.logic == "not_any":
        return not any(hits)
    if entry.logic == "not_all":
        return not all(hits)
    return True


def _match_one(key: str, window: str, case_sensitive: bool) -> bool:
    """ASCII 键全词匹配（"king" 不中 "liking"）；CJK/非 ASCII 键一律子串匹配
    （全词匹配对中文有害，中文实现强制关闭全词）。
    全词边界按 ASCII 词字符界定（对齐 JS \\b 语义）——
    中文邻接的 ASCII 键（"打开了gate的门"）也能命中。"""
    if not key or not window:
        return False
    hay = window if case_sensitive else window.lower()
    needle = key if case_sensitive else key.lower()
    if not needle:
        return False
    if all(ord(c) < 128 for c in needle):        # 纯 ASCII → 全词
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(needle) + r"(?![A-Za-z0-9_])"
        return re.search(pattern, hay) is not None
    return needle in hay                          # 含非 ASCII → 子串


def _skip_by_probability(entry: WLEntry, rng: random.Random) -> bool:
    """probability% 概率激活（roll < trigger% 保留；100 恒插、0 等效禁用）。"""
    if entry.probability >= 100:
        return False
    return rng.random() * 100 >= entry.probability


def _as_key_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_logic(value) -> str:
    if isinstance(value, bool):
        return "and_any"
    if isinstance(value, int):
        return LOGIC_BY_NUM.get(value, "and_any")
    text = str(value or "").strip().lower()
    return text if text in WI_LOGICS else "and_any"


def _as_position(value) -> str:
    if value is None:
        return "before_char"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return WI_POSITION_BY_NUM.get(int(value), "after_char")
    text = str(value).strip().lower()
    return text if text in WI_POSITIONS else "after_char"
