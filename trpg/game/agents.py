"""DM Agent 与 PC Agent 的定义（系统提示词 + 构建工厂）。

提示词面向《赛博朋克 RED》（夜之城 2045，"血染之时"）：
技能检定 = 属性 + 技能 + 1d10 对难度值（DV 13/15/17/21/24），
护甲 SP 会磨损、白刃战无视一半护甲、斗殴打两下、伤害骰两个 6 出致命伤、
伤势分轻伤/重伤/濒死、PW 归零走生存检定、幸运点每轮恢复、义体吃人性。
工具名与 PORT_SPEC §5（DM）/§6（PC 战斗）/§7（探索）一致。
"""

from __future__ import annotations

import dataclasses

from ..campaign.loader import Module
from ..harness.loop import Agent
from ..rules import data as D
from ..rules.character import Character
from ..st import (CharacterCard, Persona, assemble_system, card_from_companion,
                  card_from_dm, card_from_npc)

DM_SYSTEM_TEMPLATE = """你是《{title}》这场《赛博朋克 RED》跑团的 DM（主持人），舞台是{setting_lead}。
你的职责：推进剧情、扮演 NPC、裁定行动、控制节奏。规则数值一律通过工具完成，绝不自己编造骰点结果。

世界与基调：
- 2045 年，"血染之时"的夜之城：巨型公司、街头帮派、中间人（fixer）、义体医生、雨水与霓虹。
  没有超自然力量，只有子弹、义体、欧元（eb）和随时会崩掉的人性。
- 玩家与同伴是受雇的边缘行者：中间人派活、谈好价钱，然后去把事办成（或者死在半路）。
- 该脏就脏：黑话、脏话、廉价合成食物、假广告、债务与背叛都是这个世界的质感。

工作方式：
1. 每回合开始，先用 get_state 了解现状（队伍 PW / 伤势 / 护甲 SP / 幸运 / 人性 / 欧元），再决定如何回应玩家。
2. 叙述用中文，具体、有画面感、简洁（每次 2~5 句），保持赛博朋克的冷硬氛围。
3. 玩家做有失败可能的事时，用 skill_check（skill=技能 key、dv=难度值、who=谁来做）请引擎掷骰：
   **属性 + 技能 + 1d10，必须高于 DV 才算成功**（平手算防守方赢，别把平手叙述成成功）。
   DV 五档：日常 13 / 困难 15 / 专业 17 / 英雄 21 / 不可思议 24；特殊情形可用 9~30 的任意值。
   对手会主动反抗时用 opposed_check（说服对读心、擒抱对闪避……），要让玩家亲自掷骰时用 request_roll。
4. 剧情到达新场景时用 advance_scene 推进；到达战斗节点用 start_encounter 触发遭遇。
5. 战术地图：谁站在哪由你调度。【地图】上下文列出当前棋子（pc:名字/npc:id/prop:id）
   与坐标（1格=2米）；队伍挪动、NPC 走位、重要物件变化后用 map_update 工具同步棋盘
   （例如 {{"op":"move","id":"pc:Kyle","x":5,"y":7}}），位置会实时推到玩家面前。
6. 完成阶段性剧情后用 award_ip 发改进点（IP，用于提升技能与职业能力），
   用 give_loot 发欧元（eb）、装备与义体。
7. 战斗由系统自动结算（含走位、护甲磨损、伤势、致命伤与生存检定），你不执行战斗动作；
   战斗结束后你负责收尾叙述。街头的脸面之争（谁先怂、谁在吹牛）用 confrontation。
8. 重要 NPC 的对话可以交给子智能体：用 npc_dialogue(npc, instruction) 让该 NPC
   依据自己的人设与记忆即兴开口（台词会出现在桌面上，同伴们听得见），
   你再接叙述。一句话的过场用 speak_as_npc 更快。
9. 结束本回合时，停止调用工具，直接输出最终叙述即可（等价于 narrate）。

你的工具只有这些（不要发明别的名字）：
narrate、speak_as_npc、npc_dialogue、skill_check、opposed_check、request_roll、
get_state、set_flag、advance_scene、award_ip、give_loot、start_encounter、
confrontation、map_update。属性对抗一律走 skill_check / opposed_check / confrontation。

规则速查（引擎实际结算，你只负责据此叙述，不要自己算）：
- 检定：1d10 + 属性 + 技能；d10 掷出 10 追加一颗 d10 相加，掷出 1 追加一颗 d10 相减。
- 幸运点：每轮恢复满，掷骰前可投入，每点 +1；该让玩家赌一把的时候就直说。
- 远程攻击：按射程带查 DV（0-6/7-12/13-25/26-50/51-100/101-200/201-400/401-800 米）——
  手枪 13/15/20/25/30/30，霰弹枪 13/15/20/25/30/35，突击步枪 17/16/15/13/15/20/25/30。
- 白刃战：对抗检定，无视防守方一半护甲 SP；斗殴（徒手）每次攻击动作打两下，可擒抱、锁喉、投掷。
- 护甲 SP：伤害先扣 SP 再扣 PW，每次挡下伤害 SP 磨损 1；SP 磨光的人等于赤手空拳。
- 伤势：轻伤（PW 低于上限，无惩罚）/ 重伤（PW ≤ 上限一半，所有检定 -2）/ 濒死（PW < 1，检定 -4、移动 -6）。
- 致命伤：一次伤害骰里出现两个 6 就触发 2d6 致命伤表（额外 5 点无视护甲的伤害 + 长期后果）。
- 生存检定：PW 归零后，每回合开始掷 1d10，低于"10 + 累积修正 + 致命伤修正"就活下来，
  掷出 10 自动失败，失败即死；被稳定后 PW 回到 1，再靠休息与治疗慢慢爬回来。
- 掩体有耐久（厚钢 50 / 薄钢 25 / 厚混凝土 25 / 薄木 5……），躲在后面比站在街上强。
- 义体与人性：装义体会扣人性，人性归零人就废了；义体带来的加值已经算在角色卡里。

桌边社会性（重要）：队伍里的同伴由各自的 AI 扮演，是有主见的活人，不是背景板。
他们可能抢话、打断你或 NPC、互相争论。绝不要无视他们的插话：
- 让 NPC 直接回应同伴的话（用 speak_as_npc）——可以反驳、嘲讽、讨价还价，也可以被说服；
- 同伴对 NPC 施压、套话或说服时，用 skill_check / opposed_check / confrontation 正经裁定；
- 保留分歧与后果：NPC 会记仇，也会改主意；不必让队伍意见一致。

模组手册（保持主线走向，允许即兴发挥细节，不得提前泄露后续剧情与敌人底牌）：
{handbook}"""

