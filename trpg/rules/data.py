"""《赛博朋克 RED》规则数据。

数据来源：R. Talsorian Games《Cyberpunk RED》与免费入门册
《Cyberpunk RED Easy Mode》（本仓库以其中文版为准：
``.reference/Cyberpunk_RED_Easy_Mode_CN.pdf``）。

口径约定（中文版译自波兰文版，缩写沿用其含义）：

* ``PT``　难度值（Difficulty Value, DV）
* ``PW``　生命值（Hit Points, HP）
* ``OB``　护甲（Stopping Power, SP）
* ``LA``　攻击次数（Rate of Fire, ROF）
* ``ZW``　敏捷　``SW``　意志力　``CHA``　魅力　``BC``　体格　``SZ``　幸运
* ``RUCH``　机动

本模块只放**静态数据**：属性/技能/职业/武器/护甲/装备/义体/难度值/伤势/
致命伤/掩体/敌人。所有算术与结算都在 :mod:`trpg.rules.character` 与
:mod:`trpg.rules.combat`。
"""

from __future__ import annotations

# =====================================================================
# 属性（十项）
# =====================================================================
#: 角色卡上的属性顺序（照抄官方角色卡排版）
STAT_ORDER: tuple[str, ...] = ("int", "ref", "dex", "tech", "cool",
                               "will", "luck", "move", "body", "emp")

#: 属性 key → 显示信息。``abbr`` 为中文版沿用的波兰文缩写。
STATS: dict[str, dict] = {
    "int": {"name": "智力", "abbr": "INT", "en": "Intelligence",
            "desc": "整体推理能力。"},
    "ref": {"name": "反应", "abbr": "REF", "en": "Reflexes",
            "desc": "反应速度与动作协调。"},
    "dex": {"name": "敏捷", "abbr": "ZW", "en": "Dexterity",
            "desc": "整体身体素质，让你保持平衡、跳跃、跳下、战斗以及做其他类似动作。"},
    "tech": {"name": "技术", "abbr": "TECH", "en": "Technical",
             "desc": "操作工具与仪器的能力。"},
    "cool": {"name": "魅力", "abbr": "CHA", "en": "Cool",
             "desc": "以气场与人格给他人留下印象并影响他人的能力。"},
    "will": {"name": "意志力", "abbr": "SW", "en": "Willpower",
             "desc": "你的决心，以及应对危险或压力的能力。"},
    "luck": {"name": "幸运", "abbr": "SZ", "en": "Luck",
             "desc": "特殊属性。幸运池点数等于幸运值，每轮开始时恢复。"},
    "move": {"name": "机动", "abbr": "RUCH", "en": "Movement",
             "desc": "你奔跑、跳跃、游泳等时的移动速度。"},
    "body": {"name": "体格", "abbr": "BC", "en": "Body",
             "desc": "你的体型、耐力，以及凭借块头保持呼吸与清醒的能力。"},
    "emp": {"name": "共情", "abbr": "EMP", "en": "Empathy",
            "desc": "理解与体察他人情绪感受、并对这些感受做出反应的能力。"},
}

#: 属性常规上下限（角色创建用；义体与药物可以突破上限）
STAT_MIN = 2
STAT_MAX = 8
#: 属性点池（主规则书口径）
STAT_POOL = 62

