# TRPG：赛博朋克 RED · 纯 AI 剧场

**一桌全 AI 的《赛博朋克 RED》跑团。** DM 主持世界，每个 PC 由独立 Agent 自主行动，
NPC 是 DM 的子智能体；你负责看，也可以切到手动模式自己下场。

舞台是 **2045 年（血染之时）的夜之城**，规则是 **《Cyberpunk RED》**——以免费入门的
**轻松模式（Easy Mode）** 口径为准。没有属性点分配，没有掷骰建卡：挑一张角色卡，
接下这单活，然后活着把钱拿到手。

`trpg 2.3.1` · Python 3.10+ · 运行时零第三方依赖 · 本地 Web UI · MIT

> Gitee：<https://gitee.com/shenzhenshiguanfminquxinhujiedao/cyberpunk-red-aitrpg>

![AI 剧场](docs/screenshots/cinema-stage.png)

---

## 这是什么

这不是“把角色卡接进聊天框”的套壳，而是一个完整的单人跑团引擎：

- **规则引擎负责一切数值**：属性、技能、检定、暴击、幸运、射程、护甲磨损、致命伤、
  生存检定、掩体、IP 成长，全部由 `trpg/rules/` 结算。LLM 只做决策与叙述，
  **不生成任何骰点**。
- **AI 剧场负责演出**：全屏的《极乐迪斯科》式界面——技能之声、案卷肖像、场景底片、
  检定判决叠层、战术板、环境音。
- **多 Agent 负责“人”**：DM 是根 Agent；主角与每名队友是独立 PC Agent；
  NPC 是 DM 按需拉起的子智能体。每个 PC 有自己的会话、人设、目标与记忆。
- **两种看法**：默认看一桌全 AI 自主跑团；也可以用手动 AI 模式自己输入行动，
  或玩不依赖 LLM 的互动剧本 / 经典离线模式。

一句话：**Rules as code，Agents as players。**

---

## 快速开始

要求：Python 3.10+，运行时**不需要安装任何第三方依赖**。

```bash
git clone https://gitee.com/shenzhenshiguanfminquxinhujiedao/cyberpunk-red-aitrpg.git
cd cyberpunk-red-aitrpg

python main.py --web
# 打开 http://127.0.0.1:8500
# --port 改端口；--no-open 不自动打开浏览器
```

Windows 也可以双击 `启动游戏.bat`；macOS/Linux 用 `启动游戏.sh`。

### 先接 LLM（AI 剧场必须）

AI 剧场是**纯 AI 模式**：没有 LLM 时 `/api/start {cinema:true}` 会直接返回 400，
不会退回本地导演，也不会用剧本假装 AI。接入方式：

1. 打开 Web UI → **设置 → API 配置**；
2. 选供应商，填 API Key；
3. 点「拉取模型列表」，选一个账号真实可用的模型；
4. 「测试连接」通过后，回首页进入 **AI 剧场**。

也可以用环境变量或 `.env`：

```bash
# 复制模板后填写；.env 已在 .gitignore 中，不会提交
cp .env.example .env

# .env 示例
DEEPSEEK_API_KEY=sk-...
TRPG_LLM_MODEL=deepseek-flash
TRPG_LLM_THINKING=off
```

密钥只存在内存或本地 `.env`，不会写进源码、示例或日志。

---

## 四种玩法

| 模式 | 需要 LLM | 谁在行动 | 适合 |
|---|---|---|---|
| **AI 剧场**（默认推荐） | 是 | 所有 PC 由独立 Agent 自主行动；DM 主持；NPC 由 DM 子智能体扮演 | 看一桌全 AI 把故事演完 |
| **手动 AI 模式** | 是 | 你输入行动/对白；DM 与队友是 Agent | 自己下场，但不想手算规则 |
| **剧本模式（互动剧）** | 否 | 你在节点图上做选择；引擎掷骰 | 不接 LLM 也能玩的分支故事 |
| **经典模式（离线）** | 否 | 模组数据驱动 DM；你操作 | 完全离线、规则演示与调试 |

> 剧本模式是独立入口，不是 AI 剧场的兜底。AI 剧场永远不降级成剧本。

---

## AI 剧场：一桌全 AI 的跑团

### Agent 关系

