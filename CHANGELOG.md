# 更新日志

本项目的所有重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 变更

- **许可证**：`LICENSE` 换成标准 MIT 正文，版权人 **EudoraArcher**；
  `pyproject.toml` 的作者与 Homepage 同步更新。
- **第三方声明**：原 `LICENSE` 中混排的规则文本/美术声明拆到 `NOTICE.md`，
  并移除与当前《赛博朋克 RED》无关的 D&D SRD 5.1 残留条款。
- **收窄表述**：`README.md`、`MODULE_SPEC.md` 与 `CREDITS.md` 里的「不得用于商业发行」
  改为只约束官方规则文本与美术，不再与 MIT 对代码的商业使用许可冲突。
- 发行包清单加入 `NOTICE.md`。

## [2.3.1] — 修复思考模式 reasoning 回传 400

> 症状：NPC/PC 带工具调用时出现
> `HTTP 400: The reasoning_text in the thinking mode must be passed back to the API`，
> 对局进入「等待世界回应」后报错。

- **保留 Responses 原始 reasoning item**：不再只保存文本；`ModelResponse`
  新增 `reasoning_items`，`assistant` 消息随历史保存 `id/summary/content/
  encrypted_content`，`to_input_items` 原样回传。
- **chat/completions 适配器剥掉 Responses 字段**：只保留 `reasoning_content`，
  避免严格网关把 `reasoning_items` 当未知参数 400。
- **自动降级**：Responses 仍报 reasoning 回传 400 时，自动切到
  chat/completions 重发同一轮（chat 通道本应用完整支持），不让对局死在
  一次 400 上。
- 回归：`tests/test_llm_endpoints.py` 新增 raw reasoning item 往返与
  400→chat 降级测试；全量 708 passed。

## [2.3.0] — 全 AI 跑团：所有 PC 都是自主 Agent

> 修正 2.2.0 的架构错误。2.2.0 把 Agent 做成了“真人同桌模拟”（OOC/玩家画像/
> 桌边笔记），但目标不是模拟人类玩家，而是**让每个 PC 直接以角色身份自主行动**：
> 一桌全 AI 玩家，人类只观看。

### 架构

- **没有 Human Player 节点**。主角和同伴都是独立的 PC Agent；玩家角色不再由
  “AIPC 代打”特殊处理，而是与队友同一条自主 PC 回合链。
- **`PartyDirector`（`trpg/game/party.py`）**：每场给每个存活 PC 最多四轮自主
  行动；工具面只有 `say / explore_intent / explore_act / hold`，不能碰世界状态。
- **DM 是唯一世界 Agent**：叙述、检定、换场、开战、`npc_dialogue` 只在 DM 工具面。
- **NPC = DM 子智能体**：仍由 `NPCDirector` 按 `npc_dialogue` 拉起，只带 `say`，
  有持久 `npc:<id>` 记忆，但不能自主行动。
- **共享桌面**：PC 台词进 `table_log`；DM 消化后进入 `table_archive`，后续 PC
  的回合能看见最近对话，不再各说各的。
- **纯 AI 剧场**：`/api/start {cinema:true}` 没有 LLM 时直接返回 400；
  `CinemaUI(strict_ai=True)` 漏到 UI 的 `choose/free_input/ask_map_move`
  会报错，绝不退回本地导演。剧本/经典模式仍在剧场之外保留。
- **场景预算**：四轮后 DM 仍不推进时，编排器做确定性换场/补跑遭遇，防死循环；
  终局场景收束到 `_epilogue`。

### 移除

- 删除 `trpg/game/tableplay.py` 与 `tests/test_tableplay.py`；撤销 OOC、玩家画像、
  桌边笔记、`table_note`、检定后桌边反应、AIPC 桌边规则等全部啮合。
- `TableSocial` 恢复为纯 IC 抢话/反应/辩论；不再有 `ooc` 字段。

### 保留

- AI 剧场 UI、技能之声、案卷肖像、检定叠层、战术板、暂停/调速/低语全部不变。
- 普通 AI 模式（人类输入）与经典模式完全不变；`PartyDirector` 只在
  `CinemaUI + LLM + AIPC 成功挂载`时启用。

### 测试

- 新增 `tests/test_party_ai.py`（4 例）：PC 工具面不含 DM/NPC 工具；所有 PC
  在无 Human `free_input` 的情况下自主跑完整局并抵达终章。
- 全量：`705 passed`。

## [2.2.0] — 实验：真人桌边（已在 2.3.0 移除）

2.2.0 曾加入 `TablePlay`：OOC、玩家画像、桌边笔记、骰后桌边反应。该层把
“AI 玩家”误解成“模拟真人同桌”，与“PC 直接扮演角色、全 AI 自主跑团”的目标
不符，2.3.0 已整体移除；保留此条仅作历史记录。

## [2.1.0] — AI 剧场：不用会玩，让 Agent 自己演完这一夜

面向「我不会玩」的重构：新增**全自动 AI 剧场**，并把对局界面按《极乐迪斯科》
的叙事语法重做。规则引擎、数值、手动/经典模式与旧仪表盘均保留；剧场是
`/api/start {cinema:true}` 才启用的独立 UI，默认路径零变化。

### 新增

**一键开演**

- 首页「进入 AI 剧场」/ 快捷入口；从模组 `pregens` 选一位主角，其余预设角色
  自动补成 2 名 AI 队友；可选取 0.8× / 1× / 1.5× / 2.2× 演出节奏。
- **不需要建卡、不需要读规则、不需要在战斗中出手**。探索、检定、战斗、IP、
  终局全部由系统推进。
- **双模式自主**：接入 LLM 时由既有 `AIPCController` 真正扮演玩家角色
  （say / explore_intent / explore_act / combat_action）；未接入时由
  `CinemaUI` 的本地导演启发式接管，离线照样从第一场演到终局。
- 刷新页面从 `/api/state` 恢复暂停、速度、场景、地图、队伍与技能之声；
  SSE 历史只快速重放最后一段，不再把整局战斗日志重画一遍。

**《极乐迪斯科》式界面（`cinema.css` / `cinema.js` / `cinema/` 资产）**

- **技能之声**：`trpg/game/psyche.py` 把 RED 属性/技能折成七个声部
  （逻辑/共情/意志/反应/技术/街头/体格），在场景、NPC 台词、检定成功/失败、
  致命伤时插话。纯本地、零 token、不消耗游戏 RNG、不写状态。
- **案卷式肖像**：五名预设角色的官方 Easy Mode 角色画，经裁切/旋转/半调/
  纸纹处理；左栏是主角档案，舞台右侧是当前说话者的案卷照片。
- **场景底片**：安全屋/路上/小巷/血房间/仓库/黎明六张；官方场景插画与
  Pillow 程序化绘景混用，统一低饱和、冷影暖高光、胶片颗粒、雨幕与暗角。
- **记忆栈对话**：新句亮、旧句退到 32% 透明度，超过七条自动退出；打字机节奏
  与后端暂停/速度联动。检定弹 SKILL CHECK 叠层（声部/d10/合计/DV/成败/修正），
  伴随合成掷骰音。
- **选项的余温**：展示 AI 考虑过的选项与最终选中的一条（锈红标记），不要求点击。
- **战术板**：读取真实 `map` 事件，障碍/掩体/PC 与敌人 token 平滑移动；主角
  token 有额外高亮；战斗时画面转为红色警戒调。
- **剧场控制**：演员表抽屉、沉浸模式（隐藏侧栏/控件）、暂停/继续、速度循环、
  离场；空格暂停、H 沉浸、Esc 离场。
- **低语**：底部输入一句给主角的悄悄话。LLM 模式下进入 AI PC 下一拍上下文
  （`【观众低语】`，模型可采纳或无视）；离线模式下作为选项关键词提示；
  只消费一次，不直接改数值。
- **环境音**：55Hz/82.4Hz 低频 drone + 带通雨噪 + 极慢 LFO；战斗时滤波下潜，
  终局回到低频。仍全部 WebAudio 合成，无外部音频文件。
- **终局卡**：场景数、生还者、掷骰次数、暴击次数与旗标；「再演一场」/「回大厅」。

### 修复 / 加固

- **暂停闸门**：`CinemaUI._emit` 在事件进入 SSE 前等待；暂停时游戏线程真的停，
  速度器按档位缩放每次停顿。分片睡眠让暂停在长停顿中间也能及时响应。
- **暂停中重置 502**：`CinemaUI.abort()` 先把 `_aborting` 置位并放行暂停闸门，
  再走 `WebUI.abort()`；修复了游戏结束时 `/api/reset` 在 `_emit` 里
  自己抛 `AbortGame` 导致坏响应的问题。
- **模型可见 ⟺ 已记录**：低语经 AIPC 的 `context_messages` 进入循环，随事件日志
  可重放，不走侧信道。
- **AIPC 降级诚实化**：配置了 LLM 但 AIPC 挂载失败（组件异常/角色重名）时，
  `build_session` 把 `ui.llm_active` 置 false，剧场如实显示「本地导演接管」。
- **静态资源白名单**：`/static/` 允许 `cinema/` 子目录，仍做路径规范化与扩展名
  白名单（png/jpg/jpeg/webp/svg/css/js），杜绝 `..` 穿越。
- **地图自动落子**：新增 `ask_map_move` 的本地导演实现，从最近一帧地图里选
  朝最近敌人的合法空格；无地图/无路时取消，不阻塞。

### 资产与构建

- 新增 `tools/build_cinema_assets.py`：从 `.reference/` 的免费
  *Cyberpunk RED Easy Mode* 中文手册裁出 5 张角色画与 4 张场景插画；
  以 Pillow 生成安全屋/血房间/仓库/黎明底片，统一套色、颗粒、暗角；
  输出 14 张 JPG（9 张场景/底片 + 5 张肖像，约 4.5 MB）到
  `trpg/web/static/cinema/`。
