"""玩家角色创建向导（CLI 版；AutoUI 演示模式下可跳过）。

《赛博朋克 RED》轻松模式不掷骰建卡：从模组的**预设角色**（pregens）里挑一个，
填上自己的名字/绰号，再选几名预设角色作为 AI 队友同行。
"""

from __future__ import annotations

from ..rules import data as D
from ..rules.character import Character

# ---------------------------------------------------------------- AI PC 人设字段
# 玩家人设 = AI 代打的 PC 角色卡素材（docs/aipc-design.md §3.3/§4.2）。
# description=角色简介（行为依据），personality=说话语气；mes_example/system_prompt
# 为进阶覆盖字段（仅随 start spec 透传，Web 表单不暴露）。终端建卡 P0 不加交互项，
# 字段留空时由 st.card.card_from_player 以职业·义体兜底文案组卡。
AIPC_FIELD_LIMIT = 2000
AIPC_CARD_FIELDS = ("description", "personality", "mes_example", "system_prompt")


def clamp_aipc_text(value, limit: int = AIPC_FIELD_LIMIT) -> str:
    """清洗玩家人设文本：str 化、去首尾空白、超长截断（纯文本，无密钥面）。"""
    return str(value or "").strip()[:limit]


def aipc_card_fields(spec: dict) -> dict[str, str]:
    """从 start spec 提取非空的玩家人设字段（供 web.charbuild.build_aipc_card 组卡）。"""
    if not isinstance(spec, dict):
        return {}
    out: dict[str, str] = {}
    for key in AIPC_CARD_FIELDS:
        text = clamp_aipc_text(spec.get(key))
        if text:
            out[key] = text
    return out


# ---------------------------------------------------------------- 预设角色
def _raw_module_data(module) -> dict:
    """兜底：直接读模组源文件（campaign 层的 Module 尚未暴露 pregens 时用）。"""
    path = getattr(module, "path", None)
    if not path:
        return {}
    try:
        import json
        from pathlib import Path
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pregens_of(module) -> dict:
    """模组的预设角色表（缺省为空 dict）。"""
    pregens = getattr(module, "pregens", None)
    if isinstance(pregens, dict) and pregens:
        return pregens
    raw = _raw_module_data(module).get("pregens")
    return raw if isinstance(raw, dict) else {}


def default_companion_keys(module) -> list[str]:
    comps = getattr(module, "default_companions", None)
    if isinstance(comps, (list, tuple)) and comps:
        return [str(c) for c in comps]
    raw = _raw_module_data(module).get("default_companions")
    return [str(c) for c in raw] if isinstance(raw, (list, tuple)) else []


def role_name(role: str) -> str:
    return (D.ROLES.get(role) or {}).get("name", role or "独行侠")


def pregen_label(key: str, pregen: dict) -> str:
    """一行卡片摘要：名字（绰号）· 职业 rank —— PW n · 关键词技能。"""
    handle = str(pregen.get("handle") or "").strip()
    name = str(pregen.get("name") or key)
    role = role_name(str(pregen.get("role") or "solo"))
    rank = int(pregen.get("role_rank", 4))
    pw = pregen.get("hp") or Character.hp_for(
        int((pregen.get("stats") or {}).get("body", 5)))
    skills = sorted((pregen.get("skill_levels") or {}).items(),
                    key=lambda kv: -int(kv[1]))[:3]
    skill_txt = "、".join(f"{D.SKILLS[k]['name']}{v}" for k, v in skills
                          if k in D.SKILLS)
    head = f"{name}（{handle}）" if handle else name
    return f"{head} · {role}{rank} —— PW {pw}" + (f" · {skill_txt}" if skill_txt else "")


def build_from_pregen(module, key: str, name: str = "",
                      companion_keys: list[str] | None = None
                      ) -> tuple[Character, list[Character]]:
    """按预设角色 key 装配玩家与 AI 队友（Web 与终端共用的唯一入口）。

    ``name`` 非空时覆盖预设的姓名；``companion_keys`` 为 None 时用模组默认队友。"""
    pregens = pregens_of(module)
    if key not in pregens:
        raise ValueError("未知的预设角色")
    player = Character.from_pregen(key, pregens[key], player=True)
    if str(name or "").strip():
        player.name = str(name).strip()[:16]
    if companion_keys is None:
        companion_keys = default_companion_keys(module)
    comps: list[Character] = []
    for ck in companion_keys:
        if ck not in pregens:
            raise ValueError(f"未知的预设角色：{ck}")
        if ck == key or any(c.name == str(pregens[ck].get("name", ck)) for c in comps):
            continue
        comps.append(Character.from_pregen(ck, pregens[ck], kind="companion"))
    return player, comps