```text
GameSession（编排器，不是 Agent）
├─ PartyDirector（回合调度，不是 Agent）
│  ├─ PC Agent：主角（AIPCController 构建）
│  ├─ PC Agent：队友 A
│  └─ PC Agent：队友 B
│       ├─ 独立 Session：pc:<name>
│       ├─ 工具：say / explore_intent / explore_act / hold
│       └─ 自己决定说什么、做什么、是否开枪
│
├─ DM Agent（根）
│  ├─ Session：dm
│  ├─ 世界工具：narrate / skill_check / advance_scene / start_encounter / …
│  └─ npc_dialogue
│       └─ NPCDirector → NPC SubAgent
│            ├─ 只有 say
│            └─ Session：npc:<id>
│
└─ 没有 Human Player 节点
```

- **主角不再特殊**：主角和队友走同一条自主 PC 回合链。
- **PC 不能改世界**：PC 只能声明意图、说话、做自我行动；检定、换场、开战、NPC 回应
  都归 DM。
- **NPC 不是玩家**：NPC 只在 DM 调用 `npc_dialogue` 时被拉起，只带 `say` 工具，
  不能自主行动。
- **共享桌面**：PC 的台词进入 `table_log`，DM 消化后进入 `table_archive`；
  后面的 PC 能看见前面的人说过什么。
- **场景预算**：每个场景最多四轮 PC 自主行动；DM 仍不推进时，编排器才做确定性
  换场/补跑遭遇，防止模型把场景拖成无限对话。

### 演出

![AI 剧场选角](docs/screenshots/cinema-cast.png)

- **技能之声**：逻辑 / 共情 / 意志 / 反应 / 技术 / 街头 / 体格七个声部，根据场景、
  检定与 NPC 台词在脑内插话。由 `trpg/game/psyche.py` 本地生成，零 token，不参与结算。
- **案卷式舞台**：五名预设角色的官方角色画被处理成案卷照片；场景底片统一为低饱和、
  冷影暖高光、颗粒与雨幕。
- **检定判决**：每次技能检定弹出 SKILL CHECK 叠层：声部、d10、合计、DV、成功/失败
  与修正明细。
- **战术板**：遭遇战读取真实 `map` 事件，障碍/掩体/PC 与敌人 token 平滑走位；
  1 格 = 2 米，射程带与视线由引擎判定。
- **暂停/调速/低语**：暂停是真正的服务端闸门；支持 0.8× / 1× / 1.5× / 2.2×；
  底部低语会进入下一名 PC Agent 的上下文，不保证被采纳。
- **刷新恢复**：从快照恢复暂停、速度、当前场景、地图与技能之声。

### 手动 AI 模式

同一个 AI 引擎，但由你输入行动或对白：

```bash
python main.py --web
# 首页 → 新的冒险 / 手动 AI 模式
```

DM 和队友仍是 Agent；检定、战斗、存档、IP 成长与剧场共用同一套规则引擎。

---

## 规则引擎（轻松模式口径）

完整实现范围见 [MODULE_SPEC.md](MODULE_SPEC.md) 与代码；这里只列玩家最能感知的部分。

### 检定

- **十项属性 + 41 项技能**；检定 = `1d10 + 属性 + 技能 (+ 修正)`。
- 必须**高于** DV 才成功；平手时防守方赢。
- 难度值：日常 13 / 困难 15 / 专业 17 / 英雄 21 / 不可思议 24。
- **暴击成功**：d10 掷出 10，追加一颗 d10 **相加**。
- **暴击失败**：掷出 1，追加一颗 d10 **相减**。
- **幸运点**：等于幸运属性，每轮恢复满；掷骰前投入，每点 +1。

### 战斗

- 先攻 = 反应 + 1d10；每回合 = **1 个移动动作 + 1 个其他动作**。
- 移动距离 = 机动 × 2 米；网格战斗 1 格 = 2 米。
- **远程**：按射程带查 DV；反应 8+ 可用闪避对抗子弹；瞄准射击 -8。
- **白刃**：对抗闪避，**无视一半护甲**。
- **斗殴**：一次攻击动作打两下；可擒抱、锁喉、投掷。
- **护甲 SP 磨损**：挡下伤害后 -1，最低 0；可在场景内修复。
- **致命伤**：武器伤害骰出现两个及以上 6 → 额外 5 点无视护甲伤害 + 2d6 查表。
- **伤势**：轻伤 / 重伤（全部检定 -2）/ 濒死（-4、移动 -6、每回合生存检定）。
- **稳定 / 急救 / 治疗 / 休整**：按 RED 的时间与难度值结算。
- **掩体**：按材质有耐久，目标格有耐久大于 0 的掩体即视为被掩护。

### 成长与经济

