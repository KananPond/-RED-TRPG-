# TRPG 模组（剧本）开发规范

**版本 3.0（《赛博朋克 RED》口径）**　适用引擎：`trpg` ≥ 2.0（当前 2.0.0，`ruleset = "cyberpunk-red"`）

一个**模组（剧本）**是一个自包含的 JSON 文件：`modules/<模组id>/module.json`。
引擎按《赛博朋克 RED》轻松模式口径运行剧本：所有骰点、伤害、护甲磨损、伤势、
致命伤、生存检定与改进点（IP）都由规则引擎结算，LLM 只做决策与叙述，
**不生成任何数值结果**。

- 内置模组：`modules/cpred-easy-mode`——**《赛博朋克 RED：轻松模式》**，即轻松模式手册
  本体，内含手册的入门差事**「抢钱行动」**（第一幕 来电 → 第二幕 海伍德工业区 →
  第三幕 仓库，共六场：`south_night_city_loft` / `prep_and_drive` / `heywood_meet` /
  `lazlo_rescue` / `warehouse_approach` / `aftermath`，两场遭遇与两张战术地图）。
  不确定怎么写时，直接对照它的 `module.json` 抄结构。
- 运行时产生的 `generated-*` / `imported-*` 目录同样遵循本规范。
- 引擎**不实现**轻松模式未收录的子系统（自动武器/压制射击、网络空间与接入、
  载具战斗、武术、药物与成瘾）：它们既没有对应的数据表，也无法在模组 JSON 里声明。
  写剧本时请绕开这些内容。

> **版权提示**：本仓库的规则文本改编自 R. Talsorian Games 免费发布的
> 《Cyberpunk RED Easy Mode》与《Cyberpunk RED》核心规则书，版权归
> R. Talsorian Games 所有；Cyberpunk® 是 CD Projekt Red S.A. 的注册商标；
> 本项目为非官方同人作品，仅供个人学习与桌边自用，不得用于商业发行。
> 自己写模组时请勿逐字复制官方手册的正文。

## 1. 快速开始

```bash
# 1. 建立目录
mkdir modules/my_night_city_job
# 2. 编写 modules/my_night_city_job/module.json（本规范；目录名必须等于 id）
# 3. 启动游戏
python main.py --web                                  # 开始页 → 新的冒险 → 选择该模组
python main.py --module my_night_city_job --demo --seed 7   # 终端自动验证全流程
```

模组**加载即校验**（"坏输入大声失败"）：错误会逐条列出，任何一条 error 都会让模组
无法加载；warning 只提示（如 IP 预算异常），不影响加载。

## 2. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✔ | 1-48 字符，仅 `[A-Za-z0-9_-]`；**建议与目录名一致**（引擎按目录名加载） |
| `title` | string | ✔ | 中文标题 |
| `en_title` | string | – | 英文标题 |
| `setting` | string | ✔ | 舞台设定（1-2 句）。写夜之城及其周边：南夜之城、海伍德、海伍德工业区、沃森、小中国、上码头区、旧战区、帕西菲卡…… |
| `synopsis` | string | ✔ | 剧情梗概（3-4 句） |
| `act_titles` | object | ✔ | `{"1": "第一幕标题", ...}`，幕号 1-9（键是字符串，值是标题） |
| `epilogue` | string | ✔ | 结局叙述，`\n` 分行 |
| `scenes` | array | ✔ | 场景列表（见 §4），非空 |
| `encounters` | object | ✔ | 遭遇表（见 §5）；没有战斗的模组写 `{}` |
| `pregens` | object | – | **预设角色**（见 §3）；玩家从这里挑一个当自己，其余交给 AI |
| `default_companions` | array | – | `pregens` 的 key 列表；玩家没另选队友时启用 |
| `custom_npcs` | object | – | 剧本自定义敌人/NPC 数据块（见 §6） |
| `npcs` | object | – | 纯叙事 NPC（见 §7） |
| `tokens` | object | – | 棋子图标库 `{id: 资产路径}`（战术地图用，见 §4.1） |
| `dm` | object | – | DM 卡扩展（ST 组装线）：`persona`/`voice`/`system_prompt`/`depth_prompt`/`book` |
| `requires` | array | – | 依赖的其他模组 id（Cordis 插件语义；不得自引用、不得重复） |

**不再有的旧字段**（5e 时代的写法，写了会被校验器拒收或忽略）：
`companions`（改 `pregens`）、`custom_monsters`（改 `custom_npcs`）、
`party_level_start`、`expected_end_level`、`level_to`、`milestone`、`dark`、
`skills`（场景检定改 `checks`）、`dc`（改 `dv`）。
RED 没有等级、没有职业等级推进表，也没有另一套成长点数。

### 资产路径规则

- 资产放在 `modules/<模组id>/assets/` 下（地图 JPG/PNG、棋子 Token PNG）。
- 路径一律**相对模组目录**书写（如 `assets/maps/pier.jpg`）：
  禁止绝对路径、禁止 `~`、禁止 `..` 上跳、禁止盘符/协议前缀。
