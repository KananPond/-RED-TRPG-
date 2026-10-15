"""内置兜底模组（最后手段）—— 夜之城 · 最后一份兜底数据。

本文件**不是**正式剧本。正式剧本以 ``modules/<id>/module.json`` 为准；
只有当 ``modules/`` 目录缺失、模组文件损坏，或存档引用的模组 id 无法解析时，
引擎才会退回这里的数据（``cordis.default_fallback_module()`` 与 ``GameState``
默认使用的 ``M.BUILTIN``）。正常游玩不应看到它。

因此这里只保留**最小可运行**的内容：2 个场景、1 场遭遇、2 名预设角色，
数值全部取自 :mod:`trpg.rules.data`，口径为《赛博朋克 RED》轻松模式
（十项属性 / 41 项技能 / 难度值 DV / 生命值 PW / 护甲 SP / 改进点 IP / 欧元 eb）。

导出名与旧版保持一致（``MODULE/COMPANIONS/NPCS/ENCOUNTERS/SCENES/EPILOGUE/
ACT_TITLES/D_NAME/COND_CN/default_companions/BUILTIN``），仅内容改为 RED。
"""

from __future__ import annotations

from ..rules import data as D

# ---------------------------------------------------------------- 显示名映射（state/server 共用）
#: 职业 key → 中文名（RED 用职业 role，不再有 种族/职业/背景）
D_NAME = {k: v["name"] for k, v in D.ROLES.items()}

#: 状态 key → 中文名（RED 的伤势等级与控制状态）
COND_CN = {
    "healthy": "完好",
    "light": "轻伤",
    "serious": "重伤",
    "mortal": "濒死",
    "controlled": "被控制",
    "prone": "倒地",
    "unconscious": "昏迷",
    "stabilized": "已稳定",
    "crit_stabilized": "致命伤已固定",
    "dead": "死亡",
}


MODULE = {
    "title": "赛博朋克 RED：兜底剧本",
    "en_title": "Cyberpunk RED: Last-Resort Fallback",
    "setting": (
        "2045 年，夜之城（Night City）。血染之时（Time of the Red）之后的废墟上，"
        "霓虹与酸雨一起落下。本段数据是引擎自带的兜底场景，"
        "任何正式的 modules/<id>/module.json 都会覆盖它。"
    ),
    "synopsis": (
        "【兜底剧本 · 最后手段】中间人 Marlo 在卡布奇夜市后巷约见你的小队，"
        "要你们去海伍德工业区的废弃仓库取一只无人认领的公文包。"
        "钱不多，但足够付这个月的房租——前提是你们能活着走出仓库。"
        "（本剧本仅 2 幕，用于 modules/ 目录缺失时保证游戏不硬失败。）"
    ),
}

# ---------------------------------------------------------------- 预设角色（pregens）
#: key → 预设角色数据块（字段与 ``Character.from_pregen`` 对齐）
PREGENS: dict[str, dict] = {
    "v_solo": {
        "name": "Vera", "handle": "V", "role": "solo", "role_rank": 4,
        "role_ability": "战斗直觉",
        "stats": {"int": 5, "ref": 8, "dex": 7, "tech": 4, "cool": 6,
                  "will": 6, "luck": 6, "move": 6, "body": 6, "emp": 4},
        "hp": 40,
        "skill_levels": {"handgun": 6, "evasion": 5, "athletics": 4,
                         "perception": 4, "brawling": 4, "stealth": 3,
                         "conversation": 2},
        "armor": "light_armorjack",
        "weapons": ["very_heavy_pistol", "heavy_melee"],
        "cyberware": ["neural_link", "pain_editor"],
        "gear": {"ammo": 20, "agent": 1},
        "eurobucks": 50,
        "persona": (
            "独行侠，靠枪吃饭七年，从不接没有退路的活。战斗风格：抢占掩体，"
            "优先点掉敌方持枪者；队友倒地时先把他拖出火线再谈别的。"
            "话少，句子短，只在算钱的时候话多。"
        ),
        "voice": "低哑，语速慢，句尾常带一句脏话。",
        "desc": "黑色防弹夹克，右眼有一道旧疤。",
    },
    "k_medtech": {
        "name": "Kay", "handle": "Doc K", "role": "medtech", "role_rank": 4,
        "role_ability": "医疗",
        "stats": {"int": 6, "ref": 5, "dex": 6, "tech": 8, "cool": 5,
                  "will": 7, "luck": 5, "move": 5, "body": 5, "emp": 6},
        "hp": 35,
        "skill_levels": {"paramedic": 6, "first_aid": 6, "deduction": 5,
                         "perception": 4, "handgun": 4, "conversation": 4,
                         "electronics_security": 3},
        "armor": "kevlar",
        "weapons": ["medium_pistol"],
        "cyberware": ["cyberoptics", "internal_agent"],
        "gear": {"medkit": 2, "first_aid_kit": 2},
        "eurobucks": 30,
        "persona": (
            "医疗师，在地下诊所缝合过太多枪伤，习惯先把人救活再问对错。"
            "战斗风格：站在掩体后面，谁流血最多就先处理谁；"
            "只在没人需要急救时开枪。说话像念病历。"
        ),
        "voice": "平静，术语多，喜欢用数字描述伤势。",
        "desc": "洗得发白的旧手术服外套一件凯夫拉背心。",
    },
}