- 货币是**欧元（eb）**；武器 10 种、护甲 6 种、装备 20 种、义体 9 件。
- **人性**上限 = 共情 × 10；义体按标价扣人性，人性归零即赛博精神病。
- **IP**：技能 `20 × 新等级`（×2 技能翻倍），职业能力 `60 × 新等级`；属性不可提升。
- IP 按人发放，不队伍平摊；击败敌人另按威胁结算。

### 明确未实现

轻松模式未收录的子系统**一律不实现**：自动武器/压制射击、网络空间战斗、载具战斗、
武术、药物成瘾、完整义体精神诊断。模组 JSON 也无法声明它们。

---

## 界面预览

| 主页 | 对局仪表盘 |
|---|---|
| ![主页](docs/screenshots/home-city.png) | ![对局](docs/screenshots/game-board.png) |

| AI 剧场 · 演出 | AI 剧场 · 检定 |
|---|---|
| ![演出](docs/screenshots/cinema-stage.png) | ![检定](docs/screenshots/cinema-check.png) |

| AI 剧场 · 选角 | 移动端 |
|---|---|
| ![选角](docs/screenshots/cinema-cast.png) | ![移动端](docs/screenshots/cinema-mobile.png) |

![数据看板](docs/screenshots/stats-heatmap.png)

> 截图来自实际运行画面。战术地图与热力图在截图里使用演示数据；真实地图由模组
> `map` 声明生成，用量从第一次 Agent 调用开始累积。

---

## LLM 接入

### 支持供应商

统一走 OpenAI 兼容协议；默认自动选择 Responses API 或 Chat Completions：

| 供应商 | API Base | 预设模型 | 思考模式 |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-flash`、`deepseek-v4-pro` | V4 起默认开启，本应用默认关闭 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.3`、`glm-5.3-flash`、`glm-5.2` | GLM-5.3 强制思考 |
| Kimi | `https://api.moonshot.cn/v1` / `api.moonshot.ai/v1` | `kimi-k3`、`kimi-k2.7-code`、`kimi-k2.6` | — |
| MiniMax | `https://api.minimaxi.com/v1` / `api.minimax.io/v1` | `MiniMax-M2.1`、`MiniMax-M2` | — |
| 自定义 | 任意 OpenAI 兼容网关 | 手动填写 / 拉取 | — |

设置页的「拉取模型列表」会请求 `GET {base}/models`，用你的账号列出真实可用模型；
预设只是兜底。模型名迭代很快，以列表为准。

### 协议与思考链

- **auto**：供应商支持 Responses 时走 `POST /responses`，否则走 `/chat/completions`；
  Responses 404 自动降级。
- **Responses reasoning item 原样回传**：保存 `id / summary / content /
  encrypted_content`，不只保存文本；避免
  `reasoning_text must be passed back` 的 400。
- **chat 通道**只发送 `reasoning_content`，剥离 Responses 字段。
- 若 Responses 仍返回 reasoning 回传 400，自动切到 chat/completions 重发这一轮。
- 思考模式打开时，输出预算至少抬到 **1024**，避免思维链吃光正文。

相关代码：[responses.py](trpg/harness/responses.py)、[adapter.py](trpg/harness/adapter.py)、
[models.py](trpg/harness/models.py)。

### 网络与代理

LLM 请求**默认直连**，忽略 `HTTP_PROXY` / `HTTPS_PROXY`，避免把请求送进失效代理：

```bash
# 默认：直连
# TRPG_LLM_PROXY=direct

# 只有必须走代理时才设置
TRPG_LLM_PROXY=env
# 或
TRPG_LLM_PROXY=http://127.0.0.1:7890
```

本机模型服务（Ollama / LM Studio）默认被防 SSRF 规则挡住，需显式放行：

```bash
TRPG_LLM_ALLOW_PRIVATE=1
```

### 密钥安全

- API Key 只从环境变量、`.env` 或设置页内存读取；不写入源码、示例、日志。
- `.env` 已在 `.gitignore`；模板见 [.env.example](.env.example)。
- 服务重启后设置页填入的 key 会丢，需要重填；`.env` 不会。

---

## 命令行

```bash
python main.py --web                    # Web UI（默认）
python main.py --web --port 8600        # 换端口
python main.py --demo --seed 7          # 终端自动演示整部模组
python main.py --new                    # 终端新开一局（离线）
python main.py --story                  # 列出互动剧本
python main.py --story --story-id wyskakuj
python main.py --check-llm              # 验证 LLM 端点
python main.py --offline                # 强制经典模式
python main.py --load saves/game.json   # 读档
```

