# AI 剧场：让 Agent 自己把这一夜演完

> 「我不会玩」不应该成为进不了夜之城的理由。
>
> AI 剧场不是把按钮做得更大，而是把**玩家角色也交给 Agent**：主角会自己走位、
> 说话、犹豫、做决定；你只需要看。界面参考《极乐迪斯科》的叙事语法——技能在
> 脑内插话，肖像像案卷照片，检定像一次判决，选项像还没说出口的念头。

## 1. 入口与体验

- 首页「进入 AI 剧场」或快捷入口「AI 剧场」。
- 选一位主角（模组 `pregens`），其余预设角色按模组默认补成 2 名 AI 队友。
- 选演出节奏，点击开演。**不需要建卡、不需要读规则、不需要在战斗中出手。**
- 顶部控制：演员表、沉浸模式、暂停/继续、速度、离场。
- 底部低语：一句给主角的悄悄话。它不是命令，也不改数值；进入下一名 PC
  Agent 的上下文，不保证被采纳。
- 刷新页面会从 `/api/state` 恢复剧场（暂停、速度、当前场景、地图、队伍、
  技能之声都从快照重建）；SSE 历史只回放最后一段，避免把 700 条战斗日志
  重新画一遍。
- 演出结束是终局卡：场景数、生还者、掷骰次数、暴击次数、旗标，可「再演一场」
  或回大厅。

## 2. 纯 AI 驱动

| 角色 | 驱动 |
|---|---|
| 主角 PC | 独立 Agent：`PartyDirector` 给回合，自主选择 `say / explore_intent / explore_act` |
| 同伴 PC | 独立 Agent：同一套自主回合，不是“等玩家说话才反应” |
| DM | 独立 Agent：世界裁定；`npc_dialogue` 拉起 NPC 子智能体 |
| NPC | DM 的 `NPCDirector` 子智能体，只有 `say` |
| 技能之声 | 本地 `PsycheEngine`，零 token |
| 阻塞菜单 | `strict_ai=True`：漏到 UI 的 `choose / free_input / ask_map_move` 直接报错 |
| 节奏/暂停 | `CinemaUI._emit` 的暂停闸门与速度器 |

**剧场必须有 LLM。** 没有 API Key 时 `/api/start {cinema:true}` 返回 400，
不会退回本地导演或剧本模式；`CinemaUI` 里残留的离线启发式只给单元测试用。
普通 AI 模式、剧本模式、经典模式仍在剧场之外保留。

## 3. 界面语法（为什么像《极乐迪斯科》）

### 3.1 技能之声

《极乐迪斯科》最独特的装置是技能拥有自己的口吻，会在对话里反驳、诱惑、
诊断你。`trpg/game/psyche.py` 把 RED 的十项属性/技能折成七个声部：

| 声部 | 属性 | 语言功能 |
|---|---|---|
| 逻辑 ◈ | 智力 | 拆穿时间线、把情绪换算成变量 |
| 共情 ◉ | 共情 | 读微表情、听见没说出口的半句 |
| 意志 ▲ | 意志 | 让声音站稳、压住颤抖 |
| 反应 ⌁ | 反应 | 先看见枪口、再看见人 |
| 技术 ⌬ | 技术 | 相信手感、锁芯、线路与烧痕 |
| 街头 ✶ | 魅力 | 知道这条街把真话藏在哪 |
| 体格 ◆ | 体格 | 先撞过去，再想为什么 |

- 声部线条、颜色、数值来自角色卡；检定时优先让**对应技能的声部**说话。
- 场景开场、NPC 台词、检定成功/失败、致命伤各有本地语料；不消耗 LLM token，
  不改变任何掷骰/状态。
- 台词是「不可靠的内心」而不是系统提示：它会误读、会逞强、会说出你不想听的
  判断——这是代入感的来源，而不是信息面板。

### 3.2 案卷式肖像

- 五名预设角色的官方 Easy Mode 角色画，被裁切、旋转、半调网点化、压在
  `multiply` 纸纹上，成为「贴在案卷上的照片」。
- 左栏小档案 = 你是谁；舞台右侧的大照片 = 此刻说话的人。
- NPC 没有照片时使用首字母纸卡，不伪造角色外观。

### 3.3 场景与节奏

- 六张场景底片：安全屋、路上、会面小巷、血房间、码头仓库、黎明；官方插画
  与程序化绘景混用，统一做低饱和、冷影暖高光、胶片颗粒和暗角。
- 背景缓慢 Ken Burns 漂移；雨、颗粒、暗角、扫描线全部 CSS/SVG 合成。
- 对话以「记忆栈」呈现：最新一句亮，旧句退到 32% 透明度，超过七条自动退出；
  打字机节奏由前端控制，暂停时后端也真的停（不是藏起字幕）。
