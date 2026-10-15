"""从 Web 表单数据构建玩家角色（服务端校验）。

《赛博朋克 RED》不再掷骰生成属性：角色从模组的**预设角色**（pregens）里挑一个。
- 玩家选定 `pregen`（预设角色 key）与自己的 `name`（街头名/绰号）；
- 可另选 `companions`：本模组中其余预设角色里，哪些作为 AI 队友同行
  （缺省用模组的 `default_companions`）。
服务端只做校验与装配，一切数值来自模组数据（角色卡上的属性/技能/装备已定）。
"""

from __future__ import annotations

import random

from ..rules import lifepath as LIFE
from ..rules.character import Character
from ..ui.charcreate import (aipc_card_fields, default_companion_keys,
                             pregens_of)


def roll_lifepath(char: Character, rng=None) -> dict:
    """为角色走一遍《轻松模式》的人生之路（第 29-34 页的 11 张表）。

    结果挂在 ``Character.lifepath`` 上，供 DM 在剧情里调用（朋友来帮忙、
    仇人在尾声发誓复仇）。掷骰只决定履历，不影响任何战斗数值。
    """
    rng = rng or random.Random()
    result = LIFE.roll_all(rng)
    char.lifepath = result
    return result


def build_character(spec: dict, module, rng=None) -> Character:
    """校验并构建玩家角色。任何非法输入抛 ValueError。

    RED 建卡不掷属性：从模组的**预设角色**里挑一个，再走一遍人生之路
    （``lifepath``）；``rng`` 用于人生之路的掷骰，便于测试复现。"""
    if not isinstance(spec, dict):
        raise ValueError("请求体必须是 JSON 对象")
    pregens = pregens_of(module)
    if not pregens:
        raise ValueError("该模组没有预设角色（pregens），无法建卡")
    key = str(spec.get("pregen") or "")
    if key not in pregens:
        raise ValueError("未知的预设角色")
    pregen = pregens[key]
    if not isinstance(pregen, dict):
        raise ValueError("预设角色数据损坏")
    name = str(spec.get("name", "")).strip()[:16]
    if not name:
        raise ValueError("请填写角色名字")
    try:
        # 角色卡数值全部来自预设；玩家只改名（预设的 name/handle 保持在 pregen 里）
        char = Character.from_pregen(key, pregen, player=True)
    except ValueError as e:
        raise ValueError(f"角色构建失败：{e}")
    char.name = name
    # 人生之路：spec 里给了现成结果就用（存档/重放），否则投骰
    given = spec.get("lifepath")
    if isinstance(given, dict) and given:
        char.lifepath = {str(k): v for k, v in given.items()}
    else:
        roll_lifepath(char, rng)
    return char


def companion_ids(module, spec: dict, player_key: str) -> list[str]:
    """解析同行队友的预设 key 列表：显式选择优先，否则模组的默认队友。

    过滤规则：必须在预设法内、不能是玩家自己、去重且保持预设法顺序。"""
    pregens = pregens_of(module)
    raw = spec.get("companions")
    if raw is None:
        chosen = list(default_companion_keys(module))
    elif isinstance(raw, (list, tuple)):
        chosen = [str(c) for c in raw]
    else:
        raise ValueError("队友选择格式非法")
    out: list[str] = []
    for key in chosen:
        if key not in pregens:
            raise ValueError(f"未知的预设角色：{key}")
        if key == player_key or key in out:
            continue
        out.append(key)
    return out


def build_companions(module, spec: dict, player_key: str) -> list[Character]:
    """按玩家选择装配 AI 队友（未选则用模组默认队友）。"""
    pregens = pregens_of(module)
    return [Character.from_pregen(key, pregens[key], kind="companion")
            for key in companion_ids(module, spec, player_key)]


def build_aipc_card(spec: dict, name: str = "") -> dict | None:
    """从 start spec 提取玩家人设，产出 AI PC 的角色卡素材（aipc-design §3.3/§4.1）。

    返回与 st.card.CharacterCard.from_dict 兼容的序列化形态：st（trpg.st.card）
    未落地时以纯 dict 暂存于 GameState.aipc_card 并随存档往返，由
    server.build_session 接缝在 st 就绪后重建为 CharacterCard。
    四个字段全为空时返回 None——接缝将回退 card_from_player(state.player) 的
    引擎兜底文案（职业·义体 + 战术风格摘要）。字段经 aipc_card_fields
    清洗：纯文本、各 ≤AIPC_FIELD_LIMIT 字符（超长截断，与姓名截断同风格）。"""
    fields = aipc_card_fields(spec)
    if not fields:
        return None
    card = {"name": str(name or "")}
    card.update(fields)
    return card
