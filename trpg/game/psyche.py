"""Psyche —— AI 剧场里的「技能之声」。

《极乐迪斯科》最有辨识度的叙事装置，是技能会用自己的口吻插话：逻辑拆穿谎言，
共情读出恐惧，意志按住发抖的手。本模块把这套「内心合唱」做成纯本地、零 token
的叙事层：它只读取角色卡与事件，不参与任何结算，也不改变游戏状态。

设计边界
--------
- **零 LLM 依赖**：即使离线，剧场也有声音；接入 LLM 后，它负责给 Agent 的
  表演加一层「角色脑内」的即时反应，而不是替代 Agent 说话。
- **零数值影响**：本模块不调用骰子、不写 flag，只根据引擎已经产出的结果选词。
- **确定性**：每个对局用独立的 ``random.Random``；不消耗 ``state.rng``，
  因此不会改变战斗/检定的随机序列。
- **可审计**：文本是静态语料 + 轻量关键词匹配，不生成事实性断言；涉及剧情
  的句子都只描述当前场景（由 ``SCENE_WHISPERS`` 指定）。

UI 侧只消费 ``whisper(...)`` 返回的 dict；数据面见 ``payload()``。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..rules import data as D


# ---------------------------------------------------------------------------
# 声部
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Voice:
    key: str
    name: str
    glyph: str
    color: str
    stat: str
    temperament: str


VOICES: tuple[Voice, ...] = (
    Voice("logic", "逻辑", "◈", "#7fa9bd", "int", "把恐惧换算成变量"),
    Voice("empathy", "共情", "◉", "#c58d86", "emp", "听见别人没说出口的那半句"),
    Voice("will", "意志", "▲", "#c7a45a", "will", "在腿软之前先让声音站稳"),
    Voice("reflex", "反应", "⌁", "#92b58a", "ref", "比危险早半秒抬头"),
    Voice("techno", "技术", "⌬", "#7fb7ac", "tech", "相信手感胜过相信人"),
    Voice("street", "街头", "✶", "#b38aaf", "cool", "知道这条街把真话藏在哪"),
    Voice("body", "体格", "◆", "#b57b62", "body", "先撞过去，再想为什么"),
)

_VOICE_BY_KEY = {v.key: v for v in VOICES}

#: 技能 → 声部。未列出的技能归到「逻辑」或按角色最高属性兜底。
SKILL_VOICE: dict[str, str] = {
    "deduction": "logic", "education": "logic", "library_search": "logic",
    "criminology": "logic", "accounting": "logic", "bureaucracy": "logic",
    "human_perception": "empathy", "conversation": "empathy",
    "interrogation": "will", "concentration": "will",
    "resist_torture_drugs": "will", "endurance": "will",
    "perception": "reflex", "evasion": "reflex", "initiative": "reflex",
    "athletics": "reflex",
    "electronics_security": "techno", "paramedic": "techno",
    "first_aid": "techno", "forgery": "techno", "pick_lock": "techno",
    "pick_pocket": "techno", "weaponstech": "techno", "cybertech": "techno",
    "streetwise": "street", "persuasion": "street", "bribery": "street",
    "conceal_reveal": "street", "personal_grooming": "street",
    "play_instrument": "street", "composition": "street", "local_expert": "street",
    "acting": "street", "dancing": "street", "business": "street",
    "stealth": "street",
    "brawling": "body", "melee_weapon": "body", "shoulder_arms": "body",
    "handgun": "reflex", "drive_land_vehicle": "reflex",
    "tactics": "logic", "tracking": "logic",
    "cryptography": "techno", "photography_film": "techno",
}

# 每个声部的台词。tone = "ok" / "fail" / "idle"。
VOICE_LINES: dict[str, dict[str, list[str]]] = {
    "logic": {
        "ok": [
            "两个反常放在一起就不是巧合。改地点、催时间、避开老地方——这是清场。",
            "数字不会紧张。会紧张的是报数字的人。",
            "他说了三句话，句句都在回答一个你没问出口的问题：他怕你留下。",
            "把耳朵借给沉默。沉默里那一下停顿，比整段话都诚实。",
        ],
        "fail": [
            "信息不够。你的推理在原地打转，像一条咬住自己尾巴的狗。",
            "这里缺一块拼图。你盯着空出来的那一块，越看越像一张脸。",
            "你听懂了他的每个字，却没有听懂他为什么现在说。",
        ],
        "idle": [
            "把现场重新排一遍。谁先到，谁最后走，谁的手一直没离开口袋。",
            "不要急着下结论。先看谁希望你快一点。",
            "一个谎话需要三样东西维持：时间、细节，以及一个愿意相信的人。",
        ],
    },
    "empathy": {
        "ok": [
            "他在害怕。不是怕你们发现钱是假的——是怕你们发现得太晚。",
            "你听见她把‘没事’说成了‘别问’。这两个词之间隔着一整条巷子。",
            "那个男人看你的眼神里没有恨，只有计算。恨是热的，计算是冷的。",
            "她把手挡在身前，不是为了防御，是为了藏住正在发抖的指尖。",
        ],
        "fail": [
            "你读不懂那张脸。兜帽下面只有一片阴影，和一点反光。",
            "共情这次没有递来答案，只递来一阵说不清的难受。",
            "你太想读懂他了，于是看见的全是自己希望看见的东西。",
        ],
        "idle": [
            "别只看嘴。看喉结、看呼吸、看谁在你说完话之后才想起要眨眼。",
            "每个人都在演。问题是，他演给谁看。",
            "有些真话只能从一个人躲避的姿势里读出来。",
        ],
    },
    "will": {
        "ok": [
            "手别抖。声音压低半度。你现在需要的不是勇气，是让别人相信你有。",
            "站住。脚底的混凝土会替你保守这个秘密。",
            "疼可以等。等这件事结束，或者等你死了，再慢慢疼。",
            "把呼吸数到四。数到四之后再抬头。",
        ],
        "fail": [
            "你的心跳太快了。快到你开始怀疑，身体是不是已经替你做了决定。",
            "撑住这件事说起来容易。你的膝盖正在用另一种语言反对。",
            "你咬紧牙关，但恐惧已经从别的地方漏了出来。",
        ],
        "idle": [
            "不要眨眼。有人正等着从你的眼睛里看出下一步。",
            "害怕不丢人。让害怕替你做决定才丢人。",
            "如果你现在退，之后每一步都会比这一步更难。",
        ],
    },
    "reflex": {
        "ok": [
            "左边。铁皮后面。有人刚换过重心。",
            "枪口抬高了两厘米。他在找你的胸口，不是在找你的脸。",
            "雨声里多了一道不属于雨的声音。金属，和很轻的摩擦。",
            "你的身体先看见了。等脑子追上来时，手指已经扣在扳机护圈上。",
        ],
        "fail": [
            "雨声太密。你听见了很多东西，却分不清哪一个是要命的那一个。",
            "你迟了半拍。半拍在夜之城足够让一个人从站着变成躺着。",
            "反光晃了一下。等你再看时，那里已经什么都没有了。",
        ],
        "idle": [
            "别盯着说话的人。看他身后，看退路，看哪扇门是虚掩的。",
            "先找掩体，再找目标。活下来的人才有第二回合。",
            "数一下在场的人。再数一下影子。",
        ],
    },
    "techno": {
        "ok": [
            "锁芯的磨损告诉你，它被开过不止一次。有人比你们更早到。",
            "线路板上的烧痕像一张揉皱的地图。你认得每一道，但拼不出出口。",
            "伺服电机在低鸣。三秒之后，它会把那扇门自己打开。",
            "弹匣里的弹簧声不对。还剩四发，不是六发。",
        ],
        "fail": [
            "这不是你熟悉的系统。它老、脏、被改装过，而且正在嘲笑你。",
            "你的手很稳，工具不合适。夜之城的机器从不迁就人。",
            "你听见保险丝烧断的声音。然后灯灭了，所有人都想起你还在门边。",
        ],
        "idle": [
            "设备不会撒谎。撒谎的是操作设备的人。",
            "先看电源，再看线路，最后才看屏幕。屏幕最会骗人。",
            "别碰那个开关。它看起来像开关，实际上更像一个邀请。",
        ],
    },
    "street": {
        "ok": [
            "海伍德的人不会把‘一切都好’说得这么快。这句话是背出来的。",
            "他袖口有干掉的泥，鞋底却是干净的。他不是从外面回来的。",
            "这条街的规矩是：先问价，再问名字，最后才问谁死了。",
            "他们叫你‘朋友’，通常意味着下一句要你免费做点什么。",
        ],
        "fail": [
            "这条街对你来说还是太干净了。干净得没有一句真话。",
            "你摆出了正确的姿态，但用错了街区。",
            "老练不等于正确。你赌错了对方吃哪一套。",
        ],
        "idle": [
            "看招牌。真正的权力从不挂在最亮的那块牌子上。",
            "夜之城的规矩一直在变。唯一不变的是，先活下来的人写规则。",
            "别急着亮底牌。让他们以为你还有一张。",
        ],
    },
    "body": {
        "ok": [
            "你的身体先一步动了。拳头到的时候，脑子还在后面追。",
            "骨头发出很轻的一声。不是你的。",
            "你把重心压下去，像把一颗钉子敲进地里。",
            "疼是信息。它告诉你哪里还能用。",
        ],
        "fail": [
            "你撞上了比自己更硬的东西。世界短暂地变成一片白色。",
            "这一下没打中。你的肩膀替你承担了答案。",
            "身体说不行。你听见了，但没打算听。",
        ],
        "idle": [
            "先站稳。所有的技术都建立在双脚还踩着地这件事上。",
            "呼吸乱的时候，力气会从最需要它的地方溜走。",
            "别硬扛。硬扛是给死人用的词。",
        ],
    },
}

#: 场景开场/换场时的内心声音（按 scene id）。
SCENE_WHISPERS: dict[str, list[tuple[str, str]]] = {
    "south_night_city_loft": [
        ("logic", "那部一次性手机不该在今晚响。它只在两种时候响：大活，或者死人。"),
        ("empathy", "Lazlo 的呼吸断了一拍。他旁边有人。"),
        ("street", "安全屋的窗帘拉得太严了。严到像是在躲自己人。"),
    ],
    "prep_and_drive": [
        ("reflex", "后视镜里那辆车跟了三个路口。也可能只是同路。"),
        ("street", "海伍德不会为陌生人留车位。你们最好别停得太深。"),
        ("logic", "四十分钟。够不够，取决于你们愿意先相信谁。"),
    ],
    "heywood_meet": [
        ("empathy", "他找钥匙找得太久了。久到足够让巷子两头的人就位。"),
        ("logic", "箱子上的手铐是新的。他不是在保护钱，是在保护自己。"),
        ("reflex", "北口的风停了。有人刚刚挡在那里。"),
    ],
    "lazlo_rescue": [
        ("body", "血味盖过了铁锈味。有人在这里被拆开过。"),
        ("empathy", "Lazlo 看见你们的时候，先看的是你们身后。"),
        ("will", "别让他从你的脸上读出‘没救了’。先做事，再难过。"),
    ],
    "warehouse_approach": [
        ("techno", "卷帘门的电机还在发热。里面的人刚进去不久。"),
        ("street", "警察的巡逻车没熄火。他们在等什么，或者等谁。"),
        ("logic", "三条路。杀进去、摸进去、谈进去。第四条要你们自己写。"),
    ],
    "aftermath": [
        ("will", "天快亮了。活下来的人才有资格觉得累。"),
        ("logic", "这笔钱能洗干净。问题是，你们还洗得干净吗？"),
        ("empathy", "有人没有回头。你知道那不是因为不在乎。"),
    ],
}

#: NPC 台词后的读心短句（按关键词触发，避免机械重复）。
NPC_KEYWORDS: list[tuple[tuple[str, ...], str, str]] = [
    (("钱", "欧元", "报酬", "账"), "empathy", "他提钱的时候，眼睛先看了一眼出口。"),
    (("枪", "开火", "死", "杀"), "reflex", "说到‘死’字时，他的手从桌沿缩了回去。"),
    (("Lazlo", "拉兹洛", "中间人"), "logic", "他把 Lazlo 的名字放在句尾。人在句尾最容易说漏。"),
    (("警察", "条子", "NCPD"), "street", "他骂警察的时候没有压低声音。要么他不怕，要么他希望有人听见。"),
    (("时间", "分钟", "快"), "logic", "他一直在催。催的人通常不是掌握时间的人，是被时间追着的人。"),
    (("抱歉", "对不起", "没办法"), "empathy", "道歉是真的。真到让人更难受——他已经在替你们难过了。"),
]


def _pick(rng: random.Random, values: list[str]) -> str:
    return values[rng.randrange(len(values))] if values else ""


def _voice_payload(v: Voice, value: int) -> dict:
    return {"key": v.key, "name": v.name, "glyph": v.glyph, "color": v.color,
            "stat": v.stat, "value": int(value), "temperament": v.temperament}


class PsycheEngine:
    """把角色卡与事件折成「技能之声」。

    ``player`` 是 ``rules.character.Character``；构造后不再读取可变状态，
    因此 UI 线程可以安全调用。全部随机使用自己的 ``_rng``。
    """

    def __init__(self, player, seed: int | str = 0, *, max_voices: int = 7):
        self.player = player
        self._rng = random.Random(f"psyche:{seed}:{getattr(player, 'name', '')}")
        self._used: set[str] = set()
        self._scene = ""
        self._voices = self._build_voices(max_voices)
        self._voice_keys = {v.key for v in self._voices}

    # ------------------------------------------------------------ 数据面
    def _build_voices(self, limit: int) -> list[Voice]:
        """按角色属性保留最高声部；永远保留逻辑与共情，避免读心层缺角。"""
        ranked = sorted(
            VOICES,
            key=lambda v: (-int(self.player.stat(v.stat)), VOICES.index(v)))
        keep: list[Voice] = [v for v in ranked if v.key in ("logic", "empathy")]
        for v in ranked:
            if len(keep) >= limit:
                break
            if v not in keep:
                keep.append(v)
        # 让 UI 的顺序稳定在 VOICES 顺序（而不是强度顺序），强度用数值表示
        return [v for v in VOICES if v in keep]

    def payload(self) -> dict:
        return {
            "name": getattr(self.player, "name", ""),
            "role": getattr(self.player, "role_name", "") or getattr(self.player, "role", ""),
            "voices": [_voice_payload(v, self.player.stat(v.stat)) for v in self._voices],
        }

    def set_scene(self, scene_id: str) -> None:
        self._scene = str(scene_id or "")

    # ------------------------------------------------------------ 选声
    def _voice_for_skill(self, skill: str) -> Voice:
        key = SKILL_VOICE.get(str(skill or ""))
        if key in self._voice_keys:
            return _VOICE_BY_KEY[key]
        # 未映射技能：按挂钩属性选声部（D.SKILLS[skill]["stat"]）
        spec = D.SKILLS.get(str(skill or "")) or {}
        want = {"int": "logic", "emp": "empathy", "will": "will", "ref": "reflex",
                "tech": "techno", "cool": "street", "body": "body"}.get(
                    spec.get("stat"), "")
        if want in self._voice_keys:
            return _VOICE_BY_KEY[want]
        return self._voices[0]

    def _make(self, voice: Voice, text: str, tone: str) -> dict:
        # 去重：同一句在本局只出现一次，用尽后重置池子（长局仍可能重复）
        text = str(text or "").strip()
        if not text:
            return {}
        if text in self._used:
            for _ in range(4):
                lines = VOICE_LINES.get(voice.key, {}).get(tone if tone in ("ok", "fail") else "idle", [])
                if not lines:
                    break
                cand = _pick(self._rng, lines)
                if cand not in self._used:
                    text = cand
                    break
            else:
                return {}
        self._used.add(text)
        return {"voice": voice.key, "name": voice.name, "glyph": voice.glyph,
                "color": voice.color, "stat": voice.stat,
                "value": int(self.player.stat(voice.stat)), "tone": tone,
                "text": text, "style": voice.temperament}

    # ------------------------------------------------------------ 事件
    def scene_line(self) -> dict:
        entries = SCENE_WHISPERS.get(self._scene) or []
        if not entries:
            v = self._voices[0]
            return self._make(v, "先别急着说话。看看这个房间先替你保留了哪些秘密。", "idle")
        order = [(k, t) for k, t in entries if k in self._voice_keys] or entries
        key, text = order[self._rng.randrange(len(order))]
        return self._make(_VOICE_BY_KEY.get(key, self._voices[0]), text, "idle")

    def check_line(self, skill: str, success: bool, detail: str = "") -> dict:
        v = self._voice_for_skill(skill)
        tone = "ok" if success else "fail"
        text = _pick(self._rng, VOICE_LINES.get(v.key, {}).get(tone, []))
        return self._make(v, text, tone)

    def npc_line(self, who: str, text: str) -> dict:
        low = str(text or "")
        for keys, vkey, line in NPC_KEYWORDS:
            if any(k.lower() in low.lower() for k in keys) and vkey in self._voice_keys:
                return self._make(_VOICE_BY_KEY[vkey], line, "idle")
        if len(low) < 6:
            return {}
        v = _VOICE_BY_KEY.get("empathy") or self._voices[0]
        line = _pick(self._rng, VOICE_LINES.get(v.key, {}).get("idle", []))
        return self._make(v, line, "idle")

    def narration_line(self, text: str) -> dict:
        low = str(text or "")
        if not low:
            return {}
        if any(k in low for k in ("枪", "血", "尸体", "弹壳", "疼", "伤")):
            v = _VOICE_BY_KEY.get("body") or self._voices[0]
            tone = "idle"
        elif any(k in low for k in ("说", "回答", "沉默", "看", "眼睛", "声音")):
            v = _VOICE_BY_KEY.get("empathy") or self._voices[0]
            tone = "idle"
        elif any(k in low for k in ("门", "锁", "线", "系统", "机器", "灯")):
            v = _VOICE_BY_KEY.get("techno") or self._voices[0]
            tone = "idle"
        else:
            v = self._voices[self._rng.randrange(max(1, min(3, len(self._voices))))]
            tone = "idle"
        line = _pick(self._rng, VOICE_LINES.get(v.key, {}).get("idle", []))
        return self._make(v, line, tone)

    def combat_line(self, actor: str, text: str) -> dict:
        low = str(text or "")
        if any(k in low for k in ("中弹", "受伤", "倒地", "濒死", "血")):
            v = _VOICE_BY_KEY.get("will") or self._voices[0]
        elif any(k in low for k in ("开枪", "射击", "子弹", "命中")):
            v = _VOICE_BY_KEY.get("reflex") or self._voices[0]
        else:
            v = _VOICE_BY_KEY.get("street") or self._voices[0]
        return self._make(v, _pick(self._rng, VOICE_LINES.get(v.key, {}).get("idle", [])), "idle")

    def crit_line(self, who: str) -> dict:
        v = _VOICE_BY_KEY.get("body") or self._voices[0]
        return self._make(v, "那一瞬间你听见的不是枪声，是身体里某样东西永久改变的声音。", "fail")

    def loop_line(self, repeats: int) -> dict:
        """Agent 原地打转时的自嘲式提示（UI 剧场专用，不改变引擎决策）。"""
        v = _VOICE_BY_KEY.get("logic") or self._voices[0]
        return self._make(
            v, "同一个念头又绕回来了。别把它当成深思熟虑——它只是害怕往前走。",
            "idle") if repeats >= 3 else {}


# 兼容更短的导入：``from trpg.game.psyche import PsycheEngine``
__all__ = ["Voice", "VOICES", "PsycheEngine", "VOICE_LINES", "SCENE_WHISPERS"]
