"""《赛博朋克 RED》战斗引擎。

口径来自《Cyberpunk RED Easy Mode》第 18-23 页：

* 先攻 = 反应 + 1d10，从高到低排队。
* 每回合 = 1 个移动动作 + 1 个其他动作；移动距离 = 机动 × 2 米。
* 远程：反应 + 射击技能 + 1d10，对照**按射程带查表**的难度值；
  反应 8+ 的防守方可以选择用闪避对抗。
* 白刃战：敏捷 + 近战武器 对 敏捷 + 闪避，**无视防守方一半护甲**。
* 斗殴：敏捷 + 斗殴 对 敏捷 + 闪避，一次攻击动作打两下；含擒抱/锁喉/投掷。
* 伤害：先减被击中部位的护甲 SP，再减 PW；**护甲每次挡下伤害后 -1（磨损）**。
* 致命伤：近战/远程武器（至少两个伤害骰）掷出两个及以上的 6 → 额外 5 点无视护甲的
  伤害 + 掷 2d6 查致命伤表。
* 伤势等级：轻伤 / 重伤（PW ≤ 上限一半，全部检定 -2）/ 濒死（PW < 1，检定 -4、
  移动 -6，每回合开始做生存检定）。
* 掩体：按材质有耐久，耐久归零即被摧毁。

本模块**不**实现主规则书的自动武器/压制射击/榴弹/网络空间/载具战斗——轻松模式
未收录这些子系统，且它们依赖轻松模式技能表中不存在的「自动武器」技能。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import data as D
from . import dice as dice_mod
from .character import (Character, COND_CONTROLLED, COND_DEAD, COND_PRONE,
                        COND_STABILIZED, COND_UNCONSCIOUS, LOCATIONS,
                        LOCATION_NAMES, AIMED_SHOT_PENALTY)

#: 战术网格：1 格 = 2 米
METERS_PER_CELL = 2
#: 一回合最多推进的轮数（防死循环）
MAX_ROUNDS = 50


class BattleGrid:
    """战术网格（1 格 = 2 米）。纯几何，不依赖游戏状态。"""

    def __init__(self, w: int, h: int, obstacles=None):
        self.w = int(w)
        self.h = int(h)
        self.obstacles = {(int(x), int(y)) for x, y in (obstacles or [])}

    def inside(self, cell) -> bool:
        return 0 <= cell[0] < self.w and 0 <= cell[1] < self.h

    def dist(self, a, b) -> int:
        """切比雪夫距离（格）。"""
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

    def dist_m(self, a, b) -> int:
        """距离（米）。"""
        return self.dist(a, b) * METERS_PER_CELL

    def blocked(self, cell) -> bool:
        return cell in self.obstacles or not self.inside(cell)

    def has_los(self, a, b) -> bool:
        """Bresenham 直线检查视线（障碍格阻挡）。"""
        x0, y0 = a
        x1, y1 = b
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            if (x0, y0) != tuple(a) and (x0, y0) != tuple(b) \
                    and (x0, y0) in self.obstacles:
                return False
            if (x0, y0) == (x1, y1):
                return True
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def neighbors(self, cell):
        x, y = cell
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nxt = (x + dx, y + dy)
                if self.inside(nxt) and not self.blocked(nxt):
                    yield nxt

    def path(self, start, goal, occupied=None):
        """8 向 BFS 最短路径（不含起点）。终点被占时不返回路径。"""
        occupied = set(occupied or ())
        start, goal = tuple(start), tuple(goal)
        if start == goal or goal in occupied:
            return []
        prev = {start: None}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            for nxt in self.neighbors(cur):
                if nxt in prev or nxt in occupied:
                    continue
                prev[nxt] = cur
                if nxt == goal:
                    out = []
                    node = nxt
                    while node != start:
                        out.append(node)
                        node = prev[node]
                    return list(reversed(out))
                queue.append(nxt)
        return []

    def cells_within(self, center, radius_cells: int):
        """以某格为中心的方形范围（供掩体/范围效果使用）。"""
        cx, cy = center
        out = []
        for x in range(cx - radius_cells, cx + radius_cells + 1):
            for y in range(cy - radius_cells, cy + radius_cells + 1):
                if self.inside((x, y)):
                    out.append((x, y))
        return out


@dataclass
class Encounter:
    """一场遭遇战。``entry`` 为模组 encounters 里的那条定义。"""

    title: str = "遭遇"
    intro: str = ""
    entry: dict = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    allies: list = field(default_factory=list)      # 玩家 + 队友
    enemies: list = field(default_factory=list)     # NPC/敌人
    grid: BattleGrid | None = None
    cover: dict = field(default_factory=dict)       # 格 → [材质, 耐久]
    positions: dict = field(default_factory=dict)   # 名字 → 格坐标
    engagement: dict = field(default_factory=dict)  # 剧场模式：近身纠缠
    speed_left: dict = field(default_factory=dict)  # 本回合剩余移动（米）
    moved_this_turn: dict = field(default_factory=dict)
    order: list = field(default_factory=list)
    idx: int = 0
    round: int = 1
    log_lines: list = field(default_factory=list)
    rolls: list = field(default_factory=list)
    over: str | None = None                         # None | "party" | "tpk"
    retreated: bool = False

    # ------------------------------------------------------------ 初始化
    def __post_init__(self):
        self.title = self.entry.get("title", self.title)
        self.intro = self.entry.get("intro", self.intro)

    @property
    def combatants(self) -> list:
        return list(self.allies) + list(self.enemies)

    def alive(self, side: str | None = None) -> list:
        pool = self.allies if side == "party" else \
            self.enemies if side == "enemy" else self.combatants
        return [c for c in pool if not c.is_dead and c.hp_cur > 0]

    def find(self, who) -> Character | None:
        """按对象或名字（支持前缀）找参战者。"""
        if isinstance(who, Character):
            return who
        for c in self.combatants:
            if c.name == who:
                return c
        for c in self.combatants:
            if str(c.name).startswith(str(who)):
                return c
        return None

    # ------------------------------------------------------------ 开战
    def begin(self) -> None:
        """掷先攻（反应 + 1d10）并进入第一轮。"""
        entries = []
        for c in self.combatants:
            res = dice_mod.check_roll(self.rng, 0)
            total = c.stat("ref") + c.initiative_bonus() + res.rolls[0]
            entries.append((total, res.rolls[0], c))
        entries.sort(key=lambda e: (-e[0], -e[1]))
        self.order = [e[2] for e in entries]
        self.idx = 0
        self.round = 1
        for c in self.combatants:
            c.refresh_luck()
            self.speed_left[c.name] = c.move_value()
            self.moved_this_turn[c.name] = 0
        self.over = None
        self.log(f"◆ 战斗开始：{self.title}")
        for c in self.order:
            bonus = c.initiative_bonus()
            self.log(f"  先攻 {c.stat('ref') + bonus:+d} → {c.name}"
                     f"（{c.role_name}，PW {c.hp_cur}/{c.hp_max}）")
        self._deploy()
        self.begin_turn(self.current)

    def _deploy(self) -> None:
        """按场景声明布点；缺省时敌我分列网格两端。"""
        if self.grid is None:
            for c in self.combatants:
                self.engagement.setdefault(c.name, set())
            return
        declared = {}
        for tok in (self.entry.get("_tokens") or []):
            cell = tok.get("cell")
            if cell and len(cell) == 2:
                declared[tok.get("name")] = tuple(cell)
        spawns = self.entry.get("spawns") or {}
        used = set()
        for c in self.allies:
            cell = declared.get(c.name) or declared.get(c.display())
            if cell and cell not in used:
                self.positions[c.name] = cell
                used.add(cell)
        # 队伍落在左侧
        col = 1
        for c in self.allies:
            if c.name in self.positions:
                continue
            for y in range(self.grid.h):
                if (col, y) not in used and not self.grid.blocked((col, y)):
                    self.positions[c.name] = (col, y)
                    used.add((col, y))
                    break
        # 敌人落在右侧
        ecol = self.grid.w - 2
        for c in self.enemies:
            placed = False
            cells = spawns.get(c.source_key) or spawns.get(c.name)
            for cell in (cells or []):
                t = tuple(cell)
                if t not in used and not self.grid.blocked(t):
                    self.positions[c.name] = t
                    used.add(t)
                    placed = True
                    break
            if placed:
                continue
            for y in range(self.grid.h):
                if (ecol, y) not in used and not self.grid.blocked((ecol, y)):
                    self.positions[c.name] = (ecol, y)
                    used.add((ecol, y))
                    break
            else:
                ecol -= 1
        self._sync_engagement()

    # ------------------------------------------------------------ 回合
    @property
    def current(self) -> Character | None:
        if not self.order:
            return None
        # 有人撤退/离场后队列会变短，这里夹紧游标，避免越界
        if self.idx >= len(self.order):
            self.idx = len(self.order) - 1
        if self.idx < 0:
            self.idx = 0
        return self.order[self.idx]

    def retreat(self, c: Character) -> dict:
        """让参战者撤出战斗（脱离先攻队列与阵营列表）。

        剧本里的腐败警察在重伤时会试图撤离；战术地图上越过边界即视为成功撤退。
        返回 ``{"ok", "name", "order"}``。
        """
        if c in self.order:
            pos = self.order.index(c)
            self.order.remove(c)
            if pos < self.idx:
                self.idx -= 1
        for pool in (self.allies, self.enemies):
            if c in pool:
                pool.remove(c)
        self.positions.pop(c.name, None)
        self.speed_left.pop(c.name, None)
        self.moved_this_turn.pop(c.name, None)
        self.engagement.pop(c.name, None)
        for names in self.engagement.values():
            names.discard(c.name)
        self.log(f"→ {c.name} 撤出了战场。")
        return {"ok": True, "name": c.name, "order": [x.name for x in self.order]}

    def refresh_actions(self, c: Character) -> None:
        c.action_used = False
        c.bonus_used = False
        c.reaction_used = False
        c.turn_damage_taken = 0

    def begin_turn(self, c: Character) -> None:
        if c is None:
            return
        self.refresh_actions(c)
        self.speed_left[c.name] = c.move_value()
        self.moved_this_turn[c.name] = 0
        # 濒死：回合开始做生存检定
        if c.hp_cur < 1 and not c.is_dead:
            res = c.death_save(self.rng)
            self._rec_save(c, "生存检定", res["dv"], res["roll"])
            if res["dead"]:
                self.log(f"☠ {c.name} 的生存检定失败（1d10={res['face']} vs {res['dv']}）——死亡。")
            else:
                self.log(f"✚ {c.name} 的生存检定通过（1d10={res['face']} < {res['dv']}），"
                         f"本回合可以行动。")
        # 倒地者不能行动（昏迷）
        if COND_UNCONSCIOUS in c.conditions or c.is_dead:
            self.log(f"— {c.name} {'已死亡' if c.is_dead else '昏迷'}，跳过回合。")

    def end_turn(self, c: Character) -> None:
        if c is None:
            return
        if self.grid is not None:
            self._resolve_cover_damage(c)
        self._tick_timers(c)

    def next_turn(self) -> Character | None:
        """推进到下一个参战者；必要时开新一轮。"""
        if self.is_over():
            return None
        guard = 0
        while guard < len(self.order) * 2 + 2:
            guard += 1
            cur = self.current
            if cur is not None:
                self.end_turn(cur)
            self.idx += 1
            if self.idx >= len(self.order):
                self.idx = 0
                self.round += 1
                for c in self.combatants:
                    c.refresh_luck()
                self.log(f"—— 第 {self.round} 轮 ——")
            nxt = self.current
            if nxt is None:
                return None
            self.begin_turn(nxt)
            if nxt.is_dead or COND_UNCONSCIOUS in nxt.conditions or nxt.hp_cur < 1:
                continue
            return nxt
        return self.current

    def is_over(self) -> str | None:
        """返回 None / ``"party"``（队伍胜）/ ``"tpk"``（团灭）。"""
        if self.over:
            return self.over
        if not [c for c in self.enemies if not c.is_dead and c.hp_cur > 0]:
            self.over = "party"
        elif not [c for c in self.allies if not c.is_dead and c.hp_cur > 0]:
            self.over = "tpk"
        return self.over

    # ------------------------------------------------------------ 动作经济
    def has_action(self, c: Character) -> bool:
        return not c.action_used and not c.is_dead and c.hp_cur >= 0 \
            and COND_UNCONSCIOUS not in c.conditions

    def has_bonus(self, c: Character) -> bool:
        """「附赠动作」在 RED 中用于武器的第二发（攻击次数 LA 2）。"""
        return not c.bonus_used

    def use_action(self, c: Character) -> None:
        c.action_used = True

    def use_bonus(self, c: Character) -> None:
        c.bonus_used = True

    def use_reaction(self, c: Character) -> bool:
        if c.reaction_used:
            return False
        c.reaction_used = True
        return True

    # ------------------------------------------------------------ 距离与射程
    def measure(self, a: Character, b: Character) -> int:
        """两人之间的距离（米）。网格模式用坐标，剧场模式用纠缠近似。"""
        if self.grid is not None:
            pa, pb = self.positions.get(a.name), self.positions.get(b.name)
            if pa and pb:
                return self.grid.dist_m(pa, pb)
        if b.name in self.engagement.get(a.name, set()):
            return METERS_PER_CELL          # 近身
        if a.name in self.engagement.get(b.name, set()):
            return METERS_PER_CELL
        return 13                            # 默认中距离（13-25 米带）

    def range_band(self, kind: str, meters: int) -> tuple[int | None, int | None]:
        """按武器类别与距离查远程难度值。返回 (射程带序号, DV)。"""
        table = D.RANGED_DV.get(kind)
        if not table:
            return None, None
        for i, (lo, hi) in enumerate(D.RANGE_BANDS):
            if lo <= meters <= hi:
                dv = table[i] if i < len(table) else None
                return i, dv
        return None, None

    def _range_gate(self, att: Character, tgt: Character, weapon: dict):
        """远程射程校验：返回 ``{"ok": True, "dv": int, "band": str}`` 或错误字典。"""
        kind = weapon.get("kind")
        meters = self.measure(att, tgt)
        band, dv = self.range_band(kind, meters)
        if dv is None:
            return {"ok": False, "reason": f"{weapon.get('name')} 打不到 {meters} 米外的目标"}
        band_name = f"{D.RANGE_BANDS[band][0]}-{D.RANGE_BANDS[band][1]} 米" if band is not None else "?"
        if self.grid is not None and not self._has_line_of_sight(att, tgt):
            return {"ok": False, "reason": f"{tgt.name} 处于掩体/障碍之后，没有射击线"}
        return {"ok": True, "dv": dv, "band": band_name, "meters": meters}

    def _has_line_of_sight(self, att: Character, tgt: Character) -> bool:
        """障碍格挡视线；**掩体不挡视线**——掩体是"挡子弹"，由伤害结算吸收。"""
        if self.grid is None:
            return True
        pa, pb = self.positions.get(att.name), self.positions.get(tgt.name)
        if not pa or not pb:
            return True
        return self.grid.has_los(pa, pb)

    def cover_at(self, cell) -> tuple | None:
        """该格的掩体 ``[材质, 剩余耐久]``（无掩体返回 None）。"""
        return self.cover.get(tuple(cell))

    def set_cover(self, cell, material: str, hp: int | None = None) -> None:
        """放置掩体（材料决定耐久，见 ``D.COVER``）。"""
        self.cover[tuple(cell)] = [material,
                                   int(hp if hp is not None else D.COVER.get(material, 10))]

    def cover_hp(self, cell) -> int:
        c = self.cover_at(cell)
        return int(c[1]) if c else 0

    def _absorb_by_cover(self, tgt: Character, damage: int) -> dict | None:
        """掩体吸收：伤害先打在掩体上；掩体被摧毁时多出的伤害作废。

        规则（轻松模式第 21 页）：只有能挡下子弹的东西才算掩体；掩体耐久归零即被
        摧毁，本次攻击剩余的伤害作废，掩体后的角色不会受到这些伤害。
        """
        if self.grid is None or damage <= 0:
            return None
        cell = self.positions.get(tgt.name)
        cover = self.cover_at(cell) if cell else None
        if not cover or cover[1] <= 0:
            return None
        before = int(cover[1])
        cover[1] = max(0, before - int(damage))
        destroyed = cover[1] <= 0
        self.log(f"◼ 掩体（{cover[0]}）替 {tgt.name} 挡下 {min(before, damage)} 点伤害"
                 + (f"，掩体被摧毁（原耐久 {before}）——本次剩余伤害作废。" if destroyed
                    else f"，剩余耐久 {cover[1]}。"))
        return {"cover": cover[0], "before": before, "left": cover[1],
                "destroyed": destroyed, "absorbed": min(before, damage)}

    # ------------------------------------------------------------ 攻击
    @staticmethod
    def _magazine(att: Character, key: str, weapon: dict) -> int | None:
        """弹匣余量；无需装填的武器返回 None。缺省按满匣初始化。"""
        cap = weapon.get("ammo")
        if not cap:
            return None
        have = att.ammo.get(key)
        if have is None:
            att.ammo[key] = int(cap)
            have = int(cap)
        return int(have)

    def _magazine_empty(self, att: Character, key: str, weapon: dict) -> bool:
        have = self._magazine(att, key, weapon)
        return have is not None and have <= 0

    def _melee_gate(self, att: Character, tgt: Character, weapon: dict) -> dict:
        """白刃/斗殴必须先贴身才能打（网格：相邻格；剧场：近身纠缠）。

        距离靠 ``measure()`` 统一表达：网格模式看坐标，剧场模式看纠缠表。
        """
        meters = self.measure(att, tgt)
        if meters > METERS_PER_CELL:
            return {"ok": False,
                    "reason": f"{tgt.name} 不在{weapon.get('name')}的攻击范围内"
                              f"（白刃/斗殴必须贴身，当前 {meters} 米）"}
        return {"ok": True}

    def attack_action(self, att: Character, tgt: Character, weapon_key: str | None = None,
                      aimed: bool = False, luck: int = 0) -> dict:
        """一次攻击动作：按武器攻击次数（LA/ROF）连续攻击。

        打不到（超出射程 / 没有射击线）时**不消耗动作**——调用方可以改成移动或换武器。
        """
        if not self.has_action(att):
            return {"ok": False, "reason": f"{att.name} 本回合已经没有动作了"}
        # 默认武器：优先射程内的枪，其次近战，最后徒手
        weapon_key = weapon_key or att.best_weapon("ranged") \
            or att.best_weapon() or "brawling"
        weapon = D.WEAPONS.get(weapon_key)
        if weapon is None:
            return {"ok": False, "reason": f"未知武器 {weapon_key}"}
        if weapon.get("kind") not in ("melee", "brawling"):
            gate = self._range_gate(att, tgt, weapon)
            if not gate.get("ok"):
                return {"ok": False, "reason": gate.get("reason"), "not_spent": True}
            if self._magazine_empty(att, weapon_key, weapon):
                return {"ok": False, "not_spent": True,
                        "reason": f"{att.name} 的{weapon.get('name')}弹匣空了，需要先装填"}
        else:
            gate = self._melee_gate(att, tgt, weapon)
            if not gate.get("ok"):
                return {"ok": False, "reason": gate.get("reason"), "not_spent": True}
        rof = int(weapon.get("rof", 1))
        if weapon.get("kind") == "brawling":
            rof = D.BRAWLING_ATTACKS
        self.use_action(att)
        results = []
        for i in range(rof):
            if tgt.is_dead or (tgt.hp_cur < 1 and weapon.get("kind") != "brawling"):
                break
            res = self.attack(att, tgt, weapon_key, aimed=aimed, luck=luck if i == 0 else 0)
            results.append(res)
            if self.is_over():
                break
        return {"ok": True, "attacks": results, "weapon": weapon_key,
                "damage": sum(r.get("damage", 0) for r in results)}

    def attack(self, att: Character, tgt: Character, weapon_key: str,
               aimed: bool = False, luck: int = 0) -> dict:
        """单次攻击结算。返回命中/伤害/致命伤明细。"""
        weapon = D.WEAPONS.get(weapon_key)
        if weapon is None:
            return {"ok": False, "reason": f"未知武器 {weapon_key}"}
        kind = weapon.get("kind", "melee")
        skill = weapon.get("skill", "melee_weapon")
        penalty = AIMED_SHOT_PENALTY if aimed else 0
        if kind == "melee":
            penalty += att.melee_penalty()
        location = "head" if aimed and weapon.get("kind") in ("pistol", "shotgun",
                                                              "rifle") else "torso"
        entry = {"attacker": att.name, "target": tgt.name, "weapon": weapon_key,
                 "weapon_name": weapon.get("name", weapon_key),
                 "aimed": bool(aimed), "location": location}

        if kind in ("melee", "brawling"):
            gate = self._melee_gate(att, tgt, weapon)
            if not gate.get("ok"):
                entry.update({"ok": False, "hit": False, "damage": 0,
                              "not_spent": True, "reason": gate.get("reason")})
                return entry
            res, def_res, hit = att.opposed_check(
                self.rng, skill, tgt, "evasion",
                luck=luck, penalty=penalty)
            entry["attack_total"] = res.total
            entry["defense_total"] = def_res.total
            entry["dv"] = def_res.total
            entry["attack_rolls"] = res.rolls
            entry["defense_rolls"] = def_res.rolls
            self._rec_attack(att, tgt, res, f"{weapon.get('name')}")
        else:
            gate = self._range_gate(att, tgt, weapon)
            if not gate.get("ok"):
                entry.update({"ok": False, "hit": False, "damage": 0,
                              "reason": gate.get("reason")})
                return entry
            # 开火消耗弹药（打完就得装填）
            have = self._magazine(att, weapon_key, weapon)
            if have is not None:
                if have <= 0:
                    entry.update({"ok": False, "hit": False, "damage": 0,
                                  "not_spent": True,
                                  "reason": f"{weapon.get('name')}弹匣空了，需要先装填"})
                    return entry
                att.ammo[weapon_key] = have - 1
            dv = int(gate["dv"]) + (abs(AIMED_SHOT_PENALTY) if aimed else 0)
            # 闪避子弹：REF 8+ 且尚未用掉反应
            if tgt.can_evade_bullets() and not tgt.reaction_used:
                tgt.reaction_used = True
                res, def_res, hit = att.opposed_check(self.rng, skill, tgt, "evasion",
                                                      luck=luck)
                entry["dodged"] = True
                entry["dv"] = def_res.total
                entry["defense_rolls"] = def_res.rolls
            else:
                res = att.check(self.rng, skill, dv=dv, luck=luck, penalty=-abs(penalty)
                                if penalty else 0)
                hit = bool(res.success)
                entry["dv"] = dv
            entry["band"] = gate.get("band")
            entry["meters"] = gate.get("meters")
            entry["attack_total"] = res.total
            entry["attack_rolls"] = res.rolls
            self._rec_attack(att, tgt, res, f"{weapon.get('name')}", dv=entry["dv"])

        entry["hit"] = bool(hit)
        if not hit:
            entry["damage"] = 0
            entry["ok"] = True
            return entry

        # ---- 伤害
        dmg_expr = att.weapon_damage(weapon_key)
        dmg = dice_mod.roll(self.rng, dmg_expr)
        rolls = list(dmg.rolls)
        sixes = dice_mod.count_faces(dmg, 6)
        n_dice = len(rolls)
        raw = dmg.total
        crit = (n_dice >= D.CRIT_INJURY_MIN_DICE and sixes >= D.CRIT_INJURY_SIXES)
        entry["damage_rolls"] = rolls
        entry["raw_damage"] = raw
        # 掩体：完全躲在能挡子弹的东西后面时，伤害先打在掩体上
        absorb = self._absorb_by_cover(tgt, raw)
        if absorb:
            entry["cover"] = absorb
            entry["damage"] = 0
            entry["armor"] = 0
            entry["armor_left"] = tgt.armor_at(location)
            entry["ablated"] = False
            entry["ok"] = True
            self.log(f"→ {att.name} 用{weapon.get('name')}命中掩体（{dmg_expr}="
                     f"{sum(rolls)}），{tgt.name} 没有受伤。")
            return entry
        if crit:
            raw += D.CRIT_INJURY_BONUS_DAMAGE
        ap = (kind == "melee")
        applied = self.apply_damage(tgt, raw - (D.CRIT_INJURY_BONUS_DAMAGE if crit else 0),
                                    location=location, ap=ap)
        extra = 0
        if crit:
            # 致命伤的额外 5 点无视护甲（掩体已被上面的判断排除）
            extra = self.apply_damage(tgt, D.CRIT_INJURY_BONUS_DAMAGE,
                                      location=location, ignore_armor=True)["hp"]
            roll2d6 = dice_mod.roll(self.rng, "2d6")
            inj = tgt.apply_crit_injury(roll2d6.total)
            entry["crit_injury"] = {"roll": roll2d6.total, "name": inj["name"],
                                    "text": inj["text"]}
            self.log(f"✖ 致命伤！{tgt.name} 受到「{inj['name']}」（2d6={roll2d6.total}）："
                     f"{inj['text']}")
        entry["damage"] = applied["hp"] + extra
        entry["armor"] = applied["sp"]
        entry["armor_left"] = applied["sp_left"]
        entry["ablated"] = applied["ablate"]
        entry["ok"] = True
        self.log(
            f"→ {att.name} 用{weapon.get('name')}命中 {tgt.name}"
            f"（{'瞄准' + LOCATION_NAMES[location] if aimed else LOCATION_NAMES[location]}，"
            f"{dmg_expr}={sum(rolls)}），护甲挡下 {applied['sp']}，"
            f"造成 {entry['damage']} 点伤害；{tgt.name} PW {tgt.hp_cur}/{tgt.hp_max}"
            + (f"，护甲磨损至 {applied['sp_left']}" if applied["ablate"] else ""))
        if tgt.hp_cur < 1 and not tgt.is_dead:
            self.log(f"✚ {tgt.name} 倒地濒死，需要有人稳定他。")
        return entry

    def apply_damage(self, tgt: Character, amount: int, location: str = "torso",
                     ignore_armor: bool = False, ap: bool = False) -> dict:
        """统一的伤害入口（护甲 SP → 磨损 → PW）。"""
        before = tgt.hp_cur
        res = tgt.take_damage(amount, location=location,
                              ignore_armor=ignore_armor, ap=ap)
        res["before"] = before
        if COND_STABILIZED in tgt.conditions and res["hp"] > 0:
            tgt.conditions.discard(COND_STABILIZED)
        if tgt.hp_cur < 1 and not tgt.is_dead:
            tgt.conditions.add(COND_UNCONSCIOUS)
        return res

    def apply_resistance(self, tgt: Character, amount: int,
                         location: str = "torso") -> tuple[int, list[str]]:
        """向后兼容的护甲结算入口：返回 (实际扣血, 说明列表)。"""
        res = self.apply_damage(tgt, amount, location=location)
        notes = [f"护甲 SP {res['sp']}"]
        if res["ablate"]:
            notes.append(f"护甲磨损至 {res['sp_left']}")
        return res["hp"], notes

    # ------------------------------------------------------------ 特殊动作
    def grab(self, att: Character, tgt: Character, take_item: bool = False) -> dict:
        """擒抱：敏捷 + 斗殴 对抗 敏捷 + 斗殴（或闪避）。"""
        if not self.has_action(att):
            return {"ok": False, "reason": "没有动作了"}
        self.use_action(att)
        res, def_res, win = att.opposed_check(self.rng, "brawling", tgt, "brawling")
        out = {"ok": True, "win": bool(win), "attack_total": res.total,
               "defense_total": def_res.total, "attack_rolls": res.rolls,
               "defense_rolls": def_res.rolls}
        if win:
            if take_item:
                out["action"] = "夺取物品"
                self.log(f"→ {att.name} 擒抱并夺走了 {tgt.name} 手中的东西。")
            else:
                tgt.conditions.add(COND_CONTROLLED)
                att.conditions.add(COND_CONTROLLED)
                out["action"] = "控制"
                self.log(f"→ {att.name} 控制住了 {tgt.name}（双方动作 -2）。")
        else:
            self.log(f"→ {att.name} 的擒抱被 {tgt.name} 挣脱。")
        return out

    def escape_grab(self, att: Character, tgt: Character) -> dict:
        """挣脱控制：一次动作，成功则双方解除控制。"""
        if not self.has_action(att):
            return {"ok": False, "reason": "没有动作了"}
        self.use_action(att)
        res, def_res, win = att.opposed_check(self.rng, "brawling", tgt, "brawling")
        if win:
            att.conditions.discard(COND_CONTROLLED)
            tgt.conditions.discard(COND_CONTROLLED)
            att.timers.pop("choke_rounds", None)
            self.log(f"→ {att.name} 挣脱了 {tgt.name} 的控制。")
        else:
            self.log(f"→ {att.name} 没能挣脱 {tgt.name}。")
        return {"ok": True, "win": bool(win), "attack_total": res.total,
                "defense_total": def_res.total}

    def choke(self, att: Character, tgt: Character) -> dict:
        """锁喉：对已被控制的对手造成等于体格的伤害（无视护甲、不磨损护甲）。"""
        if COND_CONTROLLED not in tgt.conditions or COND_CONTROLLED not in att.conditions:
            return {"ok": False, "reason": "必须先控制住对方才能锁喉"}
        if not self.has_action(att):
            return {"ok": False, "reason": "没有动作了"}
        self.use_action(att)
        dmg = att.stat("body")
        res = self.apply_damage(tgt, dmg, location="torso", ignore_armor=True)
        rounds = int(att.timers.get("choke_rounds", 0)) + 1
        att.timers["choke_rounds"] = rounds
        ko = False
        if res["before"] > 1 and tgt.hp_cur < 1:
            tgt.hp_cur = 1
            tgt.conditions.add(COND_UNCONSCIOUS)
            ko = True
        if rounds >= D.CHOKE_KO_ROUNDS:
            tgt.conditions.add(COND_UNCONSCIOUS)
            ko = True
        self.log(f"→ {att.name} 锁喉 {tgt.name}：{dmg} 点无视护甲伤害"
                 f"（连续第 {rounds} 轮）" + ("，目标失去意识！" if ko else ""))
        return {"ok": True, "damage": dmg, "ko": ko, "rounds": rounds,
                "hp": tgt.hp_cur}

    def throw_target(self, att: Character, tgt: Character) -> dict:
        """投掷：造成等于体格的伤害（无视护甲），目标倒地，擒抱结束。"""
        if COND_CONTROLLED not in tgt.conditions:
            return {"ok": False, "reason": "目标没有被控制"}
        if not self.has_action(att):
            return {"ok": False, "reason": "没有动作了"}
        self.use_action(att)
        dmg = att.stat("body")
        res = self.apply_damage(tgt, dmg, location="torso", ignore_armor=True)
        tgt.conditions.add(COND_PRONE)
        att.conditions.discard(COND_CONTROLLED)
        tgt.conditions.discard(COND_CONTROLLED)
        att.timers.pop("choke_rounds", None)
        self.log(f"→ {att.name} 把 {tgt.name} 摔在地上：{dmg} 点无视护甲伤害，目标倒地。")
        return {"ok": True, "damage": dmg, "hp": tgt.hp_cur}

    def stand(self, c: Character) -> dict:
        """起身：消耗移动动作的一半距离。"""
        if COND_PRONE not in c.conditions:
            return {"ok": False, "reason": "并没有倒地"}
        cost = c.move_value() // 2
        if self.speed_left.get(c.name, 0) < cost:
            return {"ok": False, "reason": f"移动力不足（起身需要 {cost} 米）"}
        self.speed_left[c.name] = self.speed_left.get(c.name, 0) - cost
        c.conditions.discard(COND_PRONE)
        self.log(f"→ {c.name} 站起身来（消耗 {cost} 米移动力）。")
        return {"ok": True, "cost": cost}

    def reload(self, c: Character, weapon_key: str) -> dict:
        """装填：把弹匣装满（一个动作）。"""
        if not self.has_action(c):
            return {"ok": False, "reason": "没有动作了"}
        weapon = D.WEAPONS.get(weapon_key) or {}
        capacity = weapon.get("ammo")
        if not capacity:
            return {"ok": False, "reason": f"{weapon.get('name')} 不需要装填"}
        self.use_action(c)
        c.ammo[weapon_key] = int(capacity)
        self.log(f"→ {c.name} 给{weapon.get('name')}装满弹匣（{capacity} 发）。")
        return {"ok": True, "ammo": int(capacity)}

    def stabilize_action(self, att: Character, tgt: Character) -> dict:
        """稳定：技术 + 急救/医疗救援 对抗按伤势等级定的难度值。"""
        if not self.has_action(att):
            return {"ok": False, "reason": "没有动作了"}
        self.use_action(att)
        skill = "paramedic" if att.skill("paramedic") >= att.skill("first_aid") else "first_aid"
        dv = tgt.stabilize_dv()
        res = att.check(self.rng, skill, dv=dv)
        out = {"ok": True, "dv": dv, "success": bool(res.success),
               "total": res.total, "skill": skill, "rolls": res.rolls}
        self._rec_check(att, "稳定", res, dv)
        if res.success:
            tgt.stabilize()
            self.log(f"✔ {att.name} 用{att.skill_name(skill)}（{res.total} vs DV {dv}）"
                     f"稳定了 {tgt.name}：PW 回到 1，生存检定重新开始。")
        else:
            self.log(f"✘ {att.name} 没能稳定 {tgt.name}（{res.total} vs DV {dv}）。")
        return out

    def first_aid_fix(self, att: Character, tgt: Character, index: int = 0) -> dict:
        """急救固定：当天消除一处致命伤效果（耗时一分钟，可对自己用）。"""
        if not tgt.crit_injuries:
            return {"ok": False, "reason": f"{tgt.name} 没有致命伤"}
        index = max(0, min(index, len(tgt.crit_injuries) - 1))
        inj = tgt.crit_injuries[index]
        if inj.get("stabilized"):
            return {"ok": False,
                    "reason": f"「{inj['name']}」今天已经急救固定过了（每处每天一次）"}
        info = D.CRIT_INJURIES.get(inj.get("roll", 7)) or {}
        dv = info.get("first_aid_stabilize_dv")
        if dv is None:
            return {"ok": False, "reason": f"「{inj['name']}」无法用急救固定处理，需要外科手术"}
        skill = "paramedic" if att.skill("paramedic") >= att.skill("first_aid") else "first_aid"
        res = att.check(self.rng, skill, dv=dv)
        self._rec_check(att, "急救固定", res, dv)
        if res.success:
            tgt.first_aid_fix(index)
            self.log(f"✔ {att.name} 急救固定了 {tgt.name} 的「{inj['name']}」"
                     f"（{res.total} vs DV {dv}），当天不再受其影响。")
        else:
            self.log(f"✘ {att.name} 的急救固定失败（{res.total} vs DV {dv}）。")
        return {"ok": True, "success": bool(res.success), "dv": dv,
                "injury": inj["name"], "total": res.total}

    def treat_crit_injury(self, att: Character, tgt: Character, index: int = 0) -> dict:
        """治疗：永久消除一处致命伤（四小时；只有医疗师能做外科）。"""
        if att is tgt:
            return {"ok": False, "reason": "无法治疗自己"}
        if not tgt.crit_injuries:
            return {"ok": False, "reason": f"{tgt.name} 没有致命伤"}
        inj = tgt.crit_injuries[max(0, min(index, len(tgt.crit_injuries) - 1))]
        info = (D.CRIT_INJURIES.get(inj.get("roll", 7)) or {}).get("treatment") or {}
        skill, dv = info.get("skill", "first_aid"), int(info.get("dv", 13))
        if skill == "paramedic" and att.skill("paramedic") == 0:
            return {"ok": False, "reason": "该致命伤需要外科手术，只有医疗师能处理"}
        res = att.check(self.rng, skill, dv=dv)
        self._rec_check(att, "治疗", res, dv)
        if res.success:
            tgt.treat_crit_injury(index)
            self.log(f"✔ {att.name} 用{att.skill_name(skill)}治好了 {tgt.name} 的"
                     f"「{inj['name']}」（{res.total} vs DV {dv}，耗时四小时）。")
        else:
            self.log(f"✘ {att.name} 的治疗失败（{res.total} vs DV {dv}），需要再试一次。")
        return {"ok": True, "success": bool(res.success), "dv": dv,
                "injury": inj["name"], "total": res.total}

    def use_item(self, c: Character, item: str) -> dict:
        """使用物品（使用物品不需要技能掷骰）。"""
        if item in D.WEAPONS:
            c.weapons.append(item) if item not in c.weapons else None
            self.log(f"→ {c.name} 取出{item}。")
            return {"ok": True}
        if not c.use_item(item):
            return {"ok": False, "reason": f"{c.name} 没有{item}"}
        gear = D.GEAR.get(item) or {}
        self.log(f"→ {c.name} 使用了{gear.get('name', item)}。")
        return {"ok": True, "item": item}

    def confrontation(self, att: Character, tgt: Character) -> dict:
        """对峙：双方各检定 魅力 + 声望 + 1d10，败者退让或对该对手所有动作 -2。"""
        a = dice_mod.check_roll(self.rng, att.stat("cool") + att.reputation)
        b = dice_mod.check_roll(self.rng, tgt.stat("cool") + tgt.reputation)
        self._rec_check(att, "对峙", a, b.total)
        self._rec_check(tgt, "对峙", b, a.total)
        if a.total > b.total:
            loser = tgt
        elif b.total > a.total:
            loser = att
        else:
            self.log(f"→ {att.name} 与 {tgt.name} 对峙平分秋色，只能动真格的。")
            return {"ok": True, "tie": True, "attack_total": a.total,
                    "defense_total": b.total}
        loser.timers["facedown"] = {"against": (tgt.name if loser is att else att.name),
                                    "penalty": -2}
        self.log(f"→ 对峙：{att.name} {a.total} vs {tgt.name} {b.total}——"
                 f"{loser.name} 要么退让，要么对对手的所有动作 -2。")
        return {"ok": True, "winner": (att.name if loser is tgt else tgt.name),
                "loser": loser.name, "attack_total": a.total, "defense_total": b.total}

    # ------------------------------------------------------------ 移动
    def same_side(self, a: Character, b: Character) -> bool:
        """两人是否同一阵营（借机攻击只对敌人生效，队友之间不会互相开火）。"""
        a_ally = any(x is a for x in self.allies)
        b_ally = any(x is b for x in self.allies)
        return a_ally == b_ally

    def move(self, c: Character, dest, spend_all: bool = False) -> dict:
        """移动（消耗移动力，不消耗动作）。网格模式下 ``dest`` 为格坐标。"""
        if COND_PRONE in c.conditions:
            return {"ok": False, "reason": "倒地时不能移动，必须先起身"}
        budget = self.speed_left.get(c.name, 0)
        if budget <= 0:
            return {"ok": False, "reason": f"{c.name} 本回合的移动力已用尽"}
        if self.grid is None:
            self.speed_left[c.name] = 0
            self.log(f"→ {c.name} 移动（剧场模式）。")
            return {"ok": True, "spent": budget}
        start = self.positions.get(c.name)
        if start is None:
            return {"ok": False, "reason": f"{c.name} 不在场上"}
        dest = tuple(dest)
        if self.grid.blocked(dest):
            return {"ok": False, "reason": f"目标格 {dest} 不可进入"}
        occupied = {self.positions[n] for n in self.positions if n != c.name}
        path = self.grid.path(start, dest, occupied)
        if not path:
            return {"ok": False, "reason": f"没有通往 {dest} 的路径"}
        cost = len(path) * METERS_PER_CELL
        if not spend_all and cost > budget:
            return {"ok": False, "reason": f"移动力不足（需要 {cost} 米，剩 {budget} 米）"}
        spent = 0
        prev = start
        for cell in path:
            if spent + METERS_PER_CELL > budget:
                break
            # 借机攻击：从贴身离开时触发（只由敌人发动）
            for other in self.combatants:
                if other is c or other.hp_cur < 1 or other.is_dead:
                    continue
                if self.same_side(c, other):
                    continue
                op = self.positions.get(other.name)
                if op and self.grid.dist(prev, op) <= 1 and self.grid.dist(cell, op) > 1:
                    if self.use_reaction(other):
                        self.log(f"⚔ {other.name} 对脱离接触的 {c.name} 发动借机攻击！")
                        self.attack(other, c, other.best_weapon() or "brawling")
            prev = cell
            spent += METERS_PER_CELL
        self.positions[c.name] = prev
        self.speed_left[c.name] = max(0, budget - spent)
        self.moved_this_turn[c.name] = self.moved_this_turn.get(c.name, 0) + spent
        self._sync_engagement()
        self.log(f"→ {c.name} 移动到 {prev}（{spent} 米，剩 {self.speed_left[c.name]} 米）。")
        self._check_move_hurt(c)
        return {"ok": True, "cell": prev, "spent": spent,
                "left": self.speed_left[c.name]}

    def approach(self, c: Character, tgt: Character) -> dict:
        """靠近目标（网格模式走到贴身，剧场模式直接视为近身）。"""
        if self.grid is None:
            self.engagement.setdefault(c.name, set()).add(tgt.name)
            self.engagement.setdefault(tgt.name, set()).add(c.name)
            self.speed_left[c.name] = 0
            self.log(f"→ {c.name} 逼近 {tgt.name}，进入近身。")
            return {"ok": True, "engaged": True}
        tp = self.positions.get(tgt.name)
        if tp is None:
            return {"ok": False, "reason": f"{tgt.name} 不在场上"}
        occupied = {self.positions[n] for n in self.positions}
        best = None
        for cell in self.grid.cells_within(tp, 1):
            if cell in occupied or self.grid.blocked(cell):
                continue
            p = self.grid.path(self.positions.get(c.name), cell, occupied)
            if p and (best is None or len(p) < len(best[1])):
                best = (cell, p)
        if best is None:
            return {"ok": False, "reason": "无法靠近目标"}
        return self.move(c, best[0], spend_all=True)

    def dash(self, c: Character) -> dict:
        """奔跑：额外一个移动动作（本回合必须先移动过）。"""
        if not self.moved_this_turn.get(c.name):
            return {"ok": False, "reason": "必须先执行过移动动作才能奔跑"}
        extra = c.move_value()
        self.speed_left[c.name] = self.speed_left.get(c.name, 0) + extra
        self.log(f"→ {c.name} 开始奔跑（额外 {extra} 米移动力）。")
        return {"ok": True, "extra": extra}

    def delay(self, c: Character, before: str | None = None) -> dict:
        """延迟行动：把先攻顺位挪到指定参战者之前（本轮内）。"""
        if c not in self.order:
            return {"ok": False, "reason": f"{c.name} 不在先攻队列中"}
        self.order.remove(c)
        if before:
            tgt = self.find(before)
            if tgt in self.order:
                self.order.insert(self.order.index(tgt), c)
            else:
                self.order.append(c)
        else:
            self.order.append(c)
        if self.idx > self.order.index(c):
            self.idx = self.order.index(c)
        self.log(f"→ {c.name} 延迟行动。")
        return {"ok": True, "order": [x.name for x in self.order]}

    def _check_move_hurt(self, c: Character) -> None:
        """肋骨骨折/异物残留：一回合内步行移动超过 4 米则在回合结束时再受伤。"""
        moved = self.moved_this_turn.get(c.name, 0)
        for inj in c.crit_injuries:
            if inj.get("stabilized") and not inj.get("permanent"):
                continue
            for eff in inj.get("effects", []):
                if eff["kind"] == "move_hurt" and moved > int(eff.get("threshold_m", 4)):
                    dmg = dice_mod.roll(self.rng, "1d6")
                    res = self.apply_damage(c, dmg.total, ignore_armor=True)
                    self.log(f"✖ {c.name} 因「{inj['name']}」在移动后额外受到 "
                             f"{res['hp']} 点无视护甲的伤害。")

    def _resolve_cover_damage(self, c: Character) -> None:
        return None

    def _sync_engagement(self) -> None:
        """网格模式下按贴身（≤ 2 米）重建近身纠缠表。"""
        if self.grid is None:
            return
        for c in self.combatants:
            self.engagement.setdefault(c.name, set())
        for a in self.combatants:
            for b in self.combatants:
                if a is b:
                    continue
                pa, pb = self.positions.get(a.name), self.positions.get(b.name)
                if pa and pb and self.grid.dist(pa, pb) <= 1:
                    self.engagement[a.name].add(b.name)
                else:
                    self.engagement.get(a.name, set()).discard(b.name)

    # ------------------------------------------------------------ 状态与计时
    def set_condition(self, c: Character, key: str, rounds: int = 0,
                      save: str | None = None, dv: int = 13) -> None:
        c.conditions.add(key)
        if rounds:
            c.timers[key] = {"left": int(rounds), "save": save, "dv": int(dv)}

    def add_timer(self, c: Character, key: str, rounds: int,
                  save: str | None = None, dv: int = 13) -> None:
        c.timers[key] = {"left": int(rounds), "save": save, "dv": int(dv)}

    def remove_condition(self, c: Character, key: str) -> None:
        c.conditions.discard(key)
        c.timers.pop(key, None)

    def _tick_timers(self, c: Character) -> None:
        for key in list(c.timers):
            timer = c.timers.get(key)
            if not isinstance(timer, dict) or "left" not in timer:
                continue
            save = timer.get("save")
            if save:
                res = c.check(self.rng, save, dv=int(timer.get("dv", 13)))
                self._rec_check(c, f"{key} 再检定", res, int(timer.get("dv", 13)))
                if res.success:
                    c.conditions.discard(key)
                    c.timers.pop(key, None)
                    self.log(f"→ {c.name} 摆脱了「{key}」。")
                    continue
            timer["left"] = int(timer["left"]) - 1
            if timer["left"] <= 0:
                c.conditions.discard(key)
                c.timers.pop(key, None)
                self.log(f"→ {c.name} 的「{key}」结束了。")

    # ------------------------------------------------------------ 事件记录
    def log(self, msg: str) -> None:
        self.log_lines.append(msg)

    def _rec_attack(self, att: Character, tgt: Character, res, weapon: str,
                    dv: int | None = None) -> None:
        self.rolls.append({
            "kind": "attack", "who": att.name, "target": tgt.name,
            "weapon": weapon, "total": res.total, "rolls": list(res.rolls),
            "dv": dv if dv is not None else res.dv,
            "sp": tgt.armor_at("torso"), "hp": tgt.hp_cur, "hp_max": tgt.hp_max,
            "crit_success": res.crit_success, "crit_failure": res.crit_failure,
            "mods": list(res.breakdown),
        })

    def _rec_check(self, who: Character, label: str, res, dv: int) -> None:
        self.rolls.append({
            "kind": "check", "who": who.name, "skill": label,
            "total": res.total, "rolls": list(res.rolls), "dv": dv,
            "success": res.success, "crit_success": res.crit_success,
            "crit_failure": res.crit_failure, "mods": list(res.breakdown),
        })

    def _rec_save(self, who: Character, label: str, dv: int, res) -> None:
        self.rolls.append({
            "kind": "save", "who": who.name, "save": label,
            "total": res.total, "rolls": list(res.rolls), "dv": dv,
            "success": res.success, "mods": list(res.breakdown),
        })

    # ------------------------------------------------------------ 展示
    def board(self) -> str:
        """战况板（前端解析用）：``◆ 名字: PW n/n, SP n [伤势]``。"""
        lines = []
        for c in self.order:
            mark = "◆" if c is self.current else "・"
            sp = c.armor_at("torso")
            tag = ""
            if c.is_dead:
                tag = " 已死亡"
            elif c.hp_cur < 1:
                tag = " 濒死"
            elif c.wound_state() == "serious":
                tag = " 重伤"
            crit = f" 致命伤×{len(c.crit_injuries)}" if c.crit_injuries else ""
            lines.append(f"{mark} {c.name}: PW {c.hp_cur}/{c.hp_max}, SP {sp}{tag}{crit}")
        return "\n".join(lines)

    def brief(self) -> str:
        return (f"{self.title}（第 {self.round} 轮）："
                + "；".join(f"{c.name} PW {c.hp_cur}/{c.hp_max}" for c in self.combatants
                            if not c.is_dead))

    def status_lines(self) -> list[str]:
        return [c.brief() for c in self.combatants]

    def defeated_ip(self) -> int:
        """击败敌人给队伍的改进点（按敌人威胁粗算，供里程碑结算参考）。"""
        total = 0
        for c in self.enemies:
            total += 20 + 10 * max(0, c.stat("body") - 4)
        return total

    # ------------------------------------------------------------ NPC 决策
    def npc_choose_action(self, c: Character) -> dict:
        """敌人 AI：先脱困，血少就跑，否则打最近的对手。"""
        targets = [t for t in self.allies if t.hp_cur > 0 and not t.is_dead]
        if not targets:
            return {"action": "wait"}
        # 被控制 → 挣脱
        if COND_CONTROLLED in c.conditions:
            return {"action": "escape_grapple",
                    "target": min(targets, key=lambda t: self.measure(c, t)).name}
        # 重伤且是杂兵 → 撤退
        if c.hp_cur <= c.seriously_wounded_threshold and c.role == "solo" \
                and c.stat("cool") <= 6 and self.rng.random() < 0.5:
            return {"action": "flee"}
        weapon = c.best_weapon("ranged") or c.best_weapon() or "brawling"
        weapon_info = D.WEAPONS.get(weapon) or {}
        target = min(targets, key=lambda t: self.measure(c, t))
        kind = weapon_info.get("kind")
        meters = self.measure(c, target)
        needs_approach = kind in ("melee", "brawling") and meters > METERS_PER_CELL
        if kind not in ("melee", "brawling"):
            _, dv = self.range_band(kind, meters)
            if dv is None:
                needs_approach = True
        if needs_approach and self.speed_left.get(c.name, 0) <= 0:
            return {"action": "move", "target": target.name}
        return {"action": "attack", "weapon": weapon, "target": target.name,
                "approach": needs_approach}