DM_CONTEXT_TEMPLATE = """【当前场景】{scene_id}：{location}
【场景数据】{scene_json}
【队伍状态】{party}
【剧情旗标】{flags}
【最近记录】{recent}"""

#: DM 可见工具白名单（PORT_SPEC §5）
DM_TOOL_NAMES = ["narrate", "speak_as_npc", "npc_dialogue", "skill_check",
                 "opposed_check", "request_roll", "get_state", "set_flag",
                 "advance_scene", "award_ip", "give_loot", "start_encounter",
                 "confrontation", "map_update"]


def make_dm_agent(model: str, module: Module, *, user: str = "") -> Agent:
    """DM 独立 Agent（docs/battlemap-design.md §3）：与同伴/NPC/AIPC 同一条
    SillyTavern 组装线——system_prompt 经 assemble_system 组装，main =
    DM_SYSTEM_TEMPLATE（经 template_vars 预格式化），模组可用顶层 "dm" 块
    （persona/voice/system_prompt/depth_prompt/book）覆盖或补充。
    {{user}} 宏渲染玩家名。深度注入（depth_prompt/@D 世界书条目）由
    session.dm_turn 经 loop.run(post_build=…) 在每回合接线。"""
    setting_lead = (module.setting or "夜之城（Night City）").strip()
    card = card_from_dm(getattr(module, "dm", {}) or {}, title=module.title)
    template_vars = {"title": module.title, "setting_lead": setting_lead,
                     "handbook": module.handbook()}
    system_prompt = assemble_system(card, _anon_persona(user),
                                    main_default=DM_SYSTEM_TEMPLATE,
                                    template_vars=template_vars)
    agent = Agent(
        agent_id="dm", name="主持人",
        system_prompt=system_prompt,
        tool_names=list(DM_TOOL_NAMES),
        model=model, temperature=0.85, max_tokens=1200)
    agent.depth_prompt = card.depth_prompt   # session.dm_turn 深度注入读取（非字段）
    return agent


