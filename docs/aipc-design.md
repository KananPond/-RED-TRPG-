# AI PC（AI 驱动的玩家角色）设计 —— 以 SillyTavern 机制落地 trpg

> 实现依据：`docs/sillytavern-architecture.md`（下称〔ST 规格〕，引用格式 §N）与仓库代码
> （main @755d5f5，行号为该快照行号）。本文是唯一实现蓝图；〔ST 规格〕§11 的"落地建议"
> 在本文中逐条收敛为可编码的签名与分支。
>
> **铁律**：LLM 只决策；一切掷骰与数值由引擎结算（`trpg/game/tools.py:1-5` 原则）。
> 宏 `{{roll}}`、世界书、提示词组装只影响**叙事上下文**，永不替代结算路径。
>
> **基线验证（本设计期间实跑）**：`DEEPSEEK_API_KEY= python -m pytest tests/ -q`
> → `208 passed in 8.28s`。注意：环境中若存在 `DEEPSEEK_API_KEY`，`test_web.py` 有 3 例
> 因 `AppState._resolve_llm_config()` 自动识别到密钥而失败（`web/server.py:131-147`），
> 属环境变量污染，非代码缺陷；CI/本地跑全量测试须先清空供应商密钥环境变量。
> （rev2 复核：复审人以同命令实跑得 `208 passed in 7.91s`，并以
> `DEEPSEEK_API_KEY=sk-fake-test python -m pytest tests/test_web.py -q` 复现
> `3 failed, 13 passed`，证实污染判断属实。）
>
> **修订记录 rev2（2026-10-02，按复审意见）**：① `_debate_round` 的 by_name 必须改为
> party()∩pc_agents，否则玩家被邀请进辩论却永远无法发言、首轮即被"淘汰"
> （social.py:198-204/180-181）；② aipc 的 `say` 不再复用同伴 say（tools.py:183-188
> 不写 table_log），改为带桌面记账的实现并补 say-only 分支语义；③ `open_shop` 阻塞
> 菜单非交互化（抽 `buy_good`），explore_act("buy") 与旁听 /购买 都不再触达 ui.choose；
> ④ 战斗反应语义如实化：combat.py:1420-1423 使自动规则分支在 AI 模式不可达，
> `return None` = 恒不触发（与同伴现状一致），而非"引擎自动规则"；⑤ 玩家会话 id
> 收敛为 `session.pc_session(玩家名)`，消除 §2.1/§2.5 的两份会话矛盾；⑥ `_post_beat`
> 必须传播 "end"（session.py:459-460）；⑦ 行号校正（session.py:869 为 free_input、
> charbuild.py:30 截断）与 attach 失败语义统一（返回 None）。
>
> **修订记录 rev3（2026-10-02，按第二轮复审意见）**：①兜底时机二选一，收敛为"单拍无
> 工具即兜底"（§2.4，无跨拍计数状态）；②`poll_command` 防御转发——ConsoleUI/AutoUI
> 无此方法（cli.py:16-151），终端无旁听指令，`ui/cli.py` 零改动；③AI PC Agent 的
> `tool_names=[]`（registry.py:53-56 空列表=全量下发；沿用 make_pc_agent 的列表会被
> loop.py:184 过滤静默吞掉 explore_act）；④aipc 偏好改独立端点 `/api/settings/aipc`
> （apply_llm_settings 缺密钥早退 server.py:167-168 不落盘，rev2 的测试前提不成立）；
> ⑤战斗救援能力缺口如实声明（combat_action 的 use_item 无 target、枚举无 stabilize，
> tools.py:257-258）；⑥pyproject.toml packages 补 `trpg.st`；⑦改进点花费静默
> （原"system_line 通知"不实）、创建/读档两条路径的卡面数值补差抽成公共 fixup
> （此前一条路径补、另一条不补）、assemble_system 增 template_vars（模板实有 9 个
> 占位符）、`_post_beat` 内部重取 scene（session.py:454）、战斗期 say 不进 table_log
> 的措辞收敛（§2.3 vs §2.5）。

---

## 0. 总体方案

一句话：**新增纯机制库 `trpg/st/`（角色卡/persona/宏/世界书/提示词组装/预算裁剪）+
编排层 `trpg/game/aipc.py`（AIPCController），把 `session.py` 的 6 类人类交互点改为
`if self.aipc: ...` 分支，AI PC 以"第 4 名同伴"的身份复用既有同伴基础设施
（pc_agents 注册表、pc_session、register_pc_tools、社交反应波、启发式兜底）。**

设计取舍（与〔ST 规格〕§11 对照表逐条对应）：

| ST 机制 | 本文落点 | 取舍 |
|---|---|---|
| 角色卡 V3 字段子集 + 常驻字段分层（§2/§3.2） | `st/card.py` `CharacterCard` | 只取进提示词的字段；PNG/charx 导入不做 |
| Prompt Manager 顺序（§3.2） | `st/assemble.py` `assemble_system` | 渲染为**单条 system**（loop 协议不动，`_build_messages` 仍取 `agent.system_prompt`，loop.py:203-206） |
| 用户 persona（§4） | `st/card.py` `Persona`；AI PC 模式下玩家槽位注入=卡描述（§11 建议照搬） | 人类模式 persona 仅进 DM 上下文块，不动 DM system |
| 宏（§6） | `st/macros.py` `render` | 最小集：名字/persona/时间 + 引擎快照 + `{{roll}}`；嵌套两层、大小写不敏感、`\{\{` 转义 |
| 世界书（§5） | `st/worldinfo.py`；引擎状态 WI（场景/旗标/NPC）+ 角色书 | P1：激活算法、constant 先行、order 大者留、预算即停（§5.2 全套） |
| 按深度注入（§8） | `st/assemble.py` `Injection` + `inject_at_depth` | P0 提供纯函数；P0 接线仅"深度排序进每回合上下文块"，跨历史真插桩留 P1（见 §2.7 取舍说明） |
| 预算分区（§7） | `st/assemble.py` `fit_to_budget` + WI 自带预算 | 牺牲顺序=示例块→旧历史；常驻字段与 nudge 不挤 |
| 群聊 talkativeness（§9） | 社交层 speakers 扩为全队 + 卡带 `talkativeness` | P0 先"进波"；概率激活数值化 P2 |
| Swipe（§10） | AI PC 行动候选 | P2；单候选兜底 |

分层约束：`trpg/st/` **不得 import `trpg.game.*`**（纯机制、可单测）；控制器在
`trpg/game/aipc.py`，单向依赖 st。这样"开关关闭=st 全部零参与"。

**依赖与密钥约束**：全部新代码仅用 stdlib（dataclasses/random/datetime/re/itertools，
零第三方依赖，与 pyproject 现状一致）；不引入任何新密钥面——AI PC 配置块
（开关/persona/角色卡）均为非敏感叙事文本，密钥继续只经环境变量与运行时内存
（`trpg/config.py:1-2`、`web/server.py:36,107` 既有约定）。

开关语义（贯穿全文）：`GameSession.aipc: AIPCController | None`。为 None 时，
**所有**新分支短路，行为与 755d5f5 完全一致；`build_session`（web/server.py:317）仅在
`llm_cfg 非 None 且 aipc 开关开启` 时挂控制器。

---

## 1. 新包 `trpg/st/`：模块划分与公开签名

### 1.0 `trpg/st/__init__.py`

只做再导出（保持 `from trpg.st import CharacterCard, render, scan, assemble_system` 可用）：
`card.CharacterCard, Persona, card_from_companion, card_from_player`；
`macros.MacroContext, render`；`worldinfo.WLEntry, WorldInfo, WIResult, wi_from_book,
engine_worldinfo, scan, merge`；`assemble.Injection, assemble_system, build_context_block,
inject_at_depth, fit_to_budget, split_examples`。

### 1.1 `trpg/st/card.py` —— 角色卡与用户 persona

字段子集 = 〔ST 规格 §2.2〕中"进提示词"的字段 + 存放扩展字段的 `extensions`。
不引入的字段：`creator_notes/tags/creator/character_version/assets/…`（spec 明言不进 prompt）。

