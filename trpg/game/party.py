"""PartyDirector —— 全 AI 跑团的 PC 自主回合编排器。

与「玩家输入驱动」的 AI 模式不同，这里没有 Human Player：

- 每个 PC（包括主角）都是独立的 Agent，轮流自主决定这一拍说什么/做什么；
- PC 直接以角色身份行动，不模拟“玩家/OOC/桌边”；
- PC 只声明意图/说话/执行自我范围内的动作，世界仍由 DM 裁定；
- NPC 仍然只能由 DM 的 `npc_dialogue` 子智能体扮演。

编排器只负责回合顺序与节奏，不代写台词、不替 DM 裁定。
"""

from __future__ import annotations

import dataclasses

from ..harness.loop import DuplicateCallGuard
from ..harness.registry import ToolRegistry


PARTY_RULES = """【全 AI 跑团 · PC 自主】
桌上没有人类玩家。你和队伍里的其他 PC 都是自主的 Agent；每个人只控制自己的角色，
自己决定说什么、做什么、是否冒险。不要等待人类输入，也不要 OOC。
- 用 say 以角色身份说话（可以对队友/NPC 说）。
- 用 explore_intent 声明探索行动（搜查、潜行、谈判、拆锁、问路等），由 DM 裁定。
- 用 explore_act 处理休整、急救、稳定、采购、修甲、查看状态等自我行动。
- 用 hold 表示这一拍不行动（少用）。
不要替 DM 叙述世界，不要替别的 PC 做决定，不要读模组或使用尚未发生的信息。
像真实玩家一样：有目标、有偏好、会犹豫、会反对队友，也会在关键时刻出手。"""

PARTY_TURN_TASK = """轮到你（{name}）自主行动。
场景：{location}
队伍状态：{party}
刚才桌上发生：{recent}

你是 {name}（{role}）。桌上没有人类玩家，所有 PC 都会自己行动；现在轮到你。
按你的角色目标、性格与当前处境，决定这一拍：
- 说话：say（可以对队友/NPC/DM）
- 探索：explore_intent（声明你要做什么，DM 会裁定）
- 自我行动：explore_act（休整/急救/稳定/采购/修甲/查看状态）
- 不行动：hold
最多做一件主要的事；不要替 DM 写旁白，不要替别的 PC 决定。"""