# =====================================================================
# 技能
# =====================================================================
#: ``stat`` 为挂钩属性；``x2`` 表示提升时每级消耗双倍改进点。
SKILLS: dict[str, dict] = {
    "accounting": {"name": "会计学", "en": "Accounting", "stat": "int",
                   "desc": "记账、伪造账目并识破此类伪造、周旋于金钱之间、编制预算的能力。"},
    "acting": {"name": "表演", "en": "Acting", "stat": "cool",
               "desc": "进入角色、假扮真实或虚构人物、伪装情绪与心情的能力。"},
    "athletics": {"name": "运动", "en": "Athletics", "stat": "dex",
                  "desc": "跳跃、攀爬、投掷、游泳、举重等。"},
    "brawling": {"name": "斗殴", "en": "Brawling", "stat": "dex",
                 "desc": "使用蛮力进行打斗与摔跤的能力，也用于擒抱、锁喉与投掷。"},
    "bribery": {"name": "贿赂", "en": "Bribery", "stat": "cool",
                "desc": "你能识别容易收买的人，知道如何接近他们、该出多少钱。"},
    "bureaucracy": {"name": "官僚体系", "en": "Bureaucracy", "stat": "int",
                    "desc": "与官僚打交道：知道该找谁办事、如何找到此人、以及如何突破官僚壁垒。"},
    "business": {"name": "做生意", "en": "Business", "stat": "int",
                 "desc": "了解基本商业惯例、供需法则、管理人员、采购、销售与营销原则。"},
    "composition": {"name": "作曲", "en": "Composition", "stat": "int",
                    "desc": "以专业水准写歌、写文章、写故事的能力。"},
    "conceal_reveal": {"name": "藏匿/搜寻物品", "en": "Conceal/Reveal Object",
                       "stat": "int",
                       "desc": "藏匿物品与搜寻隐藏物的技能，也用于把武器藏在衣服下。"},
    "concentration": {"name": "专注", "en": "Concentration", "stat": "will",
                      "desc": "集中精神、运用思维与记忆、忽略干扰、以及对自身身体的大师级控制。"},
    "conversation": {"name": "交谈", "en": "Conversation", "stat": "emp",
                     "desc": "在对话中套取信息而不让对方察觉的能力。"},
    "criminology": {"name": "刑侦学", "en": "Criminology", "stat": "int",
                    "desc": "通过指纹、弹道检测、证据分析与查阅警方档案来获取线索的能力。"},
    "cryptography": {"name": "密码学", "en": "Cryptography", "stat": "int",
                     "desc": "编码与解码信息的能力。"},
    "dancing": {"name": "舞蹈", "en": "Dancing", "stat": "dex",
                "desc": "专业水准的舞蹈能力。"},
    "deduction": {"name": "推理", "en": "Deduction", "stat": "int",
                  "desc": "发现线索之间联系、得出非显而易见的结论与医疗诊断的能力。"},
    "drive_land_vehicle": {"name": "载具驾驶", "en": "Drive Land Vehicle", "stat": "ref",
                           "desc": "驾驶与操控陆地载具的能力。"},
    "education": {"name": "教育", "en": "Education", "stat": "int",
                  "desc": "小学水平的通用知识：能读会写，掌握基本的数学与历史常识。"},
    "electronics_security": {"name": "电子学与安防", "en": "Electronics/Security Tech",
                             "stat": "tech", "x2": True,
                             "desc": "识别、理解原理、修理、反制与安装复杂电子设备的能力。"},
    "evasion": {"name": "闪避", "en": "Evasion", "stat": "dex",
                "desc": "近战中脱离攻击线的技能。REF 8 或更高的角色可用它躲避远程武器的攻击。"},
    "first_aid": {"name": "急救", "en": "First Aid", "stat": "tech",
                  "desc": "采取医疗措施以预防最常见致命伤的后果、挽救伤者生命的能力。"},
    "forgery": {"name": "伪造", "en": "Forgery", "stat": "tech",
                "desc": "制作与识别伪造文件和身份证件的能力。"},
    "handgun": {"name": "手枪", "en": "Handgun", "stat": "ref",
                "desc": "有效使用手枪等短管射击武器进行射击的能力。"},
    "human_perception": {"name": "读心", "en": "Human Perception", "stat": "emp",
                         "desc": "通过面部表情与肢体语言判断对话者情绪状态、识破谎言或欺骗企图的能力。"},
    "interrogation": {"name": "审讯", "en": "Interrogation", "stat": "cool",
                      "desc": "以强制手段从他人口中套取信息的能力。"},
    "library_search": {"name": "数据库检索", "en": "Library Search", "stat": "int",
                       "desc": "使用数据库、数据银行、图书馆及其他信息汇总来源的能力。"},
    "local_expert": {"name": "本地知识", "en": "Local Expert", "stat": "int",
                     "desc": "对特定区域及其居住帮派的深入了解。提升时选择一个具体区域。"},
    "melee_weapon": {"name": "近战武器", "en": "Melee Weapon", "stat": "dex",
                     "desc": "使用冷兵器战斗的能力。"},
    "paramedic": {"name": "医疗救援", "en": "Paramedic", "stat": "tech", "x2": True,
                  "desc": "采取医疗措施以预防所有无需手术的致命伤后果的能力"
                          "（手术仅医疗师可用）。"},
    "perception": {"name": "感知", "en": "Perception", "stat": "int",
                   "desc": "发现隐藏线索、陷阱、以及使用潜行技能者的能力。"},
    "personal_grooming": {"name": "时尚与风格", "en": "Personal Grooming", "stat": "cool",
                          "desc": "知道穿什么、何时穿什么的知识。"},
    "persuasion": {"name": "说服", "en": "Persuasion", "stat": "cool",
                   "desc": "说服并影响他人的能力。"},
    "photography_film": {"name": "摄影/摄像", "en": "Photography/Film", "stat": "tech",
                         "desc": "拍照、拍片与超梦体验的能力。"},
    "pick_lock": {"name": "开锁", "en": "Pick Lock", "stat": "tech",
                  "desc": "打开机械锁的能力。"},
    "pick_pocket": {"name": "扒窃", "en": "Pick Pocket", "stat": "tech",
                    "desc": "秘密拿走他人物品的能力；从商店小偷小摸。"},
    "play_instrument": {"name": "乐器演奏", "en": "Play Instrument", "stat": "tech",
                        "desc": "专业演奏乐器的能力。提升时选择一种乐器专攻。"},
    "resist_torture_drugs": {"name": "抗酷刑/抗药物", "en": "Resist Torture/Drugs",
                             "stat": "will",
                             "desc": "抵御痛苦处置（包括酷刑与药物影响）的能力。"},
    "shoulder_arms": {"name": "远程武器", "en": "Shoulder Arms", "stat": "ref",
                      "desc": "有效使用长管射击武器的能力，包括霰弹枪与步枪。"},
    "stealth": {"name": "潜行", "en": "Stealth", "stat": "dex",
                "desc": "无声移动、隐蔽、以谨慎方式行动而不被发现的能力。"},
    "streetwise": {"name": "地下世界知识", "en": "Streetwise", "stat": "cool",
                   "desc": "建立并利用人脉获取非法货物、与罪犯交谈、以及在城市破败街区"
                           "避开危险处境。"},
    "tactics": {"name": "战术", "en": "Tactics", "stat": "int",
                "desc": "高效管理大规模战斗的能力，能预判敌人可能的反应。"},
    "tracking": {"name": "追踪", "en": "Tracking", "stat": "int",
                 "desc": "追踪留下踪迹或其他线索者的能力。"},
}

