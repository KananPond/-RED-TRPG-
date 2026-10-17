"""桌边社交层：PC 在探索期的自主行为——抢话、反应、辩论。

AI 模式下，玩家输入不再"玩家 → DM"两点一线，而是一整个社交节拍：

  1. 抢话窗口：玩家话音未落，同伴可能抢在 DM 前插话（interrupt）；
  2. DM 回应（其上下文包含同伴插话摘要，NPC 会"听见"并可能回应）；
  3. 自由反应：DM 说完后同伴各自决定开口（附和/追问/吐槽）或沉默；
  4. 辩论轮：若有同伴反对（stance=object）且桌上存在对立面，
     进入最多 DEBATE_MAX_ROUNDS 轮辩论——各方轮流申辩，
     hold / agree 即退出，直到只剩一种声音或达到轮数上限；
  5. DM 即席回应：同伴们交谈过后，DM 以桌面主持身份回应一次
     （NPC 接话、反驳、裁定），辩论收场后还留一个"最后一句话"名额。

原则：编排器只负责时机与秩序，不代写台词——每名同伴经各自的
harness 会话独立决策（模型可见 ⟺ 已记录），发言即工具调用。
LLM 不可达或超时时整层静默降级，绝不阻塞对局。
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor

from ..harness.loop import Agent, DefaultAgentLoop, DuplicateCallGuard
from ..rules.character import Character
from ..st import card_from_companion, engine_worldinfo, merge, scan

# 每波最多几名同伴开口（其余的说了也咽回去，避免整队复读）
MAX_WAVE_SPEAKERS = 2
# 辩论最多几轮反驳（第一轮申辩 + 至多这么多轮交锋）
DEBATE_MAX_ROUNDS = 2
# 桌面闲谈比战斗决策更放飞：温度调高、输出调短
SOCIAL_TEMPERATURE = 0.95
SOCIAL_MAX_TOKENS = 240
# 喂给同伴的桌面上下文行数
RECENT_LINES = 6

SOCIAL_TASK = """【桌边】
{recent}
【此刻】{trigger}
你是 {name}（{role}）。此刻不是你的战斗回合，桌边在说话。
- say：开口说话。text=台词；to=对谁说（同伴名/NPC名/DM，可空）；
  interrupt=true 表示你抢过话头或打断正在说话的人；stance 可选
  agree（赞同）/ object（反对）/ question（追问）。
- hold：此刻不说话（真实的人大部分时候在听）。
台词 1~2 句中文，符合你的人设与语气——2045 年夜之城的边缘行者，
可以带动作或情绪，也可以提欧元、义体、中间人与街头规矩，但不要替玩家或 DM 说话。
像角色本人一样说话；最多调用一次工具，说完即止；没有好台词就 hold，别硬找话说。"""

DEBATE_TASK = """【辩论】焦点：{topic}
【目前为止桌上的话】
{transcript}
你是 {name}（{role}），轮到你表态：
- say：继续申辩、反驳或让步。text=台词；to=对谁；
  interrupt=true 表示抢过话头；stance=object 表示仍反对 / agree 表示被说服。
