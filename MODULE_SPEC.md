# CTRCG 剧本（模组）开发规范

版本：2.0（完整 5e 规则引擎）　适用引擎：`ctrcg` ≥ 0.4

一个**剧本（模组）**是一个自包含的 JSON 文件：`modules/<模组id>/module.json`。
引擎按"被遗忘的大陆 · D&D 5e（SRD 5.1）"规则运行剧本；所有骰点由规则引擎
完成，LLM 只做决策与叙述，**不生成任何数值结果**。

- 内置剧本：`modules/starfall`（星坠矿脉的阴影）、`modules/saltmist`（盐雾灯塔的低语）。
- 两者即为规范模板：不确定怎么写时，直接对照内置剧本抄结构。
- 运行时产生的 `generated-*` / `imported-*` 目录同样遵循本规范。

## 1. 快速开始

```bash
# 1. 建立目录
mkdir modules/my_module
# 2. 编写 modules/my_module/module.json（本规范）
# 3. 启动游戏
python main.py --web        # 开始页 → 新的冒险 → 选择 my_module
python main.py --module my_module --demo --seed 7   # 终端自动验证全流程
```

剧本加载即校验（"坏输入大声失败"）：错误会给出明确条目；加载器同时做
**场景链完整性**与**经验预算**检查。

## 2. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✔ | 1-48 字符，`[A-Za-z0-9_-]`；必须与目录名一致 |
| `title` / `en_title` | string | ✔/– | 中文标题 / 英文标题 |
| `setting` | string | ✔ | 舞台设定（1-2 句）。请使用被遗忘的大陆/剑湾的真实地名，不要生造官方设定 |
| `synopsis` | string | ✔ | 剧情梗概（3-4 句） |
| `act_titles` | object | ✔ | `{\"1\": \"第一幕标题\", ...}`，幕号 1-9 |
| `epilogue` | string | ✔ | 结局叙述，`\n` 分行 |
| `scenes` | array | ✔ | 场景列表（见 §4） |
| `encounters` | object | ✔ | 遭遇表（见 §5） |
| `companions` | array | – | 剧本专属队友（见 §3）；**缺省时使用默认三人组** |
| `custom_monsters` | object | – | 剧本自定义怪物（见 §6） |
| `party_level_start` | int | – | 起始等级（默认 1） |
| `expected_end_level` | int | – | 预期结束等级（默认 3，上限 5） |

### 经验预算（校验器会警告）

全部怪物 XP 总和 + 全部 `milestone` 之和，按四人队平分后应 **≥ 900**
（升到 3 级所需），超过 6600 会提示溢出。内置剧本的人均预算约 1000-1100。

## 3. 队友（companions）——队友由剧本决定

每个剧本自带队友；玩家只创建自己的角色，队友由 AI Agent 代打。
**未提供 `companions` 时，引擎回退到默认三人组**（星坠矿脉的罗兰/薇兰/皮普）。

```json
"companions": [
  {
    "id": "garruk",
    "name": "加洛·裂颅",
    "race": "half_orc",            // 种族 key，见 §7 种族表
    "klass": "barbarian",          // 职业 key，12 选 1
    "background": "outlander",     // 出身 key，见 §7 出身表
    "fighting_style": "",          // fighter 创建时必填；paladin/ranger 2 级自动获得
    "scores": {"str": 15, "dex": 13, "con": 14, "int": 8, "wis": 12, "cha": 10},
    "skills": ["athletics", "survival"],   // 从该职业技能列表中选（数量=skill_count）
    "persona": "人设（AI 代打的依据，含战斗风格描述）",
    "voice": "语气"
  }
]
```

- `scores` 为**种族加值前**的六项基础值（引擎会询问/默认套用 +2/+1）。
- 升级到 2/3 级时的职业特性（战斗风格、子职业、魔契恩泽等）由引擎按
  推荐默认自动选择；玩家的选择由游戏内交互完成。
- 建议 3 名队友覆盖"坦克/治疗/输出"三角，并尽量使用不同职业展示机制。

## 4. 场景（scenes）

场景为**线性链**：从第一个场景沿 `next` 走到唯一的 `null` 终点；
不允许环、不允许链外孤场景（校验器强制）。

```json
{
  "id": "wolf_den",
  "act": 1,                        // 幕号，对应 act_titles
  "location": "山坳匪巢 · 狼穴隧道",
  "intro": "进场叙述，\n 分行。",
  "explore": "可做的事（引导词）",
  "skills": [                      // 可选：场景内检定机会
    {
      "skill": "stealth",          // 18 项 5e 技能 key 之一
      "dc": 13,                    // 5-30
      "success": "成功文本",
      "fail": "失败文本"
    }
  ],
  "encounter": "e_wolves",         // 或 null（无战斗）
  "next": "boss_den",              // 下一场景 id；最终场景为 null
  "milestone": 300,                // 离开本场景时的剧情奖励 XP（每人份额=÷4）
  "level_to": 2,                   // 可选：进入本场景时整队升到该级（里程碑升级）
  "outro": "战斗后叙述（可选）",
  "encounter_note": "补充说明（可选，仅供 DM 叙事参考）"
}
```

