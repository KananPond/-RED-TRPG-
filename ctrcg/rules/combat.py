"""战斗引擎：先攻、动作经济（动作/附赠动作/反应）、攻击/伤害/暴击、法术、
状态效果（含计时与再豁免）、借机攻击、死亡豁免、抗性/免疫、野变形态、
简单怪物 AI。

采用 5e 官方支持的"剧场模式"（DMG 第3章 Theater of the Mind）：
距离以近战/远程二分法处理，用"近身纠缠"集合近似 adjacency；
借机攻击、撤离、推撞、擒抱等规则在此模型下完整生效。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from . import data as D
from .character import (
    Character, DWARF_RACES, ELF_RACES, GNOME_RACES, SMALL_RACES,
    COND_BLESSED, COND_BLINDED, COND_CHARMED, COND_FRIGHTENED, COND_GRAPPLED,
    COND_GUIDED, COND_HIDDEN, COND_INVISIBLE, COND_MARKED, COND_OUTLINED,
    COND_PARALYZED, COND_POISONED, COND_PRONE, COND_RAGING, COND_RESTRAINED,
    COND_SHIELDED, COND_SLEEP, COND_STABLE, COND_STUNNED, COND_UNCONSCIOUS,
    TIMED_CONDITIONS,
)
from .dice import check_roll, roll, parse_expr, DiceResult

DTYPE_CN = {"fire": "火焰", "radiant": "光耀", "cold": "冷冻", "force": "力场",
            "poison": "毒素", "necrotic": "黯蚀", "bludgeoning": "钝击",
            "piercing": "穿刺", "slashing": "挥砍", "sleep": "催眠",
            "thunder": "雷鸣", "psychic": "心灵", "lightning": "闪电",
            "acid": "强酸", "none": ""}


def _adv_net(has_adv: list[str], has_dis: list[str]) -> tuple[int, list[str]]:
    """5e 规则：只要同时存在任一优势与任一劣势来源，即互相抵消。"""
    if has_adv and has_dis:
        return 0, [f"{a}×{b}抵消" for a, b in zip(has_adv, has_dis)]
    if has_adv:
        return 1, has_adv
    if has_dis:
        return -1, has_dis
    return 0, []


def weapon_type(weapon_key: str | None) -> str:
    """按武器键解析伤害类型（玩家武器与怪物武器两表）。"""
    if not weapon_key:
        return "bludgeoning"
    w = D.WEAPONS.get(weapon_key) or D.MONSTER_WEAPONS.get(weapon_key)
    return (w or {}).get("type", "bludgeoning")


# ---------------------------------------------------------------- 怪物
class Monster:
    """数据驱动的怪物。与 Character 共享战斗接口。"""

    def __init__(self, monster_id: str, rng: random.Random, tag: str = "",
                 table: dict | None = None):
        table = table if table is not None else D.MONSTERS
        if monster_id not in table:
            raise ValueError(f"未知怪物 {monster_id}")
        m = table[monster_id]
        self.monster_id = monster_id
        self.name = m["name"] + (f"（{tag}）" if tag else "")
        self.base_name = m["name"]
        self.xp = int(m.get("xp", 0))
        self.cr = m.get("cr", 0)
        self.abilities = dict(m.get("abilities") or {})
        self.hp_max = max(1, roll(rng, m["hp"]).total)
        self.hp_cur = self.hp_max
        self.ac_base = m["ac"]
        self.speed = m.get("speed", 30)
        self.darkvision = m.get("darkvision", 0)
        self.traits = list(m.get("traits") or [])
        self.attacks = [dict(a) for a in m.get("attacks") or []]
        # 数据表可给 save_prof: {ability: True} 表示该属性豁免带熟练加值
        self.save_prof = dict(m.get("save_prof") or {})
        self.conditions: set[str] = set()
        self.death_fails = 0
        self.death_successes = 0
        self.blessing_d4 = None
        self.bardic_dice = 0
        self.fire_resist = bool(m.get("fire_resist", False))
        self.poison_resist = bool(m.get("poison_resist", False))
        self.poison_immune = bool(m.get("poison_immune", False))
        self.side = "enemy"
        self.is_pc = False
        self.concentration: str | None = None
        self.enlarged = False
        self.enlarge_used = False

    def ac(self) -> int:
        return self.ac_base

    def mod(self, ability: str) -> int:
        return (self.abilities.get(ability, 10) - 10) // 2

    def passive_perception(self) -> int:
        return 10 + self.mod("wis")

    def take_damage(self, amount: int) -> dict:
        self.hp_cur -= amount
        info = {"damage": amount, "hp": max(self.hp_cur, 0)}
        if self.hp_cur <= 0:
            self.hp_cur = 0
            info["down"] = True
        return info

    def heal(self, amount: int) -> int:
        before = self.hp_cur
        self.hp_cur = min(self.hp_max, self.hp_cur + amount)
        return self.hp_cur - before

    def is_undead(self) -> bool:
        return "不死者" in "".join(self.traits)

    def is_fiend(self) -> bool:
        return self.monster_id in ("imp", "cambion") or "魔鬼" in "".join(self.traits)

    def is_humanoid(self) -> bool:
        # 5e 中骷髅/丧尸的生物类型同样是 humanoid（故定身人类对它们有效）
        return self.monster_id in ("goblin", "hobgoblin", "gnoll", "duergar",
                                   "bandit", "bandit_captain", "ash_mage",
                                   "skeleton", "zombie")

    def save_bonus(self, ability: str) -> int:
        b = self.mod(ability)
        if self.save_prof.get(ability):
            b += 2 if self.cr < 4 else 3
        return b

    def to_dict(self) -> dict:
        return {"name": self.name, "hp": f"{self.hp_cur}/{self.hp_max}", "ac": self.ac()}


# ---------------------------------------------------------------- 遭遇
@dataclass
class Encounter:
    encounter_id: str
    title: str = ""
    dark: bool = False          # 无光源：无黑暗视觉者攻击劣势
    round: int = 0
    combatants: list = field(default_factory=list)   # Character | Monster
    order: list = field(default_factory=list)        # 先攻序
    turn_idx: int = 0
    started: bool = False
    rng: random.Random | None = None
    log: list[str] = field(default_factory=list)
    helped: dict[str, str] = field(default_factory=dict)   # 目标名 -> 帮助者名
    dodging: set[str] = field(default_factory=set)
    engagement: dict[str, set[str]] = field(default_factory=dict)  # 近身纠缠
    # ---- 动作经济（每回合重置；反应在本人回合开始时重置）
    actions_used: dict[str, bool] = field(default_factory=dict)
    bonus_used: dict[str, bool] = field(default_factory=dict)
    reaction_used: dict[str, bool] = field(default_factory=dict)
    # ---- 状态计时器 {名字: {状态: {"left": int, "save": str|None, "dc": int}}}
    timers: dict[str, dict[str, dict]] = field(default_factory=dict)
    # ---- 突袭（第一轮不能行动/反应，对其攻击有优势）
    surprised: set[str] = field(default_factory=set)
    # ---- 回合内施法标记（附赠动作法术规则）
    cast_this_turn: dict[str, str] = field(default_factory=dict)  # name: "bonus"|"action"
    sneak_used: set[str] = field(default_factory=set)   # 本回合已偷袭者
    colossus_used: set[str] = field(default_factory=set)
    attack_debuffs: dict[str, int] = field(default_factory=dict)  # 诛心之言等
    last_melee_hit: dict[str, str] = field(default_factory=dict)  # 攻击者→最近近战命中目标
    reckless_until: set[str] = field(default_factory=set)   # 鲁莽攻击中（至其下回合开始）
    last_attack_round: dict[str, int] = field(default_factory=dict)  # 狂暴提前结束判定
    last_damaged_round: dict[str, int] = field(default_factory=dict)
    frenzy_used: set[str] = field(default_factory=set)      # 本次狂暴用过狂战士附赠攻击
    rage_last_end: dict[str, int] = field(default_factory=dict)  # 狂暴者上次回合结束轮
    # ---- 骰子事件流（供 UI 播放掷骰动画；与 log 平行，按序消费）
    rolls: list[dict] = field(default_factory=list)
    grappler_of: dict[str, str] = field(default_factory=dict)  # 被擒抱者→擒抱者
    _turn_taken: set[str] = field(default_factory=set, repr=False)
    # ---- 反应询问钩子（session 注入：(target, ctx) -> bool；None=自动规则）
    reaction_prompt: object | None = None

    # ------------------------------------------------------------ 组建
    def add(self, c, side: str) -> None:
        c.side = side
        self.combatants.append(c)

    def party(self) -> list:
        return [c for c in self.combatants if c.side == "party"]

    def enemies(self) -> list:
        return [c for c in self.combatants if c.side == "enemy"]

    def begin(self, rng: random.Random) -> list[str]:
        self.rng = rng
        rolls = []
        for c in self.combatants:
            r = roll(rng, "d20")
            bonus = c.mod("dex")
            if isinstance(c, Monster) and "伏击" in "".join(c.traits):
                bonus += 2
            rolls.append((r.kept[0] + bonus, r.kept[0], c.name, c))
        rolls.sort(key=lambda t: (-t[0], -t[1]))
        self.order = [t[3] for t in rolls]
        self.started = True
        self.round = 1
        self.turn_idx = -1
        # 小恶魔/隐形类：先攻即进入隐形
        for c in self.order:
            if isinstance(c, Monster) and "随时隐形" in "".join(c.traits):
                c.conditions.add(COND_INVISIBLE)
        lines = [f"先攻顺序：{' → '.join(t[2] for t in rolls)}"]
        if self.surprised:
            lines.append("突袭！" + "、".join(self.surprised) + " 在第一轮无法行动。")
        self.log.extend(lines)
        return lines

    # ------------------------------------------------------------ 状态
    def alive(self, c) -> bool:
        conds = getattr(c, "conditions", set())
        if "dead" in conds:
            return False
        if COND_UNCONSCIOUS in conds or COND_SLEEP in conds:
            return False
        return c.hp_cur > 0

    def active_party(self) -> list:
        return [c for c in self.party() if self.alive(c)]

    def active_enemies(self) -> list:
        return [c for c in self.enemies() if self.alive(c)]

    def is_over(self) -> str | None:
        if not self.active_enemies():
            return "party"
        if not self.active_party():
            return "enemies"
        return None

    def current(self):
        return self.order[self.turn_idx]

    def find(self, name_or_id: str):
        key = str(name_or_id).strip().lower()
        for c in self.combatants:
            if key in (c.name.lower(), getattr(c, "base_name", c.name).lower(),
                       getattr(c, "monster_id", "").lower()):
                return c
        for c in self.combatants:
            if c.name.lower().startswith(key) or key in c.name.lower():
                return c
        return None

    # ---------------- 动作经济
    def begin_turn(self, c) -> list[str]:
        events: list[str] = []
        self.actions_used[c.name] = False
        self.bonus_used[c.name] = False
        self.reaction_used[c.name] = False
        self.sneak_used.discard(c.name)
        self.colossus_used.discard(c.name)
        self.dodging.discard(c.name)          # 防御姿态只到下回合开始
        self.reckless_until.discard(c.name)   # 鲁莽破绽只到下回合开始
        self.cast_this_turn.pop(c.name, None)
        # 回合开始效果
        t = self.timers.get(c.name, {})
        if "burning" in t:
            info = self._burn_tick(c)
            if info:
                events.append(info)
        return events

    def end_turn(self, c) -> list[str]:
        events: list[str] = []
        t = self.timers.get(c.name, {})
        # 狂暴提前结束（PHB）：本回合既未攻击宿敌也未受伤 → 狂暴平息
        if COND_RAGING in c.conditions:
            prev_end = self.rage_last_end.get(c.name, 0)
            attacked = self.last_attack_round.get(c.name, -1) > prev_end
            damaged = self.last_damaged_round.get(c.name, -1) > prev_end
            if not (attacked or damaged):
                self._remove_condition(c, COND_RAGING)
                events.append(f"{c.name} 的狂暴平息了（本回合既未攻击也未受伤）。")
            self.rage_last_end[c.name] = self.round
        for key, meta in list(t.items()):
            if meta.get("save") and key in c.conditions:
                ok = self._condition_save(c, meta["save"], meta["dc"])
                if ok:
                    self._remove_condition(c, key)
                    events.append(f"{c.name} 挣脱了 {self._cond_cn(key)}。")
                    continue
            meta["left"] -= 1
            if meta["left"] <= 0:
                self._remove_condition(c, key)
                events.append(f"{c.name} 的 {self._cond_cn(key)} 结束了。")
        # 昏睡计时归零自动醒来
        if COND_SLEEP in c.conditions and COND_SLEEP not in t:
            self._remove_condition(c, COND_SLEEP)
            events.append(f"{c.name} 醒了过来。")
        return events

    def use_action(self, c) -> bool:
        if self.actions_used.get(c.name):
            return False
        self.actions_used[c.name] = True
        return True

    def use_bonus(self, c) -> bool:
        if self.bonus_used.get(c.name):
            return False
        self.bonus_used[c.name] = True
        return True

    def use_reaction(self, c) -> bool:
        if self.reaction_used.get(c.name):
            return False
        self.reaction_used[c.name] = True
        return True

    def has_action(self, c) -> bool:
        return not self.actions_used.get(c.name)

    def has_bonus(self, c) -> bool:
        return not self.bonus_used.get(c.name)

    def has_reaction(self, c) -> bool:
        return not self.reaction_used.get(c.name)

    # ---------------- 状态计时
    def add_timer(self, target, key: str, rounds: int,
                  save: str | None = None, dc: int = 10) -> None:
        self.timers.setdefault(target.name, {})[key] = {
            "left": rounds, "save": save, "dc": dc}

    def _remove_condition(self, target, key: str) -> None:
        target.conditions.discard(key)
        self.timers.get(target.name, {}).pop(key, None)
        if key == COND_GRAPPLED:
            self.grappler_of.pop(target.name, None)
        if key == COND_RAGING and isinstance(target, Character):
            self.reckless_until.discard(target.name)
            # 狂战士：若本次狂暴中用过"狂暴附赠攻击"，狂暴结束时力竭+1级
            if (target.name in self.frenzy_used
                    and target.has_feature("frenzy")):
                self.frenzy_used.discard(target.name)
                target.exhaustion = min(6, target.exhaustion + 1)
                self.log.append(f"{target.name} 的狂暴结束，狂战士的后遗症使其力竭+1级"
                                f"（现为{target.exhaustion}级）。")

    def _condition_save(self, target, ability: str, dc: int) -> bool:
        if isinstance(target, Character):
            sv = target.save(self.rng, ability, dc)
            self._rec_save(target, sv, dc, "豁免", minor=True)
            return sv["success"]
        # 麻痹/震慑/昏迷目标的力量/敏捷豁免自动失败
        if (ability in ("str", "dex")
                and (COND_PARALYZED in target.conditions
                     or COND_STUNNED in target.conditions)):
            return False
        dr = roll(self.rng, "d20")
        ok = dr.kept[0] + target.save_bonus(ability) >= dc
        self._rec_save(target, dr.kept[0], dc, "豁免", minor=True, success=ok)
        return ok

    def _rec_attack(self, att, die, adv: int, total: int, target, hit: bool,
                    crit: bool, label: str = "") -> None:
        """记录一次攻击 d20（供 UI 播放掷骰动画）。"""
        self.rolls.append({"who": att.name, "kind": "attack", "label": label,
                           "d20": die.kept[0], "rolls": list(die.rolls), "adv": adv,
                           "total": total, "ac": target.ac(),
                           "hit": bool(hit), "crit": bool(crit)})

    def _rec_save(self, target, result, dc: int, label: str = "豁免",
                  minor: bool = False, success: bool | None = None) -> None:
        """记录一次豁免骰。result 可为 Character.save() 的 dict（含结构化骰子）
        或怪物裸骰的 kept 值。"""
        if isinstance(result, dict):
            self.rolls.append({"who": target.name, "kind": "save", "label": label,
                               "d20": result.get("d20"),
                               "rolls": list(result.get("rolls") or []),
                               "adv": result.get("adv", 0), "dc": dc,
                               "success": bool(result.get("success")), "minor": minor})
        else:
            self.rolls.append({"who": target.name, "kind": "save", "label": label,
                               "d20": int(result), "rolls": [int(result)], "adv": 0,
                               "dc": dc,
                               "success": bool(result if success is not None
                                               else int(result) >= dc),
                               "minor": minor})

    def _burn_tick(self, c) -> str | None:
        """炽焰斩的持续燃烧：回合开始 1d6 火焰，体质豁免终止。"""
        t = self.timers.get(c.name, {}).get("burning")
        if not t:
            return None
        dmg = roll(self.rng, "1d6").total
        dmg, _notes = self.apply_resistance(c, "fire", dmg)
        info = self.apply_damage(c, dmg, dtype="fire")
        ok = self._condition_save(c, "con", t["dc"])
        if ok:
            self._remove_condition(c, "burning")
            return (f"{c.name} 身上的余焰造成 {dmg} 点火焰伤害后熄灭"
                    f"（HP {info['hp']}/{self._hp_max(c)}）。")
        return f"{c.name} 身上的余焰造成 {dmg} 点火焰伤害（HP {info['hp']}/{self._hp_max(c)}）。"

    @staticmethod
    def _cond_cn(key: str) -> str:
        names = {"burning": "燃烧", COND_POISONED: "中毒", COND_FRIGHTENED: "恐惧",
                 COND_OUTLINED: "妖火", COND_RESTRAINED: "束缚", COND_PARALYZED: "麻痹",
                 COND_STUNNED: "震慑", COND_CHARMED: "魅惑", COND_BLINDED: "目盲",
                 COND_SHIELDED: "护盾术", COND_RAGING: "狂暴", COND_SLEEP: "沉睡",
                 COND_GRAPPLED: "被擒抱", COND_GUIDED: "引导射击"}
        return names.get(key, key)

    def _hp_max(self, c) -> int:
        if isinstance(c, Character):
            m = c.wild_shape["hp_max"] if c.in_wild_shape() else c.hp_max
            if c.exhaustion >= 4:
                m = max(1, m // 2)          # 力竭4级：生命上限减半
            return m
        return c.hp_max

    def _hp_cur(self, c) -> int:
        """显示用当前 HP：兽形下显示兽形 HP（本体 HP 不动）。"""
        if isinstance(c, Character) and c.in_wild_shape():
            return max(int(c.wild_shape.get("hp_cur", 0)), 0)
        return max(c.hp_cur, 0)

    # ---------------- 抗性 / 免疫
    def apply_resistance(self, target, dtype: str, dmg: int) -> tuple[int, list[str]]:
        notes: list[str] = []
        if dmg <= 0:
            return dmg, notes
        if isinstance(target, Character):
            mech = target.mech()
            if mech.get(f"{dtype}_resist") or (dtype == "fire" and mech.get("fire_resist")) \
                    or (dtype == "poison" and mech.get("poison_resist")):
                dmg //= 2
                notes.append(f"{target.name} 对{DTYPE_CN.get(dtype, dtype)}伤害有抗性（减半）")
            if (COND_RAGING in target.conditions and
                    (dtype in ("bludgeoning", "piercing", "slashing")
                     or (target.has_feature("totem_bear") and dtype != "psychic"))):
                dmg //= 2
                notes.append(f"{target.name} 狂暴中：{DTYPE_CN.get(dtype, dtype)}伤害减半")
        else:
            if dtype == "fire" and target.fire_resist:
                dmg //= 2
                notes.append(f"{target.name} 对火焰伤害有抗性（减半）")
            if dtype == "poison" and target.poison_resist:
                dmg //= 2
                notes.append(f"{target.name} 对毒素伤害有抗性（减半）")
            if dtype == "poison" and target.poison_immune:
                dmg = 0
                notes.append(f"{target.name} 对毒素免疫")
        return dmg, notes

    # ---------------- 伤害入口（含野变形态）
    def apply_damage(self, target, amount: int, dtype: str = "") -> dict:
        if amount > 0:
            self.last_damaged_round[target.name] = self.round
        if isinstance(target, Character) and target.in_wild_shape() and amount > 0:
            ws = target.wild_shape
            absorbed = min(ws["hp_cur"], amount)
            ws["hp_cur"] -= absorbed
            overflow = amount - absorbed
            if ws["hp_cur"] <= 0:
                form = ws["form"]
                target.wild_shape = None
                target.conditions.discard(COND_UNCONSCIOUS)
                if overflow > 0:
                    info = target.take_damage(overflow)
                    self.log.append(
                        f"{target.name} 的兽形被打散，溢出 {overflow} 点伤害由本体承受！")
                    return info
                target.hp_cur = max(target.hp_cur, 0)
                self.log.append(f"{target.name} 的兽形被打散，本体尚存（HP {target.hp_cur}）。")
                return {"damage": absorbed, "hp": target.hp_cur}
            return {"damage": absorbed, "hp": ws["hp_cur"]}
        return target.take_damage(amount)

    # ------------------------------------------------------------ 推进回合
    def next_turn(self) -> tuple[object, list[str]]:
        """推进回合指针；处理死亡豁免、突袭跳过、回合开始/结束效果。"""
        events: list[str] = []
        for _ in range(len(self.order) * 2 + 2):
            # 结束上一个行动过的角色回合（恰好一次）
            if 0 <= self.turn_idx < len(self.order):
                prev = self.current()
                if prev.name in self._turn_taken:
                    self._turn_taken.discard(prev.name)
                    events.extend(self.end_turn(prev))
            self.turn_idx += 1
            if self.turn_idx >= len(self.order):
                self.turn_idx = 0
                self.round += 1
            c = self.order[self.turn_idx]
            if not self.alive(c):
                if isinstance(c, Character) and c.hp_cur == 0 and "dead" not in c.conditions:
                    ds = c.death_save_turn(self.rng)
                    if ds:
                        events.append(f"☠ {ds['who']} 死亡豁免：{ds['detail']} → {ds['result']}")
                continue
            if c.name in self.surprised and self.round == 1:
                events.append(f"{c.name} 措手不及，第一轮无法行动。")
                events.extend(self.end_turn(c))   # 时限状态照常递减
                continue
            events.extend(self.begin_turn(c))
            self._turn_taken.add(c.name)
            return c, events
        c = self.current()
        return c, events

    # ------------------------------------------------------------ 攻击
    def attack(self, att, target, weapon_key: str | None, rng: random.Random,
               bonus_action: bool = False, offhand: bool = False,
               reckless: bool = False, precision_bonus: int = 0,
               power_attack: bool = False) -> dict:
        """低层单次攻击（借机攻击/反应使用；不消耗动作经济）。"""
        if isinstance(att, Monster):
            return self._monster_attack(att, target, weapon_key, rng,
                                        precision_bonus=precision_bonus)
        if att.in_wild_shape():
            return self._beast_attack(att, target, weapon_key, rng)
        weapon = D.WEAPONS.get(weapon_key or (att.weapons[0] if att.weapons else None))
        if weapon is None:
            weapon = {"name": "徒手", "dmg": "1", "type": "bludgeoning", "props": [], "ranged": False}
        return self._pc_attack(att, target, weapon, rng, offhand=offhand,
                               reckless=reckless, precision_bonus=precision_bonus,
                               power_attack=power_attack)

    def extra_attack_count(self, att) -> int:
        if isinstance(att, Character) and att.extra_attack_level() \
                and att.level >= att.extra_attack_level():
            return 2
        if isinstance(att, Monster):
            return 1          # 多重攻击在 _monster_attack 内部结算
        return 1

    def attack_action(self, att, target, weapon_key: str | None, rng: random.Random,
                      reckless: bool = False, smite_slot: int | None = None,
                      maneuver: str | None = None,
                      power_attack: bool = False) -> dict:
        """攻击动作：消耗动作；5 级 martial 获得额外攻击（可自动改目标）；
        战斗大师可用战技（精确/推撞/恐吓，消耗战技骰）；
        巨武器大师在重击/击倒后以附赠动作追加一次攻击。"""
        if not self.use_action(att):
            return {"ok": False, "error": "本回合动作已用完"}
        gwm_trigger = False
        count = self.extra_attack_count(att)
        cur = target
        results = []
        total_dmg = 0
        precision_bonus = 0
        if maneuver == "precision" and isinstance(att, Character) \
                and att.superiority_dice_left() > 0:
            precision_bonus = roll(rng, "1d8").total
            att.superiority_used += 1
            results.append(f"精确攻击：攻击骰 +{precision_bonus}（消耗1颗战技骰）")
        for i in range(count):
            if cur is None or not self.alive(cur):
                if isinstance(att, Character):
                    foes = [f for f in self.active_enemies() if att.side == "party"] \
                        if att.side == "party" else \
                        [f for f in self.active_party() if att.side == "enemy"]
                    cur = min(foes, key=lambda f: f.hp_cur) if foes else None
            if cur is None:
                break
            r = self.attack(att, cur, weapon_key, rng, reckless=reckless,
                            precision_bonus=precision_bonus if i == 0 else 0,
                            power_attack=power_attack)
            if r.get("ok") and r.get("hit"):
                if r.get("crit"):
                    gwm_trigger = True
                if cur.hp_cur <= 0 or (isinstance(cur, Character)
                                       and cur.in_wild_shape()
                                       and cur.wild_shape["hp_cur"] <= 0):
                    gwm_trigger = True
            if r.get("ok") and r.get("hit") and smite_slot and i == 0 \
                    and isinstance(att, Character):
                sm = self.divine_smite(att, cur, smite_slot, rng,
                                       crit=r.get("crit", False))
                if sm.get("ok"):
                    r["detail"] += sm["detail"]
            if r.get("ok") and r.get("hit") and maneuver in ("pushing", "menacing") \
                    and isinstance(att, Character) and i == 0:
                self._apply_maneuver_on_hit(att, cur, maneuver, rng, results,
                                            wtype=weapon_type(weapon_key))
            results.append(r.get("detail", ""))
            total_dmg += r.get("damage", 0) or 0
            if isinstance(att, Monster):
                break        # 怪物多重攻击已在内部结算
        # 巨武器大师：重型近战武器重击/击倒 → 附赠动作追加一次攻击（PHB）
        if isinstance(att, Character) and gwm_trigger and self.has_bonus(att):
            wdata = D.WEAPONS.get(weapon_key or "", {})
            if not wdata.get("ranged") and "heavy" in wdata.get("props", [])                     and att.has_feature("great_weapon_master"):
                self.use_bonus(att)
                foes = self.active_enemies() if att.side == "party"                     else self.active_party()
                tgt = cur if (cur is not None and self.alive(cur))                     else (min(foes, key=lambda f: f.hp_cur) if foes else None)
                if tgt is not None:
                    r2 = self.attack(att, tgt, weapon_key, rng,
                                     power_attack=power_attack)
                    results.append("巨武器大师：附赠动作追加攻击——"
                                   + r2.get("detail", ""))
                    total_dmg += r2.get("damage", 0) or 0
        # 破阵者（游侠）：额外对一名贴身的不同目标攻击一次
        if (isinstance(att, Character) and att.has_feature("horde_breaker")
                and total_dmg > 0):
            others = [f for f in self.active_enemies()
                      if f is not target and f.name in self.engagement.get(att.name, set())]
            if others:
                r = self.attack(att, others[0], weapon_key, rng, reckless=reckless)
                results.append(r.get("detail", ""))
                total_dmg += r.get("damage", 0) or 0
        detail = "\n".join(x for x in results if x)
        return {"ok": True, "hit": total_dmg > 0, "damage": total_dmg,
                "detail": detail, "attacks": len(results)}

    def _apply_maneuver_on_hit(self, att: Character, target, maneuver: str,
                               rng: random.Random, results: list,
                               wtype: str = "bludgeoning") -> None:
        """战斗大师战技命中效果：+1d8 伤害 + 附加效果（消耗1颗战技骰）。"""
        if att.superiority_dice_left() <= 0:
            return
        att.superiority_used += 1
        r = roll(rng, "1d8")
        dmg, _ = self.apply_resistance(target, wtype, r.total)
        info = self.apply_damage(target, dmg)
        if maneuver == "pushing":
            self.do_disengage_quiet(target)
            results.append(f"推撞攻击：+{info.get('damage', dmg)} 点伤害，"
                           f"{target.name} 被推开5尺（1d8={r.total}）")
        elif maneuver == "menacing":
            dc = 8 + att.prof_bonus() + max(att.mod("str"), att.mod("dex"))
            if isinstance(target, Character):
                sv = target.save(rng, "wis", dc)
                ok, d = sv["success"], sv["detail"]
            else:
                s = roll(rng, "d20").kept[0] + target.save_bonus("wis")
                ok, d = s >= dc, f"[d20: {s}]"
            if ok:
                results.append(f"恐吓攻击：+{info.get('damage', dmg)} 点伤害，"
                               f"{target.name} 抵抗了恐惧（1d8={r.total}，{d}）")
            else:
                self.set_condition(target, COND_FRIGHTENED)
                self.add_timer(target, COND_FRIGHTENED, 10)
                results.append(f"恐吓攻击：+{info.get('damage', dmg)} 点伤害，"
                               f"{target.name} 陷入恐惧！（1d8={r.total}，{d}）")

    # ---------------- 优势/劣势来源
    def _advantage_for(self, att, target, ranged: bool,
                       reckless: bool = False) -> tuple[int, list[str]]:
        has_adv: list[str] = []
        has_dis: list[str] = []
        conds = getattr(att, "conditions", set())
        if COND_POISONED in conds:
            has_dis.append("中毒")
        if COND_FRIGHTENED in conds:
            has_dis.append("恐惧")
        if COND_RESTRAINED in conds:
            has_dis.append("束缚")
        if COND_BLINDED in conds:
            has_dis.append("目盲")
        if COND_HIDDEN in conds or COND_INVISIBLE in conds:
            has_adv.append("潜行/隐形")
        if COND_PRONE in conds and not ranged:
            has_dis.append("倒地攻击")
        if isinstance(att, Character):
            if att.exhaustion >= 3:
                has_dis.append("力竭")
            # PHB：护甲力量需求不足 → 移速-10尺（无距离模型，不带来攻击劣势）
            if not ranged and reckless and att.has_feature("reckless"):
                has_adv.append("鲁莽攻击")
                self.reckless_until.add(att.name)
        if self.dark and getattr(att, "darkvision", 0) == 0:
            has_dis.append("黑暗中无黑暗视觉")
        tconds = getattr(target, "conditions", set())
        if target.name in self.reckless_until:
            has_adv.append("目标鲁莽")
        if COND_PRONE in tconds:
            if not ranged:
                has_adv.append("目标倒地")
            else:
                has_dis.append("目标倒地（远程）")
        if COND_UNCONSCIOUS in tconds or COND_SLEEP in tconds:
            if not ranged:
                has_adv.append("目标失去意识")
            else:
                has_dis.append("目标不可见（远程）")
        if COND_RESTRAINED in tconds or COND_STUNNED in tconds:
            has_adv.append("目标受制")
        if COND_PARALYZED in tconds:
            has_adv.append("目标麻痹")
        if COND_OUTLINED in tconds or COND_GUIDED in tconds:
            has_adv.append("目标被标亮")
        if COND_INVISIBLE in tconds or COND_HIDDEN in tconds:
            has_dis.append("目标不可见")
        # 助攻：下一次对目标的攻击具有优势（由队友结算后即消耗）
        _helper = self.helped.get(target.name)
        if _helper is not None and _helper != att.name:
            self.helped.pop(target.name)
            has_adv.append("队友助攻")
        if ranged and any(self.alive(self.find(n)) for n in self.engagement.get(att.name, set())
                          if self.find(n) and self.find(n).side != att.side):
            has_dis.append("被近身纠缠")
        if target.name in self.dodging:
            has_dis.append("目标防御姿态")
        if target.name in self.surprised and self.round == 1:
            has_adv.append("目标被突袭")
        if (isinstance(att, Monster) and "成群狩猎" in "".join(att.traits)
                and any(self.alive(self.find(n)) and self.find(n).side == att.side
                        for n in self.engagement.get(target.name, set())
                        if self.find(n) and self.find(n) is not att)):
            has_adv.append("成群狩猎")
        return _adv_net(has_adv, has_dis)

    def _weapon_for(self, att, weapon: dict) -> dict:
        return weapon

    def _pc_attack(self, att: Character, target, weapon: dict, rng: random.Random,
                   offhand: bool = False, reckless: bool = False,
                   precision_bonus: int = 0, power_attack: bool = False) -> dict:
        ranged = bool(weapon.get("ranged"))
        props = weapon.get("props", [])
        finesse = "finesse" in props or ranged
        is_monk_weapon = "monk" in props and att.klass == "monk"
        # 灵巧/武僧武器：用力量或敏捷中较高者（PHB）
        if finesse or is_monk_weapon:
            abil = "dex" if att.mod("dex") >= att.mod("str") else "str"
        else:
            abil = "str"
        if att.has_feature("pact_blade") and not ranged:
            abil = "cha"
        # 双手武器（ versatile 且无盾，或 two_handed）使用较高伤害骰
        versatile = not ranged and "versatile" in str(props) and not att.shield
        bonus_to_hit = att.prof_bonus() + att.mod(abil) + att.magic_attack_bonus
        if att.fighting_style == "archery" and ranged:
            bonus_to_hit += 2
        debuff = self.attack_debuffs.pop(att.name, 0)   # 诛心之言等攻击减值
        # 专长大力攻击（PHB）：巨武器大师（重型近战）/ 神射手（远程）
        power_ok = power_attack and (
            (not ranged and "heavy" in props
             and att.has_feature("great_weapon_master"))
            or (ranged and att.has_feature("sharpshooter")))
        if power_ok:
            bonus_to_hit -= 5
        adv, notes = self._advantage_for(att, target, ranged, reckless=reckless)
        if power_ok:
            notes.append("大力攻击（−5 命中/+10 伤害）")
        # 镜影术拦截（命中判定前；远程与近战均受保护）
        if self._mirror_image_block(target, rng, notes):
            detail = (f"{att.name} 以{weapon['name']}攻击 {target.name} —— "
                      f"打中的只是镜影！（{'；'.join(notes)}）")
            self.log.append(detail)
            return {"ok": True, "hit": False, "crit": False, "damage": 0, "detail": detail}
        # 小型生物 + 重型武器 → 劣势
        if att.is_small() and "heavy" in props:
            adv = -1
            notes.append("小型生物使用重型武器（劣势）")

        bless = 0
        if COND_BLESSED in att.conditions and att.blessing_d4:
            bless = rng.randint(1, att.blessing_d4)
        die = self._attack_d20(att, rng, adv)
        nat = die.kept[0]
        total = nat + bonus_to_hit + bless + debuff + precision_bonus
        if precision_bonus:
            notes.append(f"精确攻击+{precision_bonus}")
        crit_min = 19 if att.has_feature("improved_crit") else 20
        hit = nat >= crit_min or (nat != 1 and total >= target.ac())
        # 强盗头目格挡（反应 +2 AC）
        if (hit and nat < crit_min and isinstance(target, Monster)
                and "格挡" in "".join(target.traits)
                and self.has_reaction(target) and not ranged):
            if total in (target.ac() + 1, target.ac() + 2):
                self.use_reaction(target)
                hit = False
                notes.append("目标格挡（AC+2）")
        crit = nat >= crit_min and hit
        self._rec_attack(att, die, adv, total, target, hit, crit,
                         label=f"{weapon['name']}攻击")
        # 麻痹/昏迷/沉睡目标 5 尺内命中即暴击（近战）
        helpless = (COND_PARALYZED, COND_UNCONSCIOUS, COND_SLEEP)
        if hit and not ranged and any(
                c in getattr(target, "conditions", set()) for c in helpless):
            crit = True

        dmg_mod = att.mod(abil)
        if offhand and att.fighting_style != "two_weapon":
            dmg_mod = 0                                   # 双武器格斗前副手不加属性
        if (att.fighting_style == "dueling" and not ranged and not offhand
                and "two_handed" not in props and not versatile):
            dmg_mod += 2
        dmg_mod += att.magic_damage_bonus
        if power_ok:
            dmg_mod += 10
        if COND_RAGING in att.conditions and not ranged:
            dmg_mod += 2

        extra_parts: list[tuple[str, str]] = []
        if (getattr(att, "marked_target", None) == target.name
                and COND_MARKED in att.conditions):
            extra_parts.append(("1d6", "印记"))
        if att.klass == "rogue":
            sneak_ok = (adv > 0 or (not ranged and any(
                self.alive(ally := self.find(n)) and ally.side == att.side and ally is not att
                for n in self.engagement.get(target.name, set()))))
            if sneak_ok and adv >= 0 and att.name not in self.sneak_used:
                lvl_dice = 1 + (att.level - 1) // 2
                extra_parts.append((f"{lvl_dice}d6", "偷袭"))
        if att.has_feature("colossus_slayer") and target.hp_cur < target.hp_max \
                and att.name not in self.colossus_used:
            extra_parts.append(("1d8", "巨兽杀手"))
        if att.mech().get("savage_attacks") and crit and not ranged:
            _n0, _s0, _m0 = parse_expr(weapon["dmg"])
            extra_parts.append((f"+1d{_s0}", "野蛮重击"))
        if att.has_feature("brutal_critical") and crit and not ranged:
            _n0, _s0, _m0 = parse_expr(weapon["dmg"])
            extra_parts.append((f"+1d{_s0}", "强蛮重击"))
        if getattr(att, "weapon_buff", None):
            extra_parts.append((att.weapon_buff["dice"], "武器附魔"))

        if not hit:
            detail = (f"{att.name} 以{weapon['name']}攻击 {target.name}：{die.describe()}"
                      f"+{bonus_to_hit}{f'（祝福+{bless}）' if bless else ''} = {total} "
                      f"vs AC {target.ac()} —— 未命中。"
                      + (f"（{'；'.join(notes)}）" if notes else ""))
            self.log.append(detail)
            if COND_HIDDEN in att.conditions:
                att.conditions.discard(COND_HIDDEN)
            return {"ok": True, "hit": False, "crit": False, "damage": 0, "detail": detail}

        mult = 2 if crit else 1
        n, s, m0 = parse_expr(weapon["dmg"])
        if versatile:
            n, s, m0 = parse_expr(str(weapon.get("props")).split("versatile(")[1].split(")")[0])
        r = roll(rng, f"{n * mult}d{s}")
        rolls = list(r.rolls)
        dmg = r.total + m0 + dmg_mod
        if (att.fighting_style == "great_weapon" and not ranged and not offhand
                and ("two_handed" in props or versatile)):
            # 大武器格斗：武器伤害骰出 1/2 可重骰一次
            rerolls = [rng.randint(1, s) if x <= 2 else x for x in rolls]
            gained = sum(rerolls) - sum(rolls)
            if gained:
                dmg += gained
                rolls = rerolls
                notes.append(f"大武器格斗重骰（{'+'.join(map(str, rerolls))}）")
        parts = [f"{weapon['name']}[d{s}:{'+'.join(map(str, rolls))}]"]
        for expr, label in extra_parts:
            if expr.startswith("+"):
                expr = expr[1:]
            n2, s2, m2 = parse_expr(expr)
            r2 = roll(rng, f"{n2 * mult}d{s2}" + (f"+{m2}" if m2 else ""))
            dmg += r2.total
            parts.append(f"{label}{r2.describe()}")
        for tag in ("偷袭",):
            if any(label == tag for _, label in extra_parts):
                self.sneak_used.add(att.name)
        if any(label == "巨兽杀手" for _, label in extra_parts):
            self.colossus_used.add(att.name)
        dmg, res_notes = self.apply_resistance(target, weapon.get("type", ""), dmg)
        notes.extend(res_notes)
        info = self.apply_damage(target, dmg)
        self._register_engagement(att, target, ranged)
        self.last_attack_round[att.name] = self.round
        self._post_hit_effects(att, target, crit, info.get("damage", dmg), info, rng,
                               ranged=ranged)
        if getattr(att, "weapon_buff", None):
            att.weapon_buff = None
        tail = f"（{target.name} HP {info.get('hp', max(target.hp_cur, 0))}/{self._hp_max(target)}）"
        down = target.hp_cur <= 0 or (isinstance(target, Character)
                                      and bool(target.wild_shape)
                                      and target.wild_shape["hp_cur"] <= 0)
        detail = (f"{att.name} 以{weapon['name']}攻击 {target.name}：{die.describe()}"
                  f"+{bonus_to_hit}{f'（祝福+{bless}）' if bless else ''} = {total} vs AC {target.ac()} —— "
                  f"{'暴击！' if crit else '命中，'}{' + '.join(parts)}{f'+{dmg_mod}' if dmg_mod else ''} "
                  f"→ 共 {info.get('damage', dmg)} 点伤害{tail}"
                  + (f"（{'；'.join(notes)}）" if notes else ""))
        self.log.append(detail)
        if COND_HIDDEN in att.conditions:
            att.conditions.discard(COND_HIDDEN)
        # 隐形（法术/天生）在攻击后消散——除"随时隐形"怪物特性
        if (COND_INVISIBLE in att.conditions
                and not (isinstance(att, Monster)
                         and "随时隐形" in "".join(att.traits))):
            att.conditions.discard(COND_INVISIBLE)
            detail_note = f"{att.name} 的隐形在攻击后消散。"
            self.log.append(detail_note)
        if hit and not ranged:
            self.last_melee_hit[att.name] = target.name
        return {"ok": True, "hit": True, "crit": crit, "damage": info.get("damage", dmg),
                "target_hp": info.get("hp", max(target.hp_cur, 0)), "down": down,
                "detail": detail, "notes": notes}

    def _attack_d20(self, att, rng: random.Random, adv: int):
        """攻击 d20：优势/劣势掷两次取高/低（怪物同样如此，5e 规则统一）；
        角色额外享受半身人幸运。"""
        if isinstance(att, Character):
            res = check_roll(rng, 0, adv)
            if res.kept[0] == 1 and att.mech().get("lucky_reroll_1"):
                res2 = check_roll(rng, 0, adv)
                res = res2
            if res.kept[0] <= 5 and att.has_feature("feat_lucky")                     and att.feat_lucky_used < 3:
                att.feat_lucky_used += 1
                res2 = check_roll(rng, 0, adv)
                if res2.kept[0] > res.kept[0]:
                    res = DiceResult(expr="d20(幸运专长重骰)",
                                     rolls=res.rolls + res2.rolls, kept=res2.kept,
                                     modifier=0, total=res2.kept[0])
            return res
        if adv:
            a, b = rng.randint(1, 20), rng.randint(1, 20)
            kept = max(a, b) if adv > 0 else min(a, b)
            return DiceResult(expr="d20", rolls=[a, b], kept=[kept], total=kept)
        return roll(rng, "d20")

    def _mirror_image_block(self, target, rng: random.Random,
                            notes: list[str]) -> bool:
        """镜影术拦截（PHB）：3分身d20≥6/2分身≥8/1分身≥11 → 分身被击碎，攻击落空。"""
        images = getattr(target, "mirror_images", 0)
        if images <= 0:
            return False
        threshold = {3: 6, 2: 8, 1: 11}.get(images, 6)
        r = roll(rng, "d20")
        if r.kept[0] >= threshold:
            target.mirror_images = images - 1
            note = (f"镜影术：{target.name} 的一个分身被击碎"
                    f"（{r.kept[0]}≥{threshold}，剩余 {target.mirror_images} 个）")
            notes.append(note)
            self.log.append(note)
            return True
        notes.append(f"镜影术：d20={r.kept[0]}<{threshold}，攻击落在本体")
        return False

    def _register_engagement(self, att, target, ranged: bool) -> None:
        if ranged:
            return
        self.engagement.setdefault(att.name, set()).add(target.name)
        self.engagement.setdefault(target.name, set()).add(att.name)

    def _beast_attack(self, druid: Character, target, atk_key: str | None,
                      rng: random.Random) -> dict:
        """德鲁伊野变形态的动物攻击。"""
        beast = druid.beast_info()
        atks = beast.get("attacks", [])
        atk = next((a for a in atks if a["weapon"] == atk_key), atks[0] if atks else None)
        if atk is None:
            return {"ok": False, "error": "该形态没有攻击"}
        wname = (D.WEAPONS.get(atk["weapon"]) or D.MONSTER_WEAPONS.get(
            atk["weapon"], {"name": atk["weapon"]}))["name"]
        mi_notes: list[str] = []
        if self._mirror_image_block(target, rng, mi_notes):
            detail = (f"{druid.name}（兽形）攻击 {target.name} —— 打中的只是镜影！"
                      f"（{'；'.join(mi_notes)}）")
            self.log.append(detail)
            return {"ok": True, "hit": False, "damage": 0, "detail": detail}
        results, total_dmg = [], 0
        for _ in range(atk.get("multiattack", 1)):
            adv, _n = self._advantage_for(druid, target, ranged=False)
            die = self._attack_d20(druid, rng, adv)
            nat = die.kept[0]
            total = nat + atk["bonus"]
            hit = nat == 20 or (nat != 1 and total >= target.ac())
            self._rec_attack(druid, die, adv, total, target, hit,
                             nat == 20 or any(
                                 c in getattr(target, "conditions", set())
                                 for c in (COND_PARALYZED, COND_UNCONSCIOUS, COND_SLEEP)),
                             label=f"{wname}（兽形）")
            if not hit:
                results.append(f"{wname}未命中（{total}）")
                continue
            crit = nat == 20 or any(
                c in getattr(target, "conditions", set())
                for c in (COND_PARALYZED, COND_UNCONSCIOUS, COND_SLEEP))
            n, s, m0 = parse_expr(atk.get("dmg") or "1d4")
            r = roll(rng, f"{n * (2 if crit else 1)}d{s}" + (f"+{m0}" if m0 else ""))
            dmg = r.total + int((atk.get("extra", "+0") or "+0").lstrip("+") or 0)
            dmg, _ = self.apply_resistance(target,
                                           D.MONSTER_WEAPONS.get(atk["weapon"], {}).get("type", ""),
                                           dmg)
            info = self.apply_damage(target, dmg)
            total_dmg += dmg
            self._register_engagement(druid, target, ranged=False)
            self._post_hit_effects(druid, target, crit, info.get("damage", dmg), info,
                                   rng, onhit=atk.get("onhit"))
            results.append(f"{wname}命中{'（暴击）' if crit else ''}，{dmg} 点"
                           f"（HP {info['hp']}/{self._hp_max(target)}）")
        detail = f"{druid.name}（兽形）攻击 {target.name}：" + "；".join(results)
        self.log.append(detail)
        return {"ok": True, "hit": total_dmg > 0, "damage": total_dmg,
                "target_hp": max(self._hp_max(target), 0), "detail": detail}

    # ---------------- 怪物攻击
    def _monster_attack(self, att: Monster, target, weapon_key: str | None,
                        rng: random.Random, single: bool = False,
                        precision_bonus: int = 0) -> dict:
        """怪物攻击。single=True 时只打一次（借机攻击不触发多重攻击）。"""
        atk = None
        for a in att.attacks:
            if weapon_key and weapon_key in a["weapon"]:
                atk = a
                break
        if atk is None:
            atk = att.attacks[0]
        wkey = atk["weapon"]
        if wkey.startswith("spell:"):
            try:
                # 反制法术窗口（可能抛出 CounterspellInterrupted）
                self._counterspell_window(att, atk)
            except CounterspellInterrupted:
                detail = f"{att.name} 的法术被反制打断！"
                self.log.append(detail)
                return {"ok": True, "hit": False, "countered": True, "damage": 0,
                        "detail": detail}
            if "save_dc" in atk:
                return self._monster_save_spell(att, target, atk, rng)
            adv, _notes = self._advantage_for(att, target, ranged=True)
            die = self._attack_d20(att, rng, adv)
            nat = die.kept[0]
            total = nat + atk["bonus"] + precision_bonus
            hit = nat == 20 or (nat != 1 and total >= target.ac())
            self._rec_attack(att, die, adv, total, target, hit, nat == 20,
                             label="法术射线")
            if not hit:
                detail = f"{att.name} 的法术射线飞向 {target.name}：{die.describe()}+{atk['bonus']} = {total} —— 未命中。"
                self.log.append(detail)
                return {"ok": True, "hit": False, "damage": 0, "detail": detail}
            n, s, _m = parse_expr(atk["dmg"])
            r = roll(rng, f"{n * (2 if nat == 20 else 1)}d{s}")
            dmg = r.total
            crit = nat == 20
            dtype = atk.get("dtype", "fire")
            dmg, _res = self.apply_resistance(target, dtype, dmg)
            info = self.apply_damage(target, dmg)
            self._post_hit_effects(att, target, crit, info.get("damage", dmg), info, rng)
            detail = (f"{att.name} 的法术命中 {target.name}：{info.get('damage', dmg)} 点"
                      f"{DTYPE_CN.get(dtype, dtype)}伤害"
                      f"（HP {info['hp']}/{self._hp_max(target)}）{'（暴击）' if crit else ''}。")
            self.log.append(detail)
            return {"ok": True, "hit": True, "crit": crit, "damage": info.get("damage", dmg),
                    "target_hp": info["hp"], "down": info.get("down", False), "detail": detail}

        wname = (D.WEAPONS.get(wkey) or D.MONSTER_WEAPONS.get(wkey, {"name": wkey}))["name"]
        wtype = (D.WEAPONS.get(wkey) or D.MONSTER_WEAPONS.get(wkey, {})).get("type", "")
        # 镜影术拦截（命中判定前，整个攻击动作只检一次）
        mi_notes: list[str] = []
        if self._mirror_image_block(target, rng, mi_notes):
            detail = (f"{att.name} 攻击 {target.name} —— 打中的只是镜影！"
                      f"（{'；'.join(mi_notes)}）")
            self.log.append(detail)
            return {"ok": True, "hit": False, "damage": 0, "detail": detail}
        self.last_attack_round[att.name] = self.round
        results, total_dmg = [], 0
        atk_count = 1 if single else atk.get("multiattack", 1)
        for i in range(atk_count):
            adv, _notes = self._advantage_for(att, target, ranged=(wkey == "web"))
            die = self._attack_d20(att, rng, adv)
            nat = die.kept[0]
            debuff = self.attack_debuffs.pop(att.name, 0)
            total = nat + atk["bonus"] + debuff + precision_bonus
            hit = nat == 20 or (nat != 1 and total >= target.ac())
            self._rec_attack(att, die, adv, total, target, hit, nat == 20,
                             label=wname)
            if not hit:
                results.append(f"{wname}未命中（{die.describe()}+{atk['bonus']}={total}）")
                continue
            # 目标反应：护盾术（命中补救）
            if isinstance(target, Character):
                would_drop = target.hp_cur - max(1, target.hp_max // 6) <= 0
                if (self.has_reaction(target) and "shield" in target.known_spells()
                        and any(v > 0 for v in target.spell_slots().values())
                        and (would_drop or target.hp_cur < target.hp_max * 0.3)):
                    want = self._ask_reaction(target, {"reaction": "shield",
                                                       "damage": None,
                                                       "source": att.name})
                    if want:
                        self._consume_slot(target, 1)
                        self.use_reaction(target)
                        target.conditions.add(COND_SHIELDED)
                        self.add_timer(target, COND_SHIELDED, 1)
                        self.log.append(f"{target.name} 以反应施放护盾术！AC+5。")
                        if nat != 20 and total < target.ac():
                            if self.rolls and self.rolls[-1]["who"] == att.name:
                                self.rolls[-1]["hit"] = False
                                self.rolls[-1]["note"] = "护盾术"
                            results.append("护盾术令攻击落空！")
                            continue
            crit = nat == 20
            # 麻痹/昏迷/沉睡目标 5 尺内命中即暴击（近战）
            if not (wkey == "web") and any(
                    c in getattr(target, "conditions", set())
                    for c in (COND_PARALYZED, COND_UNCONSCIOUS, COND_SLEEP)):
                crit = True
            base = atk.get("dmg") or (D.WEAPONS.get(wkey, {}).get("dmg") or "1d4")
            if base.strip() in ("0", ""):
                dmg = int((atk.get("extra", "+0") or "+0").lstrip("+") or 0)
            else:
                n, s, m0 = parse_expr(base)
                r = roll(rng, f"{n * (2 if crit else 1)}d{s}" + (f"+{m0}" if m0 else ""))
                dmg = r.total + int((atk.get("extra", "+0") or "+0").lstrip("+") or 0)
            if att.enlarged and not D.MONSTER_WEAPONS.get(wkey, {}).get("ranged"):
                dmg += roll(rng, "1d4").total
            if self.round == 1 and "突袭" in "".join(att.traits):
                dmg += roll(rng, "2d6").total
                results.append("突袭！")
            if ("军团战术" in "".join(att.traits)
                    and any(self.alive(ally) and ally is not att
                            for ally in self.enemies()
                            if target.name in self.engagement.get(ally.name, set()))):
                dmg += roll(rng, "2d6").total
                results.append("军团战术！")
            if "冲锋" in "".join(att.traits) and i == 0 and \
                    not self.engagement.get(att.name, set()) & {target.name}:
                dmg += roll(rng, "1d6").total
                results.append("冲锋！")
            dmg, _res = self.apply_resistance(target, wtype, dmg)
            # 目标反应：灵活闪避（伤害减半）
            if isinstance(target, Character) and target.has_feature("uncanny_dodge") \
                    and self.has_reaction(target) and dmg >= target.hp_max * 0.3:
                if self._ask_reaction(target, {"reaction": "uncanny_dodge",
                                               "damage": dmg, "source": att.name}):
                    self.use_reaction(target)
                    dmg //= 2
                    results.append("灵活闪避（减半）")
            info = self.apply_damage(target, dmg)
            dmg_dealt = info.get("damage", dmg)
            total_dmg += dmg_dealt
            self._post_hit_effects(att, target, crit, dmg_dealt, info, rng,
                                   onhit=atk.get("onhit"))
            results.append(f"{wname}命中{'（暴击）' if crit else ''}，{dmg_dealt} 点"
                           f"（HP {info['hp']}/{self._hp_max(target)}）")
            if isinstance(target, Character):
                self.offer_hellish_rebuke(target, att, dmg_dealt)
            # 豺狼人饕餮狂暴：击倒后附赠噬咬
            if (info.get("down") and "饕餮狂暴" in "".join(att.traits)
                    and self.has_bonus(att) and isinstance(target, Character)):
                bite = next((a for a in att.attacks if a["weapon"] == "bite"), None)
                if bite:
                    self.use_bonus(att)
                    b = roll(rng, "1d4").total + 2
                    b, _ = self.apply_resistance(target, "piercing", b)
                    i2 = self.apply_damage(target, b)
                    results.append(f"饕餮狂暴噬咬 +{i2.get('damage', b)} 点")
        # follow 组合攻击（5e 多重攻击模式：矛+噬咬、喙+爪击等）
        follow_key = atk.get("follow")
        if follow_key and not single and target.hp_cur > 0:
            f_atk = next((a for a in att.attacks if a["weapon"] == follow_key), None)
            if f_atk is not None:
                r2 = self._monster_attack(att, target, follow_key, rng, single=True)
                results.append(r2.get("detail", ""))
                total_dmg += r2.get("damage", 0) or 0
        detail = f"{att.name} 攻击 {target.name}：" + "；".join(results)
        self.log.append(detail)
        any_crit = "（暴击）" in detail
        return {"ok": True, "hit": total_dmg > 0, "damage": total_dmg, "crit": any_crit,
                "target_hp": max(self._hp_max(target), 0), "down": target.hp_cur == 0,
                "detail": detail}

    def _counterspell_window(self, caster_mon: Monster, atk: dict) -> None:
        """怪物施法时触发反应反制窗口。"""
        spell_level = atk.get("spell_level", 1)
        for c in self.active_party():
            if not (isinstance(c, Character) and self.has_reaction(c)
                    and "counterspell" in c.known_spells()):
                continue
            slots = c.spell_slots()
            usable = [lv for lv in slots if slots[lv] > 0 and lv >= spell_level]
            auto = bool(usable)
            want = auto
            if callable(self.reaction_prompt):
                want = bool(self.reaction_prompt(c, {
                    "reaction": "counterspell", "spell_level": spell_level,
                    "auto": auto, "source": caster_mon.name}))
            elif c.klass in ("wizard", "sorcerer", "warlock"):
                want = auto
            if not want:
                continue
            if auto:
                lv = min(usable)
                self._consume_slot(c, lv)
            else:
                low = min((lv for lv in slots if slots[lv] > 0), default=None)
                if low is None:
                    continue
                if not self._consume_slot(c, low):
                    continue
                check = c.check(self.rng, "arcana", 10 + spell_level)
                if not check["success"]:
                    self.log.append(f"{c.name} 的反制法术未能压制法术（检定失败）。")
                    self.use_reaction(c)
                    continue
            self.use_reaction(c)
            self.log.append(f"{c.name} 以反制法术打断了 {caster_mon.name} 的施法！")
            raise CounterspellInterrupted()

    def _monster_save_spell(self, att: Monster, target, atk: dict,
                            rng: random.Random) -> dict:
        dc = atk["save_dc"]
        if isinstance(target, Character):
            sv = target.save(rng, atk["save"], dc)
            self._rec_save(target, sv, dc, "豁免")
            success, d = sv["success"], sv["detail"]
        else:
            dr = roll(rng, "d20")
            success = dr.kept[0] + target.save_bonus(atk["save"]) >= dc
            self._rec_save(target, dr.kept[0], dc, "豁免", success=success)
            d = dr.describe()
        dmg = roll(rng, atk["dmg"]).total
        if success:
            dmg //= 2
            eff = "豁免成功，伤害减半"
        else:
            eff = "豁免失败"
        dtype = atk.get("dtype", "fire")
        dmg, _res = self.apply_resistance(target, dtype, dmg)
        info = self.apply_damage(target, dmg)
        detail = (f"{att.name} 的爆燃法术扑向 {target.name}：{d} vs DC{dc} —— {eff}，"
                  f"{info.get('damage', dmg)} 点{DTYPE_CN.get(dtype, dtype)}伤害"
                  f"（HP {info['hp']}/{self._hp_max(target)}）。")
        self.log.append(detail)
        return {"ok": True, "hit": True, "damage": info.get("damage", dmg), "saved": success,
                "target_hp": info["hp"], "down": info.get("down", False), "detail": detail}

    # ------------------------------------------------------------ 命中后效果
    def _post_hit_effects(self, att, target, crit: bool, dmg: int, info: dict,
                          rng: random.Random, ranged: bool = False,
                          onhit: str | None = None) -> None:
        # 沉睡者受创即醒
        if COND_SLEEP in getattr(target, "conditions", set()) and target.hp_cur > 0:
            self._remove_condition(target, COND_SLEEP)
            self.log.append(f"{target.name} 疼醒过来！")
        # 已稳定者再次受创 → 失去稳定，重新开始死亡豁免
        if (isinstance(target, Character) and dmg > 0
                and COND_STABLE in target.conditions):
            target.conditions.discard(COND_STABLE)
        # 0HP 角色受创 → 死亡豁免自动失败（暴击两次）
        if (isinstance(target, Character) and COND_UNCONSCIOUS in target.conditions
                and dmg > 0 and not info.get("relentless")):
            target.death_fails += 2 if crit else 1
            if target.death_fails >= 3:
                target.conditions.add("dead")
                self.log.append(f"☠ {target.name} 伤重不治！")
        # 不死者坚韧（僵尸）
        if (isinstance(target, Monster) and info.get("down")
                and "不死者坚韧" in "".join(target.traits)):
            dc = 5 + dmg
            sv = roll(rng, "d20").kept[0] + target.save_bonus("con")
            if sv >= dc:
                target.hp_cur = 1
                info["down"] = False
                self.log.append(f"{target.name} 靠不死者坚韧挺了过来（豁免 {sv} vs DC{dc}）！")
        # 邪主眷佑（魔鬼邪术师击杀获得临时生命）
        if (isinstance(att, Character) and att.has_feature("dark_blessing")
                and info.get("down") and self._hp_max(target) <= 0):
            gain = att.mod("cha") + att.level
            att.temp_hp = max(att.temp_hp, gain)
            self.log.append(f"{att.name} 邪主眷佑：获得 {gain} 点临时生命。")
        # 命中后 onhit 效果（由调用方按本次实际使用的攻击传入）
        if onhit and target.hp_cur > 0 and not info.get("down"):
            self._apply_onhit(att, target, onhit, rng, dmg=dmg)
        # 专注打断
        if isinstance(target, Character) and target.concentration and dmg > 0:
            dc = max(10, dmg // 2)
            sv = target.save(rng, "con", dc,
                             advantage=1 if target.has_feature("war_caster") else 0)
            self._rec_save(target, sv, dc, "专注豁免")
            if not sv["success"]:
                self.log.append(f"{target.name} 未能维持专注，[{target.concentration}] 中断。")
                self._break_concentration(target)
        elif isinstance(target, Monster) and target.concentration and dmg > 0:
            dc = max(10, dmg // 2)
            dr = roll(rng, "d20")
            ok = dr.kept[0] + target.save_bonus("con") >= dc
            self._rec_save(target, dr.kept[0], dc, "专注豁免", success=ok)
            if not ok:
                self.log.append(f"{target.name} 的专注被打断。")
                target.concentration = None

    def _break_concentration(self, c: Character) -> None:
        c.concentration = None
        c.conditions.discard(COND_MARKED)
        if getattr(c, "weapon_buff", None):
            c.weapon_buff = None

    def _apply_onhit(self, att, target, onhit: str, rng: random.Random,
                     dmg: int = 0) -> None:
        if onhit == "prone_dc11_str" or onhit == "charge_prone_dc14_str":
            dc = 14 if onhit.startswith("charge") else 11
            if isinstance(target, Character):
                sv = target.save(rng, "str", dc)
                ok, d = sv["success"], sv["detail"]
            else:
                s = roll(rng, "d20").kept[0] + target.save_bonus("str")
                ok, d = s >= dc, f"[d20: {s}]"
            if not ok:
                self.set_condition(target, COND_PRONE)
                self.log.append(f"{target.name} 被扑倒在地！（{d}）")
        elif onhit == "paralyze_dc10_con":
            if isinstance(target, Character) and target.race in ELF_RACES:
                self.log.append(f"{target.name} 是精灵，麻痹之爪对其无效。")
                return
            if isinstance(target, Monster) and target.is_undead():
                return
            if isinstance(target, Character):
                sv = target.save(rng, "con", 10)
                ok, d = sv["success"], sv["detail"]
            else:
                s = roll(rng, "d20").kept[0] + target.save_bonus("con")
                ok, d = s >= 10, f"[d20: {s}]"
            if not ok:
                self.set_condition(target, COND_PARALYZED)
                self.add_timer(target, COND_PARALYZED, 10, save="wis", dc=10)
                self.log.append(f"{target.name} 被麻痹了！（{d}）")
        elif onhit.startswith("web_dc"):
            dc = int(onhit.replace("web_dc", "") or 11)
            if isinstance(target, Character):
                sv = target.save(rng, "str", dc)
                ok, d = sv["success"], sv["detail"]
            else:
                s = roll(rng, "d20").kept[0] + target.save_bonus("str")
                ok, d = s >= dc, f"[d20: {s}]"
            if not ok:
                self.set_condition(target, COND_RESTRAINED)
                self.add_timer(target, COND_RESTRAINED, 10, save="str", dc=dc)
                self.log.append(f"{target.name} 被蛛网黏住，陷入束缚！（{d}）")
        elif onhit == "drain":
            # 尸妖生命汲取：目标生命上限被削减（长休恢复），尸妖回复等量生命
            if isinstance(target, Character):
                drained = min(dmg, max(0, target.hp_max - 1))
                target.hp_max -= drained
                target.hp_drain += drained
                if target.hp_cur > target.hp_max:
                    target.hp_cur = target.hp_max
                healed = att.heal(dmg)
                self.log.append(f"生命汲取！{target.name} 生命上限 −{drained}，"
                                f"尸妖回复 {healed} 点。")
        else:
            m = re.fullmatch(r"poison_(\d+d\d+)_dc(\d+)", onhit)
            if m:
                expr, dc = m.group(1), int(m.group(2))
                if isinstance(target, Monster) and target.poison_immune:
                    self.log.append(f"{target.name} 对毒素免疫。")
                    return
                if isinstance(target, Character):
                    sv = target.save(rng, "con", dc, effect="poison")
                    ok = sv["success"]
                else:
                    ok = roll(rng, "d20").kept[0] + target.save_bonus("con") >= dc
                if not ok:
                    pdmg = roll(rng, expr).total
                    pdmg, _ = self.apply_resistance(target, "poison", pdmg)
                    i2 = self.apply_damage(target, pdmg)
                    self.set_condition(target, COND_POISONED)
                    self.add_timer(target, COND_POISONED, 10)
                    self.log.append(f"{target.name} 中毒！（额外 {pdmg} 点毒素并陷入中毒状态，"
                                    f"HP {i2['hp']}）")
                else:
                    self.log.append(f"{target.name} 抵抗了毒素。")

    # ---------------- 状态设置（统一入口）
    def set_condition(self, target, key: str) -> None:
        target.conditions.add(key)

    # ------------------------------------------------------------ 反应系统
    def offer_defensive_reactions(self, target, dmg: int, rng: random.Random,
                                  context: str = "") -> dict:
        """受击后可用的反应（护盾术/灵活闪避/狱火斥喝）。返回修订后的伤害。"""
        if not isinstance(target, Character) or dmg <= 0:
            return {"dmg": dmg}
        # 灵活闪避（游荡者5级）：伤害减半
        if (target.has_feature("uncanny_dodge") and self.has_reaction(target)
                and (dmg >= target.hp_max * 0.3 or target.hp_cur - dmg <= 0)):
            want = self._ask_reaction(target, {"reaction": "uncanny_dodge",
                                               "damage": dmg, "context": context})
            if want:
                self.use_reaction(target)
                dmg = dmg // 2
                self.log.append(f"{target.name} 灵活闪避！伤害减半。")
                return {"dmg": dmg}
        # 护盾术（反应）：命中前提升 AC——此处为受击后补救（近似触发点）
        if ("shield" in target.known_spells() and self.has_reaction(target)
                and (target.hp_cur - dmg <= 0 or target.hp_cur - dmg < target.hp_max * 0.25)):
            slots = target.spell_slots()
            if any(v > 0 for v in slots.values()):
                want = self._ask_reaction(target, {"reaction": "shield", "damage": dmg,
                                                   "context": context})
                if want:
                    self._consume_slot(target, 1)
                    self.use_reaction(target)
                    target.conditions.add(COND_SHIELDED)
                    self.add_timer(target, COND_SHIELDED, 1)
                    self.log.append(f"{target.name} 以反应施放护盾术！AC+5 直到下回合开始。")
                    return {"dmg": dmg, "shielded": True}
        return {"dmg": dmg}

    def offer_hellish_rebuke(self, target, attacker, dmg: int) -> None:
        if not isinstance(target, Character):
            return
        if ("hellish_rebuke" in target.known_spells() and self.has_reaction(target)
                and target.hp_cur > 0 and dmg > 0
                and target.can_use_innate("hellish_rebuke")):
            want = self._ask_reaction(target, {"reaction": "hellish_rebuke",
                                               "damage": dmg, "source": attacker.name})
            if not want:
                return
            self.use_reaction(target)
            if target.is_innate("hellish_rebuke") and target.klass not in D.SPELLS["hellish_rebuke"]["classes"]:
                target.innate_used.add("hellish_rebuke")
            else:
                if not self._consume_slot(target, 1):
                    return
            dmg2 = roll(self.rng, "2d10").total
            dmg2, _ = self.apply_resistance(attacker, "fire", dmg2) if isinstance(
                attacker, object) else (dmg2, [])
            if isinstance(attacker, Character):
                sv = attacker.save(self.rng, "dex", 8 + target.prof_bonus()
                                   + target.mod("cha"))
                if sv["success"]:
                    dmg2 //= 2
                    self.log.append(f"{attacker.name} 豁免成功，狱火伤害减半。")
            info = self.apply_damage(attacker, dmg2)
            self.log.append(f"{target.name} 施展狱火斥喝！{attacker.name} 受到 "
                            f"{info.get('damage', dmg2)} 点火焰伤害。")

    def _ask_reaction(self, target, ctx: dict) -> bool:
        if callable(self.reaction_prompt):
            return bool(self.reaction_prompt(target, ctx))
        # 自动规则：保命类反应在重伤时自动触发
        kind = ctx.get("reaction")
        if kind == "shield" and "shield" in target.known_spells():
            slots = target.spell_slots()
            dmg_est = ctx.get("damage") or target.hp_cur   # 无伤害预估时按"会倒下"处理
            return any(v > 0 for v in slots.values()) and dmg_est >= target.hp_cur
        if kind == "uncanny_dodge":
            return ctx.get("damage", 0) >= target.hp_max * 0.3
        if kind == "hellish_rebuke":
            return target.hp_cur < target.hp_max * 0.6
        if kind == "counterspell":
            return ctx.get("auto", False)
        return False

    # ---------------- 借机攻击
    def opportunity_attack(self, mover, attacker) -> dict:
        """attacker 对正在离开的 mover 的借机攻击（反应，只打一次）。"""
        if not self.has_reaction(attacker) or not self.alive(attacker):
            return {"ok": False, "error": "无可用反应"}
        self.use_reaction(attacker)
        if isinstance(attacker, Monster):
            return self._monster_attack(attacker, mover, None, self.rng, single=True)
        weapon = D.WEAPONS.get(attacker.weapons[0]) if attacker.weapons else None
        if weapon is None or weapon.get("ranged"):
            weapon = {"name": "徒手", "dmg": "1", "type": "bludgeoning",
                      "props": [], "ranged": False}
        return self._pc_attack(attacker, mover, weapon, self.rng)

    def move_away(self, c) -> dict:
        """普通移动远离：所有纠缠者的借机攻击；任一命中则移动失败。
        被擒抱者移速归零，必须先挣脱（PHB 状态规则）。"""
        if COND_GRAPPLED in c.conditions:
            detail = f"{c.name} 被擒抱着，无法移动（先用动作挣脱）。"
            self.log.append(detail)
            return {"ok": False, "detail": detail}
        engagors = [self.find(n) for n in self.engagement.get(c.name, set())]
        engagors = [e for e in engagors if e and self.alive(e) and e.side != c.side]
        stopped = False
        for e in engagors:
            r = self.opportunity_attack(c, e)
            if r.get("hit"):
                stopped = True
        if stopped:
            detail = f"{c.name} 试图离开，但被借机攻击拦下（移动失败）。"
        else:
            self.do_disengage_quiet(c)
            detail = f"{c.name} 成功拉开距离。"
        self.log.append(detail)
        return {"ok": not stopped, "detail": detail}

    def do_disengage(self, c) -> dict:
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        self.do_disengage_quiet(c)
        detail = f"{c.name} 谨慎撤离，摆脱了近身纠缠（本回合不引发借机攻击）。"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def do_disengage_quiet(self, c) -> None:
        self.engagement.pop(c.name, None)
        for k, v in self.engagement.items():
            v.discard(c.name)

    def stabilize(self, helper, target) -> dict:
        """稳定伤势（PHB：动作，医药 DC10）。仅对 0 HP 且未稳定的角色有效。"""
        if not (isinstance(target, Character) and target.hp_cur <= 0
                and "dead" not in target.conditions):
            return {"ok": False, "error": "目标不需要稳定"}
        if COND_STABLE in target.conditions:
            return {"ok": False, "error": "目标已经稳定"}
        if not self.use_action(helper):
            return {"ok": False, "error": "本回合动作已用完"}
        r = helper.check(self.rng, "medicine", 10)
        if r["success"]:
            target.conditions.add("stable")
            self.log.append(f"{helper.name} 为 {target.name} 稳定了伤势（{r['detail']}）——"
                            "不会再累积死亡豁免失败。")
        else:
            self.log.append(f"{helper.name} 未能稳定 {target.name} 的伤势（{r['detail']}）。")
        return {"ok": r["success"], "detail": self.log[-1]}

    def do_stand(self, c) -> dict:
        if COND_PRONE not in c.conditions:
            return {"ok": False, "error": "没有倒地"}
        c.conditions.discard(COND_PRONE)
        detail = f"{c.name} 站了起来（消耗半速）。"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def do_shove(self, c, target, push: bool = False) -> dict:
        """推撞（替换一次攻击）：运动 vs 运动/杂技 → 倒地 或 推开5尺。"""
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        ra = roll(self.rng, "d20").kept[0] + self._athletics(c)
        if isinstance(target, Character):
            defense_skill = "acrobatics" if "acrobatics" in target.skills else "athletics"
            rd = roll(self.rng, "d20").kept[0] + target.skill_mod(defense_skill)
        else:
            rd = roll(self.rng, "d20").kept[0] + target.save_bonus("str")
        d = f"{ra} vs {rd}"
        if ra >= rd:
            self.engagement.setdefault(c.name, set()).add(target.name)
            self.engagement.setdefault(target.name, set()).add(c.name)
            if push:
                self.do_disengage_quiet(target)
                detail = f"{c.name} 将 {target.name} 推开5尺（{d}）——目标脱离纠缠！"
            else:
                self.set_condition(target, COND_PRONE)
                detail = f"{c.name} 推撞 {target.name} 成功（{d}）——目标倒地！"
        else:
            detail = f"{c.name} 推撞 {target.name} 失败（{d}）。"
        self.log.append(detail)
        return {"ok": ra >= rd, "detail": detail}

    def do_grapple(self, c, target) -> dict:
        """擒抱（替换一次攻击）：目标体型 ≤ 我方一级。被擒抱者移速归零。"""
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        ra = roll(self.rng, "d20").kept[0] + self._athletics(c)
        if isinstance(target, Character):
            rd = roll(self.rng, "d20").kept[0] + target.skill_mod("athletics")
        else:
            rd = roll(self.rng, "d20").kept[0] + target.save_bonus("str")
        if ra >= rd:
            self.set_condition(target, COND_GRAPPLED)
            self.grappler_of[target.name] = c.name
            self.engagement.setdefault(c.name, set()).add(target.name)
            self.engagement.setdefault(target.name, set()).add(c.name)
            detail = f"{c.name} 擒抱住 {target.name}（{ra} vs {rd}）——目标移速归零。"
        else:
            detail = f"{c.name} 擒抱 {target.name} 失败（{ra} vs {rd}）。"
        self.log.append(detail)
        return {"ok": ra >= rd, "detail": detail}

    def do_escape_grapple(self, c) -> dict:
        """挣脱擒抱（动作）：运动（或杂技）对抗擒抱者的运动。"""
        if COND_GRAPPLED not in c.conditions:
            return {"ok": False, "error": "没有被擒抱"}
        grappler = self.find(self.grappler_of.get(c.name, "")) if \
            self.grappler_of.get(c.name) else None
        ra = roll(self.rng, "d20").kept[0] + self._athletics(c)
        if grappler is not None:
            rd = roll(self.rng, "d20").kept[0] + self._athletics(grappler)
        else:
            rd = 10 + self.rng.randint(0, 4)
        if ra >= rd:
            self._remove_condition(c, COND_GRAPPLED)
            self.do_disengage_quiet(c)
            detail = f"{c.name} 挣脱了擒抱（{ra} vs {rd}）！"
        else:
            detail = f"{c.name} 未能挣脱擒抱（{ra} vs {rd}）。"
        self.log.append(detail)
        return {"ok": ra >= rd, "detail": detail}

    def _athletics(self, c) -> int:
        """运动对抗加值：角色用运动技能，怪物没有技能系统用力量调整值。"""
        if isinstance(c, Character):
            return c.skill_mod("athletics")
        return c.mod("str")

    # ------------------------------------------------------------ 其他动作
    def do_help(self, helper, target) -> dict:
        if not self.use_action(helper):
            return {"ok": False, "error": "本回合动作已用完"}
        self.helped[target.name] = helper.name
        detail = f"{helper.name} 为 {target.name} 制造破绽（其下一次对 {target.name} 的攻击具有优势）。"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def do_dodge(self, c, bonus: bool = False) -> dict:
        if bonus:
            if not self.use_bonus(c):
                return {"ok": False, "error": "本回合附赠动作已用完"}
        else:
            if not self.use_action(c):
                return {"ok": False, "error": "本回合动作已用完"}
        self.dodging.add(c.name)
        detail = f"{c.name} 采取防御姿态，直到下回合开始前针对其的攻击具有劣势。"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def do_hide(self, c, rng: random.Random) -> dict:
        if not isinstance(c, Character):
            return {"ok": False, "error": "只有角色可以尝试躲藏"}
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        return self._hide_check(c, rng)

    def do_hide_bonus(self, c, rng: random.Random) -> dict:
        """附赠动作躲藏（狡诈动作等）。"""
        if not isinstance(c, Character):
            return {"ok": False, "error": "只有角色可以尝试躲藏"}
        if not self.use_bonus(c):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        return self._hide_check(c, rng)

    def _hide_check(self, c, rng: random.Random) -> dict:
        dc = max((e.passive_perception() for e in self.active_enemies()
                  if e is not c), default=10)
        adv = 1 if (c.mech().get("stealth_adv") and self.dark) else 0
        r = c.check(rng, "stealth", dc, advantage=adv)
        if r["success"]:
            c.conditions.add(COND_HIDDEN)
            detail = f"{c.name} 潜入阴影，敌人失去其踪迹。{r['detail']}"
        else:
            detail = f"{c.name} 的藏身被识破。{r['detail']}"
        self.log.append(detail)
        return {"ok": r["success"], "detail": detail}

    def use_item(self, c, item: str, target=None) -> dict:
        """使用物品（动作）。带 heal 表达式的药水可喂给倒地队友（直接拉起并回复）；
        医疗包（stabilize）以动作稳定倒地队友，无需检定（PHB）。"""
        if not (isinstance(c, Character) and c.inventory.get(item, 0) > 0):
            return {"ok": False, "error": "没有该物品"}
        spec = D.ITEMS.get(item, {})
        heal_expr = spec.get("heal")
        if spec.get("stabilize"):
            if not isinstance(target, Character):
                return {"ok": False, "error": "医疗包需要指定目标"}
            if "dead" in target.conditions or COND_STABLE in target.conditions \
                    or target.hp_cur > 0:
                return {"ok": False, "error": "目标无需稳定"}
            if not self.use_action(c):
                return {"ok": False, "error": "本回合动作已用完"}
            c.inventory[item] -= 1
            target.conditions.add(COND_STABLE)
            detail = (f"{c.name} 用医疗包为 {target.name} 包扎止血，伤势稳定"
                      f"（剩余用次 {c.inventory[item]}）。")
            self.log.append(detail)
            return {"ok": True, "detail": detail}
        if not heal_expr:
            if not self.use_action(c):
                return {"ok": False, "error": "本回合动作已用完"}
            detail = f"{c.name} 使用了 {spec.get('name', item)}。"
            self.log.append(detail)
            return {"ok": True, "detail": detail}
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        r = roll(self.rng or random.Random(), heal_expr)
        name = spec.get("name", item)
        if target is not None and target is not c:
            c.inventory[item] -= 1
            amt = target.heal(r.total)
            detail = (f"{c.name} 把{name}喂给 {target.name}{r.describe()} → "
                      f"回复 {amt} 点并恢复意识（HP {target.hp_cur}/{target.hp_max}）。")
        else:
            c.inventory[item] -= 1
            amt = c.heal(r.total)
            detail = f"{c.name} 喝下{name}{r.describe()} → 回复 {amt} 点（HP {c.hp_cur}/{c.hp_max}）。"
        self.log.append(detail)
        return {"ok": True, "detail": detail, "healed": amt}

    def second_wind(self, c: Character, rng: random.Random) -> dict:
        if not self.alive(c):
            return {"ok": False, "error": "失去行动能力的角色无法回气"}
        if c.second_wind_used:
            return {"ok": False, "error": "回气已在本短休中使用过"}
        if not self.use_bonus(c):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        c.second_wind_used = True
        r = roll(rng, "1d10")
        amt = c.heal(r.total + c.level)
        detail = f"{c.name} 使用回气，回复 {amt} 点（HP {c.hp_cur}/{c.hp_max}）。{r.describe()}+{c.level}"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def action_surge(self, c: Character) -> dict:
        if not c.has_feature("action_surge"):
            return {"ok": False, "error": "战士 2 级才有动作如潮"}
        if c.action_surge_used:
            return {"ok": False, "error": "动作如潮已在本短休中使用过"}
        c.action_surge_used = True
        self.actions_used[c.name] = False
        detail = f"{c.name} 使用动作如潮！本回合获得一个额外的动作。"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def start_rage(self, c: Character) -> dict:
        if c.klass != "barbarian":
            return {"ok": False, "error": "只有野蛮人能狂暴"}
        if not c.rage_left():
            return {"ok": False, "error": "狂暴次数已用尽"}
        if not self.use_bonus(c):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        c.rage_used += 1
        c.conditions.add(COND_RAGING)
        self.add_timer(c, COND_RAGING, 10)
        detail = f"{c.name} 进入狂暴！（伤害+2，钝器/穿刺/挥砍抗性，1分钟）"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def breath_weapon(self, c: Character, targets: list, rng: random.Random) -> dict:
        mech = c.mech().get("breath_weapon")
        if not mech:
            return {"ok": False, "error": "该角色没有龙息"}
        if c.breath_weapon_used:
            return {"ok": False, "error": "龙息需要短休/长休恢复"}
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        c.breath_weapon_used = True
        dice = mech["dice1"] if c.level < 5 else mech["dice5"]
        dc = 8 + c.prof_bonus() + c.mod("con")
        lines = []
        for t in targets:
            sv = t.save(rng, "dex", dc) if isinstance(t, Character) else None
            if sv is None:
                dr = roll(rng, "d20")
                ok = dr.kept[0] + t.save_bonus("dex") >= dc
                self._rec_save(t, dr.kept[0], dc, "龙息豁免", success=ok)
                d = dr.describe()
            else:
                self._rec_save(t, sv, dc, "龙息豁免")
                ok, d = sv["success"], sv["detail"]
            n, s, _m = parse_expr(dice)
            dmg = roll(rng, f"{n}d{s}").total
            if ok:
                dmg //= 2
                eff = "豁免成功，减半"
            else:
                eff = "豁免失败"
            dmg, _res = self.apply_resistance(t, mech["dtype"], dmg)
            info = self.apply_damage(t, dmg)
            lines.append(f"{t.name} {eff}（{d}），{info.get('damage', dmg)} 点"
                         f"{DTYPE_CN.get(mech['dtype'], mech['dtype'])}伤害"
                         f"（HP {info['hp']}/{self._hp_max(t)}）")
        detail = f"{c.name} 呼出{DTYPE_CN.get(mech['dtype'], '')}龙息（DC{dc}）：" + "；".join(lines)
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def lay_on_hands(self, c: Character, target, amount: int) -> dict:
        if c.klass != "paladin":
            return {"ok": False, "error": "只有圣武士能圣疗"}
        amount = max(1, min(amount, c.lay_on_hands_left(), self._hp_max(target) - target.hp_cur))
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        c.lay_on_hands_used += amount
        healed = target.heal(amount)
        detail = f"{c.name} 以圣疗回复 {target.name} {healed} 点（HP {target.hp_cur}/{self._hp_max(target)}）。"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def divine_smite(self, c: Character, target, slot_level: int, rng: random.Random,
                     crit: bool = False) -> dict:
        if not c.has_feature("divine_smite"):
            return {"ok": False, "error": "圣武士 2 级才有神佑斩"}
        if not self._consume_slot(c, slot_level):
            return {"ok": False, "error": f"没有可用的 {slot_level} 环法术位"}
        dice = 2 + (slot_level - 1)
        extra = ""
        if isinstance(target, Monster) and (target.is_undead() or target.is_fiend()):
            dice += 1
            extra = "（对不死/魔鬼+1d8）"
        dice = min(dice, 5)
        rolled_dice = dice * (2 if crit else 1)      # 暴击伤害骰翻倍
        r = roll(rng, f"{rolled_dice}d8")
        dmg, _res = self.apply_resistance(target, "radiant", r.total)
        info = self.apply_damage(target, dmg)
        detail = (f"{c.name} 神佑斩！{rolled_dice}d8={r.total}"
                  f"{'（暴击翻倍）' if crit else ''}{extra} → "
                  f"{info.get('damage', dmg)} 点光耀伤害（HP {info['hp']}/{self._hp_max(target)}）。")
        self.log.append(detail)
        return {"ok": True, "damage": info.get("damage", dmg), "detail": detail}

    # ---------------- 武僧气
    def flurry_of_blows(self, c: Character, target) -> dict:
        if c.klass != "monk":
            return {"ok": False, "error": "只有武僧能使用气"}
        if c.ki_left() <= 0:
            return {"ok": False, "error": "气点不足"}
        if not self.use_bonus(c):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        c.ki_used += 1
        die = "1d6" if c.level >= 5 else "1d4"
        lines = []
        for _ in range(2):
            weapon = {"name": "徒手", "dmg": die, "type": "bludgeoning", "props": ["monk"]}
            r = self._pc_attack(c, target, weapon, self.rng)
            lines.append("命中" if r.get("hit") else "落空")
        detail = f"{c.name} 疾风连击！（{'；'.join(lines)}）"
        self.log.append(detail)
        return {"ok": True, "detail": detail, "results": lines}

    def martial_bonus_attack(self, c: Character, target) -> dict:
        """武术：攻击动作后以附赠动作打一次徒手打击（不耗气）。"""
        if c.klass != "monk":
            return {"ok": False, "error": "只有武僧能使用武术"}
        if not self.use_bonus(c):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        die = "1d6" if c.level >= 5 else "1d4"
        weapon = {"name": "徒手", "dmg": die, "type": "bludgeoning", "props": ["monk"]}
        r = self._pc_attack(c, target, weapon, self.rng)
        detail = f"{c.name} 以武术打出附赠徒手打击。{r.get('detail', '')}"
        self.log.append(detail)
        return {"ok": True, "hit": r.get("hit", False), "detail": detail}

    def frenzy_attack(self, c: Character, target, weapon_key: str | None) -> dict:
        """狂战士：狂暴期间每回合可用附赠动作打一次近战攻击（狂暴结束后力竭+1）。"""
        if not c.has_feature("frenzy"):
            return {"ok": False, "error": "需要狂战士本源（3级）"}
        if COND_RAGING not in c.conditions:
            return {"ok": False, "error": "只有狂暴期间才能狂暴攻击"}
        if not self.use_bonus(c):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        self.frenzy_used.add(c.name)
        r = self.attack(c, target, weapon_key, self.rng)
        detail = f"{c.name} 狂暴攻击！{r.get('detail', '')}"
        self.log.append(detail)
        return {"ok": True, "hit": r.get("hit", False), "detail": detail}

    def stunning_strike(self, c: Character, target) -> dict:
        if not c.has_feature("stunning_strike"):
            return {"ok": False, "error": "武僧 5 级才有震慑拳"}
        if c.ki_left() <= 0:
            return {"ok": False, "error": "气点不足"}
        c.ki_used += 1
        dc = 8 + c.prof_bonus() + c.mod("wis")
        if isinstance(target, Character):
            sv = target.save(self.rng, "con", dc)
            self._rec_save(target, sv, dc, "震慑拳豁免")
            ok, d = sv["success"], sv["detail"]
        else:
            dr = roll(self.rng, "d20")
            ok = dr.kept[0] + target.save_bonus("con") >= dc
            self._rec_save(target, dr.kept[0], dc, "震慑拳豁免", success=ok)
            d = dr.describe()
        if ok:
            detail = f"{c.name} 震慑拳被 {target.name} 抵抗（{d} vs DC{dc}）。"
        else:
            self.set_condition(target, COND_STUNNED)
            self.add_timer(target, COND_STUNNED, 2, save="con", dc=dc)
            detail = f"{c.name} 震慑拳命中！{target.name} 震慑（{d} vs DC{dc}）。"
        self.log.append(detail)
        return {"ok": not ok, "detail": detail}

    # ---------------- 吟游诗人
    def bardic_inspiration(self, bard: Character, target) -> dict:
        if bard.klass != "bard":
            return {"ok": False, "error": "只有吟游诗人能激励"}
        if bard.bardic_inspiration_used >= bard.bardic_total():
            return {"ok": False, "error": "激励次数已用尽"}
        if not self.use_bonus(bard):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        bard.bardic_inspiration_used += 1
        die = 8 if bard.level >= 5 else 6
        target.bardic_dice += 1
        target.bardic_die = die
        detail = (f"{bard.name} 以激励鼓舞 {target.name}（1d{die}，10分钟内可加到一次掷骰；"
                  f"剩余 {bard.bardic_total() - bard.bardic_inspiration_used} 次）。")
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def cutting_words(self, bard: Character, target) -> dict:
        if not bard.has_feature("cutting_words"):
            return {"ok": False, "error": "需要知识学院（诗人3级）"}
        if bard.bardic_inspiration_used >= bard.bardic_total():
            return {"ok": False, "error": "激励次数已用尽（诛心之言消耗一次激励）"}
        if not self.use_reaction(bard):
            return {"ok": False, "error": "反应不可用"}
        bard.bardic_inspiration_used += 1
        die = "1d8" if bard.level >= 5 else "1d6"
        r = roll(self.rng, die)
        self.attack_debuffs[target.name] = self.attack_debuffs.get(target.name, 0) - r.total
        detail = (f"{bard.name} 诛心之言！{target.name} 下次攻击骰获得 −{r.total}"
                  f"（{die}）减值。")
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    # ---------------- 德鲁伊野变
    def wild_shape(self, c: Character, form: str) -> dict:
        if c.klass != "druid" or c.level < 2:
            return {"ok": False, "error": "德鲁伊 2 级才能荒野变形"}
        if c.wild_shape_used >= 2:
            return {"ok": False, "error": "野变次数已用尽（短休恢复）"}
        beast = D.MONSTERS.get(form)
        if not beast or not beast.get("beast"):
            return {"ok": False, "error": f"未知野兽形态 {form}"}
        if c.level < 4 and beast.get("cr", 9) > 0.25:
            return {"ok": False, "error": "2~3 级只能变为 CR≤1/4 的野兽"}
        if beast.get("speed", 0) == 0:
            return {"ok": False, "error": "无效形态"}
        if not self.use_action(c):
            return {"ok": False, "error": "本回合动作已用完"}
        c.wild_shape_used += 1
        hp = max(1, roll(self.rng, beast["hp"]).total)
        c.wild_shape = {"form": form, "hp_cur": hp, "hp_max": hp}
        c.conditions.discard(COND_UNCONSCIOUS)
        detail = (f"{c.name} 化为{beast['name']}！兽形生命 {hp}，AC {beast['ac']}。"
                  f"兽形生命归零时自动变回本体（溢出伤害由本体承受）。")
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    def revert_shape(self, c: Character) -> dict:
        if not c.in_wild_shape():
            return {"ok": False, "error": "当前不是兽形"}
        if not self.use_bonus(c):
            return {"ok": False, "error": "本回合附赠动作已用完"}
        c.wild_shape = None
        detail = f"{c.name} 变回本体（HP {c.hp_cur}/{c.hp_max}）。"
        self.log.append(detail)
        return {"ok": True, "detail": detail}

    # ------------------------------------------------------------ 法术
    def _consume_slot(self, caster: Character, level: int) -> bool:
        slots = caster.spell_slots()
        if slots.get(level, 0) <= 0:
            return False
        caster.slots_used[level] = caster.slots_used.get(level, 0) + 1
        return True

    def cast(self, caster, spell_key: str, target_names: list[str], rng: random.Random,
             slot_level: int | None = None, as_bonus: bool = False,
             quickened: bool = False) -> dict:
        sp = D.SPELLS.get(spell_key)
        if sp is None:
            return {"ok": False, "error": f"未知法术 {spell_key}"}
        innate = isinstance(caster, Character) and caster.can_use_innate(spell_key)
        if isinstance(caster, Character) and not innate:
            sc = D.CLASSES[caster.klass]["spellcasting"]
            if sp["kind"] not in ("counter",):
                if sc and sc["type"] == "pact":
                    slots = caster.spell_slots()
                    if not slots:
                        return {"ok": False, "error": "没有可用的魔契法术位（短休恢复）"}
                    slv = min(slots)
                    if sp["level"] > 0 and slv < sp["level"]:
                        return {"ok": False, "error": "魔契法术位环阶不足"}
                    self._consume_slot(caster, slv)
                    slot_level = slv
                else:
                    lvl = max(sp["level"], 1 if sp["level"] > 0 else 0)
                    if lvl > 0:
                        if not self._consume_slot(caster, lvl):
                            return {"ok": False, "error": f"没有可用的 {lvl} 环法术位"}
                        slot_level = lvl
            # 附赠动作施法规则：本回合已施放附赠动作法术 → 只能再施戏法
            if as_bonus or quickened or sp.get("bonus_action"):
                prev = self.cast_this_turn.get(caster.name)
                if prev == "bonus":
                    if sp["level"] > 0:
                        return {"ok": False,
                                "error": "本回合已用附赠动作施法，只能再施展戏法"}
                else:
                    self.cast_this_turn[caster.name] = "bonus"
            elif sp["level"] > 0 or sp["kind"] != "utility":
                if self.cast_this_turn.get(caster.name) is None:
                    self.cast_this_turn[caster.name] = "action"
        if innate:
            caster.innate_used.add(spell_key)
        targets = [self.find(t) for t in target_names]
        targets = [t for t in targets if t is not None]
        kind = sp["kind"]
        out: dict = {"ok": True, "spell": sp["name"], "targets": [t.name for t in targets]}

        if kind in ("utility", "light"):
            out["detail"] = f"{caster.name} 施展了 {sp['name']}。"
            self.log.append(out["detail"])
            return out

        if kind == "darkness":
            self.dark = True
            out["detail"] = f"{caster.name} 施展黑暗术：无黑暗视觉120尺者陷入黑暗劣势。"
            self.log.append(out["detail"])
            return out

        if sp.get("concentration") and isinstance(caster, Character):
            if caster.concentration:
                self.log.append(f"{caster.name} 放弃了 [{caster.concentration}] 的专注。")
            caster.concentration = sp["en"]

        if kind == "heal":
            n, s, m0 = parse_expr(sp["dmg"])
            extra = max(0, (slot_level or sp["level"]) - sp["level"]) * sp.get("per_level_extra", 0)
            lines, amount = [], 0
            for t in targets:
                r = roll(rng, f"{n + extra}d{s}")
                amt = r.total + m0 + caster.mod(caster.cast_ability())
                if caster.klass == "cleric":
                    amt += 2 + (slot_level or sp["level"])
                amt = t.heal(amt)
                amount += amt
                lines.append(f"{t.name} 回复 {amt} 点（HP {t.hp_cur}/{self._hp_max(t)}）")
            out["healed"] = amount
            out["detail"] = f"{caster.name} 施展 {sp['name']}：" + "；".join(lines)
            self.log.append(out["detail"])
            return out

        if kind == "buff":
            effect = sp["effect"]
            for t in targets or [caster]:
                if effect == "ac+5":
                    t.conditions.add(COND_SHIELDED)
                    self.add_timer(t, COND_SHIELDED, 1)
                elif effect == "attack+1d4":
                    t.conditions.add(COND_BLESSED)
                    t.blessing_d4 = 4
                elif effect == "mark+1d6":
                    # 印记加在施法者身上（对被印记目标的命中 +1d6）
                    t = caster
                    t.conditions.add(COND_MARKED)
                    if targets:
                        caster.marked_target = targets[0].name
                elif effect == "invisible":
                    t.conditions.add(COND_INVISIBLE)
                    self.add_timer(t, COND_INVISIBLE, 100)
                elif effect == "ac+2":
                    t.conditions.add("ac+2")
                    self.add_timer(t, "ac+2", 10)
                elif effect == "teleport":
                    self.do_disengage_quiet(caster)
                elif effect.startswith("weapon+"):
                    dice, dtype = effect.split("weapon+")[1].split("fire") if "fire" in effect \
                        else (effect.split("weapon+")[1].split("radiant") + ["radiant"])[:2]
                    caster.weapon_buff = {"dice": dice, "dtype": "fire" if "fire" in effect
                                          else "radiant"}
                elif effect == "flame_blade":
                    caster.weapon_buff = {"dice": "2d6", "dtype": "fire"}
                elif effect == "mirror_image":
                    t.mirror_images = 3
                    self.add_timer(t, "mirror_image", 10)
            out["detail"] = f"{caster.name} 施展 {sp['name']}（效果：{effect}）。"
            self.log.append(out["detail"])
            return out

        if kind == "cleanse":
            for t in targets or [caster]:
                for key in (COND_POISONED, COND_FRIGHTENED, COND_PRONE,
                            COND_PARALYZED, COND_STUNNED, COND_CHARMED, COND_BLINDED,
                            COND_RESTRAINED):
                    self._remove_condition(t, key)
            out["detail"] = f"{caster.name} 施展 {sp['name']}，清除了不良状态。"
            self.log.append(out["detail"])
            return out

        if kind == "cleanse_magical":
            for t in targets:
                if isinstance(t, Character) and t.concentration:
                    self.log.append(f"{t.name} 的 [{t.concentration}] 被解除魔法打断。")
                    self._break_concentration(t)
            out["detail"] = f"{caster.name} 施展 {sp['name']}，魔法效果被驱散。"
            self.log.append(out["detail"])
            return out

        if kind == "counter":
            out["detail"] = f"{caster.name} 准备好了反制法术（反应）。"
            self.log.append(out["detail"])
            return out

        if kind == "auto" and sp.get("count"):  # 魔法飞弹
            missiles = sp["count"] + max(0, (slot_level or sp["level"]) - sp["level"]) * sp.get("per_level_extra", 0)
            lines = []
            for i in range(missiles):
                t = targets[i % len(targets)] if targets else None
                if t is None or not (self._hp_max(t) > 0 and t.hp_cur > 0):
                    remaining = [x for x in targets if x.hp_cur > 0]
                    if not remaining:
                        break
                    t = remaining[0]
                d = roll(rng, "1d4+1").total
                info = self.apply_damage(t, d)
                lines.append(f"飞弹击中 {t.name}（{d} 点，HP {max(info['hp'], 0)}/{self._hp_max(t)}）")
            out["detail"] = f"{caster.name} 施展 {sp['name']}：" + "；".join(lines)
            self.log.append(out["detail"])
            return out

        if kind == "auto" and sp.get("effect") == "sleep_hp":  # 睡眠术
            pool = roll(rng, sp["dmg"]).total
            lines = [f"催眠之力共 {pool} 点。"]
            slept = []
            for t in sorted([t for t in targets if t.hp_cur > 0], key=lambda x: x.hp_cur):
                if isinstance(t, Character) and t.race in ELF_RACES:
                    lines.append(f"{t.name} 是精灵，魔法睡眠对其无效。")
                    continue
                if isinstance(t, Monster) and t.is_undead():
                    lines.append(f"{t.name} 是不死者，不会入睡。")
                    continue
                if pool >= t.hp_cur:
                    pool -= t.hp_cur
                    t.conditions.add(COND_SLEEP)
                    self.add_timer(t, COND_SLEEP, 10)
                    slept.append(t.name)
            out["detail"] = f"{caster.name} 施展 {sp['name']}：" + \
                ("、".join(slept) + " 沉入梦乡。" if slept else "无人入睡。") + \
                ("" if not slept else "") + (f"（剩余 {pool} 点）" if slept else "")
            self.log.append(out["detail"])
            return out

        if kind == "save_area":  # 妖火/缠绕/定身
            save_key = sp["save"]
            effect = sp.get("effect")
            dc = 8 + caster.prof_bonus() + caster.mod(caster.cast_ability())
            if caster.has_feature("heightened") and quickened:
                pass
            lines = []
            for t in targets:
                if isinstance(t, Character):
                    sv = t.save(rng, save_key, dc)
                    self._rec_save(t, sv, dc, f"{sp['name']}豁免")
                    ok, d = sv["success"], sv["detail"]
                else:
                    dr = roll(rng, "d20")
                    ok = dr.kept[0] + t.save_bonus(save_key) >= dc
                    self._rec_save(t, dr.kept[0], dc, f"{sp['name']}豁免", success=ok)
                    d = dr.describe()
                if ok:
                    lines.append(f"{t.name} 豁免成功（{d}）")
                    continue
                if effect == "outlined":
                    self.set_condition(t, COND_OUTLINED)
                    self.add_timer(t, COND_OUTLINED, 10)
                elif effect == "restrained":
                    self.set_condition(t, COND_RESTRAINED)
                    self.add_timer(t, COND_RESTRAINED, 10, save=save_key, dc=dc)
                elif effect == "paralyzed":
                    if isinstance(t, Character) and t.race in ELF_RACES:
                        lines.append(f"{t.name} 是精灵，免疫麻痹。")
                        continue
                    if isinstance(t, Monster) and not t.is_humanoid():
                        lines.append(f"{t.name} 不是类人生物，定身无效。")
                        continue
                    self.set_condition(t, COND_PARALYZED)
                    self.add_timer(t, COND_PARALYZED, 10, save=save_key, dc=dc)
                lines.append(f"{t.name} 受到影响（{d}）")
            out["detail"] = f"{caster.name} 施展 {sp['name']}：" + "；".join(lines)
            self.log.append(out["detail"])
            return out

        # ---- 伤害类法术
        n, s, m0 = parse_expr(sp["dmg"])
        save_key = sp.get("save")
        lvl = sp["level"]
        extra_dice = 0
        if sp.get("per_level_extra_dmg"):
            extra_dice = max(0, (slot_level or lvl) - lvl) * sp["per_level_extra_dmg"]
        if lvl == 0:
            n = n * 2 if caster.level >= 5 else n   # 戏法 5 级升级

        dc = 8 + caster.prof_bonus() + caster.mod(caster.cast_ability())

        if sp.get("rays") or sp.get("beams") == "eldritch":  # 多射线/魔能爆光束
            if sp.get("beams") == "eldritch":
                rays = 1 + (1 if caster.level >= 5 else 0) + (1 if caster.level >= 11 else 0)
            else:
                rays = sp["rays"] + max(0, (slot_level or lvl) - lvl) * sp.get("per_level_extra_rays", 0)
            lines = []
            for i in range(rays):
                t = targets[i % len(targets)] if targets else None
                if t is None or t.hp_cur <= 0:
                    t = next((x for x in targets if x.hp_cur > 0), None)
                    if t is None:
                        break
                adv, _notes = self._advantage_for(caster, t, ranged=True)
                die = self._attack_d20(caster, rng, adv)
                bonus = caster.prof_bonus() + caster.mod(caster.cast_ability())
                nat = die.kept[0]
                self._rec_attack(caster, die, adv, nat + bonus, t,
                                 nat == 20 or (nat != 1 and nat + bonus >= t.ac()),
                                 nat == 20, label=f"{sp['name']}射线")
                if nat != 20 and (nat == 1 or nat + bonus < t.ac()):
                    lines.append(f"{t.name} 闪开了射线（{die.describe()}+{bonus}）")
                    continue
                r = roll(rng, f"{n * (2 if nat == 20 else 1)}d{s}" + (f"+{m0}" if m0 else ""))
                dmg = r.total
                if caster.has_feature("agonizing_blast") and sp.get("beams") == "eldritch":
                    dmg += caster.mod("cha")
                dmg, _res = self.apply_resistance(t, sp["dtype"], dmg)
                info = self.apply_damage(t, dmg)
                lines.append(f"射线命中 {t.name}{'（暴击）' if nat == 20 else ''}，"
                             f"{info.get('damage', dmg)} 点伤害（HP {max(info['hp'], 0)}）")
            out["detail"] = f"{caster.name} 施展 {sp['name']}：" + "；".join(lines)
            self.log.append(out["detail"])
            return out

        lines = []
        for t in targets:
            if save_key:
                if isinstance(t, Character):
                    sv = t.save(rng, save_key, dc)
                    self._rec_save(t, sv, dc, f"{sp['name']}豁免")
                    ok, d = sv["success"], sv["detail"]
                else:
                    dr = roll(rng, "d20")
                    ok = dr.kept[0] + t.save_bonus(save_key) >= dc
                    self._rec_save(t, dr.kept[0], dc, f"{sp['name']}豁免", success=ok)
                    d = dr.describe()
                r = roll(rng, f"{n + extra_dice}d{s}" + (f"+{m0}" if m0 else ""))
                dmg = r.total
                if ok and sp.get("half"):
                    dmg //= 2
                    eff = "豁免成功，减半"
                elif ok:
                    dmg = 0
                    eff = "完全豁免"
                else:
                    eff = "豁免失败"
                if caster.has_feature("sculpt_spells") and sp.get("aoe") \
                        and isinstance(t, Character) and t.side == caster.side:
                    dmg = 0
                    eff = "塑法：队友无伤"
                dmg, _res = self.apply_resistance(t, sp["dtype"], dmg)
                info = self.apply_damage(t, dmg)
                dtype_cn = DTYPE_CN.get(sp["dtype"], sp["dtype"])
                lines.append(f"{t.name} {eff}（{d}），{info.get('damage', dmg)} 点{dtype_cn}伤害"
                             f"（HP {max(info['hp'], 0)}/{self._hp_max(t)}）")
                if sp.get("push") and not ok:
                    self.do_disengage_quiet(t)
                    lines[-1] += "，被推离原位"
            else:  # 法术攻击（单射线）
                adv, _notes = self._advantage_for(caster, t, ranged=True)
                die = self._attack_d20(caster, rng, adv)
                bonus = caster.prof_bonus() + caster.mod(caster.cast_ability())
                nat = die.kept[0]
                self._rec_attack(caster, die, adv, nat + bonus, t,
                                 nat == 20 or (nat != 1 and nat + bonus >= t.ac()),
                                 nat == 20, label=f"{sp['name']}射线")
                if nat != 20 and (nat == 1 or nat + bonus < t.ac()):
                    lines.append(f"{t.name} 闪开了射线（{die.describe()}+{bonus}）")
                    continue
                r = roll(rng, f"{n * (2 if nat == 20 else 1)}d{s}" + (f"+{m0}" if m0 else ""))
                dmg = r.total
                dmg, _res = self.apply_resistance(t, sp["dtype"], dmg)
                info = self.apply_damage(t, dmg)
                lines.append(f"射线命中 {t.name}{'（暴击）' if nat == 20 else ''}，"
                             f"{info.get('damage', dmg)} 点伤害（HP {max(info['hp'], 0)}/{self._hp_max(t)}）")
                if sp.get("guides_next"):
                    self.set_condition(t, COND_GUIDED)
                    self.add_timer(t, COND_GUIDED, 1)
                    lines[-1] += "（下一次对它的攻击有优势）"
        out["detail"] = f"{caster.name} 施展 {sp['name']}：" + "；".join(lines)
        self.log.append(out["detail"])
        return out

    # ------------------------------------------------------------ 状态板
    def status_lines(self) -> list[str]:
        lines = [f"—— 第 {max(self.round, 1)} 轮 ——"]
        for c in self.order:
            mark = "◆" if c.side == "party" else "◇"
            conds = "/".join(_cond_names(getattr(c, "conditions", set())))
            tag = ""
            if c.hp_cur == 0:
                tag = "（已被击败）" if isinstance(c, Monster) else "（昏迷 0HP）"
            if "dead" in getattr(c, "conditions", set()):
                tag = "（死亡）"
            lines.append(f"{mark} {c.name}: HP {self._hp_cur(c)}/{self._hp_max(c)},"
                         f" AC {c.ac()}" + (f" [{conds}]" if conds else "") + tag)
        return lines

    def brief(self) -> dict:
        return {
            "round": max(self.round, 1),
            "current": self.current().name if self.started and self.order else None,
            "party": [{"name": c.name, "hp": f"{self._hp_cur(c)}/{self._hp_max(c)}",
                       "ac": c.ac()} for c in self.party()],
            "enemies": [c.to_dict() for c in self.enemies()],
            "recent_log": self.log[-6:],
        }

    def defeated_xp(self) -> int:
        return sum(m.xp for m in self.enemies() if m.hp_cur == 0)


class CounterspellInterrupted(Exception):
    """反制法术成功打断怪物施法——由攻击调用方捕获。"""


def _cond_names(conds: set) -> list[str]:
    m = {COND_POISONED: "中毒", COND_FRIGHTENED: "恐惧", COND_PRONE: "倒地",
         COND_UNCONSCIOUS: "昏迷", COND_HIDDEN: "潜行", COND_INVISIBLE: "隐形",
         COND_SLEEP: "沉睡", COND_BLESSED: "受祝福", COND_MARKED: "印记中",
         COND_SHIELDED: "护盾术", "dead": "死亡", COND_OUTLINED: "妖火标记",
         COND_RESTRAINED: "束缚", COND_PARALYZED: "麻痹", COND_STUNNED: "震慑",
         COND_CHARMED: "魅惑", COND_BLINDED: "目盲", COND_GRAPPLED: "被擒抱",
         COND_RAGING: "狂暴", COND_GUIDED: "引导标记", "ac+2": "信仰护盾",
         "mirror_image": "镜影", "burning": "燃烧"}
    return [m.get(c, c) for c in conds]


# 简单怪物 AI：优先近战最弱目标，纠缠时优先远程
def monster_choose_action(enc: Encounter, mon: Monster) -> dict:
    foes = enc.active_party()
    if not foes:
        return {"action": "none"}
    rng = enc.rng or random.Random()
    def pick(seq):
        return min(seq, key=lambda f: f.hp_cur + rng.randint(0, 6))
    traits = "".join(mon.traits)
    is_caster = "施法者" in traits
    has_ranged = any(D.WEAPONS.get(a["weapon"], {}).get("ranged")
                     or a["weapon"].startswith("spell:") for a in mon.attacks)
    engaged = any(f.name in enc.engagement.get(mon.name, set()) for f in foes)
    # 附赠动作：借机潜袭（被纠缠时撤离或躲藏）
    if enc.has_bonus(mon) and "借机潜袭" in traits and engaged and mon.hp_cur < mon.hp_max:
        return {"action": "bonus_disengage"}
    # 施法者：半血以下隔轮使用爆燃类法术
    if is_caster and mon.hp_cur <= mon.hp_max // 2 and enc.round % 2 == 0:
        save_atk = next((a for a in mon.attacks if "save_dc" in a), None)
        if save_atk:
            return {"action": "attack", "weapon": save_atk["weapon"],
                    "target": pick(foes).name}
    # 灰矮人扩大术（每场一次）
    if "扩大术" in traits and not mon.enlarged and enc.round >= 2:
        mon.enlarged = True
        mon.enlarge_used = True
    if has_ranged and not engaged:
        ranged_atk = next((a for a in mon.attacks
                           if D.WEAPONS.get(a["weapon"], {}).get("ranged")
                           or a["weapon"].startswith("spell:")), None)
        return {"action": "attack", "weapon": ranged_atk["weapon"], "target": pick(foes).name}
    melee_atk = next((a for a in mon.attacks
                      if not D.WEAPONS.get(a["weapon"], {}).get("ranged")
                      and not a["weapon"].startswith("spell:")), mon.attacks[0])
    return {"action": "attack", "weapon": melee_atk["weapon"], "target": pick(foes).name}
