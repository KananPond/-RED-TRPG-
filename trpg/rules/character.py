"""《赛博朋克 RED》角色模型。

一个类覆盖玩家角色、AI 队友与 NPC/敌人（``kind`` 区分），因为战斗引擎对三者
使用完全相同的结算路径（属性 + 技能 + 1d10 对抗难度值、护甲 SP 与磨损、
伤势等级、生存检定、致命伤）。

角色卡上有两组东西：**属性**（十项，1-8）与**技能**（41 项，0-10 级）。
技能检定的加值 = 挂钩属性 + 技能等级。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import data as D
from . import dice as dice_mod

# ---------------------------------------------------------------- 状态关键字
COND_CONTROLLED = "controlled"      # 被擒抱/控制：所有动作 -2
COND_PRONE = "prone"                # 倒地：起身前不能执行移动动作
COND_UNCONSCIOUS = "unconscious"    # 昏迷
COND_DEAD = "dead"
COND_STABILIZED = "stabilized"      # 已被稳定：每完整休息日恢复 PW
COND_STABILIZING = "crit_stabilized"  # 急救固定（当天消除致命伤效果）

#: 命中部位（瞄准射击用；默认躯干）
LOCATIONS = ("torso", "head", "arm", "leg")
LOCATION_NAMES = {"torso": "躯干", "head": "头部", "arm": "手臂", "leg": "腿部"}
#: 瞄准射击的难度值修正（主规则书口径）
AIMED_SHOT_PENALTY = -8


@dataclass
class Character:
    """角色（PC / AI 队友 / NPC）。"""

    name: str
    kind: str = "pc"                       # pc | companion | npc
    role: str = "solo"                     # 职业 key（见 D.ROLES）
    role_rank: int = 4                     # 职业特殊能力等级（1-10）
    role_ability: str = ""                 # 职业特殊能力名（预设角色带出）

    stats: dict = field(default_factory=dict)         # 十项属性
    skill_levels: dict = field(default_factory=dict)  # 技能 → 等级

    hp_cur: int = 0
    hp_max: int = 0
    luck_cur: int = 0                      # 当前幸运池（每轮开始恢复满）
    humanity_cur: int = 0
    humanity_max: int = 0
    reputation: int = D.REPUTATION_START
    eurobucks: int = 0
    ip: int = 0                            # 未花费的改进点

    armor_key: str | None = None           # 护甲 key（见 D.ARMORS）
    armor_label: str = ""                  # 护甲显示名（NPC 数据块可能只给名字）
    armor_sp: dict = field(default_factory=dict)   # 部位 → 当前 SP（会磨损）
    cyberware: list = field(default_factory=list)  # 义体 key
    weapons: list = field(default_factory=list)    # 携带武器 key
    gear: dict = field(default_factory=dict)       # 装备 key → 数量
    ammo: dict = field(default_factory=dict)       # 武器 key → 剩余弹药

    conditions: set = field(default_factory=set)
    timers: dict = field(default_factory=dict)     # 状态 → {left, save, dv}
    crit_injuries: list = field(default_factory=list)  # 致命伤 [{roll,name,effects,...}]
    death_save_mod: int = 0                # 生存检定累积修正
    lifepath: dict = field(default_factory=dict)  # 人生之路（人生轨迹）表结果

    # 叙事用（队友/NPC）
    persona: str = ""
    voice: str = ""
    desc: str = ""
    handle: str = ""                       # 街头绰号
    source_key: str = ""                   # 来源数据键（NPC 数据块 / 预设角色）

    # 战斗记账（由 Encounter 维护）
    action_used: bool = False
    bonus_used: bool = False
    reaction_used: bool = False
    turn_damage_taken: int = 0             # 本回合承受的伤害（肋骨骨折等用）

    # ------------------------------------------------------------ 基本查询
    @property
    def is_npc(self) -> bool:
        return self.kind == "npc"

    @property
    def is_player(self) -> bool:
        return self.kind == "pc"

    def stat(self, key: str) -> int:
        return int(self.stats.get(key, 0))

    def skill(self, key: str) -> int:
        """技能等级（未掌握为 0）。"""
        return int(self.skill_levels.get(key, 0))

    def skill_total(self, key: str) -> int:
        """技能检定加值 = 属性 + 技能等级（未掌握时只靠属性）。"""
        info = D.SKILLS.get(key)
        if info is None:
            return 0
        return self.stat(info["stat"]) + self.skill(key)

    def skill_name(self, key: str) -> str:
        info = D.SKILLS.get(key) or {}
        return info.get("name", key)

    @property
    def role_name(self) -> str:
        return (D.ROLES.get(self.role) or {}).get("name", self.role)

    def display(self) -> str:
        return self.handle or self.name

    # ------------------------------------------------------------ 伤势
    @property
    def seriously_wounded_threshold(self) -> int:
        """重伤阈值：PW 上限的一半（向上取整）。"""
        return max(1, math.ceil(self.hp_max / 2))

    def wound_state(self) -> str:
        """当前伤势等级 key（healthy / light / serious / mortal）。"""
        if COND_DEAD in self.conditions:
            return "mortal"
        if self.hp_cur < 1:
            return "mortal"
        if self.hp_cur <= self.seriously_wounded_threshold:
            return "serious"
        if self.hp_cur < self.hp_max:
            return "light"
        return "healthy"

    def wound_name(self) -> str:
        return D.WOUND_STATES[self.wound_state()]["name"]

    @property
    def is_downed(self) -> bool:
        """昏迷或濒死（PW < 1）。"""
        return self.hp_cur < 1 or COND_UNCONSCIOUS in self.conditions

    @property
    def is_dead(self) -> bool:
        return COND_DEAD in self.conditions

    def crit_penalty(self) -> int:
        """致命伤带来的检定修正。"""
        pen = 0
        for inj in self.crit_injuries:
            if inj.get("stabilized") and not inj.get("permanent"):
                continue
            for eff in inj.get("effects", []):
                if eff["kind"] == "hand_minus":
                    pen -= int(eff.get("value", 0))
        return pen

    def check_penalty(self) -> int:
        """所有检定的修正：伤势 + 致命伤 + 被控制。

        义体「赛博痛觉编辑器」可无视重伤的 -2。
        """
        state = self.wound_state()
        pen = int(D.WOUND_STATES[state]["check_penalty"])
        if pen == -2 and self.has_cyber_effect("ignore_serious_penalty"):
            pen = 0
        pen += self.crit_penalty()
        if COND_CONTROLLED in self.conditions:
            pen += D.GRAPPLE_PENALTY
        return pen

    def melee_penalty(self) -> int:
        """近战武器攻击的额外修正（肌肉拉伤 -2）。"""
        pen = 0
        for inj in self.crit_injuries:
            if inj.get("stabilized") and not inj.get("permanent"):
                continue
            for eff in inj.get("effects", []):
                if eff["kind"] == "melee_minus":
                    pen -= int(eff.get("value", 0))
        return pen

    def move_value(self) -> int:
        """本回合可移动的米数（= 机动 × 2 + 伤势修正，最低 1 米）。"""
        base = self.stat("move") * D.MOVE_FACTOR
        pen = int(D.WOUND_STATES[self.wound_state()]["move_penalty"])
        for inj in self.crit_injuries:
            if inj.get("stabilized") and not inj.get("permanent"):
                continue
            for eff in inj.get("effects", []):
                if eff["kind"] == "move_minus":
                    pen -= int(eff.get("value", 0))
        return max(1, base + pen)

    def can_evade_bullets(self) -> bool:
        """REF 8+ 才能用闪避技能躲子弹；腿部切断会失去该能力。"""
        if self.stat("ref") < D.EVASION_BULLET_REF:
            return False
        for inj in self.crit_injuries:
            if inj.get("stabilized") and not inj.get("permanent"):
                continue
            for eff in inj.get("effects", []):
                if eff["kind"] == "no_evade":
                    return False
        return True

    # ------------------------------------------------------------ 护甲
    def armor_at(self, location: str = "torso") -> int:
        """指定部位的当前护甲值（取护甲与皮下护甲中的高者）。"""
        worn = int(self.armor_sp.get(location, 0))
        sub = self.cyber_subdermal_sp()
        return max(worn, sub)

    def cyber_subdermal_sp(self) -> int:
        for cw in self.cyberware:
            for eff in (D.CYBERWARE.get(cw) or {}).get("effects", []):
                if eff["kind"] == "subdermal_sp":
                    return int(eff.get("value", 0))
        return 0

    def ablate_armor(self, location: str = "torso", amount: int = 1) -> int:
        """护甲磨损：挡下伤害并损失 PW 后 -1（最低 0）。返回磨损后的值。"""
        if location not in self.armor_sp:
            return 0
        self.armor_sp[location] = max(0, int(self.armor_sp[location]) - amount)
        return self.armor_sp[location]

    def armor_name(self) -> str:
        """护甲显示名：优先 NPC 数据块给的名字，其次按 key 查护甲表。"""
        if self.armor_label:
            return self.armor_label
        return (D.ARMORS.get(self.armor_key) or {}).get("name", "无护甲")

    # ------------------------------------------------------------ 义体
    def has_cyberware(self, key: str) -> bool:
        return key in self.cyberware

    def has_cyber_effect(self, kind: str) -> bool:
        for cw in self.cyberware:
            for eff in (D.CYBERWARE.get(cw) or {}).get("effects", []):
                if eff["kind"] == kind:
                    return True
        return False

    def cyber_check_bonus(self, skill: str) -> int:
        bonus = 0
        for cw in self.cyberware:
            for eff in (D.CYBERWARE.get(cw) or {}).get("effects", []):
                if eff["kind"] == "check_bonus" and eff.get("skill") == skill:
                    bonus += int(eff.get("value", 0))
        return bonus

    def install_cyberware(self, key: str, charge: bool = True) -> int:
        """植入义体：扣除人性；共情随人性下降。返回实际扣除的人性点数。"""
        info = D.CYBERWARE.get(key)
        if info is None:
            raise ValueError(f"未知义体: {key}")
        if key in self.cyberware:
            return 0
        self.cyberware.append(key)
        loss = int(info.get("humanity", 0))
        if charge:
            self.humanity_cur = max(0, self.humanity_cur - loss)
            self.stats["emp"] = max(
                0, int(math.ceil(self.humanity_cur / D.HUMANITY_PER_EMP)))
            for eff in info.get("effects", []):
                if eff["kind"] == "stat_bonus":
                    cap = int(eff.get("cap", 99))
                    self.stats[eff["stat"]] = min(
                        cap, self.stat(eff["stat"]) + int(eff.get("value", 0)))
        return loss

    def is_cyberpsycho(self) -> bool:
        return self.humanity_cur <= D.CYBERPSYCHOSIS_HUMANITY

    # ------------------------------------------------------------ 幸运
    def refresh_luck(self) -> None:
        """每轮开始时幸运池恢复满。"""
        self.luck_cur = self.stat("luck")

    def spend_luck(self, points: int) -> int:
        """投入幸运点（掷骰前声明）。返回实际投入的点数。"""
        spend = max(0, min(int(points), self.luck_cur))
        self.luck_cur -= spend
        return spend

    # ------------------------------------------------------------ 检定
    def check(self, rng: random.Random, skill: str, dv: int | None = None,
              luck: int = 0, extra_time: bool = False, complementary: bool = False,
              penalty: int = 0) -> dice_mod.DiceResult:
        """技能检定：属性 + 技能 + 1d10（+ 幸运 + 辅助 + 加时 - 伤势）。

        ``luck`` 为投入的幸运点（从幸运池扣除，掷骰前声明）。
        """
        info = D.SKILLS.get(skill)
        if info is None:
            raise ValueError(f"未知技能: {skill}")
        pen = self.check_penalty() + penalty
        mod = self.skill_total(skill) + pen
        labels = [f"{D.STATS[info['stat']]['name']}{self.stat(info['stat']):+d}",
                  f"{info['name']}{self.skill(skill):+d}"]
        if pen:
            labels.append(f"伤势/状态{pen:+d}")
        bonus = self.cyber_check_bonus(skill)
        if extra_time:
            bonus += D.CHECK_EXTRA_TIME_BONUS
        if complementary:
            bonus += D.CHECK_COMPLEMENTARY_BONUS
        if self.has_role_bonus(skill):
            bonus += 2
            labels.append("魅力效应+2")
        spent = self.spend_luck(luck)
        return dice_mod.check_roll(rng, mod, dv=dv, luck=spent, bonus=bonus,
                                   labels=labels)

    def has_role_bonus(self, skill: str) -> bool:
        """摇滚客的「魅力效应」：对粉丝的基于魅力/共情的检定 +2。"""
        info = D.SKILLS.get(skill) or {}
        return (self.role == "rockerboy" and info.get("stat") in ("cool", "emp"))

    def opposed_check(self, rng: random.Random, skill: str, defender: "Character",
                      defender_skill: str, luck: int = 0,
                      defender_luck: int = 0, penalty: int = 0
                      ) -> tuple[dice_mod.DiceResult, dice_mod.DiceResult, bool]:
        """对抗检定（说服 vs 读心、擒抱 vs 闪避等）。平手时防守方获胜。

        ``penalty`` 是本次攻击额外的修正（例如近战致命伤的「肌肉拉伤 -2」），
        只作用于攻击方。
        """
        atk_mod = self.skill_total(skill) + self.check_penalty() + int(penalty)
        def_mod = defender.skill_total(defender_skill) + defender.check_penalty()
        spent = self.spend_luck(luck)
        defender.spend_luck(defender_luck)
        return dice_mod.opposed_roll(rng, atk_mod, def_mod, luck=spent)

    def skill_attack_mod(self, skill: str) -> int:
        """攻击加值 = 属性 + 技能 + 状态修正（白刃战另计肌肉拉伤）。"""
        return self.skill_total(skill) + self.check_penalty()

    def initiative_bonus(self) -> int:
        """职业特殊能力对先攻的加值：独行侠的「战斗直觉」+4。"""
        if self.role == "solo":
            return 4
        return 0

    # ------------------------------------------------------------ 伤害
    def take_damage(self, amount: int, location: str = "torso",
                    ignore_armor: bool = False, ap: bool = False) -> dict:
        """承受伤害：先减护甲 SP，再减 PW；护甲磨损 1 点。

        返回 ``{"hp": 实际扣血, "sp": 被护甲挡下, "sp_left": 磨损后 SP,
        "ablate": 是否磨损, "downed": 是否被打到 0 以下}``。
        """
        amount = max(0, int(amount))
        sp = 0 if ignore_armor else self.armor_at(location)
        if ap:
            sp = sp // 2
        blocked = min(sp, amount)
        hp_loss = amount - blocked
        sp_left = self.armor_at(location)
        ablated = False
        if hp_loss > 0 and not ignore_armor and location in self.armor_sp:
            sp_left = self.ablate_armor(location, 1)
            ablated = True
        was_above = self.hp_cur > 0
        self.hp_cur = max(-self.hp_max, self.hp_cur - hp_loss)
        self.turn_damage_taken += hp_loss
        if self.hp_cur < 1:
            self.conditions.add(COND_UNCONSCIOUS)
        return {"hp": hp_loss, "sp": blocked, "sp_left": sp_left,
                "ablate": ablated, "downed": was_above and self.hp_cur < 1,
                "raw": amount, "location": location}

    def apply_crit_injury(self, roll: int) -> dict:
        """施加一处致命伤（roll 为 2d6 结果）。"""
        info = D.CRIT_INJURIES.get(int(roll)) or D.CRIT_INJURIES[7]
        inj = {"roll": int(roll), "name": info["name"], "text": info["text"],
               "effects": [dict(e) for e in info["effects"]], "stabilized": False,
               "permanent": False}
        self.crit_injuries.append(inj)
        return inj

    def death_save_dv(self) -> int:
        """生存检定门槛：10 + 累积修正 + 致命伤带来的基础难度提升。"""
        dv = 10 + self.death_save_mod
        for inj in self.crit_injuries:
            if inj.get("stabilized") and not inj.get("permanent"):
                continue
            for eff in inj.get("effects", []):
                if eff["kind"] == "death_save_plus":
                    dv += int(eff.get("value", 0))
        return dv

    def death_save(self, rng: random.Random) -> dict:
        """生存检定：掷 1d10，**低于**门槛即活下来；掷出 10 自动失败。

        每次生存检定后门槛 +1（越来越难），直到被稳定。任何一次失败即死亡。
        """
        dv = self.death_save_dv()
        res = dice_mod.check_roll(rng, 0, dv=None)
        face = res.rolls[0]
        auto_fail = face == D.DEATH_SAVE["auto_fail_face"]
        success = (not auto_fail) and face < dv
        if not success:
            self.conditions.add(COND_DEAD)
        self.death_save_mod += int(D.DEATH_SAVE["cumulative"])
        res.dv = dv
        res.success = success
        return {"ok": success, "face": face, "dv": dv, "roll": res, "dead": not success}

    def stabilize(self) -> None:
        """被稳定：PW 回到 1，生存检定修正清零。"""
        self.conditions.discard(COND_DEAD)
        self.hp_cur = max(1, int(D.DEATH_SAVE["stabilized_hp"]))
        self.death_save_mod = 0
        self.conditions.discard(COND_UNCONSCIOUS)
        self.conditions.add(COND_STABILIZED)

    def stabilize_dv(self) -> int:
        """稳定该角色所需的难度值（按当前伤势等级）。"""
        return D.STABILIZE_DV.get(self.wound_state(), 13)

    def revive(self, hp: int = 1) -> None:
        """场景切换时的照料：倒地者恢复少量 PW 归队。"""
        if self.is_dead:
            return
        if self.hp_cur < 1:
            self.hp_cur = max(1, int(hp))
        self.conditions.discard(COND_UNCONSCIOUS)
        self.conditions.discard(COND_CONTROLLED)
        self.conditions.discard(COND_PRONE)

    def heal(self, amount: int) -> int:
        before = self.hp_cur
        self.hp_cur = min(self.hp_max, self.hp_cur + max(0, int(amount)))
        if self.hp_cur > 0:
            self.conditions.discard(COND_UNCONSCIOUS)
        return self.hp_cur - before

    def rest_day(self) -> int:
        """完整休息一天：恢复等于体格的 PW。

        手册口径：**濒死者**必须先被稳定才能开始自然恢复；PW ≥ 1 的伤员
        （轻伤/重伤）睡一整天同样恢复体格的 PW。PW 回满时顺带清掉致命伤并修复护甲。
        """
        if self.hp_cur < 1 and COND_STABILIZED not in self.conditions:
            return 0
        healed = self.heal(self.stat(D.REST_HEAL_PER_DAY_STAT))
        if self.hp_cur >= self.hp_max:
            self.conditions.discard(COND_STABILIZED)
            self.crit_injuries = []
            self.repair_armor()
        return healed

    # ------------------------------------------------------------ 致命伤治疗
    def crit_first_aid_dv(self, index: int) -> int | None:
        """该致命伤能否用急救固定，以及难度值（None = 只能手术）。"""
        if not (0 <= index < len(self.crit_injuries)):
            return None
        inj = self.crit_injuries[index]
        info = D.CRIT_INJURIES.get(inj.get("roll", 7)) or {}
        return info.get("first_aid_stabilize_dv")

    def first_aid_fix(self, index: int) -> dict | None:
        """急救固定：当天消除该致命伤效果（可对自己使用，耗时一分钟）。

        同一天对同一处致命伤只能固定一次；已经固定过的返回 None。
        """
        if not (0 <= index < len(self.crit_injuries)):
            return None
        inj = self.crit_injuries[index]
        if inj.get("stabilized"):
            return None
        inj["stabilized"] = True
        return inj

    def fixable_crit_indices(self) -> list[int]:
        """还能急救固定的致命伤下标（未固定、且该伤允许急救固定）。"""
        out = []
        for i, inj in enumerate(self.crit_injuries):
            if inj.get("stabilized"):
                continue
            info = D.CRIT_INJURIES.get(inj.get("roll", 7)) or {}
            if info.get("first_aid_stabilize_dv") is not None:
                out.append(i)
        return out

    def treat_crit_injury(self, index: int) -> dict | None:
        """治疗：永久消除该致命伤（每次尝试需四小时，无法治疗自己）。"""
        if not (0 <= index < len(self.crit_injuries)):
            return None
        inj = self.crit_injuries.pop(index)
        inj["permanent"] = True
        return inj

    def crit_treatment_skill(self, roll: int) -> dict | None:
        """该致命伤对应的治疗技能与难度值。"""
        info = D.CRIT_INJURIES.get(int(roll))
        return (info or {}).get("treatment")

    def active_crit_effects(self) -> list:
        """仍在生效的致命伤效果。"""
        out = []
        for inj in self.crit_injuries:
            if inj.get("stabilized") and not inj.get("permanent"):
                continue
            out.extend(inj.get("effects", []))
        return out

    def has_crit_effect(self, kind: str) -> bool:
        return any(e["kind"] == kind for e in self.active_crit_effects())

    # ------------------------------------------------------------ 成长（改进点）
    def ip_cost_skill(self, skill: str, new_level: int | None = None) -> int:
        """把某技能提升一级所需改进点 = 20 × 新等级（×2 技能翻倍）。"""
        cur = self.skill(skill)
        target = cur + 1 if new_level is None else int(new_level)
        cost = D.IP_SKILL_BASE * target
        if (D.SKILLS.get(skill) or {}).get("x2"):
            cost *= 2
        return cost

    def ip_cost_stat(self, new_value: int) -> int:
        return D.IP_STAT_BASE * int(new_value)

    def ip_cost_role(self) -> int:
        return D.IP_ROLE_BASE * (self.role_rank + 1)

    def raise_skill(self, skill: str) -> tuple[bool, str]:
        if skill not in D.SKILLS:
            return False, f"未知技能 {skill}"
        if self.skill(skill) >= D.SKILL_MAX:
            return False, f"{self.skill_name(skill)} 已达上限 {D.SKILL_MAX}"
        cost = self.ip_cost_skill(skill)
        if self.ip < cost:
            return False, f"改进点不足（需要 {cost}，现有 {self.ip}）"
        self.ip -= cost
        self.skill_levels[skill] = self.skill(skill) + 1
        return True, f"{self.skill_name(skill)} 提升到 {self.skill(skill)} 级（-{cost} IP）"

    def raise_stat(self, stat_key: str) -> tuple[bool, str]:
        if stat_key not in D.STATS:
            return False, f"未知属性 {stat_key}"
        cur = self.stat(stat_key)
        if cur >= D.STAT_MAX + 4:
            return False, "属性已达上限"
        cost = self.ip_cost_stat(cur + 1)
        if self.ip < cost:
            return False, f"改进点不足（需要 {cost}，现有 {self.ip}）"
        self.ip -= cost
        self.stats[stat_key] = cur + 1
        if stat_key == "luck":
            self.refresh_luck()
        if stat_key == "emp":
            self.humanity_max = self.stat("emp") * D.HUMANITY_PER_EMP
        if stat_key == "body":
            bonus = self.hp_max - self.hp_for(cur)
            self.hp_max = self.hp_for(cur + 1)
            self.hp_cur = min(self.hp_max, self.hp_cur + max(0, bonus))
        return True, f"{D.STATS[stat_key]['name']} 提升到 {self.stat(stat_key)}（-{cost} IP）"

    def raise_role(self) -> tuple[bool, str]:
        if self.role_rank >= D.ROLE_ABILITY_MAX:
            return False, "职业特殊能力已满级"
        cost = self.ip_cost_role()
        if self.ip < cost:
            return False, f"改进点不足（需要 {cost}，现有 {self.ip}）"
        self.ip -= cost
        self.role_rank += 1
        return True, f"职业特殊能力提升到 {self.role_rank} 级（-{cost} IP）"

    def gain_ip(self, amount: int) -> int:
        self.ip += max(0, int(amount))
        return self.ip

    # ------------------------------------------------------------ 装备
    def give(self, item: str, count: int = 1) -> None:
        """获得物品：武器/护甲/义体走各自表，其余进背包。"""
        if item in D.WEAPONS:
            if item not in self.weapons:
                self.weapons.append(item)
        elif item in D.ARMORS:
            self.equip_armor(item)
        elif item in D.CYBERWARE:
            self.install_cyberware(item)
        else:
            self.gear[item] = int(self.gear.get(item, 0)) + int(count)

    def equip_armor(self, key: str) -> None:
        info = D.ARMORS.get(key)
        if info is None:
            return
        self.armor_key = key
        self.armor_sp = {loc: int(info["sp"]) for loc in LOCATIONS}

    def repair_armor(self) -> None:
        if self.armor_key:
            self.equip_armor(self.armor_key)

    def has_item(self, key: str) -> bool:
        return (key in self.weapons or key == self.armor_key
                or int(self.gear.get(key, 0)) > 0 or key in self.cyberware)

    def use_item(self, key: str, count: int = 1) -> bool:
        have = int(self.gear.get(key, 0))
        if have < count:
            return False
        left = have - count
        if left:
            self.gear[key] = left
        else:
            self.gear.pop(key, None)
        return True

    def best_weapon(self, kind: str | None = None) -> str | None:
        """身上该类武器中伤害最高者（默认近战；``kind="ranged"`` 取枪械）。"""
        order = D.MELEE_ORDER if kind != "ranged" else D.RANGED_ORDER
        for key in order:
            if key in self.weapons:
                return key
        return None

    def weapon_damage(self, key: str) -> str:
        """该角色使用某武器的伤害骰（徒手按体格取 1d6/2d6）。"""
        info = D.WEAPONS.get(key) or {}
        if key == "brawling":
            return "1d6"
        return info.get("dmg", "1d6")

    # ------------------------------------------------------------ 展示
    def stat_line(self) -> str:
        return " ".join(f"{D.STATS[k]['name']}{self.stat(k)}" for k in D.STAT_ORDER)

    def lifepath_lines(self) -> list[str]:
        """人生之路的逐条摘要（表名：文本）。"""
        return [f"{k}：{' ｜ '.join(v.get('text', [])) if isinstance(v, dict) else v}"
                for k, v in self.lifepath.items()]

    def brief(self) -> str:
        """给 DM/前端的一行状态摘要。"""
        bits = [f"{self.name}（{self.role_name}）",
                f"PW {self.hp_cur}/{self.hp_max}",
                f"重伤阈值 {self.seriously_wounded_threshold}",
                f"伤势 {self.wound_name()}",
                f"护甲 {self.armor_name()} SP {self.armor_at('torso')}",
                f"幸运 {self.luck_cur}/{self.stat('luck')}",
                f"人性 {self.humanity_cur}/{self.humanity_max}"]
        if self.crit_injuries:
            bits.append("致命伤：" + "、".join(i["name"] for i in self.crit_injuries))
        if self.conditions:
            bits.append("状态：" + "、".join(sorted(self.conditions)))
        return " | ".join(bits)

    def skill_lines(self) -> list[str]:
        out = []
        for key in D.SKILL_ORDER:
            if self.skill(key) > 0:
                info = D.SKILLS[key]
                out.append(f"{info['name']} {self.skill_total(key)}"
                           f"（{D.STATS[info['stat']]['name']}"
                           f"{self.stat(info['stat'])}+{self.skill(key)}）")
        return out

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> dict:
        return {
            "ruleset": "cyberpunk-red",
            "name": self.name, "kind": self.kind, "handle": self.handle,
            "role": self.role, "role_rank": self.role_rank,
            "role_ability": self.role_ability,
            "stats": dict(self.stats), "skill_levels": dict(self.skill_levels),
            "hp_cur": self.hp_cur, "hp_max": self.hp_max,
            "luck_cur": self.luck_cur,
            "humanity_cur": self.humanity_cur, "humanity_max": self.humanity_max,
            "reputation": self.reputation, "eurobucks": self.eurobucks, "ip": self.ip,
            "armor_key": self.armor_key, "armor_sp": dict(self.armor_sp),
            "cyberware": list(self.cyberware), "weapons": list(self.weapons),
            "gear": dict(self.gear), "ammo": dict(self.ammo),
            "conditions": sorted(self.conditions), "timers": dict(self.timers),
            "crit_injuries": [dict(i) for i in self.crit_injuries],
            "death_save_mod": self.death_save_mod,
            "lifepath": {k: dict(v) if isinstance(v, dict) else v
                         for k, v in self.lifepath.items()},
            "persona": self.persona, "voice": self.voice, "desc": self.desc,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Character":
        c = cls(name=data.get("name", "?"), kind=data.get("kind", "pc"))
        c.handle = data.get("handle", "")
        c.role = data.get("role", "solo")
        c.role_rank = int(data.get("role_rank", 4))
        c.role_ability = data.get("role_ability", "")
        c.stats = {k: int(v) for k, v in (data.get("stats") or {}).items()}
        c.skill_levels = {k: int(v) for k, v in (data.get("skill_levels") or {}).items()}
        c.hp_cur = int(data.get("hp_cur", 0))
        c.hp_max = int(data.get("hp_max", 0))
        c.luck_cur = int(data.get("luck_cur", c.stat("luck")))
        c.humanity_max = int(data.get("humanity_max",
                                     c.stat("emp") * D.HUMANITY_PER_EMP))
        c.humanity_cur = int(data.get("humanity_cur", c.humanity_max))
        c.reputation = int(data.get("reputation", D.REPUTATION_START))
        c.eurobucks = int(data.get("eurobucks", 0))
        c.ip = int(data.get("ip", 0))
        c.armor_key = data.get("armor_key")
        c.armor_sp = {k: int(v) for k, v in (data.get("armor_sp") or {}).items()}
        c.cyberware = list(data.get("cyberware") or [])
        c.weapons = list(data.get("weapons") or [])
        c.gear = {k: int(v) for k, v in (data.get("gear") or {}).items()}
        c.ammo = {k: int(v) for k, v in (data.get("ammo") or {}).items()}
        c.conditions = set(data.get("conditions") or [])
        c.timers = dict(data.get("timers") or {})
        c.crit_injuries = [dict(i) for i in (data.get("crit_injuries") or [])]
        c.death_save_mod = int(data.get("death_save_mod", 0))
        c.lifepath = {k: dict(v) if isinstance(v, dict) else v
                      for k, v in (data.get("lifepath") or {}).items()}
        c.persona = data.get("persona", "")
        c.voice = data.get("voice", "")
        c.desc = data.get("desc", "")
        return c

    # ------------------------------------------------------------ 构造
    @staticmethod
    def hp_for(body: int, will: int | None = None) -> int:
        """PW 上限 = 10 + 5 × ⌈(体格 + 意志力) / 2⌉。

        已用轻松模式的三名预设角色核对：
        体格3/意志8 → 40；体格7/意志6 → 45；体格6/意志3 → 35。
        ``will`` 省略时按体格自身计算（用于只有体格可查的场合）。
        """
        will = int(body) if will is None else int(will)
        return 10 + 5 * math.ceil((int(body) + will) / 2)

    @classmethod
    def create(cls, name: str, role: str, stats: dict,
               skill_levels: dict | None = None, role_rank: int = 4,
               armor_key: str | None = None, weapons: list | None = None,
               cyberware: list | None = None, gear: dict | None = None,
               eurobucks: int = 0, kind: str = "pc",
               charge_cyberware: bool = True, **kw) -> "Character":
        """按 RED 口径创建角色：PW = 10 + 5×⌈(体格+意志力)÷2⌉，人性 = 共情×10。

        ``charge_cyberware=False`` 用于预设角色卡——卡面上的共情/人性数值已经
        反映了他们身上的义体，不能再扣一次。
        """
        c = cls(name=name, kind=kind, role=role, role_rank=int(role_rank))
        c.stats = {k: int(stats.get(k, D.STAT_MIN)) for k in D.STAT_ORDER}
        c.skill_levels = {k: int(v) for k, v in (skill_levels or {}).items()}
        c.hp_max = cls.hp_for(c.stat("body"), c.stat("will"))
        c.hp_cur = c.hp_max
        c.humanity_max = c.stat("emp") * D.HUMANITY_PER_EMP
        c.humanity_cur = c.humanity_max
        c.luck_cur = c.stat("luck")
        c.eurobucks = int(eurobucks)
        c.gear = dict(gear or {})
        c.weapons = list(weapons or [])
        for cw in (cyberware or []):
            c.install_cyberware(cw, charge=charge_cyberware)
        if armor_key:
            c.equip_armor(armor_key)
        for k, v in kw.items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c

    @classmethod
    def from_pregen(cls, key: str, pregen: dict, kind: str = "companion",
                    player: bool = False) -> "Character":
        """从模组里的预生成角色定义构造（轻松模式的预设角色）。"""
        c = cls.create(
            name=pregen.get("name") or key,
            role=pregen.get("role", "solo"),
            stats=pregen.get("stats") or {},
            skill_levels=pregen.get("skill_levels") or {},
            role_rank=int(pregen.get("role_rank", 4)),
            armor_key=pregen.get("armor"),
            weapons=list(pregen.get("weapons") or []),
            cyberware=list(pregen.get("cyberware") or []),
            gear=dict(pregen.get("gear") or {}),
            eurobucks=int(pregen.get("eurobucks", 0)),
            kind="pc" if player else kind,
            charge_cyberware=False,
            handle=pregen.get("handle", ""),
            persona=pregen.get("persona", ""),
            voice=pregen.get("voice", ""),
            desc=pregen.get("desc", ""),
            role_ability=pregen.get("role_ability", ""),
        )
        if pregen.get("hp"):
            c.hp_max = int(pregen["hp"])
            c.hp_cur = c.hp_max
        return c

    @classmethod
    def from_statblock(cls, key: str, block: dict, tag: str = "") -> "Character":
        """从敌人/NPC 数据表构造（``custom_npcs`` 或内置 ``NPCS``）。"""
        name = block.get("name", key)
        if tag:
            name = f"{name}{tag}"
        c = cls(name=name, kind="npc", role=block.get("role", "solo"))
        c.source_key = key
        c.stats = {k: int((block.get("stats") or {}).get(k, 5)) for k in D.STAT_ORDER}
        c.skill_levels = {k: int(v) for k, v in (block.get("skills") or {}).items()}
        # 与角色卡同一条口径：PW = 10 + 5 × ⌈(体格 + 意志力) / 2⌉
        c.hp_max = int(block.get("hp") or cls.hp_for(c.stat("body"), c.stat("will")))
        c.hp_cur = c.hp_max
        c.luck_cur = c.stat("luck")
        c.humanity_max = c.stat("emp") * D.HUMANITY_PER_EMP
        c.humanity_cur = c.humanity_max
        c.weapons = list(block.get("weapons") or [])
        c.cyberware = list(block.get("cyberware") or [])
        c.gear = {k: 1 for k in (block.get("gear") or [])}
        armor = int(block.get("armor", 0) or 0)
        if armor:
            # 数据块给的是显示名（如「凯夫拉」），不是 D.ARMORS 的 key
            c.armor_label = block.get("armor_name", "凯夫拉")
            c.armor_sp = {loc: armor for loc in LOCATIONS}
        c.desc = block.get("notes", "")
        c.reputation = int(block.get("reputation", D.REPUTATION_START))
        return c

    def clone(self, name: str | None = None) -> "Character":
        import copy as _copy
        c = _copy.deepcopy(self)
        if name:
            c.name = name
        return c