- hold：不争了（说服了对方、被说服、或懒得再争）。
台词 1~2 句中文，符合人设，就事论事，不要重复自己已说过的话；
夜之城的规矩是没人能轻易说服谁，但每一句都要有分量。
最多调用一次工具。"""


class TableSocial:
    """绑定 GameSession 的桌边社交编排器（仅 AI 模式构建）。"""

    def __init__(self, session, loop: DefaultAgentLoop):
        self.session = session
        self.loop = loop          # 专用小步数循环（与战斗循环共享 adapter）

    # ================================================================ 节拍
    def beat(self, player_text: str, player_trigger: str | None = None) -> None:
        """玩家一次输入引发的完整社交节拍（在 ai_explore_turns 中调用）。

        player_trigger：抢话窗口的 trigger 文案（AI PC 模式下由
        AIPCController 传入"玩家角色（…）刚说完话"，避免自我打断的荒谬
        表述；缺省保持原文案，行为不变）。"""
        s = self.session
        # 1) 抢话窗口：玩家刚开口，DM 还没接话
        self.reaction_wave(
            trigger=player_trigger
            or f"玩家（{s.state.player.name}）刚开口说话，DM 还没来得及回应。",
            extra=[f"玩家说：「{player_text}」"])
        if s.encounter is not None:
            return                       # 话没说完就打起来了，桌面转入遭遇
        # 2) DM 回合（插话已进其上下文）
        before_scene = s.state.scene_id
        dm_text = s.dm_turn(player_text)
        if s.encounter is not None:
            return
        trigger = "DM 刚说完，桌边短暂安静。"
        if s.state.scene_id != before_scene:
            trigger = f"队伍刚抵达新的地方（{s.module.scene_by_id(s.state.scene_id).get('location', '')}）。"
        # 3) 自由反应 + 4) 辩论
        wave = self.reaction_wave(
            trigger=trigger,
            extra=[f"DM 的叙述：「{dm_text}」"] if dm_text else None)
        debated = self.run_debate(wave)
        # 5) DM 即席回应桌面（同伴向 DM/NPC 说了话才值得起这一回合）
        if s.table_log:
            s.dm_turn("（玩家本轮没有新指令——桌上的同伴们在交谈。请回应桌面："
                      "让 NPC 接话、反驳或裁定同伴的争论；若无事可应，给一句简短的环境叙述。）",
                      lead="桌上的同伴们刚交谈过", input_label="桌面")
            if s.encounter is not None:
                return
            if debated:
                # 争论刚被 DM 收场：留一个"最后一句话"名额
                self.reaction_wave(trigger="DM 刚回应了桌上的争论。",
                                   max_speakers=1)

    # ================================================================ 反应波
    def reaction_wave(self, trigger: str, extra: list[str] | None = None,
                      max_speakers: int = MAX_WAVE_SPEAKERS) -> list[dict]:
        """给每名存活同伴一次"说或不说"的机会（并行决策），返回实际出口的话。

        speakers = 全队存活 ∩ pc_agents（docs/aipc-design.md §2.6）：AI PC 开启
        时玩家名在 pc_agents（attach 注册）→ 玩家自动进抢话波/辩论；
        关闭时玩家不在 pc_agents，过滤结果与旧的 companions 过滤逐位相等
        （party() = [player]+companions，player 被剔除）——零行为变化。"""
        s = self.session
        speakers = [c for c in s.state.party()
                    if c.hp_cur > 0 and "dead" not in c.conditions
                    and c.name in s.pc_agents]
        if not speakers:
            return []
        recent = self._recent(extra)
        results: dict[str, list[dict]] = {}
        workers = min(4, len(speakers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(self._pc_social_turn, pc, trigger, recent): pc.name
                    for pc in speakers}
            for fut, name in futs.items():
                results[name] = fut.result()
        lines: list[dict] = []
        for pc in speakers:                       # 按队伍顺序取每人第一句
            said = [l for l in results.get(pc.name, []) if not l.get("hold")]
            if said:
                lines.append(said[0])
        lines.sort(key=lambda l: 0 if l.get("interrupt") else 1)   # 抢话者先开口
        spoken: list[dict] = []
        for line in lines:
            if len(spoken) >= max_speakers:
                break
            display = line["text"]
            s.ui.companion_line(line["who"], display,
                                interrupt=bool(line.get("interrupt")))
            s.table_log.append({**line, "kind": "table"})
            spoken.append(line)
        return spoken

    def _pc_social_turn(self, pc: Character, trigger: str, recent: str) -> list[dict]:
        agent = self._social_agent(pc)
        session = self.session.pc_session(pc.name)
        bucket: list[dict] = []
        reg = self._social_registry(pc, bucket)
        task = SOCIAL_TASK.format(recent=recent, trigger=trigger, name=pc.name,
                                  role=pc.role_name)
        context = self._wi_context(pc)
        try:
            result = self.loop.run(agent, session, task,
                                   context_messages=context,
                                   registry=reg, guard_dup=DuplicateCallGuard())
            self.session._record_usage(agent, result)
        except Exception:      # noqa: BLE001 —— 桌面社交绝不阻塞对局
            return []
        return bucket

    # ================================================================ 辩论
    def run_debate(self, wave_lines: list[dict]) -> bool:
        """立场冲突 → 组织多轮辩论。返回是否真的争了起来。"""
        s = self.session
        objectors = [l for l in wave_lines if l.get("stance") == "object"]
        if not objectors:
            return False
        # 参辩者：反对者 + 被点名的同伴 + 本次开口的其他人（都可随时 hold 退出）
        invited: list[str] = []
        for l in objectors:
            for name in (l["who"], str(l.get("to") or "")):
                if name in s.pc_agents and name not in invited:
                    invited.append(name)
        for l in wave_lines:
            if l["who"] in s.pc_agents and l["who"] not in invited:
                invited.append(l["who"])
        if len(invited) < 2:
            return False              # 孤身反对玩家：留给 DM/NPC 去回应
        topic = objectors[0]["text"]
        transcript = [f"{l['who']}：「{l['text']}」" for l in wave_lines]
        active = list(invited)
        debated = False
        for _ in range(DEBATE_MAX_ROUNDS):
            if len(active) < 2:
                break
            results = self._debate_round(topic, active, transcript)
            spoke = False
            still: list[str] = []
            for name in active:
                lines = results.get(name, [])
                said = [l for l in lines if not l.get("hold")]
                conceded = (not lines) or any(l.get("hold") for l in lines) \
                    or any(l.get("stance") == "agree" for l in lines)
                for l in said:
                    s.ui.companion_line(l["who"], l["text"],
                                        interrupt=bool(l.get("interrupt")))
                    s.table_log.append({**l, "kind": "debate"})
                    transcript.append(
                        f"{l['who']}：「{l['text']}」")
                    spoke = True
                if not conceded:
                    still.append(name)
            if not spoke:
                break
            debated = True
            active = still
        return debated

    def _debate_round(self, topic: str, active: list[str],
                      transcript: list[str]) -> dict[str, list[dict]]:
        # by_name 必须 = party() ∩ pc_agents（docs/aipc-design.md rev2 ①）：
        # 玩家被 run_debate 邀请进辩论后（name in s.pc_agents），若只映射同伴，
        # submit 对玩家不发牌 → results 为空 → conceded 恒真、首轮即被"淘汰"。
        # 关闭 AI PC 时 pc_agents 只含同伴，映射与旧的 companions 过滤逐位相等。
        s = self.session
        by_name = {c.name: c for c in s.state.party() if c.name in s.pc_agents}
        trans = "\n".join(transcript[-RECENT_LINES * 2:])
        workers = min(4, len(active))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {name: pool.submit(self._pc_debate_turn, by_name[name],
                                      topic, trans)
                    for name in active if name in by_name}
            return {name: fut.result() for name, fut in futs.items()}

    def _pc_debate_turn(self, pc: Character, topic: str, trans: str) -> list[dict]:
        agent = self._social_agent(pc)
        session = self.session.pc_session(pc.name)
        bucket: list[dict] = []
        reg = self._social_registry(pc, bucket)
        task = DEBATE_TASK.format(topic=topic, transcript=trans, name=pc.name,
                                  role=pc.role_name)
        try:
            result = self.loop.run(agent, session, task,
                                   context_messages=self._wi_context(pc),
                                   registry=reg, guard_dup=DuplicateCallGuard())
            self.session._record_usage(agent, result)
        except Exception:      # noqa: BLE001
            return []
        return bucket

    # ================================================================ 部件
    def _social_agent(self, pc: Character) -> Agent:
        base = self.session.pc_agents[pc.name]
        agent = dataclasses.replace(base, temperature=SOCIAL_TEMPERATURE,
                                    max_tokens=SOCIAL_MAX_TOKENS)
        return agent

    def _social_registry(self, pc: Character, bucket: list[dict]):
        from ..harness.registry import ToolRegistry
        reg = ToolRegistry()

        def say(text: str, to: str = "", interrupt: bool = False,
                stance: str = ""):
            body = str(text or "").strip()
            bucket.append({"who": pc.name, "text": body, "to": str(to or ""),
                           "interrupt": bool(interrupt), "stance": str(stance or "")})
            return {"ok": True, "note": "已说出口，桌边都听见了。"}

        def hold(reason: str = ""):
            bucket.append({"who": pc.name, "hold": True, "reason": str(reason or "")})
            return {"ok": True, "note": "你把话咽了回去，保持沉默。"}

        reg.register(
            "say", f"以 {pc.name} 的口吻说话（台词放 text）。",
            {"type": "object",
             "properties": {
                 "text": {"type": "string"},
                 "to": {"type": "string",
                        "description": "对谁说：同伴名/NPC名/DM，可空"},
                 "interrupt": {"type": "boolean",
                               "description": "true=抢话或打断正在说话的人"},
                 "stance": {"type": "string",
                            "description": "agree/object/question，可空"}},
             "required": ["text"]},
            say)
        reg.register(
            "hold", "此刻不说话。",
            {"type": "object",
             "properties": {"reason": {"type": "string"}},
             "required": []},
            hold)
        return reg

    def _wi_context(self, pc: Character) -> list[dict]:
        """同伴独立 Agent 的世界情报上下文（SillyTavern World Info 通路）。

        引擎世界书（当前场景/旗标/在桌面上被点名的 NPC）与该同伴卡上的
        角色书（模组 pregens[].book，card_from_companion）合并，按桌面
        窗口扫描激活；无激活内容时不注入（省 token）。世界书只影响叙事
        上下文，永不进入结算路径。"""
        s = self.session
        wi = engine_worldinfo(s.state)
        # PORT_SPEC §3：模组队友住在 pregens 里（旧的 companions 数组已废弃）
        comp = None
        pregens = getattr(s.module, "pregens", {}) or {}
        for key, pg in pregens.items():
            if not isinstance(pg, dict):
                continue
            if pg.get("name") == pc.name or pg.get("handle") == pc.name \
                    or str(key) == getattr(pc, "source_key", ""):
                comp = pg
                break
        if comp is None:
            comp = next((c for c in getattr(s.module, "companions", []) or []
                         if isinstance(c, dict) and c.get("name") == pc.name), None)
        if comp:
            book = card_from_companion(comp).book
            if book is not None:
                wi = merge(wi, book)
        res = scan(wi, self._recent(), rng=s.state.rng)
        block = "\n".join(x for x in (res.before, res.after) if x.strip())
        if not block.strip():
            return []
        return [{"role": "user", "content": "【世界书】\n" + block}]

    def _recent(self, extra: list[str] | None = None) -> str:
        s = self.session
        items = []
        for l in s.table_log[-RECENT_LINES:]:
            mark = "（抢话）" if l.get("interrupt") else ""
            to = f"（对{l['to']}）" if l.get("to") else ""
            items.append(f"{l['who']}{mark}{to}：「{l['text']}」")
        lines = items + list(extra or [])
        return "\n".join(lines) if lines else "（桌面安静，还没有人说话。）"
