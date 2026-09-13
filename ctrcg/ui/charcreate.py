"""玩家角色创建向导（CLI 版；AutoUI 演示模式下可跳过）。

属性采用 4d6 去最低的掷骰法；种族/职业/出身名单为《博德之门3》；
种族加值沿用 BG3 规则：任意两项属性 +2/+1（可自选）。
"""

from __future__ import annotations

from ..rules import data as D
from ..rules.character import Character
from ..rules.dice import roll_scores

STYLE_NAMES = {"archery": "箭术（远程攻击+2）", "defense": "防御（AC+1）",
               "dueling": "对决（单手近战伤害+2）", "two_weapon": "双武器格斗"}

# 按职业的推荐属性优先级（掷骰结果按此顺序从高到低分配）
RECOMMENDED: dict[str, dict[str, int]] = {
    "barbarian": {"str": 15, "con": 14, "dex": 13, "wis": 12, "cha": 10, "int": 8},
    "bard":      {"cha": 15, "dex": 14, "con": 13, "wis": 12, "int": 10, "str": 8},
    "cleric":    {"wis": 15, "con": 14, "str": 13, "cha": 12, "dex": 10, "int": 8},
    "druid":     {"wis": 15, "con": 14, "dex": 13, "int": 12, "cha": 10, "str": 8},
    "fighter":   {"str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8},
    "monk":      {"dex": 15, "wis": 14, "con": 13, "str": 12, "cha": 10, "int": 8},
    "paladin":   {"str": 15, "cha": 14, "con": 13, "wis": 12, "dex": 10, "int": 8},
    "ranger":    {"dex": 15, "wis": 14, "con": 13, "str": 12, "int": 10, "cha": 8},
    "rogue":     {"dex": 15, "int": 14, "con": 13, "cha": 12, "wis": 10, "str": 8},
    "sorcerer":  {"cha": 15, "con": 14, "dex": 13, "wis": 12, "int": 10, "str": 8},
    "warlock":   {"cha": 15, "con": 14, "dex": 13, "wis": 12, "int": 10, "str": 8},
    "wizard":    {"int": 15, "dex": 14, "con": 13, "wis": 12, "cha": 10, "str": 8},
}


def assign_by_recommended(rolled: list[int], klass: str) -> dict[str, int]:
    """把掷骰结果按职业推荐优先级从高到低分配到六项属性。"""
    rec = RECOMMENDED[klass]
    order = sorted(rec, key=lambda k: -rec[k])
    return dict(zip(order, sorted(rolled, reverse=True)))


def create_player_console(ui, rng) -> Character:
    ui.raw("\n—— 角色创建 ——（D&D 5e · 1级 · 4d6 去最低掷骰法）")
    name = ui.free_input("你的角色名字> ") or "无名者"

    rkeys = list(D.RACES)
    i = ui.choose("选择种族（博德之门3 全种族）",
                  [f"{D.RACES[r]['family']}·{D.RACES[r]['name']}：{D.RACES[r]['traits'][0]}"
                   for r in rkeys])
    race = rkeys[i]

    ckeys = list(D.CLASSES)
    i = ui.choose("选择职业", [f"{D.CLASSES[c]['name']}（生命骰 d{D.CLASSES[c]['hit_die']}）"
                               for c in ckeys])
    klass = ckeys[i]

    bkeys = list(D.BACKGROUNDS)
    i = ui.choose("选择背景", [f"{D.BACKGROUNDS[b]['name']}：{D.BACKGROUNDS[b]['desc']}"
                               for b in bkeys])
    background = bkeys[i]

    # 属性：4d6 去最低，最多重骰 2 次
    rolled = roll_scores(rng)
    for attempt in range(2):
        i = ui.choose(f"掷骰结果（4d6 去最低）：{' '.join(map(str, rolled))}",
                      ["就这样，分配属性", "重骰一次"])
        if i == 0:
            break
        rolled = roll_scores(rng)
    else:
        ui.raw(f"最终掷骰结果：{' '.join(map(str, rolled))}")

    # 分配：按职业推荐，或手动分配掷骰结果
    i = ui.choose("属性分配", ["按职业推荐自动分配（推荐）", "手动分配掷骰结果"])
    scores = assign_by_recommended(rolled, klass) if i == 0 else _assign_manual(ui, rolled)

    # BG3 种族加值：+2/+1 任意两项
    b2 = _pick_bonus(ui, "选择 +2 的属性（BG3 种族加值）")
    b1 = _pick_bonus(ui, "选择 +1 的属性（须与 +2 不同）", exclude=b2)

    # 技能
    kls = D.CLASSES[klass]
    n = kls["skill_count"] + D.RACES[race].get("skills", 0)
    chosen: list[str] = []
    avail = list(kls["skill_choices"])
    for _ in range(n):
        j = ui.choose(f"选择技能（还剩 {n - len(chosen)} 项）",
                      [f"{D.SKILLS[s]['name']}（{D.ABILITIES[D.SKILLS[s]['ability']]}）"
                       for s in avail if s not in chosen])
        chosen.append(avail[j])

    style = ""
    if klass == "fighter":
        j = ui.choose("选择战斗风格", list(STYLE_NAMES.values()))
        style = list(STYLE_NAMES)[j]

    c = Character.create(name=name, race=race, klass=klass, background=background,
                         scores=scores, skill_choices=chosen, fighting_style=style,
                         bonus2=b2, bonus1=b1)
    _print_sheet(ui, c)
    return c


def _pick_bonus(ui, title: str, exclude: str | None = None) -> str:
    keys = [k for k in D.ABILITIES if k != exclude]
    j = ui.choose(title, [f"{v}（{k}）" for k, v in
                          [(k, D.ABILITIES[k]) for k in keys]])
    return keys[j]


def _assign_manual(ui, rolled: list[int]) -> dict:
    pool = sorted(rolled, reverse=True)
    scores: dict[str, int] = {}
    for abil, disp in D.ABILITIES.items():
        opts = sorted(set(pool), reverse=True)
        j = ui.choose(f"为 {disp} 分配（剩余 {pool}）", [str(v) for v in opts])
        pool.remove(int(j))
        scores[abil] = int(j)
    return scores


def _print_sheet(ui, c: Character) -> None:
    ui.raw("\n—— 角色卡 ——")
    ui.raw(f"{c.name}｜{D.RACES[c.race]['name']} {D.CLASSES[c.klass]['name']}"
           f"（{D.BACKGROUNDS[c.background]['name']}）")
    ui.raw(" | ".join(f"{D.ABILITIES[a]} {c.abilities[a]}({c.mod(a):+d})"
                      for a in D.ABILITIES))
    ui.raw(f"HP {c.hp_max}  AC {c.ac()}  熟练+{c.prof_bonus()}  "
           f"被动察觉 {c.passive_perception()}")
    ui.raw("技能熟练：" + "、".join(D.SKILLS[s]["name"] for s in sorted(c.skills)))
    ui.raw("装备：" + "、".join(D.WEAPONS[w]["name"] for w in c.weapons)
           + f"，{D.ARMORS[c.armor]['name']}"
           + ("，盾牌" if c.shield else ""))
    ui.raw("")