```python
@dataclass
class CharacterCard:
    name: str
    description: str = ""            # 常驻：设定/身世/外观（§2.2）
    personality: str = ""            # 常驻：性格摘要
    scenario: str = ""               # 常驻：情境；AI PC 下由引擎每场景重写（§11 #1）
    first_mes: str = ""              # 一次性：开场白
    mes_example: str = ""            # 可挤出：以 <START> 分块（§3.1 三类生命周期）
    system_prompt: str = ""          # Main Prompt 覆盖；""=用默认（§3.4 覆盖语义）
    post_history_instructions: str = ""  # PHI 覆盖；""=用默认；位于历史之后（§3.4）
    alternate_greetings: list[str] = field(default_factory=list)  # 开场 swipe 备选（P2 消费）
    talkativeness: float = 0.5       # §12：新角色默认 0.5；P2 概率激活用
    depth_prompt: str = ""           # 角色注释 @D（§8）；存于 extensions 语义的平铺字段
    book: "WorldInfo | None" = None  # character_book（P1 消费）
    extensions: dict = field(default_factory=dict)  # 未知键保存区（spec：不得销毁）

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "CharacterCard": ...

def card_from_companion(comp: dict) -> CharacterCard:
    """模组同伴 dict → 卡。映射：persona→description、voice→personality、
    name→name（module_data.py:55-97 的字段形状）；未提字段留空。"""

def card_from_player(ch: Character, *, description: str = "", personality: str = "",
                     system_prompt: str = "", post_history_instructions: str = "",
                     mes_example: str = "", talkativeness: float = 0.5) -> CharacterCard:
    """玩家 Character → 卡。name 取 ch.name；description/personality 来自建卡表单
    （空则用引擎兜底文案：职业·街头身份 + TACTICS 风格摘要）。"""

@dataclass
class Persona:
    """用户 persona（§4）。AI PC 模式下 DM/同伴上下文中的"玩家侧"注入由它承担。"""
    name: str
    description: str = ""
    position: str = "in_prompt"      # none / in_prompt / at_depth（枚举取 §4 三种）
    depth: int = 2                   # at_depth 默认（personas.js DEFAULT_DEPTH=2）
    role: str = "system"

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict | None) -> "Persona": ...  # None → 匿名空 persona
```

### 1.2 `trpg/st/macros.py` —— 宏引擎

语法遵〔ST 规格 §6.1〕：`{{name}}` 大小写不敏感；参数 `::`；嵌套**先内后外**（最深 2 层，
超出按字面保留）；`\{\{text\}\}` 转义；未识别宏原样保留（不静默删除，便于发现问题）。

```python
@dataclass
class MacroContext:
    user: str                        # 玩家槽位名（{{user}}；AI PC 下=玩家角色名）
    char: str                        # 当前发言角色名（{{char}}）
    persona: str = ""                # persona 描述（{{persona}}）
    party: str = ""                  # 扩展：party_snapshot()（{{party}}）
    scene: str = ""                  # 扩展：场景 id:地点（{{scene}}）
    flags: str = ""                  # 扩展：旗标 dict 渲染（{{flags}}）
    extra: dict[str, str] = field(default_factory=dict)  # 任意补充键

def render(text: str, ctx: MacroContext, *, rng: random.Random | None = None,
           now: datetime | None = None) -> str:
    """支持的宏（最小集，〔ST 规格 §6.2〕）：
    - 名字/参与者：{{user}} {{char}} {{party}}（旧写法 <USER>/<BOT>/<CHAR> 一并替换）
    - persona：{{persona}}；字段：{{description}} {{personality}} {{scenario}}（取 ctx.extra）
    - 时间：{{time}} {{isotime}}(HH:mm) {{isodate}}(YYYY-MM-DD)
    - 随机：{{random::a::b::…}}（每次重摇，rng）、{{pick::a::b::…}}（按 (user,char,位置) 散列稳定）、
            {{roll::1d10+3}}（**引擎桥**：rules.dice.roll(rng, expr)，rng=对局 rng）
    - 快照：{{party}} {{scene}} {{flags}}
    - 实用：{{newline}} {{space}} {{noop}}
    """
```

`{{roll}}` 的审计说明：宏替换发生在**组装期**，替换结果随每回合 context 消息进入
`session.log_message`（loop.py:170-173），保持"模型可见 ⟺ 已记录"（events.py:1-5）。
宏骰只是叙事佐料（如"掷出 7 的先攻直觉"），**不进入任何结算路径**；结算骰一律在
工具/引擎内完成。所有宏展开无 token 成本（纯字符串）。

### 1.3 `trpg/st/worldinfo.py` —— 世界书（P1）

数据模型与激活算法照〔ST 规格 §5.1/§5.2/§5.3〕实现，字段取子集：

```python
@dataclass
class WLEntry:
    keys: list[str]
    content: str
    secondary_keys: list[str] = field(default_factory=list)
    logic: str = "and_any"           # and_any / and_all / not_any / not_all（§5.1 枚举）
    constant: bool = False           # 🔵 恒触发，预算内最先获得插入权
    enabled: bool = True
    position: str = "before_char"    # before_char / after_char / at_depth
    depth: int = 4                   # 仅 at_depth（§12：DEFAULT_DEPTH=4）
    role: str = "system"
    order: int = 100                 # 越大越靠近上下文末尾（ST 语义，§5.1）
    priority: int = 100              # 预算吃紧时数值小者先丢弃（V2 卡字段）
    case_sensitive: bool = False
    probability: int = 100
    comment: str = ""

@dataclass
class WorldInfo:
    entries: list[WLEntry] = field(default_factory=list)
    scan_depth: int = 4              # 扫描最近 N 条"桌面文本"
    token_budget: int = 1024         # WI 独立预算（§7 第 5 条：与主历史分账）
    recursive_scanning: bool = False # P1 先支持 0/1 轮

@dataclass
class WIResult:
    before: str                      # position=before_char，按 order 升序拼接
    after: str                       # position=after_char
    at_depth: list[Injection]        # 转为 assemble.Injection
    overflowed: bool
    activated: list[str]             # 命中的 comment（UI 可展示）

def wi_from_book(book: dict) -> WorldInfo:
    """character_book / 世界书 JSON（V2 卡 shapes，§2.3）→ WorldInfo。
    insertion_order 语义固定为 ST 内部方向：数值大 = 靠后 = 保留优先；
    导入 V2 卡时把 spec 的 insertion_order 取反（-order）换算，并在 docstring 注明方向。"""

def engine_worldinfo(state) -> WorldInfo:
    """引擎状态 WI 化（§11 #6）：
    - 当前场景：constant 条目（id/地点/intro 摘要，order 高）；
    - 剧情旗标：每条旗标一个 selective 条目（键=旗标键与其值），内容=值描述；
    - 模组 NPC：键=NPC 名，content=desc（loader.npcs）；
    - 场景 checks 未做项：键=技能中文名。
    预算内全部 constant 场景条目最先入选（§5.2 丢弃顺序第 1 条）。"""

def scan(wi: WorldInfo, window: str, *, budget_tokens: int, rng: random.Random) -> WIResult:
    """激活算法（§5.2）：constant → 关键词（selectiveLogic 结合副键）→ probability
    → 预算（estimate_tokens 计量，超限即停、不回删）→ 分桶。
    匹配规则：ASCII 键全词匹配（§5.1 match_whole_words 默认开）；**CJK 键一律子串匹配**
    （§12 明言全词匹配对中文有害——中文实现强制关闭全词）。"""

def merge(*books: WorldInfo) -> WorldInfo:
    """多来源合并（§4 次序：Chat → Persona → Character/Global）。同键（keys 集合相等）
    时先传入者（角色书）完全优先（§5.3）。"""
```

P1 接线：`AIPCController` 每回合把 `engine_worldinfo(state)` 与玩家 `card.book` 合并，
`window` = 桌面最近文本（table_log + 本回合输入）；`before/after` 拼进每回合上下文块，
`at_depth` 进 `Injection` 注册表。**DM 同样可用**：`dm_turn` 的 context 块追加
`scan(...).before/after`（§11 #6"DM 与 PC 上下文都可用"）；DM system 中的
`module.handbook()`（loader.py:191-204）保持不动，P1 不改变 DM 行为基线，仅在
上下文块"加餐"，关闭 AI PC 时该加餐同样短路（世界书属于 st，随 aipc 门控）。

### 1.4 `trpg/st/assemble.py` —— 提示词组装与按深度注入、预算裁剪

