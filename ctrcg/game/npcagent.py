"""NPCDirector —— NPC 子智能体调度器（NPC = DM 控制的 SubAgent，
docs/companion-npc-agents.md §2）。

与同伴（非玩家 PC = 独立 Agent）的层级对比：
- 同伴：独立 Agent——各有 harness 会话与完整决策面，编排器只管时机；
- NPC：**子智能体**——由 DM 的 npc_dialogue 工具调用按需拉起，只拿到
  对话面（say 一件工具），台词直接回到 DM 的工具结果里；DM 随时可用
  speak_as_npc 亲自说话覆盖它。控制权始终在 DM 手上。

每个 NPC 一份持久 harness 会话（npc:<id>）：记得此前与冒险者的交锋，
"NPC 会记仇、也会改主意"有了记忆载体。世界情报（引擎世界书 + 模组为
NPC 配的卡上角色书）每回合按桌面窗口扫描注入——SillyTavern World Info
通路与同伴/玩家同款。

降级：LLM 不可达、超时或 NPC 不开口时返回 ok=False / 空 lines，
DM 回退 speak_as_npc 自己扮演——绝不阻塞对局。
"""

from __future__ import annotations

from ..harness.loop import (DefaultAgentLoop, DuplicateCallGuard,
                            StepBudgetGuard, ToolTimeoutGuard)
from ..harness.registry import ToolRegistry
from ..st import card_from_npc, engine_worldinfo, merge, scan
from .agents import make_npc_agent

# 子代理单拍步数上限：say（可两句）+ 收束
NPC_MAX_STEPS = 3
# NPC 台词比桌边闲谈更收敛
NPC_TEMPERATURE = 0.9
NPC_MAX_TOKENS = 300
# 喂给 NPC 的桌面插话行数
NPC_TABLE_LINES = 8


class NPCDirector:
    """绑定 GameSession 的 NPC 子智能体调度器（仅 AI 模式构建）。"""

    def __init__(self, session, loop: DefaultAgentLoop):
        self.session = session
        # 专用小步数循环：线程池独立（不与 DM 战斗工具争抢 guard 池），
        # adapter/compactor 与主循环共享（与 AIPCController 同款）
        self._loop = DefaultAgentLoop(
            registry=None, adapter=loop.adapter,
            guard_step=StepBudgetGuard(max_steps=NPC_MAX_STEPS),
            guard_timeout=ToolTimeoutGuard(timeout=60.0),
            guard_dup=DuplicateCallGuard(),
            compactor=loop.compactor)

    # ================================================================ 入口
    def dialogue(self, npc_ref: str, instruction: str = "") -> dict:
        """DM 的 npc_dialogue 工具落点：让某个 NPC 以角色身份即兴开口。

        返回 {"ok": True, "lines": [...]} 或 {"ok": False, "error": ...}
        （调用方 DM 依此回退 speak_as_npc）。"""
        s = self.session
        npc, npc_id = self._resolve(npc_ref)
        if npc is None:
            known = "、".join(
                f"{d.get('name', nid)}({nid})" for nid, d in
                (s.module.npcs or {}).items()) or "（模组未定义 NPC）"
            return {"ok": False, "error": f"模组里没有 NPC「{npc_ref}」。"
                                          f"可用：{known}"}
        lines: list[str] = []
        reg = self._registry(npc["name"], lines)
        agent = make_npc_agent({**npc, "id": npc_id}, s.dm_agent.model,
                               user=s.state.player.name)
        task = f"轮到你说话了。对话焦点：{instruction or '（DM 未特别提示，按你的处境自然回应。）'}"
        try:
            result = self._loop.run(
                agent, s.npc_session(npc_id), task,
                context_messages=self._context(npc, instruction),
                registry=reg, guard_dup=DuplicateCallGuard())
            s._record_usage(agent, result)
        except Exception as e:      # noqa: BLE001 —— NPC 子代理绝不阻塞对局
            return {"ok": False, "error": f"NPC 子智能体不可用（{e}），"
                                          "请改用 speak_as_npc。"}
        if not lines:
            return {"ok": True, "lines": [],
                    "note": "NPC 沉默未语。可调整导演提示重试，或改用 speak_as_npc。"}
        for text in lines:
            s.ui.npc_line(npc["name"], text)
            s.table_log.append({"who": npc["name"], "text": text, "to": "",
                                "interrupt": False, "kind": "npc"})
        return {"ok": True, "lines": list(lines)}

    # ================================================================ 部件
    def _resolve(self, npc_ref: str) -> tuple[dict | None, str]:
        """按 id 或名字解析模组 NPC。"""
        npcs = self.session.module.npcs or {}
        ref = str(npc_ref or "").strip()
        if ref in npcs:
            return npcs[ref], ref
        for nid, n in npcs.items():
            if isinstance(n, dict) and str(n.get("name", "")) == ref:
                return n, nid
        return None, ref

    def _registry(self, npc_name: str, lines: list[str]) -> ToolRegistry:
        reg = ToolRegistry()

        def say(text: str):
            t = str(text).strip()
            if t:
                lines.append(t)
            return {"ok": True, "note": "已说出口，桌边都听见了。"}

        reg.register(
            "say", f"以 {npc_name} 的口吻说出一句台词（中文，1~2 句）。",
            {"type": "object",
             "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            say)
        return reg

    def _context(self, npc: dict, instruction: str) -> list[dict]:
        """每回合 user 上下文：场景/队伍/旗标/桌上此刻/世界书/导演提示。"""
        s = self.session
        scene = s.module.scene_by_id(s.state.scene_id) or {}
        wi_res = self._scan_worldinfo(npc)
        lines = [
            f"【场景】{scene.get('id', s.state.scene_id)}：{scene.get('location', '')}",
            f"【队伍】{s.state.party_snapshot()}",
            f"【旗标】{s.state.flags or '无'}",
        ]
        table = self._table_text()
        if table:
            lines.append("【桌上此刻】\n" + table)
        wi_block = "\n".join(x for x in (wi_res.before, wi_res.after) if x.strip())
        if wi_block:
            lines.append("【世界书】\n" + wi_block)
        lines.append(f"【导演提示】{instruction or '（无）'}")
        return [{"role": "user", "content": "\n".join(lines)}]

    def _scan_worldinfo(self, npc: dict):
        """引擎状态 WI + 该 NPC 卡上角色书合并扫描（世界书只影响叙事上下文）。"""
        s = self.session
        wi = engine_worldinfo(s.state)
        book = card_from_npc(npc).book
        if book is not None:
            wi = merge(wi, book)
        window = self._table_text() + "\n" + str(npc.get("desc", ""))
        return scan(wi, window.strip(), rng=s.state.rng)

    def _table_text(self) -> str:
        """桌面最近插话（只读，不清账——DM 的 _digest_table 负责消化）。"""
        lines = []
        for l in self.session.table_log[-NPC_TABLE_LINES:]:
            mark = "（抢话）" if l.get("interrupt") else ""
            to = f"（对{l['to']}）" if l.get("to") else ""
            lines.append(f"{l['who']}{mark}{to}：「{l['text']}」")
        return "\n".join(lines)