def print_pregen_sheet(ui, key: str, pregen: dict) -> None:
    """把预设角色的完整卡片打到终端（十项属性 / 伤势 / 技能 / 装备 / 义体）。"""
    stats = pregen.get("stats") or {}
    pw = int(pregen.get("hp") or Character.hp_for(int(stats.get("body", 5))))
    ui.raw(f"—— 预设角色 · {pregen.get('name', key)}"
           + (f"（{pregen['handle']}）" if pregen.get("handle") else "") + " ——")
    ui.raw(f"职业：{role_name(str(pregen.get('role') or 'solo'))}"
           f" {int(pregen.get('role_rank', 4))}"
           + (f"　特殊能力：{pregen['role_ability']}" if pregen.get("role_ability") else ""))
    ui.raw("属性：" + " ".join(
        f"{D.STATS[k]['name']}{int(stats.get(k, 0))}" for k in D.STAT_ORDER))
    ui.raw(f"PW {pw}　重伤阈值 {max(1, -(-pw // 2))}"
           f"　幸运 {int(stats.get('luck', 0))}"
           f"　人性 {int(stats.get('emp', 0)) * D.HUMANITY_PER_EMP}")
    skills = sorted((pregen.get("skill_levels") or {}).items(), key=lambda kv: -int(kv[1]))
    if skills:
        ui.raw("技能：" + "、".join(
            f"{D.SKILLS[k]['name']} {int(stats.get(D.SKILLS[k]['stat'], 0)) + int(v)}"
            for k, v in skills if k in D.SKILLS))
    armor = pregen.get("armor")
    if armor:
        ui.raw(f"护甲：{D.ARMORS.get(armor, {}).get('name', armor)}"
               f" SP {D.ARMORS.get(armor, {}).get('sp', 0)}")
    weapons = [D.WEAPONS[w]["name"] for w in (pregen.get("weapons") or []) if w in D.WEAPONS]
    if weapons:
        ui.raw("武器：" + "、".join(weapons))
    cw = [D.CYBERWARE[c]["name"] for c in (pregen.get("cyberware") or []) if c in D.CYBERWARE]
    if cw:
        ui.raw("义体：" + "、".join(cw))
    if pregen.get("desc"):
        ui.raw(str(pregen["desc"]))
    ui.raw("")


def create_player_console(ui, rng=None, module=None) -> tuple[Character, list[str]]:
    """终端建卡：挑预设角色 → 填名字 → 选队友。返回 (玩家, 队友预设 key 列表)。

    ``rng`` 仅为兼容旧签名保留（RED 建卡不掷骰）。"""
    ui.raw("\n—— 角色创建 ——（赛博朋克 RED · 轻松模式 · 预设角色）")
    pregens = pregens_of(module)
    if not pregens:
        raise ValueError("该模组没有预设角色（pregens），无法建卡")
    keys = list(pregens)
    while True:
        i = ui.choose("选择预设角色", [pregen_label(k, pregens[k]) for k in keys])
        print_pregen_sheet(ui, keys[i], pregens[keys[i]])
        if ui.choose("就用这个角色吗？", ["就是TA", "看下一个"]) == 0:
            break
    key = keys[i]
    default_name = str(pregens[key].get("name") or "无名者")
    name = ui.free_input(f"你的名字或街头绰号（回车沿用「{default_name}」）> ") or default_name

    rest = [k for k in keys if k != key]
    picks = [k for k in default_companion_keys(module) if k in rest]
    if rest:
        ui.raw("默认队友：" + ("、".join(str(pregens[k].get("name", k)) for k in picks)
                              or "无"))
        if ui.choose("是否另选队友？", ["沿用默认", "自己挑"]) == 1:
            picks = []
            for k in rest:
                if ui.choose(f"「{pregens[k].get('name', k)}」同行吗？",
                             ["同行", "留守"]) == 0:
                    picks.append(k)

    player = Character.from_pregen(key, pregens[key], player=True)
    player.name = name
    print_character_sheet(ui, player)
    return player, picks


def print_character_sheet(ui, c: Character) -> None:
    """玩家角色卡（终端）。"""
    ui.raw("\n—— 角色卡 ——")
    ui.raw(f"{c.name}" + (f"（{c.handle}）" if c.handle else "")
           + f"｜{c.role_name} {c.role_rank}"
           + (f"（{c.role_ability}）" if c.role_ability else ""))
    ui.raw(c.stat_line())
    ui.raw(f"PW {c.hp_cur}/{c.hp_max}　重伤阈值 {c.seriously_wounded_threshold}"
           f"　伤势 {c.wound_name()}　护甲 {c.armor_name()} SP {c.armor_at('torso')}")
    ui.raw(f"幸运 {c.luck_cur}/{c.stat('luck')}　人性 {c.humanity_cur}/{c.humanity_max}"
           f"　欧元 {c.eurobucks} eb　改进点 {c.ip}")
    skills = c.skill_lines()
    if skills:
        ui.raw("技能：" + "、".join(skills))
    ui.raw("装备：" + "、".join(D.WEAPONS[w]["name"] for w in c.weapons if w in D.WEAPONS)
           + (f"，{c.armor_name()}" if c.armor_key else ""))
    if c.cyberware:
        ui.raw("义体：" + "、".join(D.CYBERWARE[x]["name"] for x in c.cyberware
                                   if x in D.CYBERWARE))
    ui.raw("")