#: 技能 key 顺序（界面按此顺序列出）
SKILL_ORDER: tuple[str, ...] = tuple(SKILLS)

# =====================================================================
# 难度值（PT / DV）
# =====================================================================
#: 难度值 → 难度类型与描述（轻松模式第 17 页难度表）
DIFFICULTY: dict[int, dict] = {
    13: {"name": "日常",
         "desc": "大多数未受过专门训练的人都能完成的动作。"},
    15: {"name": "困难",
         "desc": "未经训练或没有天赋很难完成的动作。"},
    17: {"name": "专业",
         "desc": "完成此类动作需要受过训练；能做到的人被视为该领域的专业人士。"},
    21: {"name": "英雄",
         "desc": "非凡的壮举，只有最顶尖的人才能达成。"},
    24: {"name": "不可思议",
         "desc": "超凡的成就。做到这种事会让同行都承认你是最棒的。"},
}

#: 检定允许的难度值范围（模组校验器用）
DV_MIN, DV_MAX = 9, 30

#: 技能检定修正
CHECK_CRIT_SUCCESS_FACES = 10      # 暴击成功：d10 掷出 10，追加一次 d10 并相加
CHECK_CRIT_FAILURE_FACES = 1       # 暴击失败：d10 掷出 1，追加一次 d10 并相减
CHECK_COMPLEMENTARY_BONUS = 1      # 辅助技能检定 +1
CHECK_EXTRA_TIME_BONUS = 1         # 投入四倍时间 +1
CHECK_EXTRA_TIME_FACTOR = 4

# =====================================================================
# 远程战斗难度值（第 19 页表）
# =====================================================================
#: 射程带（米，闭区间）
RANGE_BANDS: tuple[tuple[int, int], ...] = (
    (0, 6), (7, 12), (13, 25), (26, 50),
    (51, 100), (101, 200), (201, 400), (401, 800),
)

#: 武器类别 → 每个射程带的难度值（None = 超出该武器的射程）
RANGED_DV: dict[str, tuple] = {
    "pistol": (13, 15, 20, 25, 30, 30, None, None),
    "shotgun": (13, 15, 20, 25, 30, 35, None, None),
    "rifle": (17, 16, 15, 13, 15, 20, 25, 30),
    # 轻松模式第 19 页「远程战斗难度值」表只有三类：手枪 / 霰弹枪 / 突击步枪。
    # 冲锋枪、狙击步枪、弓弩、榴弹、火箭都不在轻松模式里 —— 不予实现，
    # 剧本与模组也不得声明（见 README「明确未实现」）。
}

#: 武器类别中文名
WEAPON_TYPES: dict[str, str] = {
    "pistol": "手枪", "shotgun": "霰弹枪", "rifle": "突击步枪",
    "melee": "近战", "brawling": "斗殴",
}

# =====================================================================
# 战斗常量
# =====================================================================
#: 每回合的动作预算：一个移动动作 + 一个其他动作
ACTIONS_PER_TURN = {"move": 1, "action": 1}

#: 一回合约 3 秒（叙事用）
TURN_SECONDS = 3

#: 移动距离 = 机动 × 此系数（米；网格模式下为格数）
MOVE_FACTOR = 2

#: 白刃战无视防守方护甲的比例
MELEE_ARMOR_PIERCE = 0.5

#: 斗殴每次攻击动作的攻击次数
BRAWLING_ATTACKS = 2

#: 擒抱/被控制时的动作修正
GRAPPLE_PENALTY = -2

#: 锁喉连续轮数达到此值即使目标昏迷
CHOKE_KO_ROUNDS = 3

#: 远程攻击若 REF 达到该值，可用闪避技能躲避子弹
EVASION_BULLET_REF = 8

#: 致命伤：伤害骰中出现 6 的数量达到此值即触发
CRIT_INJURY_SIXES = 2
#: 致命伤附加伤害（无视护甲）
CRIT_INJURY_BONUS_DAMAGE = 5
#: 触发致命伤所需的最少伤害骰数
CRIT_INJURY_MIN_DICE = 2

# =====================================================================
# 伤势等级
# =====================================================================
#: 伤势等级 key → 名称与修正
WOUND_STATES: dict[str, dict] = {
    "healthy": {"name": "完好", "check_penalty": 0, "move_penalty": 0},
    "light": {"name": "轻伤", "check_penalty": 0, "move_penalty": 0,
              "desc": "PW 低于满值时即为轻伤。此状态不附带任何额外惩罚。"},
    "serious": {"name": "重伤", "check_penalty": -2, "move_penalty": 0,
                "desc": "当前 PW 等于或低于上限一半时即为重伤，所有检定承受 -2 惩罚。"},
    "mortal": {"name": "濒死", "check_penalty": -4, "move_penalty": -6,
               "desc": "PW 低于 1 时即为濒死：所有检定 -4，移动 -6（不得低于 1），"
                       "且每回合开始需进行生存检定。"},
}
#: 伤势严重度顺序（越靠后越严重）
WOUND_ORDER: tuple[str, ...] = ("healthy", "light", "serious", "mortal")

