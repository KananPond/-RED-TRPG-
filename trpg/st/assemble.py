"""提示词组装与按深度注入、预算裁剪（docs/aipc-design.md §1.4）。

两层分工：
- 静态层（system）：assemble_system 按〔ST 规格 §3.2〕Prompt Manager 默认序把
  卡渲染成**单条 system**，调用方填进 Agent.system_prompt；
- 动态层（每回合 context）：build_context_block 产出 user 消息，
  经 loop.run(context_messages=…)，"模型可见 ⟺ 已记录"不变量自动成立。

计量复用 harness.loop.estimate_tokens（CJK≈1 token/字，ASCII≈1/4 字符）。
本模块只依赖 harness 与 st 内部，不 import trpg.game.*。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..harness.loop import estimate_tokens
from .card import CharacterCard, Persona
from .macros import MacroContext, expand_original, render

_START_RE = re.compile(r"<START>", re.IGNORECASE)

# 同深度内 role 次序：User → Assistant → System（§3.2/§8）
_ROLE_RANK = {"user": 0, "assistant": 1, "system": 2}


@dataclass(frozen=True, order=True)
class Injection:
    """按深度注入条目（§8）。depth 从消息列表末尾数起：0=最新消息之后，
    越大越靠前；同 depth+role 内 order 小者先发送。"""

    depth: int
    role: str = "system"             # system / user / assistant
    order: int = 100
    text: str = field(default="", compare=False)


def split_examples(mes_example: str) -> list[str]:
    """按 <START> 分块（§2.2）；标记本身不进提示词。
    无标记且非空 → 整体一块；空白块丢弃。"""
    if not mes_example or not mes_example.strip():
        return []
    blocks = []
    for part in _START_RE.split(str(mes_example)):
        part = part.strip()
        if part:
            blocks.append(part)
    return blocks


def assemble_system(card: CharacterCard, persona: Persona | None, *,
                    wi_before: str = "", wi_after: str = "",
                    main_default: str = "", role_lines: str = "",
                    template_vars: dict[str, str] | None = None) -> str:
    """把卡渲染成**单条 system**，顺序照〔ST 规格 §3.2〕Prompt Manager 默认序：

    main（card.system_prompt 覆盖 main_default，{{original}} 展开=main_default）
    → role_lines（附加行，如职业机制速览）→ wiBefore → personaDescription
    → charDescription → charPersonality → scenario（"Scenario: …"式前缀）
    → wiAfter → dialogueExamples（分块拼接）。
    空段跳过；全文最后统一过 macros.render。

    main_default 含 str.format 占位符时必须经 template_vars 预格式化
    （缺键 KeyError，构造期 fail-fast；PC_SYSTEM_TEMPLATE 实有 9 个占位符）——
    先 str.format 填充模板占位符，format 产物与卡文本再整体过 macros.render
    展开卡文本里的 {{…}} 宏：两套语法（单花括号 str.format / 双花括号宏）各司其职。
    persona.position=none / at_depth 时不进 system（由调用方另行消费）。
    """
    # 1) main_default 先过 str.format（缺占位符键 → KeyError，fail-fast）
    try:
        main_default = (main_default or "").format(**(template_vars or {}))
    except (KeyError, IndexError, ValueError):
        raise
    # 2) main = 卡覆盖 或 默认模板；卡里的 {{original}} 展开为默认模板全文
    main = expand_original(card.system_prompt, main_default) if card.system_prompt \
        else main_default
    # 3) 依序收集非空段
    sections: list[str] = []
    if main.strip():
        sections.append(main.strip())
    if role_lines.strip():
        sections.append(role_lines.strip())
    if wi_before.strip():
        sections.append(wi_before.strip())
    if persona is not None and persona.position == "in_prompt" \
            and persona.description.strip():
        sections.append(persona.description.strip())
    if card.description.strip():
        sections.append(card.description.strip())
    if card.personality.strip():
        sections.append(card.personality.strip())
    if card.scenario.strip():
        sections.append("Scenario: " + card.scenario.strip())
    if wi_after.strip():
        sections.append(wi_after.strip())
    examples = split_examples(card.mes_example)
    if examples:
        sections.append("\n".join(examples))
    # 4) 宏渲染（{{user}}=persona 名，{{char}}=卡名，卡字段经 extra 自引用）
    ctx = MacroContext(
        user=(persona.name if persona else "") or "",
        char=card.name,
        persona=(persona.description if persona else "") or "",
        extra={"description": card.description, "personality": card.personality,
               "scenario": card.scenario},
    )
    return render("\n\n".join(sections), ctx)


def build_context_block(*, lead: str, scene: str, party: str, flags: str,
                        table: str = "", last_narration: str = "",
                        injections: list[Injection] | None = None,
                        persona_note: str = "", phi: str = "",
                        wi: str = "") -> list[dict]:
    """每回合 user 上下文块（沿用 dm_turn 现有形状，向后兼容）：

    【场景】【队伍】【旗标】→【桌上插话】→【最近旁白】→【世界书】(P1)→
    【注入】(depth 降序、role 次序 User→Assistant→System、order 升序，§8)→
    【玩家】(persona_note)→ lead（任务行，块尾）。
    phi 非空时作为**最后一条** user 消息单独返回（PHI 语义=历史之后、最后发送）。
    返回值即 loop.run(context_messages=…)，天然进事件日志。
    """
    lines = [f"【场景】{scene}", f"【队伍】{party}", f"【旗标】{flags or '无'}"]
    if table and table.strip():
        lines.append("【桌上插话】\n" + table.strip())
    if last_narration and last_narration.strip():
        lines.append("【最近旁白】" + last_narration.strip())
    if wi and wi.strip():
        lines.append("【世界书】\n" + wi.strip())
    ordered = _ordered_injections(injections or [])
    if ordered:
        lines.append("【注入】\n" + "\n".join(i.text.strip() for i in ordered))
    if persona_note and persona_note.strip():
        lines.append("【玩家】" + persona_note.strip())
    if lead and lead.strip():
        lines.append(lead.strip())
    out = [{"role": "user", "content": "\n".join(lines)}]
    if phi and phi.strip():
        out.append({"role": "user", "content": phi.strip()})
    return out


def inject_at_depth(messages: list[dict], injections: list[Injection]) -> list[dict]:
    """真·按深度注入（P1 接线 loop 时启用；P0 由 build_context_block 以
    "depth 即块内排序键"的退化形式消费）。

    depth 从消息列表**末尾**数起：depth=0 插在最后一条之后，depth=N 插在
    倒数第 N 条消息之前；同深度 role 次序 User→Assistant→System、order 小者先。
    （〔ST 规格〕"depth 4 在最近 3 条之前"与本文档口径差一位，按本文档实现。）
    """
    ordered = _ordered_injections(injections or [])
    n = len(messages)
    buckets: dict[int, list[dict]] = {}
    for inj in ordered:
        idx = min(max(n - max(int(inj.depth), 0), 0), n)
        buckets.setdefault(idx, []).append({"role": inj.role, "content": inj.text})
    out: list[dict] = []
    for i in range(n + 1):
        out.extend(buckets.get(i, []))
        if i < n:
            out.append(messages[i])
    return out


def fit_to_budget(sections: list[tuple[str, str]],
                  budget_tokens: int) -> list[tuple[str, str]]:
    """组装期预算裁剪（§7 牺牲顺序）。sections=[(标记, 文本), …]：

    1) 永不裁：main/system、常驻字段（description/personality/scenario）、
       persona、nudge、phi、wi（世界书已自行按独立预算扫描）；
    2) 先整块挤示例区（标记以 example 开头或为 mes_example，按 <START> 分块，
       后面的块先丢）；
    3) 再丢最旧的上下文行（其余标记视为时间序文本，从最早的行开始丢）。
    计量用 harness.loop.estimate_tokens（CJK≈1/字）。全部可裁内容丢光仍超预算时，
    保留常驻段原样返回（常驻字段与强制 nudge 永不挤）。
    """
    secs = [(str(m), str(t)) for m, t in sections if t and str(t).strip()]
    if budget_tokens is None or budget_tokens <= 0:
        return secs

    def total(items: list[tuple[str, str]]) -> int:
        return sum(estimate_tokens(t) for _, t in items)

    if total(secs) <= budget_tokens:
        return secs
    # 条目化：[(标记, 行列表 | 示例块列表, 类别)]
    entries: list[list[str]] = []
    kinds: list[str] = []
    for marker, text in secs:
        kind = _category(marker)
        entries.append(split_examples(text) if kind == "example"
                       else text.split("\n"))
        kinds.append(kind)

    def used() -> int:
        return sum(estimate_tokens("\n".join(lines)) for lines in entries if lines)

    # 2) 先挤示例块（逐块，后面的块先丢）
    for i, kind in enumerate(kinds):
        if used() <= budget_tokens:
            break
        if kind != "example":
            continue
        while entries[i] and used() > budget_tokens:
            entries[i].pop()
    # 3) 再丢最旧的上下文行（最早的节、节内最早的行先丢）
    for i, kind in enumerate(kinds):
        if used() <= budget_tokens:
            break
        if kind != "drop":
            continue
        while entries[i] and used() > budget_tokens:
            entries[i].pop(0)
    out: list[tuple[str, str]] = []
    for (marker, _), lines in zip(secs, entries):
        if lines:
            out.append((marker, "\n".join(lines)))
    return out


# ---------------------------------------------------------------- 内部
def _category(marker: str) -> str:
    m = marker.strip().lower()
    if m in ("main", "system", "description", "personality", "scenario",
             "persona", "nudge", "phi", "wi", "wi_before", "wi_after"):
        return "keep"
    if m.startswith("example") or m == "mes_example":
        return "example"
    return "drop"


def _ordered_injections(injections: list[Injection]) -> list[Injection]:
    """depth 降序（越大越靠前）→ role User→Assistant→System → order 升序；
    仅保留有内容的条目。"""
    items = [i for i in injections if i.text and i.text.strip()]
    items.sort(key=lambda i: (-max(int(i.depth), 0),
                              _ROLE_RANK.get(str(i.role), 2), int(i.order)))
    return items
