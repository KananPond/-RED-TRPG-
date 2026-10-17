"""游戏运行时状态：队伍、剧情旗标、场景指针、战术地图、存档。

修订为《赛博朋克 RED》口径（PORT_SPEC §1/§4）：

* 术语：PW（生命值）/ 重伤阈值 / 伤势（轻伤·重伤·濒死）/ 护甲 SP（OB）/
  幸运 / 人性 / 欧元（eb）/ 改进点（IP）/ 职业（Role）——没有 AC、法术位、
  等级、经验、金币。
* 单位：战术网格 **1 格 = 2 米**（``rules.combat.METERS_PER_CELL``）。
* 存档：顶层 ``ruleset: "cyberpunk-red"``；读档时缺失或不是该值即拒绝
  （旧 5e 存档绝不静默载入）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import TYPE_CHECKING

from ..rules import data as D
from ..rules.character import Character
from ..rules.combat import METERS_PER_CELL      # 1 格 = 2 米（RED）
from ..campaign import module_data as M
from ..campaign.loader import Module

if TYPE_CHECKING:
    from ..rules.combat import Encounter

#: 新存档的规则集标识（PORT_SPEC §4）
RULESET = "cyberpunk-red"


class GameState:
    def __init__(self, player: Character, companions: list[Character],
                 rng: random.Random | None = None,
                 module: Module | None = None):
        self.player = player
        self.companions = companions
        self.flags: dict[str, str] = {}
        self.module = module or M.BUILTIN
        self.scene_id = self.module.first_scene_id()
        self.visited: list[str] = []
        self.rng = rng or random.Random()
        # 队伍公用欧元（PORT_SPEC §4）：分钱箱子，不是某个角色的钱包。
        # 缺省与玩家钱包一致，避免旧路径（直接给 player.eurobucks 加钱）丢账。
        self.party_eurobucks: int = int(getattr(player, "eurobucks", 0) or 0)
        # AI PC（docs/aipc-design.md §3.1）：全部带默认值，既有构造调用方零改动。
        self.aipc_enabled: bool = False   # 本局是否 AI 代打（构建期快照，局内只读）
        self.persona = None               # 用户 persona：Persona | None
        self.aipc_card = None             # 玩家角色卡：CharacterCard | None
        # 战术地图：None = 本局无地图（战斗退回剧场模式）。
        self.map: dict | None = None

    # ------------------------------------------------------------ 队伍
    def party(self) -> list[Character]:
        return [self.player] + self.companions

    def find_member(self, name: str) -> Character | None:
        key = str(name or "").strip().lower()
        if not key:
            return None
        if key in ("队伍", "全队", "party", "大家"):
            return self.player
        for c in self.party():
            if c.name.lower() == key or c.handle.lower() == key:
                return c
        for c in self.party():
            if key in c.name.lower():
                return c
        return None

    def alive_party(self) -> list[Character]:
        return [c for c in self.party() if c.hp_cur > 0 and not c.is_dead]

    # ------------------------------------------------------------ 队伍快照
    def party_snapshot(self) -> str:
        """给 DM/AI 队友的【队伍】文本段（RED 术语：PW/伤势/护甲 SP/幸运/人性/eb/IP）。"""
        lines = []
        for c in self.party():
            weapon = self._weapon_text(c)
            conds = "、".join(c.conditions) if c.conditions else "正常"
            crit = "、".join(i.get("name", "?") for i in c.crit_injuries)
            bits = [
                f"{c.name}（{c.role_name}·职级{c.role_rank}）",
                f"PW {c.hp_cur}/{c.hp_max}",
                f"重伤阈值 {c.seriously_wounded_threshold}",
                f"伤势 {c.wound_name()}",
                f"护甲 {c.armor_name()} SP {c.armor_at('torso')}",
                f"幸运 {c.luck_cur}/{c.stat('luck')}",
                f"人性 {c.humanity_cur}/{c.humanity_max}",
                f"声望 {c.reputation}",
                f"武器 {weapon}",
                f"{D.CURRENCY} {c.eurobucks}",
                f"IP {c.ip}",
            ]
            tail = f"状态[{conds}]"
            if crit:
                tail += f" 致命伤[{crit}]"
            lines.append(" ".join(bits) + " " + tail)
        lp = self.lifepath_digest()
        if lp:
            lines.append(lp)
        return "\n".join(lines)

    def lifepath_digest(self) -> str:
        """玩家角色的人生之路（人生轨迹）文本段，供 DM 编织剧情钩子。

        轻松模式建卡时会走一遍第 29-34 页的人生之路：朋友会来帮忙，仇人会在
        尾声里发誓复仇，人生目标决定他为什么接这一票。"""
        rows = []
        for c in self.party():
            if not c.lifepath:
                continue
            rows.append(f"{c.name} 的人生之路：")
            for line in c.lifepath_lines():
                rows.append(f"  · {line}")
        return "\n".join(rows)

    @staticmethod
    def _weapon_text(c: Character) -> str:
        """该成员的武器（含弹药余量与「无需装填」标记）。"""
        if not c.weapons:
            return "徒手"
        out = []
        for key in c.weapons:
            info = D.WEAPONS.get(key) or {}
            name = info.get("name", key)
            cap = info.get("ammo")
            if cap:
                name += f"（{c.ammo.get(key, cap)}/{cap} 发）"
            out.append(name)
        return "、".join(out)

    def member_brief(self, c: Character) -> dict:
        """单个成员的结构化摘要（前端队伍面板 / get_state / 终局卡共用，§9）。"""
        return {
            "name": c.name, "handle": c.handle,
            "role": c.role, "role_name": c.role_name,
            "role_rank": c.role_rank, "role_ability": c.role_ability,
            "hp_cur": c.hp_cur, "hp_max": c.hp_max,
            "seriously_wounded": c.wound_state() == "serious",
            "seriously_wounded_threshold": c.seriously_wounded_threshold,
            "wound": c.wound_name(), "wound_state": c.wound_state(),
            "luck_cur": c.luck_cur, "luck_max": c.stat("luck"),
            "humanity_cur": c.humanity_cur, "humanity_max": c.humanity_max,
            "reputation": c.reputation,
            "sp": c.armor_at("torso"), "armor": c.armor_name(),
            "eurobucks": c.eurobucks, "ip": c.ip,
            "conditions": sorted(c.conditions),
            "crit_injuries": [i.get("name", "?") for i in c.crit_injuries],
            "cyberware": [self._cyber_name(k) for k in c.cyberware],
            "weapons": [self._weapon_name(k) for k in c.weapons],
            "downed": c.is_downed, "dead": c.is_dead,
            "lifepath": c.lifepath_lines(),
            "brief": c.brief(),
        }

    @staticmethod
    def _weapon_name(key: str) -> str:
        return (D.WEAPONS.get(key) or {}).get("name", key)

    @staticmethod
    def _cyber_name(key: str) -> str:
        return (D.CYBERWARE.get(key) or {}).get("name", key)

    def party_panel(self) -> list[dict]:
        """前端队伍面板载荷（PORT_SPEC §9 的 /api/state 成员字段）。"""
        return [self.member_brief(c) for c in self.party()]

    def epilogue_snapshot(self) -> dict:
        """终局卡数据：队伍结局、欧元、旗标、足迹。"""
        return {
            "module_title": self.module.title,
            "scene_id": self.scene_id,
            "visited": len(self.visited),
            "scenes_total": len(self.module.scenes),
            "eurobucks": int(self.party_eurobucks),
            "currency": D.CURRENCY,
            "flags": dict(self.flags),
            "party": self.party_panel(),
        }

    # ------------------------------------------------------------ 战术地图
    def map_brief(self, enc=None) -> dict | None:
        """地图快照（SSE 广播 / 快照接口 / 存档共用；浅拷贝防别名写入）。

        ``enc``（战斗中的 ``Encounter``）在场时附带**掩体实时状态**：材质、剩余
        耐久与是否已被摧毁——前端 VTT 据此画掩体块与耐久条。掩体**不挡视线**
        （视线只由障碍格决定，见 ``BattleGrid.has_los``），故与 ``obstacles``
        分开表达，前端不得把两者混为一谈。
        """
        if not self.map:
            return None
        m = dict(self.map)
        m["tokens"] = {k: dict(v) for k, v in m["tokens"].items()}
        m["meters_per_cell"] = METERS_PER_CELL
        m["cover"] = self._cover_brief(enc)
        m["cover_mesh"] = bool(enc is not None and getattr(enc, "grid", None) is not None)
        return m

    def _cover_brief(self, enc=None) -> dict:
        """掩体 → ``{"x,y": [材质, 剩余耐久, 已摧毁]}``。

        战斗期以 ``Encounter.cover``（含耐久损耗）为准；探索期退回场景声明里
        的静态掩体，按材质满值显示（``D.COVER``）。"""
        from ..rules import data as D
        if enc is not None and getattr(enc, "cover", None):
            return {f"{int(c[0])},{int(c[1])}":
                    [str(v[0]), int(v[1]), bool(int(v[1]) <= 0)]
                    for c, v in enc.cover.items()}
        out: dict = {}
        spec = self.map.get("cover") or {}
        if not isinstance(spec, dict):
            return out
        for material, cells in spec.items():
            hp = int(D.COVER.get(str(material), 0) or 0)
            for cell in cells or []:
                out[f"{int(cell[0])},{int(cell[1])}"] = [str(material), hp, hp <= 0]
        return out

    def map_grid(self) -> tuple[int, int]:
        return tuple(self.map["grid"]) if self.map else (0, 0)

    def map_token_at(self, cell) -> str | None:
        """返回占住该格的棋子 id（探索期用；战斗期以 Encounter 为准）。"""
        for tid, t in (self.map or {}).get("tokens", {}).items():
            if list(t.get("cell") or []) == list(cell):
                return tid
        return None

    def map_cell_free(self, cell) -> bool:
        if not self.map:
            return False
        w, h = self.map_grid()
        if not (0 <= cell[0] < w and 0 <= cell[1] < h):
            return False
        if [cell[0], cell[1]] in self.map["obstacles"]:
            return False
        return self.map_token_at(cell) is None

    def map_place(self, token_id: str, name: str, kind: str, cell, icon=None) -> dict:
        """放置棋子（格被占/越界/障碍 → ok=False）。"""
        if not self.map:
            return {"ok": False, "error": "当前没有地图"}
        if not self.map_cell_free(cell):
            return {"ok": False, "error": f"格子 {cell} 不可用（越界/障碍/已占用）"}
        self.map["tokens"][token_id] = {"name": name, "kind": kind,
                                        "cell": [int(cell[0]), int(cell[1])],
                                        "icon": icon}
        return {"ok": True}

    def map_move(self, token_id: str, cell) -> dict:
        """移动棋子到空格（战术网格，1 格 = 2 米）。"""
        if not self.map:
            return {"ok": False, "error": "当前没有地图"}
        t = self.map["tokens"].get(token_id)
        if t is None:
            return {"ok": False, "error": f"棋盘上没有 {token_id}"}
        if not self.map_cell_free(cell):
            return {"ok": False, "error": f"格子 {cell} 不可用（越界/障碍/已占用）"}
        t["cell"] = [int(cell[0]), int(cell[1])]
        return {"ok": True}

    def map_remove(self, token_id: str) -> dict:
        if not self.map or token_id not in self.map["tokens"]:
            return {"ok": False, "error": f"棋盘上没有 {token_id}"}
        del self.map["tokens"][token_id]
        return {"ok": True}

    def map_digest(self) -> str:
        """【地图】上下文段：DM 每轮的战场态势（谁在哪；1 格 = 2 米）。"""
        if not self.map:
            return ""
        lines = [f"{self.map['label']}（{self.map['grid'][0]}×{self.map['grid'][1]} 格，"
                 f"1格={METERS_PER_CELL}米，障碍 {len(self.map['obstacles'])} 处）"]
        for t in self.map["tokens"].values():
            x, y = t["cell"]
            lines.append(f"- {t['name']}（{t['kind']}）@ ({x},{y})")
        return "\n".join(lines)


# ---------------------------------------------------------------- 战术地图
def build_map_state(module: Module, scene: dict, party: list[Character]) -> dict | None:
    """由场景 map 声明构建运行时地图状态（无 map 声明 → None = 剧场模式）。

    单位：1 格 = 2 米（RED）。场景棋子（NPC/物件）按声明布点；队伍以
    party_spawn 为锚点就近散布（玩家在锚点，同伴顺时钟找空格）。
    icon 解析模组级 tokens 映射。
    """
    spec = scene.get("map")
    if not spec:
        return None
    grid = [int(spec.get("grid", [20, 14])[0]), int(spec.get("grid", [20, 14])[1])]
    obstacles = [[int(x), int(y)] for x, y in (spec.get("obstacles") or [])]
    taken: set[tuple[int, int]] = {tuple(o) for o in obstacles}
    tokens: dict[str, dict] = {}
    icons = dict(getattr(module, "token_icons", {}) or {})
    for t in spec.get("tokens") or []:
        cell = [int(t["cell"][0]), int(t["cell"][1])]
        if _cell_blocked(cell, grid, taken):
            continue                      # 声明越界/落障碍 → 丢弃（校验器已警告）
        taken.add(tuple(cell))
        tokens[str(t["id"])] = {"name": str(t.get("name") or t["id"]),
                                "kind": str(t.get("kind", "npc")),
                                "cell": cell,
                                "icon": icons.get(str(t["id"]).split(":", 1)[-1])}
    # 队伍落位：玩家在 party_spawn（缺省取网格中心），同伴就近散布
    spawn = spec.get("party_spawn") or [grid[0] // 2, grid[1] // 2]
    anchor = [int(spawn[0]), int(spawn[1])]
    for i, c in enumerate(party):
        cell = anchor if i == 0 and not _cell_blocked(anchor, grid, taken) \
            else _free_cell_near(anchor, grid, taken)
        if cell is None:
            break
        taken.add(tuple(cell))
        tokens[f"pc:{c.name}"] = {"name": c.name, "kind": "pc",
                                  "cell": [cell[0], cell[1]], "icon": None}
    return {"scene_id": scene["id"], "image": spec.get("image"),
            "label": str(spec.get("label") or scene["location"]),
            "grid": grid, "obstacles": obstacles, "tokens": tokens,
            "meters_per_cell": METERS_PER_CELL,
            "image_grid": spec.get("image_grid") or list(grid),
            "crop": spec.get("crop"),
            # 掩体声明原样入图：探索期由 ``map_brief`` 按材质满值下发，
            # 战斗期改用 ``Encounter.cover`` 的实时耐久（``_place_cover`` 落点）
            "cover": {str(k): [[int(c[0]), int(c[1])] for c in (v or [])]
                      for k, v in (spec.get("cover") or {}).items()}}


def _cell_blocked(cell, grid, taken) -> bool:
    x, y = cell
    return not (0 <= x < grid[0] and 0 <= y < grid[1]) or tuple(cell) in taken


def _free_cell_near(anchor, grid, taken):
    """BFS 找锚点附近最近空格（含障碍绕行），全满返回 None。"""
    from collections import deque
    seen = {tuple(anchor)}
    q = deque([tuple(anchor)])
    while q:
        cur = q.popleft()
        if cur not in taken and 0 <= cur[0] < grid[0] and 0 <= cur[1] < grid[1]:
            return [cur[0], cur[1]]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nxt = (cur[0] + dx, cur[1] + dy)
                if nxt not in seen and 0 <= nxt[0] < grid[0] and 0 <= nxt[1] < grid[1]:
                    seen.add(nxt)
                    q.append(nxt)
    return None


class EncounterBuildError(Exception):
    pass


# ---------------------------------------------------------------- 遭遇
def build_encounter(encounter_id: str, state: GameState,
                    surprised: bool = False) -> "Encounter":
    """由模组 encounters 条目组建一场 RED 遭遇（PORT_SPEC §3）。

    * ``enemies: [[npc_key, 数量], ...]`` → ``Character.from_statblock``
      （同类多只加编号 tag，避免目标重名）。
    * 队伍：当前存活成员进场。
    * 网格：场景声明 ``map`` 时建 ``BattleGrid``（1 格 = 2 米）；场景
      ``map.tokens`` 作为 ``entry["_tokens"]`` 透传，供 ``Encounter._deploy``
      按需布点（棋子 id 以 ``npc:``/``pc:`` 前缀书写，按显示名匹配）。
    """
    from ..rules.combat import BattleGrid, Encounter
    spec = state.module.encounter(encounter_id)
    if spec is None:
        raise EncounterBuildError(f"未知遭遇 {encounter_id}")
    scene = state.module.scene_by_id(state.scene_id) or {}
    map_spec = scene.get("map") or {}
    entry = dict(spec)
    entry["_tokens"] = [dict(t) for t in (map_spec.get("tokens") or [])]
    enc = Encounter(title=spec.get("title", encounter_id),
                    intro=spec.get("intro", ""), entry=entry, rng=state.rng)
    for pair in (spec.get("enemies") or []):
        key, count = (pair[0], pair[1]) if isinstance(pair, (list, tuple)) \
            else (pair, 1)
        count = max(1, int(count or 1))
        block = _npc_block(state.module, str(key))
        if block is None:
            raise EncounterBuildError(f"未知敌人 {key}（不在 NPC 数据表中）")
        tags = [str(i + 1) for i in range(count)] if count > 1 else [""]
        for tag in tags:
            enc.enemies.append(
                Character.from_statblock(str(key), block, tag=tag))
    for c in state.alive_party():
        enc.allies.append(c)
    if surprised:
        # 保留入口（session 传参兼容）：RED 无 5e 突袭轮；改记为叙事提示。
        enc.log("⚠ 队伍抢得先手（潜行成功）。")
    if map_spec:
        grid = map_spec.get("grid") or [20, 14]
        enc.grid = BattleGrid(int(grid[0]), int(grid[1]),
                              map_spec.get("obstacles") or [])
        _place_cover(enc, map_spec)
    elif isinstance(state.map, dict) and state.map.get("grid"):
        enc.grid = BattleGrid(state.map["grid"][0], state.map["grid"][1],
                              state.map.get("obstacles") or [])
        _place_cover(enc, state.map)
    return enc


def _place_cover(enc, map_spec: dict) -> None:
    """把场景声明的掩体放进战斗（``map.cover`` = {材质: [[x,y], ...]}）。

    掩体按材质有耐久（见 ``rules.data.COVER``），伤害先打在掩体上；耐久归零即被
    摧毁，本次攻击剩余的伤害作废（轻松模式第 21 页）。"""
    cover = map_spec.get("cover") or {}
    if not isinstance(cover, dict):
        return
    for material, cells in cover.items():
        for cell in (cells or []):
            if isinstance(cell, (list, tuple)) and len(cell) == 2:
                enc.set_cover((int(cell[0]), int(cell[1])), str(material))


def _npc_block(module: Module, key: str) -> dict | None:
    """敌人数据块：模组 ``npc_table`` → ``custom_npcs`` → 内置 ``D.NPCS``。"""
    for holder in (getattr(module, "npc_table", None),
                   getattr(module, "custom_npcs", None)):
        if isinstance(holder, dict) and isinstance(holder.get(key), dict):
            return holder[key]
    block = D.NPCS.get(key)
    return block if isinstance(block, dict) else None


# ---------------------------------------------------------------- 存档
def _aipc_to_json(obj):
    """AI PC 块内 persona/card → JSON 形态：st 对象经 to_dict()，dict 原样透传。"""
    if obj is None:
        return None
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return obj if isinstance(obj, dict) else None


def _aipc_from_json(raw, class_name: str):
    """aipc 块内 persona/card 反序列化（aipc-design §3.2）。

    trpg.st.card 已落地时用其 from_dict 重建为对象；st 未就绪（持久化通路
    先行合入）或数据异常时保留原始 dict——内容无损，就绪后读档即恢复为对象，
    接缝层（server.build_session）同时接受两种形态。"""
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        from ..st import card as _st_card
        return getattr(_st_card, class_name).from_dict(raw)
    except Exception:  # noqa: BLE001 —— st 缺失/数据损坏时保留原始形态
        return raw


def save_game(state: GameState, path: str | Path) -> Path:
    """写 RED 存档：顶层 ``ruleset`` + 角色 ``Character.to_dict()``（§4）。"""
    _ver, ints, _gauss = state.rng.getstate()
    data = {
        "ruleset": RULESET,
        "module_id": state.module.id,
        "scene_id": state.scene_id,
        "visited": list(state.visited),
        "flags": dict(state.flags),
        "party_eurobucks": int(getattr(state, "party_eurobucks", 0) or 0),
        "player": state.player.to_dict(),
        "companions": [c.to_dict() for c in state.companions],
        "rng_state": list(ints),
        "aipc": {
            "enabled": bool(getattr(state, "aipc_enabled", False)),
            "persona": _aipc_to_json(getattr(state, "persona", None)),
            "card": _aipc_to_json(getattr(state, "aipc_card", None)),
        },
        "map": state.map,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_game(path: str | Path, module: Module | None = None) -> GameState:
    """读 RED 存档；``ruleset`` 缺失或不是 ``cyberpunk-red`` → 拒绝加载。

    旧 D&D 5e 存档没有 ``ruleset`` 键（或有别的值），一律不静默载入——
    字段（AC/法术位/等级/金币）与新规则层不兼容，硬读只会产生错误角色。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ruleset = data.get("ruleset")
    if ruleset != RULESET:
        raise ValueError(
            "存档不兼容：这不是《赛博朋克 RED》的存档"
            f"（规则集标记为 {ruleset!r}，需要 {RULESET!r}）。"
            "旧 D&D 5e 存档无法继续，请开始新游戏。")
    player = Character.from_dict(data["player"])
    companions = [Character.from_dict(d) for d in data.get("companions") or []]
    if module is None:
        module = M.BUILTIN
    rng = random.Random()
    if data.get("rng_state"):
        rng.setstate((3, tuple(data["rng_state"]), None))
    st = GameState(player, companions, rng, module=module)
    st.flags = dict(data.get("flags") or {})
    st.scene_id = data.get("scene_id") or module.first_scene_id()
    st.visited = list(data.get("visited") or [])
    st.party_eurobucks = int(data.get("party_eurobucks", player.eurobucks) or 0)
    # AI PC（§3.2）：旧档无 aipc 键 → 开关关闭、persona/卡为空
    aipc = data.get("aipc")
    aipc = aipc if isinstance(aipc, dict) else {}
    st.aipc_enabled = bool(aipc.get("enabled"))
    st.persona = _aipc_from_json(aipc.get("persona"), "Persona")
    st.aipc_card = _aipc_from_json(aipc.get("card"), "CharacterCard")
    # 战术地图：无图模组 → None（剧场模式）；有则原样恢复（纯 JSON 形态）
    mp = data.get("map")
    if isinstance(mp, dict) and mp.get("grid"):
        mp.setdefault("meters_per_cell", METERS_PER_CELL)
        st.map = mp
    else:
        st.map = None
    return st
