"""游戏运行时状态：队伍、剧情旗标、场景指针、存档。"""

from __future__ import annotations

import json
import random
from pathlib import Path

from ..rules.character import Character
from ..campaign import module_data as M
from ..campaign.loader import Module


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


class EncounterBuildError(Exception):
    pass


def build_encounter(encounter_id: str, state: GameState,
                    surprised: bool = False) -> "Encounter":  # noqa: F821
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
    return st
