"""角色系统：属性、技能、装备、施法（满/半/魔契）、资源池、状态、休整、升级。

PC 与 Friendly NPC 共用。严格按 SRD 5.1 / PHB 2014 机制实现：
- 动作经济由战斗引擎管理；本模块维护资源池与种族/职业特性。
- 种族特性全部机制化（幸运重骰、豁免优势、抗性、不屈坚韧等）。
- 施法分满施法者/半施法者/魔契（邪术师，短休恢复、同环阶）三种表。
- 力竭六级、死亡豁免、大伤害即死、稳定等生命规则完整实现。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import data as D
from .dice import DiceResult, check_roll, roll

ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")

# 条件（战斗引擎亦使用）
COND_POISONED = "poisoned"
COND_FRIGHTENED = "frightened"
COND_PRONE = "prone"
COND_UNCONSCIOUS = "unconscious"
COND_STABLE = "stable"
COND_HIDDEN = "hidden"
COND_INVISIBLE = "invisible"
COND_DEAD = "dead"
COND_CONCENTRATING = "concentrating"
COND_BLESSED = "blessed"
COND_MARKED = "hunters_mark"       # 印记/噬魂咒的施法者侧标记
COND_SHIELDED = "shielded"
COND_OUTLINED = "outlined"         # 妖火术：对它的攻击有优势
COND_RESTRAINED = "restrained"
COND_PARALYZED = "paralyzed"
COND_STUNNED = "stunned"
COND_CHARMED = "charmed"
COND_BLINDED = "blinded"
COND_GRAPPLED = "grappled"
COND_RAGING = "raging"
COND_SLEEP = "asleep"
COND_GUIDED = "guided"             # 引导射击：对它的下一次攻击有优势

# 带时限的状态（回合结束时由战斗引擎计时）
TIMED_CONDITIONS: dict[str, dict] = {
    COND_POISONED:   {"rounds": 10},
    COND_FRIGHTENED: {"rounds": 10},
    COND_OUTLINED:   {"rounds": 10},
    COND_RESTRAINED: {"rounds": 10, "save": "str", "dc": 11},
    COND_PARALYZED:  {"rounds": 10, "save": "wis"},
    COND_STUNNED:    {"rounds": 2, "save": "con"},
    COND_CHARMED:    {"rounds": 10},
    COND_BLINDED:    {"rounds": 10},
    COND_SHIELDED:   {"rounds": 1},
    COND_RAGING:     {"rounds": 10},
    COND_SLEEP:      {"rounds": 10},
}

SMALL_RACES = ("lightfoot_halfling", "strongheart_halfling",
               "forest_gnome", "rock_gnome", "deep_gnome")
GNOME_RACES = ("forest_gnome", "rock_gnome", "deep_gnome")
DWARF_RACES = ("dwarf_shield", "dwarf_gold", "duergar")
ELF_RACES = ("high_elf", "wood_elf", "drow_lolth", "drow_seldarine")


@dataclass
class Character:
    """一个 5e 角色（玩家或队友）。"""

    name: str
    race: str
    klass: str
    background: str
    level: int = 1
    xp: int = 0
    abilities: dict[str, int] = field(default_factory=lambda: {k: 10 for k in ABILITY_KEYS})
    skills: set[str] = field(default_factory=set)
    expertise: set[str] = field(default_factory=set)
    saves: set[str] = field(default_factory=set)
    fighting_style: str = ""
    # 装备
    armor: str = "none"
    shield: bool = False
    weapons: list[str] = field(default_factory=list)
    magic_attack_bonus: int = 0
    magic_damage_bonus: int = 0
    inventory: dict[str, int] = field(default_factory=dict)
    gold: int = 0
    # 状态
    hp_cur: int = 1
    hp_max: int = 1
    temp_hp: int = 0
    hit_dice_used: int = 0
    conditions: set[str] = field(default_factory=set)
    exhaustion: int = 0
    death_fails: int = 0
    death_successes: int = 0
    # 施法
    slots_used: dict[int, int] = field(default_factory=dict)
    concentration: str | None = None
    blessing_d4: int | None = None  # 祝福术骰面（本战斗固定）
    marked_target: str | None = None
    # 资源池（短休/长休恢复；-1 表示未定义）
    second_wind_used: bool = False
    action_surge_used: bool = False
    ki_used: int = 0
    rage_used: int = 0
    sorcery_points_used: int = 0
    bardic_inspiration_used: int = 0
    wild_shape_used: int = 0
    channel_divinity_used: int = 0
    lay_on_hands_used: int = 0
    superiority_used: int = 0
    breath_weapon_used: bool = False
    innate_used: set[str] = field(default_factory=set)   # 1/天 种族法术
    relentless_used: bool = False
    # 职业选择（子职业/魔契恩泽/超魔等）
    feature_choices: dict[str, str] = field(default_factory=dict)
    # 野变形态（德鲁伊）
    wild_shape: dict | None = None     # {"form": key, "hp_cur": int, "hp_max": int}
    # 武器附加伤害（炽焰斩/灼烙印记等）
    weapon_buff: dict | None = None    # {"dice": "1d6", "dtype": "fire"}
    # 镜影术
    mirror_images: int = 0
    # 吟游诗人激励（受赠者持有）
    bardic_dice: int = 0
    bardic_die: int = 0               # 激励骰面（授予者等级决定：d6/d8）
    # 吉斯洋基 星界知识
    astral_ability: str | None = None

    # ------------------------------------------------------------ 创建
    @classmethod
    def create(cls, name: str, race: str, klass: str, background: str,
               scores: dict[str, int], skill_choices: list[str],
               fighting_style: str = "", level: int = 1,
               bonus2: str | None = None, bonus1: str | None = None,
               feature_choices: dict[str, str] | None = None) -> "Character":
        if race not in D.RACES:
            raise ValueError(f"未知种族 {race}")
        if klass not in D.CLASSES:
            raise ValueError(f"未知职业 {klass}")
        if background not in D.BACKGROUNDS:
            raise ValueError(f"未知背景 {background}")
        c = cls(name=name, race=race, klass=klass, background=background)
        racial = D.resolve_bonuses(race, bonus2, bonus1)
        for k in ABILITY_KEYS:
            c.abilities[k] = scores[k] + racial.get(k, 0)
        kls = D.CLASSES[klass]
        c.saves = set(kls["saves"])
        chosen = list(skill_choices)
        # 职业技能只能从职业列表选；种族赠送的额外技能（人类/半精灵）可任选
        n_class = kls["skill_count"]
        class_pick = chosen[:n_class]
        bad = [s for s in class_pick if s not in kls["skill_choices"]]
        if bad:
            raise ValueError(f"{kls['name']}不能选择技能: {bad}")
        race_extra = chosen[n_class:n_class + D.RACES[race].get("skills", 0)]
        bad2 = [s for s in race_extra if s not in D.SKILLS]
        if bad2:
            raise ValueError(f"种族技能非法: {bad2}")
        c.skills = set(class_pick) | set(race_extra)
        for s in D.BACKGROUNDS[background]["skills"]:
            c.skills.add(s)
        for s in D.RACES[race].get("trait_skills", []):
            c.skills.add(s)
        c.fighting_style = fighting_style
        c.armor = kls["armor"] or "none"
        c.shield = bool(kls["shield"])
        c.weapons = list(kls["weapons"])
        c.inventory = {"healing_potion": 2, "torch": 2}
        c.gold = 15
        c.feature_choices = dict(feature_choices or {})
        c.hp_max = max(1, kls["hit_die"] + c.mod("con")
                       + (1 if D.RACES[race].get("toughness") else 0)
                       + (1 if c._draconic_resilience() else 0))
        for _ in range(level - 1):
            c._level_up_static()
        c.hp_cur = c.hp_max
        return c

    def _draconic_resilience(self) -> bool:
        return (self.klass == "sorcerer"
                and self.feature_choices.get("origin") == "draconic")

    def mech(self) -> dict:
        return D.race_mech(self.race)

    # ------------------------------------------------------------ 数值
    def mod(self, ability: str) -> int:
        return (self.abilities[ability] - 10) // 2

    def prof_bonus(self) -> int:
        return D.PROF_BONUS[self.level]

    def hp_die_count(self) -> int:
        return self.level - self.hit_dice_used

    def in_wild_shape(self) -> bool:
        return bool(self.wild_shape)

    def beast_info(self) -> dict:
        if not self.wild_shape:
            return {}
        return D.MONSTERS.get(self.wild_shape["form"], {})

    def armor_proficient(self) -> bool:
        armor_obj = D.ARMORS.get(self.armor, D.ARMORS["none"])
        kls = D.CLASSES[self.klass]
        if self.armor == "none" or armor_obj["ac"] == 0:
            return True
        prof = kls["armor"]
        if self.armor in ("chain_mail",):
            return prof == "chain_mail"
        if self.armor in ("chain_shirt", "scale_mail"):      # 中甲
            ok = prof in ("chain_mail", "chain_shirt", "leather", "scale_mail")
            return ok or self.mech().get("armor_training") == "medium"
        return True                                          # 轻甲全职业熟练

    def ac(self) -> int:
        if self.in_wild_shape():
            ac = self.beast_info().get("ac", 10)
            if self.shield:
                ac += 2
            return ac
        armor = D.ARMORS.get(self.armor, D.ARMORS["none"])
        dex = self.mod("dex")
        cap = armor["dex_cap"]
        if cap is not None:
            dex = min(dex, cap)
        if self.armor == "none":
            ud = D.CLASSES[self.klass].get("unarmored_def")
            ac = 10 + dex + (self.mod(ud) if ud else 0)
            if self._draconic_resilience():
                ac = 13 + dex
        else:
            ac = armor["ac"] + (dex if self.armor_proficient() else 0)
        if self.shield:
            ac += 2
        if self.fighting_style == "defense":
            ac += 1
        if COND_SHIELDED in self.conditions:  # 护盾术（1轮）
            ac += 5
        if "ac+2" in self.conditions:      # 信仰护盾
            ac += 2
        return ac

    def skill_mod(self, skill: str) -> int:
        if skill not in D.SKILLS:
            raise ValueError(f"未知技能 {skill}")
        abil = D.SKILLS[skill]["ability"]
        m = self.mod(abil)
        prof = self.prof_bonus()
        if skill in self.skills:
            m += prof
        if skill in self.expertise:
            m += prof
        elif (self.klass == "bard" and self.level >= 2
              and skill not in self.saves):
            m += prof // 2                                  # 万事通
        astral = self.astral_ability and D.SKILLS[skill]["ability"] == self.astral_ability
        if astral and skill not in self.skills and skill not in self.expertise:
            m += prof
        return m

    def save_mod(self, ability: str) -> int:
        m = self.mod(ability)
        if ability in self.saves:
            m += self.prof_bonus()
        return m

    def passive_perception(self) -> int:
        return 10 + self.skill_mod("perception")

    def is_small(self) -> bool:
        return self.race in SMALL_RACES

    @property
    def darkvision(self) -> int:
        """黑暗视觉射程（含魔鬼之视魔契恩泽）。"""
        base = D.RACES.get(self.race, {}).get("darkvision", 0)
        if self.has_feature("devils_sight"):
            return 120
        return base

    # ---------------- 施法
    def spell_slots(self) -> dict[int, int]:
        """可用法术位 {环阶: 次数}。魔契法术位为同环阶（如 5级: {3: 2}）。"""
        sc = D.CLASSES[self.klass]["spellcasting"]
        if not sc:
            return {}
        if sc["type"] == "pact":
            count, slv = D.PACT_MAGIC_SLOTS[self.level]
            used = self.slots_used.get(slv, 0)
            return {slv: count - used} if count - used > 0 else {}
        table = D.FULL_CASTER_SLOTS if sc["type"] == "full" else D.HALF_CASTER_SLOTS
        arr = table[self.level]
        used = {lv: self.slots_used.get(lv, 0) for lv in range(1, 4)}
        return {lv: arr[lv - 1] - used[lv] for lv in (1, 2, 3) if arr[lv - 1] > 0}

    def cast_ability(self) -> str:
        sc = D.CLASSES[self.klass]["spellcasting"]
        return sc["ability"] if sc else "int"

    def class_spells(self) -> list[str]:
        return [k for k, sp in D.SPELLS.items() if self.klass in sp["classes"]]

    def known_spells(self) -> list[str]:
        """可施展：职业技能 + 种族戏法 + 1/天 种族天生法术（已达获得等级）。"""
        out = list(self.class_spells())
        cantrip = D.RACES[self.race].get("cantrip")
        if cantrip:
            out.append(cantrip)
        for lv, spells in self.mech().get("innate_spells", {}).items():
            if self.level >= lv:
                out.extend(spells)
        return sorted(set(out))

    def is_innate(self, spell_key: str) -> bool:
        for lv, spells in self.mech().get("innate_spells", {}).items():
            if spell_key in spells and self.level >= lv:
                return True
        return spell_key == D.RACES[self.race].get("cantrip")

    def can_use_innate(self, spell_key: str) -> bool:
        return (self.is_innate(spell_key)
                and spell_key not in self.innate_used)

    def resource_lines(self) -> list[str]:
        out = []
        kls = D.CLASSES[self.klass]
        if self.klass == "fighter":
            out.append("回气" + ("已用" if self.second_wind_used else "可用")
                       + "；动作如潮" + ("已用" if self.action_surge_used else "可用"))
            if self.feature_choices.get("archetype") == "battlemaster":
                out.append(f"战技骰 {self.superiority_dice_left()}/4")
        if self.klass == "monk":
            out.append(f"气 {self.ki_left()}/{self.level}")
        if self.klass == "barbarian":
            out.append(f"狂暴 {self.rage_left()}/{self.rage_total()}")
        if self.klass == "sorcerer":
            out.append(f"术法点 {self.sorcery_points_left()}/{self.level}")
        if self.klass == "bard":
            total = self.bardic_total()
            out.append(f"激励 {total - self.bardic_inspiration_used}/{total}"
                       + ("（d8）" if self.level >= 5 else "（d6）"))
        if self.klass in ("cleric", "paladin"):
            out.append("引导神力" + ("已用" if self.channel_divinity_used else "可用"))
        if self.klass == "paladin":
            out.append(f"圣疗池 {self.lay_on_hands_left()}")
        if self.klass == "druid":
            out.append(f"野变 {2 - self.wild_shape_used}/2")
        if D.RACES[self.race].get("mech", {}).get("breath_weapon"):
            out.append("龙息" + ("已用（短休恢复）" if self.breath_weapon_used else "可用"))
        if self.exhaustion:
            out.append(f"力竭{self.exhaustion}级（{D.EXHAUSTION_LEVELS.get(self.exhaustion, '')}）")
        return out or ["（无特殊资源）"]

    # ---------------- 资源池
    def rage_total(self) -> int:
        return {1: 2, 2: 2, 3: 3, 4: 3, 5: 3}.get(self.level, 2)

    def rage_left(self) -> int:
        return max(0, self.rage_total() - self.rage_used)

    def ki_left(self) -> int:
        return max(0, self.level - self.ki_used)

    def sorcery_points_left(self) -> int:
        return max(0, self.level - self.sorcery_points_used)

    def bardic_total(self) -> int:
        return max(1, self.mod("cha"))

    def lay_on_hands_left(self) -> int:
        return max(0, 5 * self.level - self.lay_on_hands_used)

    def superiority_dice_left(self) -> int:
        return max(0, 4 - self.superiority_used)

    def has_feature(self, key: str) -> bool:
        """职业特性开关（含子职业与等级门槛）。"""
        fc = self.feature_choices
        lvl = self.level
        checks = {
            "joat": self.klass == "bard" and lvl >= 2,
            "song_of_rest": self.klass == "bard" and lvl >= 2,
            "cutting_words": self.klass == "bard" and lvl >= 3 and fc.get("college") == "lore",
            "danger_sense": self.klass == "barbarian" and lvl >= 2,
            "reckless": self.klass == "barbarian" and lvl >= 2,
            "frenzy": self.klass == "barbarian" and lvl >= 3 and fc.get("path") == "berserker",
            "totem_bear": self.klass == "barbarian" and lvl >= 3 and fc.get("path") == "totem",
            "fast_movement": self.klass == "barbarian" and lvl >= 5,
            "brutal_critical": self.klass == "barbarian" and lvl >= 5,
            "improved_crit": self.klass == "fighter" and lvl >= 3 and fc.get("archetype") == "champion",
            "battlemaster": self.klass == "fighter" and lvl >= 3 and fc.get("archetype") == "battlemaster",
            "action_surge": self.klass == "fighter" and lvl >= 2,
            "open_hand": self.klass == "monk" and lvl >= 3 and fc.get("tradition") == "open_hand",
            "wholeness": self.klass == "monk" and lvl >= 3,
            "stunning_strike": self.klass == "monk" and lvl >= 5,
            "divine_smite": self.klass == "paladin" and lvl >= 2,
            "sacred_weapon": self.klass == "paladin" and lvl >= 3,
            "turn_unholy": self.klass == "paladin" and lvl >= 3,
            "colossus_slayer": self.klass == "ranger" and lvl >= 3 and fc.get("prey") == "colossus",
            "horde_breaker": self.klass == "ranger" and lvl >= 3 and fc.get("prey") == "horde",
            "cunning_action": self.klass == "rogue" and lvl >= 2,
            "fast_hands": self.klass == "rogue" and lvl >= 3 and fc.get("archetype") == "thief",
            "assassinate": self.klass == "rogue" and lvl >= 3 and fc.get("archetype") == "assassin",
            "uncanny_dodge": self.klass == "rogue" and lvl >= 5,
            "font_of_magic": self.klass == "sorcerer" and lvl >= 2,
            "draconic_resilience": self._draconic_resilience(),
            "quickened": self.klass == "sorcerer" and lvl >= 3 and fc.get("metamagic_quickened"),
            "heightened": self.klass == "sorcerer" and lvl >= 3 and fc.get("metamagic_heightened"),
            "careful": self.klass == "sorcerer" and lvl >= 3 and fc.get("metamagic_careful"),
            "dark_blessing": self.klass == "warlock" and lvl >= 1 and fc.get("patron") == "fiend",
            "agonizing_blast": self.klass == "warlock" and lvl >= 2 and fc.get("invocation_1") == "agonizing",
            "devils_sight": self.klass == "warlock" and lvl >= 2 and
                           ("invocation_1" in fc and fc.get("invocation_1") == "devils_sight"
                            or fc.get("invocation_2") == "devils_sight"),
            "repelling_blast": self.klass == "warlock" and lvl >= 2 and
                               ("invocation_1" in fc and fc.get("invocation_1") == "repelling"
                                or fc.get("invocation_2") == "repelling"),
            "pact_blade": self.klass == "warlock" and lvl >= 3 and fc.get("boon") == "blade",
            "sculpt_spells": self.klass == "wizard" and lvl >= 2 and fc.get("school") == "evocation",
            "magical_cunning": self.klass == "warlock" and lvl >= 2,
        }
        return bool(checks.get(key))

    def extra_attack_level(self) -> int | None:
        return D.CLASSES[self.klass].get("extra_attack")

    # ------------------------------------------------------------ 检定
    def _lucky_d20(self, rng: random.Random, advantage: int) -> DiceResult:
        """d20 + 半身人幸运（出1重骰一次）。"""
        res = check_roll(rng, 0, advantage)
        if res.kept[0] == 1 and self.mech().get("lucky_reroll_1"):
            res2 = check_roll(rng, 0, advantage)
            res = DiceResult(expr="d20(幸运重骰)", rolls=res.rolls + res2.rolls,
                             kept=res2.kept, modifier=0, total=res2.kept[0])
        return res

    def check(self, rng: random.Random, skill: str, dc: int,
              advantage: int = 0, extra_hint: str = "") -> dict:
        adv = advantage
        if self.exhaustion >= 1:
            adv -= 1
        if COND_POISONED in self.conditions:
            adv -= 1
        res = self._lucky_d20(rng, adv)
        # 祝福术只作用于攻击检定与豁免，不加到属性检定（PHB 2014）
        bi = self.take_bardic(rng)
        ok = res.total + bi >= dc
        total = res.total + bi
        return {
            "who": self.name, "check": skill, "dc": dc, "success": ok, "total": total,
            "d20": res.kept[0], "rolls": list(res.rolls), "adv": adv,
            "detail": res.describe() + (f"（激励+{bi}）" if bi else ""),
        }

    def save(self, rng: random.Random, ability: str, dc: int, advantage: int = 0,
             effect: str = "") -> dict:
        # 麻痹/震慑/昏迷：力量与敏捷豁免自动失败（PHB 状态说明）
        if (ability in ("str", "dex")
                and (COND_PARALYZED in self.conditions
                     or COND_STUNNED in self.conditions
                     or COND_UNCONSCIOUS in self.conditions)):
            return {"who": self.name, "save": ability, "dc": dc, "success": False,
                    "d20": None, "rolls": [], "adv": advantage,
                    "detail": "[自动失败：麻痹/昏迷]", "bless": 0}
        adv = advantage
        mech = self.mech()
        if effect and effect in mech.get("save_adv_vs", []):
            adv += 1
        if ability in mech.get("save_adv", []):
            adv += 1
        if COND_RESTRAINED in self.conditions and ability == "dex":
            adv -= 1                                       # 束缚：敏捷豁免劣势
        if self.klass == "barbarian" and self.level >= 2 and ability == "dex":
            adv += 1                                       # 危险感
        if self.exhaustion >= 3:
            adv -= 1
        res = self._lucky_d20(rng, adv)
        bless = 0
        if COND_BLESSED in self.conditions and self.blessing_d4:
            bless = rng.randint(1, self.blessing_d4)
            res.total += bless
        bi = self.take_bardic(rng)
        return {"who": self.name, "save": ability, "dc": dc,
                "success": res.total + bi >= dc,
                "d20": res.kept[0], "rolls": list(res.rolls), "adv": adv,
                "detail": res.describe() + (f"（激励+{bi}）" if bi else "")
                          + (f"（祝福+{bless}）" if bless else ""),
                "bless": bless}

    def take_bardic(self, rng: random.Random) -> int:
        """自动消耗持有的激励骰（受赠者加到本次掷骰）。骰面由授予者等级决定。"""
        if self.bardic_dice > 0:
            self.bardic_dice -= 1
            die = self.bardic_die or 6
            if self.bardic_dice == 0:
                self.bardic_die = 0
            return rng.randint(1, die)
        return 0

    # ------------------------------------------------------------ 生命与休整
    def take_damage(self, amount: int) -> dict:
        """受伤。处理临时生命、大伤害即死（先于不屈坚韧判定）、不屈坚韧。

        PHB 大伤害规则：该次伤害将你打到 0 且溢出伤害 ≥ 生命上限 → 立即死亡
        （"killed outright"，不屈坚韧等"降到0时改为1"的效果救不了）；
        已在 0 HP 时再受一次 ≥ 生命上限的伤害同样立即死亡。
        """
        if amount <= 0:
            return {"damage": 0, "hp": max(self.hp_cur, 0)}
        before = self.hp_cur
        absorbed = 0
        if self.temp_hp > 0:
            absorbed = min(self.temp_hp, amount)
            self.temp_hp -= absorbed
            amount -= absorbed
        self.hp_cur -= amount
        info = {"damage": before - self.hp_cur, "hp": max(self.hp_cur, 0),
                "temp_absorbed": absorbed}
        if self.hp_cur <= 0:
            overflow = -self.hp_cur                      # 打到 0 之后的剩余伤害
            if (before <= 0 and amount >= self.hp_max) or overflow >= self.hp_max:
                self.conditions |= {COND_DEAD}
                self.hp_cur = 0
                info["massive_death"] = True
                info["down"] = True
                return info
            if (self.mech().get("relentless_endurance")
                    and not self.relentless_used):
                # 不屈坚韧（1/长休）：非"被当场杀死"时改为保留 1 HP
                self.relentless_used = True
                self.hp_cur = 1
                info["relentless"] = True
                info["hp"] = 1
                info["damage"] = before - 1
                return info
            if COND_UNCONSCIOUS not in self.conditions:
                self.conditions |= {COND_UNCONSCIOUS}
                self.death_fails = 0
                self.death_successes = 0
                info["down"] = True
            self.hp_cur = 0
        return info

    def heal(self, amount: int) -> int:
        if "dead" in self.conditions:
            return 0                        # 死亡者无法被治疗（需复生魔法）
        before = self.hp_cur
        self.hp_cur = min(self.hp_max, self.hp_cur + amount)
        if self.hp_cur > 0:
            self.conditions -= {COND_UNCONSCIOUS, COND_STABLE}
            self.death_fails = self.death_successes = 0
        return self.hp_cur - before

    def revive(self, hp: int = 1) -> None:
        """场景转换时的简化复活（清死亡状态，保持可玩性）。"""
        hp = max(1, hp)
        self.conditions -= {"dead", COND_UNCONSCIOUS, COND_STABLE,
                            COND_POISONED, COND_FRIGHTENED}
        self.death_fails = self.death_successes = 0
        self.hp_cur = min(self.hp_max, hp)

    def death_save_turn(self, rng: random.Random) -> dict:
        """回合开始时的死亡豁免（已稳定者不再掷骰）。"""
        if self.hp_cur > 0 or COND_STABLE in self.conditions:
            return {}
        res = self._lucky_d20(rng, 0)
        base = {"who": self.name, "d20": res.kept[0], "rolls": list(res.rolls)}
        if res.kept[0] == 20:
            self.hp_cur = 1
            self.conditions -= {COND_UNCONSCIOUS}
            return {**base, "result": "revived", "detail": res.describe()}
        if res.kept[0] == 1:
            self.death_fails += 2
        elif res.total >= 10:
            self.death_successes += 1
        else:
            self.death_fails += 1
        if self.death_fails >= 3:
            self.conditions |= {COND_DEAD}
            self.conditions -= {COND_STABLE, COND_UNCONSCIOUS}
            return {**base, "result": "died", "detail": res.describe()}
        if self.death_successes >= 3:
            self.conditions |= {COND_STABLE}
            return {**base, "result": "stable", "detail": res.describe()}
        return {**base, "result": "pending", "detail": res.describe()}

    def short_rest(self, rng: random.Random, spend_dice: int | None = None,
                   song_of_rest: bool = False) -> str:
        """短休：消耗生命骰（spend_dice=None 时自动花到回满或骰尽）。

        song_of_rest：队伍中有 2 级+ 吟游诗人时，每次生命骰回复额外 +1d6。
        恢复：回气/动作如潮/战技骰/气/野变/引导神力/激励(5级)/魔契法术位；
        法师奥术回复、德鲁伊自然回复按机制结算。
        """
        self.second_wind_used = False
        self.action_surge_used = False
        self.superiority_used = 0
        self.ki_used = 0
        self.wild_shape_used = 0
        self.channel_divinity_used = 0
        sc = D.CLASSES[self.klass]["spellcasting"]
        if sc and sc["type"] == "pact":
            self.slots_used.clear()
        lines = []
        remaining = spend_dice if spend_dice is not None else self.hp_die_count()
        while remaining > 0 and self.hp_cur < self.hp_max and self.hp_die_count() > 0:
            self.hit_dice_used += 1
            remaining -= 1
            r = roll(rng, f"1d{D.CLASSES[self.klass]['hit_die']}")
            gained = r.total + self.mod("con")
            if song_of_rest or self.has_feature("song_of_rest"):
                gained += roll(rng, "1d6").total
            self.hp_cur = min(self.hp_max, self.hp_cur + gained)
            lines.append(f"{r.describe()} → 回复 {gained}")
        if self.klass == "wizard":
            budget = (self.level + 1) // 2
            for lv in (3, 2, 1):
                used = self.slots_used.get(lv, 0)
                while used > 0 and budget >= lv:
                    used -= 1
                    budget -= lv
                    self.slots_used[lv] = used
        if self.klass == "druid" and self.level >= 2:
            budget = (self.level + 1) // 2                 # 自然回复：半德鲁伊等级向上取整
            for lv in (3, 2, 1):
                used = self.slots_used.get(lv, 0)
                while used > 0 and budget >= lv:
                    used -= 1
                    budget -= lv
                    self.slots_used[lv] = used
        if self.klass == "bard" and self.level >= 5:
            self.bardic_inspiration_used = 0               # 泉涌激励：5级起短休恢复
        msg = "；".join(lines) if lines else "生命骰已用尽或无需回复。"
        return msg

    def long_rest(self) -> str:
        self.hp_cur = self.hp_max
        self.recover_hit_dice_long()
        self.slots_used.clear()
        self.conditions -= {COND_POISONED, COND_FRIGHTENED, COND_RAGING,
                            COND_BLINDED, COND_CHARMED}
        self.death_fails = self.death_successes = 0
        self.second_wind_used = False
        self.action_surge_used = False
        self.ki_used = 0
        self.rage_used = 0
        self.sorcery_points_used = 0
        self.bardic_inspiration_used = 0
        self.wild_shape_used = 0
        self.channel_divinity_used = 0
        self.lay_on_hands_used = 0
        self.superiority_used = 0
        self.breath_weapon_used = False
        self.innate_used.clear()
        self.relentless_used = False
        self.temp_hp = 0
        if self.exhaustion > 0:
            self.exhaustion -= 1
        return f"{self.name} 长休完毕：生命 {self.hp_max}，法术位与资源全部恢复。"

    def recover_hit_dice_long(self) -> None:
        """长休恢复生命骰：恢复一半（向下取整，至少 1 颗）。"""
        recovered = max(1, self.level // 2)
        self.hit_dice_used = max(0, self.hit_dice_used - recovered)

    # ------------------------------------------------------------ 升级
    def level_up_to(self, target: int) -> str:
        """里程碑升级：直接升至目标等级并回复全满。"""
        msgs = []
        while self.level < target:
            self._level_up_static()
            self.xp = max(self.xp, D.XP_TABLE[self.level])
            self.hp_cur = self.hp_max
            self.slots_used.clear()
            msgs.append(f"{self.name} 升到 {self.level} 级！（生命全满：{self.hp_max}）")
        return "；".join(msgs)

    def gain_xp(self, amount: int) -> str:
        self.xp += amount
        msgs = []
        while self.level < D.MAX_LEVEL and self.xp >= D.XP_TABLE[self.level + 1]:
            self._level_up_static()
            self.hp_cur = self.hp_max
            msgs.append(f"{self.name} 升到 {self.level} 级！（生命全满：{self.hp_max}）")
        return "；".join(msgs)

    def _level_up_static(self) -> None:
        """升级：HP（取均骰=骰面/2+1 + 体质）与资源上限。固定值便于确定性。"""
        die = D.CLASSES[self.klass]["hit_die"]
        avg = die // 2 + 1
        gain = avg + self.mod("con")
        if D.RACES[self.race].get("toughness"):
            gain += 1
        if self._draconic_resilience():
            gain += 1
        self.hp_max += max(1, gain)
        self.level += 1
        self.slots_used.clear()

    def pending_feature_choices(self) -> list[str]:
        """升级后待选的职业特性（由 UI 询问玩家；同伴走默认值）。"""
        out = []
        lvl, kls = self.level, self.klass
        if lvl >= 1 and kls == "rogue" and "expertise" not in self.feature_choices:
            out.append("expertise")
        if lvl >= 3 and kls == "bard" and "expertise" not in self.feature_choices:
            out.append("expertise")
        if lvl >= 2 and kls in ("paladin", "ranger") and not self.fighting_style:
            out.append("fighting_style")
        if lvl >= 3 and kls == "barbarian" and "path" not in self.feature_choices:
            out.append("path")
        if lvl >= 3 and kls == "fighter" and "archetype" not in self.feature_choices:
            out.append("archetype")
        if lvl >= 3 and kls == "monk" and "tradition" not in self.feature_choices:
            out.append("tradition")
        if lvl >= 3 and kls == "rogue" and "archetype" not in self.feature_choices:
            out.append("rogue_archetype")
        if lvl >= 3 and kls == "ranger" and "prey" not in self.feature_choices:
            out.append("prey")
        if lvl >= 2 and kls == "bard" and "college" not in self.feature_choices:
            out.append("college")
        if lvl >= 2 and kls == "wizard" and "school" not in self.feature_choices:
            out.append("school")
        if lvl >= 1 and kls == "sorcerer" and "origin" not in self.feature_choices:
            out.append("origin")
        if lvl >= 3 and kls == "sorcerer" and "metamagic" not in self.feature_choices:
            out.append("metamagic")
        if lvl >= 2 and kls == "warlock" and "invocations" not in self.feature_choices:
            out.append("invocations")
        if lvl >= 3 and kls == "warlock" and "boon" not in self.feature_choices:
            out.append("boon")
        return out

    def apply_default_choices(self) -> None:
        """给同伴/自动流程填默认职业特性选择。"""
        fc = self.feature_choices
        kls = self.klass
        for need in self.pending_feature_choices():
            if need == "fighting_style":
                self.fighting_style = "archery" if kls == "ranger" else "defense"
            elif need == "expertise":
                picks = sorted(self.skills)[:2]
                self.expertise |= set(picks)
                fc["expertise"] = "+".join(picks)
            elif need == "path":
                fc["path"] = "totem"
            elif need == "archetype":
                fc["archetype"] = "champion"
            elif need == "rogue_archetype":
                fc["archetype"] = "thief"
            elif need == "tradition":
                fc["tradition"] = "open_hand"
            elif need == "prey":
                fc["prey"] = "colossus"
            elif need == "college":
                fc["college"] = "lore"
            elif need == "school":
                fc["school"] = "evocation"
            elif need == "origin":
                fc["origin"] = "draconic"
            elif need == "metamagic":
                fc["metamagic"] = "quickened+heightened"
                fc["metamagic_quickened"] = True
                fc["metamagic_heightened"] = True
            elif need == "invocations":
                fc["invocations"] = "agonizing+devils_sight"
                fc["invocation_1"] = "agonizing"
                fc["invocation_2"] = "devils_sight"
            elif need == "boon":
                fc["boon"] = "blade"
        self.feature_choices.setdefault("patron", "fiend")

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> dict:
        return {
            "name": self.name, "race": self.race, "klass": self.klass,
            "background": self.background, "level": self.level, "xp": self.xp,
            "abilities": dict(self.abilities), "skills": sorted(self.skills),
            "expertise": sorted(self.expertise), "saves": sorted(self.saves),
            "fighting_style": self.fighting_style, "armor": self.armor,
            "shield": self.shield, "weapons": list(self.weapons),
            "magic_attack_bonus": self.magic_attack_bonus,
            "magic_damage_bonus": self.magic_damage_bonus,
            "inventory": dict(self.inventory), "gold": self.gold,
            "hp_cur": self.hp_cur, "hp_max": self.hp_max,
            "hit_dice_used": self.hit_dice_used,
            "slots_used": {str(k): v for k, v in self.slots_used.items()},
            "feature_choices": dict(self.feature_choices),
            "exhaustion": self.exhaustion,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        c = cls(
            name=d["name"], race=d["race"], klass=d["klass"], background=d["background"],
            level=d["level"], xp=d["xp"], abilities=dict(d["abilities"]),
            skills=set(d["skills"]), expertise=set(d["expertise"]), saves=set(d["saves"]),
            fighting_style=d.get("fighting_style", ""), armor=d["armor"],
            shield=d["shield"], weapons=list(d["weapons"]),
            magic_attack_bonus=d.get("magic_attack_bonus", 0),
            magic_damage_bonus=d.get("magic_damage_bonus", 0),
            inventory=dict(d["inventory"]), gold=d.get("gold", 0),
            hp_cur=d["hp_cur"], hp_max=d["hp_max"],
            hit_dice_used=d.get("hit_dice_used", 0),
            slots_used={int(k): v for k, v in d.get("slots_used", {}).items()},
            feature_choices=dict(d.get("feature_choices", {})),
            exhaustion=d.get("exhaustion", 0),
        )
        return c