- 检定叠层：SKILL CHECK / 声部 / d10 / 合计 / DV / 成败 / 修正明细；成功失败
  用青与锈红两种色相，掷骰音由 WebAudio 合成。
- 行动选项以「她考虑过的选项」短暂出现，AI 选中的一条用锈红标出；这既保留
  了《极乐迪斯科》的选项感，又不要求玩家点击。
- 右下战术板读取 `/api/stream` 的 `map` 事件：障碍、掩体、PC/敌人 token 的
  位置用 CSS transition 平滑移动，主角 token 有额外高亮环。

### 3.4 声音

- 环境音：55Hz 正弦 + 82.4Hz 三角波低频 drone、带通雨噪、极慢 LFO；
  战斗时滤波下潜、音量抬高，结束时回到低频。
- 事件音：掷骰、命中、暴击、致命伤沿用既有 `AudioFX` 合成音源。
- 浏览器自动播放策略：只有用户点击开演/首次 pointerdown 后才启动 AudioContext。

## 4. 全 AI 跑团（PartyDirector，v2.3.0）

2.2.0 曾尝试 `TablePlay`“真人同桌”：OOC、玩家画像、桌边笔记。它把
“AI 玩家”误解成“模拟人类玩家”。2.3.0 改为**每个 PC 直接以角色身份自主行动**：
一桌全 AI 玩家，人类只观看。

### 4.1 每个 PC 都是独立 Agent

| 角色 | 驱动 | 工具面 | 记忆 |
|---|---|---|---|
| 主角 PC | `AIPCController` 构建的 Agent | `say / explore_intent / explore_act / hold` | `pc:<玩家名>` |
| 同伴 PC | `make_pc_agent` 构建的 Agent | 同上 | `pc:<同伴名>` |
| DM | `dm_agent` | 世界工具 + `npc_dialogue` | `dm` |
| NPC | `NPCDirector` 子智能体 | 只有 `say` | `npc:<id>` |

没有 Human Player 节点：`PartyDirector` 直接给每个 PC 回合，不调用
`ui.free_input`；主角与同伴是平级的 PC，不是“一个主角 + 反应式队友”。

### 4.2 自主探索回合

每个场景最多四轮，每轮每个存活 PC 行动一次：

1. PC 看到场景、队伍、`last_dm_narration`、桌面最近的话、自己的角色；
2. 它选择 `say`（角色内说话）或 `explore_intent`（声明探索行动）；
3. `explore_intent` 交给 `dm_turn` 裁定；DM 可检定、换场、开战、拉 NPC 子智能体；
4. `say` 直接进 `table_log`，DM 消化后进 `table_archive`，后续 PC 能看见；
5. 四轮后 DM 仍不推进时，编排器才做确定性换场/补跑未打遭遇，终局收束 `_epilogue`。

`explore_act` 只处理休整/急救/稳定/采购/修甲/查看状态等自我行动；PC 不能改场景。

### 4.3 与 `TableSocial` 的分工

`PartyDirector` 用于 AI 剧场（全 AI）：所有 PC 轮流自主行动。
`TableSocial` 仍用于普通 AI 模式（人类输入）：玩家输入后，同伴抢话/反应/辩论。
两者不叠加；AI 剧场不构建 `TablePlay`，也不把同伴降级成只会在玩家说话后反应。

## 5. 后端结构

```
trpg/web/cinema.py     CinemaUI(WebUI)
                         ├─ _emit: 暂停闸门 + 节奏器
                         ├─ strict_ai: 拒绝本地导演输入；旧离线启发式仅供测试
                         ├─ whisper/take_whisper: 观众低语
                         ├─ agent_thinking/agent_idle: Agent 状态事件
                         └─ cinema_payload: 快照
trpg/game/psyche.py    PsycheEngine: 声部/语料/选声
trpg/game/party.py    PartyDirector: 全 AI PC 回合（自主行动/共享桌面/场景预算）
trpg/game/aipc.py     构建主角 PC Agent（场景刷新/卡/persona）
trpg/game/session.py   party_ai 分支；dm_turn 发 agent_state；数值路径不变
trpg/web/server.py     /api/start {cinema:true}、/api/cinema/control、
                       /api/cinema/whisper、snapshot["cinema"]、嵌套静态资源
trpg/web/static/
  cinema.css / cinema.js      全屏剧场 UI + SSE 队列 + 低语 + 战术板
  cinema/*.jpg                场景底片、角色案卷、标题/终局底片
```

