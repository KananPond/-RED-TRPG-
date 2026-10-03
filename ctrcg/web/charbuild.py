"""从 Web 表单数据构建玩家角色（服务端校验）。

属性分配采用 4d6 去最低的掷骰结果：
- ability_mode="rolled"：前端携带本次掷骰结果 rolled 与六项分配 scores，
  服务端校验分配恰好使用该次掷骰结果；
- ability_mode="recommended"：服务端代为掷骰并按职业推荐优先级分配。
种族加值沿用 BG3 规则：任意两项属性 +2/+1（bonus2/bonus1，可自选）。
"""

from __future__ import annotations

import random

from ..rules import data as D
from ..rules.character import Character
from ..rules.dice import roll_scores
from ..ui.charcreate import aipc_card_fields, assign_by_recommended

STYLE_KEYS = {"archery", "defense", "dueling", "two_weapon", "great_weapon"}
_ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")


def build_character(spec: dict, rng: random.Random | None = None) -> Character:
    """校验并构建玩家角色。任何非法输入抛 ValueError。

    rng：掷骰属性（ability_mode="recommended"）使用的随机源；
    传入已按 seed 播种的实例可保证开局完全可复现。"""
    if not isinstance(spec, dict):
        raise ValueError("请求体必须是 JSON 对象")
    name = str(spec.get("name", "")).strip()[:16] or "无名者"
    race = spec.get("race")
    klass = spec.get("klass")
    background = spec.get("background")
    if race not in D.RACES:
        raise ValueError("未知种族")
    if klass not in D.CLASSES:
        raise ValueError("未知职业")
    if background not in D.BACKGROUNDS:
        raise ValueError("未知背景")

    scores = _resolve_scores(spec, klass, rng)
    bonus2 = spec.get("bonus2") or None
    bonus1 = spec.get("bonus1") or None
    skill_choices = spec.get("skills")
    if not isinstance(skill_choices, list) or not skill_choices:
        raise ValueError("请选择职业技能")
    style = str(spec.get("fighting_style") or "")
    if klass == "fighter" and style not in STYLE_KEYS:
        raise ValueError("战士必须选择战斗风格")
    if klass != "fighter":
        style = ""
    try:
        return Character.create(
            name=name, race=race, klass=klass, background=background,
            scores=scores, skill_choices=[str(s) for s in skill_choices],
            fighting_style=style, bonus2=bonus2, bonus1=bonus1)
    except ValueError as e:
        raise ValueError(f"角色构建失败：{e}")


def build_aipc_card(spec: dict, name: str = "") -> dict | None:
    """从 start spec 提取玩家人设，产出 AI PC 的角色卡素材（aipc-design §3.3/§4.1）。

    返回与 st.card.CharacterCard.from_dict 兼容的序列化形态：st（ctrcg.st.card）
    未落地时以纯 dict 暂存于 GameState.aipc_card 并随存档往返，由
    server.build_session 接缝在 st 就绪后重建为 CharacterCard。
    四个字段全为空时返回 None——接缝将回退 card_from_player(state.player) 的
    引擎兜底文案（种族·职业·背景 + 战术风格摘要）。字段经 aipc_card_fields
    清洗：纯文本、各 ≤AIPC_FIELD_LIMIT 字符（超长截断，与姓名截断同风格）。"""
    fields = aipc_card_fields(spec)
    if not fields:
        return None
    card = {"name": str(name or "")}
    card.update(fields)
    return card


def _resolve_scores(spec: dict, klass: str, rng: random.Random | None = None) -> dict[str, int]:
    mode = spec.get("ability_mode", "rolled")
    if mode == "rolled":
        rolled = spec.get("rolled")
        if (not isinstance(rolled, list) or len(rolled) != 6
                or not all(isinstance(v, int) and 3 <= v <= 18 for v in rolled)):
            raise ValueError("掷骰结果无效（需要六个 3-18 的数值）")
        scores = spec.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(_ABILITY_KEYS):
            raise ValueError("需要完整的六项属性分配")
        try:
            values = [int(v) for v in scores.values()]
        except (TypeError, ValueError):
            raise ValueError("属性值必须是整数")
        if sorted(values) != sorted(rolled):
            raise ValueError("属性分配必须恰好使用本次掷骰结果")
        return {k: int(v) for k, v in scores.items()}
    if mode == "recommended":
        # 服务端代为掷骰（4d6 去最低），按职业推荐优先级分配
        return assign_by_recommended(roll_scores(rng or random.Random()), klass)
    raise ValueError("未知的能力分配方式")