```python
@dataclass(frozen=True, order=True)
class Injection:
    depth: int                       # 0=最新消息之后；越大越靠前（§8 实现要点）
    role: str = "system"             # system / user / assistant
    order: int = 100                 # 同 depth+role 内小者先
    text: str = field(compare=False)

def split_examples(mes_example: str) -> list[str]:
    """按 <START> 分块（§2.2）；标记本身不进提示词。"""

def assemble_system(card: CharacterCard, persona: Persona | None, *,
                    wi_before: str = "", wi_after: str = "",
                    main_default: str = "", role_lines: str = "",
                    template_vars: dict[str, str] | None = None) -> str:
    """把卡渲染成**单条 system**，顺序照〔ST 规格 §3.2〕Prompt Manager 默认序：
    main（card.system_prompt 或 main_default，支持 {{original}} 展开=main_default）
    → wiBefore → personaDescription → charDescription → charPersonality
    → scenario（"Scenario: …"式前缀）→ wiAfter → dialogueExamples（分块拼接）
    全文先过 macros.render。返回的字符串由调用方填进 Agent.system_prompt。
    main_default 含 str.format 占位符时必须由调用方经 template_vars 预格式化：
    PC_SYSTEM_TEMPLATE 实有 9 个占位符 name/race_name/klass_name/level/persona/
    voice/weapons/slots_hint/tactics（agents.py:50-74）——本函数先
    main_default.format(**template_vars)（缺键 KeyError，构造期 fail-fast），
    format 产物与卡文本再统一过 macros.render；直传裸模板必然 KeyError。"""

def build_context_block(*, lead: str, scene: str, party: str, flags: str,
                        table: str = "", last_narration: str = "",
                        injections: list[Injection] | None = None,
                        persona_note: str = "", phi: str = "") -> list[dict]:
    """每回合 user 上下文块（沿用 dm_turn 现有形状 session.py:469-474，向后兼容）：
    【场景】【队伍】【旗标】→【桌上插话】→【世界书】(P1)→【AN/注入】(depth 降序、
    role 次序 User→Assistant→System、order 升序，§8)→【{lead}】。
    phi 非空时作为**最后一条** user 消息单独返回（PHI 语义=历史之后、最后发送，§3.2 #12）。
    返回值即 loop.run(context_messages=…)，天然进事件日志。"""

def inject_at_depth(messages: list[dict], injections: list[Injection]) -> list[dict]:
    """真·按深度注入（P1 接线 loop 时启用；P0 由 build_context_block 以"depth 即块内
    排序键"的退化形式消费）。depth 从消息列表**末尾**数起：depth=0 插在最后一条之后；
    同深度 role 次序 User→Assistant→System、order 小者先（§3.2）。"""

def fit_to_budget(sections: list[tuple[str, str]], budget_tokens: int) -> list[tuple[str, str]]:
    """组装期预算裁剪（§7 牺牲顺序）。sections=[(标记, 文本), …]：
    1) 永不裁：main/常驻字段/强制 nudge；
    2) 先整块挤 dialogue_examples（逐块）；
    3) 再丢最旧的上下文行；
    计量用 harness.loop.estimate_tokens（loop.py:82-85，CJK≈1/字）。"""
```

**组装的两层分工**（关键取舍）：
- **静态层（system）**：卡常驻字段 + persona + WI before/after + 对话示例 →
  `assemble_system` 一次性产出，填进 `Agent.system_prompt`。场景变化时由
  `AIPCController.refresh()`（§2.2）用 `dataclasses.replace(agent, system_prompt=…)`
  重建（同伴侧已有同款先例 social.py:222-225）。system 不进事件日志（loop.py:203-206
  现状即如此），卡文本属于"人格"，不需要逐回合重放。
- **动态层（每回合 context）**：场景/队伍/旗标快照、桌上插话、AN、PHI、WI@D →
  `build_context_block` 产出 user 消息，经 `loop.run(context_messages=…)` 日志化。
  这保持了 loop 协议零改动、且"模型可见 ⟺ 已记录"不变量自动成立。

---

## 2. AI PC 控制器：嵌入 session.py 的每个人类交互点

### 2.1 `trpg/game/aipc.py` —— AIPCController

```python
class AIPCController:
    """玩家角色的 AI 代打编排器。持有独立 harness 会话（战斗/探索/桌边共用记忆）。"""

    def __init__(self, session: GameSession, loop: DefaultAgentLoop,
                 card: CharacterCard, persona: Persona,
                 model: str, *, max_steps: int = 4): ...

    @classmethod
    def attach(cls, session: GameSession, loop: DefaultAgentLoop, model: str,
               card: CharacterCard, persona: Persona) -> "AIPCController | None":
        """构建并挂载：注册 agent 到 session.pc_agents[玩家名]（第 4 名同伴）；
        会话复用 session.pc_session(玩家名)（id=pc:{玩家名}，session.py:86-89）——
        战斗（§2.5 的 _pc_agent_turn 复用链）、探索（§2.4）、桌边（social.py:137）
        三处共用同一份记忆，不另建第二会话；
        建专用 registry（say / explore_intent / explore_act，见 §2.3）。
        玩家名与任一同伴名冲突（state.find_member 语义）时返回 None（不抛异常），
        调用方（build_session / main.py）检测 None 后降级为不启用并提示。
        **Agent.tool_names 必须为 []**（registry.py:53-56：空列表=注册表全部工具）：
        探索 registry 的 say/explore_intent/explore_act 与战斗 registry 的
        say/combat_action/explore_intent 全量下发；绝不能沿用 make_pc_agent 的
        tool_names=["say","combat_action","explore_intent"]（agents.py:128）——
        loop.py:184 的 registry.schemas(tool_names) 按该列表过滤，explore_act 会被
        静默吞掉、永不出现在发给模型的 schema 里。
        注意区分两个标识：Agent.agent_id = f"aipc:{玩家名}"（仅作 meter/日志标签，
        §7.2）；Session id = pc:{玩家名}（记忆容器，经 pc_session 创建）。"""

    agent: Agent                                   # 动态重建（refresh）
    # ---------------- 交互点接管
    def explore_beat(self) -> bool:
        """ai_explore_turns 的 AI 分支（§2.4）。返回 False=LLM 不可用，本轮交还人类。"""
    def combat_turn(self, enc: Encounter) -> None:
        """player_combat_turn 的 AI 分支：直接走 session.companion_turn(enc, 玩家)（§2.5）。"""
    def poll_command(self, timeout: float = 0.0) -> "str | None":
        """人类旁听指令（非阻塞）。经 getattr(self.ui, "poll_command", None) 防御转发：
        WebUI 有实现（§2.8）；ConsoleUI/AutoUI 无此方法（cli.py:16-151）→ 恒返回 None
        ——终端模式无旁听指令，/退出 走 Ctrl+C 既有路径（main.py:208-212），
        ui/cli.py 零改动。"""
    # ---------------- 供 social.py / tools.py 查询
    def reaction_speakers(self) -> list[Character]:     # 全队存活 ∩ pc_agents（§2.6）
    def talkativeness_of(self, ch: Character) -> float: # P2
    # ---------------- 内部
    def refresh(self) -> None:                     # enter_scene 后重写 scenario 并重建 agent
    def _registry(self, *, explore: bool) -> ToolRegistry:
    def _task(self, *, combat: bool = False, enc=None) -> str:
        """任务行：复用 _pc_agent_turn 的 task 形状（session.py:1103-1107）+
        build_context_block 的快照块；PHI（card.post_history_instructions）经宏渲染后
        作为最后一条 user 消息。"""
```

系统提示词来源：`assemble_system(card, persona, main_default=PC_SYSTEM_TEMPLATE 模板)`。
现有 `PC_SYSTEM_TEMPLATE`（agents.py:50-74）保留为 `main_default`；卡上的
`system_prompt` 覆盖它，`{{original}}` 展开为模板全文（§3.4）。插值顺序固定为
**先 str.format、后宏渲染**：`macros.render` 只匹配双花括号 `{{…}}`，模板里的单花括号
str.format 占位符 `{name}/{persona}/{voice}` 不会被它命中——因此先用 str.format 填充
模板占位符，format 产物与卡上 `system_prompt` 覆盖文本再整体过一次 `macros.render`
展开卡文本里的 `{{…}}` 宏。两套语法各司其职，不做模板转换。

### 2.2 场景刷新（enter_scene 钩子）