首次启动会自动读取项目根目录的 `.env`；已存在的系统环境变量优先。

---

## 项目结构

```text
trpg/
  rules/       # RED 引擎：dice / data / character / combat
  campaign/    # 模组体系：module_data / loader / cordis 注册表
  harness/     # Agent 核心：Session / ModelAdapter / ToolRegistry / loop / guards / usage
  game/        # 编排器、DM/PC Agent、工具绑定、存档
               #   aipc(主角 PC Agent) / party(全 AI PC 回合) / psyche(技能之声)
  st/          # SillyTavern 式提示词栈：角色卡 / 宏 / 世界书 / 深度注入
  ui/          # 终端 UI、角色创建、WebUI（SSE 事件流）
  web/         # 本地 HTTP + SSE 服务；前端 index.html / app.css / app.js
               #   cinema.py(纯 AI 剧场 UI) + static/cinema.* + static/cinema/ 美术

modules/       # 原子模组目录（内置 cpred-easy-mode；generated-*/imported-*）
tools/         # 资产生成脚本（剧场场景/肖像）
tests/         # 单元 + 集成测试（含真实 HTTP 驱动的整局）
docs/          # 设计文档与截图
main.py        # CLI 入口
HARNESS.md     # Agent 循环架构
MODULE_SPEC.md # 模组开发规范
```

---

## 测试

```bash
python -m pytest tests -q
# 当前：708 passed
```

覆盖：

- 骰子/检定/暴击/幸运/DV 边界；
- 角色数值、伤势、致命伤、稳定、休整、IP；
- 战斗：先攻、动作经济、射程带、白刃半甲、擒抱/锁喉/投掷、护甲磨损、掩体、生存检定；
- 模组校验、场景链、Cordis 安装/删除/热加载；
- Harness：工具校验、循环、guards、压缩、SSE 重放、TokenMeter、余额；
- Web：阻塞应答、SSE、设置、用量、真实 HTTP 整局；
- AI 剧场：无 LLM 拒绝开局、`strict_ai` 拒绝本地导演、暂停/重置/低语；
- 全 AI 跑团：所有 PC 无 Human `free_input` 自主跑完整局、共享桌面、主角战斗走 Agent；
- Responses reasoning item 往返与 400→chat 降级。

---

## 设计文档

- [HARNESS.md](HARNESS.md)：Agent 循环、工具边界、全 AI 跑团层。
- [docs/cinema-design.md](docs/cinema-design.md)：剧场 UI、`PartyDirector`、资产与边界。
- [docs/companion-npc-agents.md](docs/companion-npc-agents.md)：非玩家 PC 与 NPC 子智能体。
- [docs/aipc-design.md](docs/aipc-design.md)：主角 PC Agent 的提示词与上下文。
- [docs/battlemap-design.md](docs/battlemap-design.md)：网格战斗与战术板。
- [docs/sillytavern-architecture.md](docs/sillytavern-architecture.md)：ST 式提示词栈。
- [MODULE_SPEC.md](MODULE_SPEC.md)：模组 JSON 规范。

---

## 发行与许可

- 打包：`python build_release.py`（源码 + 启动器 + 内置模组，解压即玩）。
- **源代码**：[MIT](LICENSE)，版权人 **EudoraArcher**。
- **第三方内容**（MIT 不覆盖）：规则文本改编自 R. Talsorian Games 免费发布的
  *Cyberpunk RED Easy Mode* 与 *Cyberpunk RED* 核心规则书，版权归
  R. Talsorian Games 所有；Cyberpunk® 是 CD Projekt Red S.A. 的注册商标。
- **AI 剧场美术**：角色画与部分场景插画裁自免费 Easy Mode 手册，经裁切/调色/
  半调处理；其余场景底片、纸纹、颗粒、雨幕与音效为程序化/CSS/SVG 生成。
  本项目不主张官方美术所有权。
- 本项目为非官方同人作品，与 R. Talsorian Games / CD Projekt Red 无隶属关系；
  规则文本与官方美术仅用于非商业的同人展示。
- 完整的第三方声明与出处见 [NOTICE.md](NOTICE.md)；剧场美术细节见
  [trpg/web/static/cinema/CREDITS.md](trpg/web/static/cinema/CREDITS.md)。

---

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。当前版本 **2.3.1**：
纯 AI 剧场 + 全 AI 跑团，修复 Responses 思考模式 reasoning 回传 400。
