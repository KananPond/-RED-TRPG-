"""D&D 5e 规则数据表（SRD 5.1 机制全集，中文显示名）。

键名使用英文稳定标识，显示名供 UI 与 Agent 叙事使用。
机制数值为 SRD 5.1 (CC-BY-4.0) 公开数值；种族、职业、出身名单沿用
《博德之门3》(Baldur's Gate 3) 的完整名单：
- 种族：11 大族全部亚种（含灰矮人、吉斯洋基人与 10 色龙裔）；属性加值
  采用官方可选规则（Tasha's 万能起源 / 2024 版规则）：任意两项 +2/+1；
  种族特性全部由引擎机制实现（见 RACES 各条目的 mech 字段）。
- 职业：12 个职业，1~5 级特性全部按 PHB 2014 机制实现（含附赠动作、
  反应、资源池、魔契法术位、额外攻击等），不再做"满施法者"近似。
- 出身：BG3 可选的 11 个出身（"被诅咒者"为邪念起源专属，故不列入）。
- 生物：SRD 图鉴数值（CR/XP 与属性块一致），另含野变形态野兽表。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 属性 / 技能
ABILITIES = {
    "str": "力量", "dex": "敏捷", "con": "体质",
    "int": "智力", "wis": "感知", "cha": "魅力",
}

SKILLS: dict[str, dict] = {
    "athletics":        {"name": "运动", "en": "Athletics", "ability": "str"},
    "acrobatics":       {"name": "杂技", "en": "Acrobatics", "ability": "dex"},
    "sleight_of_hand":  {"name": "巧手", "en": "Sleight of Hand", "ability": "dex"},
    "stealth":          {"name": "隐匿", "en": "Stealth", "ability": "dex"},
    "arcana":           {"name": "奥秘", "en": "Arcana", "ability": "int"},
    "history":          {"name": "历史", "en": "History", "ability": "int"},
    "investigation":    {"name": "调查", "en": "Investigation", "ability": "int"},
    "nature":           {"name": "自然", "en": "Nature", "ability": "int"},
    "religion":         {"name": "宗教", "en": "Religion", "ability": "int"},
    "animal_handling":  {"name": "驯兽", "en": "Animal Handling", "ability": "wis"},
    "insight":          {"name": "洞悉", "en": "Insight", "ability": "wis"},
    "medicine":         {"name": "医药", "en": "Medicine", "ability": "wis"},
    "perception":       {"name": "察觉", "en": "Perception", "ability": "wis"},
    "survival":         {"name": "生存", "en": "Survival", "ability": "wis"},
    "deception":        {"name": "欺瞒", "en": "Deception", "ability": "cha"},
    "intimidation":     {"name": "威吓", "en": "Intimidation", "ability": "cha"},
    "performance":      {"name": "表演", "en": "Performance", "ability": "cha"},
    "persuasion":       {"name": "说服", "en": "Persuasion", "ability": "cha"},
}

# ---------------------------------------------------------------- 种族（BG3 名单 · 5e 机制）
# mech 键（引擎实现）：
#   lucky_reroll_1      d20 出 1 可重骰一次（半身人幸运）
#   save_adv            豁免固定优势的属性列表（侏儒狡黠等）
#   save_adv_vs         对指定效果的豁免优势（frightened/charm/poison/paralysis/illusion）
#   poison_resist       毒素伤害抗性
#   sleep_immune        免疫魔法睡眠（精类血统）
#   fire_resist         火焰伤害抗性
#   savage_attacks      近战暴击时额外一颗武器伤害骰
#   relentless_endurance 每长休1次：降到0HP时改为保留1HP
#   toughness           每级生命上限+1
#   breath_weapon       龙息 {dtype, shape, dice1, dice5}
#   astral_knowledge    每次长休获得一项属性的全部技能熟练
#   innate_spells       {获得等级: [法术key]}（种族天生施法）
#   innate_frequency    innate_spells 的使用频率（"day"=1/天）
#   stealth_adv         隐匿检定优势（场景受限：石皮伪装/天生隐匿）
#   armor_training      额外护甲熟练（dwarf→medium）
#   small               小型生物（重型武器攻击劣势）
#   devil_sight_pending 天生黑暗视觉（darkvision 字段）
RACES: dict[str, dict] = {
    "human": {
        "name": "人类", "en": "Human", "family": "人类", "speed": 30, "skills": 1,
        "default_bonuses": {"str": 2, "con": 1},
        "traits": ["人类多才：+2/+1 任意属性（创建时自选）", "自选 1 项技能熟练"],
        "darkvision": 0,
    },
    "high_elf": {
        "name": "高等精灵", "en": "High Elf", "family": "精灵", "speed": 30, "skills": 0,
        "trait_skills": ["perception"], "cantrip": "fire_bolt",
        "default_bonuses": {"dex": 2, "int": 1},
        "traits": ["精类血统（魅惑豁免优势、免疫魔法睡眠）", "自选一个法师戏法", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"sleep_immune": True, "save_adv_vs": ["charm"]},
    },
    "wood_elf": {
        "name": "木精灵", "en": "Wood Elf", "family": "精灵", "speed": 35, "skills": 0,
        "trait_skills": ["perception", "stealth"],
        "default_bonuses": {"dex": 2, "wis": 1},
        "traits": ["轻足疾行（移速35尺）", "隐匿熟练", "精类血统", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"sleep_immune": True, "save_adv_vs": ["charm"]},
    },
    "drow_lolth": {
        "name": "罗丝信仰卓尔", "en": "Lolth-Sworn Drow", "family": "卓尔", "speed": 30, "skills": 0,
        "trait_skills": ["perception"],
        "default_bonuses": {"dex": 2, "cha": 1},
        "traits": ["卓尔魔法（舞光术/妖火术@3/黑暗术@5）", "黑暗视觉120尺", "精类血统"],
        "darkvision": 120,
        "mech": {"sleep_immune": True, "save_adv_vs": ["charm"],
                 "innate_spells": {1: ["dancing_lights"], 3: ["faerie_fire"], 5: ["darkness_spell"]},
                 "innate_frequency": "day"},
    },
    "drow_seldarine": {
        "name": "塞内瑞恩卓尔", "en": "Seldarine Drow", "family": "卓尔", "speed": 30, "skills": 0,
        "trait_skills": ["perception"],
        "default_bonuses": {"dex": 2, "cha": 1},
        "traits": ["卓尔魔法（舞光术/妖火术@3/黑暗术@5）", "黑暗视觉120尺", "精类血统"],
        "darkvision": 120,
        "mech": {"sleep_immune": True, "save_adv_vs": ["charm"],
                 "innate_spells": {1: ["dancing_lights"], 3: ["faerie_fire"], 5: ["darkness_spell"]},
                 "innate_frequency": "day"},
    },
    "half_elf_high": {
        "name": "高等半精灵", "en": "High Half-Elf", "family": "半精灵", "speed": 30, "skills": 1,
        "trait_skills": [], "cantrip": "fire_bolt",
        "default_bonuses": {"cha": 2, "dex": 1},
        "traits": ["精类血统（魅惑豁免优势、免疫魔法睡眠）", "自选一个法师戏法", "自选 1 项技能", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"sleep_immune": True, "save_adv_vs": ["charm"]},
    },
    "half_elf_wood": {
        "name": "木半精灵", "en": "Wood Half-Elf", "family": "半精灵", "speed": 35, "skills": 1,
        "trait_skills": ["stealth"],
        "default_bonuses": {"cha": 2, "dex": 1},
        "traits": ["轻足疾行（移速35尺）", "隐匿熟练", "精类血统", "自选 1 项技能", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"sleep_immune": True, "save_adv_vs": ["charm"]},
    },
    "half_elf_drow": {
        "name": "卓尔半精灵", "en": "Drow Half-Elf", "family": "半精灵", "speed": 30, "skills": 1,
        "trait_skills": [],
        "default_bonuses": {"cha": 2, "dex": 1},
        "traits": ["卓尔魔法（舞光术/妖火术@3/黑暗术@5）", "精类血统", "自选 1 项技能", "黑暗视觉120尺"],
        "darkvision": 120,
        "mech": {"sleep_immune": True, "save_adv_vs": ["charm"],
                 "innate_spells": {1: ["dancing_lights"], 3: ["faerie_fire"], 5: ["darkness_spell"]},
                 "innate_frequency": "day"},
    },
    "dwarf_shield": {
        "name": "盾矮人", "en": "Shield Dwarf", "family": "矮人", "speed": 25, "skills": 0,
        "default_bonuses": {"con": 2, "str": 1},
        "traits": ["矮人韧劲（对抗毒素优势/毒素伤害减半）", "矮人护甲训练（中甲熟练）", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"save_adv_vs": ["poison"], "poison_resist": True, "armor_training": "medium"},
    },
    "dwarf_gold": {
        "name": "黄金矮人", "en": "Gold Dwarf", "family": "矮人", "speed": 25, "skills": 0,
        "toughness": True,
        "default_bonuses": {"con": 2, "wis": 1},
        "traits": ["矮人韧劲（对抗毒素优势/毒素伤害减半）", "矮人坚韧（每级生命上限+1）", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"save_adv_vs": ["poison"], "poison_resist": True, "toughness": True,
                 "armor_training": "medium"},
    },
    "duergar": {
        "name": "灰矮人", "en": "Duergar", "family": "矮人", "speed": 25, "skills": 0,
        "default_bonuses": {"con": 2, "str": 1},
        "traits": ["灰矮人韧劲（幻术/魅惑/麻痹豁免优势）", "灰矮人魔法（扩大术/隐形术，1/天）", "黑暗视觉120尺"],
        "darkvision": 120,
        "mech": {"save_adv_vs": ["illusion", "charm", "paralysis"],
                 "innate_spells": {1: ["enlarge_self"], 3: ["invisibility_self"]},
                 "innate_frequency": "day", "armor_training": "medium"},
    },
    "lightfoot_halfling": {
        "name": "轻足半身人", "en": "Lightfoot Halfling", "family": "半身人", "speed": 25, "skills": 0,
        "default_bonuses": {"dex": 2, "cha": 1},
        "traits": ["半身人幸运（d20出1可重骰）", "无畏（对抗恐惧优势）", "天生隐匿", "小型生物"],
        "darkvision": 0,
        "mech": {"lucky_reroll_1": True, "save_adv_vs": ["frightened"],
                 "stealth_adv": True, "small": True},
    },
    "strongheart_halfling": {
        "name": "强心半身人", "en": "Strongheart Halfling", "family": "半身人", "speed": 25, "skills": 0,
        "default_bonuses": {"con": 2, "dex": 1},
        "traits": ["半身人幸运（d20出1可重骰）", "强心韧性（对抗毒素优势、毒素抗性）", "无畏", "小型生物"],
        "darkvision": 0,
        "mech": {"lucky_reroll_1": True, "save_adv_vs": ["frightened", "poison"],
                 "poison_resist": True, "small": True},
    },
    "forest_gnome": {
        "name": "森林侏儒", "en": "Forest Gnome", "family": "侏儒", "speed": 25, "skills": 0,
        "default_bonuses": {"int": 2, "dex": 1},
        "traits": ["侏儒狡黠（智力/感知/魅力豁免优势）", "与动物交谈（天然）", "小型生物", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"save_adv": ["int", "wis", "cha"], "small": True},
    },
    "rock_gnome": {
        "name": "岩侏儒", "en": "Rock Gnome", "family": "侏儒", "speed": 25, "skills": 0,
        "default_bonuses": {"int": 2, "con": 1},
        "traits": ["侏儒狡黠（智力/感知/魅力豁免优势）", "工匠知识（历史相关检定优势）", "小型生物", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"save_adv": ["int", "wis", "cha"], "small": True},
    },
    "deep_gnome": {
        "name": "深地侏儒", "en": "Deep Gnome", "family": "侏儒", "speed": 25, "skills": 0,
        "default_bonuses": {"int": 2, "dex": 1},
        "traits": ["侏儒狡黠", "石皮伪装（岩石中隐匿优势）", "小型生物", "黑暗视觉120尺"],
        "darkvision": 120,
        "mech": {"save_adv": ["int", "wis", "cha"], "stealth_adv": True, "small": True},
    },
    "tiefling_asmodeus": {
        "name": "提夫林·阿斯莫德", "en": "Asmodeus Tiefling", "family": "提夫林", "speed": 30, "skills": 0,
        "default_bonuses": {"cha": 2, "con": 1},
        "traits": ["地狱抗性（火焰伤害减半）", "地狱血统（燃火术/狱火斥喝@3/黑暗术@5，1/天）", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"fire_resist": True,
                 "innate_spells": {1: ["produce_flame"], 3: ["hellish_rebuke"], 5: ["darkness_spell"]},
                 "innate_frequency": "day"},
    },
    "tiefling_mephisto": {
        "name": "提夫林·梅菲斯托费勒斯", "en": "Mephistopheles Tiefling", "family": "提夫林", "speed": 30, "skills": 0,
        "default_bonuses": {"cha": 2, "con": 1},
        "traits": ["地狱抗性（火焰伤害减半）", "卡尼亚血统（法师之手/燃烧之手@3/烈焰之刃@5，1/天）", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"fire_resist": True,
                 "innate_spells": {1: ["mage_hand"], 3: ["burning_hands"], 5: ["flame_blade"]},
                 "innate_frequency": "day"},
    },
    "tiefling_zariel": {
        "name": "提夫林·扎瑞尔", "en": "Zariel Tiefling", "family": "提夫林", "speed": 30, "skills": 0,
        "default_bonuses": {"cha": 2, "str": 1},
        "traits": ["地狱抗性（火焰伤害减半）", "阿弗纳斯血统（奇术/炽焰斩@3/灼烙印记@5，1/天）", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"fire_resist": True,
                 "innate_spells": {1: ["thaumaturgy"], 3: ["searing_smite"], 5: ["branding_smite"]},
                 "innate_frequency": "day"},
    },
    "half_orc": {
        "name": "半兽人", "en": "Half-Orc", "family": "半兽人", "speed": 30, "skills": 0,
        "trait_skills": ["intimidation"],
        "default_bonuses": {"str": 2, "con": 1},
        "traits": ["野蛮重击（近战暴击时额外一颗伤害骰）", "不屈坚韧（每长休1次、倒下时保留1点生命）", "威吓熟练", "黑暗视觉60尺"],
        "darkvision": 60,
        "mech": {"savage_attacks": True, "relentless_endurance": True},
    },
    "githyanki": {
        "name": "吉斯洋基人", "en": "Githyanki", "family": "吉斯洋基人", "speed": 30, "skills": 0,
        "default_bonuses": {"str": 2, "int": 1},
        "traits": ["星界知识（每次长休获得一项属性的全部技能熟练）", "吉斯洋基灵能（法师之手/跳跃术/迷踪步，1/天）"],
        "darkvision": 0,
        "mech": {"astral_knowledge": True,
                 "innate_spells": {1: ["mage_hand"], 2: ["misty_step"]},
                 "innate_frequency": "day"},
    },
}

# 龙裔 10 个亚种（BG3）：抗性与龙息成对出现（龙息由引擎按 5e 机制结算：
# 2d6（5级3d6），DC = 8 + 熟练 + 体质，短休/长休恢复一次）
_DRAGONBORN = {
    "black":  ("黑龙裔", "Black Dragonborn", "强酸", "acid", "强酸", "5×30尺线形"),
    "blue":   ("蓝龙裔", "Blue Dragonborn", "闪电", "lightning", "闪电", "5×30尺线形"),
    "brass":  ("黄铜龙裔", "Brass Dragonborn", "火焰", "fire", "火焰", "5×30尺线形"),
    "bronze": ("青铜龙裔", "Bronze Dragonborn", "闪电", "lightning", "闪电", "5×30尺线形"),
    "copper": ("红铜龙裔", "Copper Dragonborn", "强酸", "acid", "强酸", "5×30尺线形"),
    "gold":   ("金龙裔", "Gold Dragonborn", "火焰", "fire", "火焰", "15尺锥形"),
    "green":  ("绿龙裔", "Green Dragonborn", "毒素", "poison", "毒素", "15尺锥形"),
    "red":    ("红龙裔", "Red Dragonborn", "火焰", "fire", "火焰", "15尺锥形"),
    "silver": ("银龙裔", "Silver Dragonborn", "冷冻", "cold", "寒霜", "15尺锥形"),
    "white":  ("白龙裔", "White Dragonborn", "冷冻", "cold", "寒霜", "15尺锥形"),
}
for _k, (_n, _e, _res, _rdtype, _breath, _shape) in _DRAGONBORN.items():
    RACES[f"dragonborn_{_k}"] = {
        "name": _n, "en": _e, "family": "龙裔", "speed": 30, "skills": 0,
        "default_bonuses": {"str": 2, "cha": 1},
        "traits": [f"龙族血统（{_res}伤害减半）",
                   f"{_breath}吐息（{_shape}，2d6，DC 8+熟练+体质，短休恢复）"],
        "darkvision": 0,
        "mech": {f"{_rdtype}_resist": True,
                 "breath_weapon": {"dtype": _rdtype, "shape": _shape,
                                   "dice1": "2d6", "dice5": "3d6"}},
    }

_MECH_OF = {
    "dragonborn_fire": ("fire_resist",), "dragonborn_acid": ("acid_resist",),
    "dragonborn_lightning": ("lightning_resist",), "dragonborn_cold": ("cold_resist",),
    "dragonborn_poison": ("poison_resist",),
}


def resolve_bonuses(race: str, bonus2: str | None = None,
                    bonus1: str | None = None) -> dict[str, int]:
    """种族加值：任意两项属性 +2/+1（自选，需为不同属性）。

    采用官方可选规则（Tasha's 万能起源 / 2024 玩家手册）；未显式指定时
    退回该种族的 default_bonuses（引擎默认取法）。
    """
    info = RACES[race]
    if not bonus2 and not bonus1:
        return dict(info.get("default_bonuses", {}))
    out: dict[str, int] = {}
    for key, val in ((bonus2, 2), (bonus1, 1)):
        if key:
            if key not in ABILITIES:
                raise ValueError(f"种族加值属性非法：{key}")
            if key in out:
                raise ValueError("种族加值必须分配给两项不同属性")
            out[key] = val
    return out


def race_mech(race: str) -> dict:
    """种族的机制键集合（引擎消费）。"""
    return dict(RACES.get(race, {}).get("mech", {}))


# ---------------------------------------------------------------- 职业（12 个，PHB 2014 完整机制）
# resources 键（引擎实现）：
#   second_wind / action_surge / ki / rage / sorcery_points / bardic_inspiration
#   wild_shape / channel_divinity / lay_on_hands / superiority_dice / breath_weapon
# spellcasting.type: full（满施法者表）| half（半施法者表）| pact（魔契表，短休恢复）
# extra_attack: 获得额外攻击的等级
# subclass: {获得等级: {选项key: 机制效果}}
CLASSES: dict[str, dict] = {
    "barbarian": {
        "name": "野蛮人", "en": "Barbarian", "hit_die": 12,
        "saves": ["str", "con"], "armor": "none", "shield": False,
        "unarmored_def": "con",
        "skill_choices": ["animal_handling", "athletics", "intimidation", "nature", "perception", "survival"], "skill_count": 2,
        "weapons": ["greataxe", "handaxe", "handaxe", "light_crossbow"],
        "resources": {"rage": "uses_by_level", "reckless": "toggle"},
        "extra_attack": 5,
        "features": {
            "1": ["狂暴（附赠动作，1-2级2次/长休：力量近战伤害+2、钝器/穿刺/挥砍抗性、禁施法）",
                  "无甲防御（AC=10+敏捷+体质）"],
            "2": ["鲁莽攻击（每回合可选：本回合力量近战攻击优势，敌人对你也优势）",
                  "危险感（未失能时敏捷豁免优势）"],
            "3": ["本源之路（创建/升级时选择）：狂战士（狂暴期间每回合附赠动作一次近战，狂暴后力竭+1级）或 图腾战士·熊（狂暴抗性扩展为除心灵外全部伤害）"],
            "5": ["额外攻击（攻击动作可攻击两次）", "迅捷移动（未穿重甲移速+10尺）", "强蛮重击（近战暴击额外+1颗伤害骰）"],
        },
        "spellcasting": None,
    },
    "bard": {
        "name": "吟游诗人", "en": "Bard", "hit_die": 8,
        "saves": ["dex", "cha"], "armor": "leather", "shield": False,
        "skill_choices": list(SKILLS), "skill_count": 3,
        "weapons": ["rapier", "dagger", "light_crossbow"],
        "resources": {"bardic_inspiration": "cha_mod_uses", "cutting_words": "reaction"},
        "features": {
            "1": ["激励（附赠动作：给予友方1颗激励骰，10分钟内可加到一次攻击/检定/豁免；次数=魅力调整值/长休）",
                  "仪式施法"],
            "2": ["万事通（未熟练的属性检定加一半熟练加值）", "休憩之歌（短休回复时额外+1d6）"],
            "3": ["专精（2项技能熟练翻倍）", "知识学院：诛心之言（反应：敌人攻击骰/伤害骰 −1d6）"],
            "5": ["激励骰升级为d8；泉涌激励（激励次数短休恢复）", "3环法术位"],
        },
        "spellcasting": {"ability": "cha", "type": "full"},
    },
    "cleric": {
        "name": "牧师", "en": "Cleric", "hit_die": 8,
        "saves": ["wis", "cha"], "skill_choices": ["history", "insight", "medicine", "persuasion", "religion"], "skill_count": 2,
        "armor": "chain_shirt", "shield": True,
        "weapons": ["mace", "warhammer", "dagger"],
        "resources": {"channel_divinity": "short_rest", "preserve_life": "pool"},
        "features": {
            "1": ["领域：生命领域（中甲+盾牌熟练、生命门徒：治疗法术额外回复2+环阶）"],
            "2": ["引导神力（1/短休）：驱散不死者（不死生物感知豁免 DC 8+熟练+感知，失败恐惧你1分钟，CR≤其上限者被摧毁）或 生命守护（分配 5×等级 点治疗池）"],
            "3": [],
            "5": ["摧毁不死者：驱散时 CR≤1/2 的不死者直接被摧毁", "3环法术位"],
        },
        "spellcasting": {"ability": "wis", "type": "full"},
    },
    "druid": {
        "name": "德鲁伊", "en": "Druid", "hit_die": 8,
        "saves": ["int", "wis"], "skill_choices": ["arcana", "animal_handling", "insight", "medicine", "nature", "perception", "religion", "survival"], "skill_count": 2,
        "armor": "leather", "shield": True,
        "weapons": ["quarterstaff", "dagger", "scimitar"],
        "resources": {"wild_shape": "short_rest_2"},
        "features": {
            "1": ["德鲁伊语", "荒野身份"],
            "2": ["荒野变形（2/短休：变为 CR≤1/4 的野兽，4级起 CR≤1/2 且可水生；动物形态拥有其HP与攻击）",
                  "大地结社：自然回复（短休恢复一个 ≤半级环阶 的法术位）、结社法术"],
            "4": ["荒野变形升级：CR≤1/2、可水生形态"],
            "5": ["3环法术位"],
        },
        "spellcasting": {"ability": "wis", "type": "full"},
    },
    "fighter": {
        "name": "战士", "en": "Fighter", "hit_die": 10,
        "saves": ["str", "con"], "skill_choices": ["acrobatics", "animal_handling", "athletics", "history", "insight", "intimidation", "perception", "survival"], "skill_count": 2,
        "armor": "chain_mail", "shield": True,
        "weapons": ["longsword", "handaxe", "light_crossbow"],
        "resources": {"second_wind": "short_rest", "action_surge": "short_rest",
                      "superiority_dice": "short_rest_4"},
        "extra_attack": 5,
        "features": {
            "1": ["战斗风格：箭术/防御/对决/双武器（创建时选择）", "回气（附赠动作：1d10+等级 治疗，1/短休）"],
            "2": ["动作如潮（1/短休：本回合获得一个额外的动作）"],
            "3": ["武技流派：冠军（19-20 即暴击）或 战斗大师（4颗战技骰d8/短休：精确攻击/推撞攻击/恐吓攻击）"],
            "5": ["额外攻击（攻击动作可攻击两次）"],
        },
        "spellcasting": None,
    },
    "monk": {
        "name": "武僧", "en": "Monk", "hit_die": 8,
        "saves": ["str", "dex"], "armor": "none", "shield": False,
        "unarmored_def": "wis",
        "skill_choices": ["acrobatics", "athletics", "history", "insight", "religion", "stealth"], "skill_count": 2,
        "weapons": ["shortsword", "shortsword", "dagger"],
        "resources": {"ki": "level_short_rest"},
        "extra_attack": 5,
        "features": {
            "1": ["武术（徒手/武僧武器伤害骰 1d4，可用敏捷；攻击动作后可附赠动作徒手打击）",
                  "无甲防御（AC=10+敏捷+感知）"],
            "2": ["气（气点=等级，短休恢复）：疾风连击（1气：附赠动作2次徒手）/ 灵巧防御（1气：附赠动作防御）/ 风行步（1气：附赠动作撤离或疾走）",
                  "无甲移动（+10尺）"],
            "3": ["开掌道：开掌技巧（疾风连击时可令目标力量豁免否则倒地/被推开/无法反应）、周身调和（1/长休回复 3×等级 生命）"],
            "4": ["缓坠（反应，坠落伤害减免——叙事呈现）", "定心（自行解除魅惑/恐惧）"],
            "5": ["额外攻击（攻击动作可攻击两次）", "震慑拳（命中后1气：体质豁免 DC 8+熟练+感知，失败震慑至你下回合结束）", "武术骰升级为d6"],
        },
        "spellcasting": None,
    },
    "paladin": {
        "name": "圣武士", "en": "Paladin", "hit_die": 10,
        "saves": ["wis", "cha"], "skill_choices": ["athletics", "insight", "intimidation", "medicine", "persuasion", "religion"], "skill_count": 2,
        "armor": "chain_mail", "shield": True,
        "weapons": ["longsword", "handaxe", "light_crossbow"],
        "resources": {"lay_on_hands": "5x_level", "channel_divinity": "short_rest"},
        "extra_attack": 5,
        "features": {
            "1": ["圣疗（圣疗池=5×等级生命；可用5点治愈疾病/毒素）", "神威（察觉邪恶——叙事呈现）"],
            "2": ["战斗风格：对决/防御/双武器（升级时选择）", "圣术（施法·半施法者）", "神佑斩（命中后消耗法术位：+2d8光耀，环阶每高1级+1d8，对不死/魔鬼再+1d8，上限5d8）"],
            "3": ["神圣健康（免疫疾病）", "奉献之誓：神圣武器（引导神力：武器+魅力攻击加值并散发光亮1分钟）或 驱散邪秽（魔鬼/不死生物感知豁免否则恐惧你1分钟）"],
            "5": ["额外攻击（攻击动作可攻击两次）"],
        },
        "spellcasting": {"ability": "cha", "type": "half"},
    },
    "ranger": {
        "name": "游侠", "en": "Ranger", "hit_die": 10,
        "saves": ["str", "dex"], "skill_choices": ["animal_handling", "athletics", "insight", "investigation", "nature", "perception", "stealth", "survival"], "skill_count": 3,
        "armor": "leather", "shield": False,
        "weapons": ["longbow", "shortsword", "dagger"],
        "resources": {},
        "extra_attack": 5,
        "features": {
            "1": ["宿敌（相关检定优势）", "自然探索者（旅行时优势）"],
            "2": ["战斗风格：箭术/防御/双武器（升级时选择）", "施法（半施法者）"],
            "3": ["猎人猎物：巨兽杀手（每回合一次对已受伤目标+1d8）或 破阵者（对贴身不同目标可额外攻击一次）", "太古知觉（叙事呈现）"],
            "5": ["额外攻击（攻击动作可攻击两次）"],
        },
        "spellcasting": {"ability": "wis", "type": "half"},
    },
    "rogue": {
        "name": "游荡者", "en": "Rogue", "hit_die": 8,
        "saves": ["dex", "int"], "skill_choices": ["acrobatics", "athletics", "deception", "insight", "intimidation", "investigation", "perception", "performance", "persuasion", "sleight_of_hand", "stealth"], "skill_count": 4,
        "armor": "leather", "shield": False,
        "weapons": ["shortsword", "dagger", "shortbow"],
        "resources": {"cunning_action": "bonus", "uncanny_dodge": "reaction"},
        "features": {
            "1": ["偷袭（每回合一次：1d6，需优势或目标近身有你队友；自身处于劣势时不可用；每2级+1d6）",
                  "盗贼黑话", "专精（2项技能熟练翻倍）"],
            "2": ["狡诈动作（附赠动作：疾走/撤离/躲藏）"],
            "3": ["盗贼流派：盗贼（快手：附赠动作使用物品/巧手/开门）或 刺客（ assassination：对未行动过的目标攻击优势，对被突袭目标必暴击）"],
            "5": ["灵活闪避（反应：一次攻击的伤害减半）", "偷袭升级 3d6"],
        },
        "spellcasting": None,
    },
    "sorcerer": {
        "name": "术士", "en": "Sorcerer", "hit_die": 6,
        "saves": ["con", "cha"], "armor": "none", "shield": False,
        "skill_choices": ["arcana", "deception", "insight", "intimidation", "persuasion", "religion"], "skill_count": 2,
        "weapons": ["dagger", "quarterstaff", "light_crossbow"],
        "resources": {"sorcery_points": "level"},
        "features": {
            "1": ["源法血统：龙脉（龙族韧性：无甲时 AC=13+敏捷、每级生命+1）或 混沌原力（叙事呈现）"],
            "2": ["泉涌（术法点=等级；附赠动作：1点换1个1~5环法术位，或法术位换点）"],
            "3": ["超魔（选2）：疾咒（1点：动作法术改附赠动作施放）/ 强咒（3点：目标豁免劣势）/ 谨咒（1点：AOE 中队友豁免自动成功）"],
            "5": ["3环法术位"],
        },
        "spellcasting": {"ability": "cha", "type": "full"},
    },
    "warlock": {
        "name": "邪术师", "en": "Warlock", "hit_die": 8,
        "saves": ["wis", "cha"], "armor": "leather", "shield": False,
        "skill_choices": ["arcana", "deception", "history", "intimidation", "investigation", "nature", "religion"], "skill_count": 2,
        "weapons": ["dagger", "quarterstaff", "light_crossbow"],
        "resources": {},
        "features": {
            "1": ["魔契（与异界存在缔约）", "邪主眷佑（魔鬼系：击杀敌人获得 魅力调整+等级 点临时生命）", "魔契法术位（全部同环阶，短休恢复）"],
            "2": ["魔契恩泽（选2）：剧痛魔爆（魔能爆伤害+魅力调整）/ 魔鬼之视（黑暗视觉120尺，黑暗中无视魔法黑暗）/ 击退魔爆（魔能爆命中推开10尺）", "奥术狡诈（1/长休：短休恢复全部魔契法术位）"],
            "3": ["魔契恩赐：魔剑（可用魅力进行武器攻击）或 魔典/魔链"],
            "5": ["魔能爆 2 光束", "魔契法术位升级为2个3环"],
        },
        "spellcasting": {"ability": "cha", "type": "pact"},
    },
    "wizard": {
        "name": "法师", "en": "Wizard", "hit_die": 6,
        "saves": ["int", "wis"], "skill_choices": ["arcana", "history", "insight", "investigation", "medicine", "religion"], "skill_count": 2,
        "armor": None, "shield": False,
        "weapons": ["quarterstaff", "dagger"],
        "resources": {},
        "features": {
            "1": ["奥术回复（短休恢复合计 ≤ 半级（向上取整）环阶 的法术位）", "法术书"],
            "2": ["奥术传承：塑能（塑法：你的塑能AOE中队友豁免自动成功、伤害为0）或 防护（奥术护盾）"],
            "3": [],
            "5": ["3环法术位"],
        },
        "spellcasting": {"ability": "int", "type": "full"},
    },
}

# 魔契法术位（邪术师）：[数量, 环阶]，等级1-5，短休恢复
PACT_MAGIC_SLOTS = {1: (1, 1), 2: (2, 1), 3: (2, 2), 4: (2, 2), 5: (2, 3)}

# 经验表（等级1-5）
XP_TABLE = {1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500}
MAX_LEVEL = 5

# 熟练加值
PROF_BONUS = {1: 2, 2: 2, 3: 2, 4: 2, 5: 3}

# 满施法者法术位表 [1环,2环,3环]（等级1-5）
FULL_CASTER_SLOTS = {
    1: [2, 0, 0], 2: [3, 0, 0], 3: [4, 2, 0], 4: [4, 3, 0], 5: [4, 3, 2],
}
HALF_CASTER_SLOTS = {
    1: [0, 0, 0], 2: [2, 0, 0], 3: [3, 0, 0], 4: [3, 0, 0], 5: [4, 2, 0],
}

# 力竭（PHB 2014 六级）
EXHAUSTION_LEVELS = {
    1: "属性检定劣势",
    2: "移速减半",
    3: "攻击与豁免劣势",
    4: "生命上限减半",
    5: "移速归零",
    6: "死亡",
}

# ---------------------------------------------------------------- 出身（BG3 可选的 11 个）
BACKGROUNDS: dict[str, dict] = {
    "acolyte":       {"name": "侍僧", "skills": ["insight", "religion"], "desc": "你在神殿中度过多年，侍奉某位神祇。"},
    "charlatan":     {"name": "骗子", "skills": ["deception", "sleight_of_hand"], "desc": "你靠一张巧嘴和更巧的手过活。"},
    "criminal":      {"name": "罪犯", "skills": ["deception", "stealth"], "desc": "你有一段见不得光的过去。"},
    "entertainer":   {"name": "艺人", "skills": ["acrobatics", "performance"], "desc": "你的舞台是酒馆与集市，掌声是你的干粮。"},
    "folk_hero":     {"name": "民间英雄", "skills": ["animal_handling", "survival"], "desc": "你曾是拯救故乡于危难的凡人。"},
    "guild_artisan": {"name": "行会工匠", "skills": ["insight", "persuasion"], "desc": "你隶属某行会，手艺与账目同样精通。"},
    "noble":         {"name": "贵族", "skills": ["history", "persuasion"], "desc": "你生于显赫之家，家名即是通行证。"},
    "outlander":     {"name": "异乡人", "skills": ["athletics", "survival"], "desc": "文明于你是客店，荒野才是故乡。"},
    "sage":          {"name": "贤者", "skills": ["arcana", "history"], "desc": "你曾在烛堡的书堆中钻研秘法知识。"},
    "soldier":       {"name": "士兵", "skills": ["athletics", "intimidation"], "desc": "你曾服务于无冬城的卫戍部队。"},
    "urchin":        {"name": "街头流浪儿", "skills": ["sleight_of_hand", "stealth"], "desc": "城市的暗巷把你养大，教你要快、要轻、要无声。"},
}

# ---------------------------------------------------------------- 装备
WEAPONS: dict[str, dict] = {
    "dagger":        {"name": "匕首", "dmg": "1d4", "type": "piercing", "props": ["finesse", "light", "thrown(20/60)"], "ranged": False},
    "handaxe":       {"name": "手斧", "dmg": "1d6", "type": "slashing", "props": ["light", "thrown(20/60)"], "ranged": False},
    "mace":          {"name": "硬头锤", "dmg": "1d6", "type": "bludgeoning", "props": [], "ranged": False},
    "quarterstaff":  {"name": "木棍", "dmg": "1d6", "type": "bludgeoning", "props": ["versatile(1d8)", "monk"], "ranged": False},
    "shortsword":    {"name": "短剑", "dmg": "1d6", "type": "piercing", "props": ["finesse", "light", "monk"], "ranged": False},
    "longsword":     {"name": "长剑", "dmg": "1d8", "type": "slashing", "props": ["versatile(1d10)"], "ranged": False},
    "greatsword":    {"name": "大剑", "dmg": "2d6", "type": "slashing", "props": ["heavy", "two_handed"], "ranged": False},
    "scimitar":      {"name": "弯刀", "dmg": "1d6", "type": "slashing", "props": ["finesse", "light"], "ranged": False},
    "rapier":        {"name": "刺剑", "dmg": "1d8", "type": "piercing", "props": ["finesse"], "ranged": False},
    "morningstar":   {"name": "钉锤", "dmg": "1d8", "type": "piercing", "props": [], "ranged": False},
    "greataxe":      {"name": "巨斧", "dmg": "1d12", "type": "slashing", "props": ["heavy", "two_handed"], "ranged": False},
    "warhammer":     {"name": "战锤", "dmg": "1d8", "type": "bludgeoning", "props": ["versatile(1d10)"], "ranged": False},
    "spear":         {"name": "长矛", "dmg": "1d6", "type": "piercing", "props": ["thrown(20/60)", "versatile(1d8)"], "ranged": False},
    "shortbow":      {"name": "短弓", "dmg": "1d6", "type": "piercing", "props": ["ammunition", "two_handed"], "ranged": True, "range": "80/320"},
    "longbow":       {"name": "长弓", "dmg": "1d8", "type": "piercing", "props": ["ammunition", "heavy", "two_handed"], "ranged": True, "range": "150/600"},
    "light_crossbow": {"name": "轻弩", "dmg": "1d8", "type": "piercing", "props": ["ammunition", "two_handed"], "ranged": True, "range": "80/320"},
    "sling":         {"name": "投石索", "dmg": "1d4", "type": "bludgeoning", "props": ["ammunition"], "ranged": True, "range": "30/120"},
}

ARMORS: dict[str, dict] = {
    "none":        {"name": "无甲", "ac": 0, "dex_cap": None, "str_req": 0, "stealth_dis": False},
    "padded":      {"name": "布甲", "ac": 11, "dex_cap": None, "str_req": 0, "stealth_dis": True},
    "leather":     {"name": "皮甲", "ac": 11, "dex_cap": None, "str_req": 0, "stealth_dis": False},
    "studded":     {"name": "镶钉皮甲", "ac": 12, "dex_cap": None, "str_req": 0, "stealth_dis": False},
    "chain_shirt": {"name": "链甲衫", "ac": 13, "dex_cap": 2, "str_req": 0, "stealth_dis": False},
    "chain_mail":  {"name": "链甲", "ac": 16, "dex_cap": 0, "str_req": 13, "stealth_dis": True},
    "scale_mail":  {"name": "鳞甲", "ac": 14, "dex_cap": 2, "str_req": 0, "stealth_dis": True},
}

# 物品引擎字段：heal=饮用回复表达式；stabilize+uses=医疗包稳定；mech=持有即生效的机制键
ITEMS: dict[str, dict] = {
    "healing_potion": {"name": "治疗药水", "desc": "回复 2d4+2 生命值。", "heal": "2d4+2"},
    "potion_greater_healing": {"name": "上乘治疗药水", "desc": "回复 4d4+4 生命值。", "heal": "4d4+4"},
    "healer_kit": {"name": "医疗包", "desc": "10 次使用：以动作稳定倒地队友（无需检定）。", "stabilize": True},
    "torch":          {"name": "火把", "desc": "照明 60 尺，燃烧 1 小时。"},
    "rope":           {"name": "麻绳", "desc": "50 尺。"},
    "gold":           {"name": "金币", "desc": "通用货币。"},
    "star_forge_blade": {"name": "星炉长剑(+1)", "desc": "星炉锻造的魔法长剑，攻击与伤害+1。", "magic": {"attack_bonus": 1, "damage_bonus": 1}},
    "watchers_blade": {"name": "蓝望之刃(+1)", "desc": "蓝望军指挥官的魔法长剑（单手1d10/双手1d12），攻击与伤害+1。", "magic": {"attack_bonus": 1, "damage_bonus": 1}},
    "ember_ward_charm": {"name": "余烬护符", "desc": "灰烬锻纹护符：持有者获得火焰伤害抗性。", "mech": {"fire_resist": True}},
}

# ---------------------------------------------------------------- 法术
# kind: attack（法术攻击）/ save（豁免）/ auto（自动命中）/ heal / buff / cleanse /
#       cleanse_magical / counter（反应反制）/ reaction（受击触发）
# save: 豁免属性; half: 成功减半; concentration: 专注; bonus_action/reaction: 施法动作类型
SPELLS: dict[str, dict] = {
    # ---------------- 戏法（5级时伤害骰翻倍由引擎实现）
    "fire_bolt":     {"name": "火焰箭", "en": "Fire Bolt", "level": 0, "classes": ["wizard", "sorcerer"], "kind": "attack", "dmg": "1d10", "dtype": "fire", "range_ft": 120},
    "ray_of_frost":  {"name": "冷冻射线", "en": "Ray of Frost", "level": 0, "classes": ["wizard", "sorcerer"], "kind": "attack", "dmg": "1d8", "dtype": "cold", "range_ft": 60},
    "produce_flame": {"name": "燃火术", "en": "Produce Flame", "level": 0, "classes": ["druid"], "kind": "attack", "dmg": "1d8", "dtype": "fire", "range_ft": 30},
    "eldritch_blast": {"name": "魔能爆", "en": "Eldritch Blast", "level": 0, "classes": ["warlock"], "kind": "attack", "dmg": "1d10", "dtype": "force", "range_ft": 120,
                       "beams": "eldritch"},
    "sacred_flame":  {"name": "神圣火焰", "en": "Sacred Flame", "level": 0, "classes": ["cleric"], "kind": "save", "save": "dex", "dmg": "1d8", "dtype": "radiant", "range_ft": 60},
    "vicious_mockery": {"name": "恶性嘲讽", "en": "Vicious Mockery", "level": 0, "classes": ["bard"], "kind": "save", "save": "wis", "dmg": "1d4", "dtype": "psychic", "range_ft": 60},
    "thorn_whip":    {"name": "荆棘鞭", "en": "Thorn Whip", "level": 0, "classes": ["druid"], "kind": "attack", "dmg": "1d6", "dtype": "piercing", "range_ft": 30, "melee_spell": True, "pull": True},
    "dancing_lights": {"name": "舞光术", "en": "Dancing Lights", "level": 0, "classes": [], "kind": "light", "range_ft": 120},
    "mage_hand":     {"name": "法师之手", "en": "Mage Hand", "level": 0, "classes": [], "kind": "utility", "range_ft": 30},
    "thaumaturgy":   {"name": "奇术", "en": "Thaumaturgy", "level": 0, "classes": [], "kind": "utility", "range_ft": 30},
    "light":         {"name": "光亮术", "en": "Light", "level": 0, "classes": ["wizard", "cleric"], "kind": "light", "range_ft": 0},
    "guidance":      {"name": "神导术", "en": "Guidance", "level": 0, "classes": ["cleric", "druid"], "kind": "utility", "range_ft": 0},
    # ---------------- 1环
    "magic_missile": {"name": "魔法飞弹", "en": "Magic Missile", "level": 1, "classes": ["wizard", "sorcerer"], "kind": "auto", "dmg": "1d4+1", "dtype": "force", "count": 3, "per_level_extra": 1, "range_ft": 120},
    "burning_hands": {"name": "燃烧之手", "en": "Burning Hands", "level": 1, "classes": ["wizard", "sorcerer"], "kind": "save", "save": "dex", "dmg": "3d6", "dtype": "fire", "half": True, "range_ft": 15, "aoe": True, "spell_level": 1},
    "thunderwave":   {"name": "雷鸣波", "en": "Thunderwave", "level": 1, "classes": ["bard", "druid", "sorcerer", "wizard"], "kind": "save", "save": "con", "dmg": "2d8", "dtype": "thunder", "half": True, "range_ft": 15, "aoe": True, "push": True},
    "hex":           {"name": "噬魂咒", "en": "Hex", "level": 1, "classes": ["warlock"], "kind": "buff", "effect": "mark+1d6", "concentration": True, "bonus_action": True, "range_ft": 90},
    "shield":        {"name": "护盾术", "en": "Shield", "level": 1, "classes": ["wizard", "sorcerer"], "kind": "buff", "effect": "ac+5", "duration_rounds": 1, "reaction": True, "range_ft": 0, "spell_level": 1},
    "sleep":         {"name": "睡眠术", "en": "Sleep", "level": 1, "classes": ["wizard", "sorcerer"], "kind": "auto", "effect": "sleep_hp", "dmg": "5d8", "dtype": "sleep", "range_ft": 90, "aoe": True},
    "cure_wounds":   {"name": "疗伤术", "en": "Cure Wounds", "level": 1, "classes": ["cleric", "druid", "bard", "paladin", "ranger"], "kind": "heal", "dmg": "1d8", "per_level_extra": 1, "touch": True},
    "healing_word":  {"name": "治疗真言", "en": "Healing Word", "level": 1, "classes": ["cleric", "druid", "bard"], "kind": "heal", "dmg": "1d4", "per_level_extra": 1, "bonus_action": True, "range_ft": 60},
    "bless":         {"name": "祝福术", "en": "Bless", "level": 1, "classes": ["cleric", "paladin"], "kind": "buff", "effect": "attack+1d4", "concentration": True, "targets": 3, "range_ft": 30},
    "hunters_mark":  {"name": "猎人印记", "en": "Hunter's Mark", "level": 1, "classes": ["ranger"], "kind": "buff", "effect": "mark+1d6", "concentration": True, "bonus_action": True, "range_ft": 90},
    "guiding_bolt":  {"name": "引导射击", "en": "Guiding Bolt", "level": 1, "classes": ["cleric"], "kind": "attack", "dmg": "4d6", "dtype": "radiant", "range_ft": 120, "guides_next": True},
    "shield_of_faith": {"name": "信仰护盾", "en": "Shield of Faith", "level": 1, "classes": ["cleric", "paladin"], "kind": "buff", "effect": "ac+2", "concentration": True, "range_ft": 60},
    "dissonant_whispers": {"name": "不谐低语", "en": "Dissonant Whispers", "level": 1, "classes": ["bard"], "kind": "save", "save": "wis", "dmg": "3d10", "dtype": "psychic", "half": True, "range_ft": 60, "frightens": True},
    "hellish_rebuke": {"name": "狱火斥喝", "en": "Hellish Rebuke", "level": 1, "classes": ["warlock"], "kind": "reaction", "save": "dex", "dmg": "2d10", "dtype": "fire", "half": True, "range_ft": 60, "per_level_extra_dmg": 1},
    "searing_smite": {"name": "炽焰斩", "en": "Searing Smite", "level": 1, "classes": [], "kind": "buff", "effect": "weapon+1d6fire", "concentration": True, "bonus_action": True, "range_ft": 0},
    "branding_smite": {"name": "灼烙印记", "en": "Branding Smite", "level": 2, "classes": [], "kind": "buff", "effect": "weapon+2d6radiant", "concentration": True, "bonus_action": True, "range_ft": 0},
    "flame_blade":   {"name": "烈焰之刃", "en": "Flame Blade", "level": 2, "classes": [], "kind": "buff", "effect": "flame_blade", "bonus_action": True, "range_ft": 0},
    "faerie_fire":   {"name": "妖火术", "en": "Faerie Fire", "level": 1, "classes": ["bard", "druid"], "kind": "save_area", "save": "dex", "dmg": "0", "dtype": "none", "range_ft": 60, "aoe": True, "effect": "outlined", "concentration": True},
    "entangle":      {"name": "缠绕术", "en": "Entangle", "level": 1, "classes": ["druid"], "kind": "save_area", "save": "str", "dmg": "0", "dtype": "none", "range_ft": 90, "aoe": True, "effect": "restrained", "concentration": True},
    "invisibility":  {"name": "隐形术", "en": "Invisibility", "level": 2, "classes": ["wizard", "sorcerer", "warlock"], "kind": "buff", "effect": "invisible", "concentration": True, "touch": True},
    "hold_person":   {"name": "定身人类", "en": "Hold Person", "level": 2, "classes": ["bard", "cleric", "druid", "sorcerer", "warlock", "wizard"], "kind": "save_area", "save": "wis", "dmg": "0", "dtype": "none", "range_ft": 60, "effect": "paralyzed", "concentration": True, "humanoid_only": True},
    "misty_step":    {"name": "迷踪步", "en": "Misty Step", "level": 2, "classes": ["sorcerer", "warlock", "wizard"], "kind": "buff", "effect": "teleport", "bonus_action": True, "range_ft": 0},
    "scorching_ray": {"name": "炽焰射线", "en": "Scorching Ray", "level": 2, "classes": ["sorcerer", "wizard"], "kind": "attack", "dmg": "2d6", "dtype": "fire", "range_ft": 120, "rays": 3, "per_level_extra_rays": 1},
    "mirror_image":  {"name": "镜影术", "en": "Mirror Image", "level": 2, "classes": ["sorcerer", "warlock", "wizard"], "kind": "buff", "effect": "mirror_image", "range_ft": 0},
    "lesser_restoration": {"name": "次级复原术", "en": "Lesser Restoration", "level": 2, "classes": ["cleric", "druid", "bard", "paladin", "ranger"], "kind": "cleanse", "touch": True},
    "darkness_spell": {"name": "黑暗术", "en": "Darkness", "level": 2, "classes": ["sorcerer", "warlock", "wizard"], "kind": "darkness", "range_ft": 60, "concentration": True},
    # ---------------- 3环
    "fireball":      {"name": "火球术", "en": "Fireball", "level": 3, "classes": ["wizard", "sorcerer"], "kind": "save", "save": "dex", "dmg": "8d6", "dtype": "fire", "half": True, "per_level_extra_dmg": 1, "range_ft": 150, "aoe": True, "spell_level": 3},
    "counterspell":  {"name": "反制法术", "en": "Counterspell", "level": 3, "classes": ["sorcerer", "warlock", "wizard"], "kind": "counter", "range_ft": 60},
    "dispel_magic":  {"name": "解除魔法", "en": "Dispel Magic", "level": 3, "classes": ["bard", "cleric", "druid", "paladin", "sorcerer", "warlock", "wizard"], "kind": "cleanse_magical", "range_ft": 120},
}

# ---------------------------------------------------------------- 专长（PHB 2014，4 级可选）
# 本内容范围（1-5 级）精选 4 项由引擎机制实现的招牌专长
FEATS: dict[str, dict] = {
    "great_weapon_master": {"name": "巨武器大师",
        "desc": "重型近战武器：攻击前可选 −5 命中/+10 伤害；重击或击倒目标后，可用附赠动作追加一次近战攻击。"},
    "sharpshooter": {"name": "神射手",
        "desc": "远程武器：攻击前可选 −5 命中/+10 伤害（本引擎无掩体/远距分档规则）。"},
    "war_caster": {"name": "战争施法者",
        "desc": "维持专注的体质豁免具有优势。"},
    "lucky": {"name": "幸运",
        "desc": "3 次/长休：d20 出 1~5 时自动重骰一次（攻击/豁免/检定均适用；以结果更优者结算）。"},
}

# 同伴 4 级专长的推荐默认
FEAT_DEFAULT = {
    "ranger": "sharpshooter", "paladin": "war_caster",
    "bard": "war_caster", "cleric": "war_caster", "druid": "war_caster",
    "sorcerer": "war_caster", "warlock": "war_caster", "wizard": "war_caster",
}

# 戏法 5 级伤害升级（SRD cantrip progression）
CANTRIP_SCALED_DICE = {  # 戏法key → (基础骰数, 骰面)
    "fire_bolt": (1, 10), "ray_of_frost": (1, 8), "produce_flame": (1, 8),
    "sacred_flame": (1, 8), "vicious_mockery": (1, 4), "thorn_whip": (1, 6),
}

# ---------------------------------------------------------------- 怪物（SRD 5.1 图鉴数值）
# attacks: [{weapon, bonus, dmg?, extra, multiattack?, onhit?}]
# traits 引擎关键字：
#   施法者（低血隔轮AOE）/ 突袭（首轮+2d6）/ 伏击（先攻+2）/ 不死者坚韧（DC5+伤害）
#   军团战术（队友贴身目标时+2d6）/ 饕餮狂暴（击杀后附赠噬咬）/ 格挡（反应AC+2）
#   冲锋（对未纠缠目标首击+1d6）/ 随时隐形（先攻时进入隐形，攻击后仍隐形）
#   借机潜袭（nimble escape：附赠动作撤离或躲藏）
# onhit 关键字：poison_<expr>_dc<n>（失败中毒）/ prone_dc<n>_str（扑倒）/
#   paralyze_dc<n>_con（麻痹）/ web_dc<n>（束缚）/ drain（生命汲取：减HP上限）
MONSTERS: dict[str, dict] = {
    "goblin": {
        "name": "哥布林", "en": "Goblin", "cr": 0.25, "xp": 50, "hp": "2d6", "ac": 15,
        "speed": 30, "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "traits": ["借机潜袭（附赠动作撤离或躲藏）"],
        "attacks": [{"weapon": "scimitar", "bonus": 4, "extra": "+2"}, {"weapon": "shortbow", "bonus": 4, "extra": "+2"}],
        "darkvision": 60,
    },
    "hobgoblin": {
        "name": "大地精", "en": "Hobgoblin", "cr": 0.5, "xp": 100, "hp": "2d8+2", "ac": 18,
        "speed": 30, "abilities": {"str": 14, "dex": 12, "con": 12, "int": 10, "wis": 10, "cha": 9},
        "traits": ["军团战术（队友贴身目标时+2d6）"],
        "attacks": [{"weapon": "longsword", "bonus": 3, "extra": "+1", "multiattack": 2}, {"weapon": "longbow", "bonus": 3, "extra": "+1", "multiattack": 2}],
        "darkvision": 60,
    },
    "worg": {
        "name": "座狼", "en": "Worg", "cr": 0.5, "xp": 100, "hp": "4d10+4", "ac": 13,
        "speed": 50, "abilities": {"str": 16, "dex": 13, "con": 13, "int": 7, "wis": 12, "cha": 8},
        "traits": ["敏锐听嗅", "通晓语言（听得懂通用语与地精语）"],
        "attacks": [{"weapon": "bite", "bonus": 5, "dmg": "2d6", "extra": "+4", "onhit": "prone_dc13_str"}],
        "darkvision": 60,
    },
    "gnoll": {
        "name": "豺狼人", "en": "Gnoll", "cr": 0.5, "xp": 100, "hp": "5d8", "ac": 15,
        "speed": 30, "abilities": {"str": 14, "dex": 12, "con": 11, "int": 6, "wis": 10, "cha": 7},
        "traits": ["饕餮狂暴（击倒敌人后附赠动作噬咬）"],
        "attacks": [{"weapon": "spear", "bonus": 4, "extra": "+2", "follow": "bite"}, {"weapon": "bite", "bonus": 4, "dmg": "1d4", "extra": "+2"}],
        "darkvision": 60,
    },
    "duergar": {
        "name": "灰矮人", "en": "Duergar", "cr": 1, "xp": 200, "hp": "4d8+8", "ac": 16,
        "speed": 25, "abilities": {"str": 14, "dex": 11, "con": 14, "int": 11, "wis": 10, "cha": 9},
        "traits": ["扩大术（1/天：近战伤害+1d4）", "幻术/魅惑/麻痹豁免优势", "灰矮人魔法（隐形术，1/天）"],
        "attacks": [{"weapon": "warhammer", "bonus": 4, "extra": "+2", "multiattack": 2}],
        "save_prof": {"con": True, "wis": True},
        "darkvision": 120,
    },
    "ettercap": {
        "name": "艾特卡普", "en": "Ettercap", "cr": 2, "xp": 450, "hp": "8d8+8", "ac": 13,
        "speed": 30, "abilities": {"str": 14, "dex": 15, "con": 13, "int": 5, "wis": 10, "cha": 4},
        "traits": ["蛛网行者", "蛛网感应", "多重攻击（噬咬+爪击）"],
        "attacks": [{"weapon": "bite", "bonus": 4, "dmg": "1d6", "extra": "+2", "onhit": "poison_1d8_dc11", "follow": "claws"},
                    {"weapon": "claws", "bonus": 4, "dmg": "1d4", "extra": "+2"},
                    {"weapon": "web", "bonus": 4, "dmg": "0", "extra": "+0", "onhit": "web_dc11"}],
        "darkvision": 60,
    },
    "giant_spider": {
        "name": "巨蛛", "en": "Giant Spider", "cr": 1, "xp": 200, "hp": "4d10+4", "ac": 14,
        "speed": 30, "abilities": {"str": 14, "dex": 16, "con": 12, "int": 2, "wis": 11, "cha": 4},
        "traits": ["蛛网行者", "蛛网感应", "蛛网（附赠动作，1/天）"],
        "attacks": [{"weapon": "bite", "bonus": 5, "dmg": "1d8", "extra": "+3", "onhit": "poison_2d8_dc11"},
                    {"weapon": "web", "bonus": 5, "dmg": "0", "extra": "+0", "onhit": "web_dc12"}],
        "darkvision": 60,
    },
    "phase_spider": {
        "name": "相位蜘蛛", "en": "Phase Spider", "cr": 3, "xp": 700, "hp": "5d10+5", "ac": 13,
        "speed": 30, "abilities": {"str": 12, "dex": 15, "con": 12, "int": 7, "wis": 12, "cha": 6},
        "traits": ["相位跳转（以以太位面传送）", "蛛网行者"],
        "attacks": [{"weapon": "bite", "bonus": 4, "dmg": "1d6", "extra": "+3", "onhit": "poison_2d8_dc11"}],
        "darkvision": 90,
    },
    "redcap": {
        "name": "红帽子", "en": "Redcap", "cr": 3, "xp": 700, "hp": "10d8", "ac": 14,
        "speed": 40, "abilities": {"str": 16, "dex": 14, "con": 14, "int": 11, "wis": 10, "cha": 12},
        "traits": ["铁剪（多重攻击）", "帽中血沸（帽子浸满血，离帽即死）", "凶暴"],
        "attacks": [{"weapon": "scimitar", "bonus": 6, "dmg": "2d6", "extra": "+3", "multiattack": 2}],
        "darkvision": 0,
    },
    "hook_horror": {
        "name": "钩爪恐魔", "en": "Hook Horror", "cr": 2, "xp": 450, "hp": "10d10+20", "ac": 15,
        "speed": 30, "abilities": {"str": 18, "dex": 10, "con": 15, "int": 5, "wis": 12, "cha": 6},
        "traits": ["钩爪攀爬（沿岩壁攀行）", "回声定位（盲目时仍可感知）"],
        "attacks": [{"weapon": "claws", "bonus": 5, "dmg": "2d6", "extra": "+3", "multiattack": 2}],
        "darkvision": 120,
    },
    "owlbear": {
        "name": "枭熊", "en": "Owlbear", "cr": 3, "xp": 700, "hp": "7d10+21", "ac": 13,
        "speed": 40, "abilities": {"str": 20, "dex": 12, "con": 17, "int": 3, "wis": 12, "cha": 7},
        "traits": ["热感嗅视", "凶猛暴怒（受创后不退反进）"],
        "attacks": [{"weapon": "beak", "bonus": 7, "dmg": "1d10", "extra": "+5", "follow": "claws"},
                    {"weapon": "claws", "bonus": 7, "dmg": "2d8", "extra": "+5"}],
        "darkvision": 60,
    },
    "minotaur": {
        "name": "米诺陶", "en": "Minotaur", "cr": 3, "xp": 700, "hp": "9d12+22", "ac": 18,
        "speed": 40, "abilities": {"str": 18, "dex": 11, "con": 16, "int": 6, "wis": 12, "cha": 9},
        "traits": ["冲锋（对未贴身目标首击+1d6并扑倒 DC14 力量）", "天然护甲", "狂怒冲撞"],
        "attacks": [{"weapon": "greataxe", "bonus": 6, "dmg": "2d12", "extra": "+4"},
                    {"weapon": "gore", "bonus": 6, "dmg": "2d8", "extra": "+4", "onhit": "charge_prone_dc14_str"}],
        "darkvision": 60,
    },
    "bulette": {
        "name": "钻地魔虫", "en": "Bulette", "cr": 5, "xp": 1800, "hp": "9d12+36", "ac": 17,
        "speed": 40, "abilities": {"str": 19, "dex": 11, "con": 18, "int": 2, "wis": 10, "cha": 5},
        "traits": ["跳跃突袭（跃出地面扑倒猎物）", "掘地（潜行于岩土之下）", "颤动感知"],
        "attacks": [{"weapon": "bite", "bonus": 7, "dmg": "4d8", "extra": "+4"}],
        "darkvision": 60,
    },
    "skeleton": {
        "name": "骷髅", "en": "Skeleton", "cr": 0.25, "xp": 50, "hp": "2d8+4", "ac": 13,
        "speed": 30, "abilities": {"str": 10, "dex": 14, "con": 15, "int": 6, "wis": 8, "cha": 5},
        "traits": ["不死者（免疫中毒/力竭）"],
        "attacks": [{"weapon": "shortsword", "bonus": 4, "extra": "+2"}, {"weapon": "shortbow", "bonus": 4, "extra": "+2"}],
        "darkvision": 60,
    },
    "zombie": {
        "name": "丧尸", "en": "Zombie", "cr": 0.25, "xp": 50, "hp": "3d8+9", "ac": 8,
        "speed": 20, "abilities": {"str": 13, "dex": 6, "con": 16, "int": 3, "wis": 6, "cha": 5},
        "traits": ["不死者坚韧（被降至0HP时体质豁免DC5+伤害成功则以1HP存活）"],
        "attacks": [{"weapon": "slam", "bonus": 3, "dmg": "1d6", "extra": "+1"}],
        "save_prof": {"wis": True},
        "darkvision": 60,
    },
    "ghoul": {
        "name": "食尸鬼", "en": "Ghoul", "cr": 1, "xp": 200, "hp": "5d8", "ac": 12,
        "speed": 30, "abilities": {"str": 13, "dex": 15, "con": 10, "int": 7, "wis": 10, "cha": 6},
        "traits": ["麻痹之爪（命中后体质豁免DC10否则麻痹；精灵免疫）", "不死者"],
        "attacks": [{"weapon": "claws", "bonus": 2, "dmg": "2d4", "extra": "+0", "onhit": "paralyze_dc10_con", "follow": "bite"},
                    {"weapon": "bite", "bonus": 2, "dmg": "2d6", "extra": "+2"}],
        "save_prof": {"wis": True},
        "darkvision": 60,
    },
    "wight": {
        "name": "尸妖", "en": "Wight", "cr": 3, "xp": 700, "hp": "6d8+18", "ac": 14,
        "speed": 30, "abilities": {"str": 15, "dex": 14, "con": 16, "int": 10, "wis": 13, "cha": 15},
        "traits": ["不死者", "生命汲取（近战命中吸取生命并削减HP上限）", "日光过敏（日光下劣势）"],
        "attacks": [{"weapon": "longsword", "bonus": 4, "extra": "+2", "multiattack": 2, "onhit": "drain"}],
        "darkvision": 60,
    },
    "imp": {
        "name": "小恶魔", "en": "Imp", "cr": 1, "xp": 200, "hp": "3d6", "ac": 13,
        "speed": 20, "abilities": {"str": 6, "dex": 14, "con": 11, "int": 11, "wis": 12, "cha": 14},
        "traits": ["随时隐形（先攻时隐形；攻击不解除）", "变形（老鼠/乌鸦/蜘蛛）", "火焰抗性、毒素免疫", "诡诈"],
        "attacks": [{"weapon": "sting", "bonus": 5, "dmg": "1d4", "extra": "+3", "onhit": "poison_3d6_dc11"}],
        "darkvision": 120,
        "poison_immune": True,
        "fire_resist": True,
        "save_prof": {"dex": True},
    },
    "cambion": {
        "name": "坎比翁", "en": "Cambion", "cr": 5, "xp": 1800, "hp": "12d8+12", "ac": 17,
        "speed": 30, "abilities": {"str": 16, "dex": 14, "con": 14, "int": 14, "wis": 12, "cha": 16},
        "traits": ["施法者", "火焰/毒素抗性", "魔翼飞行", "地狱契约（受魔鬼君侯庇护）"],
        "attacks": [{"weapon": "fire_ray", "bonus": 7, "dmg": "2d6", "extra": "+3"},
                    {"weapon": "spell:fireball", "bonus": 0, "save_dc": 14, "save": "dex", "dmg": "8d6", "extra": "0", "spell_level": 3}],
        "darkvision": 120,
        "fire_resist": True,
        "poison_resist": True,
        "save_prof": {"con": True, "wis": True, "cha": True},
    },
    "bandit": {
        "name": "强盗", "en": "Bandit", "cr": 0.125, "xp": 25, "hp": "2d8+2", "ac": 12,
        "speed": 30, "abilities": {"str": 11, "dex": 12, "con": 12, "int": 10, "wis": 10, "cha": 10},
        "traits": [],
        "attacks": [{"weapon": "scimitar", "bonus": 3, "extra": "+1"}],
        "darkvision": 0,
    },
    "ogre": {
        "name": "食人魔", "en": "Ogre", "cr": 2, "xp": 450, "hp": "7d10+21", "ac": 11,
        "speed": 40, "abilities": {"str": 19, "dex": 8, "con": 16, "int": 5, "wis": 7, "cha": 7},
        "traits": ["黑暗视觉60尺"],
        "attacks": [{"weapon": "greatclub", "bonus": 6, "dmg": "2d8", "extra": "+4"}],
        "darkvision": 60,
    },
    "bandit_captain": {
        "name": "强盗头目", "en": "Bandit Captain", "cr": 2, "xp": 450, "hp": "10d8+20", "ac": 15,
        "speed": 30, "abilities": {"str": 15, "dex": 16, "con": 14, "int": 14, "wis": 11, "cha": 14},
        "traits": ["勇敢（恐惧豁免优势）", "格挡（反应：AC+2 可能令近战攻击落空）"],
        "attacks": [{"weapon": "scimitar", "bonus": 5, "extra": "+3", "multiattack": 2}, {"weapon": "dagger", "bonus": 5, "extra": "+3"}],
        "save_prof": {"str": True, "dex": True, "wis": True},
        "darkvision": 0,
    },
    "ash_mage": {
        "name": "灰烬之主", "en": "Ashen One", "cr": 2, "xp": 450, "hp": "9d8", "ac": 12,
        "speed": 30, "abilities": {"str": 9, "dex": 14, "con": 11, "int": 17, "wis": 13, "cha": 12},
        "traits": ["施法者", "暗黑祝福（死亡时爆发暗焰）"],
        "attacks": [
            {"weapon": "spell:fire_bolt", "bonus": 6, "dmg": "2d10", "extra": "0"},
            {"weapon": "spell:burning_hands", "bonus": 0, "save_dc": 14, "save": "dex", "dmg": "3d6", "extra": "0", "spell_level": 1},
        ],
        "darkvision": 0,
    },
    # ---------------- 野兽（野变形态 / 一般遭遇）
    "wolf": {
        "name": "狼", "en": "Wolf", "cr": 0.25, "xp": 50, "hp": "2d8+2", "ac": 15,
        "speed": 40, "abilities": {"str": 12, "dex": 15, "con": 12, "int": 3, "wis": 12, "cha": 6},
        "traits": ["成群狩猎（队友贴身目标时攻击优势）", "敏锐听嗅"],
        "attacks": [{"weapon": "bite", "bonus": 4, "dmg": "2d4", "extra": "+2", "onhit": "prone_dc11_str"}],
        "darkvision": 0, "beast": True,
    },
    "giant_badger": {
        "name": "巨獾", "en": "Giant Badger", "cr": 0.25, "xp": 50, "hp": "3d8", "ac": 13,
        "speed": 30, "abilities": {"str": 13, "dex": 10, "con": 15, "int": 2, "wis": 12, "cha": 5},
        "traits": ["敏锐嗅觉", "掘地"],
        "attacks": [{"weapon": "bite", "bonus": 3, "dmg": "1d6", "extra": "+1", "follow": "claws"},
                    {"weapon": "claws", "bonus": 3, "dmg": "2d4", "extra": "+1"}],
        "darkvision": 30, "beast": True,
    },
    "riding_horse": {
        "name": "乘骑马", "en": "Riding Horse", "cr": 0.25, "xp": 50, "hp": "3d10+3", "ac": 10,
        "speed": 60, "abilities": {"str": 16, "dex": 10, "con": 13, "int": 2, "wis": 11, "cha": 7},
        "traits": [],
        "attacks": [{"weapon": "hooves", "bonus": 5, "dmg": "1d4", "extra": "+3"}],
        "darkvision": 0, "beast": True,
    },
    "black_bear": {
        "name": "黑熊", "en": "Black Bear", "cr": 0.5, "xp": 100, "hp": "4d10+8", "ac": 12,
        "speed": 40, "abilities": {"str": 15, "dex": 14, "con": 18, "int": 2, "wis": 11, "cha": 7},
        "traits": ["敏锐嗅觉", "攀爬游泳"],
        "attacks": [{"weapon": "claws", "bonus": 4, "dmg": "1d8", "extra": "+2"},
                    {"weapon": "bite", "bonus": 4, "dmg": "1d6", "extra": "+2", "follow": "claws"}],
        "darkvision": 60, "beast": True,
    },
}

# 数据表未收录的怪物武器
MONSTER_WEAPONS: dict[str, dict] = {
    "bite":   {"name": "噬咬", "dmg": None, "type": "piercing", "props": [], "ranged": False},
    "claws":  {"name": "爪击", "dmg": None, "type": "slashing", "props": [], "ranged": False},
    "slam":   {"name": "挥击", "dmg": None, "type": "bludgeoning", "props": [], "ranged": False},
    "beak":   {"name": "喙击", "dmg": None, "type": "piercing", "props": [], "ranged": False},
    "sting":  {"name": "螫刺", "dmg": None, "type": "piercing", "props": [], "ranged": False},
    "gore":   {"name": "冲顶", "dmg": None, "type": "piercing", "props": [], "ranged": False},
    "hooves": {"name": "马蹄踢", "dmg": None, "type": "bludgeoning", "props": [], "ranged": False},
    "web":    {"name": "蛛网喷射", "dmg": None, "type": "none", "props": [], "ranged": True},
    "fire_ray": {"name": "狱焰射线", "dmg": None, "type": "fire", "props": [], "ranged": True},
    "greatclub": {"name": "巨棒", "dmg": None, "type": "bludgeoning", "props": [], "ranged": False},
}