`session.enter_scene`（session.py:127-155）在 autosave 前加一行：
`if self.aipc: self.aipc.refresh()`。
`refresh()`：`card.scenario = f"{scene['location']}：{scene['intro']}"`（经宏渲染）→
`self.agent = dataclasses.replace(self.agent, system_prompt=assemble_system(...))` →
`session.pc_agents[玩家名] = self.agent`。

### 2.3 AI PC 专属工具（tools.py 新增 `register_aipc_tools`）

在 `GameTools` 上加方法（tools.py，`register_pc_tools` 之后）：

```python
def register_aipc_tools(self, registry: ToolRegistry, who: Character) -> None:
    # say 不能复用 register_pc_tools 的原实现（tools.py:183-188 只调 ui.companion_line、
    # 不写 table_log）——**探索期** AI PC 的话必须进桌面账本，DM 的 _digest_table 与
    # 同伴的 _recent 才"听得见"（战斗期仍复用同伴 say、不进 table_log，见 §2.5）：
    registry.register(
        "say", f"以 {who.name} 的口吻说一句话（中文，符合人设）。to=对谁说（可空）。",
        {"type": "object",
         "properties": {"text": {"type": "string"}, "to": {"type": "string"}},
         "required": ["text"]},
        lambda text, to="": self._aipc_say(who, text, to))
    registry.register("explore_intent", …, …)      # 复用（tools.py:207-211）
    registry.register("explore_act",  # 非交互探索行动面（替代人类斜杠指令）
        "执行一次探索期行动。kind ∈ rest_short/rest_long/drink_potion/buy/save/state/"
        "inventory；buy 需 item=商品 key（healing_potion/potion_greater_healing/"
        "healer_kit/torch）。",
        {"type": "object",
         "properties": {"kind": {"type": "string"}, "item": {"type": "string"}},
         "required": ["kind"]},
        self._tool_explore_act)

def _aipc_say(self, who: Character, text: str, to: str = "") -> dict:
    self.ui.companion_line(who.name, text)
    self.harness_session.table_log.append(         # 行结构与 social.py:231-234 对齐
        {"who": who.name, "text": str(text), "to": str(to or ""),
         "interrupt": False, "kind": "table"})
    return {"ok": True, "note": "已说出口，桌边都听见了。"}

def _tool_explore_act(self, kind: str, item: str = "") -> dict:
    # rest_day → 休整一天（每个完整休息日恢复等于体格的 PW，须已被稳定）
    # stabilize/first_aid(target,index) → Encounter.stabilize_action / first_aid_fix
    # repair_armor → 花欧元把护甲 SP 修回满值；buy → buy_good(item)（仅 scene.shop）
    # save → G.save_game(...)；state/inventory → ui.raw(...)。
    # 全部毫秒级完成；不在 ToolTimeoutGuard.INTERACTIVE_TOOLS（loop.py:40）内，无超时风险。
```

**非交互购买**：`open_shop()`（session.py:1311-1329）是 `ui.choose` 的 while 菜单——
AI 在工具分发内调它会被 10s 工具超时砍断（loop.py:53-56），人类旁听调它则阻塞游戏线程，
两者都不可接受。把定价/扣款/库存逻辑从 `open_shop` 抽成
`GameSession.buy_good(key) -> dict`（校验 scene.shop、SHOP_GOODS 价目
（session.py:35-40）、欧元是否足够，写入库存、autosave、返回 {ok/note}）；
`open_shop` 的菜单循环改为调用 `buy_good`（人类路径行为不变）；AI 的
`explore_act(kind="buy", item=…)` 只走 `buy_good`。

`_tool_skill_check/_tool_opposed_check`（tools.py:97-117）不新增玩家分支——掷骰本身仍是引擎
结算，AI PC 不参与（铁律）；只把 `_ask_player_roll`（tools.py:90-95）的人类按钮门控
改为：`if member is self.state.player and hasattr(self.ui, "ask_roll") \
and not (self.harness_session and self.harness_session.aipc):`。
AI PC 模式下玩家的技能检定与对抗检定与同伴一样自动落骰，`ui.check_line` 照常播骰动画。

### 2.4 探索 free_input（session.py:411-460 `ai_explore_turns`）

改造为三段：人类输入 / 旁听指令 / AI beat，复用同一条指令分发：

```python
def ai_explore_turns(self, scene: dict) -> str:
    self.ui.system_line("输入行动或对白；/状态 /背包 /义体 /急救 /稳定 /休整 /购买 /存档 /退出 为指令。")
    while True:
        if self.aipc is not None:
            cmd = self.aipc.poll_command(0.4)      # 人类旁听指令（非阻塞，§2.8）
            if cmd is not None:
                if cmd == "/退出":
                    return "quit"
                self._handle_explore_command(cmd)  # 其余斜杠指令（从现有 if 链抽出）
                continue
            if self.aipc.explore_beat():           # AI 代打本节拍
                ended = self._post_beat()          # 战斗触发/收尾（现 L449-460 逻辑抽出）
                if ended:                          # "end" 必须传播：session.py:459-460 的
                    return "end"                   # 最终场景战斗完结 → epilogue 才能触发
                continue
            # explore_beat 返回 False：LLM 故障 → 本轮交还人类（优雅降级，§7.1）
            self.ui.system_line("（AI 代打暂不可用，本轮请亲自输入行动。）")
        text = self.ui.free_input("\n你> ")        # ← 原路径，AI PC 关闭/降级时才到达
        …（现有指令分发与 social.beat/dm_turn 分支保持原文不动）…
```

`_post_beat()`：节拍/战斗收尾，**内部按 `self.state.scene_id` 重取 scene**
（session.py:454 语义——战斗可能已推进场景；不得使用调用方传入的旧 scene 变量，
否则 AI 模式下 "end" 永远检测不到、epilogue 不触发），返回 "end" 或 None。

`explore_beat()` 内部（aipc.py）：
1. `reg = self._registry(explore=True)`（say / explore_intent / explore_act）；
2. task = `build_context_block(lead="以 {name} 的身份行动：先考虑要不要说话，再行动。"
   "用 explore_intent 声明你的行动意图（引擎会交给 DM 推进），闲聊用 say，"
   "休整、急救、购买等用 explore_act。", scene=…, party=…, flags=…, table=_digest_table 前的 table_log,
   last_narration=self._last_narration)`；
3. `result = loop.run(agent, aipc_session, task, context_messages=…, registry=reg,
   guard_dup=DuplicateCallGuard())` → `session._record_usage(agent, result)`（记账，§7.2）；
4. 解释 trace（四分支，自上而下匹配）：
   - 有 `explore_intent(text)`（取最后一个）→ 记入 `self._last_narration` 后触发
     `social.beat(text)`（AI PC 开启时 social 必非 None）；本节拍内的 say 行**已由工具
     记入 table_log**（§2.3 `_aipc_say`），DM 的 `_digest_table`（session.py:484-494）
     会把它们与意图一并消化，同伴的 `_recent`（social.py:261-269）同样可见；
   - 无 intent 但有 say → 取最后一句 say 的台词作为节拍输入：`social.beat(台词)`
     （人类模式"说话即输入"，AI 模式语义对齐：同伴可反应、DM/NPC 会接话）；
   - 无 intent/say 但有**面向世界**的 explore_act（rest_day/stabilize/first_aid/repair_armor/buy）
     → `dm_turn(f"（{玩家名} 刚完成了{动作描述}。）")` 让世界回应；
   - 全程无工具（纯文本收束），或只有 save/state/inventory 这类纯 UI 行动 →
     以 `dm_turn(f"（{玩家名} 按兵不动。）")` 兜底推进，防止无限空转；
5. 全程 `try/except RuntimeError` → 返回 False。

`_digest_table`（session.py:484-494）语义不变；AI PC 的桌上行同样进 `table_log`，
由 DM 消化——"AI PC 抢话 → NPC 听见"链路免费获得。

### 2.5 战斗回合（session.py:680-703 `player_combat_turn`）

```python
def player_combat_turn(self, enc: Encounter) -> None:
    if self.aipc is not None:
        self.aipc.combat_turn(enc)     # 内部 = self.companion_turn(enc, self.state.player)
        return
    …原方法体不动…
```