- 扩展名白名单：`.jpg` / `.jpeg` / `.png` / `.webp` / `.svg`。
- Web 端经 `/api/asset/<模组id>/<路径>` 服务；单文件模组 JSON 上限 **4 MB**。

### 文本约定

- `intro` / `explore` / `success` / `fail` / `outro` 等文本字段用 `\n` 分行，
  引擎按行渲染。文本里**不要写机制数值**（"敌人工事 +2"这类）——
  机制一律走引擎字段，叙述只描述世界。
- 夜之城的语域：脏、快、具体。叫得出价钱、品牌、街区与绰号，不要写"勇者""地牢"。

## 3. 预设角色（pregens）

轻松模式**不掷骰建卡**：玩家从模组的 `pregens` 里挑一位，只改自己的街头名
（`name`/`handle` 来自设定卡，数值全部照搬）。其余预设角色由 AI 代打，成为队友。

```json
"pregens": {
  "solo_vee": {
    "name": "Vee", "role": "solo", "role_rank": 4,
    "role_ability": "战斗直觉",
    "stats": {"int": 6, "ref": 8, "dex": 7, "tech": 4, "cool": 6,
              "will": 7, "luck": 5, "move": 6, "body": 7, "emp": 4},
    "hp": 45,
    "skill_levels": {"athletics": 4, "brawling": 5, "conceal_reveal": 5,
                     "evasion": 6, "handgun": 6, "melee_weapon": 4,
                     "perception": 5, "stealth": 4, "streetwise": 4,
                     "shoulder_arms": 3, "resist_torture_drugs": 4},
    "armor": "light_armorjack",
    "weapons": ["very_heavy_pistol", "heavy_melee"],
    "cyberware": ["cyberoptics", "neural_link"],
    "gear": {"first_aid_kit": 1},
    "eurobucks": 100,
    "persona": "话少、动作快，收钱前不谈感情；把队伍当成合同的一部分。",
    "voice": "短句，几乎不用问句；谈价时反而话多。",
    "desc": "从第四次企业战争的废墟里爬出来的枪手，右手是义体。"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✔ | 角色名——用英文/拉丁转写的街头名（与官方手册一致） |
| `handle` | string | – | 街头绰号；与 `name` 相同时省略（界面显示为「名字（绰号）」） |
| `role` | string | ✔ | 职业 key，见 §8 |
| `role_rank` | int | – | 职业特殊能力等级（1-10，默认 4） |
| `role_ability` | string | – | 职业特殊能力名（如"战斗直觉"） |
| `stats` | object | ✔ | **十项属性**，缺项按 2 补齐；常规 2-8（预设卡可更高） |
| `hp` | int | – | PW 上限；缺省按 `10 + 5 × ⌈(体格 + 意志力) ÷ 2⌉` 计算（推荐显式写死，便于校对） |
| `skill_levels` | object | ✔ | 技能 key → 等级 0-10；未列的技能视为 0 级 |
| `armor` | string | – | 护甲 key，见 §8；缺省无护甲 |
| `weapons` | array | – | 携带武器 key，见 §8 |
| `cyberware` | array | – | 义体 key，见 §8。预设卡的 `emp`/人性已反映义体，引擎**不会**二次扣人性 |
| `gear` | object | – | 装备 key → 数量，见 §8 |
| `eurobucks` | int | – | 起始欧元（默认 0） |
| `persona` / `voice` / `desc` | string | – | 人设 / 语气 / 外观描述——AI 代打的依据，越具体越好 |

建议至少写 2 名预设角色（本规范示例给 2 名：独行侠 + 医疗师），
覆盖"能打"与"能救"两个位置；`default_companions` 只列 pregens 里存在的 key。
**人名一律写英文**（预设角色、NPC、敌人专名都用英文原名或拉丁转写，如
`Leonard Houston`），只有叙述正文用中文——中文音译名一律不要（本书里的名字
本来就是英文街头名）。**地名与类型标签保持中文**：`夜之城`、`海伍德`、
`歌舞伎町`，以及 `腐败警察`、`摩托增援`、`线人` 这类职业/兵种名照写中文即可。
内置模组《赛博朋克 RED：轻松模式》给了完整的 5 张卡可作参照：
摇滚客 Forty、独行侠 Mover、技术师 Torch、医疗师 Redtail、
媒体人 24/7（后两张卡的属性列在中文版卡面上不完整，其十项属性是按卡面
可读约束反推的；`default_companions` = `["medtech_redtail", "solo_mover"]`）。

## 4. 场景（scenes）

场景为**线性链**：从第一个场景沿 `next` 走到唯一的 `null` 终点。
不允许环、不允许链外孤场景（校验器强制）。

```json
{
  "id": "night_market",
  "act": 2,
  "location": "沃森 · 歌舞伎町夜市",
  "intro": "雨棚下的霓虹把水洼染成三种颜色。\n摊主们用眼角打量你的枪。",
  "explore": "找 Wei 验货；打听码头那边的动静；补一点弹药。",
  "checks": [
    {"skill": "human_perception", "dv": 15,
     "success": "Wei 的手指在箱扣上多停了一拍——他在等别的买家。",
     "fail": "他笑得恰到好处，你什么也没看出来。"}
  ],
  "encounter": null,
  "next": "the_pier",
  "ip": 30,
  "shop": true,
  "outro": "",
  "encounter_note": "",
  "map": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✔ | 场景 id，模组内唯一 |
| `act` | int | ✔ | 幕号 1-9，对应 `act_titles` |
| `location` | string | ✔ | 地点小标题（夜之城地名 + 具体场所） |
| `intro` | string | ✔ | 进场叙述 |
| `explore` | string | – | 可做的事（给玩家的引导词） |
| `checks` | array | – | 检定机会（见下） |
| `encounter` | string/null | ✔ | 引用的遭遇 id，或 `null`（无战斗）；引用的遭遇必须已定义 |
| `next` | string/null | ✔ | 下一场景 id；最终场景必须是 `null` |
| `ip` | int | – | **离开本场景时给队伍的改进点总数**（默认 0），见 §9 |
| `shop` | bool | – | `true` 时该场景开放夜市场货摊（玩家可 `/购买`） |
| `rest_day` | bool | – | `true` 表示进入本场景时「一天过去了」：按手册的休整规则，每名队员恢复等于体格的 PW、修好护甲磨损，队里的医疗师还会治疗一处致命伤。用于给打过硬仗的队伍喘息余地（例：营救中间人之后、强攻仓库之前） |
| `outro` | string | – | 战斗后叙述（可选） |
| `encounter_note` | string | – | 补充说明（可选，仅供 DM 叙事参考） |
| `map` | object/null | – | 战术地图声明（见 §4.1）；不写即剧场模式 |

### 4.1 检定块（checks）

```json
"checks": [
  {"skill": "perception", "dv": 13,
   "success": "你在消防梯上发现一枚掐灭的烟蒂——五分钟前的。",
   "fail": "这地方看起来和别的巷子一样脏。"},
  {"skill": "persuasion", "dv": 17,
   "success": "摊主收了 20 eb，把巷子的后门指给你。",
   "fail": "他摇头，把手按在柜台下的霰弹枪上。"}
]
```

- `skill` **必须是** 41 项技能之一（§8 的技能 key），写错即加载失败。
- `dv` 是难度值，允许 9-30 的整数；常用档 13/15/17/21/24。**必须高于** DV 才算成功
  （平手算失败），所以 `dv` 比 5e 时代的 DC 更像"门槛"。
- 一个场景可以有 0 到多个检定；检定在探索期由 DM 工具 `skill_check` 或
  玩家主动尝试触发。检定失败**不应**卡死场景链——`fail` 文本要给出继续前进的路。

### 4.2 战术地图（map，可选）

声明 `map` 后，该场景的战斗走**网格模式**（**1 格 = 2 米**，移动力 = 机动 × 2 米），
Web 端显示地图面板并可点格走位；未声明的场景保持**剧场模式**（近身纠缠近似 2 米）。

```json
"map": {
  "image": "assets/maps/pier.jpg",
  "label": "旧码头 · 三号仓库外",
  "image_grid": [20, 12],
  "crop": [6, 2, 12, 8],
  "grid": [12, 8],
  "obstacles": [[0, 0], [0, 1], [5, 3], [5, 4], [6, 3]],
  "cover": {"厚木": [[3, 2], [3, 3]], "厚钢": [[8, 5]]},
  "party_spawn": [2, 4],
  "tokens": [
    {"id": "npc:broker_wei", "kind": "npc", "name": "Wei", "cell": [11, 7]}
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `grid` | [int,int] | ✔（有 map 时） | 战斗网格 `[宽,高]`，每维 4-64 格 |
| `image` | string | – | 底图（相对模组目录，纯网格可省略） |
| `label` | string | – | 面板标题，缺省用 `location` |
| `image_grid` | [int,int] | – | 源图自带格子数（每维 4-128，合成图裁剪用） |
| `crop` | [int,int,int,int] | – | 从合成图裁出分幅 `[x,y,w,h]`（格），越出源图即报错 |
| `obstacles` | array | – | 阻挡格 `[[x,y], ...]`：不可进入、挡视线（实心墙/立柱） |
| `cover` | object | – | 掩体：`{材质: [[x,y], ...]}`。材质取 `厚钢/薄钢/厚石/薄石/厚防弹玻璃/薄防弹玻璃/厚混凝土/薄混凝土/厚木/薄木/厚灰泥\泡沫\塑料`，耐久见规则层 `COVER` 表。**掩体不挡视线**：伤害先打在掩体上，耐久归零即被摧毁，该次攻击剩余的伤害作废（轻松模式第 21 页）。格子可与 `obstacles` 不同，但两者不要重叠 |
| `party_spawn` | [int,int] | – | 队伍落位锚点（缺省取网格中心就近散布）；不得落在障碍格上 |
| `tokens` | array | – | 场景初始棋子：`{id, kind, name, cell}` |

- `tokens[].kind` ∈ `pc` / `npc` / `monster` / `prop`；队伍棋子由引擎自动布点，
  **不要**手写 `pc` 棋子。棋子 `id` 在场景内唯一、`cell` 界内且不在障碍/已占格上。
- 棋子图标按 id（去掉 `npc:` 等前缀）去顶层 `tokens` 图标库查路径。
- 战斗期敌人布点见 §5 的 `spawns`；探索期棋子位置由 DM 用 `map_update` 工具调度。
- **射程是网格模式的核心**：远程攻击按射程带查难度值
  （0-6 / 7-12 / 13-25 / 26-50 / 51-100 / 101-200 / 201-400 / 401-800 米），
  手枪超过 200 米、霰弹枪超过 200 米就打不到——布图时把交战距离设计在
  1-25 米带（即 1-12 格）最顺手。掩体按材质给耐久（厚钢 50 …薄木 5），
  对 2m × 2m 区域攻击，耐久归零即被摧毁。

## 5. 遭遇（encounters）

```json
"encounters": {
  "e_pier_ambush": {
    "title": "码头伏击",
    "intro": "仓库卷帘门升到一半，铁链在风里响。你们不是唯一在等那只箱子的人。",
    "enemies": [["corrupt_cop", 2], ["dock_booster", 1]],
    "loot": {"eurobucks": 200, "items": {"ammo": 20, "first_aid_kit": 1}},
    "spawns": {"corrupt_cop": [[9, 2], [9, 6]], "dock_booster": [[10, 4]]}
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | – | 遭遇标题（战况板与日志用） |
| `intro` | string | – | 开战叙述 |
| `enemies` | array | ✔ | `[[敌人key, 数量], ...]`，数量 ≥ 1；**非空** |
| `loot` | object | – | `{"eurobucks": int, "items": {物品key: 数量}}` |
| `spawns` | object | – | `{敌人key: [[x,y], ...]}`，按序消费；缺省在队伍对侧边缘散开 |

- 敌人 key ∈ 引擎内置 NPC 表（`corrupt_cop` 腐败警察 / `houston` Leonard Houston /
  `bike_ganger` 摩托增援 / `lazlo` 中间人 Lazlo）∪ 本模组 `custom_npcs`。
  引用了未定义的 key 即加载失败。
- 同类多只需写数量，引擎自动编号（腐败警察1、腐败警察2……）。
- `spawns` 只能引用本遭遇 `enemies` 里出现过的 key，坐标必须是网格内的整数格
  （只有场景声明了 `map` 时才有意义）。
- `loot.items` 的 key 取武器表键（进武器栏）、护甲表键（自动穿上）、
  义体表键（自动植入并扣人性）或装备表键（进背包按数量累加）。
- 敌人的护甲会**磨损**（每次挡下伤害 -1）：多打几轮，重甲敌人会自己变软。

## 6. 自定义敌人 / NPC 数据块（custom_npcs）

字段与引擎内置 NPC 表同构；引擎用与其他角色**完全相同**的结算路径
（属性 + 技能 + 1d10 对抗难度值、护甲 SP 与磨损、伤势等级、生存检定、致命伤）。

```json
"custom_npcs": {
  "dock_booster": {
    "name": "码头义体帮", "en": "Dock Booster",
    "stats": {"int": 4, "ref": 6, "dex": 6, "tech": 3, "cool": 5,
              "will": 5, "luck": 4, "move": 6, "body": 6, "emp": 3},
    "skills": {"athletics": 4, "brawling": 6, "evasion": 5, "handgun": 5,
               "melee_weapon": 3, "perception": 4, "stealth": 3,
               "streetwise": 4, "resist_torture_drugs": 3},
    "hp": 30,
    "armor": 7,
    "armor_name": "凯夫拉",
    "weapons": ["heavy_pistol", "medium_melee"],
    "cyberware": ["muscle_enhance"],
    "gear": ["ammo"],
    "reputation": 2,
    "notes": "替码头帮收账的打手；先开枪，再问话。"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✔ | 显示名（同批多个自动加编号） |
| `stats` | object | ✔ | 十项属性，缺项按 5 补齐 |
| `skills` | object | ✔ | 技能 key → 等级（不必写全 41 项） |
| `hp` | int | – | PW 上限；缺省 `10 + 5 × ⌈(体格 + 意志力) ÷ 2⌉` |
| `armor` | int | – | 护甲 SP（默认 0），所有部位同值 |
| `armor_name` | string | – | 护甲显示名（默认"凯夫拉"） |
| `weapons` | array | ✔ | 武器 key；AI 会挑伤害最高的一把用 |
| `cyberware` | array | – | 义体 key（判定时会生效） |
| `gear` | array | – | 装备 key 列表（各 1 件） |
| `role` | string | – | 职业 key（影响 AI 行为与显示，默认 solo） |
| `reputation` | int | – | 声望（对峙用，默认 0） |
| `notes` | string | – | 给 DM 的战术/性格提示 |

## 7. 纯叙事 NPC（npcs）

不需要数据块的 NPC（线人、雇主、店主）写在这里，只进 DM 上下文：

```json
"npcs": {
  "broker_wei": {
    "name": "Wei", "role": "中间人",
    "desc": "夜市东头三号摊的老板，什么货都过手，从不多问。",
    "persona": "和气、算得清、从不赊账。",
    "voice": "先谈价，再谈事；笑的时候眼睛不笑。"
  }
}
```

`name`/`desc` 必填，`role`/`persona`/`voice` 可选。可选的 ST 扩展字段
（`goals`/`secret`/`mes_example`/`first_mes`/`depth_prompt`/`talkativeness`/`book`）
由 `npc_dialogue` 子智能体使用，写法见 `docs/companion-npc-agents.md`。

## 8. 内置 key 速查

### 属性（10）
`int 智力, ref 反应, dex 敏捷, tech 技术, cool 魅力, will 意志, luck 幸运,
move 机动, body 体格, emp 共情`

### 技能（41，等级 0-10）
`accounting 会计学, acting 表演, athletics 运动, brawling 斗殴, bribery 贿赂,
bureaucracy 官僚体系, business 做生意, composition 作曲,
conceal_reveal 藏匿/搜寻物品, concentration 专注, conversation 交谈,
criminology 刑侦学, cryptography 密码学, dancing 舞蹈, deduction 推理,
drive_land_vehicle 载具驾驶, education 教育, electronics_security 电子学与安防(×2),
evasion 闪避, first_aid 急救, forgery 伪造, handgun 手枪,
human_perception 读心, interrogation 审讯, library_search 数据库检索,
local_expert 本地知识, melee_weapon 近战武器, paramedic 医疗救援(×2),
perception 感知, personal_grooming 时尚与风格, persuasion 说服,
photography_film 摄影/摄像, pick_lock 开锁, pick_pocket 扒窃,
play_instrument 乐器演奏, resist_torture_drugs 抗酷刑/抗药物, shoulder_arms 远程武器,
stealth 潜行, streetwise 地下世界知识, tactics 战术, tracking 追踪`

（"×2"= 提升时改进点消耗翻倍。技能加值 = 挂钩属性 + 技能等级。）

### 职业（10）
`solo 独行侠, netrunner 网络行者, tech 技术师, medtech 医疗师, media 媒体人,
cop 警察, corporate 公司人, fixer 中间人, nomad 游牧民, rockerboy 摇滚客`
——轻松模式给出五名预设角色（摇滚客/独行侠/技术师/医疗师/媒体人）。

### 武器（10）
`brawling 徒手, light_melee 轻型近战, medium_melee 中型近战, heavy_melee 重型近战,
very_heavy_melee 超重型近战, medium_pistol 中型手枪, heavy_pistol 重型手枪,
very_heavy_pistol 超重型手枪, shotgun 霰弹枪, assault_rifle 突击步枪`
远程武器只有手枪 / 霰弹枪 / 突击步枪三类——正是轻松模式第 19 页「远程战斗难度值」
表列出的全部。冲锋枪、狙击步枪、弓弩、榴弹与火箭不在轻松模式里，模组不得声明。

### 护甲（6）
`leather 皮衣(SP4), kevlar 凯夫拉(7), light_armorjack 轻型防弹夹克(11),
medium_armorjack 中型防弹衣(12), heavy_armorjack 重型防弹衣(13),
bulletproof_shield 防弹盾(10)`

### 义体（9）
`internal_agent 内置智能体, pain_editor 赛博痛觉编辑器, cyberaudio 赛博听觉组件,
cyberoptics 赛博光学组件, neural_link 神经接口, subdermal_armor 皮下护甲,
muscle_enhance 肌肉强化, cyberarm 义体手臂, tool_hand 工具手`
（人性损失 7-14 不等；人性上限 = 共情 × 10。）

### 装备（20）
`ammo 弹药, first_aid_kit 急救包, medkit 医疗包, flashlight 手电, rope 绳索,
radio 对讲机, agent 智能体, video_camera 摄像机, glow_paint 荧光喷漆,
instrument 乐器, amp 便携音箱, handcuffs 手铐, fake_id 伪造证件, binoculars 望远镜,
burner_phone 一次性手机, duct_tape 强力胶带, road_flare 道路照明弹,
recorder 录音机, toolkit 工具箱, medkit_full 医疗工具组`

### 内置敌人（4）
`corrupt_cop 腐败警察（凯夫拉 SP7、超重型手枪）, houston Leonard Houston（同上）,
bike_ganger 摩托增援（SP11、突击步枪、移动力 20，仓库战的 Boss）,
lazlo Lazlo（中间人，重伤被俘状态）`

### 难度值（DV）
日常 13 / 困难 15 / 专业 17 / 英雄 21 / 不可思议 24；允许区间 9-30。
**高于** DV 才成功，平手算失败（防守方获胜）。

### 伤势与生存
轻伤（PW < 上限，无惩罚）/ 重伤（PW ≤ 上限一半，检定 -2）/ 濒死（PW < 1，
检定 -4、移动 -6，每回合生存检定）。稳定 DV：轻伤 10 / 重伤 13 / 濒死 15。

## 9. 改进点（ip）怎么写

- `scene.ip` 是**离开该场景时，队伍里每名成员各得的改进点数**（RED 的改进点按人计，
  **不做队伍平摊**）：`"ip": 40` 就是每人 +40。同一场景只发一次
  （`next` 走两次不会发两次）。
- 战斗另有结算：击败的每个敌人给队伍 `20 + 10 × max(0, 体格 - 4)` 点，
  引擎自动发放、**每人各得**（玩家口袋里的钱只看 `loot.eurobucks`）。
- 花费口径：技能升到新等级 = **20 × 新等级**（"×2"技能再翻倍）；
  职业特殊能力 = **60 × 新等级**；技能与职业能力上限都是 10 级。
  **属性不可提升**——RED 的成长只有这两条路。
- 预算建议：一场戏（一个场景）给 20-60 IP，三幕六场的活合计 150-250
  （内置模组「抢钱行动」是每人 200：0 / 20 / 60 / 40 / 60 / 20）。
  按人算的好处是**难度不随队伍人数漂移**：`ip: 40` 无论队伍三人还是五人，
  每个人都拿 40——够把一项技能从 0 升到 1 级（20 IP）再摸到 2 级的门槛（40 IP）。
  校验器对全篇 `ip` 总和 > 4000 只给警告（软上限），但那已经远超轻松模式内容量。

## 10. 校验器会检查什么

**error（有一条就加载失败）**

- JSON 可解析、是对象、单文件 ≤ 4 MB；顶层必填字段齐全（§2）。
- `id` 为 1-48 字符的 `[A-Za-z0-9_-]`。加载器按**目录名**找模组、不比对 `id`
  与目录名是否相同，但请保持一致（否则界面与日志会各叫一个名字）。
- `act_titles` 是对象，幕号（键）必须能读成 1-9 的整数。
- `scenes` 是非空数组；每个场景含 `id`/`act`/`location`/`intro`/`next`；
  `act` 是 1-9 的整数；`ip` 非负整数。
- 场景 id 唯一；场景链自第一场沿 `next` 到唯一 `null` 终点，**无环、无链外孤场景**、
  `next` 指向的场景必须存在。
- 场景 `encounter` 非空时，引用的遭遇必须已在 `encounters` 中定义。
- `checks` 是数组（不是对象）；`checks[].skill` 必须是 41 项技能之一的 key；
  `checks[].dv` 必须是 **9-30 的整数**（浮点或字符串都会被拒）。
- `map.grid` 是 `[宽,高]` 且每维 4-64；`image_grid` 每维 4-128；
  `crop` 是 `[x,y,w,h]`、宽高至少 4 格且不越出源图网格；
  `obstacles` / `party_spawn` / `tokens[].cell` 必须是网格内的整数格，
  `party_spawn` 与棋子落格不得在障碍格上、棋子落格不得与其他棋子重叠；
  `tokens[].id` 非空且场景内唯一；`tokens[].kind` ∈ `pc/npc/monster/prop`。
- `encounters` 是对象；每个遭遇的 `enemies` 非空，写成 `[key, 数量]`，
  数量是 ≥ 1 的整数；key ∈ 内置敌人表 ∪ `custom_npcs`；
  `spawns` 的 key 必须出现在该遭遇的 `enemies` 里，坐标是 0-64 的整数格。
- `custom_npcs` 的每个数据块必须有 `name`/`stats`/`skills`/`weapons`；
  `stats` 的键必须在十项属性里、`skills` 的键必须是 41 项技能之一、
  `weapons` 是**非空**且都在武器表里；`armor` 只能写**非负整数**（护甲 SP）。
- `pregens` 的每个角色卡必须有 `name`/`role`/`stats`；`role` ∈ 十种职业；
  `stats` 必须**十项齐全**且每项是正整数；`skill_levels` 的键必须在技能表里、
  等级 0-10；`weapons` / `armor` / `cyberware` 都必须有定义。
- `default_companions` 是字符串数组，每一项都必须是本模组 `pregens` 的 key。
- 所有资产路径（`map.image`、顶层 `tokens`）合法：相对路径、无 `..`/绝对路径、
  扩展名在 `.jpg/.jpeg/.png/.webp/.svg` 之内。
- `requires` 是合法模组 id 的字符串数组：不重复、不自引用（成环）。

**warning（不阻塞加载）**

- 全篇 `ip` 合计 ≤ 0：`模组没有任何场景发放改进点 IP，队伍将无法成长`。
- 全篇 `ip` 合计 > 4000：`改进点预算偏多`（软上限）。
- `encounters` 为空：`模组没有任何遭遇，全程不会有战斗`。

## 11. 完整范例（可直接另存为 `modules/night-market-run/module.json`）

一部三场戏的夜之城跑腿活：安全屋接活 → 歌舞伎町夜市取货（一个检定块 + 商店）
→ 旧码头交货（一张地图 + 一场遭遇 + 一个自定义敌人 + 两名预设角色）。

```json
{
  "id": "night-market-run",
  "title": "夜市跑单",
  "en_title": "Night Market Run",
  "setting": "2045 年，血染之时的夜之城。红天压在沃森的霓虹上，雨里全是机油味。",
  "synopsis": "老中间人 Lazlo 把一单跑腿活丢给你：去歌舞伎町夜市从 Wei 手里取一只公文包，连夜送到旧码头三号仓库。活很简单，前提是没人知道箱子在哪、箱子里是什么、以及你几点到。这三件事都已经不是秘密了。",
  "act_titles": {"1": "接活", "2": "夜市", "3": "码头"},
  "epilogue": "卷帘门落到底，雨声被关在外面。\n你数完钱，把箱子推进车厢——这一次，公文包没换主人。\n\n夜之城不会记得今晚，但你的房租记得。",
  "pregens": {
    "solo_vee": {
      "name": "Vee", "role": "solo", "role_rank": 4,
      "role_ability": "战斗直觉",
      "stats": {"int": 6, "ref": 8, "dex": 7, "tech": 4, "cool": 6,
                "will": 7, "luck": 5, "move": 6, "body": 7, "emp": 4},
      "hp": 45,
      "skill_levels": {"athletics": 4, "brawling": 5, "conceal_reveal": 5,
                       "evasion": 6, "handgun": 6, "melee_weapon": 4,
                       "perception": 5, "stealth": 4, "streetwise": 4,
                       "shoulder_arms": 3, "resist_torture_drugs": 4},
      "armor": "light_armorjack",
      "weapons": ["very_heavy_pistol", "heavy_melee"],
      "cyberware": ["cyberoptics", "neural_link"],
      "gear": {"first_aid_kit": 1},
      "eurobucks": 100,
      "persona": "话少、动作快；收钱前不谈感情，把队伍当成合同的一部分。",
      "voice": "短句，几乎不用问句；谈价时反而话多。",
      "desc": "从第四次企业战争的废墟里爬出来的枪手，右手是义体。"
    },
    "medtech_taro": {
      "name": "Doc Taro", "role": "medtech", "role_rank": 4,
      "role_ability": "医疗",
      "stats": {"int": 7, "ref": 5, "dex": 5, "tech": 8, "cool": 6,
                "will": 6, "luck": 5, "move": 5, "body": 6, "emp": 5},
      "hp": 40,
      "skill_levels": {"athletics": 2, "brawling": 3, "conversation": 5,
                       "deduction": 4, "drive_land_vehicle": 4, "evasion": 4,
                       "first_aid": 6, "handgun": 4, "human_perception": 5,
                       "paramedic": 6, "perception": 5, "persuasion": 4,
                       "stealth": 3},
      "armor": "kevlar",
      "weapons": ["heavy_pistol"],
      "cyberware": ["internal_agent"],
      "gear": {"medkit": 2, "first_aid_kit": 1},
      "eurobucks": 50,
      "persona": "街头义医，先救人再问为什么；讨厌还嘴的伤员。",
      "voice": "平静、命令式，动词开头：'坐下。别动。深呼吸。'",
      "desc": "诊所开在废车场，招牌是手写的；看见枪伤就手痒。"
    }
  },
  "default_companions": ["solo_vee", "medtech_taro"],
  "scenes": [
    {
      "id": "the_job",
      "act": 1,
      "location": "南夜之城 · Lazlo 的安全屋",
      "intro": "安全屋在一家倒闭的洗衣店后面，烘干机还在转——电是从街对面偷的。\nLazlo 坐在折叠椅上，右手缠着纱布，把一张写着地址的餐巾纸推给你。",
      "explore": "问清货是什么、几点、谁付钱；检查房间；谈价。",
      "checks": [
        {"skill": "human_perception", "dv": 15,
         "success": "他说'没人会拦你'的时候，视线滑向门口——他在等一个不会来的搭档。",
         "fail": "他笑得很稳，你没看出破绽。"},
        {"skill": "conversation", "dv": 13,
         "success": "你旁敲侧击问出 Wei 的摊位号，还顺手多要了 50 eb 的预付款。",
         "fail": "Lazlo 把话题绕回价钱，什么也没多说。"}
      ],
      "encounter": null,
      "next": "night_market",
      "ip": 30,
      "shop": false,
      "outro": "",
      "encounter_note": "",
      "map": null
    },
    {
      "id": "night_market",
      "act": 2,
      "location": "沃森 · 歌舞伎町夜市",
      "intro": "雨棚下的霓虹把水洼染成三种颜色。摊主们用眼角打量你的枪，没有人抬头。",
      "explore": "找 Wei 验货；打听码头那边的动静；补一点弹药。",
      "checks": [
        {"skill": "perception", "dv": 15,
         "success": "两辆没熄火的摩托停在夜市西口——车牌被泥糊住了，人却不脏。",
         "fail": "夜市里全是人，你分不清谁在等人。"}
      ],
      "encounter": null,
      "next": "the_pier",
      "ip": 30,
      "shop": true,
      "outro": "",
      "encounter_note": "Wei 只有在你通过 perception 检定后才会提一句'码头上有人在等你'。",
      "map": null
    },
    {
      "id": "the_pier",
      "act": 3,
      "location": "旧码头 · 三号仓库外",
      "intro": "仓库卷帘门升到一半，铁链在风里响。集装箱之间积着及踝的水。\n你不是唯一在等这只箱子的人。",
      "explore": "",
      "checks": [],
      "encounter": "e_pier_ambush",
      "next": null,
      "ip": 60,
      "shop": false,
      "outro": "最后一个人倒下时，箱子还立在水里。你听见远处有警笛开始拉长调子。",
      "encounter_note": "码头义体帮会先喊话索要箱子再开枪；腐败警察只想灭口。",
      "map": {
        "image": "assets/maps/pier.jpg",
        "label": "旧码头 · 三号仓库外",
        "image_grid": [20, 12],
        "crop": [6, 2, 12, 8],
        "grid": [12, 8],
        "obstacles": [[0, 0], [0, 1], [5, 3], [5, 4], [6, 3]],
        "party_spawn": [2, 4],
        "tokens": [
          {"id": "npc:broker_wei", "kind": "npc", "name": "Wei", "cell": [11, 7]}
        ]
      }
    }
  ],
  "encounters": {
    "e_pier_ambush": {
      "title": "码头伏击",
      "intro": "卷帘门后面传来两副对讲机的电流声。对面的人不打算讲道理。",
      "enemies": [["corrupt_cop", 2], ["dock_booster", 1]],
      "loot": {"eurobucks": 200, "items": {"ammo": 20, "first_aid_kit": 1}},
      "spawns": {"corrupt_cop": [[9, 2], [9, 6]], "dock_booster": [[10, 4]]}
    }
  },
  "custom_npcs": {
    "dock_booster": {
      "name": "码头义体帮", "en": "Dock Booster",
      "stats": {"int": 4, "ref": 6, "dex": 6, "tech": 3, "cool": 5,
                "will": 5, "luck": 4, "move": 6, "body": 6, "emp": 3},
      "skills": {"athletics": 4, "brawling": 6, "evasion": 5, "handgun": 5,
                 "melee_weapon": 3, "perception": 4, "stealth": 3,
                 "streetwise": 4, "resist_torture_drugs": 3},
      "hp": 30,
      "armor": 7,
      "armor_name": "凯夫拉",
      "weapons": ["heavy_pistol", "medium_melee"],
      "cyberware": ["muscle_enhance"],
      "gear": ["ammo"],
      "role": "solo",
      "reputation": 2,
      "notes": "替码头帮收账的打手；先开枪，再问话。"
    }
  },
  "npcs": {
    "broker_wei": {
      "name": "Wei", "role": "中间人",
      "desc": "夜市东头三号摊的老板，什么货都过手，从不多问。",
      "persona": "和气、算得清、从不赊账。",
      "voice": "先谈价，再谈事；笑的时候眼睛不笑。"
    }
  },
  "tokens": {
    "broker_wei": "assets/tokens/broker_wei.png"
  },
  "dm": {
    "persona": "一个见过太多尸体、懒得评价的老主持人；镜头贴着雨和霓虹走。",
    "voice": "短句、现在时、第二人称；不解释规则，只描述后果。",
    "system_prompt": "",
    "depth_prompt": "记住：RED 的钱是欧元（eb），伤势分轻伤/重伤/濒死，别用别的说法。",
    "book": {}
  },
  "requires": []
}
```

## 12. 质量检查清单

- [ ] `python main.py --module <id> --demo --seed 7` 能完整通关（无异常、不团灭）
- [ ] 场景链无环、无孤场景；最终场景 `next: null`
- [ ] 每个带 `encounter` 的场景都在战斗后给出 `outro` 或衔接文本
- [ ] 每场戏的 `ip` 在 20-60 之间（按人计，同一场景只发一次）；合计不超过 4000；成长节奏与内容量匹配
- [ ] 每个场景的 `checks[].skill` 都是 41 项技能里的 key，`dv` 在 9-30 且难度递进
- [ ] 遭遇里的敌人 key 全部有定义（内置表或 `custom_npcs`），数量与队伍实力匹配
- [ ] 声明了 `map` 的场景：网格 4-64、障碍/棋子/落位坐标都在界内、交战距离在 1-25 米带
- [ ] 自定义敌人的 `stats` 十项齐全、技能与武器自洽（PW 与体格一致）
- [ ] 预设角色 2 名以上、覆盖不同职业；`default_companions` 只引用存在的 key
- [ ] 资产路径全部相对、扩展名合法，文件确实存在于 `assets/` 下
- [ ] 文本用中文（**人名除外：角色名/NPC 名/敌人专名一律英文**；地名与
  `腐败警察`/`摩托增援` 这类类型标签仍用中文）、无未翻译残留；
  不含旧规则词汇（旧的难度等级/护甲等级/休整/货币术语）
- [ ] 没有把机制数值写进叙述文本；没有使用轻松模式未收录的子系统
  （自动武器、网络空间、载具战斗、武术、药物）
- [ ] 世界观只谈夜之城与红时，别把别的 IP 的地名、族类或神明搬进来
