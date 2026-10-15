"""AIPCController —— 玩家角色的 AI 代打编排器（docs/aipc-design.md §2）。

以"第 4 名同伴"的身份复用既有同伴基础设施：
- 注册 agent 到 session.pc_agents[玩家名] → 战斗回合免费获得
  companion_turn → _pc_agent_turn → combat_action 工具链（session.py）；
- 会话复用 session.pc_session(玩家名)（探索/战斗/桌边共用一份记忆）；
- 探索期 explore_beat 用专属工具面（say/explore_intent/explore_act）决策，
  说的话经 _aipc_say 进桌面账本，DM 的 _digest_table 与同伴的 _recent 免费听见。

铁律：LLM 只决策；一切掷骰与数值由引擎结算（宏 {{roll}}、世界书只影响
叙事上下文，永不进入结算路径）。开关关闭（session.aipc is None）时本模块
零参与，所有分支短路为既有行为。

LLM 不可达的降级：explore_beat 返回 False → 探索循环交还人类输入；
战斗由 _pc_agent_turn 的既有 RuntimeError 兜底落启发式 _scripted_pc_turn；
桌边由 social 层静默降级。三层都不阻塞对局。
"""

from __future__ import annotations


from ..harness.loop import (Agent, DefaultAgentLoop, DuplicateCallGuard,
                            StepBudgetGuard, ToolTimeoutGuard)
from ..harness.registry import ToolRegistry
from ..st import (Injection, MacroContext, Persona, CharacterCard,
                  build_context_block, engine_worldinfo, merge, render, scan)
from .agents import make_aipc_agent

# 面向世界的探索行动（触达世界状态，DM 需要回应其后果）
# 取值面 = PORT_SPEC §7：rest_day / first_aid / stabilize / buy / repair_armor
# （save / state / inventory 是纯 UI 行动，不需要 DM 回应）
_WORLD_ACTS = {"rest_day", "first_aid", "stabilize", "buy", "repair_armor"}
_ACT_DESC = {"rest_day": "一次休整（恢复 PW）", "first_aid": "一次急救固定（处理致命伤）",
             "stabilize": "一次稳定（把人从濒死拉回来）", "buy": "一次采购",
             "repair_armor": "一次护甲维护"}

EXPLORE_LEAD = ("以 {name} 的身份行动：先考虑要不要说话，再行动。"
                "用 explore_intent 声明你的行动意图（引擎会交给 DM 推进），"
                "闲聊用 say，休整（rest_day）、急救（first_aid）、稳定（stabilize）、"
                "采购（buy）、修甲（repair_armor）用 explore_act。")

# 探索 beat 的单拍步数上限（同 social 层：小步数防空转，design §7.5）
EXPLORE_MAX_STEPS = 4
# 喂给 AI PC 的桌面插话行数上限
TABLE_LINES = 8