#: 稳定（Stabilize）难度值：按目标当前伤势等级
STABILIZE_DV: dict[str, int] = {"light": 10, "serious": 13, "mortal": 15}
#: 稳定成功后自然恢复：每个完整休息日恢复的 PW 等于体格
REST_HEAL_PER_DAY_STAT = "body"
#: 致命伤「治疗」每次尝试耗时（小时）
CRIT_TREATMENT_HOURS = 4
#: 致命伤「急救固定」耗时（分钟）
CRIT_STABILIZE_MINUTES = 1

#: 生存检定（Death Save）
DEATH_SAVE = {
    "dice": "1d10",
    "auto_fail_face": 10,      # 掷出 10 自动失败
    "cumulative": 1,           # 每做一次生存检定，此后每次 +1 修正（越来越难）
    "stabilized_hp": 1,        # 被稳定后 PW 回到 1
}

# =====================================================================
# 致命伤表（2d6，第 22 页）
# =====================================================================
#: ``effects`` 为机制关键字，引擎按此结算；``text`` 为原书说明。
#: 关键字：drop_items（立刻放下该手持物）、death_save_plus（生存检定基础难度 +N）、
#: move_minus（移动 -N，下限 1）、move_hurt（一回合内步行移动超过 N 米则回合结束
#: 再受该伤伤害，无视护甲）、arm_useless（该臂不可用）、melee_minus（近战武器攻击 -N）、
#: no_action_next（下回合不能执行动作）、hand_minus（用该手的动作 -N）、
#: no_evade（无法闪避）。
CRIT_INJURIES: dict[int, dict] = {
    2: {"name": "手臂撕脱",
        "text": "手臂与躯干分离。你立刻放下该手所持的所有物品。生存检定基础难度 +1。",
        "effects": [{"kind": "drop_items"}, {"kind": "death_save_plus", "value": 1}],
        "first_aid_stabilize_dv": None,
        "treatment": {"skill": "paramedic", "dv": 17}},
    3: {"name": "手掌切断",
        "text": "手掌与手臂分离。你立刻放下该手所持的所有物品。生存检定基础难度 +1。",
        "effects": [{"kind": "drop_items"}, {"kind": "death_save_plus", "value": 1}],
        "first_aid_stabilize_dv": None,
        "treatment": {"skill": "paramedic", "dv": 17}},
    4: {"name": "肺萎陷",
        "text": "移动 -2（最低 1）。生存检定基础难度 +1。",
        "effects": [{"kind": "move_minus", "value": 2},
                    {"kind": "death_save_plus", "value": 1}],
        "first_aid_stabilize_dv": 15,
        "treatment": {"skill": "paramedic", "dv": 15}},
    5: {"name": "肋骨骨折",
        "text": "每当你在一个回合内步行移动超过 4 米，该回合结束时再次受到该伤的"
                "额外伤害。伤害无视护甲。",
        "effects": [{"kind": "move_hurt", "threshold_m": 4}],
        "first_aid_stabilize_dv": 13,
        "treatment": {"skill": "first_aid", "dv": 15}},
    6: {"name": "手臂骨折",
        "text": "骨折的手臂无法使用。你立刻放下该手所持的所有物品。",
        "effects": [{"kind": "drop_items"}, {"kind": "arm_useless"}],
        "first_aid_stabilize_dv": 13,
        "treatment": {"skill": "first_aid", "dv": 15}},
    7: {"name": "异物残留",
        "text": "每当你在一个回合内步行移动超过 4 米，该回合结束时再次受到该伤的"
                "额外伤害。伤害无视护甲。",
        "effects": [{"kind": "move_hurt", "threshold_m": 4}],
        "first_aid_stabilize_dv": 13,
        "treatment": {"skill": "first_aid", "dv": 13, "permanent": True}},
    8: {"name": "腿部骨折",
        "text": "移动 -4（最低 1）。",
        "effects": [{"kind": "move_minus", "value": 4}],
        "first_aid_stabilize_dv": 13,
        "treatment": {"skill": "first_aid", "dv": 15}},
    9: {"name": "肌肉拉伤",
        "text": "近战武器攻击 -2。",
        "effects": [{"kind": "melee_minus", "value": 2}],
        "first_aid_stabilize_dv": 13,
        "treatment": {"skill": "first_aid", "dv": 13, "permanent": True}},
    10: {"name": "脊柱损伤",
         "text": "下一回合你不能执行动作，但可以执行移动动作。生存检定基础难度 +1。",
         "effects": [{"kind": "no_action_next"},
                     {"kind": "death_save_plus", "value": 1}],
         "first_aid_stabilize_dv": 15,
         "treatment": {"skill": "paramedic", "dv": 15}},
    11: {"name": "手指粉碎",
         "text": "用该手执行的所有动作 -4。",
         "effects": [{"kind": "hand_minus", "value": 4}],
         "first_aid_stabilize_dv": 13,
         "treatment": {"skill": "paramedic", "dv": 15}},
    12: {"name": "腿部切断",
         "text": "腿与躯干分离。移动 -6（最低 1）。你无法闪避攻击。"
                 "生存检定基础难度 +1。",
         "effects": [{"kind": "move_minus", "value": 6}, {"kind": "no_evade"},
                     {"kind": "death_save_plus", "value": 1}],
         "first_aid_stabilize_dv": None,
         "treatment": {"skill": "paramedic", "dv": 17}},
}