PC_SYSTEM_TEMPLATE = """你是《赛博朋克 RED》跑团队伍中的玩家角色 {name}（职业：{role_name}；
职业能力「{role_ability}」{role_rank} 级），由 AI 代打。严格按你的人设说话与行动，中文，每次发言 1~3 句。

人设：{persona}
语气：{voice}

你的现状：{status}
武器：{weapons}

规则要点（引擎实际结算，你只做决策）：
- 技能检定：1d10 + 属性 + 技能，对难度值 DV（日常 13 / 困难 15 / 专业 17 / 英雄 21 / 不可思议 24），
  必须高于 DV 才算成功，平手算防守方赢。
- 幸运点：每轮恢复满，可以在掷骰前投入换加值（attack 的 luck 参数，每点 +1）——该赌就赌。
- 每回合你有一个移动动作 + 一个其他动作，combat_action 工具一次执行一个。
  动作：move（cell=[x,y] 目标格）、approach（贴上去）、dash、attack（weapon / target / aimed / luck）、
  grab、choke、throw、escape_grapple、reload、stand、stabilize、first_aid（target / index）、
  use_item、delay、confrontation、flee、end_turn。
- 远程攻击按射程带决定 DV：手枪 6 米内 13、25 米内 20、50 米内 25，越远越难；贴脸缠斗改用近战武器。
- 白刃战无视对方一半护甲 SP；徒手斗殴每次攻击动作打两下，还能擒抱、锁喉、把对手摔出去。
- 护甲 SP 会被磨损：挨打越多身上的护甲越薄，甲薄了就换位、找掩体或让技术师修甲。
- 伤势：重伤（PW 不到上限一半）所有检定 -2；濒死（PW < 1）检定 -4、移动 -6，每回合还要掷生存检定。
- 伤害骰出现两个 6 会打出致命伤（额外伤害 + 长期后果），别硬吃子弹。
- 掩体能挡子弹也有耐久，躲在后面比站在街上强。
- 优先策略：{tactics}

桌边社会性：你是夜之城的活人，不是工具人。
- 有想法就说：可以抢话、打断 DM 或队友、反驳别人的计划、追问 NPC、吐槽。
- 别人（包括玩家）说了你不认同的话，别附和——按你的性格反对并给出理由；被说服就大方改口。
- 好台词值得等：场合不对或没话可说时保持安静；不要抢每一句，不要替玩家或 DM 说话。
- 战斗里你也可以先用 say 喊一句简短的战吼、提醒或嘲讽，再行动。

非战斗时，用 say 发言或 explore_intent 声明意图；{explore_hint}
无关时输出"（保持安静，跟随队伍）"。结束时停止调用工具即可。
绝不要编造骰点或数值结果，一切以工具返回为准。"""

#: 同伴（非玩家 PC）探索面提示（register_pc_tools 的实际工具面）
_EXPLORE_HINT_PC = "休整（rest_day）、急救（first_aid）、稳定（stabilize）、采购（buy）交给玩家处理。"
#: AI 代打玩家本人的探索面提示（register_aipc_tools 多一件 explore_act）
_EXPLORE_HINT_AIPC = ("休整一天（rest_day）、急救固定致命伤（first_aid）、稳定（stabilize）、"
                      "采购（buy）、修甲（repair_armor）用 explore_act，其余行动用 explore_intent。")

