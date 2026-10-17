# 非玩家 PC（独立 Agent）与 NPC（DM 控制的子智能体）

版本：1.1（对应 trpg 2.0 ·《赛博朋克 RED》口径）　上游参照：`_local/reference/SillyTavern`（浅克隆源码，gitignored）

本设计把〔AI PC 设计（docs/aipc-design.md）〕建成的 SillyTavern 式提示词栈
（`trpg/st/`：角色卡 / persona / 宏 / 世界书 / 组装 / 深度注入）推广到
**桌上其余的智能体**，并给两类角色划清层级：

| 角色 | 架构地位 | 控制权 | 记忆载体 |
|------|----------|--------|----------|
| 主角 PC（AI 自主） | 独立 Agent（`AIPCController`） | 自主决策 | `pc:<玩家名>` |
| **同伴（非玩家 PC）** | **独立 Agent**（`make_pc_agent` + 社交层） | 各自独立决策，编排器只管时机 | `pc:<同伴名>`（战斗/桌边共用） |
| **NPC** | **DM 控制的子智能体**（`NPCDirector`） | DM 用 `npc_dialogue` 工具按需拉起，随时可用 `speak_as_npc` 覆盖 | `npc:<id>`（跨回合持久） |

2.3.0 的全 AI 跑团由 `PartyDirector` 给**所有 PC（含主角）**轮流发回合；
本表里没有“人类玩家”这一层——主角也是 PC Agent，不是“AI 代打人类”。

ST 源码对照（克隆于 `_local/reference/SillyTavern`，仅作机制参照，不引入代码依赖）：
角色卡字段 → `src/character-card-parser.js`、`public/char-data.js`；
群聊多角色 → `public/scripts/group-chats.js`；用户 persona →
`public/scripts/personas.js`；世界书 → `public/scripts/world-info.js`。
本仓 `trpg/st` 的纯 Python 实现与 docs/sillytavern-architecture.md 的
规格一一对应，本设计只做**接线**，不改 st 机制。

---

## 1. 非玩家 PC = 独立 Agent（同伴接入 ST 组装线）

### 1.1 组装

`make_pc_agent`（trpg/game/agents.py）不再手拼 `str.format`，改走与玩家版
`make_aipc_agent` 完全相同的 `st.assemble_system` 默认序：

```
main（PC_SYSTEM_TEMPLATE 经 template_vars 预格式化）
→ 卡对话示例（mes_example，<START> 分块）
→ 全文宏渲染（{{user}}/{{char}}/{{roll::…}}）
```

- **卡字段进模组**：`companions[]` 现在接受可选 ST 扩展字段——
  `mes_example`（对话示例）、`first_mes`、`depth_prompt`、`system_prompt`
  （覆盖默认 main，支持 `{{original}}`）、`talkativeness`、
  `book`（V2 世界书 dict，`wi_from_book` 导入）→ `card_from_companion`。
- **`{{user}}` 语义**：`user` 参数传入玩家名，经 `_anon_persona`
  （`Persona(name=玩家, position="none")`）只供宏渲染、不进 system——
  同伴卡里的 `{{user}}` 渲染成玩家角色名（ST 用户 persona 语义）。
- 人设/语气仍填模板槽位（persona/voice → description/personality 后置空副本），
  与 aipc 的取舍一致：不双份进提示词。

### 1.2 世界书通路（每回合）

桌边社交回合（`TableSocial._pc_social_turn` / `_pc_debate_turn`）注入
`_wi_context(pc)`：

- 引擎世界书 `engine_worldinfo(state)`（场景 constant 条目 / 旗标 / 桌面上
  被点名的 NPC / 场景检定）+ 该同伴卡上的 `book` 合并（`merge`）；
- 以桌面最近文本为扫描窗口激活（`scan`），无激活内容不注入（省 token）；
- 作为 `context_messages` 传入 `loop.run`——"模型可见 ⟺ 已记录"不变量自动成立。

世界书只影响叙事上下文，永不进入结算路径（铁律不变）。

### 1.3 不变量

- AI 模式关闭 → `pc_agents` 为空、`social` 不构建 → 全部新分支不存在。
- 同伴战斗回合链（`companion_turn`）不变；AI PC 的 attach/refresh 机制不变。

## 2. NPC = DM 控制的子智能体（`trpg/game/npcagent.py`）

### 2.1 控制流

```
DM Agent ── npc_dialogue(npc, instruction) ──▶ NPCDirector.dialogue()
                                               │ 卡: card_from_npc → make_npc_agent（ST 组装线）
                                               │ 循环: 专用小步数循环（adapter/compactor 共享）
                                               │ 会话: npc:<id>（持久——NPC 记得每次交锋）
                                               ▼
                                    say-only registry ──▶ 台词列表
                                               │
              ui.npc_line（桌面渲染）+ table_log（kind="npc"）+ 返回 DM 工具结果
```

- **DM 是导演**：何时让 NPC 说话、给什么导演提示（instruction），都是 DM 的
  工具调用决策；台词作为工具结果回到 DM 的上下文，DM 继续叙述收束。
  DM 不想授权时，`speak_as_npc` 一句话过场的路径原样保留。