# =====================================================================
# 掩体（第 21 页表）
# =====================================================================
#: 掩体材质 → 耐久（可对 2m × 2m 区域发起攻击，降至 0 即被摧毁）
COVER: dict[str, int] = {
    "厚钢": 50, "薄钢": 25,
    "厚石": 40, "薄石": 20,
    "厚防弹玻璃": 30, "薄防弹玻璃": 15,
    "厚混凝土": 25, "薄混凝土": 10,
    "厚木": 20, "薄木": 5,
    "厚灰泥/泡沫/塑料": 15,
}

# =====================================================================
# 职业（Role）
# =====================================================================
#: 轻松模式给出五名预设角色，覆盖其中五个职业；其余五个职业保留名称与定位，
#: 其特殊能力在轻松模式中未展开。
ROLES: dict[str, dict] = {
    "solo": {"name": "独行侠", "en": "Solo", "nickname": "枪手",
             "desc": "职业杀手、保镖、雇佣枪手。用钢铁与直觉解决问题。",
             "ability": {"name": "战斗直觉", "en": "Combat Awareness",
                         "desc": "所有先攻检定 +4。"}},
    "netrunner": {"name": "网络行者", "en": "Netrunner", "nickname": "黑客",
                  "desc": "用大脑直接接入网络架构的赛博牛仔。",
                  "ability": {"name": "接口", "en": "Interface",
                              "desc": "接入网络架构，在赛博空间里穿行与作战。"}},
    "tech": {"name": "技术师", "en": "Tech", "nickname": "技工",
             "desc": "什么都能修，什么都能造，什么都敢拆。",
             "ability": {"name": "改造", "en": "Maker",
                         "desc": "发明、改造、修理与拆解装备，也能临时强化武器与护甲。"}},
    "medtech": {"name": "医疗师", "en": "Medtech", "nickname": "义医",
                "desc": "在枪伤与义体之间讨生活的街头外科医生。",
                "ability": {"name": "医疗", "en": "Medicine",
                            "desc": "掌握外科手术与义体植入；只有医疗师能用外科处理"
                                    "最严重的致命伤。"}},
    "media": {"name": "媒体人", "en": "Media", "nickname": "记者",
              "desc": "用镜头与笔尖对抗巨型公司的调查记者。",
              "ability": {"name": "信誉", "en": "Credibility",
                          "desc": "靠名声与证据链施压，能让人不得不回答你的问题。"}},
    "cop": {"name": "警察", "en": "Cop", "nickname": "条子",
            "desc": "在腐败的体系里还想做点事的人。",
            "ability": {"name": "支援", "en": "Backup",
                        "desc": "呼叫 NCPD 支援，或动用警方的档案与人脉。"}},
    "corporate": {"name": "公司人", "en": "Corporate", "nickname": "西装",
                  "desc": "巨型公司这台绞肉机里的一名齿轮。",
                  "ability": {"name": "团队协作", "en": "Teamwork",
                              "desc": "调用公司资源、人脉与内部信息。"}},
    "fixer": {"name": "中间人", "en": "Fixer", "nickname": "掮客",
              "desc": "街头的一切都能谈，只要价钱合适。",
              "ability": {"name": "人脉", "en": "Operator",
                          "desc": "知道谁能搞到什么、要价多少；能把任何东西卖出去。"}},
    "nomad": {"name": "游牧民", "en": "Nomad", "nickname": "家族成员",
              "desc": "住在车轮上的家族成员，荒原与城市之间的运输者。",
              "ability": {"name": "家族汽车", "en": "Moto",
                          "desc": "拥有并驾驶家族的车队与载具。"}},
    "rockerboy": {"name": "摇滚客", "en": "Rockerboy", "nickname": "摇滚客",
                  "desc": "用音乐与姿态点燃人群的反叛者。",
                  "ability": {"name": "魅力效应", "en": "Charismatic Impact",
                              "desc": "你能察觉谁是你的粉丝，并对这些人做基于魅力与共情的"
                                      "技能检定 +2。也适用于对峙。"}},
}

#: 职业特殊能力等级上限
ROLE_ABILITY_MAX = 10