#: 职业 key → 中文名（D.ROLES）
ROLE_NAME = {key: info.get("name", key) for key, info in D.ROLES.items()}
#: 职业 key → 职业特殊能力名（D.ROLES）
ROLE_ABILITY = {key: (info.get("ability") or {}).get("name", "")
                for key, info in D.ROLES.items()}

#: 逐职业战术（RED：每个职业只有枪、义体与街头手段）
TACTICS = {
    "solo": "顶在最前，用身上最好的枪点掉对方威胁最大的人（战斗中把战斗直觉分配到攻击与伤害上）；"
            "护甲被打薄后缩回掩体换甲再战。",
    "netrunner": "轻松模式里不展开赛博空间：你靠电子学与安防、数据库检索、感知做事——"
                 "事先踩点、开锁、黑门禁、切断监控；真打起来就躲在掩体后用手枪自保。",
    "tech": "让队友的武器与护甲保持可用：开打前修甲、改造装备，战斗中在掩体后射击，"
            "别为了炫技冲到街心去。",
    "medtech": "站后排。有人倒地或被致命伤咬住时，优先 stabilize 与 first_aid（急救固定），"
               "再考虑开枪；平时用推理与急救技能做诊断与判断。",
    "media": "信誉与摄像机是你的武器：交涉、套话、留下证据、把事闹大。避免正面交火，"
             "必要时用手枪自保并找掩体。",
    "cop": "该叫支援就叫支援（动用 NCPD 档案与人脉），对峙与审讯是你的主场；"
           "战斗中护甲最厚，可以顶在前面，但别一个人追进巷子。",
    "corporate": "团队协作调资源：用官僚体系、会计学与说服解决问题，把冲突变成谈判与合同；"
                 "非打不可时躲在掩体后射击。",
    "fixer": "人脉与生意：谈判、找货源、谈价、牵线，对峙时用魅力（COOL）+ 声望压场；"
             "打架不是你的活，掏枪只为保命。",
    "nomad": "家族的车与荒原生存技巧：载具驾驶、追踪、机动绕侧；"
             "战斗中与队友保持射界，靠远程武器控制距离，别被围住。",
    "rockerboy": "魅力效应：对粉丝做基于魅力/共情的检定 +2（引擎自动计入），对峙也能吃到；"
                 "用表演与煽动带节奏，枪是最后的台词。",
}


def _anon_persona(user: str) -> Persona | None:
    """{{user}} 宏的来源（〔ST 规格 §4〕用户 persona 语义）：
    position=none → 不进 system，只以 name 供给宏渲染；user 为空返回 None
    （{{user}} 渲染为空串，与旧行为一致）。"""
    if not user:
        return None
    return Persona(name=user, description="", position="none")


# ---------------------------------------------------------------- 取值助手
#: 现状提示串里允许出现的 RED 口径关键词（白名单）
_STATUS_KEYS = ("PW", "伤势", "护甲", "SP", "幸运", "人性", "欧元", "eb", "IP")


def _field(obj, key: str, default=None):
    """同伴既可能是模组预生成角色 dict，也可能是 Character：统一取值。"""
    if isinstance(obj, dict):
        value = obj.get(key, default)
    else:
        value = getattr(obj, key, default)
    return default if value is None else value


def _known_gear_names() -> set:
    """武器/护甲/装备的中文名（判断调用方提示是否为合法的随身装备清单）。"""
    names = {info.get("name", "") for info in D.WEAPONS.values()}
    names |= {info.get("name", "") for info in D.ARMORS.values()}
    names |= {info.get("name", "") for info in D.GEAR.values()}
    names.add("徒手")
    names.discard("")
    return names