# ---------------------------------------------------------------- 同伴（预设角色的列表视图）
COMPANIONS: list[dict] = [{"key": k, **dict(v)} for k, v in PREGENS.items()]

#: 玩家未另选队友时默认启用的预设角色
DEFAULT_COMPANIONS: list[str] = list(PREGENS)


def default_companions() -> list[dict]:
    """模组未定义预设角色时的默认队友（返回数据块副本，每项含 key）。"""
    return [dict(c) for c in COMPANIONS]


# ---------------------------------------------------------------- NPC（叙事角色，非战斗数据）
NPCS = {
    "malou": {
        "name": "Marlo", "role": "中间人",
        "desc": "卡布奇夜市的老中间人，抽着劣质香烟报价，从不多说一个字。",
        "persona": "只谈价钱与风险，讨厌废话；报价时喜欢停顿。",
        "voice": "沙哑，短句，报数字时特别清楚。",
    },
    "rennick": {
        "name": "Rennick", "role": "警察",
        "desc": "NCPD 巡警，收了钱就会在正确的时间出现在错误的地方。",
        "persona": "嘴上讲程序，手上讲价钱；被戳穿时会突然变得很正式。",
        "voice": "公事公办的腔调，夹着街头俚语。",
    },
}

# ---------------------------------------------------------------- 自定义 NPC（战斗数据）
CUSTOM_NPCS: dict[str, dict] = {
    "boosterganger": {
        "name": "莽汉帮小混混", "role": "solo",
        "stats": {"int": 4, "ref": 6, "dex": 6, "tech": 3, "cool": 5,
                  "will": 5, "luck": 4, "move": 6, "body": 5, "emp": 3},
        "skills": {"handgun": 4, "brawling": 4, "athletics": 3,
                   "evasion": 2, "perception": 2},
        "hp": 25, "armor": 4, "armor_name": "皮衣",
        "weapons": ["medium_pistol", "light_melee"],
        "cyberware": [], "gear": ["ammo"],
        "notes": "莽汉帮的廉价打手，靠人多和兴奋剂壮胆；见血就散。",
    },
}

# ---------------------------------------------------------------- 遭遇
ENCOUNTERS: dict[str, dict] = {
    "e_warehouse_ambush": {
        "title": "仓库伏击",
        "intro": (
            "公文包就放在仓库正中的货箱上，太干净了。"
            "卷帘门在身后落下的同时，两束战术手电从货架间亮起——"
            "Rennick 把警徽挂在胸前的防弹衣上，笑得很客气："
            "'东西留下，你们走。或者你们也留下。'"
        ),
        "enemies": [["corrupt_cop", 2], ["boosterganger", 2]],
        "loot": {"eurobucks": 80, "items": {"ammo": 20, "first_aid_kit": 1}},
        "spawns": {"corrupt_cop": [[9, 2], [9, 5]],
                   "boosterganger": [[8, 3], [8, 6]]},
    },
}

