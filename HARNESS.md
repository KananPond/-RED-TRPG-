# Agent Harness 设计 —— 参考 deepseek-harness

本项目的 Agent 核心循环（`trpg/harness/`）在架构理念上参考了
[DeepSeek Harness（dsh）](https://github.com/deepseek-ai/deepseek-harness)——
DeepSeek 开源的"一切皆插件（Everything is a Plugin）"Agent 框架。
我们用 Python 实现了其中一个面向游戏引擎的精简变体。

## 核心不变量

**模型可见 ⟺ 已记录（model-visible ⟺ logged）**

任何送入模型的消息，都能从 Session 事件日志逐条重放重建。
`trpg/harness/events.py` 的 `Session` 把每条消息、每次工具调用/结果、
每次 guard 干预都写成带序号的事件（可选 JSONL 持久化到 `saves/logs/`），
`replay_messages()` 可随时重放重建完整消息序列。测试
`test_harness.py::test_tool_call_then_final` 直接验证该不变量。

## 插件分层

deepseek-harness 的"API 主干"是 session → system-prompt → tools → agent → agent-loop，
且 **模型适配器、工具注册表、agent 循环本身都是可替换插件**。对应实现：

| deepseek-harness 概念 | 本项目实现 | 说明 |
|---|---|---|
| Session / durable session events | `harness/events.py::Session` | 事件日志，JSONL 持久化，可重放 |
| Model adapter（可插拔） | `harness/adapter.py` | `OpenAICompatibleAdapter`（DeepSeek 等兼容端点）/ `ScriptedAdapter`（测试与离线） |
| Tool registry（register→disposer） | `harness/registry.py::ToolRegistry` | `register()` 返回 `disposer`；JSON Schema 校验；错误结构化回填给模型自愈而非中断循环 |
| Agent loop（循环本身是插件） | `harness/loop.py::DefaultAgentLoop` | 默认循环：观察→模型→tool_calls→guard 链执行→结果回填→下一轮；无 tool_calls 即终结 |
| guard 插件（loop-hygiene + tool-timeout） | `harness/loop.py` | `StepBudgetGuard`（步数预算）、`ToolTimeoutGuard`（线程池超时，Windows 兼容）、`DuplicateCallGuard`（连续重复调用短路并提示换路） |
| compaction / context 插件 | `harness/loop.py::TruncateCompactor` | 粗略 token 估算超限时：先截断单条超长消息，再丢弃最旧中间消息（保留 system 与最近若干条） |
| Waterfall listeners MUST call next() | guard 链语义 | 每个 guard 决定"放行/短路"，短路时返回结构化错误给模型 |

## 默认循环伪代码

```
run(agent, session, task):
    session.log(user(task))                    # 记录任务
    for step in 0..max_steps:                  # StepBudgetGuard
        messages = [system] + session.replay() # compaction 在此介入
        resp = adapter.chat(model, messages, tools)
        session.log(assistant(resp))
        if not resp.tool_calls:
            return final(resp.content)         # 终结输出
        for call in resp.tool_calls:
            session.log_tool_call(call)
            if dup_guard.short_circuit(call):  # 循环卫生
                result = {ok: false, error: 提示换路}
            else:
                result = timeout_guard.dispatch(registry, call)   # 工具调用
            session.log_tool_result(call, result)
```

## 在游戏中的两个实例

- **DM Agent**（`game/agents.py` + `game/tools.py`）：工具组 =
  `narrate / speak_as_npc / npc_dialogue / skill_check / opposed_check /
  get_state / set_flag / advance_scene / start_encounter / map_update /
  give_loot / award_ip / confrontation / request_roll`。
  检定一律走技能（`1d10 + 属性 + 技能` 对抗难度值 DV）与对抗检定，
  成长走改进点（`award_ip`）：旧规则层的独立检定工具与成长点数发放工具已随规则层删除。
  所有掷骰、数值、剧情推进都在引擎里完成，LLM 只做"决策与叙述"，
  因此模型永远无法编造骰点结果——结果以工具返回为准回填给模型。
- **PC Agent**（每名 AI 同伴一个）：工具组 = `say / combat_action /
  explore_intent`（AI 代打的玩家用 `explore_act`）。战斗回合给到 PC Agent 的
  上下文是遭遇快照（双方 PW/护甲 SP/伤势/剩余移动力），动作面为
  `move / dash / attack / grab / choke / throw / escape_grapple / reload /
  stand / stabilize / first_aid / use_item / delay / confrontation / flee /
  end_turn`，它必须调用 `combat_action` 完成回合；
  LLM 不可达时编排器自动降级为启发式战斗 AI（`_scripted_pc_turn`）。

每个 Agent 调用使用独立注册表（DM 与 PC 工具组不同），
通过 `DefaultAgentLoop.run(..., registry=...)` 按次注入。

## AI 剧场：CinemaUI 作为第三个实例（v2.1.0 / 全 AI 跑团 v2.3.0）

AI 剧场不是新引擎，而是把同一个 `GameSession` 包成一场**不需要人类输入**的演出：

- **`CinemaUI(WebUI, strict_ai=True)`**（`trpg/web/cinema.py`）是产品路径：
  所有 PC 行动由 `PartyDirector` 消化；如果还有 `choose / free_input /
  ask_map_move` 漏到 UI，直接报错，绝不退回本地导演。旧离线启发式只保留给
  单元测试/兼容，不进入产品。
- **暂停闸门**：`_emit` 在事件进入 SSE 历史之前等待；暂停时游戏线程真的停，
  不是前端隐藏字幕。速度器按 0.8×–2.2× 缩放每个事件的演出停顿。
  `abort()` 会先放行暂停闸门再走既有终止路径（暂停中重置不会 502）。
- **纯 AI 决策层**：主角与每名同伴都是独立的 PC Agent（`pc_agents`），由
  `PartyDirector` 轮流驱动。没有 LLM 时 `/api/start {cinema:true}` 直接返回
  400，绝不用本地导演开演；所有 Agent 输出同一套 SSE
  （`scene / npc / companion / dice / map / cinema_choice / inner`）。
- **技能之声**：`trpg/game/psyche.py` 从 RED 十项属性/技能折出七个声部，本地生成
  `inner` 事件。它不调用 LLM、不消耗游戏 RNG、不写 flag；检定时优先让对应技能
  的声部评论，NPC 台词也会触发读心短句。
- **低语**：`POST /api/cinema/whisper` 把一句话放进 `CinemaUI` 的槽位。
  `PartyDirector` 在下一名 PC 的回合 `take_whisper()`，把 `【观众低语】…` 放进
  上下文；它不进入结算路径，也不能保证被采纳。
- **快照恢复**：`snapshot["cinema"]` 带 `paused/speed/autopilot/psyche/scene_id`；
  刷新页面后前端从快照重建状态，SSE 历史只快速重放最后一段。
- **Agent 状态**：`dm_turn` 与 `PartyDirector` 的 PC 回合在调用前发
  `agent_state(thinking)`，调用后发 `idle`；前端把对应演员行点亮，能看见「谁在思考」。
- **与旧界面的关系**：旧仪表盘（`app.js`）完全保留；CinemaUI 只在
  `/api/start {cinema:true}` 时创建，默认手动模式逐字节不受影响。

资产与界面设计见 `docs/cinema-design.md`；回归测试见 `tests/test_cinema.py`。

## 全 AI 跑团层（game/party.py，v2.3.0）

`PartyDirector` 把 AI 剧场从“一个主角 + 反应式队友”变成**一桌全 AI 玩家**：

- **所有 PC 都是独立 Agent**：主角与同伴共用 `pc_session` 机制，但各有自己的
  Agent、人设、目标与记忆；没有 Human Player 节点，也没有“玩家/OOC/桌边”层。
- **自主探索回合**：每个场景最多四轮，每轮让每个存活 PC 自主行动一次；
  PC 只拿到自我范围的工具 `say / explore_intent / explore_act / hold`。
  `explore_intent` 交给 DM 裁定；`say` 直接进入桌面；`explore_act` 只做休整/
  急救/稳定/采购/修甲/查看状态等自我行动。
- **DM 仍是唯一世界 Agent**：DM 持有叙述、检定、换场、开战、`npc_dialogue`；
  NPC 仍是 DM 的 `NPCDirector` 子智能体，只带 `say`，不能自主行动。
- **共享桌面**：PC 的台词进 `table_log`；DM 消化后进 `table_archive`，后续 PC
  的回合从 archive + 当前 log 读取最近对话，不会各说各的。
- **场景预算与兜底**：四轮后 DM 仍不推进，编排器才做确定性换场（或补跑未打的
  遭遇），避免无限对话；终局场景收束到 `_epilogue`。
- **失败降级**：任一 PC Agent 调用失败只丢这一拍；DM 仍可用本地/规则引擎兜底。
- **开关**：只有 `CinemaUI` + LLM + AIPC 成功挂载时 `party_ai=True`；普通 AI 模式
  仍是人类输入驱动，经典模式零变化。

回归测试见 `tests/test_party_ai.py`（4 例）；全量 708 passed。

## 桌边社交层（game/social.py，v1.5.0）

探索期的玩家输入由 `TableSocial.beat()` 编排成一整个社交节拍，
让 PC 智能体像真实桌边的玩家一样行动：

1. **抢话窗口**：玩家输入后、DM 回应前，每名存活同伴并行获得一次
   "说或不说"的决策（工具 `say`/`hold`；`say(interrupt=true)` 表示
   抢过话头）。抢话者先出口，每波至多 2 人开口（`MAX_WAVE_SPEAKERS`）。
2. **DM 回应**：插话摘要经 `GameSession._digest_table()` 进入 DM 上下文
   （`【桌上插话】`）并清账——保持"模型可见 ⟺ 已记录"不变量。
3. **自由反应**：DM 说完后再来一波反应（`say` 可带立场
   `agree/object/question`）。
4. **辩论轮**（`run_debate`）：存在 object 立场且桌上有对立面时，
   反对者、被点名者与本次开口者进入至多 `DEBATE_MAX_ROUNDS` 轮辩论；
   每轮每人一次 `say`/`hold`（并行、各自 fresh `DuplicateCallGuard`），
   hold / agree / 无回应即退出，少于 2 人仍在争则收束。
5. **DM 即席回应**：桌上有新话后 `dm_turn(lead="桌上的同伴们刚交谈过")`
   一次；辩论发生过则再留一个"最后一句话"反应波（max_speakers=1）。

实现要点：
- 社交运行走**专用 DefaultAgentLoop**（`StepBudgetGuard(max_steps=4)`、
  独立 `ToolTimeoutGuard` 线程池），复用同一 adapter；共享 loop 的
  `guard_dup` 有状态，故 `run()` 支持按次注入 fresh guard（v1.5.0）。
- 同伴的桌面发言与战斗行动**共用同一 Session**（`pc_session()`）——
  人设记忆跨阶段延续。
- DM 回合内若 `start_encounter` 拉开了战斗，节拍立即中止交还战斗编排。
- LLM 不可达/超时：单次社交运行静默失败（返回空），绝不阻塞对局；
  剧本模式（loop=None）不构建社交层。

## 与 deepseek-harness 的差异（有意为之）

- dsh 基于 Cordis/TypeScript 的进程级插件容器；本项目是单进程 Python
  库，用组合与按次注入表达同样的"可替换"边界。Cordis 本体的语义
  （Context 服务容器、插件即函数、Fiber 三态 PENDING/ACTIVE/DISPOSED、
  副作用 LIFO 回滚、依赖就绪自动激活、服务消失时依赖方连坐处置）已按
  检索到的官方教程在 `trpg/campaign/cordis.py` 中对模组加载层做了忠实
  的 Python 变体实现。
- dsh 的 Session 有 SQLite 持久化与多 SDK 投影；本项目使用 JSONL 事件流
  （每事件一行），足够游戏审计与重放。
- 未实现 dsh 的 subagent/skill/web/shell 等生态插件；游戏领域工具组
  （规则引擎 API）即本 harness 的全部插件面。

## 何时用真实 LLM

设置环境变量后 `main.py`（终端）或 `python main.py --web`（网页版）自动构建
`OpenAICompatibleAdapter` 并进入 AI 模式，DM 与三名 AI 队友分别经 harness
循环接入 DeepSeek：

```
TRPG_LLM_BASE_URL=https://api.deepseek.com/v1
TRPG_LLM_API_KEY=<你的密钥>            # 只放环境变量，绝不入库
TRPG_LLM_MODEL=deepseek-chat
```

不配置则自动落入剧本模式（模组数据驱动 DM + 启发式队友 AI），
两种模式共用同一规则引擎与剧情数据。`python main.py --check-llm`
可验证端点连通性。连接本机模型（Ollama/LM Studio）需显式设置
`TRPG_LLM_ALLOW_PRIVATE=1`（SSRF 加固的例外开关，见 `adapter.py`）。

出网**默认直连**：进程里继承的 `HTTP_PROXY` / `HTTPS_PROXY` 一律忽略
（它们常把请求送进一条不认识的隧道，报 `Tunnel connection failed: 403`）。
要经代理必须用 `TRPG_LLM_PROXY` 显式声明：`direct`（默认）/ `env`（沿用
环境与系统代理）/ 代理地址；`env` 下隧道失败会自动直连重试一次。

## 参考来源

- [deepseek-ai/deepseek-harness（GitHub）](https://github.com/deepseek-ai/deepseek-harness)
- [deepseek-harness AGENTS.md（循环与 session 约定）](https://github.com/deepseek-ai/deepseek-harness/blob/master/AGENTS.md)
- [The New Stack：DeepSeek open sources an agent harness](https://thenewstack.io/deepseek-harness-open-source-plugins/)
- [DeepInfra Blog：DeepSeek Harness Review — Agent Loop & Plugin Architecture](https://deepinfra.com/blog/deepseek-harness-review)