def _clean_hint(text: str) -> str:
    """只接受按 RED 口径写的现状提示（PW/伤势/… 关键词，或装备清单）；
    其余（并发移植期可能残留的旧规则集文案）一律丢弃，由规则层数据现算。"""
    text = str(text or "").strip()
    if not text:
        return ""
    if any(k in text for k in _STATUS_KEYS):
        return text
    known = _known_gear_names()
    parts = [p.strip() for p in text.replace("、", ",").split(",") if p.strip()]
    if parts and all(p in known for p in parts):
        return text
    return ""


def _pregen_pw(obj) -> str:
    """dict 形状的预生成角色：PW 取卡面 hp 字段。"""
    hp = _field(obj, "hp", None)
    try:
        hp = int(hp) if hp else 0
    except (TypeError, ValueError):
        hp = 0
    return f"PW {hp}/{hp}（以 get_state 的实时状态为准）" if hp else ""


def _role_key(obj) -> str:
    return str(_field(obj, "role", "solo") or "solo")


def _role_ability(obj) -> str:
    named = str(_field(obj, "role_ability", "") or "")
    return named or ROLE_ABILITY.get(_role_key(obj), "")


def _weapon_text(weapons) -> str:
    """武器 key 列表 → 中文武器名（未知 key 原样列出）。"""
    items = list(weapons or []) if not isinstance(weapons, str) else [weapons]
    names = []
    for key in items:
        info = D.WEAPONS.get(str(key))
        names.append(info["name"] if info else str(key))
    return "、".join(names) or "徒手"


def _char_status(ch: Character) -> str:
    """给提示词的一行现状（PW/伤势/护甲 SP/幸运/人性）。"""
    bits = [f"PW {ch.hp_cur}/{ch.hp_max}（{ch.wound_name()}）",
            f"护甲 {ch.armor_name()} SP {ch.armor_at('torso')}",
            f"幸运 {ch.luck_cur}/{ch.stat('luck')}",
            f"人性 {ch.humanity_cur}/{ch.humanity_max}"]
    if ch.crit_injuries:
        bits.append("致命伤：" + "、".join(i["name"] for i in ch.crit_injuries))
    return "，".join(bits)


def _status_text(obj, hint: str = "") -> str:
    """现状行：规则层数据现算（PW/伤势/护甲 SP/幸运/人性）+ 调用方给的
    RED 口径提示（随身装备或状态）。"""
    clean = _clean_hint(hint)
    parts: list[str] = []
    if hasattr(obj, "hp_cur"):
        parts.append(_char_status(obj))
    else:
        pw = _pregen_pw(obj)
        if pw:
            parts.append(pw)
    if clean:
        parts.append(("随身：" if any(k in clean for k in _STATUS_KEYS)
                      else "装备：") + clean)
    return "；".join(parts) or "以 get_state / 工具返回的状态为准"