复用链条：`companion_turn`（session.py:1081-1090）→ `pc_agents.get(玩家名)` 命中 →
`_pc_agent_turn`（session.py:1092-1114）：为玩家单独 `register_pc_tools(reg, pc)`
+ 跑循环 + `RuntimeError` 时返回 False → `companion_turn` 落到
`_scripted_pc_turn`（session.py:1116-1258，按职业的启发式，对玩家职业天然适用）。
`_pc_agent_turn` 的返回条件 `any(t["name"]=="combat_action")` 保证"只喊不打"时启发式
兜底出手——玩家永远不会空过回合。

`_pc_agent_turn` 的 task（session.py:1103-1107）已是 group-nudge 语义
（"【第N轮 · X 的回合】…想喊话就先 say…再用 combat_action"），P0 原样复用；
差异仅在 agent 的 system（卡渲染版）与会话（`pc_session(玩家名)`，与桌边/探索同份记忆）。
战斗喊话沿用同伴 say（tools.py:183-188，只渲染、不进 table_log）——与同伴现状一致，
战斗中台词的可见性不变；table_log 记账仅探索期（§2.3）。

### 2.6 反应（session.py:546-557 `_reaction_prompt` + social.py）

**战斗反应**：`_reaction_prompt` 玩家分支加门控——
`if target is self.state.player: if self.aipc is not None: return None`。
语义如实声明：AI 模式下 `enc.reaction_prompt` 恒被赋值（session.py:537），而
`Encounter._ask_reaction` 对可调用 prompt 直接
`return bool(self.reaction_prompt(target, ctx))`（combat.py:1420-1423，bool(None)=False），
其后的引擎自动规则分支（combat.py:1425-1434）**不可达**——即现行 AI 模式里同伴本就
从不在战斗中自动触发护盾术/灵活闪避等反应，AI PC 玩家同样**恒不触发**。`return None`
保证的只是"不弹人类菜单"，与同伴现状一致；不是"引擎自动规则接管"。若要让 None 落入
引擎自动规则须改 combat.py:1421 对 None 的处理——那会同时改变同伴行为，超出本设计
声称的零影响面，不做。P0 也不为反应骰叫 LLM（一次反应 = 一次 10s 级调用，拖慢战斗
节奏且收益低）；P2 可选 1-step 循环让 AI PC 决策。

**桌边反应**（social.py:102-133 `reaction_wave`）：speakers 过滤从
`s.state.companions`（social.py:106）改为
`[c for c in s.state.party() if c.hp_cur > 0 and "dead" not in c.conditions and c.name in s.pc_agents]`。
AI PC 开启时玩家名在 `pc_agents`（attach 注册）→ 玩家自动进抢话波/辩论；
**关闭时玩家不在 `pc_agents`，过滤结果与现在逐位相等**（party() = [player]+companions，
player 被 `c.name in s.pc_agents` 剔除）——这是零行为变化论证的关键一步。
玩家行渲染：`run_debate`/`reaction_wave` 的 `ui.companion_line(line["who"], …)` 对玩家
照常可用（前端按 who 渲染头像），P2 再加独立样式。`beat()` 的 trigger 文案
（social.py:72 "玩家（…）刚开口说话"）在 AI PC 模式下改为
"玩家角色（{名}）刚说完话"——由 controller 传入 beat 的可选参数实现，默认值不变。

辩论：`run_debate` 邀请侧（social.py:157-165，`name in s.pc_agents`）自动纳入玩家，
但**邀请≠能发言**——`_debate_round` 的
`by_name = {c.name: c for c in self.session.state.companions}`（social.py:198）只映射
同伴，submit 仅对 `name in by_name` 发牌（social.py:201-204）：不改它，玩家进 active
后每轮 results 为空 → `conceded`（social.py:180-181）恒真、第一轮即被"淘汰"，
AI PC 会开启一场自己永远无法发言的辩论。必须把 by_name 改为
`{c.name: c for c in self.session.state.party() if c.name in s.pc_agents}`（§6）；
`_pc_debate_turn` 与 `_social_agent` 按 `pc_agents[pc.name]` 取 agent
（social.py:137,222-225），玩家与同伴共用，无需再改。

### 2.7 技能/对抗检定掷骰（tools.py:90-117）

见 §2.3：`_ask_player_roll` 门控后，AI PC 模式下 DM 发起的 `skill_check(玩家,…)`/
`opposed_check(玩家,…)` 自动落骰（引擎结算不变），不再阻塞等待人类点击。
webui 的 `ask_roll`（webui.py:134-140）在 AI PC 对局中不会被调用；人类仍能从骰子动画
（`ui.check_line`）看到结果。其余 UI 无 `ask_roll` 属性的路径本就自动落骰。

> 按深度注入接线取舍：P0 的注入全部经 `build_context_block`（= depth 0 的 user 注入，
> 现状最接近物，〔ST 规格 §11 #8〕）；`inject_at_depth` 真插桩需要给
> **（1.7.0 已落地）** `DefaultAgentLoop.run` 加可选 kwarg（如 `post_build: Callable[[list[dict]], list[dict]]`，
> loop.py:160-166 追加参数、默认 None，向后兼容）——列入 P1，届时 AN/WI@D 才能落在
> 历史中段。

### 2.8 webui 阻塞应答 → AI PC agent 应答的替代矩阵

| 阻塞点（现状） | AI PC 模式下的替代 |
|---|---|
| `ui.free_input`（session.py:414，探索主输入） | `AIPCController.explore_beat()`（§2.4） |
| `ui.choose` 战斗菜单（session.py:689/699 及全部子菜单 L750-1027） | `companion_turn → _pc_agent_turn → combat_action 工具`（§2.5） |
| `ui.free_input` 战斗说话（session.py:869——实为自由输入，非 choose） | agent 回合内 `say` 工具（PC_SYSTEM_TEMPLATE 已提示，agents.py:71） |
| `ui.choose` 攻击目标选择（session.py:1071 free_input） | 不触达（AI PC 走 `combat_action` 的 `target` 字段） |
| `open_shop` 的 `ui.choose` 菜单（session.py:1311-1329；AI 的 explore_act("buy") 与人类旁听 /购买 都会触及） | AI：`explore_act(kind="buy", item=…)` 只走非交互 `buy_good`（§2.3），毫秒级、无工具超时；人类旁听 /购买：`system_line` 列货单与价目（只读、不阻塞），购买决策归 AI PC |
| `ui.choose` 反应询问（闪避子弹/借机攻击，session.py:554） | 恒不触发询问（`return None`，与同伴现状一致——RED 的反应由引擎按规则自动结算，不再回头问玩家，§2.6） |
| `ui.ask_roll` 掷骰按钮（tools.py:94 → webui.py:134） | 门控跳过，自动落骰（§2.7） |
| `ui.choose` 建卡表单（挑选预设角色） | 不触达（AI PC 开局直接采用模组 `pregens` 里的预设角色卡） |
| `ui.choose` 改进点（IP）花费菜单（session.py:184-283） | 默认策略自动花完（先升职业特殊能力、再补主用技能，零 LLM） |
| `ui.free_input`（webui.py:142 人类唯一自由输入） | 保留为**旁听指令**通道（见下） |

**已知能力缺口（如实声明）**：RED 的动作面已经把救援能力交给 LLM——
`combat_action` 的 `stabilize(target)`（技术 + 急救/医疗救援对抗难度值，把濒死者的
PW 拉回 1）与 `first_aid(target,index)`（急救固定一处致命伤，当天不再受影响）都接收
target，`flee`（脱离战斗）也在枚举里。仍然缺的是 `use_item`：它不接收 target
（tools.py 只调 `enc.use_item(pc, item)`），因此"把急救包/医疗包递给倒地队友"
仍需人类战斗菜单操作（引擎能力存在：`enc.use_item(c, item, target)`）。
AI PC（与同伴现状完全相同）在这条路径上只能依赖启发式兜底
（`_scripted_pc_turn` 的救援分支自动执行）。P0 不扩 combat_action schema——
那会改变 AI 模式下同伴收到的工具面，破坏"开关关闭零变化"；
P2 可在 AI PC 专属 registry 上 dispose 原 `combat_action` 并注册加宽版
（`use_item` 加 target），仅 AI PC 生效。

**旁听指令**（AI PC 模式下人类保留的唯一入口，P0 必做——/退出 与降级需要它）：
- `WebUI` 新增：`pending` 之外维护 `self._command: str | None`（锁内读写）；
  `post_command(text)` 供 `/api/answer` 复用（`kind=="command"` 时写入 `_command`）；
  `poll_command(timeout=0.0) -> str | None`：取走并清空 `_command`。