**突袭联动**：若场景中安排 `stealth` 检定且玩家成功，紧随其后的遭遇中
敌人按 5e 突袭（Surprise）规则第一轮无法行动。把潜行检定放在带战斗的
场景里即可自动生效。

**升级节奏建议**：狼穴/第一幕后 `level_to: 2`，第二幕后 `level_to: 3`
（与官方入门模组一致）；1-3 级内容请用 CR 1/8~3 的怪物。

## 5. 遭遇（encounters）

```json
"e_wolves": {
  "title": "狼穴",
  "dark": true,                    // 黑暗环境：无黑暗视觉/无光源者攻击劣势
  "intro": "开战叙述",
  "monsters": [["worg", 2], ["goblin", 1]],   // [怪物id, 数量]
  "loot": {"gold": 0, "items": {"healing_potion": 1}}
}
```

- 怪物 id 来自内置表（§7 生物表）或本剧本 `custom_monsters`。
- 同类多只需数量，引擎自动编号（哥布林1、哥布林2…）。
- `loot.items` 的 key 必须是 `D.ITEMS` 中的物品
  （`healing_potion`/`torch`/`rope`/`gold`）。

## 6. 自定义怪物（custom_monsters）

字段与引擎怪物表同构。引擎按 5e 规则结算：先攻 d20+敏捷、攻击 d20+加值
vs AC、暴击骰翻倍、死亡/状态/抗性全套。

```json
"salt_witch": {
  "name": "盐巫·摩尔加丝",
  "en": "Salt Witch Morgath",
  "cr": 3, "xp": 700,              // CR 与 XP 必须与 SRD 换算一致（CR3=700）
  "hp": "9d8", "ac": 13, "speed": 30,
  "abilities": {"str": 9, "dex": 14, "con": 12, "int": 16, "wis": 14, "cha": 17},
  "traits": ["施法者", "盐雾护体（对火焰伤害有抗性）"],
  "attacks": [
    {"weapon": "spell:salt_javelin", "bonus": 5, "dmg": "2d8", "extra": "0", "dtype": "cold"},
    {"weapon": "spell:salt_burst", "bonus": 0, "save_dc": 14, "save": "con",
     "dmg": "3d8", "extra": "0", "dtype": "cold", "spell_level": 2},
    {"weapon": "scimitar", "bonus": 4, "dmg": "1d6", "extra": "+2", "multiattack": 2,
     "onhit": "poison_1d8_dc11"}
  ],
  "darkvision": 60,
  "fire_resist": true
}
```

### 6.1 attack 字段

| 字段 | 说明 |
|------|------|
| `weapon` | 武器 key（§7 武器表）、天生武器（`bite/claws/slam/beak/sting/gore/hooves/web/greatclub/fire_ray`）或 `spell:<名>` 法术攻击 |
| `bonus` | 攻击加值（含属性+熟练；法术豁免类填 0） |
| `dmg` | 伤害骰表达式（如 `"2d6"`；天生武器必填） |
| `extra` | 附加固定伤害，如 `"+2"` |
| `multiattack` | 每轮该击次数（如怪物的多重攻击） |
| `follow` | 组合攻击：该击命中判定后接续一次指定武器攻击（如 SRD"矛+噬咬"、"喙+爪击"），填另一攻击的 weapon 键 |
| `save_dc`/`save`/`spell_level` | 法术豁免攻击三件套（`spell:` 前缀）；`spell_level` 供反制法术判定 |
| `dtype` | 法术伤害类型（`fire/cold/acid/lightning/poison/necrotic/radiant/force/psychic/thunder`） |
| `onhit` | 命中后效果（见 6.2） |

### 6.2 onhit 关键字（引擎机制）

| onhit | 效果 |
|-------|------|
| `poison_<表达式>_dc<n>` | 豁免失败受额外毒素伤害并陷入中毒（10轮） |
| `prone_dc<n>_str` | 力量豁免失败被扑倒 |
| `charge_prone_dc<n>_str` | 冲锋扑倒（配合 `冲锋` trait） |
| `paralyze_dc<n>_con` | 体质豁免失败麻痹（10轮，每轮再豁免；**精灵免疫**） |
| `web_dc<n>` | 力量豁免失败被束缚（10轮，每轮再豁免） |
| `drain` | 生命汲取：削减目标生命上限，攻击者回复等量 |

### 6.3 traits 引擎关键字