def make_pc_agent(comp: dict, level: int, weapons: list[str], slots_hint: str,
                  model: str, *, user: str = "",
                  party_ai: bool = False) -> Agent:
    """同伴（非玩家 PC）的**独立 Agent**（docs/companion-npc-agents.md §1）。

    SillyTavern 架构与玩家版 make_aipc_agent 同一条组装线：
    system_prompt 经 trpg.st.assemble_system 按〔ST 规格 §3.2〕默认序组装——
    main（PC_SYSTEM_TEMPLATE 经 template_vars 预格式化；模组可用卡 system_prompt
    覆盖并展开 {{original}}）→ 对话示例（mes_example，<START> 分块）；
    人设/语气进模板槽位后以空副本传卡，避免同一段文本双份进提示词。

    ``comp`` 为模组预生成角色 dict（name/handle/role/role_rank/persona/voice/…）
    或 Character；``level`` 沿用旧签名，实际承载职业能力级（role_rank），
    ``slots_hint`` 为可选的现状提示串（旧文案里的规则词汇会被丢弃）。
    模组可为同伴提供 ST 卡字段：mes_example / first_mes / depth_prompt /
    book / talkativeness（card_from_companion）。user=玩家名，供 {{user}} 宏。
    ``party_ai=True`` 时把探索面扩到 explore_act（全 AI 跑团，PC 自己处理休整/
    急救/采购），并提示“没有人类玩家可代劳”。"""
    card = card_from_companion(comp if isinstance(comp, dict) else
                               _character_as_pregen(comp))
    role = _role_key(comp)
    rank = _field(comp, "role_rank", None)
    if rank is None:
        rank = level
    template_vars = {
        "name": card.name or _field(comp, "name", ""),
        "role_name": ROLE_NAME.get(role, role),
        "role_ability": _role_ability(comp) or "（未展开）",
        "role_rank": rank,
        "persona": card.description,
        "voice": card.personality,
        "weapons": _weapon_text(weapons or _field(comp, "weapons", [])),
        "status": _status_text(comp, slots_hint),
        "tactics": TACTICS.get(role, "见机行事。"),
        "explore_hint": (_EXPLORE_HINT_AIPC if party_ai
                         else _EXPLORE_HINT_PC),
    }
    # 人设/语气进模板槽位后，卡上同字段不再重复成独立 section
    st_card = dataclasses.replace(card, description="", personality="")
    system_prompt = assemble_system(
        st_card, _anon_persona(user), main_default=PC_SYSTEM_TEMPLATE,
        template_vars=template_vars)
    return Agent(
        agent_id=f"pc:{_field(comp, 'id', _field(comp, 'name', ''))}",
        name=str(_field(comp, "name", "")),
        system_prompt=system_prompt,
        tool_names=(["say", "combat_action", "explore_intent", "explore_act"]
                    if party_ai else
                    ["say", "combat_action", "explore_intent"]),
        model=model, temperature=0.5, max_tokens=500)


def _character_as_pregen(ch: Character) -> dict:
    """Character → pregen 形状 dict（同伴卡字段取用）。"""
    return {"id": getattr(ch, "name", ""), "name": ch.name, "handle": ch.handle,
            "role": ch.role, "role_rank": ch.role_rank,
            "role_ability": ch.role_ability, "persona": ch.persona,
            "voice": ch.voice, "desc": ch.desc, "weapons": list(ch.weapons)}


# ---------------------------------------------------------------- NPC（子智能体版）
NPC_SYSTEM_TEMPLATE = """你是夜之城这张桌面上的 NPC「{name}」（{role}），由 DM 授权的子智能体扮演。
DM 仍是导演：他决定何时轮到你说话，并给你对话焦点与导演提示；你负责以角色身份即兴。

角色设定：{description}
语气与举止：{personality}
{goals}{secret}

规矩：
- 你只说话，不动世界：你没有影响世界的工具，台词之外的一切由 DM 裁定。
- 台词符合你的身份、处境与记忆（你记得此前与这些边缘行者的每次交锋）；你可以
  反驳、拖延、讨价还价、被说服或翻脸，但不要无理由服从他们。
- 不替 DM 叙述场景，不替玩家或同伴说话；不要泄露你角色不该知道的剧情底牌
  （secret 仅供你把握分寸，只有剧情合理揭示时才说出口）。
- 中文，每次 1~2 句，精炼、有性格、口语化。这是 2045 年的夜之城，人们靠枪、义体与信用活着。

用 say 工具说出此刻的台词（至多两句，一句一次调用），说完即停止调用工具。"""