- 全部资源本地化，运行时不联网；字体只使用系统衬线/无衬线。
- 版权与来源说明见 `README.md`「发行与许可」与
  [`docs/cinema-design.md`](docs/cinema-design.md)。

### 测试

- 新增 `tests/test_cinema.py`（9 例）：离线整局零输入到 `aftermath`、IP 菜单自动
  「稍后再花」、低语影响选项、暂停闸门阻塞/恢复、暂停中 abort 不抛异常、
  快照声部/autopilot、AIPC 下一拍上下文含低语且只消费一次、HTTP
  cinema 开局/暂停/速度/低语/重置与嵌套静态美术。
- 全量：`700 passed`（含剧场 8 例）。

## [未发行] — 修复：DeepSeek Responses 并行工具调用报 400（No tool output found）

上一版之后网络与探针都正常了，但对局推进到第二次模型调用时报：

```
LLM 请求失败（HTTP 400）：No tool output found for tool call call_01_….
```

根因是 **DeepSeek 的 Responses 兼容端点要求「结果紧跟自己的调用」**（严格相邻），
而并行工具调用按 `[call A, call B, out A, out B]` 平铺时，B 夹在 A 和 A 的结果
之间，于是它判定第二个调用没有结果。OpenAI 自己只按 `call_id` 配对、不要求相邻，
所以这个坑只在 DeepSeek 上暴露（社区有同类 issue：
[awesome-deepseek-integration#740](https://github.com/deepseek-ai/awesome-deepseek-integration/issues/740)、
[DeepSeek-V3#1588](https://github.com/deepseek-ai/DeepSeek-V3/issues/1588)）。

### 修复

- **转换层改为交错输出**：`[call A, out A, call B, out B]`，每个调用后面紧跟
  自己的结果；两轮「思考 + 并行调用」的历史也逐个校验通过
  （`responses.to_input_items`）。
- **丢弃半截工具链**：找不到结果的调用、找不到调用的结果都不发送
  （`adapter.sanitize_tool_messages`，chat 与 responses 两条路径共用）。
- **工具执行异常也回填结果**：`loop` 里工具抛异常时写一条 `ok:false` 的结果，
  历史不再留下「调了没回」的缺口。
- **上下文压缩不再切断工具对**：切口遇到 `tool` 消息时向前回退，把对应的
  `assistant(tool_calls)` 一起保留。
- **兜底降级**：万一仍收到「工具配对」类 400，Responses 适配器会把同一条对话
  切到 chat/completions 重发（显式选 Responses 时也生效），不再一 400 打死整局。
- 400 的提示改成可读解释（不再误报「模型名不存在」）。
- 回归测试 +11：并行调用交错、逐个相邻校验、孤儿调用/结果丢弃、
  修形器、工具异常成对、压缩切口、400 兜底降级。

## [未发行] — 修复：思考模式下「测试连接」必然失败（探针预算 16 → 1024/2048）

上一版修好了「Tunnel 403」之后，「拉取模型列表」正常，但「测试连接」在
**思考模式开着**时报 `模型输出被 max_output_tokens 截断且没有正文`。
这不是密钥/网络/协议问题：`POST /api/llm/test` 的探针只给 **16** 个输出 token，
而 DeepSeek V4 起思维链先花预算，正文还没开始写就被截断。

### 修复

- **探针给够预算**：1024 token；仍被吃光则自动翻倍到 2048 重试一次
  （`budget` / `budget_boosted` 随响应下发，界面注明「已自动加大探针预算」），
  两次都截断才报错，hint 直接说「把思考模式关掉再点一次」。
- **对局同样兜底**：思维链也占输出预算，而 NPC 台词只有 300 的 cap——
  chat 与 responses 两条路径在思考打开时把预算抬到 `THINKING_MIN_TOKENS = 1024`
  的下限（`max_tokens` 是上限不是账单，抬上去只让正文写得完，不多花钱）。
- **保存密钥后侧栏即时刷新**：底部「未接入 LLM」不再需要整页重载。
- **`.env` 免填密钥**：启动时读取项目根目录的 `.env`（已在 `.gitignore`），
  已存在的环境变量优先，只读不写、不打印任何值。
- 清理 `.env.example` 里从未实现的 `TRPG_LLM_TEMPERATURE` / `TRPG_LLM_MAX_TOKENS`
  （各 Agent 的温度与预算本就是自己的参数，现在由思考下限兜底）。
- 回归测试 +8：探针预算/翻倍重试/两次截断提示、思考下限（chat + responses 请求体）、
  `.env` 读取与优先级。

## [未发行] — 修复：LLM 请求被进程代理挡在隧道层（Tunnel 403）

设置页「拉取模型列表」「测试连接」一起报
`URLError: Tunnel connection failed: 403 Forbidden`，但密钥、模型、协议都是对的
——**这条 403 来自代理，请求根本没到供应商**：服务进程继承了终端/策略代理
（或已关闭 VPN 残留）的 `HTTP_PROXY` / `HTTPS_PROXY`，urllib 把请求塞进
`CONNECT` 隧道，代理直接回 403。

### 修复

- **出网默认直连**（`trpg/harness/adapter.py`）：统一构建 opener，默认
  `ProxyHandler({})` 忽略环境变量与系统代理；要经代理必须显式声明
  `TRPG_LLM_PROXY`（`direct` / `env` / `http://host:port`）。
- **继承来的代理失败时自动直连重试一次**：`Tunnel connection failed` / 407
  这类隧道层失败说明请求没到供应商，重试不会重复计费；显式指定的代理不回落
  （用户点名了就如实报错）。
- **提示说清「是代理挡的」**：`/api/models`、`/api/llm/test` 的 hint 与
  `--check-llm` 的报错都直接点名 `TRPG_LLM_PROXY`；设置页新增「出网方式」一行，
  直连时列出被忽略的代理地址（`/api/settings/llm` 的 `network` 字段）。
- 回归测试 `tests/test_network_proxy.py`（16 例，无网络）：代理解析、
  直连/回落策略、设置视图字段、隧道失败提示。
- 文档：README「网络与代理」、HARNESS.md、`.env.example` 同步。

## [未发行] — 界面改版：全暗赛博仪表盘 · 自研 VTT · 随时存档

面向"当代 AI 跑团 / AI 驱动 CRPG"的操作方式重做**界面层**：把原先
"墨色底 + 羊皮纸金框"的外观整体换成**单层暗色仪表盘**，补齐 CRPG 该有的
行动条、动作经济与战术棋盘，并加了一枚随时可用的存档按钮。
**规则口径、数值、剧情文案、引擎 API 与 SSE 事件契约一律未改动**——
新增字段全部是向后兼容的追加。

### 变更

**外观：弃用羊皮纸，改为夜之城暗玻璃**

- 材质体系从"暗底 + 纸面"改为**单层暗色玻璃**：底 `#06080c`、面板 `#0d1219`、
  描边 `#1b2532`；语义色收敛为三个——霓虹青（己方/可交互）、品红（敌方/危险）、
  琥珀（掷骰/结算/幸运）。
- **字体分三族**：操作层用系统无衬线、叙述长文保留衬线（中文长段无衬线读起来发飘）、
  **所有数字（PW / SP / eb / IP / DV / 米数 / 轮次）一律等宽**。
- 全局背板改为霓虹夜之城剪影 + 透视网格 + 扫描线，开始页另加星点与城市剪影层，
  全部为 CSS/SVG 运行时合成，**未引入任何外部资产**。
- 统一动效 token（`--dur-1/2/3`、`--ease-out`），收掉此前散落的硬编码时长；
  补 `prefers-reduced-motion` 降级。
- 清掉 `app.css` 里自开始页改版后无人引用的 `.cover-*` 死代码。

**对局界面：CRPG 式行动条**

- 底栏重做为三段式**行动条**：左为**当前行动者**（头像 / PW 条 / 护甲 SP），
  中为当前情境行（此前被整条丢弃的引擎提示串，如"—— Mover 的回合 · 移动 10 米 /
  其他动作 1 次 ——"），右为按 **检定 · 推进 · 战斗 · 支援 · 移动 · 队伍** 分组
  的行动按钮（按引擎菜单串前缀归类，不改协议），支持数字键 `1`–`9` 直选。
- 战斗时显示**动作经济槽**（移动动作 / 其他动作 / 附赠 · 第二发 / 反应 · 闪避），
  从提示串与当前状态推导，实时反映"这一回合还剩什么"。
- **作战卷宗**：点任意队伍卡打开角色档案——十项属性、技能等级与总值、武器、
  义体、致命伤、状态、人生之路（这些数据服务端早已下发，此前前端一个字没用）。
- **DM 裁定指示器**：AI 模式下等待期间显示呼吸式"DM 正在裁定 ●●●"，
  下一个事件到达即收起（此前只有一行静止的"等待世界回应……"）。
- **反馈层**：顶部细提示（进入场景、暴击成功/失败、致命伤）+ PW 跌破重伤线后的
  暗红脉动边框；致命伤改为 2d6 表窄条（含掷值 chip），不再打断叙事流。
- 顶栏新增「？」随时重看操作速查；新手引导内容同步改版（含地图与存档说明）。

**自研 VTT：模组地图直接画成战术棋盘**

- 模组 `map` 声明（`grid` / `obstacles` / `cover` / `party_spawn` / `tokens`）
  在浏览器里直接渲染：障碍格（**阻挡视线**）、掩体格（**挡子弹不挡视线**，
  带材质配色与**剩余耐久**）、角色棋子（程序化头像 + PW 血条）、敌人棋子、倒地处灰化。
- 交互：滚轮以光标为锚缩放（0.4×–4×）、指针拖动平移、`⤢` 适应窗口、
  `格` 切换网格线、窗口变化自动重排；轮到移动时点图落子。
- **演示层**：悬停任意格实时显示距离（Chebyshev × 2 米）、Bresenham 视线是否被
  障碍阻挡、当前手持每种远程武器在该距离的**射程带 DV**（超射程标"超出射程"）；
  本回合可达范围以青色虚线格点亮（8 向 BFS，绕开障碍与占用）。
- 判定口径与 `rules/combat.py` 严格一致（`dist_m` / `has_los` / `range_band`），
  **前端只做演示不做结算**；掩体表与射程带由 `/api/meta` 下发，不在前端硬编码，
  避免与 `rules/data.py` 漂移。
- 无地图场景给空态卡（"本场无战术地图 · 剧场模式"），不再是一个空面板。

**随时存档**

- 顶栏新增**保存按钮**：探索期立即落盘；战斗期经既有旁听指令通道排入队列，
  由 `player_combat_turn` 在**本回合开头**消费（战斗期只认存档指令，
  以免在战斗正中间弹出购买/休整菜单）；终端 UI 无指令槽时静默跳过，行为不变。
- 主页新增「最近一次旅程」卡片：一步续档，不必先翻存档页。
- 已知行为边界（既有存档格式所致）：存档记录**场景位置 + 队伍 + 旗标 + RNG + 地图**，
  **不含战斗中盘**——战斗中存的档读回来会让该场遭遇战从头开始。

### 修复

- **场景掩体在探索期丢失**：`build_map_state` 未把场景的 `map.cover` 带进运行时
  地图，导致探索期前端拿不到掩体（战斗期走 `Encounter.cover` 才有）。
  现已原样入图，探索期按材质满值下发。
- **掩体耐久在战斗中途不刷新**：攻击造成的掩体损耗原本要等到回合末才上棋盘。
  现在 `combat_action` 攻击后与 AI PC 回合结束（含 LLM 故障兜底路径）都会广播地图快照，
  掩体耐久条即时更新。

### 数据面（向后兼容的字段追加）

- `GET /api/meta` 新增 `cover_materials`（掩体材质 → 耐久）、`range_bands`（八档射程带）、
  `ranged_dv`（三类远程武器各档 DV）、`meters_per_cell`。
- `GET /api/state` 的队伍成员新增 `weapon_details`（含 `kind`，供射程查表）与
  `skills`（技能等级表）；快照新增 `in_encounter`（战斗标记）与 `pending`
  （最后一条待答请求，用于刷新页面后重建行动条）。
- SSE `map` 事件新增 `cover`（`{"x,y": [材质, 耐久, 已摧毁]}`）与 `cover_mesh`
  （该帧是否来自战斗网格）。

### 文档

- `README.md` 的「对局界面」重写为改版后的操作说明（行动条 / 动作经济 / 作战卷宗 /
  自研 VTT / 随时存档）；「界面预览」一节的旧截图**尚未重出**，已就地标注说明。
- `docs/battlemap-design.md`：修正两处过时表述——掩体**不**阻挡视线（只挡子弹，
  伤害先打掩体、耐久归零即摧毁、剩余伤害作废），并新增 §5.1 前端 VTT 的数据映射、
  判定一致性与交互说明。

### 追加（同一发行窗口）：数据看板 · 全站布局重构

**新增：token 用量热力图（GitHub 贡献图式）**

- `TokenMeter` 增加**按日聚合** `daily`（`YYYY-MM-DD` → 输入/输出/合计/调用数）：
  逐次明细仍只留最近 2000 条供「最近调用」表，而按日聚合不随明细裁剪丢失，
  一次游玩散落在几十天里也能画出完整一年。旧存档（只有 `records`）加载时自动回填。
- 热力图布局由服务端算好：**列 = 周、行 = 周日…周六**，末列补齐到周六、
  首列对齐到起始日所在周的周日，月份标签只在换月且间隔 ≥3 周时落在对应列；
  格子等级按当日用量的**平方根四档**（`l1`–`l4`），少量极端值不会把其余日子压成同一档。
  同时给出峰值日、活跃天数、当前连续与历史最长连续。
- 前端渲染在四处复用同一套组件：**数据看板页**、**对局内「▦」叠加层**、
  设置页概览、**侧栏迷你图**；前端按容器宽度反推格子尺寸（含窄屏 2px/1px 间距），
  一年 53 周能塞进一屏而不出现横向溢出；时间跨度（近 4 个月 / 7 个月 / 一年）
  记在 `localStorage`，页面缩放时自动重排。

**新增：账户余额（只读）**

- 新增 `trpg/harness/balance.py`：按供应商分派余额接口——DeepSeek
  `GET {root}/user/balance`（`balance_infos[]`：总额 + 赠送/充值）、Kimi/Moonshot
  `GET {base}/users/me/balance`（可用余额 + 代金券/现金）；GLM / MiniMax / 自定义
  官方无公开接口，可用 `TRPG_BALANCE_URL` 指向兼容网关，未配置时返回「不支持」。
- 安全边界与 `adapter` 同源：协议白名单 + 主机解析校验（阻断私网/环回/链路本地）、
  只读 GET、逐跳重定向复检；**密钥只进 `Authorization` 头，不回显、不落盘**
  （有测试断言响应里不含密钥）。任何异常都归一成 `{"ok": false, "error", "hint"}`。
- 服务端 60 秒内存缓存（`?refresh=1` 强刷）；顶栏新增**余额芯片**，
  点开即数据看板；设置页保存 API 后自动刷新看板。
- 余额不可用时界面显示「余额 —」与可读原因（未接入 / 该供应商无接口 / HTTP 401…），
  token 热力图与**估算花费**照常工作；估算价按「元 / 百万 token」参考价折算，
  可用 `TRPG_PRICE_IN` / `TRPG_PRICE_OUT` 覆盖（只做展示，不参与结算）。

**布局重构：大厅导航栏 + 对局三栏 + 数据看板**

- 大厅由「单列堆叠」改为**左侧固定导航栏 + 右侧视图区**：七个视图（主页 / 新的冒险 /
  剧本 / 存档 / 数据看板 / 帮助 / 设置）共用 `[data-view]` 一处绑定，导航项高亮当前视图，
  侧栏底部常驻**今日用量 + 迷你热力图 + 接入状态**。
- 主页改为「巨幕 + 看板」：标题巨幕（星点与城市剪影仍为纯 CSS）、主行动按钮、
  最近旅程卡、六个快捷入口卡，右栏为用量概览与本局速览。
- 对局界面改为**左栏队伍 / 中栏编年史 / 右栏战术地图 dock / 底部行动坞**：
  地图不再挤在中间，无地图场景自动收起右栏；顶栏重排为品牌 · 面包屑 · 模式徽标 ·
  资源芯片（IP / 欧元 / **余额**）· 存档 · 数据看板 · 帮助。
- 全新设计系统：分层表面（`--surface-1..4`）+ 发丝描边 + 圆角/间距/字号/动效全部走 token，
  统一按钮（solid/ghost/quiet/tiny/icon/lg）、卡片、芯片、分段控件、KPI、条形分布与表格；
  响应式重做（1320 / 1100 / 980 / 820 / 620 五档），窄屏导航折叠为横向滚动条，
  修正 `1fr` 网格在窄屏被内容撑开的横向溢出。
- 深链：`?view=stats` 直接打开某个大厅视图，`?tour=0` 跳过开场引导。

**修复**

- **主页「最近一次旅程」永远不出现**：该卡只由「新的冒险」页的模组列表渲染函数产生，
  从未在主页渲染。现在进入主页即拉取存档与接入状态。
- **可达范围高亮从未生效**：前端读 `S.eco.move_left`，而行动经济对象里字段名是
  `moveLeft`，判定恒为假；改为同一字段后，「轮到移动时点亮可达格」按设计工作。
- 「清空记录」同时清掉按日聚合（此前只清明细，热力图仍有数据），并同步刷新
  看板、设置页与侧栏三处显示。
- 余额卡与看板在窄屏溢出、热力图末列被裁：见上文的网格与尺寸自适应。

**数据面（向后兼容的字段追加）**

- `GET /api/usage` 追加 `by_model`、`heatmap`、`today`、`last_7`、`last_30`、
  `cost`；`totals` 追加 `calls`（按日聚合的调用数）。新增可选查询参数 `days`（7–400）。
- 新增 `GET /api/balance`（`?refresh=1` 绕过缓存）。
- `saves/token_usage.json` 升级为 `{"version": 2, "records": [...], "daily": {...}}`；
  旧文件（无 `version`/`daily`）仍可读，加载时自动回填。

**测试**

- 新增 `tests/test_usage_balance.py`（23 项）：按日聚合与明细裁剪的解耦、旧存档回填、
  时间窗口与费用估算（含环境变量改价）、热力图周行列对齐与等级单调、连续天数、
  空账本安全、DeepSeek/Kimi/自定义网关三种余额响应解析、端点映射与环境变量覆盖、
  缺配置/无接口/非法协议三条早退路径、**密钥不外泄**，以及 HTTP 端点的形状与清空行为。
- 全套 `python -m pytest tests -q`：**614 passed**（原 591 + 新增 23）。

**文档**

- `README.md`：「开始页」补数据看板入口与深链说明，「对局界面」按三栏新布局重写，
  新增「用量热力图与账户余额」一节（数据源、供应商接口对照表、两个环境变量、
  三个 HTTP 端点），项目结构与测试覆盖同步更新。
- `docs/screenshots/` 按新版外观重出三张实际运行截图：`home-city.png`（主页）、
  `game-board.png`（对局三栏 + 地图 dock）、`stats-heatmap.png`（数据看板），
  删掉已过期的 `home-dark.png`；「界面预览」改为图集并标注演示数据来源。

**修复（剧本模式的「DM 正在裁定」卡死）**

- **现象**：剧本模式（互动剧）里点掉一个选项后，底部一直显示「DM 正在裁定 ●●●」，
  新选项不出现，只能刷新页面。
- **根因（前端竞态）**：`submitAnswer()` 在 `await POST /api/answer` 返回后才清理
  「正在等待」状态——把 `S.ask` 置空、600ms 后收起选项、点亮裁定指示器。剧本引擎是
  **毫秒级**的：应答期间下一问已经由 SSE 推来并由 `showAsk()` 渲染好，于是这笔迟到的
  清理把**新问题**清掉、把**新选项**收起、把指示器点亮，而之后再没有事件来清除它。
  跑团模式里 DM 回合要几秒，这段竞态被时间掩盖了，所以此前只在剧本模式稳定复现。
- **修法**：作答前记住「本次作答针对哪一问」（`const asked = S.ask`）；回包后若
  `S.ask` 已不是它（说明新问题已经到达并渲染），就**什么都不动**——不清 `S.ask`、
  不收起新选项、不点亮裁定指示器，只顺带刷新快照。
- **顺带修掉的两处同源问题**：
  - `/api/state` 的剧本快照此前**不带 `pending`**（只有跑团对局带），刷新页面时只能
    靠 SSE 历史重放重建选项；现在补上，与跑团快照同口径。
  - `refreshState()` 的 `pending` 兜底会把「刚答过的那一问」重新画回屏幕（点了只会拿到
    409）。改为比对问题签名（kind + prompt + options），已答过的旧问题不再回填。
  - 剧本模式没有 DM：等待文案改为「剧本推进中」，不再借用「DM 正在裁定」
    （`setThinking` 按 `S.mode` 取词）。
- **验证**：无头 Edge 走完整链路——剧本模式连续作答 13 回合直到结局卡
  （ENDING · B「一个不留」），每回合 `thinking=false`、选项可见；跑团模式
  检定→掷骰→推进场景多回合无回归；对局中刷新页面选项能正确重建；
  新增 `tests/test_story.py::test_state_exposes_pending_in_story_mode`。

**修复（AI 模式开局即失败：思维链未回传 + 接口/模型过期）**

- **现象**：配好密钥后开 AI 模式，第一个 DM 回合就失败，界面退回经典模式或
  只报一句「LLM 请求失败」。
- **根因（其一，致命）**：DeepSeek V4 起思考模式**默认开启**，官方规则是
  「带 `tools` 的请求，历史轮次的 `reasoning_content` 必须逐轮回传，否则返回
  **400**」。本项目每个 Agent（DM / 队友 / NPC / AI 代打）都靠工具推进，
  而 `adapter` 没解析 `reasoning_content`、`loop` 回写 assistant 消息时也没带上它
  ——第一次工具调用之后必然 400。
- **修法**：`ModelResponse` 新增 `reasoning`；适配器解析
  `reasoning_content`（兼容 `reasoning`）；`assistant()` 仅在非空时写入
  `reasoning_content`；agent 循环随历史回写，事件日志同步（模型可见 ⟺ 已记录）。
  同时**默认给 DeepSeek 下发 `{"thinking": {"type": "disabled"}}`**（设置页可开
  ——思考更快更省，也更适合叙事；打开后思维链回传已就绪）。
- **根因（其二）**：供应商预设停留在旧世代——`deepseek-chat`、`glm-4.6`、
  `kimi-k2-turbo-preview`、`moonshot-v1-8k`、`MiniMax-Text-01` 均已非官方推荐，
  Kimi 的 kimi-k2 / moonshot-v1 系列 2026 年已下线。按 2026-10 官方文档重写预设：
  DeepSeek `https://api.deepseek.com` + `deepseek-flash` / `deepseek-v4-pro`；
  智谱 `.../api/paas/v4` + `glm-5.3` / `glm-5.3-flash` / `glm-5.3-flashx` / `glm-5.2`；
  Kimi `.../v1` + `kimi-k3` / `kimi-k2.7-code` / `kimi-k2.6`；
  MiniMax `.../v1` + `MiniMax-M2.1` / `MiniMax-M2`。
- **模型名不再写死**:新增 `GET/POST /api/models`：用当前密钥请求 `GET {base}/models`，
  把账号真实可用的型号列进设置页候选；设置页新增「⇩ 拉取模型列表」按钮，
  并提示「当前填写的模型不在可用列表里」。预设只是默认值与兜底。
- **地址整形**：`normalize_base_url()` 处理整段粘贴（`.../v1/chat/completions`）、
  结尾斜杠与空格；`/v1`、`/api/paas/v4` 这类路径前缀保留。
- **错误可读**：HTTP 4xx 直接抛（不再无意义重试），错误文本带上供应商返回的
  `error.message` 与按状态码的可操作提示（401 密钥 / 402 余额 / 404 地址 /
  400 模型名或参数）；「空正文 + `finish_reason=length`」单独报成
  「输出被 max_tokens 截断：思考模式会先消耗输出预算」，不再静默失败。
  `/api/llm/test` 与设置页同步展示 `hint`。
- **思考开关持久化**：`saves/llm_config.json` 增加非敏感字段 `thinking`
  （仍无任何密钥字段），环境变量 `TRPG_LLM_THINKING` / `TRPG_LLM_REASONING_EFFORT`。
- 新增 `tests/test_llm_endpoints.py`（22 项）：端点整形、思考参数下发、**思维链
  回传（含事件日志同口径断言）**、错误文本与提示、模型列表解析（OpenAI 风格与
  网关风格）、预设地址/模型钉住、`/api/models` 与保存整形的 HTTP 行为。
  全套 637 passed。

**新增（接口协议）：默认改走 OpenAI Responses API**

- 新增 [trpg/harness/responses.py](trpg/harness/responses.py)：`POST {base}/responses`
  适配器，只做协议转换不做业务判断——
  出站把 chat 风格消息折叠成 `instructions` + `input` items
  （`message` / `function_call` / `function_call_output` / `reasoning`），
  工具表拍平成 `{"type":"function","name","description","parameters"}`，
  `max_tokens` → `max_output_tokens`；入站把 `output` items 还原成
  正文 / `tool_calls`（`call_id` 配对）/ 思维链 / usage
  （`input_tokens`→`prompt_tokens`、`output_tokens`→`completion_tokens`，
  数据看板口径不变）。无状态，不用 `previous_response_id` / `store`。
- 协议三选一（设置页「接口协议」+ `TRPG_LLM_API_STYLE`）：
  **自动** = 供应商声明支持就走 Responses（DeepSeek / Kimi / 智谱）；
  MiniMax 与自定义网关默认 chat；`auto` 下 Responses 遇 404 自动降级并在
  「测试连接」注明；显式指定协议时不降级（便于排查）。
- 预设新增 `responses` 能力标记；DeepSeek 文档链接改指 Responses 指南。
- 「测试连接」回报实际走的通道（`api_style` / `fallback` / `reasoning`），
  400 时若协议为 Responses 会额外提示可切回自动/chat。
- 新增 16 项测试（消息与工具表转换、items 配对、思维链 item、响应解析、
  usage 映射、截断报错、端点与请求体、404 降级、协议解析、设置往返），
  全套 **653 passed**。

**测试卫生（顺带修掉的真实事故）**

- 跑测试会**覆盖玩家的真实存档**：引擎的 `autosave()` 与 `/api/start` 都用相对路径
  `saves/game.json`，而 `test_game` / `test_aipc` / `test_ember_features` 三个模块
  会推进场景、购物与休整（都触发 autosave），在仓库根跑测试就把存档写成了夹具
  `fixture-red`。现在三个模块在 `setUpModule` 里调用新增的
  `helpers.isolate_saves()` 切进临时目录、`tearDownModule` 还原
  （pytest 与 `unittest discover` 两种跑法都已验证不再写仓库）。

## [上一版] — 仓库整理 · 改名 TRPG · 模组对齐轻松模式 · 新增互动剧本

纯整理，**不改动任何游戏逻辑、规则口径或数据**。

### 变更

**项目标识统一为 TRPG**

- Python 包 `ctrcg` → `trpg`（`git mv`，文件历史保留）：全部导入、`pyproject.toml`
  项目名与打包清单、发行包名 `TRPG-<版本>.zip`、界面显示名与 HTTP 服务标识同步。
- **环境变量前缀 `CTRCG_` → `TRPG_`**：`TRPG_LLM_API_KEY`、`TRPG_LLM_BASE_URL`、
  `TRPG_LLM_MODEL`、`TRPG_LLM_PROVIDER`、`TRPG_LLM_ALLOW_PRIVATE`、`TRPG_DATA_DIR`、
  `TRPG_AIPC`。**旧变量名不再识别**，升级时请改写环境变量或改用「设置 → API 配置」。
- HTTP 服务标识改为由 `trpg.__version__` 推导（原为硬编码 `CTRCG/1.4`，
  自 1.5.0 起就没跟上版本号）。

**仓库布局**

- 仓库根上移一层：原先嵌套在 `WebUI/TRCG/`，现直接以仓库根为项目根
  （目录名 `TRCG` 的拼写不一致随之消失）。
- 规则书 PDF 由 `模组/` 移到 `.reference/`——与既有 `.gitignore` 规则一致（约 53 MB 不入库），
  也不再与代码用的 `modules/`（原子模组目录）混淆。
- 开发过程资料由 `_work/` 整理归档为 `_archive/`：`design/`（移植规格 PORT_SPEC）、
  `recon/`（架构侦察笔记）、`pdf-extraction/`（角色卡 PDF 反推脚本与产出数据）、
  `verification/`（验收与冒烟脚本）、`scripts/`（数据加工脚本）、`notes/`、`reorg/`；
  可再生的中间裁图与运行日志已删除（约 37 MB → 约 5 MB）。
- `docs/screenshots/` 收入开始页截图两张，README 的「界面预览待补」据此补齐。
- `trpg/campaign/loader.py`、`trpg/rules/data.py`、`docs/sillytavern-architecture.md`、
  `_archive/design/PORT_SPEC.md` 中指向旧路径的引用已同步更新。

**模组严格对齐《轻松模式》**

- 移除轻松模式未收录的武器数据：`D.WEAPONS` 删掉冲锋枪 `smg`、重型冲锋枪
  `heavy_smg`、狙击步枪 `sniper_rifle`、弓 `bow`、弩 `crossbow`（15 → **10 种**）；
  `D.WEAPON_TYPES` 删掉 `smg`/`bow`/`grenade`/`rocket`；`D.RANGED_DV` 只留
  手枪 / 霰弹枪 / 突击步枪三条，与手册第 19 页「远程战斗难度值」表完全一致；
  `RANGED_ORDER`、`game/session.py`、`rules/combat.py`、`web/server.py` 同步更新。
- 依据：手册第 19 页的射程表只有这三类远程武器；中、波两版全文都检索不到
  冲锋枪、狙击步枪、弓弩、火箭（`pistolet maszynowy`/`snajper`/`kusza`/`rakiet`
  零命中），榴弹也只出现在冒险正文的一句「闪光手榴弹」里。
- 终章仓库的守军改为手册口径的**玩家角色数 + 2** 名腐败警察（3 人队 = 5 名，
  原先只配 2 名），摩托增援仍在第二回合额外入场；战术地图补上新守军的落点。
- `D.CYBERWARE` 的工具手（`tool_hand`）确认在轻松模式角色卡上（Torch / Redtail），
  保留不动。README 的装备计数与「明确未实现」措辞同步更新。

**新增内置互动剧本**

- `modules/cpred-easy-mode/story_wyskakuj.json`：《抢钱行动》（*WYSKAKUJ Z FORSY!*）
  ——**70 个节点、8 个结局**，主角为模组的五名预设角色
  （属性与技能直接取自同一份角色卡数据，两种模式共用一份事实来源）。
- 结构照手册：来电（情绪解读）→ 出发前准备（踩点 / 排战术 / 检装备，各换一个有利条件，
  可做可不做）→ 会面与巷中伏击（假钞、审讯、追逃）→ 营救中间人（急救 / 医疗救援 /
  战斗出租车）→ **清除 / 渗透 / 外交 / 第四选项**四条路 → 尾声结算。
- 八个结局：`walkaway`（这单我不接）、`deal`（五五分成）、`deal_better`（更好的条件）、
  `ghost`（夜之城的鬼，全程没开枪）、`wipeout`（一个不留）、`fourth`（第四选项）、
  `paid`（拿到报酬）、`blood`（血染之时，死亡结局）。
- 外交线对摇滚客与媒体人有专属桥段（引擎的 `variants` 条件文本第一次有内置数据在用）；
  伏击中接过箱子的人在尾声中身价翻倍。
- 脚本化通关验证：4000 次随机演绎零运行时错误，8 个结局全部可达。

## [2.0.0] — 2026-10（发行 ·《赛博朋克 RED》移植）

规则集整体替换：旧的 5e 规则层整体换成 **《赛博朋克 RED》（Cyberpunk RED）**，
以 R. Talsorian Games 免费发布的**轻松模式（Easy Mode）**口径为准。舞台从旧奇幻设定
迁到 **2045 年的夜之城**。规则层、模组体系、内置剧本、角色创建与界面词汇全部重写。

### 新增（规则引擎 `ctrcg/rules/`）

- **十项属性**（智力/反应/敏捷/技术/魅力/意志/幸运/机动/体格/共情）与
  **41 项技能**（等级 0-10，其中「电子学与安防」「医疗救援」为 ×2 技能）；
  检定加值 = 挂钩属性 + 技能等级。
- **技能检定**：`1d10 + 属性 + 技能` 对照**难度值 DV**，**必须高于** DV
  （平手判防守方胜）；**暴击成功**（掷出 10 追加一颗 d10 相加）与
  **暴击失败**（掷出 1 追加一颗 d10 相减）；**幸运点**（幸运池每轮恢复满，
  掷骰前声明，每点 +1）；辅助技能 +1、投入四倍时间 +1。
- **难度值档位**：日常 13 / 困难 15 / 专业 17 / 英雄 21 / 不可思议 24。
- **远程战斗**：按**射程带查难度值**（0-6/7-12/13-25/26-50/51-100/101-200/201-400/401-800 米，
  手枪/霰弹枪/突击步枪各一列），超出射程即无法攻击；瞄准射击 -8（头部/躯干）；
  反应 8+ 可用闪避对抗子弹（消耗反应）。
- **白刃战**：对抗闪避并**无视防守方一半护甲**；**斗殴**一次动作打两下，
  含**擒抱**（双方动作 -2）、**锁喉**（等于体格的无视护甲伤害，连续三轮昏迷）与
  **投掷**（伤害 + 倒地）。
- **护甲 SP 与磨损**：先减护甲再减 PW，挡下伤害即 **-1**；皮下护甲与护甲取高者；
  护甲可花钱修复。
- **致命伤**：伤害骰至少两颗且出现两个及以上 6 → 额外 5 点无视护甲伤害 +
  **2d6 致命伤表**（12 条，效果与治疗难度全部机制化）。
- **伤势等级**：轻伤 / 重伤（PW ≤ 上限一半，检定 -2）/ 濒死（PW < 1，检定 -4、
  移动 -6，每回合生存检定）；**生存检定**低于门槛（10 + 累积 + 致命伤）即存活、
  掷出 10 自动失败；**稳定**（DV 按伤势 10/13/15）、**急救固定**（当天消除一处致命伤）、
  **治疗**（四小时永久消除，外科条目限医疗师，不能治疗自己）、
  **休整**（每个完整休息日恢复等于体格的 PW）。
- **掩体**（按材质给出耐久，2m × 2m 区域，耐久归零即毁）、**对峙**
  （魅力 + 声望 + 1d10）、**义体与人性**（人性 = 共情 × 10，义体扣人性并压低共情，
  归零即赛博精神病）、**欧元（eb）**经济与夜市场购买。
- **成长改用改进点（IP）**：技能 = 20 × 新等级（×2 技能翻倍）、职业特殊能力 =
  60 × 新等级；没有等级，也没有别的成长轨道，属性不可提升。
- 战术网格单位改为 **1 格 = 2 米**，移动力 = 机动 × 2 米。

### 变更

- **模组 JSON 规范升至 v3**（`MODULE_SPEC.md` 版本 3.0，适用引擎 `ctrcg` ≥ 2.0）：
  `pregens`（预设角色，取代 `companions`）、`custom_npcs`（取代 `custom_monsters`）、
  场景 `checks`（`skill` + `dv`，取代 `skills` + `dc`）、场景 `ip`（改进点，
  取代 `milestone` / `level_to`）、`default_companions`；
  校验器改写为 RED 口径（41 项技能 key、DV 9-30、敌人 key、地图 1 格 = 2 米、
  IP 总量软上限 4000）。
- **角色创建改为选预设角色**：轻松模式不掷骰建卡，玩家从模组 `pregens` 里挑一位，
  只给自己起街头名；终端与 Web 两条路径同步。
- **玩家指令**：`/状态 /背包 /义体 /急救 /稳定 /休整 /购买 /存档 /退出`
  （删除 `/短休 /长休 /喝药`）。
- **队伍面板与 SSE 载荷**：PW/护甲 SP/伤势/幸运/人性/欧元/IP/致命伤/状态；
  掷骰事件携带暴击、幸运点、护甲剩余与致命伤条目。
- **Web 元数据 `/api/meta`**：改为 `stats`(10) / `skills`(41) / `roles`(10) /
  `difficulty` / `pregens` / `weapons` / `armors`；删除 `races` / `classes` /
  `backgrounds` / `abilities` / `dice_rule`。
- **DM 工具面**：`skill_check(skill, dv, who, reason)` 改造，新增
  `opposed_check` / `award_ip` / `confrontation` / `request_roll`，
  删除 `save_roll` 与 `award_xp`；战斗动作面删除全部施法类与 5e 专属动作
  （`cast/shove/help/dodge/hide/disengage/second_wind/...`），
  新增 `grab/choke/throw/escape_grapple/reload/stand/stabilize/first_aid/
  confrontation/flee`。
- **存档**：`saves/game.json` 增加 `ruleset: "cyberpunk-red"`；旧存档（无此键或
  值不符）读取时被拒绝并提示不兼容；`saves/` 下的 5e 旧档与日志清空。
- **文档**：`README.md`、`MODULE_SPEC.md`、`HARNESS.md`（工具面一节）、
  `docs/aipc-design.md`、`docs/companion-npc-agents.md`、
  `docs/battlemap-design.md`（1 格 = 2 米、射程/AOE 口径）、
  `docs/sillytavern-architecture.md` 全部改写为 RED 词汇。
- **许可与归属**：移除旧的开放许可规则内容归属声明；改为声明规则文本改编自
  R. Talsorian Games 免费发布的《Cyberpunk RED Easy Mode》与《Cyberpunk RED》
  核心规则书，版权归 R. Talsorian Games 所有；Cyberpunk® 是 CD Projekt Red S.A.
  的注册商标；本项目为非官方同人作品，仅供个人学习与桌边自用，不得用于商业发行。

### 内置剧本（`modules/cpred-easy-mode`）

- **《赛博朋克 RED：轻松模式》**（*Cyberpunk RED: Easy Mode*）——轻松模式手册本体，
  内含手册的入门差事**「抢钱行动」**（原文 *WYSKAKUJ Z FORSY*）。
- **三幕六场**：第一幕·来电（`south_night_city_loft` 安全屋的来电、
  `prep_and_drive` 前往海伍德的准备）→ 第二幕·海伍德工业区（`heywood_meet` 假钞与
  伏击、`lazlo_rescue` 营救被拷打的中间人）→ 第三幕·仓库（`warehouse_approach`
  清除/渗透/外交/第四选项、`aftermath` 尾声与赏金，`next: null`）。
- **两场遭遇**：`e_alley_ambush`（警察人数 = 玩家角色数 + 2，含 Leonard Houston；
  逃走的人会在终章再次出现）与 `e_warehouse`（3 名腐败警察 + 第二回合撞窗冲进来的
  摩托增援 `bike_ganger`）；两场都带战术地图。
- **五名预设角色**：摇滚客 Forty（PW 40）、独行侠 Mover（45）、
  技术师 Torch（35）、医疗师 Redtail（35）、媒体人 24/7（40）。
  **人名一律用英文**（卡面上的街头名原名，如 `Lazlo` / `Leonard Houston`；
  中文只用于叙述正文与类型标签）——模组、内置敌人表、兜底剧本、界面、
  文档与测试统一口径，AI 模组生成提示词也加了这条硬性约束。
  `default_companions = ["medtech_redtail", "solo_mover"]`。
  前三张卡在中文版手册里数据完整（直接照录）；Redtail 与 24/7 两张卡的属性列不完整，
  其十项属性按卡面可读约束反推（PW = 10 + 5 × ⌈(体格 + 意志力)/2⌉、
  重伤阈值 = ⌈PW/2⌉、卡面死亡豁免项 = 体格、技能基础值 = 属性 + 等级）。
- **改进点**：六场戏合计每人 200 IP（0 / 20 / 60 / 40 / 60 / 20）——
  RED 的 IP 按人计，**不做队伍平摊**。
- 角色创建同步改为「选模组 → 选预设角色 → 起名 → 选 AI 队友（默认
  `default_companions`）」四步，不再掷骰建卡。

### 移除

- 全部 5e 模组目录与数据：`modules/blue-watch`、`starfall`、`saltmist`、
  `mistgate`、`embergate`、`generated-saltmarsh-siren-song`，
  以及 `blue-watch/story_last_watch.json`（剧情图引擎保留，不再内置剧情数据）。
- 旧规则层的全部数据与 API：六项属性与 18 项技能、豁免、法术、升级与成长点表、
  护甲等级、挑战等级、优势/劣势、黑暗视觉、旧货币，以及 `roll_scores`、
  `award_xp`、`save_roll` 等工具与函数（含 `data.py` / `character.py` /
  `dice.py` / `loader.py` 中的对应实现）。
- 界面与文档中的旧奇幻设定表述（大陆、海岸、BG3 名单等）。
- 旧 5e 模组的本地素材：`Additional Ressources/`（《蓝色守望之塔》原版 PDF、
  指挥官信件、怪物数据、四张地图、物品卡、纸模与棋子，约 131 MB）与
  `_local/pdf_pages` / `_local/pdf_images` / `_local/pdf_extract*.txt`
  （该模组中文版的页图与文本抽取物，约 44 MB）——两者都已被 2.0.0 取代，
  仓库不再保留任何 D&D 5e 素材。
- `docs/screenshots/`（旧规则版本的 4 张界面截图，约 3 MB）：界面预览待
  RED 版出图后重拍补入。
- 版本库外的旧时代产物一并清理（约 724 MB）：`_work/run/`（移植前的 5e
  工程整份拷贝，git 历史已有）、`_local/dist/CTRCG-1.0.0~1.4.0.zip`（旧版
  发行包）、死循环事故留下的 `demo_out.txt` / `demo7.log`（约 711 MB）。
  1.6.0 会话日志的唯一抢救副本 `_local/logs-ads/` **保留**。

## [1.8.0] — 2026-10-05

剧本模式（互动剧）：自写分支剧本 · 分支选择 · 多结局（自 Godot 版移植回 demo）。

### 新增
- **剧本数据**：`modules/blue-watch/story_last_watch.json` —— 《蓝色守望 · 最后的值夜》，
  基于原版模组世界观创作的前传互动剧（蓝色守望覆灭之夜）。35 节点、3 位可选主角
  （各有技能面/生命值/专属选项）、6 种预设结局（真结局/光荣/灰色/堕落/沉默/死亡），
  与正篇模组的地窖箱子严格呼应。剧本与 Godot 版共享同一份 JSON，两个引擎通用。
- **剧情引擎**：`ctrcg/game/story.py` —— 节点图演绎（chapter/text/variants/
  next/branch/choices/ending），选项支持显示条件（require flags/not_flags）、
  d20+技能加值检定（骰子系统结算，前端播掷骰动画）、伤害与生命归零进入
  death_ending；`validate_story` 图校验 + `reachable_endings` 全结局可达验证；
  结局图鉴 `saves/story_endings.json` 持久化。
- **Web 端**：`GET /api/stories`（剧本列表+图鉴解锁状态）、`POST /api/story/start`
  （与跑团对局共用 UI 槽/SSE 流/`/api/answer` 应答协议，互斥开团）；
  `AppState.snapshot()` 新增剧本模式分支（mode=story + 主角 HP）。
- **前端**：开始页新增「❖ 剧本模式」入口与剧本页（模组卡片式剧本列表、
  结局图鉴收集展示、主角选择）；对局界面完整复用（编年史/骰子演出/选择按钮）；
  剧本结局卡（Flourish.storyEnding：结局名/等级/正文/图鉴进度）。
- **终端**：`python main.py --story [--story-id ID]` —— 选剧本/选主角/演绎/结局卡。

### 变更
- 模式命名梳理：原离线「剧本模式」更名为「经典模式」（会话横幅/主页提示/帮助页），
  与新的剧本模式（互动剧）区分；帮助页新增三种模式说明。

## [1.7.1] — 2026-10-04

默认剧本《蓝色守望之塔》按原版模组 PDF 全文校正：`Additional Ressources/English-TheTowerOfTheBlueWatch.pdf` 为模组本体。

### 修正（modules/blue-watch/module.json 按原版 PDF 重写）
- **译名对齐原版**：《蓝望塔》→《蓝色守望之塔》；影林→沙滕瓦尔德（Schattenwald）、
  夏叶旅店→萨默布拉特客栈、蓝望军→蓝色守望；戈托/博托为**半身人兄弟**
  （原误作人类）、新增矮人厨师罗加尔·艾森普法内；**灰胡子（格劳巴特）实为
  戈托的骡子**（原误作老猎人 NPC）。
- **结构还原原版**：第一幕修车检定（运动 DC10 / 巧手 DC5）→ 风铃伏击
  （4 只+可增援 2 只；DC15 生存重燃提灯即驱散；每人 100 XP）→ 客栈
  （抵达即升 2 级、盛宴、蓝色守望历史朗读、长休、赠礼：符文提灯/2 治疗药水/
  每人半身人奶酪）→ **宿舍为非战斗遭遇**（感知排名分配：锈蚀装备/2d20 金币/
  上锁活板门/瞥见『那造物』）→ **队长室开战**（1 队长+4 骷髅，可议价安息
  DC15/DC12；战利品 71 金币+钥匙+日记+孔雀石+守望者之刃/之盾）→ 地窖 Boss
  **那造物单独出战**（原多配的 2 骷髅移除；1238 金币+银铜币宝石全表+魔镜+
  +2 长矛+2 高级治疗药水）。`expected_end_level` 3→2（原版为 1→2 级单回团）。
- **属性块对齐原版**：风铃（心灵抗性/魅惑恐慌免疫/强光敏感）、蓝色守望者骷髅
  （锈蚀长剑）、蓝色守望者队长（**盾击无伤害**，DC14 力量豁免否则倒地——
  引擎新增 `dmg "0"` 无伤害攻击路径）、那造物（动作仅嗜噬面孔 2d10+4；
  **撕裂之爪实为传奇动作**，熄光/熄灭所有光/撕裂之爪写入 encounter_note）。
- **队长日记**（1387-05-12/05-16 两则）与蓝色守望历史逐字朗读进场景文本。

### 引擎配套（最小改动）
- `data.py`：`MONSTER_WEAPONS` 新增命名天生武器（利爪/锈蚀长剑/盾击/
  嗜噬面孔/撕裂之爪）；`ITEMS` 新增 `watchers_shield`（拾取即装备 AC+2、
  感知检定优势）、`magic_spear`（+2）、`halfling_cheese`（1d10 治疗）、
  `goto_lantern`、`magic_mirror`；守望者之刃名称随原版校正。
- `tools.py`：loot 发放支持 `shield: true` 拾取即装备（AC+2）。
- `character.py`：`mech.check_adv` 技能检定优势键（守望者之盾的感知优势）。
- `loader.py`：经验预算警告阈值随 `expected_end_level` 缩放（2 级团 300，
  3 级及以上维持 900）——1→2 级单回团不再误报"预算偏少"。

## [1.7.0] — 2026-10-03

战术地图 + DM 独立 Agent：世界有了空间。设计文档：`docs/battlemap-design.md`。

### 新增
- **战术网格战斗**（`rules/combat.py` + `rules/data.py`，1 格 = 5 尺）：
  `BattleGrid`（Chebyshev 距离 / Bresenham 视线 / 8 向 BFS / AOE 形状）；
  `Encounter` 增 `positions/speed_left`——移动逐格扣移速（倒地爬行双倍、
  起身耗半速、dash 动作翻倍、移速每轮重置），离开敌触及即借机攻击
  （5e RAW：命中不打断移动）；远程按武器射程分档（短程/长程劣势/超长端
  拒绝）＋视线阻挡；AOE 法术补 shape/shape_ft 元数据（锥/立方/球），
  覆盖格内全体参战（友军照打，塑法/谨咒免伤照常）。怪物与脚本队友
  启发式走位（逼近/后撤），全程确定性。
- **地图数据层**（`game/state.py` + `campaign/loader.py`）：场景可选
  `map` 声明（底图/网格/障碍/落点/初始棋子/合成图 crop）、遭遇可选
  `spawns` 布点、模组级 `tokens` 图标库与 `dm` 卡块；校验器同步扩展
  （资产路径安全/坐标界内/crop 越界/spawns 引用）。`GameState.map`
  随存档持久化（旧档无此键 → None）。
- **Web 地图面板**：第三列可折叠面板——底图（含合成图分幅裁剪）、
  可开关网格线、棋子三级回退（模组 Token PNG → AvatarFX 过程头像 →
  彩色圆点）、位置变化 CSS 平滑滑动；SSE 新事件 `map`（全量快照
  latest-wins，历史重放零成本重建）；`/api/asset/<模组>/<路径>` 资产路由
  （路径净化 + 扩展名白名单）；玩家回合**点图落子**（`ask_map_move`
  阻塞应答 + `/api/command` 探索期移动意图转 DM）；终端 `/地图` 文字态势。
- **DM 独立 Agent 升级**（aipc-design §P1 落地）：`make_dm_agent` 上
  SillyTavern 组装线（模组顶层 `dm` 块：persona/voice/system_prompt/
  depth_prompt/book 可扩展，蓝望塔注入哥特恐怖演出基调）；每轮世界书
  "加餐"（engine WI + 模组书按本轮文本扫描）；`harness.loop.run` 新增
  `post_build` 钩子，DM depth_prompt 与 @D 世界书条目真按深度注入；
  DM 新工具 `map_update`（place/move/remove/label，探索/社交期空间权威，
  战斗期归引擎）。
- **参战者位置感知**：同伴/AIPC 战斗上下文注入 ASCII 战况图 + 剩余移速；
  NPC 子智能体上下文附地图态势（只感知不改动）；玩家战斗菜单新增
  "移动（战术走位）/冲刺"；`combat_action` 工具新增 `move/dash`。
- **blue-watch 全场景配图**：原版地图 JPG（土路 20×15 / 旅店大堂与塔一层、
  宿舍、地窖分幅裁剪）+ 9 枚棋子 Token PNG 入 `modules/blue-watch/assets/`；
  三场遭遇配置 spawns；五场景障碍/落点/图标齐备。

### 变更
- **双模式铁律**：场景无 `map` 声明 ⇔ 战斗回退剧场模式，1.6.0 行为与
  304 项既有测试逐字节保留；同模组可逐场景混合。
- `enter_scene` 按场景声明换图布子；`launch_encounter` 布怪推初始阵型；
  战斗中每回合把 `Encounter.positions` 写回地图棋子（击倒即时撤子），
  战后怪物棋子撤下、队伍位置写回。
- 网格模式下脚本 AI 的敌方目标改为"最近者优先"（剧场模式保持最弱者优先）。

## [1.6.0] — 2026-10-03

SillyTavern 架构推广：非玩家 PC = 独立 Agent，NPC = DM 控制的子智能体；
默认剧本《蓝望塔》。设计文档：`docs/companion-npc-agents.md`。

### 新增
- **NPC 子智能体**（`ctrcg/game/npcagent.py`，仅 AI 模式）：DM 新工具
  `npc_dialogue(npc, instruction)` 把话头交给该 NPC 的子智能体——它依据
  人设、目标（goals）与对局记忆即兴说 1~2 句，台词渲染到桌面（同伴听得见）、
  记入 `npc:<id>` 持久会话并回到 DM 的工具结果；DM 随时可用 `speak_as_npc`
  覆盖，导演权始终在 DM。子代理只有 say 一件工具（无世界面）；LLM 不可达/
  未知 NPC/剧本模式时优雅降级（返回 ok=False，绝不阻塞对局）。
- **NPC 持久记忆**：每个 NPC 一份 harness 会话（`GameSession.npc_session`），
  "NPC 会记仇、也会改主意"有了记忆载体。
- **模组 NPC 扩展字段**（可选）：`persona/voice/goals/secret/mes_example/
  first_mes/depth_prompt/talkativeness/book`——最简 name/race/desc 形状
  行为不变；NPC 卡由 `st.card.card_from_npc` 构建。
- **同伴接入 ST 组装线**（非玩家 PC = 独立 Agent）：`make_pc_agent` 改经
  `st.assemble_system` 组装（与 AI PC 同一条通路）——模组可为同伴提供
  `mes_example / first_mes / depth_prompt / system_prompt / talkativeness /
  book`（V2 角色书）；卡文本中的 `{{user}}` 宏渲染为玩家角色名
  （`user` 参数，ST 用户 persona 语义）。
- **同伴世界书通路**：桌边社交回合注入 `_wi_context`——引擎世界书与该同伴
  卡上角色书合并扫描（场景/旗标/被点名的 NPC 自动激活），无激活内容不注入。
- **默认剧本《蓝望塔》**（The Tower of the Blue Watch，
  `modules/blue-watch/`）：**改编自 InternalRockStudio 的原版冒险素材**
  （`Additional Ressources/`：指挥官信件、怪物数据、四张地图、物品卡）。
  三幕五场景（影林土路 → 夏叶旅店 → 蓝望塔），3 遭遇 + 4 种原版自定义
  怪物（风铃妖/蓝望骷髅卫兵/蓝望队长/『那造物』CR4，CR-XP 与 SRD 一致），
  校验零警告；蓝望之刃（+1 魔法长剑）按物品卡加入引擎物品表。CLI 与 Web
  的默认模组 id 均切换为 `blue-watch`；默认剧本缺失时回退代码内置剧本，
  `blue-watch` 加入 `BUILTIN_IDS`（embergate 一并补齐内置保护）。
- SillyTavern 上游源码浅克隆至 `.reference/SillyTavern`（gitignored），
  作为机制参照（`docs/companion-npc-agents.md` 附对照表）。

### 变更
- `harness.ToolTimeoutGuard` 新增 `LONG_TOOLS` 类别（`npc_dialogue`，180s）：
  子智能体工具内部要跑几次模型调用，10s 常规超时不够，但仍须有限。
- `make_pc_agent` / `make_npc_agent` 新增 `user` 关键字参数（玩家名，供
  `{{user}}` 宏）；既有调用不传时行为不变。
- 1→3 级入门节奏参考从 starfall 调整为 blue-watch/starfall
  （MODULE_SPEC §0）。

## [1.5.0] — 2026-09-20

桌边社交层：智能体更自主——抢话、反应、辩论。

### 新增
- **桌边社交层**（`ctrcg/game/social.py`，仅 AI 模式）：探索期的玩家输入
  不再是"玩家 → DM"两点一线，而是一整个社交节拍——
  ① **抢话窗口**：玩家话音未落，同伴可能抢在 DM 前插话（终端显示
  `（抢话）`标记，网页版显示"抢话"徽标）；
  ② **DM 回应**：同伴的插话摘要进入 DM 上下文（`【桌上插话】`），
  NPC 会"听见"并可能回应、反驳或被说服；
  ③ **自由反应**：DM 说完后每名存活同伴经各自 harness 会话独立决策
  开口（say，可带立场 agree/object/question）或沉默（hold），
  每波至多 2 人出口、抢话者先开口；
  ④ **辩论轮**：出现反对立场且桌上有对立面时，进入至多 2 轮辩论——
  各方轮流申辩，hold / agree 即退出，直到只剩一种声音；
  ⑤ **DM 即席回应**：同伴们交谈过后 DM 以桌面主持身份回应一次
  （NPC 接话/裁定），辩论收场后另留一个"最后一句话"名额。
- **战斗喊话**：PC Agent 的战斗回合允许先用 `say` 喊一句简短战吼/
  提醒/嘲讽，再执行 `combat_action`。
- DM 与 PC 的系统提示词加入"桌边社会性"段落：NPC 不是提词器
  （会记仇、会改主意、可被 skill_check 裁定的说服），同伴是活人
  （可以打断、抬杠、反对玩家计划，但大部分时候在听）。

### 变更
- `harness.loop.DefaultAgentLoop.run()` 新增按次注入的 `guard_dup`
  参数（并行社交运行各自独立计数，不改默认行为）。
- `GameSession.dm_turn()` 支持 `lead`/`input_label` 参数（桌面回应轮）
  并返回最终叙述；新增 `pc_session()` 统一同伴会话的获取。
- UI：`companion_line()` 新增 `interrupt` 可选参数（终端前缀、
  网页版徽标）；网页同伴气泡样式新增 `.sp-name .interrupt`。
- 版本号升至 1.5.0（`ctrcg.__version__`，HTTP 服务标识 `CTRCG/1.5`）。

## [1.4.0] — 2026-09-20

商店与金币消费。

### 新增
- **商店系统**：场景新增 `"shop": true` 字段——开放"商队货摊"菜单：
  治疗药水 50 金、上乘治疗药水 150 金、医疗包 100 金（一整包 10 次用次）、
  火把 5 金；金币不足会被掌柜摇头拒绝。四部内置剧本的城镇/酒馆/驿站场景
  均已铺设（松溪镇口与醉獾酒馆、雾门客栈大厅、盐雾镇码头、长鞍镇驿站）；
  AI 模式下可用 `/购买` 指令（仅限商店场景）。
  此前金币只进不出（通关可攒 600+ 金却无处可花）。

### 变更
- 版本号升至 1.4.0（`ctrcg.__version__`，HTTP 服务标识 `CTRCG/1.4`）。

## [1.3.0] — 2026-09-20

魔法奖励真正落地与经验升级补全。

### 修复
- **星炉长剑(+1)是死物品**：`ITEMS` 的 `magic` 字段此前无任何代码消费，
  且《星坠矿脉的阴影》的战利品表里根本没有它——结局文案承诺的奖励从未发放。
  现在：①魔法武器拾取即同调（`give_loot` 时自动应用攻击/伤害加值并提示
  "已同调生效"，重复拾取不叠加）；②星炉决战战利品加入星炉长剑(+1)
  （内置数据与 modules/starfall/module.json 同步修正，数据表补齐
  damage_bonus 声明）。
- **经验升级跳过职业特性选择**：只有场景 `level_to` 升级会询问特性，
  经验值（award_xp 工具/里程碑奖励）跨过升级线时 ASI/专长/子职业被静默
  跳过。现在 `award_xp` 后立即结算全队待选（玩家交互、同伴默认），
  与开局结算共用 `resolve_pending_choices()`。

### 变更
- 状态板资源行显示已习得专长（如"专长：巨武器大师"）。
- 版本号升至 1.3.0（`ctrcg.__version__`，HTTP 服务标识 `CTRCG/1.3`）。

## [1.2.0] — 2026-09-19

4 级专长与探索期药水。

### 新增
- **专长系统（PHB 2014）**：4 级升级菜单扩展为"属性提升或专长"——
  - **巨武器大师**：重型近战武器 −5 命中/+10 伤害（攻击前选择）；
    重击或击倒目标后以附赠动作追加一次近战攻击；
  - **神射手**：远程武器 −5 命中/+10 伤害（本引擎无掩体/远距分档规则）；
  - **战争施法者**：维持专注的体质豁免优势；
  - **幸运**：3 次/长休，d20 出 1~5 自动重骰并以更优者结算
    （攻击/豁免/检定均适用——含死亡豁免）。
  玩家在游戏内交互选择；同伴按职业取推荐默认（施法者→战争施法者、
  游侠→神射手、其余→幸运）。"幸运"用次计入状态板资源行，长休重置。
- **探索期喝药**：剧本模式探索菜单新增"喝药水（回复生命）"（仍由引擎代掷、
  自动用背包中药效最强者）；AI 模式新增 `/喝药` 指令。

### 变更
- 版本号升至 1.2.0（`ctrcg.__version__`，HTTP 服务标识 `CTRCG/1.2`）。

## [1.1.0] — 2026-09-19

第四部模组、4 级内容与一批规则修复。

### 新增
- **内置模组《余烬之门》**（`embergate`）：3→5 级终章战役。星坠矿脉事件后，
  灰烬之主残党携星炉裂片"灰烬之种"东逃老鸮井（Old Owl Well），与地狱使者
  立约重开奈瑟瑞尔传送门。三幕九景八战，启用图鉴中此前未用的
  豺狼人/枭熊/尸妖/红帽子/相位蜘蛛/米诺陶/钻地魔虫/坎比翁，并新增自定义
  怪物"余烬祭司"；自带新三人组（奉献圣武士/森林侏儒德鲁伊/龙脉提夫林术士），
  覆盖此前队友 AI 未实践过的圣疗/神佑斩、荒野变形、超魔法术等机制。
- **4 级属性提升（ASI，PHB RAW）**：升到 4 级出现属性提升选择——单项 +2 或
  两项各 +1（上限 20）。玩家在游戏内交互选择；同伴按职业主属性默认提升。
  本内容范围（1-5 级）暂不引入专长。
- **物品系统通用化**：任意带 `heal` 表达式的物品可作为药水饮用/喂服
  （新增"上乘治疗药水"4d4+4）；新增"医疗包"（10 次用次，动作稳定倒地队友
  无需检定，PHB）与"余烬护符"（持有即获火焰抗性，`ITEMS` 新增 `mech` 机制键）。
  战斗菜单的喝药/喂药自动改用背包中药效最强的药水。
- **启发式队友 AI 扩展至全 12 职业**：新增武僧（疾风连击+震慑拳）、德鲁伊
  （野变熊形/狼形、缠绕术）、术士（火球/炽焰射线/魔法飞弹/护盾术）、邪术师
  （噬魂咒+魔能爆）、诗人（激励+恶性嘲讽）、圣武士（圣疗+神佑斩+远程拉扯）
  分支；倒地队友的救援（喂药/稳定/治疗真言拉起）优先级整体重排。

### 修复
- **死亡豁免死循环**：战斗后无人施救时，濒死者掷出自然 20 复活（1 HP）后
  死亡豁免循环永不终止（`death_save_turn` 恒返回空 dict）——整个对局卡死。
  循环条件现包含 `hp_cur <= 0`。
- **队友看不见倒地者**：同伴 AI 的救援决策此前取自 `active_party()`（只含
  存活者），导致倒地队友永远无人稳定/喂药、只能靠自己死亡豁免流血而亡。
  现改用完整队伍名单做救援判定，增益与激励仍只给存活者。
- **死者升级即复活**：里程碑升级与经验升级不再作用于已死亡成员
  （此前死者会被"生命全满"式复活）；长休对死者不再生效（"长休无法让逝者归来"）。
- **生命汲取不可恢复**：尸妖等 `drain` 攻击削减的生命上限此前永久丢失；
  现以 `hp_drain` 记账、长休结束即恢复（RAW），并夹紧当前 HP 至新上限、
  随存档序列化。
- **野变状态栏错位**：德鲁伊变身期间状态板显示"本体当前/兽形上限"（如
  24/9）；新增 `_hp_cur` 后状态板与 `brief()` 均按兽形 HP 显示。
- **模组起始等级被无视**：`party_level_start` 此前在全部三个入口（终端/demo/Web）
  都不生效——3 级开局的模组会以 1 级队伍开打。现按模组配置整队开局，且开局时
  会结算玩家未选的职业特性（此前仅升级场景询问）。
- demo 模式玩家角色同样按 `party_level_start` 创建。

### 变更
- 版本号升至 1.1.0（`ctrcg.__version__`，HTTP 服务标识 `CTRCG/1.1`）。
- 《余烬之门》加入内置模组清单与发行包。

## [1.0.0] — 2026-09-13

首个正式发行版。

### 新增
- **视听演出**：d20 掷骰全屏动画（优势/劣势双骰、天然 20 金色爆辉、天然 1 震裂）；
  Web Audio 实时合成音效（骰子/命中/暴击/施法/治疗/金币/升级/终局，零外部音频文件）；
  队伍角色按种族×职业生成程序化 SVG 头像；升级华彩与终幕结算卡
  （同伴结局、掷骰统计、大成功/大失败计数）。
- **新手引导**：首次进入对局弹出四步玩法说明（本地记忆，只出现一次）。
- **内置模组《雾门客栈的第一次试炼》**：专为新手准备的教学剧本——
  检定/潜行突袭/黑暗与光源/短休长休/里程碑升级全部边玩边教；三场渐进式战斗。
- **移动端适配**：≤860px 视口自动切换为纵向布局。
- **发行工程**：MIT 许可证（规则内容按 SRD 5.1 CC BY 4.0 声明）、
  `pyproject.toml`、`build_release.py` 一键打包发行 zip、
  macOS/Linux 启动器 `启动游戏.sh`。

### 修复
- 《盐雾灯塔的低语》此前被误归类为"导入"模组（且可从界面误删）——
  内置模组统一由 `BUILTIN_IDS` 管理并禁止删除。
- 战斗检定/豁免事件现在携带结构化骰子数据（d20 原值、骰池、优势），
  终端与 Web 界面的骰子行不再只显示文本串。
- **对局更替跟随**：服务端开新对局时，旧 SSE 连接按"对局代际"自动断开重连，
  页面自动清空旧编年史并跟随新对局（此前会卡在已结束的旧对局流上）；
  停留在开始页的页面收到新对局事件会自动进入对局视图。
- 剧本模式下非默认队友（如教学模组同伴）错拿默认三人组台词的问题——
  现按职业应景台词池散列取句，整队不再复读同一句。

### 变更
- 版本号升至 1.0.0（`ctrcg.__version__`，HTTP 服务标识 `CTRCG/1.0`）。

## [0.x] — 2026-09-12 / 09-13（开发期）

- 完整 D&D 5e 规则引擎：属性/技能/豁免、完整动作经济（动作+附赠+反应）、
  先攻、暴击、借机攻击、推撞/擒抱、法术位（满施法/半施法/魔契）与专注、
  状态效果、死亡豁免、力竭六级、短休/长休、经验与里程碑升级。
- 双预置剧本《星坠矿脉的阴影》《盐雾灯塔的低语》；AI 一键生成模组与 JSON 导入。
- Agent 循环（参考 deepseek-harness）：多供应商接入（DeepSeek/GLM/Kimi/MiniMax）、
  Token 记账、SSF 防护（端点私网解析禁用）。
- 网页版（本地 HTTP+SSE）与终端版；BG3 名单角色创建（4d6 掷骰）。