# ---------------------------------------------------------------- 场景
# 每个场景：intro 进场叙述；explore 引导词；checks 可选检定（技能 + 难度值 DV）；
# encounter 触发战斗（可选）；next 下一场景；ip 离开本场景时发放的改进点。
SCENES: list[dict] = [
    {
        "id": "kabuki_meet", "act": 1,
        "location": "夜之城 · 卡布奇 · 夜市后巷",
        "intro": (
            "酸雨把霓虹招牌泡成一团团化开的颜色。Marlo 靠在自动贩卖机上，"
            "把一只数据芯片弹给你。\n"
            "'海伍德，工业区，第七街的废弃仓库。一只公文包，没人认领。'\n"
            "他吸了口烟：'三百欧，先付一半。别问包里是什么——问了就得加钱。'"
        ),
        "explore": "可以查看芯片上的坐标、和 Marlo 讨价还价，或观察后巷里的人。",
        "checks": [
            {"skill": "human_perception", "dv": 13,
             "success": "Marlo 的报价里有一处犹豫：他自己也不知道包里是什么——这单是转手的。",
             "fail": "Marlo 的表情被烟雾和霓虹灯挡得干干净净。"},
        ],
        "encounter": None, "next": "warehouse_drop", "ip": 0,
        "shop": True, "outro": "", "encounter_note": "",
    },
    {
        "id": "warehouse_drop", "act": 2,
        "location": "海伍德工业区 · 第七街废弃仓库",
        "intro": (
            "仓库里只有一处光源：货箱顶上那只公文包，被一盏应急灯照着。"
            "你们的脚步声在铁皮屋顶下面回响了两次——第三次不是你们的。"
        ),
        "explore": "可以检查货架、抢占掩体，或者直接拿了包就走。",
        "checks": [
            {"skill": "perception", "dv": 15,
             "success": "你从货架反光里认出了 Rennick 的警徽——NCPD 也想拿这只包。",
             "fail": "手电亮起之前，仓库看起来确实空无一人。"},
        ],
        "encounter": "e_warehouse_ambush", "next": None, "ip": 30,
        "shop": False,
        "outro": (
            "Rennick 倒在货架之间，警徽还在胸前晃。"
            "公文包里只有一份纸质名单：夜之城七名中间人的名字，"
            "其中三个已经被划掉了。第一行写着 Marlo 的名字。"
        ),
        "encounter_note": "拿包的动作会触发伏击。",
    },
]

EPILOGUE = (
    "名单上的第一个名字是 Marlo。你们把纸折好，塞进夹克内袋。\n"
    "夜市那边传来警笛，霓虹还在雨里化开——夜之城从不为一件事停下来。\n"
    "（兜底剧本到此结束。正式的模组会从这里接上。）"
)

ACT_TITLES = {1: "第一幕：卡布奇夜市", 2: "第二幕：海伍德仓库"}


def scene_by_id(scene_id: str) -> dict | None:
    for s in SCENES:
        if s["id"] == scene_id:
            return s
    return None


def module_handbook() -> str:
    """供 DM Agent 的浓缩手册文本。"""
    lines = [f"模组：{MODULE['title']}（{MODULE['en_title']}）", MODULE["synopsis"], ""]
    for act in sorted(ACT_TITLES):
        lines.append(f"== {ACT_TITLES[act]} ==")
        for s in SCENES:
            if s["act"] == act:
                lines.append(f"[{s['id']}] {s['location']}")
                lines.append("  " + s["intro"].replace("\n", " ")[:120])
        lines.append("")
    lines.append("== 预设角色 ==")
    for k, pg in PREGENS.items():
        role = D.ROLES.get(pg["role"], {}).get("name", pg["role"])
        lines.append(f"{pg['name']}（{role} · PW {pg.get('hp', '?')}）")
    lines.append("")
    lines.append("== 关键NPC ==")
    for n in NPCS.values():
        lines.append(f"{n['name']}（{n['role']}）：{n['desc']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 内置模组实例
def builtin_data() -> dict:
    """兜底模组的原始数据（与 ``modules/<id>/module.json`` 同构，且必须通过校验）。"""
    return {
        "id": "cpred-easy-mode",
        "title": MODULE["title"],
        "en_title": MODULE["en_title"],
        "setting": MODULE["setting"],
        "synopsis": MODULE["synopsis"],
        "act_titles": {str(k): v for k, v in ACT_TITLES.items()},
        "epilogue": EPILOGUE,
        "npcs": NPCS,
        "pregens": PREGENS,
        "default_companions": list(DEFAULT_COMPANIONS),
        "custom_npcs": CUSTOM_NPCS,
        "encounters": ENCOUNTERS,
        "scenes": SCENES,
    }


def _builtin():
    from .loader import Module
    return Module(builtin_data(), source="builtin")


BUILTIN = _builtin()