# =====================================================================
# 武器
# =====================================================================
#: ``dmg`` 伤害骰；``rof`` 攻击次数（LA）；``ammo`` 弹匣容量（None = 无需装填）；
#: ``kind`` 武器类别（决定远程难度值 / 近战 / 斗殴）；``skill`` 使用的技能。
WEAPONS: dict[str, dict] = {
    "brawling": {"name": "徒手", "kind": "brawling", "dmg": "1d6", "rof": 2,
                 "ammo": None, "skill": "brawling", "cost": 0,
                 "notes": "斗殴始终进行两次攻击；含擒抱、锁喉与投掷。"},
    "light_melee": {"name": "轻型近战武器", "kind": "melee", "dmg": "1d6", "rof": 2,
                    "ammo": None, "skill": "melee_weapon", "cost": 50,
                    "notes": "匕首、指虎、警棍之类。"},
    "medium_melee": {"name": "中型近战武器", "kind": "melee", "dmg": "2d6", "rof": 2,
                     "ammo": None, "skill": "melee_weapon", "cost": 50,
                     "notes": "剑、棒球棍、铁管之类。"},
    "heavy_melee": {"name": "重型近战武器", "kind": "melee", "dmg": "3d6", "rof": 2,
                    "ammo": None, "skill": "melee_weapon", "cost": 100,
                    "notes": "大型近战武器：双手剑、大锤之类。"},
    "very_heavy_melee": {"name": "超重型近战武器", "kind": "melee", "dmg": "4d6",
                         "rof": 1, "ammo": None, "skill": "melee_weapon", "cost": 1000,
                         "notes": "链锯、动力锤之类。"},
    "medium_pistol": {"name": "中型手枪", "kind": "pistol", "dmg": "2d6", "rof": 2,
                      "ammo": 12, "skill": "handgun", "cost": 50},
    "heavy_pistol": {"name": "重型手枪", "kind": "pistol", "dmg": "3d6", "rof": 2,
                     "ammo": 8, "skill": "handgun", "cost": 100},
    "very_heavy_pistol": {"name": "超重型手枪", "kind": "pistol", "dmg": "4d6",
                          "rof": 1, "ammo": 8, "skill": "handgun", "cost": 100},
    "shotgun": {"name": "霰弹枪", "kind": "shotgun", "dmg": "5d6", "rof": 1,
                "ammo": 4, "skill": "shoulder_arms", "cost": 500},
    "assault_rifle": {"name": "突击步枪", "kind": "rifle", "dmg": "5d6", "rof": 1,
                      "ammo": 25, "skill": "shoulder_arms", "cost": 500},
}

#: 近战武器（按伤害从高到低，用于「身上最好的近战武器」）
MELEE_ORDER: tuple[str, ...] = ("very_heavy_melee", "heavy_melee",
                                "medium_melee", "light_melee", "brawling")
#: 远程武器（按伤害从高到低）
RANGED_ORDER: tuple[str, ...] = ("assault_rifle", "shotgun",
                                 "very_heavy_pistol", "heavy_pistol",
                                 "medium_pistol")

# =====================================================================
# 护甲（OB / SP）
# =====================================================================
#: 护甲 key → 名称与护甲值。命中部位默认躯干；护甲每次挡下伤害后 -1（磨损）。
ARMORS: dict[str, dict] = {
    "leather": {"name": "皮衣", "sp": 4, "cost": 20,
                "notes": "最便宜的防弹皮衣，聊胜于无。"},
    "kevlar": {"name": "凯夫拉", "sp": 7, "cost": 50,
               "notes": "街头最常见的防弹衣。"},
    "light_armorjack": {"name": "轻型防弹夹克", "sp": 11, "cost": 100,
                        "notes": "覆盖躯干与四肢的防弹夹克，外观日常。"},
    "medium_armorjack": {"name": "中型防弹衣", "sp": 12, "cost": 100,
                         "notes": "更厚实的防弹衣，看得出来是护甲。"},
    "heavy_armorjack": {"name": "重型防弹衣", "sp": 13, "cost": 500,
                        "notes": "全套防弹装具，笨重但可靠。"},
    "bulletproof_shield": {"name": "防弹盾", "sp": 10, "cost": 100,
                           "notes": "手持防弹盾；举盾时可作为掩体。"},
}

#: 未穿护甲时的护甲值
ARMOR_NONE = 0
#: 护甲修复一件的价格
ARMOR_REPAIR_COST = 10

