# SillyTavern 提示词架构规格 —— 实现"AI 驱动的玩家角色（AI PC）"的参考

> **文档目的**：本文档完整描述 SillyTavern（下称 ST）的角色卡、提示词组装、用户 persona、
> 世界书、宏、按深度注入、群聊轮替与 swipe/重生成/继续等机制的精确语义，
> 并在末尾给出 **机制 → ctrcg 现状对照表**。实现者只读这一份文档即可照做。
>
> **来源标注约定**：
> - 〔doc〕= docs.sillytavern.app 官方文档（release 分支文档站，抓取于 2026-10-02）；
> - 〔src〕= SillyTavern 源码（GitHub `SillyTavern/SillyTavern` release 分支，抓取于 2026-10-02；
>   个别标注为 `1.11.0` tag）；
> - 〔spec〕= 角色卡规范（V2：malfoyslastname/character-card-spec-v2；V3：kwaroran/character-card-spec-v3）。
> - 完整 URL 清单见 [§13](#13-来源清单)。
>
> **可信度说明（如实声明）**：以下两处通过网络抓取大文件被截断、未能从当前 release 源码逐行核实，
> 文中相应结论改以文档或 1.11.0 源码为出处：
> 1. 当前 release `world-info.js` 的 `checkWorldInfo` 预算比较器（文件过大被截断）——
>    预算语义以〔doc worldinfo〕为准，预算机制以 1.11.0〔src〕为准；
> 2. 发行包默认设置文件中的完整 story string（`default/settings.json`、`default/context.json`
>    均 404，未能取得）——`power-user.js` 内 fallback 常量已核实，完整顺序以〔doc context-template〕为准。

---

## 1. 总体模型：两条提示词组装路径

ST 对两类 API 使用两套组装机制〔doc context-template〕：

| 路径 | 适用 API | 核心机制 | 配置位置 |
|---|---|---|---|
| **Text Completion** | 本地/文本补全后端 | **Story String**（Handlebars 模板）把角色卡字段渲染成一段前置文本，后接示例对话与聊天历史 | Advanced Formatting → Context Template |
| **Chat Completion** | OpenAI 兼容 `/chat/completions` 等 | **Prompt Manager**：一组有序"提示词条目"（marker + 内容），逐条映射为 messages 数组 | AI Response Configuration → Prompt Manager |

**本项目（ctrcg）走 OpenAI 兼容协议**（`ctrcg/harness/adapter.py:83` 的 `OpenAICompatibleAdapter`
直连 `/chat/completions`），因此实现 AI PC 时应以 **Chat Completion 路径（Prompt Manager 模型）为主线**，
Text Completion 的 story string 作为字段编排的概念对照（见 §3.3）。

两条路径共享同一批"素材"：角色卡字段、用户 persona、世界书、作者注释、聊天历史；
差别只在"拼装顺序由谁决定"——story string 模板 vs 可拖拽排序的 prompt 条目列表。

---

## 2. 角色卡 V2 / V3 全部字段

### 2.1 三代卡结构

| 代 | 结构 | 标识 |
|---|---|---|
| V1 | 顶层平铺字段 `name/description/personality/scenario/first_mes/mes_example`，PNG 内嵌 `chara` chunk | 无 spec 字段 |
| V2 | `{"spec":"chara_card_v2","spec_version":"2.0","data":{...V1字段+V2新增}}`；`spec`/`spec_version` **MUST** 为上述字面值〔spec V2〕 | PNG `chara` chunk |
| V3 | `{"spec":"chara_card_v3","spec_version":"3.0","data":{...V2超集+V3新增}}`；PNG 用 `ccv3` tEXt chunk，**与 `chara` 并存时优先 `ccv3`**〔spec V3〕；`.charx` = zip，根下 `card.json`〔spec V3〕 | PNG `ccv3` chunk |

V3 的 `data` 是 V2 的超集；应用 SHOULD 忽略未知字段但不得拒绝导入，并须保存未知字段以便无损再导出
〔spec V3〕。ST 导入侧把 V2 字段提升到顶层存储（`readFromV2`），导出时经 `charaFormatData` 回填，
`talkativeness` 缺省回填 `0.5`、`fav` 缺省 `false`〔src characters.js, fieldMappings 与 defaultValue 分支〕。

### 2.2 `data` 字段表（V2 定义、V3 沿用；★=V3 新增）

| 字段 | 类型/必填 | 进提示词？ | 语义与注入位置 |
|---|---|---|---|
| `name` | string，必填 | **是，常驻** | 角色名是唯一必填字段，随每次生成请求发送〔doc characterdesign〕；`{{char}}` 宏的取值。V3 中若设 `nickname`，`{{char}}`/`<char>`/`<bot>` 改用 nickname〔spec V3〕 |
| `description` | string，必填 | **是，常驻** | 角色设定/世界/外观/身世，"always included in the prompt"；任意长度任意格式〔doc characterdesign〕。渲染进 story string 的 `{{description}}` / PromptManager 的 `charDescription` marker |
| `personality` | string，必填 | **是，常驻** | 人格摘要，"A brief summary of the character's personality"〔doc characterdesign〕。默认模板渲染为 `{{char}}'s personality: {{personality}}`〔src power-user.js defaultStoryString；doc prompt-manager personality_format〕 |
| `scenario` | string，必填 | **是，常驻** | 对话所处的情境与上下文〔doc characterdesign〕。默认渲染为 `Scenario: {{scenario}}`〔src power-user.js〕 |
| `first_mes` | string，必填 | **仅一次** | 开场白，只在新会话开头出现一次（不常驻）；它定义回答的风格与长度基线——模型"更可能从第一条消息学到风格和长度约束"〔doc characterdesign〕。支持 Markdown/HTML |
| `mes_example` | string，必填 | **可挤出** | 对话示例；每个示例块必须以 `<START>` 标记开头（标记本身不进提示词，被 "Example Separator"（文本补全，默认 `***`〔src power-user.js〕）或 "New Example Chat"（`[Example Chat]`〔src openai.js〕）替换）。块内用 `{{char}}:` / `{{user}}:` 前缀。"only inserted if there is free space in the context"、上下文吃紧时**逐块**被挤出；可选强制保留（pin examples）〔doc characterdesign〕 |
| `system_prompt` | string，选填 | 是（覆盖主提示词） | **Main Prompt**：前端默认以它替换自己的主提示词设置（ST 中随 "Prefer Char. Prompt" 开关生效）；空字符串=回退用户设置；支持 `{{original}}` 占位符，展开为"前端原本会用到的默认提示词"〔spec V2；doc characterdesign〕。对应 PromptManager 的 `main` 条目 |
| `post_history_instructions` | string，选填 | 是（覆盖历史后指令） | **Post-History Instructions**：默认替换前端 "ujb/jailbreak" 设置（"Prefer Char. Instructions" 开关）；空串回退；支持 `{{original}}`〔spec V2〕。位于聊天历史**之后**发送（列表最底端，"sent last"）〔doc prompt-manager〕 |
| `alternate_greetings` | string[]，选填 | **否（UI 层）** | 首条消息的额外"swipes"：前端 **MUST** 为首条消息提供 swipe，每个数组元素是一张备选开场白〔spec V2〕；群聊中随机选用其中之一〔doc characterdesign〕。不进提示词组装 |
| `character_book` | object，选填 | 是（经世界书管线） | 角色自带世界书：默认启用，且必须与用户世界书**叠加**，角色书对同键内容取完全优先〔spec V2〕。字段见 §2.3；ST 导入时由 `data.world` 经 `convertWorldInfoToCharacterBook` 生成，失败仅告警不致命〔src characters.js〕 |
| `creator_notes` | string，选填 | **否** | "MUST NOT be used inside prompts"，仅展示〔spec V2〕。V3 中若存在 `creator_notes_multilingual` 则本字段被忽略、视为 `en` 条目〔spec V3〕 |
| `tags` | string[]，选填 | **否** | 不做 prompt 工程，仅排序/过滤用（大小写不敏感理想）〔spec V2〕 |
| `creator` | string，选填 | **否** | 仅展示〔spec V2〕 |
| `character_version` | string，选填 | **否** | 仅展示与排序〔spec V2〕 |
| `extensions` | object，**必填（默认 `{}`）** | —— | 任意键值对；编辑器**不得销毁**未知键；应用应加命名空间前缀（如 `"agnai/voice"`）〔spec V2〕。ST 把 `talkativeness`、`fav`、`depth_prompt`（角色注释）都存于 `data.extensions` 下〔src characters.js〕 |
| ★`assets` | `{type,uri,name,ext}[]`，选填 | 否 | `type` 预定义 `icon/background/user_icon/emotion`（自定义须 `x_` 前缀）；`uri` 支持 HTTP(S)、base64 data URL、`embeded://path`（规范原文即拼作 embeded）、`ccdefault:`；`name:"main"` 为主资产；未定义时默认 `[{"type":"icon","uri":"ccdefault:","name":"main","ext":"png"}]`〔spec V3〕 |
| ★`nickname` | string，选填 | 间接 | 存在时 `{{char}}`/`<char>`/`<bot>` 用 nickname 替代 name〔spec V3〕 |
| ★`creator_notes_multilingual` | object，选填 | 否 | 键为 ISO 639-1 语言码（无地区后缀）〔spec V3〕 |
| ★`source` | string[]，选填 | 否 | 来源 ID/URL，理想上只追加〔spec V3〕 |
| ★`group_only_greetings` | string[]，**必填（可为空数组）** | 否 | 仅群聊使用的开场白〔spec V3〕。注意：ST 的 V2 导入器目前不处理该字段〔src characters.js〕 |
| ★`creation_date` / `modification_date` | number，选填 | 否 | Unix 秒（UTC），`0` 表示未知〔spec V3〕 |

### 2.3 `character_book` 与 entry 字段表

书级字段〔spec V2/V3〕：

| 字段 | 必填 | 语义 |
|---|---|---|
| `name` / `description` | 否（V3 中 description 必填性以接口为准） | 仅元信息 |
| `scan_depth` | 否 | 对应 Agnai "Memory: Chat History Depth"——覆盖全局扫描深度 |
| `token_budget` | 否 | 对应 Agnai "Memory: Context Limit"——覆盖全局 token 预算 |
| `recursive_scanning` | 否 | "whether entry content can trigger other entries"——覆盖全局递归开关 |
| `extensions` | **必填** | 同卡级 extensions 规则 |
| `entries` | **必填（V3 可为空数组）** | 条目数组 |

entry 字段〔spec V2；V3 变更以加粗注明〕：

| 字段 | 必填 | 语义 |
|---|---|---|
| `keys` | **必填** | 主触发关键词（数组） |
| `secondary_keys` | 否 | 副键（过滤器），"ignored if selective == false" |
| `content` | **必填** | 被注入的文本（键/标题/其他字段都不会进提示词，条目须自含语义）〔doc worldinfo〕 |
| `extensions` | **必填** | ST 把 probability、depth（默认 4）、selectiveLogic、scan_depth、case_sensitive、vectorized、sticky、cooldown、triggers 等扩展字段都放在这里〔src characters.js convertWorldInfoToCharacterBook〕 |
| `enabled` | **必填** | false = "IN ANY CASE" 永不匹配〔spec V3〕 |
| `insertion_order` | **必填** | "lower 'insertion order' = inserted higher"（数值越小越靠前）〔spec V2〕。注意 ST 世界书 UI 的 `order` 语义相反：**数值越大越靠近上下文末尾、影响越强**（"Order 100 appears before Order 250"）〔doc worldinfo〕——V2 卡与 ST 内部字段方向相反，实现时须固定一个方向并注明 |
| `case_sensitive` | 否 | 大小写敏感开关 |
| `constant` | 否（V2）/**V3 要求必须实现** | true = 预算内总是插入，无需关键词〔spec V2/V3〕 |
| `selective` | 否 | true = 需 `keys` 与 `secondary_keys` **各**命中一个才触发〔spec V2〕 |
| `position` | 否 | `'before_char' \| 'after_char'`〔spec V2/V3〕。ST 双向转换：数字 `0 → 'before_char'`，其余 → `'after_char'`〔src characters.js〕。ST 内部另有 8 档 position（见 §5.3） |
| `priority` | 否 | "if token budget reached, lower priority value = discarded first"（预算吃紧时**优先级数值小者先被丢弃**；ST 原生世界书无此字段）〔spec V2〕 |
| `name`/`id`/`comment` | 否 | 均不参与 prompt 工程〔spec V2〕 |
| ★`use_regex` | **必填（V3）** | 布尔，V3 新增；ST 侧注释 "ST keys are always regex"（ST 的键一律按正则解析）〔spec V3；src characters.js〕 |

V3 的深度注入不经 `position` 枚举，而经 **`@@` 装饰器**写在 content 里：`@@depth N`（距最新 N 条消息处插入）、
`@@role`（**MUST** 为 `assistant|system|user`）、`@@position`（值 `"after_desc"/"before_desc"/personality/scenario`）、
另有 `@@scan_depth`、`@@activate_only_after`、`@@activate_only_every`、`@@keep_activate_after_match`、
`@@dont_activate_after_match`、`@@ignore_on_max_context`、`@@additional_keys`、`@@exclude_keys`、`@@is_greeting`、
`@@activate`、`@@dont_activate`、`@@disable_ui_prompt`（`post_history_instructions` 或 `system_prompt`）等；
装饰器回退链 `@@@` 最少 5 个；**V3 entry 无 `probability` 字段**，概率类语义由上述装饰器承担〔spec V3〕。
回填 V2 时装饰器 SHOULD 被剥离〔spec V3〕。

---

## 3. 注入顺序与提示词组装

### 3.1 三类"素材生命周期"

〔doc characterdesign〕

| 类别 | 字段 | 规则 |
|---|---|---|
| **常驻（permanent）** | `name`、`description`、`personality`、`scenario` | 每次生成请求都发送 |
| **一次性** | `first_mes` | 只进新会话的第一条历史 |
| **可挤出（ephemeral）** | `mes_example` | 仅在上下文有空位时插入，且整块逐块被挤出；可 pin |

### 3.2 Chat Completion 默认顺序（Prompt Manager）

默认提示条目（marker 除外均可编辑内容；默认项**不可删除只能停用**）〔doc prompt-manager〕。
默认顺序〔src PromptManager.js promptManagerDefaultPromptOrder〕：

| # | 标识符 | UI 名称 | 默认启用 | 说明 |
|---|---|---|---|---|
| 1 | `main` | Main Prompt | ✅ | 默认文案 `'Write {{char}}\'s next reply in a fictional chat between {{charIfNotGroup}} and {{user}}.'`〔src openai.js〕；被角色卡 `system_prompt` 覆盖（Prefer Char. Prompt） |
| 2 | `worldInfoBefore` | World Info (before) | ✅ | marker，填入 position=before(0) 的世界书条目 |
| 3 | `personaDescription` | Persona Description | ✅ | marker，填入用户 persona 描述（In Story String 位置时） |
| 4 | `charDescription` | Character Description | ✅ | marker ← 卡 `description` |
| 5 | `charPersonality` | Character Personality | ✅ | marker ← 卡 `personality`（格式 `{{personality}}`） |
| 6 | `scenario` | Scenario | ✅ | marker ← 卡 `scenario`（格式 `{{scenario}}`） |
| 7 | `enhanceDefinitions` | Enhance Definitions | ❌（唯一默认停用） | 默认文案："If you have more knowledge of {{char}}, add to the character's lore and personality..."〔src openai.js〕 |
| 8 | `nsfw` | Auxiliary Prompt | ✅ | 默认空〔src openai.js〕 |
| 9 | `worldInfoAfter` | World Info (after) | ✅ | marker，填入 position=after(1) 的世界书条目 |
| 10 | `dialogueExamples` | Chat Examples | ✅ | marker ← 卡 `mes_example` |
| 11 | `chatHistory` | Chat History | ✅ | marker，聊天历史从此注入 |
| 12 | `jailbreak` | Post-History Instructions | ✅ | 被角色卡 `post_history_instructions` 覆盖（Prefer Char. Instructions）；位于历史之后，最后发送 |

条目排序规则：列表顶部先发送，底部（通常是 Post-History Instructions）最后发送〔doc prompt-manager〕。
每个条目可设：**Position**（Relative=按列表位置；**In-Chat=忽略列表顺序，注入聊天历史的指定 Depth**，
`Depth 0 = 紧跟最后一条消息之后`）、**Role**（System/AI Assistant/User）、**Order**（同 Depth+Role 的条目按
Order 排序，"Lower Order values are sent earlier"；同深度分组的展示次序自上而下为 User → AI Assistant → System）、
**Triggers**（Normal/Continue/Impersonate/Swipe/Regenerate/Quiet；全不选=所有生成类型都生效）〔doc prompt-manager〕。
默认注入参数：`injection_position = RELATIVE(0)`、`injection_depth = 4`、`injection_order = 100`〔src PromptManager.js〕。

### 3.3 Text Completion：Story String（概念对照）

- Story string 是 Handlebars 模板；**模板里没写的参数就完全不进提示词**〔doc context-template〕。
- 可用参数：`{{anchorBefore}}`/`{{anchorAfter}}`（锚点：作者注释、摘要、Data Bank、STscript 注入、
  Web 搜索等）、`{{description}}`、`{{scenario}}`、`{{personality}}`、`{{system}}`（主提示词或角色的
  Main Prompt 覆盖）、`{{persona}}`、`{{char}}`、`{{user}}`、`{{wiBefore}}`/`{{loreBefore}}`、
  `{{wiAfter}}`/`{{loreAfter}}`、`{{mesExamples}}`（instruct 格式）/`{{mesExamplesRaw}}`〔doc context-template〕。
- 源码核实的 **fallback 常量**（`power-user.js` `defaultStoryString`）只含五段：
  `{{#if system}}{{system}}\n{{/if}}{{#if description}}{{description}}\n{{/if}}{{#if personality}}{{char}}'s personality: {{personality}}\n{{/if}}{{#if scenario}}Scenario: {{scenario}}\n{{/if}}{{#if persona}}{{persona}}\n{{/if}}`〔src power-user.js〕；
  该文件同时存在 `validateMissingField('wiBefore','loreBefore')` 校验（模板缺 WI 字段会告警）
  与 `autoFixStoryString`（缺 `anchorBefore`/`anchorAfter` 时自动补）〔src power-user.js〕。
  结合〔doc context-template〕，**实现时应采用的完整默认顺序为**：
  `system → wiBefore → description → personality("{{char}}'s personality: …") → scenario("Scenario: …") → persona → wiAfter → anchorBefore/After`。
- 分隔符默认值：`example_separator = '***'`、`chat_start = '***'`（Chat Start 插在 story string 与示例块之后、
  第一条历史之前）〔src power-user.js defaults；doc context-template〕；`<START>` 即被 example separator 替换〔doc characterdesign〕。
- Story string 默认位于提示词最前，可改为 **In-chat @ Depth**（power_user.context 默认：位置 IN_PROMPT、
  role SYSTEM、depth 1）〔src power-user.js defaults；doc context-template〕。
- "Always add character's name to prompt"：把角色名追加进提示词促使模型代入角色；Instruct 开启时由
  "Include Names" 接管〔doc context-template〕。Names as Stop Strings 建议常开，防止模型代写用户台词〔doc context-template〕。

### 3.4 覆盖语义：`system_prompt` / `post_history_instructions` / `{{original}}`

- 卡片 `system_prompt`（Main Prompt）与 `post_history_instructions`（PHI）**默认替换**前端对应设置；
  空字符串=不替换；`{{original}}` 展开为"被替换前的默认内容"，用于在卡片提示词里嵌一段默认提示词
  〔spec V2；doc characterdesign〕。
- 宏 `{{charPrompt}}`/`{{charInstruction}}` 可在别处引用这两个覆盖值；`{{original}}` 在覆盖之外无意义
  （"Original message content for substitution in character prompt overrides"）〔doc macros〕。

### 3.5 组装管线伪代码（Chat Completion 路径，据〔src openai.js〕整理）

```text
chat_completion.set_token_budget(max_context, max_tokens)
强制保留区（reserve_budget）: assistant 起始前缀 3 tokens、new chat / group nudge /
    continue nudge、控制类提示、工具调用数据 —— 先占位后回填，保证不被挤出
按 prompt_order 逐项发射条目（relative 条目按列表序；in-chat 条目按 depth/role/order 插入历史）:
    worldInfoBefore / worldInfoAfter  ← 世界书激活结果（§5）
    personaDescription                ← persona 描述
    charDescription / charPersonality / scenario ← 角色卡常驻字段
    dialogueExamples                  ← 卡 mes_example（按 <START> 分块）
chat_history: 从最新一条往旧循环
    if can_afford(msg): insert_at_start('chatHistory') else: break   # 旧消息先出局
dialogue_examples: 默认在 history 之后填充（pin_examples 时先填）
    if not can_afford_all([new_example_chat, ...blocks]): 丢弃   # 示例最先被牺牲
工具消息整组原子填充（all-or-nothing）
若强制保留区本身超限 → TokenBudgetExceededError: "Mandatory prompts exceed the context size."
```

要点：**超预算时的牺牲顺序 = 例示块 → 旧历史消息**；角色卡常驻字段与强制 nudge 不会被挤掉。

---

## 4. 用户 persona

- persona = 展示名 + 头像 + **可选描述文本**（性格/年龄/职业等）；描述文本支持宏〔doc personas〕。
- `{{user}}` = 当前 persona 名；`{{persona}}` = persona 描述〔doc macros〕。
  注意方向陷阱：persona 描述里的 `{{user}}`/`{{char}}` 与角色卡字段里**语义相反**，互转时 ST 会提示交换〔doc personas〕。
- **注入位置（Position，per-persona 保存）**〔doc personas〕：
  - `None (disabled)` —— 不注入；
  - `In Story String / Prompt Manager`（**默认**）—— 填入 story string 的 `{{persona}}` / PromptManager 的
    `personaDescription` marker（默认顺序第 3 位，早于角色卡字段）；
  - `Top of Author's Note` / `Bottom of Author's Note` —— 仅当作者注释启用时生效；
  - `In Chat @ Depth` —— 注入聊天历史指定深度，另配 **depth 与 role**。
- 源码默认值：`persona_description_positions = {IN_PROMPT:0, AFTER_CHAR:1(弃用), TOP_AN:2, BOTTOM_AN:3, AT_DEPTH:4, NONE:9}`；
  `DEFAULT_DEPTH = 2`，`DEFAULT_ROLE = 0(system)`〔src personas.js〕。每个 persona 独立保存
  description/position/depth/role/lorebook〔src personas.js〕。
- persona 可挂**专属世界书**；多来源世界书合并次序：**Chat → Persona → Character/Global**（后两者再按
  Sorted Evenly（默认，合成一个文件按 order 排）/ Character Lore First / Global Lore First 合并）〔doc worldinfo〕。

---

## 5. 世界书（World Info / Lorebook）

### 5.1 条目字段全表

〔doc worldinfo；位置/逻辑枚举经〔src world-info.js〕核实〕

| 字段 | 默认 | 语义 |
|---|---|---|
| `key`（主键） | — | 逗号分隔关键词；支持 JS 正则 `/.../flags`；明文键内不能含逗号。**默认不区分大小写**。进阶：扫描时消息带 `角色名:` 前缀与 `\x01` 前缀标记，可写正则只匹配特定说话人的话 |
| `keysecondary`（Optional Filter） | 空 | 副键，与 `selectiveLogic` 配合；空=忽略 |
| `selectiveLogic` | AND_ANY | `AND_ANY:0 / NOT_ALL:1 / NOT_ANY:2 / AND_ALL:3`〔src world-info.js〕；语义：AND_ANY=主键命中**且**副键至少一个；AND_ALL=主键命中**且**副键全部；NOT_ANY=主键命中**且**副键一个都没有；NOT_ALL=主键命中**且**副键未全部命中〔doc worldinfo〕 |
| `constant` | false | 🔵 蓝圈：无视内容恒触发（无键也插）；**预算耗尽时最先获得插入权**〔doc worldinfo〕 |
| `selective` | true | 🟢 绿圈：仅由关键词触发 |
| `vectorized` | false | 🔗 链圈：交给 Vector Storage 按嵌入相似度决定插入（用其 "Query messages" 代替 Scan Depth；"Max Entries" 独立于 token 预算）；有键时仍可常规触发〔doc worldinfo〕 |
| `position` | 0 | `before:0, after:1, ANTop:2, ANBottom:3, atDepth:4, EMTop:5, EMBottom:6, outlet:7`〔src world-info.js〕。before/after=角色定义前/后（影响中等/较强）；ANTop/ANBottom=作者注释顶/底（AN 被禁用即忽略）；EMTop/EMBottom=示例消息前/后（按示例对话解析）；atDepth=历史指定深度；outlet=不自动注入，存为具名 outlet 由 `{{outlet::Name}}` 宏取用（名字大小写敏感、未引用则跳过、不嵌套）〔doc worldinfo〕 |
| `depth` | 4 | 仅 atDepth 用；`DEFAULT_DEPTH = 4`〔src world-info.js〕 |
| `role` | system | 仅 atDepth 用；system/user/assistant（`extension_prompt_roles = {SYSTEM:0, USER:1, ASSISTANT:2}`〔src script.js〕） |
| `order`（insertion order） | — | 数值**越大越靠近上下文末尾**（"Order 100 appears before Order 250"）；越靠末尾影响越强〔doc worldinfo〕 |
| `probability` + `useProbability` | 100/false | Trigger %：激活后被跳过的概率（100=恒插；0=等效禁用）；sticky 激活期间跳过概率判定〔doc worldinfo〕 |
| `scan_depth` | 全局值 | 扫描最近 N 条消息找键（0=只扫递归条目与作者注释；1=只扫最后一条）〔doc worldinfo〕 |
| `context_percentage` / budget cap | 全局值 | token 预算：相对 max context 的百分比或绝对值；耗尽后不再激活新条目〔doc worldinfo〕 |
| `recursive_scanning` | 全局值 | 条目内容可再触发其他条目；每条目可设 Non-recursable（不被别人触发）/ Prevent further recursion（不再触发别人）/ Delay until recursion（仅递归轮激活，按 Recursion Level 低者先匹配）〔doc worldinfo〕 |
| `case_sensitive` | false | 区分大小写（`rose` vs `Rose`） |
| `match_whole_words` | **true** | 全词匹配（"king" 不匹配 "liking"）；**对日文/中文有害，应关闭**〔doc worldinfo〕——中文实现必须注意 |
| `include_names` | **true** | 扫描缓冲区加 `"Alice: Hello!"` 式说话人前缀，使角色名可作关键词命中 |
| `sticky` / `cooldown` / `delay` | 0 | 按消息数计（0=无效果）；sticky=激活后停留 N 条（期间跳过概率）；cooldown=激活后冷却 N 条（接在 sticky 之后）；delay=聊天不足 N 条不激活（delay=1 空会话也不激活）。随 swipe/删除最后消息而移除，重复触发不续时；分支继承状态〔doc worldinfo〕 |
| `inclusion group` + `group_weight` | —/100 | 同组多条只活一条：按 Group Weight 加权随机（默认 100 点）；可属多组；`prioritize_inclusion`=确定性选择（order 高者胜）；`use_group_scoring`=按命中数计分（主键 1 分/个，AND_ANY 副键 1 分/个，AND_ALL 全中才 1 分，NOT_* 不计）〔doc worldinfo〕 |
| `automation_id` | — | 绑定 Quick Replies/STscript，每批激活按"蓝圈先、order 次、递归最后"执行一次 |
| `character_filter` | — | 角色名/标签白名单（Exclude 反转） |
| `triggers` | 全部 | 按生成类型门控：Normal / Continue / Impersonate / Swipe / Regenerate（群聊不可用）/ Quiet |
| `comment`（标题） | — | 仅 UI 标签，AI 不可见 |
| `disable` | false | 停用条目 |

### 5.2 激活算法（语义整理：〔doc worldinfo〕+〔src world-info.js @1.11.0〕）

```text
budget = cap_abs 或 round(context_percent% × max_context)  # 1.11.0: Math.round(wi_budget*maxContext/100) || 1
合并来源条目（Chat → Persona → Character/Global；后两者 Sorted Evenly 时按 order 全局排序；
             每一挂载源内部 "Chat lore always goes first"，按 order 降序逐条尝试）
对每个候选条目:
    constant → 无条件候选
    否则: 在扫描窗口（最近 scan_depth 条消息，include_names 时带 "名字: " 前缀）内
          按键匹配规则（正则 / 全词 / 大小写）判主键命中，再按 selectiveLogic 结合副键
    概率: roll < trigger% 才保留（sticky 期间跳过）
    inclusion group: 同组只活一条（加权随机 / prioritize / scoring）
    预算: if 已用 tokens + 本条 tokens >= budget:
              "WI budget reached, stopping"; token_budget_overflowed = true; 停止扫描
              # 溢出条目直接不被激活；不回删已激活条目（1.11.0 源码行为）
    激活 → 计入已用预算；其 content 参与递归扫描（Max Recursion Steps 限制轮数：
          0=仅受预算限制，1=递归实质关闭，2=允许一轮递归 …）
插入分桶: before/after → story string 的 wiBefore/wiAfter 槽位
          ANTop/ANBottom → 作者注释顶/底
          EMTop/EMBottom → 示例消息前/后
          atDepth(depth, role) → 聊天历史指定深度
          outlet → 暂存，等待 {{outlet::Name}} 宏取用
```

**超预算丢弃顺序（重点）**〔doc worldinfo〕：
1. **constant（蓝圈）条目最先获得插入权**；
2. 然后**按 insertion order 从大到小**（order 大 = 更靠近上下文末尾 = 优先保留）；
3. **直接关键词命中的条目优先于**经由其他条目内容递归命中的条目；
4. 预算耗尽后其余条目**整体丢弃**（可 "Alert on overflow" 告警）；已插入的不回删〔src world-info.js @1.11.0〕。
   卡片书特有的 `priority` 字段另规定"**priority 数值小者先被丢弃**"〔spec V2〕。

### 5.3 与角色卡书的关系

卡片 `character_book` 默认启用并与用户世界书叠加（角色书同键优先）〔spec V2〕；ST 把它作为
"角色世界书（primary，随卡导出）"挂载，全局/角色/人格/聊天四类书按 §4 的次序合并〔doc worldinfo〕。

---

## 6. 宏

### 6.1 语法规则〔doc macros〕

- 形如 `{{macroName}}`，**大小写不敏感**；参数用 `::` 或空格（`{{roll 1d10}}` / `{{random::a::b}}`）；
- **嵌套宏先解析内层**（`{{getvar::{{char}}_mood}}`）；`\{\{text\}\}` 转义为字面量；
- 条件块 `{{if cond}}...{{/if}}`（内容作为最后一个参数）；注释 `{{// 注释}}` 解析为空；
- 旧写法：`<USER>`→`{{user}}`、`<BOT>`/`<CHAR>`→`{{char}}`、`<GROUP>`→`{{group}}`；
- `{{random}}` 每次重摇；`{{pick}}` 按会话+位置稳定（可 `/reroll-pick`）。

### 6.2 宏表（按用途分组；全部出处〔doc macros〕）

| 分组 | 宏 → 取值 |
|---|---|
| **名字/参与者** | `{{user}}`→当前 persona 名；`{{char}}`→当前角色名；`{{group}}`→群成员名单（含静音，逗号分隔；单聊=角色名）；`{{groupNotMuted}}`→排除静音；`{{charIfNotGroup}}`→单聊=角色名、群聊=空；`{{notChar}}`→除当前发言者外的所有参与者 |
| **角色卡字段** | `{{description}}`、`{{personality}}`、`{{scenario}}`；`{{charPrompt}}`→Main Prompt 覆盖；`{{charInstruction}}`→PHI 覆盖；`{{charDepthPrompt}}`→角色注释；`{{charCreatorNotes}}`、`{{charVersion}}`；`{{mesExamples}}`/`{{mesExamplesRaw}}`；`{{charFirstMessage}}`（可 `{{charFirstMessage::1}}` 取指定序号）；`{{original}}`→覆盖替换点的原默认内容 |
| **persona** | `{{persona}}`→用户 persona 描述 |
| **聊天历史** | `{{lastMessage}}`/`{{lastMessageId}}`/`{{lastUserMessage}}`/`{{lastCharMessage}}`；`{{firstIncludedMessageId}}`（当前上下文里第一条消息的序号）；`{{firstDisplayedMessageId}}`；`{{allChatRange}}`（如 `0-{{lastMessageId}}`）；`{{summary}}`（摘要扩展最新总结） |
| **时间日期** | `{{time}}`、`{{date}}`、`{{weekday}}`、`{{isotime}}`(HH:mm)、`{{isodate}}`(YYYY-MM-DD)、`{{time::UTC±offset}}`、`{{datetimeformat::格式}}`、`{{idleDuration}}`（距上一条用户消息的可读时长）、`{{timeDiff::左::右}}` |
| **变量** | `{{setvar::名::值}}`/`{{getvar::名}}`/`{{addvar}}`/`{{incvar}}`/`{{decvar}}`/`{{hasvar}}`/`{{deletevar}}`；全局版 `{{setglobalvar}}` 等；简写 `{{.x}}`(局部)/`{{$x}}`(全局)；支持 `= ++ -- += -= \|\| ?? == != > >= < <=` 等运算符 |
| **随机/骰子** | `{{random::a::b::c}}`（每次重摇）、`{{pick::a::b::c}}`（稳定）、`{{roll::1d10}}`（droll 骰语法掷骰） |
| **运行时状态** | `{{maxPrompt}}`、`{{maxContextTokens}}`、`{{maxResponseTokens}}`、`{{model}}`、`{{lastGenerationType}}`、`{{hasExtension::名}}`、`{{input}}`（当前输入框内容） |
| **提示词模板** | `{{systemPrompt}}`、`{{defaultSystemPrompt}}`、`{{authorsNote}}`/`{{charAuthorsNote}}`/`{{defaultAuthorsNote}}`；instruct 序列 `{{instructStoryStringPrefix/Suffix}}`、`{{instructUserPrefix/Suffix}}`、`{{instructAssistantPrefix/Suffix}}`、`{{instructSeparator}}`、`{{instructStop}}` 等；`{{chatSeparator}}`、`{{chatStart}}`；reasoning 前后缀 |
| **实用** | `{{newline}}`（可 `::n`）、`{{space}}`、`{{noop}}`→空串、`{{trim}}`→去首尾换行、`{{outlet::名}}`→取世界书 outlet 条目、`{{banned::词}}`（文本补全后端禁词）、`{{reverse::text}}` |

### 6.3 替换范围与时机

- 宏"贯穿 ST 的提示词、角色卡、世界书、Quick Replies 等"所有支持宏的文本字段〔doc macros〕——
  即**先做宏替换、再组装进提示词**，角色卡字段与世界书条目内的宏都会被展开。
- 嵌套先内后外；`random` 每轮重摇、`pick` 稳定；`||`/`??` 的回退值惰性求值〔doc macros〕。

---

## 7. 上下文预算与裁剪策略

综合〔src openai.js〕与〔doc worldinfo / prompt-manager〕，ST 的预算是**分区管理**而非全局一条线：

1. **总预算** = `max_context − max_tokens`（`setTokenBudget`）。
2. **强制保留区（reserve）**：assistant 起始前缀（3 tokens）、new chat nudge `[Start a new Chat]`、
   group nudge `[Write the next reply only as {{char}}.]`、continue nudge
   `[Continue your last message without repeating its original content.]`、控制提示与工具数据——
   用 `reserveBudget` 先占位，保证任何情况下不被挤掉；整区超限直接报错
   `TokenBudgetExceededError("Mandatory prompts exceed the context size.")`〔src openai.js〕。
3. **聊天历史**：从最新往旧逐条 `canAfford` → 放不进即停（旧消息先出局）〔src openai.js〕。
4. **对话示例**：默认在历史**之后**填充（`pin_examples` 开启时先填）；放不下就丢——
   **示例块是第一个被牺牲的素材**〔src openai.js；doc characterdesign "pushed out of context block by block"〕。
5. **世界书**：独立预算（context % 或绝对 cap），只管世界书条目；超预算按 §5.2 的丢弃顺序处理，
   与主历史裁剪互不影响〔doc worldinfo〕。
6. 可选开关：`pin_examples`（钉住示例）、"Squash system messages"（已弃置，建议用 Prompt Post-Processing 代替）〔doc prompt-manager〕。

---

## 8. 按深度注入的统一模型（作者注释 / 角色注释 / persona@depth / WI@D）

ST 所有"插进聊天历史某一层"的机制共用一套底层设施：**extension prompt 注册表**——
`setExtensionPrompt(key, value, position, depth, scan, role)`，其中
`extension_prompt_types = {NONE:-1, IN_PROMPT:0, IN_CHAT:1, BEFORE_PROMPT:2}`、
`extension_prompt_roles = {SYSTEM:0, USER:1, ASSISTANT:2}`〔src script.js〕。
作者注释即通过 `context.setExtensionPrompt(MODULE_NAME, …)` 注入〔src authors-note.js〕。
各机制语义：

| 机制 | 默认参数 | 语义 |
|---|---|---|
| **Author's Note（作者注释）** | depth=4，position=In-chat(1)，interval=1，role=system〔src authors-note.js `DEFAULT_DEPTH/DEFAULT_POSITION/DEFAULT_INTERVAL/DEFAULT_ROLE`〕 | 聊天/角色两个框：chat 框仅当前会话、default 框用于每个新会话〔doc authors-note〕。插入位置两选一：**After Scenario**（角色定义之后、示例消息之前）或 **In-chat @ Depth**（depth 0=最后一条消息之后；"depth 4 places it before the most recent 3 messages"；**越靠近提示词底部影响越强**）〔doc authors-note〕。**Interval（插入频率）**：0=从不插入；1=每次用户输入都插入；4=每 4 次用户输入插入一次〔doc authors-note〕。slah 命令位置映射：`after/scenario→0、chat→1、before_scenario→2`〔src authors-note.js validPositions〕 |
| **Character's Note（角色注释，@D）** | depth=4，role='system'（`depth_prompt_depth_default = 4`、`depth_prompt_role_default = 'system'`〔src script.js〕） | 角色卡编辑器内的 "@ Depth" 注入：距最新消息 N 条处、以指定 Role（User/System/Assistant）注入，**始终保持在固定深度**〔doc characterdesign〕；V2 卡存于 `data.extensions.depth_prompt`〔src characters.js〕；V3 卡用 `@@depth`/`@@role` 装饰器表达〔spec V3〕 |
| **persona @ Depth** | depth=2，role=system〔src personas.js〕 | 见 §4 |
| **WI 条目 @ D** | depth=4，role=system〔src world-info.js DEFAULT_DEPTH〕 | 见 §5.1 |
| **Prompt 条目 In-Chat** | depth=4（Prompt 类默认），depth 0=最后一条消息后〔src PromptManager.js；doc prompt-manager〕 | 见 §3.2 |

**实现要点**：把"深度注入"实现成一个统一的 `list[(depth, role, order, text)]` 注册表，
生成时按 depth 从大到小插入消息流（depth 越大越靠前），同 depth 内 role 次序 User→AI→System、order 小者先〔doc prompt-manager〕。

---

## 9. 群聊（Group Chat）

- **回复顺序策略**〔doc groupchats〕：
  - **Manual**：用户菜单点人或 `/trigger`；用户消息不触发任何回复；发空输入=随机一名未静音成员发言。
  - **Natural Order（自然顺序）**：先从最后一条消息提取**提及**（全词匹配，"Misaka Mikoto" 可被
    "Misaka"/"Mikoto" 命中而非 "Misa"）；其余成员按 **Talkativeness 概率激活**——线性 0%~100%
    （Shy=不点名不说话，Chatty=每次必说，**新角色默认 50%**〔doc groupchats〕；源码
    `talkativeness_default = 0.5`〔src script.js〕，激活判定 `rollValue = Math.random(); if (talkativeness >= rollValue) activate`〔src group-chats.js activateNaturalOrder〕）。
    若无人激活，**随机选一人**（偏好 talkativeness>0 的成员）〔doc groupchats；src group-chats.js〕。
    不开 "Allow Self Responses" 时：角色不响应自己名字的提及、且不允许同一角色连续发言（`bannedUser` 逻辑）〔doc groupchats；src group-chats.js〕。
  - **List Order**：按成员列表顺序。
  - **Pooled**：随机选一位"自上次用户消息以来还没说过话"的成员；全员说完再回到随机〔doc groupchats〕。
- **Auto 模式**：开启后群聊按所选策略**无需用户交互自动连发**，每次间隔默认 **5 秒**
  （`DEFAULT_AUTO_MODE_DELAY = 5`，`setInterval(groupChatAutoModeWorker, autoModeDelay * 1000)`）；
  在输入框打字即暂停，排队中的生成继续；Manual 策略下 auto worker 随机挑一人〔doc groupchats；src group-chats.js〕。
- **生成处理（Generation Handling）**〔doc groupchats〕：
  - `Swap character cards`（默认）：每次只把**当前发言者**的角色卡放进上下文；
  - `Join character cards`：所有成员的 Description、Scenario（除非被会话覆盖）、Personality、
    Message examples、Character notes/Depth prompts 按列表序**合并**（可含/排除静音成员）；
    文档警告可能导致"人格混合、特质混乱"；
  - `Join Prefix and Suffix`：合并字段的前后缀模板（支持宏，`{{char}}` 换成该成员名、`<FIELDNAME>` 为字段名）。
- 群聊内：Mute（静音开关）、**Force Talk**（无视策略与静音强制发言）、Scenario Override（全体成员共用
  覆盖情境）、新成员默认插入列表顶部；**Regenerate 在群聊不可用**——改为删除最后一条回复并按策略重新排队〔doc groupchats〕。
- **Group Nudge**：`[Write the next reply only as {{char}}.]`——群聊生成时附加，强制模型只以指定角色口吻回答〔src openai.js default_group_nudge_prompt〕。
- 消息对象带 `name` 字段标记说话人（`mes.name = character.name`〔src group-chats.js〕）；群聊开场白可选
  `alternate_greetings` 随机一张〔doc characterdesign〕或 V3 的 `group_only_greetings`〔spec V3〕。

---

## 10. Swipe / 重新生成 / 继续

| 操作 | 语义 | 出处 |
|---|---|---|
| **Swipe** | 对同一条 AI 消息生成**替身回复**（左右切换备选，右方向键即 swipe）；编辑自己的消息后可 swipe 得到基于新输入的回复。V2 卡 `alternate_greetings` 就是"首条消息的 swipes" | 〔doc chatting；spec V2〕 |
| **Regenerate** | 重新生成最后一条回应；群聊中不可用（删除后按策略重排） | 〔doc chatting；doc prompt-manager〕 |
| **Continue** | "Extend last message"——把模型最后的消息**续写**下去；配套 Continue Nudge `[Continue your last message without repeating its original content.]`、Continue Postfix（续写文本的前缀）与 Continue Prefill（以 Assistant 角色先发 nudge，"May not work with all Chat Completion sources"） | 〔doc chatting；doc prompt-manager；src openai.js〕 |
| **Impersonate** | "AI writes message as user"——模型以用户口吻写一条消息；默认提示词 `[Write your next reply from the point of view of {{user}}, …]` | 〔doc chatting；src openai.js〕 |
| **消息出入上下文** | 每条消息可切 Included/Excluded（"AI sees this message"），Branch/Checkpoint 建立平行叙事线 | 〔doc chatting〕 |
| **生成类型门控** | WI 条目与 Prompt 条目都可按 Triggers（Normal/Continue/Impersonate/Swipe/Regenerate/Quiet）决定在哪种生成中出现 | 〔doc worldinfo；doc prompt-manager〕 |

---

## 11. 机制 → ctrcg 现状对照表（含 AI PC 落地建议）

> 现状引用均为本仓库代码（D:\code_study\TRCG，main 分支 @755d5f5）。
> "AI PC" 指目标：把 `main.py:194-202` / `ctrcg/web/server.py:334-338` 中**只为同伴**构建的
> `make_pc_agent` 同样应用到玩家角色（当前玩家永远是人类操控：战斗走
> `session.player_combat_turn`（session.py:680-703）菜单流，探索走 `ui.free_input`（session.py:414））。

| # | SillyTavern 机制 | ctrcg 现状 | 差距与 AI PC 落地建议 |
|---|---|---|---|
| 1 | 角色卡常驻字段分层（description/personality/scenario 按固定顺序注入，§2/§3） | 无分层卡。同伴人设是模组 JSON 里的 `persona`/`voice` 两个字段（module_data.py:55-130），连同职业/战术/武器/义体**一次性烘进单条 system prompt**（agents.py:114-144）；DM 同理（agents.py:21-82） | 把每个 PC（含玩家角色）建模为 **chara_card_v3 式 dict**：description（设定/身世）、personality（性格摘要）、scenario（当前情境由引擎生成）、system_prompt/post_history_instructions 可选覆盖；组装器按 §3.2 顺序渲染，而非全部塞进 system |
| 2 | `first_mes` / `alternate_greetings` | 无。开局呈现来自模组场景 `intro` 文本（session.py:127-137 `enter_scene`） | AI PC 可在开局生成 greeting 并支持 swipe 换开场；落地为 `enter_scene` 后追加 PC 自我介绍工具（如 `greet`） |
| 3 | `mes_example`（可挤出的语气示例，`<START>` 分块） | 无 | 可选：把 `voice` 扩展成 2~3 段 `<START>` 示例对话；预算紧张时整块先挤（对应 §3.5 的牺牲顺序） |
| 4 | `system_prompt`/`post_history_instructions` 覆盖 + `{{original}}` | 无覆盖机制；模板为硬编码常量（agents.py DM_SYSTEM_TEMPLATE / PC_SYSTEM_TEMPLATE） | 把模板改为"默认提示词 + 卡片可覆盖字段"，`{{original}}` 语义照搬（PHI 放在历史之后，对 AI PC 的"回合结束前提醒"非常合适） |
| 5 | 用户 persona（In Story String 默认 / @Depth2，§4） | 无 persona 概念：玩家是人类，无注入；同伴的 persona 属于卡内 | 引入"玩家面板 persona"（人类玩家保留）；**AI PC 模式下玩家槽位由 AI 接管**——其 persona 即卡 description，注入位置建议 In Story String 等价物（system 区），避免与同伴重复 |
| 6 | 世界书 constant/selective 条目 + 键逻辑（§5） | **无世界书**。模组手册全量文本经 `module.handbook()` 烘进 DM system prompt（loader.py:191-199 → agents.py:27-28,43）＝"无限预算的 constant 条目"；场景/队伍/旗标快照以 user 消息注入 DM 上下文（session.py:469-474） | 引擎状态 WI 化：每场景一条 constant 条目（order 高）、剧情旗标转 selective 条目（键=旗标值）、NPC/物品条目按名触发；给 AI PC 的私有知识（如职业与街头身份）用角色书挂载 |
| 7 | 世界书预算与丢弃顺序（constant 先、order 大者留，§5.2） | 仅全局粗略压缩（见 #12），无 WI 预算 | WI 管线自带预算；手册按幕拆条目后即可享受"按需进上下文" |
| 8 | 按深度注入统一模型（AN/@D，§8） | 无深度注入器；一切上下文都落在**提示词尾部 user 消息**（loop.py:164-176 context_messages；session.py:469-474），最接近"depth 0 的 user 注入" | 实现统一 `Injection(depth, role, order, text)` 注册表；桌上插话、DM 任务前缀（session.py:462-477 lead/task）都迁进来 |
| 9 | Author's Note（interval/深度/角色） | 无 AN。最接近物：`dm_turn` 的 `lead`/`input_label` 可变任务前缀（session.py:462-477）与【桌上插话】摘要 `_digest_table`（session.py:484-494） | 战斗风格、场面指令、AI PC 的"本轮目标"适合 AN 语义（depth 4、role system、每回合刷新） |
| 10 | 宏系统（{{user}}/{{char}}/{{persona}}/{{time}}/{{roll}}…，§6） | 无宏；模板用 Python `str.format`（agents.py `{name}/{role_name}/{persona}/{voice}/{weapons}/{status}/{tactics}/{handbook}`；session.py 场景插值） | 实现最小宏集：`{{user}}/{{char}}/{{persona}}/{{time}}` + 扩展 `{{party}}/{{scene}}/{{flags}}`（引擎快照）；`{{roll::1d10}}` 直接桥接 `rules/dice.py`（LLM 仍不产生数值，宏由引擎解析） |
| 11 | Token 预算分区（强制保留区/历史从新到旧/示例先挤，§7） | `TruncateCompactor`（loop.py:88-121）：CJK≈1字/1token 粗估（loop.py:82-85）、单条 8000 字符截断、**保留 system + 最近 8 条**、丢弃中间并插"系统注"占位（loop.py:105-111） | 已有"历史从新到旧"雏形；补齐：强制保留区（nudge/工具说明）、示例区先挤、与 WI 预算分账 |
| 12 | 群聊轮替：talkativeness 概率 + natural/pooled + auto 5s（§9） | `TableSocial`：每次玩家输入触发一整拍——抢话窗口→DM 回应→自由反应→辩论→DM 即席回应（social.py:67-99）；反应波**全员并行决策**、出口上限 `MAX_WAVE_SPEAKERS=2`（social.py:28,102-133）、抢话排序（social.py:124）；辩论 `DEBATE_MAX_ROUNDS=2`（social.py:30,150-194）；无 talkativeness、无自动连发（节拍由玩家输入驱动） | 把"全员并行+出口上限"换成/叠加 **talkativeness 概率激活**（成员带 0~1 值）；pooled 轮替可让 AI PC 也有"还没说过话"的机会；auto 模式（如桌面冷场 N 秒后同伴接话）可选 |
| 13 | Swap vs Join 卡片 | 每同伴**独立 Session + 独立卡**（近似 Swap；`_pc_agent_turn` session.py:1092-1114、`pc_session` session.py:86-89）；桌面可见性靠 `table_log` 摘要 `_recent`（social.py:261-269）与 DM `_digest_table` | 维持 Swap；"Join"仅用于构建"队伍共同上下文"（party 快照）；AI PC 加入同一机制即可（关键改动点） |
| 14 | Group Nudge `[Write the next reply only as {{char}}.]` | 无显式 nudge；等价物是 PC 回合 task 前缀`【第N轮 · X 的回合】`（session.py:1103-1107）与桌边 `SOCIAL_TASK`（social.py:37-46） | 保留现做法即可，语义一致；多 PC 桌面轮次建议加 "only as {{char}}" 式约束句 |
| 15 | Swipe（替身回复）/ Regenerate / Continue | **全无**。Agent 循环单次产出 final（loop.py:186-191），无重生成/续写入口 | AI PC 回合可生成 N 个候选动作（swipe 语义，玩家或 DM 挑选）；长回合被 `max_tokens=500`（agents.py:129）截断时用 Continue nudge 续写；`DuplicateCallGuard`（loop.py:59-77）保证重试不空转 |
| 16 | 会话持久（chat jsonl / 消息级 include-exclude） | `Session` 事件日志 + 重放，不变量"模型可见 ⟺ 已记录"（events.py:1-70）；swipe 候选无处存放 | 日志加 `swipe` 事件类型；候选动作全量落日志再挑选，保持不变量 |
| 17 | 生成类型门控（Triggers） | 无（单一生成类型） | 引入 `gen_type ∈ {normal, swipe, continue, impersonate}`，让世界书/AN 可按类型门控（如 swipe 时不重复注入 AN） |
| 18 | Guard 插件（步数/超时/去重） | ST 无对应物，ctrcg 独有增强（loop.py:26-78：StepBudgetGuard 12 步、ToolTimeoutGuard 10s/交互 3600s、DuplicateCallGuard） | 保留；swipe/continue 生成同样过 guard 链 |
| 19 | 规则数值引擎化（ST 无此概念） | 一切掷骰在引擎（tools.py 原则注释；`skill_check`/`opposed_check` 等工具组） | 保持——AI PC 的所有动作仍经 `combat_action` 工具结算，宏/世界书只影响**叙事上下文**，不得替代引擎 |

### AI PC 最小落地清单（按依赖顺序）

1. **PC 卡结构**（#1/#4/#5）：`CharacterCard` dataclass（V3 字段子集）+ 组装器（§3.2 顺序）。
2. **深度注入器**（#8/#9）：`injections: list[Injection]`，替换 `context_messages` 尾部堆叠。
3. **玩家角色 Agent**：`make_pc_agent(player_comp, …)` 接入 `player_combat_turn`/`ai_explore_turns`
   的 AI 分支（人类模式照旧，AI PC 为开关）；复用同伴的会话/工具/守卫。
4. **引擎状态世界书**（#6/#7）：场景/旗标/NPC 条目 + 预算。
5. **桌面轮替升级**（#12/#14）：talkativeness + pooled；AI PC 参加反应波与辩论。
6. **Swipe/Continue**（#15/#16/#17）：候选动作与续写，事件日志记录候选。

---

## 12. 快查：默认值一览

| 项 | 默认值 | 出处 |
|---|---|---|
| Prompt 条目注入 | position=RELATIVE(0)、depth=4、order=100 | 〔src PromptManager.js〕 |
| persona @Depth | depth=2、role=system | 〔src personas.js〕 |
| 作者注释 | depth=4、position=In-chat、interval=1、role=system | 〔src authors-note.js〕 |
| 角色注释（@D） | depth=4、role=system | 〔src script.js〕 |
| WI 条目 @D | depth=4 | 〔src world-info.js〕 |
| WI 逻辑枚举 | AND_ANY=0、NOT_ALL=1、NOT_ANY=2、AND_ALL=3 | 〔src world-info.js〕 |
| WI position 枚举 | before=0、after=1、ANTop=2、ANBottom=3、atDepth=4、EMTop=5、EMBottom=6、outlet=7 | 〔src world-info.js〕 |
| talkativeness | 0.5（新角色） | 〔src script.js；doc groupchats〕 |
| 群聊 auto 间隔 | 5 秒 | 〔src group-chats.js〕 |
| 示例分隔符 / Chat Start | `***` / `***` | 〔src power-user.js〕 |
| WI 预算 | context %（相对 max context）或绝对 cap；耗尽即停 | 〔doc worldinfo；src world-info.js@1.11.0〕 |
| 全词匹配 / 包含名字 | 默认开 / 默认开（中文应关全词匹配） | 〔doc worldinfo〕 |

---

## 13. 来源清单

**官方文档（docs.sillytavern.app，抓取 2026-10-02）**

- 角色设计：<https://docs.sillytavern.app/usage/core-concepts/characterdesign/>
- 世界书：<https://docs.sillytavern.app/usage/core-concepts/worldinfo/>
- Persona：<https://docs.sillytavern.app/usage/core-concepts/personas/>
- 作者注释：<https://docs.sillytavern.app/usage/core-concepts/authors-note/>
- 宏：<https://docs.sillytavern.app/usage/core-concepts/macros/>
- 群聊：<https://docs.sillytavern.app/usage/core-concepts/groupchats/>
- 聊天操作（swipe/regenerate/continue）：<https://docs.sillytavern.app/usage/chatting/>
- Context Template：<https://docs.sillytavern.app/usage/prompts/context-template/>
- Prompt Manager：<https://docs.sillytavern.app/usage/prompts/prompt-manager/>

**SillyTavern 源码（GitHub SillyTavern/SillyTavern，release 分支；另注明者除外）**

- `public/scripts/world-info.js`（position/logic 枚举、DEFAULT_DEPTH=4、DEFAULT_WEIGHT=100）
- `public/scripts/world-info.js @ tag 1.11.0`（预算公式与"预算到达即停止、不回删"的激活循环）
- `public/script.js`（`talkativeness_default = 0.5`、`depth_prompt_depth_default = 4`、
  `depth_prompt_role_default = 'system'`、`extension_prompt_types/roles`）
- `public/scripts/power-user.js`（`defaultStoryString` fallback、`chat_start/example_separator = '***'`、
  context 默认、`validateStoryString`/`autoFixStoryString`）
- `public/scripts/PromptManager.js`（`promptManagerDefaultPromptOrder` 12 项默认顺序、注入默认值）
- `public/scripts/openai.js`（默认提示词文案、预算/牺牲顺序、nudge 文案）
- `public/scripts/personas.js`（persona 位置枚举与 depth=2/role=0 默认）
- `public/scripts/authors-note.js`（AN 默认 depth=4/position=1/interval=1/role=system、setExtensionPrompt 注入）
- `public/scripts/group-chats.js`（talkativeness 激活公式、auto 模式 5s、pooled/natural、消息 name 字段）
- `src/endpoints/characters.js`（V2 导入映射 `readFromV2`/`charaFormatData`、depth_prompt 默认、
  `convertWorldInfoToCharacterBook` 的 position 0↔before_char 映射与 extensions 扩展字段）

**角色卡规范**

- V2：<https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md>
- V3：<https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md>