### 5.1 不变量

- **LUDO 数值路径零改动**：CinemaUI 只做输入接管与呈现；掷骰、伤害、IP、
  护甲、致命伤仍由 `rules/` 结算。
- **LLM 只决策**：与既有 AIPC 相同，模型不能生成骰点。
- **暂停是服务端闸门**：`_emit` 在事件进入 SSE 历史之前等待；暂停时游戏线程
  真的停，不是前端把动画藏起来。
- **模型可见 ⟺ 已记录**：低语通过 `PartyDirector` 的 `context_messages` 进入循环，
  随事件日志可重放；不是侧信道。
- **刷新可恢复**：`cinema_payload()` 带 `paused/speed/autopilot/psyche/scene_id`，
  前端从快照重建。

### 5.2 低语的生命周期

1. `POST /api/cinema/whisper` → `CinemaUI.whisper(text)` 写入一个槽位，并发一条
   `whisper` 事件（旁听者可见）。
2. LLM 模式：`PartyDirector` 在下一名 PC 的回合开头 `take_whisper()`，
   把 `【观众低语】…` 加进该 PC 的 context，模型自行决定采不采纳。
3. 离线模式：`choose` 用关键词匹配影响下一个选项（谈/救/潜行/攻击…）；
   `take_whisper` 只消费一次，不累积、不重复注入。
4. 若低语在战斗/无选项时到达，槽位保留到下一次探索选择；不会直接改战斗数值。

## 6. 资产与授权

- **官方免费素材**：`.reference/Cyberpunk_RED_Easy_Mode_CN.pdf` 是 R. Talsorian
  Games 免费发布的 *Cyberpunk RED Easy Mode*。本项目的模组文本已经改编自该手册；
  AI 剧场从同一份免费手册里裁出五名预设角色的角色画和若干场景插画，做调色、
  半调与纸纹处理，**仅用于非商业同人展示**。
- **自制素材**：安全屋、血房间、码头仓库、黎明等底片由
  `tools/build_cinema_assets.py` 用 Pillow 程序化绘制（渐变、笔触、雨、颗粒、
  暗角），输出到 `trpg/web/static/cinema/`，运行时不联网、不依赖外部 CDN。
- **字体**：使用系统衬线（Noto Serif SC / Songti SC / SimSun）与
  Bahnschrift/Segoe UI 标签字体，不打包商业字体。
- 影像的版权归属见仓库根 `README.md` 的「发行与许可」；本项目不以任何方式
  主张官方素材的所有权。

## 7. 已知边界

- **LLM 模式的现场表现**取决于供应商的延迟与工具调用质量；无法在无密钥环境
  做端到端视频验收。全 AI 回合、AIPC 上下文注入、暂停/低语/重置接缝均有回归测试。
- **纯 AI 剧场必须有 LLM**：没有密钥时开局被拒绝，绝不退回本地导演；
  `strict_ai` 下漏到 UI 的阻塞输入会直接报错，而不是悄悄选一个剧本选项。
  桌面的最近对话存在 `table_archive`，不写存档；重开一局是新的桌面记忆。
- **场景预算**：每个场景最多四轮 PC 自主行动；四轮后 DM 仍不推进时，编排器会
  确定性换场/补跑未打遭遇，防止模型把场景拖成无限对话。
- **战斗走位**：AI PC 通过 `combat_action` 的 `move/approach` 走位；战术板显示
  真实 token 移动。
- **剧本/经典模式仍在剧场之外**：它们是独立入口，不会作为 AI 剧场的兜底。
- **音景依赖用户手势**：无交互的自动恢复页面不会强行播放环境音。

## 8. 测试

`tests/test_cinema.py`（9 例）与 `tests/test_party_ai.py`（4 例）：

- 旧 `CinemaUI` 离线启发式单元回归（非产品路径）：整局、选项、低语、暂停；
- 无 LLM 时 `/api/start {cinema:true}` 返回 400，不启动本地导演；
- 暂停闸门阻塞/恢复；`abort()` 在暂停中不抛异常；
- 快照带声部与 autopilot；
- AIPC 的下一拍上下文包含【观众低语】且只消费一次；
- 剧场美术/静态资源可访问；
- PC 工具面只有 `say / explore_intent / explore_act / hold`，没有 DM/NPC 工具；
- 所有 PC 在没有 Human `free_input` 的情况下自主跑完整局并抵达终章；
- 主角战斗走 Agent 而不是人类菜单；
- `build_session` 在剧场 + LLM 时启用 `party_ai`；
- PC 台词共享到 `table_archive`；终章确实演出。

全量 `python -m pytest tests -q`：**708 passed**。