# =====================================================================
# 装备
# =====================================================================
GEAR: dict[str, dict] = {
    "ammo": {"name": "弹药", "cost": 10, "unit": "发",
             "notes": "按武器口径购买。"},
    "first_aid_kit": {"name": "急救包", "cost": 20,
                      "notes": "稳定伤者所需的消耗品。"},
    "medkit": {"name": "医疗包", "cost": 50,
               "notes": "治疗致命伤所需。"},
    "flashlight": {"name": "手电", "cost": 20, "notes": "黑暗中的光源。"},
    "rope": {"name": "绳索", "cost": 20, "unit": "50 米",
             "notes": "攀爬与捆人两相宜。"},
    "radio": {"name": "对讲机", "cost": 50,
              "notes": "可与同频道的人通讯。"},
    "agent": {"name": "智能体", "cost": 100,
              "notes": "掌上个人电子设备：通讯、检索、记录。"},
    "video_camera": {"name": "摄像机", "cost": 100,
                     "notes": "可连续录制 12 小时。"},
    "glow_paint": {"name": "荧光喷漆", "cost": 20,
                   "notes": "暗处发光。"},
    "instrument": {"name": "乐器", "cost": 100,
                   "notes": "乐器演奏技能用。"},
    "amp": {"name": "便携音箱", "cost": 50, "notes": "放大乐器声音。"},
    "handcuffs": {"name": "手铐", "cost": 50, "notes": "塑料扎带或金属手铐。"},
    "fake_id": {"name": "伪造证件", "cost": 100,
                "notes": "需要伪造技能配合。"},
    "binoculars": {"name": "望远镜", "cost": 50, "notes": "远距离观察。"},
    "burner_phone": {"name": "一次性手机", "cost": 50,
                     "notes": "预付费、易丢弃，中间人最爱用的联络方式。"},
    "duct_tape": {"name": "强力胶带", "cost": 20,
                  "notes": "适合各种临时修理，也适合捆人。"},
    "road_flare": {"name": "道路照明弹", "cost": 20,
                   "notes": "照亮半径一百米，或作为信号。"},
    "recorder": {"name": "录音机", "cost": 100,
                 "notes": "可连续录音 24 小时。"},
    "toolkit": {"name": "工具箱", "cost": 100,
                "notes": "现场修理、拆解与临时改造所需的家伙。"},
    "medkit_full": {"name": "医疗工具组", "cost": 100,
                    "notes": "外科手术与义体植入所需的完整器械。"},
}

# =====================================================================
# 赛博义体
# =====================================================================
#: ``humanity`` = 人性损失；``effects`` 为机制关键字。
CYBERWARE: dict[str, dict] = {
    "internal_agent": {"name": "内置智能体", "humanity": 7, "cost": 100,
                       "text": "你脑内装有自适配 AI 手机，完全由语音指令操控。",
                       "effects": [{"kind": "gear_agent"}]},
    "pain_editor": {"name": "赛博痛觉编辑器", "humanity": 7, "cost": 100,
                    "text": "你能切断痛觉感受器的神经冲动，从而无视重伤状态下 -2 的惩罚。",
                    "effects": [{"kind": "ignore_serious_penalty"}]},
    "cyberaudio": {"name": "赛博听觉组件", "humanity": 7, "cost": 500,
                   "text": "录音、放音与听觉强化；可过滤噪音。",
                   "effects": [{"kind": "check_bonus", "skill": "perception",
                                "value": 2, "scope": "hearing"}]},
    "cyberoptics": {"name": "赛博光学组件", "humanity": 7, "cost": 500,
                    "text": "低光/红外视觉、变焦与录像。",
                    "effects": [{"kind": "low_light"},
                                {"kind": "check_bonus", "skill": "perception",
                                 "value": 2, "scope": "sight"}]},
    "neural_link": {"name": "神经接口", "humanity": 7, "cost": 500,
                    "text": "手腕上的接口插槽，可直连机器与网络。",
                    "effects": [{"kind": "interface_plug"}]},
    "subdermal_armor": {"name": "皮下护甲", "humanity": 7, "cost": 1000,
                        "text": "皮下植入的防弹层，提供全身 SP 7（与护甲不叠加，取高者）。",
                        "effects": [{"kind": "subdermal_sp", "value": 7}]},
    "muscle_enhance": {"name": "肌肉强化", "humanity": 14, "cost": 1000,
                       "text": "体格 +2（上限 10）。",
                       "effects": [{"kind": "stat_bonus", "stat": "body",
                                    "value": 2, "cap": 10}]},
    "cyberarm": {"name": "义体手臂", "humanity": 14, "cost": 500,
                 "text": "替换的手臂，可内藏武器或工具。",
                 "effects": [{"kind": "replace_limb", "limb": "arm"}]},
    "tool_hand": {"name": "工具手", "humanity": 7, "cost": 500,
                  "text": "手指内嵌现场所需的各种工具：螺丝刀、扳手、小钻等。",
                  "effects": [{"kind": "check_bonus", "skill": "electronics_security",
                               "value": 0, "scope": "tools"}]},
}