- 探索循环每节拍 `poll_command(0.4)`（§2.4）；前端在 aipc 对局中把 ask-form 常驻显示为
  "旁听指令（/状态 /保存 /退出 …）"，提交后走现有 `POST /api/answer {text}`，
  服务端按 pending 类型分派（server.py:574-579 微调）。
- 战斗进行中不 poll（回合编排是同步循环）；人类中断战斗只能用既有的"放弃本局"
  （`#btn-reset` → `/api/reset` → `ui.abort()`，server.py:250-256）。

### 2.9 改进点（IP）花费（session.py `resolve_pending_choices`）

```python
def _resolve_member_ip(self, c: Character) -> None:
    if c.ip <= 0 or c.is_dead:
        return
    if c is not self.state.player or self.aipc is not None:
        self._auto_spend_ip(c)         # 同伴 / AI 代打的玩家：默认策略
        return
    self._ask_ip_choice(c)             # 人类玩家：ui.choose 菜单
```

RED 没有等级系统，成长只有两条路：技能（20 × 新等级，×2 技能翻倍）与
职业特殊能力（60 × 新等级），属性不可提升。默认策略（`_auto_spend_ip`）无任何 UI 调用：
先把职业能力顶上去，再补主用技能（枪械/近战/闪避/潜行/感知/急救/医疗救援/运动/说服）。
AI PC 的花费同样**静默**，人类从侧栏队伍快照（技能与职业能力变化）感知。
P2：让 AI PC 在卡人设口吻下挑选优先提升的技能（1-step 循环 +
`spend_ip` 工具，结果仍由引擎 `raise_skill`/`raise_role` 落账，数值面零新增）。

**既有双路径不一致的收敛（卡面数值补差）**：某些创建期补差此前只在交互路径生效，
AI PC 走默认策略时不补，两条路径结果不一致。P0 把补差抽成公共 helper
（`_apply_*_fixup(c)`），交互分支与 aipc 分支共用（交互路径=代码平移、行为不变；
同伴仍直接走默认策略、不经过该函数，行为不变）。

---

## 3. 数据模型与存档扩展

### 3.1 GameState（state.py:14-24）

新增两个字段（全部带默认值，构造调用方零改动）：

```python
self.aipc_enabled: bool = False           # 本局是否 AI 代打（构建期快照，局内只读）
self.persona: Persona | None = None       # 人类玩家 persona（AI PC 关闭时也可用于 DM 上下文）
self.aipc_card: CharacterCard | None = None  # 玩家角色卡（建卡/读档时填充）
```

### 3.2 存档（state.py:93-122）

`save_game` 追加一个顶层键（旧档无此键 → 读档默认关闭，完全兼容）：

```python
data["aipc"] = {
    "enabled": state.aipc_enabled,
    "persona": state.persona.to_dict() if state.persona else None,
    "card": state.aipc_card.to_dict() if state.aipc_card else None,
}
```

`load_game` 对称读取：`raw = data.get("aipc") or {}` →
`st.aipc_enabled = bool(raw.get("enabled"))`；
`st.persona = Persona.from_dict(raw.get("persona"))`（None 安全）；
`st.aipc_card = CharacterCard.from_dict(raw["card"]) if raw.get("card") else None`。
**约束**：`aipc` 块内没有任何密钥字段（卡片/persona 是用户叙事文本，非敏感；
密钥仍只走环境变量与运行时内存，config.py:1-2 的铁律不变）。

续档语义：`AppState.start(spec["continue"])`（server.py:201-210）读出 aipc 块后，
`build_session` 据其 enabled 决定是否挂控制器（LLM 未配置时强制关，§7.1）；
开关在局内不可切换（与 LLM 设置"仅对新开对局生效"同语义，app.js:221 既有提示）。

### 3.3 角色卡字段从哪来

- **Web 建卡**：`build_character`（web/charbuild.py:23-59）从 spec 透传四个可选叙事字段
  `description / personality / mes_example / system_prompt`（各 ≤2000 字符，纯文本），
  `card_from_player(ch, …)` 组卡；AI PC 开关 `aipc_enabled` 亦随 start spec 传入。
- **终端建卡**：P0 不加交互项（`create_player_console` 不动），终端玩家用环境变量
  `TRPG_AIPC=1` 开启 + 兜底卡（card_from_player 的默认文案：职业·街头身份 + TACTICS）
  （终端模式无旁听指令，/退出 走 Ctrl+C，见 §2.1 poll_command）。
- **同伴**：`card_from_companion(module.companions[i])` 仅 P1 世界书挂载时消费；
  P0 同伴提示词构造路径不动（make_pc_agent，agents.py:117-129 原样保留）。

---

## 4. 设置页与前端改动点

### 4.1 后端（web/server.py）

- **新增独立端点 `GET/POST /api/settings/aipc`**（body
  `{"enabled": bool, "persona": str}`，persona ≤1000 字符）：写入
  `self.aipc_prefs` 并持久化到 `saves/llm_config.json` 的 `"aipc"` 键
  （server.py:36 非敏感文件，无密钥）。**不走 `apply_llm_settings`**——它在缺密钥时
  提前 return 且不落盘（server.py:165-168），aipc 偏好若挂在它下面、无密钥状态下
  永远存不进去；独立端点使 `apply_llm_settings` 与 `_resolve_llm_config`
  （server.py:131-147）完全零改动。`llm_settings_view`（server.py:182-191）
  返回值增加 `"aipc"` 字段。
- `llm_settings_view`（server.py:182-191）：返回值增加 `"aipc": {"enabled":…, "persona":…}`。
- `start`（server.py:194-248）：新建时把 `spec.get("aipc_enabled")`、建卡卡字段传给
  `build_character`/`GameState`；续档时以存档 `aipc.enabled` 为准。
- `build_session`（server.py:317-341）：签名不变；llm_cfg 非 None 且
  `state.aipc_enabled` 时——
  ```python
  card = state.aipc_card or card_from_player(state.player)
  persona = state.persona or Persona(name=state.player.name)
  aipc = AIPCController.attach(session, loop, llm_cfg.model, card, persona)
  if aipc is None:  # 玩家名与同伴冲突（attach 返回 None，§2.1）→ 提示并放弃 AI PC
  ```
  注意 `GameSession.__init__` 已把 social 建于构造期（session.py:77-84），attach 在
  GameSession 构造后、`session.run()` 前调用即可（ai_explore_turns 才消费 aipc）。
- `snapshot`（server.py:258-295）：`base["aipc"] = {"enabled": bool(self.session and self.session.aipc),
  "player": 玩家名}`；前端 mode-badge 消费。
- `POST /api/answer`（server.py:574-579）：pending 为 None 且 UI 处于 aipc 对局时，
  把 `text` 转投 `ui.post_command(text)`（旁听指令）；否则维持现有语义。

### 4.2 前端（web/static/）

- `index.html` 设置区（L108-152）API 配置 field 之后新增 field「AI 玩家角色」：
  复选框 `#aipc-on`（"让 AI 代打你的角色（需要已接入 LLM）"）+ textarea `#aipc-persona`
  （"你的 persona：希望队伍如何称呼/看待'玩家'（可空）"）。独立保存按钮 `#aipc-save`
  （POST /api/settings/aipc），不复用 `#set-save`（避免与密钥校验耦合）。
- 建卡表单（`#create-form`，charbuild 渲染）：追加两个可选输入
  「角色简介（AI 代打时的行为依据）」「说话语气」，随 start spec 提交
  （`description/personality` 字段）。
- `app.js`：
  - `renderSettings`（L164-197）：另发 `GET /api/settings/aipc` 回填 aipc 开关与
    persona；
  - start 提交体带 `aipc_enabled` 与卡字段；
  - mode-badge（L601-603）：`st.aipc && st.aipc.enabled` 时徽标追加「· AI 代打」；
  - pending 处理（L423-489 区域）：aipc 对局中 ask-form 常驻（label 改
    "旁听指令：/状态 /保存 /退出…"），提交仍走 `#ask-form` submit（L489）→
    `/api/answer {text}`，无 pending 时代码路径即 §4.1 的 post_command；
  - 队伍卡（refreshState 的 party 渲染）：玩家行 `is_player` 徽记从「你」改为
    「你 · AI 代打」（数据已含 is_player，server.py:292）。