- **子代理无世界面**：registry 只有 `say`（产出台词），没有任何影响游戏
  状态的工具——"LLM 只决策、引擎结算"在子代理层同样成立。
- **NPC 有记忆与立场**：`npc:<id>` 持久会话让它记得此前与冒险者的交锋
  （记仇 / 改主意有了载体）；模组可给 NPC 写 `goals`（动机）与 `secret`
  （底牌，进系统提示仅供把握分寸，不主动泄露）。
- **桌面互通**：NPC 台词进 `table_log`（`kind="npc"`）→ 同伴的 `_recent`
  与 DM 的 `_digest_table` 都"听得见"，桌边辩论可以围绕 NPC 的话展开。

### 2.2 模组 NPC 字段扩展

`npcs{}` 条目在必有字段 `name/desc`（`role` 建议写）之外，接受可选 ST 扩展：
`persona`（性格，进 description）、`voice`（语气）、`goals`、`secret`、
`mes_example`、`first_mes`、`depth_prompt`、`talkativeness`、`book`。
最简形状（name/desc）行为不变；`card_from_npc` 兜底
description = persona 或"职业。desc"。

### 2.3 降级矩阵（绝不阻塞对局）

| 场景 | 行为 |
|------|------|
| 剧本模式（无 LLM） | `npc_director=None`，工具返回 `ok=False`（"仅 AI 模式可用"） |
| LLM 不可达/超时 | 返回 `ok=False` + 提示 DM 改用 `speak_as_npc`，桌面零污染 |
| NPC 不开口（未调 say） | `ok=True, lines=[]`，提示调整导演提示或回退 |
| 未知 NPC | `ok=False`，error 列出模组可用 NPC |
| 超时层级 | DM 侧 `npc_dialogue` 归入 `ToolTimeoutGuard.LONG_TOOLS`（180s，内部还要跑几次模型调用）；子代理自身工具超时 60s、步数 ≤3 |

### 2.4 DM 侧接线

- 工具面：`npc_dialogue` 加入 `make_dm_agent.tool_names` 与
  `GameTools.register_dm_tools`；DM 系统模板工作方式第 7 条指导用法
  （重要对话交给子智能体、过场用 speak_as_npc）。
- 用量计量：挂在 `npc:<id>` agent 名下（模型取 dm_agent.model）。

## 3. 内置模组、兜底数据与默认 id

- `modules/cpred-easy-mode/module.json`：**唯一内置剧本《赛博朋克 RED：轻松模式》**
  （*Cyberpunk RED: Easy Mode*）——轻松模式手册本体，内含入门差事**「抢钱行动」**
  （原文 *WYSKAKUJ Z FORSY*）：三幕六场（来电 → 海伍德工业区 → 仓库），
  两场遭遇（`e_alley_ambush` / `e_warehouse`）、两张战术地图、五张预设角色卡
  （摇滚客 Forty / 独行侠 Mover / 技术师 Torch / 医疗师 Redtail / 媒体人 24/7）
  与合计 200 IP 的成长节点。
- **兜底剧本**（`trpg/campaign/module_data.py`）：两幕的"卡布奇夜市 → 海伍德工业区
  废弃仓库"，只在 `modules/` 目录缺失、模组文件损坏或存档引用的模组 id 无法解析时
  启用（`cordis.default_fallback_module()`），保证游戏永不硬失败。
  导出名与旧版保持一致（`MODULE/COMPANIONS/NPCS/ENCOUNTERS/SCENES/EPILOGUE/
  ACT_TITLES/BUILTIN`），内容已是 RED 口径（十项属性 / 41 项技能 / DV / PW / SP /
  IP / 欧元 eb）。
- 默认 id：`main.DEFAULT_MODULE_ID = "cpred-easy-mode"`（CLI）与 web `AppState.start`
  默认一致；`cpred-easy-mode` 属于 `BUILTIN_IDS`（不可卸载/删除）。
- NPC 子智能体的示范字段：内置模组的中间人 **Lazlo**带 `persona`/`voice`/`secret`
  （secret = 行程是谁卖的）；兜底剧本的中间人 **Marlo**带 `persona`/`voice`。
  模组未定义预设角色时回退到引擎默认两名预设角色（独行侠 Vera / 医疗师 Kay）；
  ST 扩展字段（`book`/`mes_example`）的机制示范见 `tests/test_npc_agent.py` 的模组夹具。

## 4. 测试

`tests/test_npc_agent.py`（全离线 ScriptedAdapter）：
NPC 子代理的工具注册/委派渲染/持久记忆/未知 NPC/LLM 降级/剧本模式不可用/
ST 字段进系统提示/台词回流 DM 上下文；同伴卡的 mes_example+宏渲染/book
往返/最简兜底/社交 WI 上下文注入与激活；内置模组《赛博朋克 RED：轻松模式》
校验零警告、内置保护（不可删除）、默认 id 与缺失回退。