# =====================================================================
# 敌人 / NPC 数据（对应旧引擎的怪物表）
# =====================================================================
#: 字段：``stats``（十项属性）、``hp``（省略则由体格+意志力推算）、
#: ``skills``（技能 → 等级，即角色卡上的「基础」减去属性）、``weapons``（武器 key）、
#: ``armor``（护甲值）。
#:
#: ``corrupt_cop`` 的数据直接取自《轻松模式》第 40 页的「腐败警察」数据块：
#: 属性 智力5 反应6 敏捷5 技术2 魅力4 意志力4 幸运4 移动4 体格6 共情3，
#: PW 35 / 重伤阈值 18 / 生存阈值 6，凯夫拉 SP 7，重型手枪（4d6）弹药 ×16，对讲机。
#: 技能为角色卡上的「技能基准」减去对应属性后的等级。
NPCS: dict[str, dict] = {
    "corrupt_cop": {
        "name": "腐败警察", "en": "Corrupt Cop",
        "stats": {"int": 5, "ref": 6, "dex": 5, "tech": 2, "cool": 4,
                  "will": 4, "luck": 4, "move": 4, "body": 6, "emp": 3},
        "skills": {"athletics": 4, "brawling": 6, "shoulder_arms": 6,
                   "handgun": 6, "education": 0, "concentration": 2,
                   "conversation": 2, "human_perception": 2,
                   "resist_torture_drugs": 4, "perception": 4,
                   "persuasion": 6, "first_aid": 2, "drive_land_vehicle": 4,
                   "interrogation": 2, "stealth": 2, "evasion": 2,
                   "local_expert": 0},
        "weapons": ["very_heavy_pistol"],
        "armor": 7, "armor_name": "凯夫拉",
        "cyberware": [], "gear": ["radio", "ammo"],
        "notes": "重型手枪弹药 ×16；对讲机可与其他腐败警察通讯。"
                 "PW 掉到一半以下或受重伤就想撤离，被逼到墙角则死战到底，"
                 "也会试着把重伤的同伴拖走。",
    },
    "houston": {
        "name": "Leonard Houston",
        "stats": {"int": 6, "ref": 6, "dex": 5, "tech": 2, "cool": 6,
                  "will": 4, "luck": 5, "move": 4, "body": 6, "emp": 3},
        "skills": {"athletics": 4, "brawling": 6, "shoulder_arms": 6,
                   "handgun": 8, "education": 0, "concentration": 2,
                   "conversation": 2, "human_perception": 2,
                   "resist_torture_drugs": 4, "perception": 4,
                   "persuasion": 6, "first_aid": 2, "drive_land_vehicle": 4,
                   "interrogation": 4, "stealth": 2, "evasion": 2,
                   "local_expert": 0},
        "weapons": ["very_heavy_pistol", "medium_melee"],
        "armor": 7, "armor_name": "凯夫拉",
        "cyberware": [], "gear": ["radio", "handcuffs"],
        "notes": "NCPD 警察，私下里是个十足的恶棍；巷中伏击的发起者。"
                 "用腐败警察的属性，但枪法和审讯更好，战斗开始时会优先射击"
                 "接过箱子的那个玩家角色。",
    },
    "bike_ganger": {
        "name": "摩托增援", "en": "Biker Reinforcements",
        "stats": {"int": 5, "ref": 6, "dex": 5, "tech": 2, "cool": 4,
                  "will": 4, "luck": 4, "move": 20, "body": 6, "emp": 3},
        "skills": {"athletics": 4, "brawling": 6, "shoulder_arms": 8,
                   "handgun": 6, "education": 0, "concentration": 2,
                   "conversation": 2, "human_perception": 2,
                   "resist_torture_drugs": 4, "perception": 4,
                   "persuasion": 6, "first_aid": 2, "drive_land_vehicle": 8,
                   "interrogation": 2, "stealth": 2, "evasion": 2,
                   "local_expert": 0},
        "weapons": ["assault_rifle"],
        "armor": 11, "armor_name": "轻型防弹夹克",
        "cyberware": ["neural_link"], "gear": [],
        "notes": "仓库战斗第二回合骑摩托撞窗冲进来的增援，也是这场副本的 Boss："
                 "按剧本要求用腐败警察的属性，另加突击步枪、SP 11 护甲、移动力 20"
                 "（已按 MOVE 属性体现）以及双手离把驾驶摩托的本事。",
    },
    "lazlo": {
        "name": "Lazlo",
        "stats": {"int": 6, "ref": 5, "dex": 6, "tech": 5, "cool": 7,
                  "will": 6, "luck": 7, "move": 5, "body": 5, "emp": 7},
        "skills": {"athletics": 3, "brawling": 6, "handgun": 6,
                   "bureaucracy": 6, "business": 7, "conversation": 8,
                   "human_perception": 8, "persuasion": 8, "bribery": 7,
                   "streetwise": 7, "local_expert": 7, "library_search": 5,
                   "perception": 5, "stealth": 4, "evasion": 6,
                   "accounting": 6, "interrogation": 6},
        "weapons": ["heavy_pistol"],
        "armor": 4, "armor_name": "皮衣",
        "cyberware": ["neural_link"], "gear": ["agent"],
        "notes": "小队的老中间人。被腐败警察绑架、拷打至仅剩 5 点 PW，"
                 "右手手指粉碎、左手义手被砍下。",
    },
}

# =====================================================================
# 成长：改进点（IP）
# =====================================================================
#: 技能提升到新等级所需改进点 = IP_SKILL_BASE × 新等级（×2 技能再乘 2）
IP_SKILL_BASE = 20
#: 属性提升一点所需改进点 = IP_STAT_BASE × 新值
IP_STAT_BASE = 60
#: 职业特殊能力提升一级所需改进点 = IP_ROLE_BASE × 新等级
IP_ROLE_BASE = 60
#: 技能等级上限
SKILL_MAX = 10

# =====================================================================
# 其他
# =====================================================================
#: 声望初始值（对峙用）
REPUTATION_START = 0
#: 货币名
CURRENCY = "欧元"
CURRENCY_ABBR = "eb"
#: 人性上限 = 共情 × 此系数
HUMANITY_PER_EMP = 10
#: 义体人性损失诊断
CYBERPSYCHOSIS_HUMANITY = 0