---

## 5. 测试计划（ScriptedAdapter 模式）

新增 `tests/test_aipc.py`，全部沿用 test_game.py / test_social.py 的既有手法：
`ScriptedAdapter(script=[ModelResponse(...)])` 或 handler 按 system prompt 中的名字发牌
（test_social.py:38-69 的 `TableScript` 模式）。基座 fixture 复用 test_game.make_state
（Kyle + 默认三人组）。

| 测试 | 断言要点 |
|---|---|
| `test_aipc_off_zero_change` | 不 attach 的 AI 模式对局：`player_combat_turn` 仍走 `ui.choose`（AutoUI 脚本计数）、`social.reaction_wave` speakers=3 同伴、`_ask_player_roll` 仍触发 `_RollGateUI.rolls_asked`、存档无 `aipc` 键时 `enabled=False` |
| `test_aipc_explore_takeover` | script=[`explore_intent{"text":"检查箱子"}`, 收束] → `dm_turn` 收到含"检查箱子"的输入；`ui.free_input` 从未被调（AutoUI 打桩） |
| `test_aipc_explore_act` | script=[`explore_act{"kind":"rest_day"}`] → 全队 PW 走休整结算（每个完整休息日恢复等于体格的 PW）、无 LLM 参与数值 |
| `test_aipc_tools_schema_full` | 探索 beat 的 `adapter.calls[-1]["tools"]` 含 explore_act、战斗回合含 combat_action（守护 Agent.tool_names=[] 的全量下发——registry.py:53-56 空列表语义；若误用 agents.py:128 的列表，schema 过滤会静默吞掉 explore_act） |
| `test_aipc_no_tool_keeps_moving` | script=[纯文本] → `dm_turn("（Kyle 按兵不动。）")` 被调用，探索循环不空转 |
| `test_aipc_combat_takeover` | script=[`combat_action{"action":"attack","weapon":"very_heavy_pistol"}`] → `enc.log` 有攻击行、`AutoUI.choose` 计数为 0；`tool_trace` 命中 combat_action |
| `test_aipc_combat_fallback` | BoomAdapter → `companion_turn` 落 `_scripted_pc_turn`，`enc.log` 增长（同 test_game.py:159-183 手法） |
| `test_aipc_roll_gate_bypassed` | aipc 开启时 `tools._tool_skill_check("Kyle",…)` → `_RollGateUI.rolls_asked == []` 且结果含 total（引擎结算） |
| `test_aipc_reaction_no_menu` | aipc 开启时 `_reaction_prompt(玩家, {"reaction":"evade"})` 返回 None 且不触 ui.choose——战斗中恒不弹人类菜单（与同伴现状一致；测试只守护"不弹菜单"，不声称"自动触发"）；关闭时走 ui.choose |
| `test_aipc_rest_day_no_prompt` | aipc 开启时休整不调 `ui.choose`，玩家的 PW 恢复与同伴走同一条引擎结算路径 |
| `test_aipc_ip_defaults` | 同伴带着改进点（IP）入场 → 走默认花费策略（先升职业能力、再补主用技能），无 ui.choose |
| `test_aipc_in_social_wave` | TableScript 给Kyle发 say 牌 → `reaction_wave` 返回含玩家行、`table_log` 记账、DM 上下文含其插话；抢话排序含玩家 |
| `test_debate_player_can_speak` | 玩家 stance=object 进辩论后 `_debate_round` 给玩家发牌（by_name 修复的回归测试）：玩家第二轮仍能申辩或 agree 退出；修复前玩家首轮即被"淘汰"（social.py:198-204 发牌过滤、social.py:180-181 conceded 判定） |
| `test_aipc_say_reaches_table` | explore beat 仅发 `say{"text":"…纹章…","to":"商人"}` → `table_log` 出现 kind=table 行、后续 DM 上下文含【桌上插话】与该句（`_digest_table` 消化）；say-only 节拍触发 `social.beat(台词)` 而非"按兵不动"兜底 |
| `test_aipc_buy_noninteractive` | 商店场景 script=[`explore_act{"kind":"buy","item":"first_aid_kit"}`] → 欧元按 SHOP_GOODS 价目扣减、库存 +1、全程零 ui.choose、无工具超时；非商店场景或欧元不足返回 ok=False |
| `test_aipc_llm_down_explore_hands_back` | BoomAdapter + aipc → `explore_beat()` 返回 False，循环回到 `ui.free_input`，system 行提示降级 |
| `test_macros_render` | `{{user}}/{{char}}`、嵌套 `{{upper::{{char}}}}` 式两层、`\{\{x\}\}` 转义、未识别宏原样保留、`{{roll::1d10}}` 同 seed 确定 |
| `test_assemble_system_order` | system 分段顺序 = main→wiBefore→persona→description→personality→scenario→wiAfter→examples；`card.system_prompt` 覆盖 main 且 `{{original}}` 展开 |
| `test_fit_to_budget_sacrifice` | 预算不足时示例块先整块消失、常驻字段保留（§7 牺牲顺序） |
| `test_worldinfo_scan` | constant 先入选；order 大者保留、小者丢；AND_ANY/NOT_ANY 副键逻辑；CJK 子串命中、ASCII 全词不误中（"king" 不中 "liking"）；预算即停不回删 |
| `test_aipc_save_roundtrip` | save→load 保留 `aipc.enabled/persona/card`；无 aipc 键的旧档 load 后 enabled=False |
| `test_aipc_settings_no_secret` | POST /api/settings/aipc `{"enabled":true,"persona":"…"}`（无密钥、CI 已清供应商环境变量）→ 200 且 `saves/llm_config.json` 含 aipc 键、全文无 api_key 值；`POST /api/settings/llm` 缺密钥仍报错不落盘（现状不变，server.py:165-168） |
| `test_name_collision_refused` | 玩家名=同伴名 → `AIPCController.attach` 返回 None（不抛异常），`build_session` 降级为无 aipc 并提示 |

既有测试回归门槛：`DEEPSEEK_API_KEY= python -m pytest tests/ -q` 全绿
（本设计期间基线 208 passed 已实跑确认；唯一注意点是测试环境不得带供应商密钥变量，
见文档头部）。

---

## 6. 逐文件改动清单

**新增**

| 文件 | 内容 |
|---|---|
| `trpg/st/__init__.py` | 再导出（§1.0） |
| `trpg/st/card.py` | `CharacterCard` / `Persona` / `card_from_companion` / `card_from_player` |
| `trpg/st/macros.py` | `MacroContext` / `render` |
| `trpg/st/worldinfo.py` | `WLEntry` / `WorldInfo` / `WIResult` / `wi_from_book` / `engine_worldinfo` / `scan` / `merge`（P1 完成度） |
| `trpg/st/assemble.py` | `Injection` / `assemble_system` / `build_context_block` / `inject_at_depth` / `fit_to_budget` / `split_examples` |
| `trpg/game/aipc.py` | `AIPCController`（attach/explore_beat/combat_turn/refresh/poll_command 转发/reaction_speakers） |
| `tests/test_aipc.py` | §5 全表 |
| `docs/aipc-design.md` | 本文 |

**修改**

