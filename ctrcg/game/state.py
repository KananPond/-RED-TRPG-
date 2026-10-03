"""游戏运行时状态：队伍、剧情旗标、场景指针、存档。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import TYPE_CHECKING

from ..rules.character import Character
from ..campaign import module_data as M
from ..campaign.loader import Module

if TYPE_CHECKING:
    from ..rules.combat import Encounter


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
        # AI PC（docs/aipc-design.md §3.1）：全部带默认值，既有构造调用方零改动。
        # ctrcg.st（st.card）未落地时 persona/aipc_card 以 dict 序列形态暂存，
        # st 就绪后由读档（from_dict）与 server.build_session 接缝重建为对象。
        self.aipc_enabled: bool = False   # 本局是否 AI 代打（构建期快照，局内只读）
        self.persona = None               # 用户 persona：Persona | None（AI PC 关闭时也可供 DM 上下文用）
        self.aipc_card = None             # 玩家角色卡：CharacterCard | None（建卡/读档时填充）

    # ------------------------------------------------------------ 队伍
    def party(self) -> list[Character]:
        return [self.player] + self.companions

    def find_member(self, name: str) -> Character | None:
        key = name.strip().lower()
        for c in self.party():
            if c.name.lower() == key or key in c.name.lower():
                return c
        return None

    def alive_party(self) -> list[Character]:
        return [c for c in self.party() if c.hp_cur > 0]

    def party_snapshot(self) -> str:
        lines = []
        for c in self.party():
            slots = "、".join(f"{lv}环×{n}" for lv, n in c.spell_slots().items() if n > 0) or "—"
            conds = "、".join(M.COND_CN.get(x, x) for x in sorted(c.conditions)) if c.conditions else "正常"
            res = "；".join(c.resource_lines())
            lines.append(f"{c.name}（{M.D_NAME.get(c.klass, c.klass)}{c.level}级）"
                         f" HP {c.hp_cur}/{c.hp_max} AC {c.ac()} 法术位[{slots}]"
                         f" 状态[{conds}] {res}")
        return "\n".join(lines)

    def epilogue_snapshot(self) -> dict:
        """终局卡数据：队伍结局、金币、旗标、足迹。"""
        return {
            "module_title": self.module.title,
            "scene_id": self.scene_id,
            "visited": len(self.visited),
            "scenes_total": len(self.module.scenes),
            "gold": getattr(self.player, "gold", 0),
            "flags": dict(self.flags),
            "party": [{"name": c.name, "klass": M.D_NAME.get(c.klass, c.klass),
                       "level": c.level, "hp": c.hp_cur, "hp_max": c.hp_max,
                       "dead": "dead" in c.conditions,
                       "down": c.hp_cur <= 0 and "dead" not in c.conditions}
                      for c in self.party()],
        }


class EncounterBuildError(Exception):
    pass


def build_encounter(encounter_id: str, state: GameState,
                    surprised: bool = False) -> "Encounter":
    from ..rules.combat import Encounter, Monster
    spec = state.module.encounter(encounter_id)
    if spec is None:
        raise EncounterBuildError(f"未知遭遇 {encounter_id}")
    enc = Encounter(encounter_id, title=spec["title"], dark=spec.get("dark", False))
    # 同类多只要编号，避免目标重名
    for mid, count in spec["monsters"]:
        tags = [str(i + 1) for i in range(count)] if count > 1 else [""]
        for tag in tags:
            enc.add(Monster(mid, state.rng, tag=tag,
                            table=state.module.monster_table), "enemy")
    for c in state.alive_party():
        enc.add(c, "party")
    if surprised:
        enc.surprised = {m.name for m in enc.enemies()}
    return enc


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

    ctrcg.st.card 已落地时用其 from_dict 重建为对象；st 未就绪（持久化通路
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
    _ver, ints, _gauss = state.rng.getstate()
    data = {
        "module_id": state.module.id,
        "player": state.player.to_dict(),
        "companions": [c.to_dict() for c in state.companions],
        "flags": state.flags,
        "scene_id": state.scene_id,
        "visited": state.visited,
        "rng_state": list(ints),
        # AI PC 块（§3.2）：旧档无此键 → 读档默认关闭，完全兼容；
        # 块内只有开关与非敏感叙事文本（persona/角色卡），无任何密钥字段。
        "aipc": {
            "enabled": bool(getattr(state, "aipc_enabled", False)),
            "persona": _aipc_to_json(getattr(state, "persona", None)),
            "card": _aipc_to_json(getattr(state, "aipc_card", None)),
        },
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_game(path: str | Path, module: Module | None = None) -> GameState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if module is None:
        module = M.BUILTIN
    player = Character.from_dict(data["player"])
    companions = [Character.from_dict(d) for d in data["companions"]]
    rng = random.Random()
    rng.setstate((3, tuple(data["rng_state"]), None))
    st = GameState(player, companions, rng, module=module)
    st.flags = data.get("flags", {})
    st.scene_id = data.get("scene_id") or module.first_scene_id()
    st.visited = data.get("visited", [])
    # AI PC（§3.2）：旧档无 aipc 键 → 开关关闭、persona/卡为空
    aipc = data.get("aipc")
    aipc = aipc if isinstance(aipc, dict) else {}
    st.aipc_enabled = bool(aipc.get("enabled"))
    st.persona = _aipc_from_json(aipc.get("persona"), "Persona")
    st.aipc_card = _aipc_from_json(aipc.get("card"), "CharacterCard")
    return st