def make_npc_agent(npc: dict, model: str, *, user: str = "") -> Agent:
    """NPC 子智能体 Agent（DM 控制的 SubAgent，docs/companion-npc-agents.md §2）。

    与同伴的独立 Agent 相对：NPC 的 agent 由 DM 的 npc_dialogue 工具调用按需
    拉起，只配对话面（say-only registry，由 npcagent.NPCDirector 提供），
    产出直接回到 DM 的工具结果。组装线同 make_pc_agent（assemble_system），
    模组可为 NPC 提供扩展字段：role/affiliation、persona/voice/goals/secret/
    mes_example/book（card_from_npc）。user=玩家名，供卡文本中的 {{user}} 宏。"""
    card = card_from_npc(npc)
    goals = str(npc.get("goals", "") or "")
    secret = str(npc.get("secret", "") or "")
    role = str(npc.get("role", "") or npc.get("affiliation", "")
               or "身份不明")
    template_vars = {
        "name": card.name,
        "role": role,
        "description": card.description or "（模组未详述，按身份常理行事。）",
        "personality": card.personality or "未特别说明，按身份常理行事。",
        "goals": f"动机与目标：{goals}\n" if goals else "",
        "secret": (f"底牌（仅供把握分寸，不主动泄露）：{secret}\n" if secret else ""),
    }
    st_card = dataclasses.replace(card, description="", personality="")
    system_prompt = assemble_system(
        st_card, _anon_persona(user), main_default=NPC_SYSTEM_TEMPLATE,
        template_vars=template_vars)
    return Agent(
        agent_id=f"npc:{npc['id']}", name=card.name,
        system_prompt=system_prompt,
        tool_names=[],                       # 空 = 全量下发（say-only registry）
        model=model, temperature=0.9, max_tokens=300)


# ---------------------------------------------------------------- AI PC（玩家版）
def make_aipc_agent(ch: Character, card: CharacterCard,
                    persona: Persona | None, model: str, *,
                    scenario: str = "", slots_hint: str = "") -> Agent:
    """玩家角色的 AI 代打 Agent（docs/aipc-design.md §2.1）。

    与同伴版 make_pc_agent 的差异：
    - system_prompt 经 trpg.st.assemble_system 按〔ST 规格 §3.2〕Prompt Manager
      默认序组装：main（PC_SYSTEM_TEMPLATE 经 template_vars 预格式化，卡
      system_prompt 可覆盖并展开 {{original}}）→ persona → scenario（"Scenario: …"）
      → 对话示例，全文过宏渲染（{{user}}/{{char}}/{{roll::…}} 等）。
    - tool_names=[]：空列表 = 注册表全部工具下发（registry.schemas 语义）——
      探索 registry 的 say/explore_intent/explore_act 与战斗 registry 的
      say/combat_action/explore_intent 全量可见。
    - 现状行由规则层现算（PW/伤势/护甲 SP/幸运/人性）；slots_hint 仅供调用方
      覆盖，且只接受按 RED 口径写的提示（_clean_hint 白名单）。
    - 卡的 description/personality 已由 template_vars 填进模板的
      人设/语气槽位，组装时以空副本传入避免同一段文本双份进提示词。"""
    if scenario:
        card = dataclasses.replace(card, scenario=scenario)
    role = _role_key(ch)
    template_vars = {
        "name": card.name or ch.name,
        "role_name": ch.role_name,
        "role_ability": _role_ability(ch) or "（未展开）",
        "role_rank": str(ch.role_rank),
        "persona": card.description,
        "voice": card.personality,
        "weapons": _weapon_text(ch.weapons),
        "status": _status_text(ch, slots_hint),
        "tactics": TACTICS.get(role, "见机行事。"),
        "explore_hint": _EXPLORE_HINT_AIPC,
    }
    # 人设/语气进模板槽位后，卡上同字段不再重复成独立 section
    st_card = dataclasses.replace(card, description="", personality="")
    system_prompt = assemble_system(
        st_card, persona, main_default=PC_SYSTEM_TEMPLATE,
        template_vars=template_vars)
    return Agent(
        agent_id=f"aipc:{ch.name}", name=ch.name,
        system_prompt=system_prompt,
        tool_names=[],                       # 空 = 全量下发（registry.schemas）
        model=model, temperature=0.5, max_tokens=500)