class AIPCController:
    """玩家角色的 AI 代打编排器。持有独立探索循环；战斗复用 session 主循环。"""

    def __init__(self, session, loop: DefaultAgentLoop, model: str,
                 card: CharacterCard, persona: Persona | None, *,
                 max_steps: int = EXPLORE_MAX_STEPS):
        self.session = session
        self.model = model
        self.card = card
        self.persona = persona
        self._last_narration = ""          # 最近一次声明的行动意图（下拍回喂）
        self._scenario = ""                # enter_scene 时刷新（refresh）
        # 探索 beat 专用小步数循环：线程池独立（不与 DM 战斗工具争抢 guard 池），
        # adapter/compactor 与主循环共享
        self._explore_loop = DefaultAgentLoop(
            registry=None, adapter=loop.adapter,
            guard_step=StepBudgetGuard(max_steps=max_steps),
            guard_timeout=ToolTimeoutGuard(),
            guard_dup=DuplicateCallGuard(),
            compactor=loop.compactor)
        self.agent: Agent | None = None    # refresh() 构建并注册

    # ------------------------------------------------------------ 挂载
    @classmethod
    def attach(cls, session, loop: DefaultAgentLoop, model: str,
               card: CharacterCard, persona: Persona | None, *,
               max_steps: int = EXPLORE_MAX_STEPS) -> "AIPCController | None":
        """构建并挂载。返回 None = 玩家名与同伴冲突（不抛异常），
        调用方（server.build_session / main）降级为不启用。"""
        player = session.state.player
        companion_names = {c.name.strip().lower() for c in session.state.companions}
        if player.name.strip().lower() in companion_names:
            return None                    # pc_agents 键冲突会顶掉同伴的 agent
        controller = cls(session, loop, model, card, persona, max_steps=max_steps)
        session.aipc = controller
        controller.refresh()               # 构建 agent 并注册为第 4 名同伴
        return controller

    # ------------------------------------------------------------ 交互点
    def explore_beat(self) -> bool:
        """ai_explore_turns 的 AI 分支。返回 False = LLM 不可用，本轮交还人类。"""
        s = self.session
        player = s.state.player
        reg = ToolRegistry()
        s.tools.register_aipc_tools(reg, player)
        lead = EXPLORE_LEAD.format(name=player.name)
        wi_res = self._scan_worldinfo()
        context = build_context_block(
            lead=lead, scene=self._scene_text(), party=s.state.party_snapshot(),
            flags=str(s.state.flags or "无"), table=self._table_text(),
            last_narration=self._last_narration,
            injections=self._persona_injections() + wi_res.at_depth,
            wi="\n".join(x for x in (wi_res.before, wi_res.after) if x.strip()),
            phi=self._rendered_phi())
        try:
            result = self._explore_loop.run(
                self.agent, s.pc_session(player.name), lead,
                context_messages=context, registry=reg,
                guard_dup=DuplicateCallGuard())
            s._record_usage(self.agent, result)
        except RuntimeError:               # LLM 不可达 → 优雅降级（design §7.1）
            return False
        self._interpret(result)
        return True

    def combat_turn(self, enc) -> None:
        """player_combat_turn 的 AI 分支：直接走同伴回合链
        （companion_turn → _pc_agent_turn → combat_action 工具；
        RuntimeError 时既有兜底落 _scripted_pc_turn 启发式）。"""
        self.session.companion_turn(enc, self.session.state.player)

    def poll_command(self, timeout: float = 0.0) -> "str | None":
        """人类旁听指令（非阻塞）。ConsoleUI/AutoUI 无 poll_command → 恒 None
        （终端无旁听指令，/退出 走 Ctrl+C 既有路径）。"""
        fn = getattr(self.session.ui, "poll_command", None)
        if fn is None:
            return None
        return fn(timeout)

    # ------------------------------------------------------------ 场景刷新
    def refresh(self) -> None:
        """enter_scene 后重写 scenario 并重建 agent（同伴侧同款机制：
        social._social_agent 的 dataclasses.replace 先例）。"""
        s = self.session
        scene = s.module.scene_by_id(s.state.scene_id) or {}
        self._scenario = f"{scene.get('location', '')}：{scene.get('intro', '')}"
        self.agent = make_aipc_agent(
            s.state.player, self.card, self.persona, self.model,
            scenario=self._scenario)
        s.pc_agents[s.state.player.name] = self.agent

    # ------------------------------------------------------------ 内部
    def _interpret(self, result) -> None:
        """解释 trace（四分支，自上而下匹配，design §2.4 步骤 4）。"""
        s = self.session
        player = s.state.player
        trace = result.tool_trace or []
        intents = [t for t in trace if t["name"] == "explore_intent"]
        says = [t for t in trace if t["name"] == "say"]
        acts = [t for t in trace if t["name"] == "explore_act"]
        if intents:                        # 有意图 → 记入最近旁白并走完整社交节拍
            text = str(intents[-1]["arguments"].get("text", "")).strip()
            if text:
                self._last_narration = text
                self._beat(text)
                return
        if says:                           # 只说话 → "说话即输入"，同伴可反应
            text = str(says[-1]["arguments"].get("text", "")).strip()
            if text:
                self._beat(text)
                return
        world_acts = [t for t in acts
                      if str(t["arguments"].get("kind", "")) in _WORLD_ACTS]
        if world_acts:                     # 面向世界的行动已完成 → 让世界回应
            kind = str(world_acts[-1]["arguments"].get("kind", ""))
            s.dm_turn(f"（{player.name} 刚完成了{_ACT_DESC.get(kind, '一次行动')}。）")
            return
        # 全程无工具（纯文本收束）或只有 save/state 等纯 UI 行动 → 兜底推进
        s.dm_turn(f"（{player.name} 按兵不动。）")

    def _beat(self, text: str) -> None:
        """以玩家的话触发完整社交节拍（抢话→DM→反应→辩论→DM 收场）。"""
        s = self.session
        if s.social is not None:
            # 玩家自己的"发言"已经发生，trigger 文案避免自我打断的荒谬表述
            s.social.beat(
                text,
                player_trigger=f"玩家角色（{s.state.player.name}）刚说完话，"
                               f"DM 还没来得及回应。")
        else:
            s.dm_turn(text)

    def _scan_worldinfo(self):
        """引擎状态 WI + 角色书合并扫描（世界书只影响叙事上下文，design §1.3）。"""
        s = self.session
        wi = engine_worldinfo(s.state)
        if self.card.book is not None:
            wi = merge(wi, self.card.book)
        window = self._table_text()
        if self._last_narration:
            window = (window + "\n" + self._last_narration).strip()
        return scan(wi, window, rng=s.state.rng)

    def _persona_injections(self) -> list[Injection]:
        """persona position=at_depth 时按其 depth/role 注入每回合上下文。"""
        if (self.persona is None or self.persona.position != "at_depth"
                or not self.persona.description.strip()):
            return []
        return [Injection(depth=max(int(self.persona.depth), 0),
                          role=self.persona.role, order=100,
                          text=self.persona.description)]

    def _rendered_phi(self) -> str:
        if not self.card.post_history_instructions:
            return ""
        return render(self.card.post_history_instructions, self._macro_ctx())

    def _macro_ctx(self) -> MacroContext:
        s = self.session
        scene = s.module.scene_by_id(s.state.scene_id) or {}
        return MacroContext(
            user=s.state.player.name,
            char=self.card.name or s.state.player.name,
            persona=(self.persona.description if self.persona else "") or "",
            party=s.state.party_snapshot(),
            scene=f"{scene.get('id', '')}：{scene.get('location', '')}",
            flags=str(s.state.flags or "无"),
            extra={"description": self.card.description,
                   "personality": self.card.personality,
                   "scenario": self._scenario or self.card.scenario},
        )

    def _scene_text(self) -> str:
        s = self.session
        scene = s.module.scene_by_id(s.state.scene_id) or {}
        return f"{scene.get('id', s.state.scene_id)}：{scene.get('location', '')}"

    def _table_text(self) -> str:
        """桌面最近插话（只读，不清账——DM 的 _digest_table 负责消化）。"""
        lines = []
        for l in self.session.table_log[-TABLE_LINES:]:
            mark = "（抢话）" if l.get("interrupt") else ""
            to = f"（对{l['to']}）" if l.get("to") else ""
            lines.append(f"{l['who']}{mark}{to}：「{l['text']}」")
        return "\n".join(lines)