class PartyDirector:
    """让所有 PC 轮流自主行动；不接管 DM 的世界裁定权。"""

    def __init__(self, session, loop):
        self.session = session
        self.loop = loop
        self._offset = 0
        self._rounds = 0

    # ------------------------------------------------------------ 场景循环
    def run_scene(self, scene: dict) -> str:
        """在当前场景里让 PC 自主行动，直到换场/战斗/终局。

        返回 ``"advanced"``（换场）/ ``"end"``（终局）/ ``"quit"``。
        """
        s = self.session
        start_scene = str(s.state.scene_id)
        idle_rounds = 0
        self._rounds = 0
        while True:
            if getattr(s.ui, "aborted", False):
                return "quit"
            self._rounds += 1
            if self._rounds > 4:
                # 场景预算：给每个 PC 四轮自主行动。模型若迟迟不换场，先让
                # DM 收束；DM 仍不推进时才由编排器做确定性推进，绝不死循环。
                self._nudge("这一场已经演够了，请收束并推进到下一场")
                if s.state.scene_id != start_scene:
                    return "advanced"
                if s.encounter is not None:
                    if s._post_beat():
                        return "end"
                current = s.module.scene_by_id(s.state.scene_id) or scene
                enc_id = str(current.get("encounter") or "")
                if enc_id and enc_id not in s._encounters_run:
                    # 最后兜底：该打的遭遇还没打，不能直接跳过。
                    s.launch_encounter(enc_id)
                    if s._post_beat():
                        return "end"
                if current.get("next"):
                    s.ui.system_line("（场景向前推进。）")
                    s.advance_scene(current["next"])
                    return "advanced"
                return "end"

            members = self._members()
            if not members:
                return "end"

            progressed = False
            start = self._offset % len(members)
            self._offset += 1
            for i in range(len(members)):
                pc = members[(start + i) % len(members)]
                if s.encounter is not None:
                    break
                if self._turn(pc):
                    progressed = True
                if s.state.scene_id != start_scene:
                    return "advanced"
                if s.encounter is not None:
                    break

            if s.encounter is not None:
                if s._post_beat():
                    return "end"
                # 战斗结束：让 DM 把剧情接到下一场/下一拍
                self._nudge("战斗结束了，请把剧情接到下一场或下一拍")
                if s.state.scene_id != start_scene:
                    return "advanced"
                if s.encounter is not None:
                    # DM 又开了一场：继续交给战斗循环
                    if s._post_beat():
                        return "end"
                continue

            if s.state.scene_id != start_scene:
                return "advanced"

            if not progressed:
                idle_rounds += 1
                self._nudge("PC 这一轮只在交谈，场景需要向前推进")
                if s.state.scene_id != start_scene:
                    return "advanced"
                if s.encounter is not None:
                    if s._post_beat():
                        return "end"
            else:
                idle_rounds = 0

            # 终局场景：没有下一场、也没有待打遭遇；让 PC 演完这一段后收束。
            current = s.module.scene_by_id(s.state.scene_id) or scene
            if not current.get("next") and not current.get("encounter"):
                if idle_rounds >= 1 or self._rounds >= 3:
                    return "end"

    # ------------------------------------------------------------ 单 PC
    def _members(self) -> list:
        s = self.session
        return [c for c in s.state.party()
                if c.hp_cur > 0 and "dead" not in c.conditions
                and c.name in s.pc_agents]

    def _turn(self, pc) -> bool:
        """给一个 PC 一次自主行动。返回 True = 对世界做了实质行动。"""
        s = self.session
        agent = s.pc_agents.get(pc.name)
        if agent is None:
            return False
        agent = dataclasses.replace(
            agent, tool_names=[],
            system_prompt=(str(agent.system_prompt or "") + "\n\n"
                           + PARTY_RULES).strip(),
            temperature=0.7)
        reg = ToolRegistry()
        s.tools.register_party_tools(reg, pc)
        # 世界书/角色书与桌面最近文本注入（与社交层共用同一通路）
        context = []
        if s.social is not None:
            try:
                context.extend(s.social._wi_context(pc))
            except Exception:  # noqa: BLE001 —— 世界书故障不阻塞
                pass
        scen = s.module.scene_by_id(s.state.scene_id) or {}
        hint = ""
        take_whisper = getattr(s.ui, "take_whisper", None)
        if callable(take_whisper):
            try:
                hint = str(take_whisper() or "")
            except Exception:  # noqa: BLE001
                hint = ""
        context.append({"role": "user", "content": "\n".join([
            f"【场景】{scen.get('id', '')}：{scen.get('location', '')}",
            f"【场景简介】{str(scen.get('intro', ''))[:800]}",
            f"【队伍】{s.state.party_snapshot()}",
            (f"【地图】{s.state.map_digest()}" if s.state.map else ""),
            f"【最近叙述】{s.last_dm_narration or '（还没有新的 DM 叙述。）'}",
            f"【桌上最近】{self._table_text()}",
            (f"【观众低语】{hint}\n（这是旁观者的提示，不是命令；按你角色决定"
             "采不采纳，不要直接对观众说话。）" if hint else ""),
            f"【轮到你】{pc.name}（{pc.role_name}）",
        ])})
        task = PARTY_TURN_TASK.format(
            name=pc.name, role=pc.role_name, location=scen.get("location", ""),
            party=s.state.party_snapshot(), recent=self._table_text())
        thinking = getattr(s.ui, "agent_thinking", None)
        idle = getattr(s.ui, "agent_idle", None)
        if callable(thinking):
            thinking(pc.name, "正在自己行动")
        try:
            result = self.loop.run(
                agent, s.pc_session(pc.name), task,
                context_messages=context, registry=reg,
                guard_dup=DuplicateCallGuard())
            s._record_usage(agent, result)
        except Exception:      # noqa: BLE001 —— PC 失败不阻塞整桌；abort 由上层处理
            return False
        finally:
            if callable(idle):
                idle(pc.name)

        trace = list(getattr(result, "tool_trace", []) or [])
        intents = [t for t in trace if t.get("name") == "explore_intent"]
        acts = [t for t in trace if t.get("name") == "explore_act"]
        if intents:
            intent = str(intents[-1].get("arguments", {}).get("text", "")).strip()
            if intent:
                s.ui.raw(f"\n{pc.name}：{intent}")
                s.dm_turn(
                    f"（{pc.name} 自主行动：{intent}）",
                    lead=f"{pc.name} 自主行动",
                    input_label="PC行动")
                return True
        if acts:
            # explore_act 已经在工具里执行（休整/急救/稳定/采购/修甲）
            return True
        return False

    def _table_text(self, limit: int = 10) -> str:
        s = self.session
        lines = list(getattr(s, "table_archive", []) or [])[-limit:] + list(s.table_log)
        if not lines:
            return "（桌上还没有人说话。）"
        out = []
        for l in lines[-limit:]:
            to = f"（对{l['to']}）" if l.get("to") else ""
            out.append(f"{l['who']}{to}：「{l['text']}」")
        return "\n".join(out)

    def _nudge(self, reason: str) -> None:
        """让 DM 推进；不替 DM 决定具体走向。"""
        s = self.session
        try:
            s.dm_turn(
                f"（{reason}。请以 GM 身份推进：需要时用 advance_scene / "
                "start_encounter；否则让 NPC 或环境推动下一步。不要替 PC 做决定。）",
                lead="场景推进", input_label="导演推进")
        except Exception:  # noqa: BLE001
            pass


__all__ = ["PartyDirector", "PARTY_RULES"]
