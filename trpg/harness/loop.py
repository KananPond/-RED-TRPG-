"""AgentLoop 插件与 Guard / Compaction。

DefaultAgentLoop（默认循环插件）：
    task → [上下文构建(压缩)] → 模型 → 有 tool_calls? →
        guard 链调用工具 → 结果回填 → 下一轮；无 tool_calls → 终结输出。

Guard 插件（瀑布式：guard 决定放行或短路）：
- StepBudgetGuard：单次 Agent 调用的最大循环步数。
- ToolTimeoutGuard：单工具调用超时（线程池实现，Windows 兼容）。
- DuplicateCallGuard：抑制连续重复的相同调用（循环卫生）。

Compaction 插件：粗略 token 估算超限时收缩旧消息。
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field

from .events import Session
from .models import ToolCall, assistant, tool_result, user
from .registry import ToolRegistry


# ---------------------------------------------------------------- Guard
class StepBudgetGuard:
    def __init__(self, max_steps: int = 12):
        self.max_steps = max_steps

    def budget_ok(self, step: int, session: Session) -> bool:
        if step >= self.max_steps:
            session.log_guard("step_budget", "已达最大循环步数 " + str(self.max_steps) + "，强制收束")
            return False
        return True


class ToolTimeoutGuard:
    # 可能阻塞等待玩家输入的工具（掷骰按钮/升级特性选择）——不受常规超时约束，
    # 否则玩家思考的十几秒就会把合法的工具调用误判为挂死
    INTERACTIVE_TOOLS = {"skill_check", "request_roll", "opposed_check",
                         "advance_scene", "award_ip"}
    # 子智能体类工具：内部还要跑几次模型调用，10s 不够；但仍须有限——
    # 外部服务挂起时不能把 DM 回合卡死一小时
    LONG_TOOLS = {"npc_dialogue"}

    def __init__(self, timeout: float = 10.0, interactive_timeout: float = 3600.0,
                 long_timeout: float = 180.0):
        self.timeout = timeout
        self.interactive_timeout = interactive_timeout
        self.long_timeout = long_timeout
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def dispatch(self, registry: ToolRegistry, call: ToolCall, session: Session):
        future = self._pool.submit(registry.invoke, call.name, call.arguments)
        if call.name in self.INTERACTIVE_TOOLS:
            wait = self.interactive_timeout
        elif call.name in self.LONG_TOOLS:
            wait = self.long_timeout
        else:
            wait = self.timeout
        try:
            return future.result(timeout=wait)
        except concurrent.futures.TimeoutError:
            future.cancel()
            session.log_guard("tool_timeout", call.name + " 调用超时（" + str(wait) + "s）")
            return {"ok": False, "error": "工具 " + call.name + " 调用超时"}


class DuplicateCallGuard:
    """同一签名工具在循环内连续重复时短路，提示模型换路。"""

    def __init__(self, max_repeats: int = 2):
        self.max_repeats = max_repeats
        self._seen: dict[str, int] = {}

    def reset(self):
        self._seen.clear()

    def inspect(self, call: ToolCall, session: Session):
        sig = call.signature()
        count = self._seen.get(sig, 0) + 1
        self._seen[sig] = count
        if count > self.max_repeats:
            session.log_guard("duplicate_call", call.name + " 重复 " + str(count) + " 次，已短路")
            return {"ok": False,
                    "error": "该工具以相同参数被重复调用多次。请改用其他方法达成目标，"
                             "或直接输出最终叙述。"}
        return None


# ---------------------------------------------------------------- Compaction
def estimate_tokens(text: str) -> int:
    """粗略估算：CJK ≈1 token/字，ASCII ≈1 token/4字符。"""
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    return cjk + (len(text) - cjk) // 4


class TruncateCompactor:
    """超出预算时：先截断单条超长消息，再丢弃最旧的中间消息
    （保留 system 与最近 keep_recent 条）。"""

    MAX_MSG_CHARS = 8000

    def __init__(self, token_limit: int = 12000, keep_recent: int = 8):
        self.token_limit = token_limit
        self.keep_recent = keep_recent

    def compact(self, messages: list[dict], session: Session | None = None) -> list[dict]:
        messages = self._cap_single(messages)
        total = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        if total <= self.token_limit:
            return messages
        if session:
            session.log_guard("compaction", "上下文约 " + str(total) + " tokens 超限，截断旧消息")
        head, tail = messages[:1], messages[-self.keep_recent:]
        dropped = len(messages) - len(head) - len(tail)
        if dropped <= 0:
            return messages
        note = user("（系统注：为控制上下文长度，已省略较早的 " + str(dropped) +
                    " 条消息。当前任务所需的游戏状态请用 get_state 工具查询。）")
        return head + [note] + tail

    def _cap_single(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, str) and len(c) > self.MAX_MSG_CHARS:
                m = dict(m)
                m["content"] = c[:self.MAX_MSG_CHARS] + "…（内容过长已截断）"
            out.append(m)
        return out


# ---------------------------------------------------------------- Agent
@dataclass
class Agent:
    agent_id: str
    name: str
    system_prompt: str
    tool_names: list[str] = field(default_factory=list)   # 空=全部注册工具
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 1600


@dataclass
class AgentResult:
    content: str
    steps: int
    tool_trace: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


# ---------------------------------------------------------------- Loop
class DefaultAgentLoop:
    """默认 agent 循环插件。run() 在任务结束后返回 AgentResult。"""

    def __init__(self, registry: ToolRegistry, adapter,
                 guard_step: StepBudgetGuard | None = None,
                 guard_timeout: ToolTimeoutGuard | None = None,
                 guard_dup: DuplicateCallGuard | None = None,
                 compactor: TruncateCompactor | None = None):
        self.registry = registry
        self.adapter = adapter
        self.guard_step = guard_step or StepBudgetGuard()
        self.guard_timeout = guard_timeout or ToolTimeoutGuard()
        self.guard_dup = guard_dup or DuplicateCallGuard()
        self.compactor = compactor or TruncateCompactor()

    def run(self, agent: Agent, session: Session, task: str,
            context_messages: list[dict] | None = None,
            registry: ToolRegistry | None = None,
            guard_dup: DuplicateCallGuard | None = None,
            post_build=None) -> AgentResult:
        """context_messages：调用方注入的场景上下文（作为 user 消息）。
        registry：可按次覆盖（DM/PC 使用不同工具组）。
        guard_dup：可按次覆盖（并行运行同一 loop 时各自独立计数）。
        post_build：消息构建后的最终整形钩子（按深度注入用，
        docs/battlemap-design.md §3）——签名 (messages) -> messages，
        只影响本次请求的消息列表，事件日志仍按注入前记录（模型可见 ⟺
        已记录以"内容等价"口径成立：注入条目同时写日志，见调用方）。"""
        registry = registry or self.registry
        dup = guard_dup or self.guard_dup
        dup.reset()
        session.log_message(user(task))
        if context_messages:
            for m in context_messages:
                session.log_message(m)
        trace: list[dict] = []
        usage_total: dict = {}
        step = 0
        while True:
            if not self.guard_step.budget_ok(step, session):
                return self._finish(agent, session, "（本回合循环步数已达上限，简要收束。）",
                                    step, trace, usage_total)
            messages = self._build_messages(agent, session)
            if post_build is not None:
                messages = post_build(list(messages))
            resp = self.adapter.chat(
                model=agent.model, messages=messages,
                tools=registry.schemas(agent.tool_names) or None,
                temperature=agent.temperature, max_tokens=agent.max_tokens)
            for k, v in (resp.usage or {}).items():
                if isinstance(v, (int, float)):
                    usage_total[k] = usage_total.get(k, 0) + v
            session.log_message(assistant(resp.content, resp.tool_calls or None))
            if not resp.tool_calls:
                return self._finish(agent, session, resp.content or "", step, trace, usage_total)
            for call in resp.tool_calls:
                session.log_tool_call(call)
                blocked = dup.inspect(call, session)
                result = blocked if blocked else self.guard_timeout.dispatch(
                    registry, call, session)
                session.log_tool_result(call.id, call.name, result)
                session.log_message(tool_result(call.id, call.name, result))
                trace.append({"name": call.name, "arguments": call.arguments,
                              "result": result})
            step += 1

    def _build_messages(self, agent: Agent, session: Session) -> list[dict]:
        base = [{"role": "system", "content": agent.system_prompt}]
        base += session.replay_messages()
        return self.compactor.compact(base, session)

    def _finish(self, agent, session, content, steps, trace, usage) -> AgentResult:
        session.log_final(agent.agent_id, content, steps)
        return AgentResult(content=content, steps=steps, tool_trace=trace, usage=usage)