| 文件 | 改动 |
|---|---|
| `trpg/game/session.py` | `__init__` 增加 `self.aipc=None` 属性（构造后由 attach 赋值）；`ai_explore_turns` 三段化 + `_handle_explore_command`/`_post_beat` 抽取（`_post_beat` 内部按 scene_id 重取 scene（session.py:454）、返回 "end" 并向上传播，session.py:459-460）；从 `open_shop` 抽出非交互 `buy_good(key)`（§2.3）；抽卡面数值补差 helper 供交互路径与 aipc 花费分支共用（§2.9）；`player_combat_turn` 顶部分支（§2.5）；`_reaction_prompt` 玩家分支门控（§2.6）；休整/修甲的玩家分支（§2.8 表）；`resolve_pending_choices` 的玩家分支（§2.9）；`enter_scene` 末尾 `aipc.refresh()`（§2.2） |
| `trpg/game/tools.py` | `_ask_player_roll` 门控加 aipc 条件（§2.3）；新增 `register_aipc_tools`（say 带桌面记账、explore_intent、explore_act 含非交互 buy）+ `_aipc_say`/`_tool_explore_act` |
| `trpg/game/social.py` | `reaction_wave` speakers 改 `party() ∩ pc_agents`（§2.6）；**`_debate_round` 的 `by_name` 从 `state.companions` 改为 `party() ∩ pc_agents` 查找**（否则玩家被邀请进辩论却永远无法发言、首轮即"被说服"，social.py:198-204）；`beat` trigger 文案参数化（默认值不变）；talkativeness 钩子留 P2 |
| `trpg/game/state.py` | `GameState` 三个新字段；`save_game`/`load_game` 的 `aipc` 块（§3.1/§3.2） |
| `trpg/ui/webui.py` | `_command` 槽 + `post_command`/`poll_command`；`resolve` 支持 `kind="command"`（§2.8） |
| `trpg/web/server.py` | 新增 `GET/POST /api/settings/aipc`（apply_llm_settings 零改动，§4.1）；`llm_settings_view` 增 aipc；`start` 透传建卡字段与开关（缺省取 `aipc_prefs`）；`build_session` 挂控制器；`snapshot` 增 `aipc`；`POST /api/answer` 旁听分派 |
| `pyproject.toml` | `[tool.setuptools] packages` 追加 `"trpg.st"`（现清单硬编码七包，缺新包则 pip 安装产物缺 trpg/st；build_release.py 的全目录打包不受影响） |
| `trpg/web/charbuild.py` | `build_character` 透传卡叙事字段（≤2000 字符校验）（§3.3） |
| `trpg/web/static/index.html` | 设置页「AI 玩家角色」field；建卡两输入；ask-form label（§4.2） |
| `trpg/web/static/app.js` | `renderSettings` aipc 回填、start 提交体、mode-badge、旁听指令常驻表单（§4.2） |
| `main.py` | `--aipc` 开关 + `TRPG_AIPC=1` 环境变量：CLI 模式下 `build_ai_stack` 后同款 attach（§3.3）；`build_companions` 不动 |
| `trpg/game/agents.py` | 不改模板常量；导出 `PC_SYSTEM_TEMPLATE` 供 assemble 作 `main_default`（现已模块级常量，零改动即可 import） |
| `README.md` / `CHANGELOG.md` | 功能说明 + 版本条目 |

**不改**：`trpg/harness/loop.py`、`adapter.py`、`events.py`、`registry.py`、`usage.py`
（P1 才给 loop.run 加可选 `post_build` kwarg）；`trpg/rules/*`（数值引擎零触碰）；
`trpg/campaign/*`（模组格式零改动——同伴 persona/voice 继续作卡字段来源）。

---

## 7. 边界情况

### 7.1 LLM 未配置 → 优雅降级
- `build_session`（server.py:317-341）：`llm_cfg is None` → 剧本模式，`aipc` 恒为 None，
  前端 toggle 置灰（`llm_active=false` 时提示"需先接入 LLM"）。
- 运行中断网（已开局）：探索 beat → `explore_beat` 返回 False → 本轮交还人类 +
  system 提示（§2.4）；战斗 → `_pc_agent_turn` 既有 `RuntimeError` 兜底 →
  启发式 `_scripted_pc_turn`（session.py:1111-1113，玩家同样适用）；
  桌边 → social 既有整层静默降级（social.py:145-146）。三层都不阻塞对局。

### 7.2 TokenMeter 记账
- AI PC 一切 LLM 调用（探索 beat、战斗回合、社交波、辩论）都经 `loop.run` 并调
  `session._record_usage`（session.py:496-502；social.py:144 同款）——
  `by_agent` 自动出现 `aipc:{玩家名}`，无新记账代码。
- 宏/世界书/组装/预算裁剪是纯字符串运算，**零 token**；`{{roll}}` 结果随 context 消息
  落日志，不产生额外调用。
- meter.record 的异常已吞（session.py:501 `except Exception`），AI PC 路径不新增
  裸调用。

### 7.3 社交层把 AI PC 计入抢话波
- speakers 改为 `party() ∩ pc_agents`（§2.6）；玩家行带 interrupt/stance，可触发辩论、
  可被 DM 即席回应轮消化（beat 第 5 步，social.py:90-99）。
- `MAX_WAVE_SPEAKERS=2` 不变：4 人桌抢话上限仍 2，防复读语义保留。
- 玩家处于 `table_log` 的行与同伴同构（`who/text/to/interrupt/stance`），`_digest_table`
  与 `_recent` 零改动。
- AI PC 探索意图经 `social.beat(text)` 走完整节拍（抢话→DM→反应→辩论→DM 收场），
  玩家自己的"发言"已经发生，故 beat 的第 1 步 trigger 文案参数化，避免自我打断的
  荒谬表述（"玩家刚开口"→"你刚说完话"）。

### 7.4 开关关闭时零行为变化（逐点论证）
1. 所有 session 分支以 `if self.aipc is not None` 门控，关闭即原代码路径。
2. `tools._ask_player_roll` 新条件在 `harness_session.aipc is None` 时短路为原判断。
3. `social.reaction_wave` 的 party() 改写：关闭时玩家不在 `pc_agents`，过滤结果与
   companions 逐位相等（§2.6）。
4. `_resolve_level_up_choices`/`party_short_rest` 新分支不可达。
5. 存档：旧档无 `aipc` 键 → enabled=False；新档关闭时 `aipc.enabled=false` 亦短路。
6. 前端：`snapshot` 无 aipc 键或 enabled=false → 徽标/表单与现状一致。
7. 剧本模式（loop=None）：`build_session` 与 `main.py` 均不 attach；`--demo` 的
   AutoUI 全自动路径不触任何新代码。
8. 回归命令：`DEEPSEEK_API_KEY= python -m pytest tests/ -q`（基线 208 passed 已实跑）。

### 7.5 其他
- **名字冲突**：玩家名与同伴同名（16 字符截断，charbuild.py:30）→ `attach` 返回
  None（§2.1）→ `build_session` 降级为无 aipc 并 system 提示；存档照常。
- **玩家死亡**：combat 分支内 `companion_turn` 只在 `enc.alive(c)` 后调用；
  TPK 判定（session.py:643-646）不经过 AI PC，语义不变。
- **昏迷/濒死/被控制**：全部数值面在引擎，AI PC 仅"决策输入"，与同伴同权。
- **循环卫生**：探索 beat 最多 4 步（StepBudgetGuard(4)，同 social loop）；单拍内无
  工具收束即兜底 dm_turn（§2.4，无跨拍计数），DuplicateCallGuard 抑制重复
  explore_intent。
- **终端模式无旁听指令**：ConsoleUI/AutoUI 无 poll_command（cli.py:16-151），
  控制器 `getattr` 防御转发恒得 None；AI PC 照常接管，/退出 靠 Ctrl+C
  （main.py:208-212），`ui/cli.py` 零改动。
- **旁听指令竞态**：`_command` 读写全程持 `WebUI._lock`；`poll_command` 取走即清，
  不存在双消费。
- **Windows/编码**：st 包只产出 str，无新 I/O；aipc 会话日志沿用 `Session` 的
  utf-8 jsonl（events.py:22-32）。

---

## 8. 分期与明确不做

- **P0**：§1.1/§1.2/§1.4（含 fit_to_budget）+ §2 全部接线 + §3 存档 + §4 前端最小面 +
  §5 测试 + §7 降级。交付即"AI PC 完整接管六个交互点"。
- **P1**：§1.3 世界书全量（`engine_worldinfo`/`scan`/`merge` + 角色书挂载 + DM 上下文
  加餐）；`DefaultAgentLoop.run` 增可选 `post_build`，`inject_at_depth` 真插桩启用
  （AN/WI@D 落历史中段）；手册按幕条目化以省 token。
- **P2**：swipe 行动候选（每回合 2 候选，`Session.log` 增 `swipe` 事件存候选+选中，
  UI 左右切换；LLM 故障/省时降级单候选）；talkativeness 数值化（`reaction_wave`
  概率激活，roll < talkativeness 才进决策；空波随机补 1 人，〔ST 规格 §9〕）；
  AI PC 的休整/急救固定/购买/改进点花费的 LLM 决策化；开场 `first_mes` + `alternate_greetings`
  swipe 换开场。
- **不做**：PNG/charx 角色卡导入导出、向量检索世界书、群聊 auto 模式定时器、
  Continue 续写、Impersonate、消息级 include-exclude、`{{setvar}}` 系变量宏
  （引擎状态已有 flags，不引入第二套变量空间）。
