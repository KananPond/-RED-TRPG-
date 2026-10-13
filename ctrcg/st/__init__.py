"""ctrcg.st —— SillyTavern 式提示词机制纯库（docs/aipc-design.md §1.0）。

角色卡 / persona / 宏 / 世界书 / 提示词组装 / 按深度注入 / 预算裁剪。
分层约束：本包只依赖 stdlib、ctrcg.rules 与 ctrcg.harness，
**不得 import ctrcg.game.***（纯机制、可单测）；
编排层在 ctrcg/game/aipc.py（P1），单向依赖本包。
开关关闭（无 AIPCController）时本包全部零参与。
规则词汇口径：《赛博朋克 RED》——技能用 D.SKILLS 的中文名，
难度值一律写 DV（worldinfo.engine_worldinfo），货币为欧元（eb）。
"""

from .card import (CharacterCard, Persona, card_from_companion, card_from_dm,
                   card_from_npc, card_from_player)
from .macros import MacroContext, render
from .worldinfo import (WIResult, WLEntry, WorldInfo, engine_worldinfo,
                        merge, scan, wi_from_book)
from .assemble import (Injection, assemble_system, build_context_block,
                       fit_to_budget, inject_at_depth, split_examples)

__all__ = [
    "CharacterCard", "Persona", "card_from_companion", "card_from_dm",
    "card_from_npc", "card_from_player",
    "MacroContext", "render",
    "WLEntry", "WorldInfo", "WIResult", "wi_from_book", "engine_worldinfo",
    "scan", "merge",
    "Injection", "assemble_system", "build_context_block", "inject_at_depth",
    "fit_to_budget", "split_examples",
]
