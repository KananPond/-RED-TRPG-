"""原创模组《星坠矿脉的阴影》—— 被遗忘的大陆 · 剑湾。

结构致敬主流入门模组的经典节奏：
护送任务 → 路上伏击 → 追踪至匪巢 → 前线小镇与黑帮 → 地下城决战。
文本为原创，机制数值取自 SRD 5.1 数据表。

数据同时供两种 DM 使用：
- 剧本模式（离线）：按 scenes 线性推进，intro/outro 直接朗读。
- AI 模式：场景数据作为 DM Agent 的"模组手册"上下文，
  通过工具推进 story flag，叙事由 LLM 即兴生成。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 显示名映射（state/server 共用）
D_NAME = {"fighter": "战士", "ranger": "游侠", "cleric": "牧师",
          "wizard": "法师", "rogue": "游荡者", "barbarian": "野蛮人",
          "bard": "吟游诗人", "druid": "德鲁伊", "monk": "武僧",
          "paladin": "圣武士", "sorcerer": "术士", "warlock": "邪术师"}

COND_CN = {"poisoned": "中毒", "frightened": "恐惧", "prone": "倒地",
           "unconscious": "昏迷", "hidden": "潜行", "invisible": "隐形",
           "asleep": "沉睡", "blessed": "受祝福", "hunters_mark": "印记中",
           "shielded": "护盾术", "stable": "稳定", "dead": "死亡",
           "outlined": "妖火标记", "restrained": "束缚", "paralyzed": "麻痹",
           "stunned": "震慑", "charmed": "魅惑", "blinded": "目盲",
           "grappled": "被擒抱", "raging": "狂暴", "guided": "引导标记",
           "ac+2": "信仰护盾", "mirror_image": "镜影", "burning": "燃烧"}


def default_companions() -> list[dict]:
    """模组未定义同伴时的默认队友（星坠矿脉三人组）。"""
    return [dict(c) for c in COMPANIONS]


MODULE = {
    "title": "星坠矿脉的阴影",
    "en_title": "Shadows over the Starfall Mine",
    "setting": (
        "被遗忘的大陆（Forgotten Realms），剑湾（Sword Coast）东缘。"
        "无冬城（Neverwinter）东南的丘陵地带，商路与古代矿脉交织，"
        "哥布林、灰矮人与更阴暗的势力在废墟间游荡。"
    ),
    "synopsis": (
        "三百年前，矮人们在星坠矿脉深处建成了传说中的魔法锻造炉——星炉，"
        "随后矿脉在地动中封存。如今，矮人商会的贡林·石锤出资助人手重开矿脉，"
        "护送重建物资前往边陲小镇松溪镇。但商队在半路失了音讯，"
        "而矿脉的阴影里，某个自称'灰烬之主'的存在也在寻找星炉……"
    ),
    "party_level_start": 1,
    "expected_end_level": 3,
}

# ---------------------------------------------------------------- 同伴
COMPANIONS = [
    {
        "id": "roland",
        "name": "罗兰·北风",
        "race": "human", "klass": "fighter", "background": "soldier",
        "fighting_style": "defense",
        "scores": {"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
        "skills": ["athletics", "intimidation"],
        "persona": (
            "前无冬城卫戍兵，沉稳寡言，把保护队友看得比自己的命重。"
            "战斗风格：顶在队伍最前面，优先攻击靠近虚弱的同伴的敌人；"
            "血量过半时用回气。说话简短、直接，偶尔冷幽默。"
        ),
        "voice": "低沉，句子很短。",
    },
    {
        "id": "veilan",
        "name": "薇兰·叶语",
        "race": "high_elf", "klass": "ranger", "background": "folk_hero",
        "fighting_style": "archery",
        "scores": {"str": 12, "dex": 15, "con": 13, "int": 10, "wis": 14, "cha": 8},
        "skills": ["stealth", "perception", "survival"],
        "persona": (
            "高等精灵游侠，在杉落道林间长大，曾在雪夜独自引回迷路的商队。"
            "战斗风格：保持距离用长弓点杀，优先射击落单或最虚弱的敌人；"
            "得到猎人印记就贴上去。观察力敏锐，喜欢提醒同伴注意细节。"
        ),
        "voice": "轻快，爱用自然作比。",
    },
    {
        "id": "pip",
        "name": "皮普·铜齿轮",
        "race": "rock_gnome", "klass": "cleric", "background": "sage",
        "fighting_style": "",
        "scores": {"str": 8, "dex": 13, "con": 14, "int": 12, "wis": 15, "cha": 10},
        "skills": ["insight", "medicine"],
        "persona": (
            "岩侏儒牧师，贡林商会的随行医者兼书记，侍奉锻造之神，"
            "对古代机械与星炉传说痴迷到失态。战斗风格：优先治疗倒下或重伤的队友"
            "（治疗真言/疗伤术），需要时给近战队友上祝福术，平时用神圣火焰消耗敌人。"
            "话多，爱给一切现象起名字。"
        ),
        "voice": "兴奋，句子里全是感叹号和术语。",
    },
]

# ---------------------------------------------------------------- NPC
NPCS = {
    "gunlir": {
        "name": "贡林·石锤", "race": "盾矮人",
        "desc": "石锤商会的老主事，留络腮胡，说话像算账。雇佣队伍护送重建物资，报酬每人10金币。",
    },
    "durga": {
        "name": "杜尔加·石拳", "race": "盾矮人",
        "desc": "商队护卫队长。伏击中被俘，囚于匪巢。获救后透露：货物是修复矿脉竖井的机械，而矿脉深处封存着星炉。",
    },
    "mira": {
        "name": "米拉·褐穗", "race": "人类",
        "desc": "松溪镇镇长，疲惫而务实。委托队伍调查黑鸦帮，酬金每人25金币。",
    },
    "bomm": {
        "name": "波姆·蜜酒", "race": "轻足半身人",
        "desc": "'醉獾酒馆'老板，消息灵通，胆小但心善。知道黑鸦帮从老柳井进出。",
    },
    "vessk": {
        "name": "维斯克", "race": "人类",
        "desc": "黑鸦帮头目，红斗篷下的疤脸剑客。为'灰烬之主'卖命，负责封锁矿脉消息。（强盗头目数据）",
    },
    "sylith": {
        "name": "丝莉丝", "race": "小恶魔（伪装人形）",
        "desc": "商会派驻松溪镇的'会计'，礼貌得过分。真身是潜伏的小恶魔，灰烬之主的耳目。",
    },
    "ashen_one": {
        "name": "维克托·灰烬", "race": "人类（堕法师）",
        "desc": "自称'灰烬之主'的亡灵法师。三十年前在矿脉事故中失去一切，如今要夺星炉锻造亡者大军。决战首领。（灰烬之主数据）",
    },
}

# ---------------------------------------------------------------- 遭遇
ENCOUNTERS: dict[str, dict] = {
    "e_ambush": {
        "title": "大路伏击",
        "dark": False,
        "intro": "两匹驮马嘶鸣着倒在路中央，草丛里传出弓弦的震颤声——哥布林伏击！",
        "monsters": [("goblin", 4)],
        "loot": {"gold": 20, "items": {"healing_potion": 1}},
    },
    "e_cave_sentinel": {
        "title": "洞口哨卫",
        "dark": True,
        "intro": "洞穴入口的阴影里，两条狼藉的皮斗篷动了动——哨卫发现了你们。",
        "monsters": [("goblin", 3)],
        "loot": {"gold": 12, "items": {}},
    },
    "e_wolves": {
        "title": "狼穴",
        "dark": True,
        "intro": "腐肉与兽腥味扑面而来。两头座狼从骨堆后低吼着扑出，一个哥布林驯兽员尖叫着吹响骨哨。",
        "monsters": [("worg", 2), ("goblin", 1)],
        "loot": {"gold": 0, "items": {"healing_potion": 1}},
    },
    "e_bugbear_boss": {
        "title": "匪巢头目",
        "dark": True,
        "intro": (
            "洞穴大厅里火光摇晃。大地精格鲁什坐在矮人的盾牌堆成的'王座'上，"
            "两个哥布林守着角落里被捆住的杜尔加。'肉……来了！'"
        ),
        "monsters": [("hobgoblin", 1), ("goblin", 2)],
        "loot": {"gold": 60, "items": {"healing_potion": 2, "torch": 2}},
    },
    "e_street": {
        "title": "夜巷袭击",
        "dark": True,
        "intro": "散场后的街巷静得反常。红斗篷从三个方向围了上来。",
        "monsters": [("bandit", 3)],
        "loot": {"gold": 30, "items": {}},
    },
    "e_hideout": {
        "title": "柳井密室",
        "dark": True,
        "intro": (
            "老柳井下的密室里烛火通明。维斯克拔出弯刀：'多管闲事的，都沉到井底去。'"
        ),
        "monsters": [("bandit", 4), ("bandit_captain", 1)],
        "loot": {"gold": 120, "items": {"healing_potion": 2}},
    },
    "e_orc_post": {
        "title": "矿门灰矮人哨站",
        "dark": True,
        "intro": (
            "矿脉入口的木门被劈成了碎片。灰矮人哨兵围着火堆沉默地磨着战锤，"
            "一头裹着锁子甲碎片的大块头食人魔拎着巨棒站了起来。"
        ),
        "monsters": [("duergar", 2), ("ogre", 1)],
        "loot": {"gold": 80, "items": {"healing_potion": 1}},
    },
    "e_spiders": {
        "title": "蛛巢回廊",
        "dark": True,
        "intro": (
            "矿道两侧结满厚重的蛛网，几具缠成茧的尸骸悬在半空。"
            "一只巨蛛从洞顶垂落，尸骸中传来骨头摩擦的声音——两具僵尸挣脱了茧。"
        ),
        "monsters": [("giant_spider", 1), ("zombie", 2)],
        "loot": {"gold": 40, "items": {"healing_potion": 1}},
    },
    "e_forge_boss": {
        "title": "星炉大厅决战",
        "dark": True,
        "intro": (
            "巨大的球形锻造厅中央，星炉如一颗沉睡的蓝色心脏静默着。"
            "灰袍的维克托·灰烬张开双臂：'来得正好。星炉认我，而你们——将成为灰烬。'\n"
            "他身侧的'丝莉丝'笑得愈发标准——人皮之下，小恶魔的螫刺在蓝光中闪亮。"
            "一把食尸鬼的利爪从阴影里探了出来。"
        ),
        "monsters": [("ash_mage", 1), ("imp", 1), ("ghoul", 1)],
        "loot": {"gold": 200, "items": {"healing_potion": 2}},
    },
}

# ---------------------------------------------------------------- 场景
# 每个场景：intro 进场叙述；explore 引导词；skills 可选检定；
# encounter 触发战斗（可选）；next 下一场景；milestone 幕完成奖励。
SCENES: list[dict] = [
    {
        "id": "road_intro", "act": 1, "location": "杉落道官道 · 无冬城以东",
        "intro": (
            "清晨的雾气还没散尽。你随商队离开无冬城东门已有两天，"
            "矮人主事贡林·石锤坐在头车上一遍遍清点货物清单。\n"
            "'星坠矿脉的竖井机械——一样都不能少。'他敲了敲车厢，"
            "'松溪镇就在前面。老规矩，每人十枚金币，货到了再请你们喝一顿。'\n"
            "护卫队长杜尔加·石拳策马靠近：'这段路不太平。都把眼睛放亮点。'"
        ),
        "explore": "你可以和商队成员攀谈，检查装备，或者观察四周的丘陵。",
        "skills": [
            {"skill": "perception", "dc": 12,
             "success": "你注意到远处的乌鸦盘旋不落——有东西惊扰了它们。",
             "fail": "官道两侧只有风声。"},
        ],
        "encounter": None, "next": "road_ambush", "milestone": 0,
    },
    {
        "id": "road_ambush", "act": 1, "location": "杉落道官道 · 林弯",
        "intro": (
            "转过一个林弯，前方路面横着两匹驮马的尸体——正是商队先行的小车！"
            "箭雨从左侧的杉树林中泼下——哥布林伏击！"
        ),
        "explore": "战斗结束后，可以搜索树林和尸体。",
        "skills": [
            {"skill": "survival", "dc": 12,
             "success": "你从折断的枝叶和拖痕中读出了去向：哥布林拖着物资进了北面的山坳。",
             "fail": "拖痕在乱石间消失了。不过杜尔加的斥候本能最终还是找到了方向——北面山坳。"},
        ],
        "encounter": "e_ambush", "next": "cave_entrance", "milestone": 0,
    },
    {
        "id": "cave_entrance", "act": 1, "location": "山坳匪巢 · 洞口",
        "intro": (
            "山坳深处，一道裂隙在岩壁上张开，洞口的荆棘被人刻意编成了掩护。"
            "里面隐隐传来粗粝的说话声。杜尔加不在商队里——很可能被拖了进去。"
        ),
        "explore": "可以尝试潜行接近、光明正大地突入，或投掷石块引诱哨兵。",
        "skills": [
            {"skill": "stealth", "dc": 13,
             "success": "你们像影子一样贴近洞口，哨兵毫无察觉——战斗开始时你们具有优势！",
             "fail": "碎石从脚边滚落，洞里的说话声戛然而止。"},
        ],
        "encounter": "e_cave_sentinel", "next": "wolf_den", "milestone": 0,
    },
    {
        "id": "wolf_den", "act": 1, "location": "山坳匪巢 · 狼穴隧道",
        "intro": (
            "隧道向下延伸，火把的光晕尽头是一片扩建的兽栏，"
            "空气中弥漫着腐肉味。骨头堆后面传来低低的咆哮。"
        ),
        "explore": "战斗结束后，兽栏角落里有一只被哥布林抢走的行李箱。",
        "skills": [
            {"skill": "investigation", "dc": 12,
             "success": "你在兽栏草料下找到商队丢失的银器箱（+15金币）。",
             "fail": "草料下只有老鼠。"},
        ],
        "encounter": "e_wolves", "next": "boss_den", "milestone": 0, "level_to": 2,
    },
    {
        "id": "boss_den", "act": 1, "location": "山坳匪巢 · 大厅",
        "intro": (
            "洞穴豁然开朗，火光把石壁照得忽明忽暗。"
            "大地精格鲁什的'王座'竟是用矮人的盾牌垒成的。"
            "角落里，一个满身血污的矮人被捆在柱子上——杜尔加还活着！"
        ),
        "explore": "击败格鲁什后，记得给杜尔加松绑。他的装备堆在墙角。",
        "skills": [],
        "encounter": "e_bugbear_boss", "next": "town_arrive", "milestone": 300,
        "outro": (
            "杜尔加揉着手腕站起来：'多谢……咳，欠你们一条命。'\n"
            "他压低声音：'听着，货物不只是机械。贡林真正要找的是矿脉深处的星炉——"
            "矮人古国的魔法锻造炉。三天前，矿脉的竖井被人从里面锁死了。'\n"
            "'先去松溪镇。贡林的主事点在镇上，把消息带给他。'"
        ),
    },
    {
        "id": "town_arrive", "act": 2, "location": "松溪镇 · 镇口",
        "intro": (
            "松溪镇比想象中萧条：半数店铺上了门板，广场的告示牌钉满悬赏令。"
            "一个裹着红斗篷的家伙正从杂货铺里'收取保护费'，掌柜敢怒不敢言。\n"
            "镇长米拉·褐穗闻讯赶来：'矿脉的援兵？感谢诸神！但先帮我个忙——"
            "黑鸦帮把整座镇子攥在手里，我的巡卫不敢碰他们。"
            "每人25金币，替松溪镇拔掉这颗钉子。'"
        ),
        "explore": "可以在镇上打探消息：醉獾酒馆、铁匠铺、被查封的商行。",
        "skills": [
            {"skill": "insight", "dc": 12,
             "success": "你注意到商会驻点的'会计'丝莉丝笑得太标准了——她的眼神根本没有落在人身上。",
             "fail": "镇民们大多讳莫如深。"},
        ],
        "encounter": None, "next": "inn_rumors", "milestone": 0,
    },
    {
        "id": "inn_rumors", "act": 2, "location": "松溪镇 · 醉獾酒馆",
        "intro": (
            "醉獾酒馆的壁炉噼啪作响，但酒客们压低的交谈一遇生人就停。"
            "半身人老板波姆擦着杯子凑过来：'外地来的？……嘘。'\n"
            "'黑鸦帮的老巢在镇西老柳井下面。天黑后他们才走动。"
            "帮头维斯克……他脖子上挂着一枚灰烬色的徽章，不是普通的强盗。'"
        ),
        "explore": "可以向波姆进一步打听，或请镇民喝酒套话。",
        "skills": [
            {"skill": "persuasion", "dc": 13,
             "success": "波姆一咬牙画出了柳井密室的入口和守卫轮值：'这么说，我可什么都没说！'",
             "fail": "波姆缩了缩脖子：'知道的越多，死得越快……你们自己当心吧。'"},
        ],
        "encounter": "e_street", "next": "well_hideout", "milestone": 0,
        "encounter_note": "离开酒馆时，黑鸦帮的暗哨在夜巷里动手了。",
    },
    {
        "id": "well_hideout", "act": 2, "location": "松溪镇 · 老柳井密室",
        "intro": (
            "老柳井的辘轳绳尽头不是水面，而是凿在石灰岩里的密室。"
            "烛火下摊着矿脉图纸，墙上的灰烬色徽章泛着微光。\n"
            "维斯克放下酒杯，缓缓拔出弯刀：'矿脉的事，知道的人都不该活着。'"
        ),
        "explore": "战斗结束后，搜查维斯克的书桌。",
        "skills": [
            {"skill": "investigation", "dc": 13,
             "success": "你找到一叠通信：落款是一个灰烬色的印记——'矿门已开，星炉之夜将至。——灰烬之主'。还有矿脉的完整地图。",
             "fail": "书信被烧了大半，只剩矿脉的位置图。"},
        ],
        "encounter": "e_hideout", "next": "mine_entrance", "milestone": 300, "level_to": 3,
        "outro": (
            "米拉镇长亲自来井口接应：'干得漂亮！矿脉的地图……'\n"
            "她脸色发白：'他们在矿里待了一个月了。带上这些药水，星炉不能再落入那种人手里。'"
        ),
    },
    {
        "id": "mine_entrance", "act": 3, "location": "星坠矿脉 · 矿门",
        "intro": (
            "被劈碎的矿门后面，古老的竖井机械锈死在半空。"
            "矿道里火光摇曳——灰矮人占领了门厅，火堆边还拴着一头食人魔。"
        ),
        "explore": "战斗结束后，竖井绞盘还能勉强使用——这是通往深处的捷径。",
        "skills": [
            {"skill": "athletics", "dc": 12,
             "success": "你们摇动绞盘降下矿车，省去了半日的下坡路，人人都恢复了些体力。",
             "fail": "绞盘卡死了，你们只能沿旋梯下行，一路气喘吁吁。"},
        ],
        "encounter": "e_orc_post", "next": "spider_hall", "milestone": 0,
    },
    {
        "id": "spider_hall", "act": 3, "location": "星坠矿脉 · 蛛巢回廊",
        "intro": (
            "矿道渐渐向下，岩壁上开始出现人工凿出的导水槽——这里曾是矮人的水力车间。"
            "而如今，厚重的蛛网封住了通道，茧状的尸骸悬在半空。"
        ),
        "explore": "茧里有一具穿着学徒袍的骸骨，手里攥着一本烧焦一半的日记。",
        "skills": [
            {"skill": "arcana", "dc": 13,
             "success": "日记残页写着：'星炉不是炉……它是一扇门。他要把'灰烬'灌进去。'你隐约理解了星炉的真正用途。",
             "fail": "残页的字迹已无法辨认。"},
        ],
        "encounter": "e_spiders", "next": "forge_hall", "milestone": 0,
    },
    {
        "id": "forge_hall", "act": 3, "location": "星坠矿脉 · 星炉大厅",
        "intro": (
            "最后一段矿道尽头，是一座倒扣的碗状大厅。"
            "中央悬浮着一颗半透明的蓝色晶体心脏——星炉沉睡于此，光尘如雪飘落。\n"
            "灰袍身影背对你们：'三百年了，它一直在等我。'"
        ),
        "explore": "击败灰烬之主后，星炉将认可触碰它的人。",
        "skills": [],
        "encounter": "e_forge_boss", "next": None, "milestone": 300,
        "outro": None,
    },
]

EPILOGUE = (
    "星炉的蓝光漫过大厅，尘埃般的魔法光点环绕着你们上升。"
    "星炉缓缓认出了触碰它的人，锻造台上浮起一柄流转星辉的长剑。\n"
    "三天后，松溪镇为'星炉的英雄们'举行了庆典。贡林·石锤把报酬翻了一倍，"
    "米拉镇长宣布重开矿脉，杜尔加·石拳被任命为矿脉卫队长。\n"
    "而在大陆的其他角落，新的传闻正在酝酿——但那是下一段冒险的故事了。\n"
    "—— 冒险结束。感谢游玩《星坠矿脉的阴影》——"
)

ACT_TITLES = {1: "第一幕：杉落道上的伏击", 2: "第二幕：松溪镇的黑鸦帮", 3: "第三幕：星坠矿脉"}


def scene_by_id(scene_id: str) -> dict | None:
    for s in SCENES:
        if s["id"] == scene_id:
            return s
    return None


def module_handbook() -> str:
    """供 DM Agent 的浓缩手册文本。"""
    lines = [f"模组：{MODULE['title']}（{MODULE['en_title']}）", MODULE["synopsis"], ""]
    for act in (1, 2, 3):
        lines.append(f"== {ACT_TITLES[act]} ==")
        for s in SCENES:
            if s["act"] == act:
                lines.append(f"[{s['id']}] {s['location']}")
                lines.append("  " + s["intro"].replace("\n", " ")[:120])
        lines.append("")
    lines.append("== 关键NPC ==")
    for n in NPCS.values():
        lines.append(f"{n['name']}（{n['race']}）：{n['desc']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 内置模组实例
def _builtin():
    from .loader import Module
    data = {
        "id": "starfall", "title": MODULE["title"],
        "en_title": MODULE["en_title"], "setting": MODULE["setting"],
        "synopsis": MODULE["synopsis"],
        "party_level_start": MODULE["party_level_start"],
        "expected_end_level": MODULE["expected_end_level"],
        "act_titles": {str(k): v for k, v in ACT_TITLES.items()},
        "epilogue": EPILOGUE, "npcs": NPCS, "companions": COMPANIONS,
        "encounters": ENCOUNTERS, "scenes": SCENES, "custom_monsters": {},
    }
    return Module(data, source="builtin")


BUILTIN = _builtin()