| 关键字 | 机制 |
|--------|------|
| `施法者` | 半血以下隔轮使用爆燃法术；施法时可被反制法术打断 |
| `突袭` | 首轮命中 +2d6 |
| `伏击` | 先攻 +2 |
| `不死者坚韧` | 0 HP 时体质豁免 DC 5+伤害，成功以 1 HP 存活 |
| `军团战术` | 队友贴身目标时伤害 +2d6 |
| `饕餮狂暴` | 击倒敌人后以附赠动作噬咬 |
| `格挡` | 反应 AC+2，可能令近战攻击落空 |
| `冲锋` | 对未纠缠目标首击 +1d6 |
| `借机潜袭` | 附赠动作撤离或躲藏 |
| `随时隐形` | 先攻即进入隐形（攻击不解除） |
| `成群狩猎` | 队友贴身目标时攻击优势 |
| `扩大术` | 第 2 轮起近战伤害 +1d4 |
| `不死者` | 免疫毒素/睡眠/麻痹之爪等 |
| 其余文本 | 叙事呈现（DM 上下文可见） |

### 6.4 怪物豁免熟练（可选）

字段 `"save_prof": {"con": true, "wis": true}`：声明该怪物哪些属性豁免带熟练加值
（熟练加值按 CR 取：CR0-4 为 +2，CR5+ 为 +3）。未声明时按属性调整值豁免。
借机攻击只结算一次攻击（不触发 `multiattack`/`follow`）。

### 6.5 抗性 / 免疫字段

`"fire_resist": true`、`"poison_resist": true`、`"poison_immune": true`。
其余抗性写在 traits 中由 DM 叙事处理。

## 7. 内置 key 速查

### 种族（BG3 全 11 族 + 亚种）
`human, high_elf, wood_elf, drow_lolth, drow_seldarine, half_elf_high,
half_elf_wood, half_elf_drow, dwarf_shield, dwarf_gold, duergar,
lightfoot_halfling, strongheart_halfling, forest_gnome, rock_gnome,
deep_gnome, tiefling_asmodeus, tiefling_mephisto, tiefling_zariel,
half_orc, githyanki, dragonborn_{black,blue,brass,bronze,copper,gold,
green,red,silver,white}`

种族特性全部机制化（幸运重骰/豁免优势/抗性/龙息/不屈坚韧/星界知识等）。
属性加值采用官方可选规则（Tasha's 万能起源）：任意两项 +2/+1。

### 职业（12）
`barbarian 野蛮人, bard 吟游诗人, cleric 牧师, druid 德鲁伊, fighter 战士,
monk 武僧, paladin 圣武士, ranger 游侠, rogue 游荡者, sorcerer 术士,
warlock 邪术师, wizard 法师` —— 1-5 级特性全部按 PHB 2014 机制实现
（动作经济/资源池/额外攻击/魔契法术位/野变形态等）。

### 出身（11）
`acolyte, charlatan, criminal, entertainer, folk_hero, guild_artisan,
noble, outlander, sage, soldier, urchin`

### 武器
`dagger, handaxe, mace, quarterstaff, shortsword, longsword, greatsword,
scimitar, rapier, morningstar, greataxe, warhammer, spear, shortbow,
longbow, light_crossbow, sling`

### 技能（18）
`athletics, acrobatics, sleight_of_hand, stealth, arcana, history,
investigation, nature, religion, animal_handling, insight, medicine,
perception, survival, deception, intimidation, performance, persuasion`

### 生物（内置表）
`goblin(CR1/4), skeleton(1/4), zombie(1/4), drowned_one*(1/4), wolf(1/4),
giant_badger(1/4), riding_horse(1/4), bandit(1/8), hobgoblin(1/2),
worg(1/2), gnoll(1/2), black_bear(1/2), duergar(1), giant_spider(1),
ghoul(1), imp(1), wrecker_boss*(1), hook_horror(2), ettercap(2),
bandit_captain(2), ogre(2), ash_mage(2), phase_spider(3), redcap(3),
owlbear(3), minotaur(3), wight(3), salt_witch*(3), bulette(5), cambion(5)`
（`*` 为盐雾剧本自定义怪，引用需自带定义或复制定义）

## 8. 质量检查清单

- [ ] `python main.py --module <id> --demo --seed 7` 能完整通关（无异常、非团灭）
- [ ] 场景链无环、无孤场景；最终场景 `next: null`
- [ ] 每个带 `encounter` 的场景都在战斗后给出 `outro` 或衔接文本
- [ ] 人均 XP 预算 900-2500；战斗难度递进；至少一场 boss 战
- [ ] 自定义怪物的 CR/XP 与 SRD 换算一致（CR1/4=50 … CR3=700 … CR5=1800）
- [ ] 文本全部中文、无未翻译词残留；地名使用 FR 正典或自洽原创
- [ ] 未在剧本中写入任何机制数值的新规则——机制一律走引擎
