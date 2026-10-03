"""GameSession：游戏主编排器。

- AI 模式：DM Agent / PC Agent 经 harness 循环决策，引擎执行规则。
- 剧本模式（离线）：模组数据驱动 + 启发式队友 AI，无需任何 API。
两种模式共用同一套 5e 规则引擎与 UI。

战斗采用完整 5e 动作经济：每回合一个动作 + 一个附赠动作 + 一个反应；
玩家回合分为"动作阶段"与"附赠阶段"两层菜单。
"""

from __future__ import annotations

from ..campaign.loader import Module
from ..harness.events import Session
from ..harness.loop import (
    AgentResult, DefaultAgentLoop, DuplicateCallGuard, StepBudgetGuard,
    ToolTimeoutGuard,
)
from ..harness.registry import ToolRegistry
from ..rules import data as D
from ..rules.character import (
    Character, COND_BLESSED, COND_GRAPPLED, COND_INVISIBLE, COND_MARKED,
    COND_RAGING, COND_SHIELDED, COND_SLEEP, COND_PRONE, COND_STABLE,
)
from ..rules.combat import Encounter, monster_choose_action
from ..rules.dice import roll
from . import state as G
from .social import TableSocial
from .tools import GameTools

SAVE_FILE = "saves/game.json"

# 商店价目（scene.shop = true 时可购买；金币有了消费出口）
SHOP_GOODS = [
    ("healing_potion", 50),
    ("potion_greater_healing", 150),
    ("healer_kit", 100),          # 一整包 = 10 次用次
    ("torch", 5),
]

# 战斗结束后清除的临时战斗状态（中毒/恐惧等按规则延续到战斗外）；
# 倒地/被擒抱/标亮等战斗内定位状态离场即消失（换场自然起身/脱离）
_POST_COMBAT_CLEAR = {COND_SHIELDED, "burning", "mirror_image", COND_INVISIBLE,
                      COND_RAGING, "ac+2", COND_SLEEP, "hunters_mark",
                      COND_PRONE, COND_GRAPPLED, "outlined", "guided"}


class GameOver(Exception):
    pass


class GameSession:
    def __init__(self, game_state: G.GameState, ui, loop: DefaultAgentLoop | None = None,
                 dm_agent=None, pc_agents: dict | None = None, log_dir="saves/logs",
                 module: Module | None = None, meter=None):
        self.state = game_state
        self.ui = ui
        self.loop = loop                      # None → 剧本模式
        self.dm_agent = dm_agent
        self.pc_agents = pc_agents or {}
        self.meter = meter                    # TokenMeter（AI 模式记录用量）
        self.module = module or game_state.module
        self.state.module = self.module
        self.tools = GameTools(game_state, ui, session=self)
        self.registry = ToolRegistry()
        self.tools.register_dm_tools(self.registry)
        self.encounter: Encounter | None = None
        self.ai_mode = loop is not None
        self._dm_session = Session("dm", log_dir=log_dir)
        self._pc_sessions: dict[str, Session] = {}
        self._npc_sessions: dict[str, Session] = {}
        self._log_dir = log_dir
        self.table_log: list[dict] = []       # 桌面插话（DM 消化进上下文后清账）
        # 桌边社交层（抢话/反应/辩论）：专用小步数循环，工具调用线程池独立，
        # 避免与 DM 战斗工具调用争抢同一 guard 线程池
        self.social: TableSocial | None = None
        self.npc_director = None
        if self.ai_mode:
            social_loop = DefaultAgentLoop(
                registry=None, adapter=loop.adapter,
                guard_step=StepBudgetGuard(max_steps=4),
                guard_timeout=ToolTimeoutGuard(),
                guard_dup=DuplicateCallGuard(),
                compactor=loop.compactor)
            self.social = TableSocial(self, social_loop)
            # NPC 子智能体调度器（NPC = DM 控制的 SubAgent，
            # docs/companion-npc-agents.md §2）：剧本模式不构建，
            # npc_dialogue 工具返回不可用
            from .npcagent import NPCDirector
            self.npc_director: NPCDirector | None = NPCDirector(self, loop)
        # AI PC 控制器（docs/aipc-design.md §2.1）：构造后由 AIPCController.attach
        # 赋值；为 None 时全部新分支短路，行为与基线完全一致
        self.aipc = None

    def pc_session(self, name: str) -> Session:
        """同伴的持久会话（战斗与桌面社交共用一份记忆）。"""
        return self._pc_sessions.setdefault(
            name, Session(f"pc:{name}", log_dir=self._log_dir))

    def npc_session(self, npc_id: str) -> Session:
        """NPC 子智能体的持久会话：NPC 记得此前与冒险者的每次交锋。"""
        return self._npc_sessions.setdefault(
            npc_id, Session(f"npc:{npc_id}", log_dir=self._log_dir))

    def npc_dialogue(self, npc: str, instruction: str = "") -> dict:
        """把话头交给某个 NPC 的子智能体（DM 的 npc_dialogue 工具落点）。"""
        if self.npc_director is None:
            return {"ok": False, "error": "NPC 子智能体仅在 AI 模式可用"}
        return self.npc_director.dialogue(npc, instruction)

    # ================================================================ 主流程
    def run(self) -> None:
        mode = "AI 模式（DM 与队友由 Agent 扮演）" if self.ai_mode \
            else "剧本模式（离线 · 规则引擎结算）"
        self.ui.raw(f"\n《{self.module.title}》 —— {self.module.en_title}")
        self.ui.raw(f"模式：{mode}")
        self.ui.raw(self.module.setting)
        self.enter_scene(self.state.scene_id)
        # 开局结算：模组起始等级 > 1 或职业特性尚有待选时，玩家交互补选
        # （同伴在建队时已应用推荐默认，此处通常只询问玩家）
        self.resolve_pending_choices()
        try:
            while True:
                scene = self.module.scene_by_id(self.state.scene_id)
                if self.ai_mode:
                    progressed = self.ai_explore_turns(scene)
                else:
                    progressed = self.scripted_explore_turns(scene)
                if progressed == "quit":
                    break
                if progressed == "end":
                    self._epilogue()
                    break
        except GameOver:
            self.ui.raw("\n════════ 队伍全灭 ════════")
            self.ui.raw("剑湾的风雪掩埋了这段冒险……可使用存档重新来过（菜单：读取）。")

    def _epilogue(self) -> None:
        self.ui.raw("\n" + "=" * 62)
        for line in self.module.epilogue.split("\n"):
            self.ui.raw(line)
        self.ui.raw(f"\n最终队伍：\n{self.state.party_snapshot()}")
        self.ui.raw(f"金币：{self.state.player.gold}    经验："
                    + "、".join(f"{c.name}{c.xp}" for c in self.state.party()))

    # ================================================================ 场景
    def enter_scene(self, scene_id: str) -> None:
        scene = self.module.scene_by_id(scene_id)
        if scene is None:
            raise GameOver(f"场景不存在: {scene_id}")
        self.state.scene_id = scene_id
        if scene_id not in self.state.visited:
            self.state.visited.append(scene_id)
        self.ui.scene_header(scene["act"], self.module.act_titles.get(scene["act"], ""),
                             scene["location"])
        for para in scene["intro"].split("\n"):
            self.ui.raw(para)
        # 稳定/昏迷的成员在换场时经 1d4 小时苏醒（1 HP 归队）；
        # 死亡的成员按规则保持死亡——5e 中复活需要复生术等高环魔法（本内容范围为1-3级）
        for c in self.state.party():
            if "dead" in c.conditions:
                continue
            if c.hp_cur <= 0:
                c.revive(1)
                who = "你" if c is self.state.player else c.name
                self.ui.system_line(f"休整与照料后，{who} 恢复了意识（1 HP 归队）。")
        self._flavor_line(scene)
        # 里程碑升级（关键节点整队升级）+ 职业特性选择
        if scene.get("level_to"):
            for c in self.state.party():
                msg = c.level_up_to(scene["level_to"])
                if msg:
                    self.ui.system_line(msg)
                self._resolve_level_up_choices(c)
        # AI PC：场景变化 → 重写卡 scenario 并重建 agent（design §2.2）
        if self.aipc is not None:
            self.aipc.refresh()
        self.autosave()

    def resolve_pending_choices(self) -> None:
        """结算全队待选的职业特性（玩家交互、同伴默认）。

        场景 level_to 升级、开局结算与经验升级（award_xp 工具）共用。"""
        for c in self.state.party():
            self._resolve_level_up_choices(c)

    def _resolve_level_up_choices(self, c: Character) -> None:
        """升级带来的职业特性选择：玩家交互选择，同伴走推荐默认。

        AI PC 模式下玩家同样走推荐默认（无 UI，静默结算，design §2.9）——
        与同伴路径的差异仅在术士龙脉血统的 HP 补差（交互路径有、
        apply_default_choices 没有），经 _apply_origin_fixup 对齐。"""
        if not c.pending_feature_choices():
            return
        if c is not self.state.player:
            c.apply_default_choices()
            return
        if self.aipc is not None:
            needs = c.pending_feature_choices()
            c.apply_default_choices()
            # 龙脉 HP 补差仅在本轮 pending 确实含 origin 时做一次回溯补齐
            # （origin 已定时每级 +1 由 _level_up_static 的 _draconic_resilience
            # 分支结算，这里再补会逐级重复膨胀——与交互路径 need=="origin"
            # 分支的触发条件对齐）
            if "origin" in needs:
                self._apply_origin_fixup(c)
            return
        for need in c.pending_feature_choices():
            self._ask_feature_choice(c, need)

    def _apply_origin_fixup(self, c: Character) -> None:
        """术士龙脉血统的每级 +1 HP 回溯补差（仅在 origin 本轮被选中时调用）。

        补足 1..c.level 各级漏掉的 +1（_level_up_static 在 origin 未定时
        不加）；交互路径 need=="origin" 分支与 aipc 默认选择分支共用；
        同伴仍直接走 apply_default_choices、不经过本函数，行为不变。"""
        if c.feature_choices.get("origin") == "draconic":
            c.hp_max += c.level
            c.hp_cur = c.hp_max

    _STYLE_NAMES = {"archery": "箭术（远程攻击+2）", "defense": "防御（AC+1）",
                    "dueling": "对决（单手近战伤害+2）", "two_weapon": "双武器格斗（副手加属性）",
                    "great_weapon": "大武器格斗（双手武器伤害骰1/2重骰）"}

    def _ask_feature_choice(self, c: Character, need: str) -> None:
        fc = c.feature_choices
        if need == "asi":
            abls = list(D.ABILITIES)
            feat_keys = list(D.FEATS)
            opts = ["属性提升：单项 +2", "属性提升：两项各 +1"] +                 [f"专长：{D.FEATS[k]['name']}——{D.FEATS[k]['desc']}" for k in feat_keys]
            i = self.ui.choose("4 级：选择属性提升（ASI）或习得一项专长", opts)
            if i == 0:
                j = self.ui.choose("提升哪项属性？（+2，上限 20）",
                                   [D.ABILITIES[a] for a in abls])
                c.apply_asi({abls[j]: 2})
                fc["asi"] = f"{abls[j]}+2"
                self.ui.system_line(f"属性提升：{D.ABILITIES[abls[j]]} +2"
                                    f"（现为 {c.abilities[abls[j]]}）")
            elif i == 1:
                first = self.ui.choose("第一项 +1", [D.ABILITIES[a] for a in abls])
                second = self.ui.choose("第二项 +1",
                                        [D.ABILITIES[a] for a in abls if a != abls[first]])
                pairs = {abls[first]: 1, abls[second]: 1}
                c.apply_asi(pairs)
                fc["asi"] = "+".join(f"{a}+1" for a in pairs)
                self.ui.system_line("属性提升：" + "、".join(
                    f"{D.ABILITIES[a]} +1（现为 {c.abilities[a]}）" for a in pairs))
            else:
                key = feat_keys[i - 2]
                fc["feat"] = key
                fc["asi"] = f"feat:{key}"
                self.ui.system_line(f"专长习得：{D.FEATS[key]['name']}！{D.FEATS[key]['desc']}")
        elif need == "expertise":
            picks = sorted(c.skills)
            pairs = [(picks[i], picks[j]) for i in range(len(picks))
                     for j in range(i + 1, len(picks))]
            labels = [f"{D.SKILLS[a]['name']} + {D.SKILLS[b]['name']}" for a, b in pairs]
            i = self.ui.choose("选择专精（2项技能熟练翻倍）", labels)
            first, second = pairs[i]
            c.expertise |= {first, second}
            fc["expertise"] = f"{first}+{second}"
            self.ui.system_line(f"专精：{D.SKILLS[first]['name']}、{D.SKILLS[second]['name']}")
        elif need == "fighting_style":
            keys = (["archery", "defense", "dueling", "two_weapon", "great_weapon"]
                    if c.klass == "fighter" else ["archery", "defense", "dueling", "two_weapon"])
            i = self.ui.choose("选择战斗风格",
                               [self._STYLE_NAMES[k] for k in keys])
            c.fighting_style = keys[i]
            self.ui.system_line(f"战斗风格：{self._STYLE_NAMES[c.fighting_style]}")
        elif need == "path":
            i = self.ui.choose("选择本源之路（野蛮人3级）",
                               ["狂战士（狂暴期间每回合附赠动作近战，狂暴后力竭+1）",
                                "图腾战士·熊（狂暴时几乎全部伤害抗性）"])
            fc["path"] = "berserker" if i == 0 else "totem"
        elif need == "archetype" and c.klass == "fighter":
            i = self.ui.choose("选择武技流派（战士3级）",
                               ["冠军（19-20即暴击）", "战斗大师（4颗战技骰d8）"])
            fc["archetype"] = "champion" if i == 0 else "battlemaster"
        elif need == "rogue_archetype":
            i = self.ui.choose("选择盗贼流派（3级）",
                               ["盗贼（快手：附赠动作使用物品/巧手）",
                                "刺客（对未行动目标攻击优势，对被突袭目标必暴击）"])
            fc["archetype"] = "thief" if i == 0 else "assassin"
        elif need == "tradition":
            fc["tradition"] = "open_hand"
            self.ui.system_line("武僧流派：开掌道")
        elif need == "prey":
            i = self.ui.choose("选择猎人猎物（游侠3级）",
                               ["巨兽杀手（每回合对已受伤目标+1d8）",
                                "破阵者（对贴身不同目标额外攻击）"])
            fc["prey"] = "colossus" if i == 0 else "horde"
        elif need == "college":
            i = self.ui.choose("选择诗人学院（2级）",
                               ["知识学院（诛心之言：反应减敌1d6）",
                                "勇者学院（叙事呈现）"])
            fc["college"] = "lore" if i == 0 else "valor"
        elif need == "school":
            i = self.ui.choose("选择奥术传承（法师2级）",
                               ["塑能（塑法：AOE中队友自动豁免成功无伤）",
                                "防护（奥术护盾）"])
            fc["school"] = "evocation" if i == 0 else "abjuration"
        elif need == "origin":
            i = self.ui.choose("选择源法血统（术士1级）",
                               ["龙脉（无甲AC 13+敏捷，每级生命+1）",
                                "混沌原力（叙事呈现）"])
            fc["origin"] = "draconic" if i == 0 else "wild"
            if fc["origin"] == "draconic":
                self._apply_origin_fixup(c)  # 补齐每级+1（代码平移，行为不变）
        elif need == "metamagic":
            i = self.ui.choose("选择超魔×2（术士3级）",
                               ["疾咒 + 强咒（动作法术改附赠 / 目标豁免劣势）",
                                "疾咒 + 谨咒（AOE中队友自动豁免成功）"])
            fc["metamagic_quickened"] = True
            if i == 0:
                fc["metamagic_heightened"] = True
                fc["metamagic"] = "quickened+heightened"
            else:
                fc["metamagic_careful"] = True
                fc["metamagic"] = "quickened+careful"
        elif need == "invocations":
            i = self.ui.choose("选择魔契恩泽×2（邪术师2级）",
                               ["剧痛魔爆 + 魔鬼之视（魔能爆+魅力伤害 / 黑暗视觉120尺）",
                                "剧痛魔爆 + 击退魔爆（魔能爆命中推开10尺）"])
            fc["invocation_1"] = "agonizing"
            fc["invocation_2"] = "devils_sight" if i == 0 else "repelling"
        elif need == "boon":
            i = self.ui.choose("选择魔契恩赐（邪术师3级）",
                               ["魔剑（可用魅力进行武器攻击）", "魔典", "魔链"])
            fc["boon"] = "blade" if i == 0 else ("tome" if i == 1 else "chain")
        else:
            c.apply_default_choices()

    def _flavor_line(self, scene: dict) -> None:
        """剧本模式下同伴的应景发言。

        默认三人组用专属台词；其余同伴按职业取应景台词池，
        以（场景 id × 同伴名）散列选句，避免整队复读同一句。"""
        if self.ai_mode:
            return
        named = {
            "roland": "保持队形。有情况先告诉我。",
            "veilan": "这地方的气味……可不太妙。",
            "pip": "哇哦，你看这些石头！典型的矮人工艺！",
        }
        generic = {
            "barbarian": ["哼，这地方闻起来有血味。我喜欢。", "要打起来就交给我。", "别拦着我上前。"],
            "bard": ["这地方能写进歌里——大概吧。", "要我唱一段壮壮胆吗？", "注意听，风里有故事。"],
            "cleric": ["愿晨光护佑此行。", "若有伤者，交给我。", "这里……有些不安的气息。"],
            "druid": ["草木在低声说话……它们不太高兴。", " 自然在这里被扭曲了。", "轻些走，别惊扰它们。"],
            "fighter": ["保持队形，稳住。", "我在前面开路。", "注意两翼，这里适合设伏。"],
            "monk": ["心如止水，步履如风。", "我先行探路。", "这里的气……不顺。"],
            "paladin": ["以誓言之名，此处不容邪祟。", "跟紧我。", "若有人受伤，到我身后来。"],
            "ranger": ["这里的痕迹……不对劲。", "我听得到——有东西在动。", "留神脚下，有陷阱的迹象。"],
            "rogue": ["让我先看看有什么值钱的……和要命的。", "影子里有动静。", "嘘——你们听。"],
            "sorcerer": ["空气里魔力在发烫。", "直觉告诉我：小心。", "这地方……让我不舒服。"],
            "warlock": ["我的主低语着警告……", "黑暗里有眼睛在看。", "契约指引我前行。"],
            "wizard": ["典型的奥术痕迹，有意思。", "让我记录一下这里。", "理论上有危险，实践上……也有。"],
        }
        for i, c in enumerate(self.state.companions):
            if c.hp_cur <= 0:
                continue
            key = ("roland" if "罗兰" in c.name else
                   "veilan" if "薇兰" in c.name else
                   "pip" if "皮普" in c.name else None)
            if key:
                self.ui.companion_line(c.name, named[key])
                continue
            pool = generic.get(c.klass, generic["fighter"])
            h = sum((scene.get("id", "") + c.name).encode("utf-8"))
            self.ui.companion_line(c.name, pool[h % len(pool)])

    def advance_scene(self, scene_id: str) -> dict:
        cur = self.module.scene_by_id(self.state.scene_id)
        valid = {cur["next"]} if cur["next"] else set()
        if scene_id != self.state.scene_id and scene_id not in valid:
            return {"ok": False,
                    "error": f"不能直接跳到 {scene_id}。当前可推进到：{sorted(valid) or '（已是最终场景）'}"}
        if scene_id == self.state.scene_id:
            return {"ok": True, "note": "已在该场景。"}
        # 幕完成奖励
        if cur.get("milestone"):
            self.tools._tool_award_xp(cur["milestone"], "阶段性剧情完成")
        self.enter_scene(scene_id)
        if self.module.scene_by_id(scene_id)["next"] is None:
            return {"ok": True, "note": "已进入最终场景。"}
        return {"ok": True, "scene": scene_id}

    # ================================================================ 探索：剧本模式
    def scripted_explore_turns(self, scene: dict) -> str:
        encounter_done = False
        skills_done: set[int] = set()
        while True:
            options = [f"检定机会：{s['skill']}（{D.SKILLS[s['skill']]['name']}，DC{s['dc']}）"
                       for i, s in enumerate(scene["skills"]) if i not in skills_done]
            can_forward = scene["encounter"] and not encounter_done
            base = []
            if can_forward:
                base.append("前进（触发剧情）")
            elif scene["next"]:
                base.append("前往下一场景")
            base += ["队伍短休", "队伍长休", "查看状态", "查看背包", "保存游戏", "退出游戏"]
            if self.best_potion(self.state.player)                     and self.state.player.hp_cur < self.state.player.hp_max:
                base.insert(2, "喝药水（回复生命）")
            if scene.get("shop"):
                base.insert(2, "商店（购买补给）")
            opts = options + base
            idx = self.ui.choose(f"—— {scene['location']} —— 你要做什么？", opts)
            if idx < len(options):
                orig = [i for i in range(len(scene["skills"])) if i not in skills_done]
                sk_idx = orig[idx]
                sk = scene["skills"][sk_idx]
                r = self.tools._tool_skill_check("队伍", sk["skill"], sk["dc"],
                                                 context=sk["skill"])
                self.ui.raw("  " + (sk["success"] if r.get("success") else sk["fail"]))
                skills_done.add(sk_idx)
                # 潜行成功 → 下一场遭遇的敌人被突袭（5e 突袭规则）
                if sk["skill"] == "stealth" and r.get("success"):
                    self.state.flags["party_stealth"] = self.state.scene_id
                continue
            choice = opts[idx]
            if choice.startswith(("前进", "前往")):
                if can_forward:
                    encounter_done = True
                    self.launch_encounter(scene["encounter"])
                    outcome = self.run_combat()
                    if outcome == "tpk":
                        raise GameOver()
                    if scene.get("outro"):
                        self.ui.raw("")
                        for para in scene["outro"].split("\n"):
                            self.ui.raw(para)
                    if scene["next"] is None:
                        return "end"
                    self.advance_scene(scene["next"])
                    return "advanced"
                self.advance_scene(scene["next"])
                return "advanced"
            if choice == "队伍短休":
                self.party_short_rest()
            elif choice == "队伍长休":
                self.party_long_rest()
            elif choice == "喝药水（回复生命）":
                self.drink_potion_out_of_combat()
            elif choice == "商店（购买补给）":
                self.open_shop()
            elif choice == "查看状态":
                self.ui.raw(self.state.party_snapshot())
            elif choice == "查看背包":
                self.show_inventory()
            elif choice == "保存游戏":
                path = G.save_game(self.state, SAVE_FILE)
                self.ui.system_line(f"已保存到 {path}")
            else:
                return "quit"

    # ================================================================ 探索：AI 模式
    _EXPLORE_COMMANDS = ("/状态", "/背包", "/短休", "/喝药", "/购买", "/长休", "/保存")

    def ai_explore_turns(self, scene: dict) -> str:
        self.ui.system_line("输入行动或对白；/状态 /背包 /短休 /长休 /喝药 /购买 /保存 /退出 为指令。")
        while True:
            if self.aipc is not None:
                # 1) 人类旁听指令（非阻塞，design §2.4/§2.8）
                cmd = self.aipc.poll_command(0.4)
                if cmd is not None:
                    cmd = cmd.strip()
                    if cmd == "/退出":
                        return "quit"
                    self._handle_explore_command(cmd)
                    continue
                # 2) AI 代打本节拍；False = LLM 故障 → 本轮交还人类（优雅降级）
                if self.aipc.explore_beat():
                    if self._post_beat():
                        return "end"
                    continue
                self.ui.system_line("（AI 代打暂不可用，本轮请亲自输入行动。）")
            # 3) 人类输入（AI PC 关闭/降级时才到达；原路径逐字保留）
            text = self.ui.free_input("\n你> ")
            if not text:
                continue
            if text == "/退出":
                return "quit"
            if self._handle_explore_command(text):
                continue
            if self.social is not None:
                self.social.beat(text)    # 抢话/反应/辩论 + DM 回合
            else:
                self.dm_turn(text)
            if self._post_beat():
                return "end"

    def _handle_explore_command(self, text: str) -> bool:
        """探索期斜杠指令分发（从 ai_explore_turns 原有 if 链抽出）。

        返回 True = 已消费（调用方 continue）；False = 非指令，交回正常流程。
        AI PC 模式下亦作为旁听指令的处理器（未识别文本给一行提示）。"""
        text = str(text or "").strip()
        if not text.startswith("/") or text == "/退出":
            return False
        if text == "/状态":
            self.ui.raw(self.state.party_snapshot())
            return True
        if text == "/背包":
            self.show_inventory()
            return True
        if text == "/短休":
            self.party_short_rest()
            return True
        if text == "/喝药":
            self.drink_potion_out_of_combat()
            return True
        if text == "/购买":
            scene = self.module.scene_by_id(self.state.scene_id) or {}
            if scene.get("shop"):
                self.open_shop()
            else:
                self.ui.system_line("这里没有商队。")
            return True
        if text == "/长休":
            self.party_long_rest()
            return True
        if text == "/保存":
            path = G.save_game(self.state, SAVE_FILE)
            self.ui.system_line(f"已保存到 {path}")
            return True
        if self.aipc is not None:
            self.ui.system_line("（旁听指令仅支持：/状态 /背包 /短休 /长休 /喝药 /购买 /保存 /退出）")
            return True
        return False

    def _post_beat(self) -> bool:
        """节拍/战斗收尾（原 ai_explore_turns 尾部逻辑抽出，design §2.4）。

        内部按 self.state.scene_id 重取 scene（战斗可能已推进场景，不得使用
        调用方传入的旧 scene 变量）。返回 True = 最终场景战斗完结，
        调用方应向上传播 "end"（epilogue 才能触发）。"""
        if self.encounter is None:
            return False
        outcome = self.run_combat()
        if outcome == "tpk":
            raise GameOver()
        scene = self.module.scene_by_id(self.state.scene_id)
        if scene.get("outro"):
            self.ui.raw("")
            for para in scene["outro"].split("\n"):
                self.ui.raw(para)
        return scene["next"] is None

    def dm_turn(self, player_text: str, lead: str = "玩家回合，请推进剧情",
                input_label: str = "玩家输入") -> str | None:
        """跑一次 DM Agent。桌面上同伴的插话（table_log）先消化进其上下文。

        lead：任务前缀（桌面回应轮用别的措辞）；返回最终叙述（无则 None）。"""
        scene = self.module.scene_by_id(self.state.scene_id)
        table = self._digest_table()
        context = [{"role": "user", "content": (
            f"【场景】{scene['id']}：{scene['location']}\n"
            f"【队伍】{self.state.party_snapshot()}\n"
            f"【旗标】{self.state.flags or '无'}\n"
            + (f"【桌上插话】\n{table}\n" if table else "")
            + f"【{input_label}】{player_text}")}]
        result = self.loop.run(self.dm_agent, self._dm_session,
                               f"{lead}：{player_text}",
                               context_messages=context, registry=self.registry)
        self._record_usage(self.dm_agent, result)
        if result.content and result.content.strip():
            self.ui.dm_narration(result.content.strip())
            return result.content.strip()
        return None

    def _digest_table(self) -> str:
        """把桌上插话折成 DM 可读的摘要并清账（模型可见 ⟺ 已记录）。"""
        if not self.table_log:
            return ""
        lines = []
        for l in self.table_log:
            mark = "（抢话）" if l.get("interrupt") else ""
            to = f"（对{l['to']}）" if l.get("to") else ""
            lines.append(f"{l['who']}{mark}{to}：「{l['text']}」")
        self.table_log.clear()
        return "\n".join(lines)

    def _record_usage(self, agent, result: AgentResult) -> None:
        if self.meter is not None and result.usage:
            try:
                self.meter.record(agent.agent_id, agent.model, result.usage,
                                  result.steps)
            except Exception:  # noqa: BLE001 —— 用量记录不阻塞对局
                pass

    # ================================================================ 战斗
    def launch_encounter(self, encounter_id: str) -> dict:
        spec = self.module.encounter(encounter_id)
        if spec is None:
            return {"ok": False, "error": "未知遭遇 " + encounter_id}
        enc = G.build_encounter(encounter_id, self.state)
        # 黑暗环境中先点亮光源（火把 > 光亮术），否则承受黑暗劣势
        if enc.dark:
            pc = self.state.player
            holder = next((c for c in self.state.alive_party()
                           if c.inventory.get("torch", 0) > 0), None)
            if holder is not None:
                holder.inventory["torch"] -= 1
                enc.dark = False
                self.ui.system_line(
                    f"{holder.name if holder is not pc else '你'}点亮一支火把，"
                    "光晕驱散了黑暗（消耗火把×1）。")
            else:
                caster = next((c for c in self.state.alive_party()
                               if "light" in c.known_spells()), None)
                if caster is not None:
                    enc.dark = False
                    self.ui.system_line(f"{caster.name} 施展光亮术，光亮驱散了黑暗。")
                else:
                    self.ui.system_line("队伍没有光源！无黑暗视觉的成员将在黑暗中艰难作战。")
        # 场景潜行成功 → 敌人被突袭（5e Surprise）
        if self.state.flags.get("party_stealth") == self.state.scene_id:
            enc.surprised = {m.name for m in enc.enemies()}
            self.state.flags.pop("party_stealth", None)
            self.ui.system_line("你们悄然就位——敌人措手不及，第一轮无法行动！")
        if hasattr(self.ui, "sw_used"):
            self.ui.sw_used = False
        # 反应询问（护盾术/灵活闪避/狱火斥喝/反制法术）
        enc.reaction_prompt = self._reaction_prompt
        self.encounter = enc
        self.tools.encounter = enc
        self.ui.raw("")
        for para in spec["intro"].split("\n"):
            self.ui.raw("  " + para)
        return {"ok": True, "encounter": encounter_id,
                "note": "战斗即将开始，编排器接管回合流程。"}

    def _reaction_prompt(self, target, ctx: dict) -> bool:
        """玩家反应询问；同伴自动决策。

        AI PC 模式（docs/aipc-design.md §2.6）：返回 None —— 恒不弹人类菜单。
        语义如实声明：combat.Encounter._ask_reaction 对可调用 prompt 直接
        bool(prompt(...))，bool(None)=False，其后的引擎自动规则分支不可达——
        即 AI 模式里同伴本就从不在战斗中自动触发反应，AI PC 玩家同样恒不触发
        （与同伴现状一致），并非"引擎自动规则接管"。"""
        if target is self.state.player:
            if self.aipc is not None:
                return None
            label = {"shield": "以反应施放护盾术（1环，AC+5）",
                     "uncanny_dodge": "灵活闪避（反应：伤害减半）",
                     "hellish_rebuke": "狱火斥喝（反应：2d10 火焰）",
                     "counterspell": "反制法术（反应）"}.get(ctx.get("reaction"),
                                                           ctx.get("reaction", "反应"))
            i = self.ui.choose(f"⚠ {target.name}：{label}？",
                               ["不使用", "使用"])
            return i == 1
        return None  # 同伴：交给引擎自动规则

    def run_combat(self) -> str:
        enc = self.encounter
        lines = enc.begin(self.state.rng)
        if hasattr(self.ui, "mode"):
            self.ui.mode = "combat"
        self.ui.raw("\n—— 战斗开始 ——")
        self.ui.status_board(lines)
        outcome = None
        while outcome is None:
            over = enc.is_over()
            if over:
                outcome = over
                break
            if enc.round > 50:
                self.ui.system_line("战斗陷入漫长僵持，队伍且战且退……")
                outcome = "retreat"
                break
            c, events = enc.next_turn()
            for e in events:
                self.ui.system_line(e)
            if not enc.alive(c):
                continue
            if isinstance(c, Character):
                if c is self.state.player:
                    self.player_combat_turn(enc)
                else:
                    self.companion_turn(enc, c)
            else:
                self.monster_turn(enc, c)
            outcome = enc.is_over()
        self.ui.raw("—— 战斗结束 ——")
        if hasattr(self.ui, "mode"):
            self.ui.mode = "explore"
        # 濒死结算（PHB 战斗外规则）：盟友可用动作做医药 DC10 稳定检定，
        # 战斗外时间充裕可反复尝试；若无人施救，濒死者继续掷死亡豁免
        # （RAW 每 6 秒一次）直至稳定或死亡——只有真正死亡才终结冒险。
        for d in [c for c in self.state.party()
                  if c.hp_cur <= 0 and "dead" not in c.conditions
                  and COND_STABLE not in c.conditions]:
            helpers = [h for h in self.state.party()
                       if h is not d and h.hp_cur > 0 and "dead" not in h.conditions]
            if helpers:
                helper = max(helpers, key=lambda h: h.skill_mod("medicine"))
                for _ in range(10):
                    r = helper.check(self.state.rng, "medicine", 10)
                    if r["success"]:
                        d.conditions.add(COND_STABLE)
                        self.ui.system_line(
                            f"{helper.name} 为 {d.name} 稳定了伤势（{r['detail']}）。")
                        break
                else:
                    d.conditions.add(COND_STABLE)
                    self.ui.system_line(
                        f"{helper.name} 反复按压止血，终于稳住了 {d.name} 的伤势。")
            else:
                # RAW：自然 20 复活（1 HP、恢复意识）后不再掷死亡豁免——
                # 循环条件须包含 hp_cur <= 0，否则复活者会永远空转
                while "dead" not in d.conditions and COND_STABLE not in d.conditions \
                        and d.hp_cur <= 0:
                    ds = d.death_save_turn(self.state.rng)
                    if ds:
                        self.ui.roll_event({
                            "who": ds["who"], "kind": "death", "label": "死亡豁免",
                            "d20": ds.get("d20"), "rolls": list(ds.get("rolls") or []),
                            "adv": 0, "dc": 10, "total": ds.get("d20"),
                            "success": ds["result"] in ("revived", "stable")
                                       or (ds.get("d20") or 0) >= 10,
                            "note": {"revived": "奇迹生还！", "stable": "伤势稳定",
                                     "died": "回天乏术……", "pending": ""}[ds["result"]],
                        })
                        self.ui.system_line(
                            f"☠ {ds['who']} 死亡豁免：{ds['detail']} → {ds['result']}")
        # 战斗结束清理：临时战斗状态消失
        for c in self.state.party():
            c.conditions -= _POST_COMBAT_CLEAR
            c.concentration = None
            c.conditions.discard(COND_MARKED)
            c.weapon_buff = None
            c.mirror_images = 0
            if "dead" in c.conditions:
                c.conditions = {"dead"}          # 规范死亡状态显示
            elif c.hp_cur == 0 and COND_STABLE in c.conditions:
                c.heal(1)                        # 稳定者经短暂喘息恢复 1 HP
        # 玩家死亡 → 本局结束（RAW：无复生魔法可依）
        if "dead" in self.state.player.conditions:
            self.encounter = None
            self.tools.encounter = None
            return "tpk"
        self.encounter = None
        self.tools.encounter = None
        if outcome == "enemies":
            return "tpk"
        if outcome == "party":
            self._aftermath(enc)
        return outcome if outcome == "retreat" else "win"

    def monster_turn(self, enc: Encounter, mon) -> None:
        # 怪物附赠动作（借机潜袭等）
        act = monster_choose_action(enc, mon)
        if act.get("action") == "bonus_disengage":
            enc.do_disengage_quiet(mon)
            self.ui.system_line(f"  {mon.name} 以附赠动作灵巧撤离。")
            act = monster_choose_action(enc, mon)
        if act.get("action") != "attack":
            return
        target = enc.find(act["target"])
        if target is None:
            return
        mark = (len(enc.log), len(enc.rolls))
        enc.attack_action(mon, target, act.get("weapon"), self.state.rng)
        self._flush_enc(enc, mark)

    # ---------------- 玩家回合：动作阶段 + 附赠阶段
    def _flush_enc(self, enc: Encounter, mark: tuple[int, int]) -> None:
        """把自 mark 以来累积的骰子事件与战斗日志按序推给 UI。"""
        b_log, b_rolls = mark
        for ev in enc.rolls[b_rolls:]:
            self.ui.roll_event(ev)
        for line in enc.log[b_log:]:
            self.ui.raw("  " + line)

    def player_combat_turn(self, enc: Encounter) -> None:
        # AI PC（docs/aipc-design.md §2.5）：走同伴回合链（pc_agents 命中 →
        # _pc_agent_turn → combat_action 工具；LLM 故障落 _scripted_pc_turn 启发式）
        if self.aipc is not None:
            self.aipc.combat_turn(enc)
            return
        pc = self.state.player
        # 动作阶段（begin_turn 已由 next_turn 处理）
        while enc.has_action(pc) and enc.is_over() is None:
            foes = enc.active_enemies()
            if not foes:
                return
            self.ui.status_board(enc.status_lines())
            opts = self._action_options(enc, pc)
            idx = self.ui.choose(f"—— {pc.name} 的回合 · 动作阶段 ——", opts)
            if not self._execute_action(enc, pc, idx, opts, foes):
                continue
        # 附赠动作阶段
        while enc.has_bonus(pc) and enc.is_over() is None:
            opts = self._bonus_options(pc)
            if not opts:
                break
            self.ui.status_board(enc.status_lines())
            opts = opts + ["（结束回合）"]
            idx = self.ui.choose(f"—— {pc.name} · 附赠动作阶段 ——", opts)
            if idx >= len(opts) - 1:
                break
            if not self._execute_bonus(enc, pc, idx, opts):
                continue

    def _action_options(self, enc: Encounter, pc: Character) -> list[str]:
        opts = ["攻击", "施法"]
        opts += ["推撞（运动对抗：击倒或推开）", "擒抱（运动对抗，目标无法移动）",
                 "助攻（帮助队友获得优势）", "防御", "躲藏", "撤离",
                 "移动远离（可能引发借机攻击）",
                 "说话", "查看状态", "（结束动作阶段）"]
        if COND_GRAPPLED in pc.conditions:
            opts.insert(0, "挣脱擒抱（动作：运动对抗）")
        downed = [a for a in enc.party()
                  if isinstance(a, Character) and a.hp_cur <= 0
                  and "dead" not in a.conditions]
        if downed and self.best_potion(pc):
            opts.insert(opts.index("（结束动作阶段）"), "喂药（救起倒地队友）")
        if downed and pc.inventory.get("healer_kit", 0) > 0:
            opts.insert(opts.index("（结束动作阶段）"), "医疗包（稳定倒地队友）")
        if any(isinstance(a, Character) and a.hp_cur <= 0 and "dead" not in a.conditions
               and "stable" not in a.conditions for a in downed):
            opts.insert(opts.index("（结束动作阶段）"), "稳定伤势（医药 DC10）")
        if self.best_potion(pc):
            opts.insert(opts.index("（结束动作阶段）"), "喝药水")
        if pc.has_feature("action_surge") and not pc.action_surge_used:
            opts.insert(0, "动作如潮（额外动作）")
        if pc.mech().get("breath_weapon"):
            opts.insert(0, "龙息")
        if pc.klass == "druid" and pc.level >= 2 and pc.wild_shape_used < 2 \
                and not pc.in_wild_shape():
            opts.insert(0, "荒野变形")
        if pc.klass == "paladin" and pc.lay_on_hands_left() > 0:
            opts.insert(0, "圣疗（治疗池）")
        return opts

    def _execute_action(self, enc: Encounter, pc: Character, idx: int,
                        opts: list[str], foes: list) -> bool:
        choice = opts[idx]
        mark = (len(enc.log), len(enc.rolls))
        def flush():
            self._flush_enc(enc, mark)
        if choice.startswith("动作如潮"):
            r = enc.action_surge(pc)
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice.startswith("龙息"):
            ti = self.ui.choose("龙息目标", [f.name for f in foes])
            r = enc.breath_weapon(pc, [foes[ti]], self.state.rng)
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice.startswith("荒野变形"):
            beasts = [k for k, v in D.MONSTERS.items()
                      if v.get("beast") and v.get("cr", 9) <= (0.25 if pc.level < 4 else 0.5)]
            fi = self.ui.choose("选择野兽形态",
                                [f"{D.MONSTERS[b]['name']}（CR{D.MONSTERS[b]['cr']}，"
                                 f"{D.MONSTERS[b]['hp']}，AC{D.MONSTERS[b]['ac']}）"
                                 for b in beasts])
            r = enc.wild_shape(pc, beasts[fi])
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice.startswith("圣疗"):
            allies = enc.active_party()
            ti = self.ui.choose("圣疗目标", [f"{a.name}（HP {a.hp_cur}/{a.hp_max}）"
                                             for a in allies])
            amt = self.ui.choose("消耗治疗池", ["5 点", "10 点", "全部（可尽情回复）"])
            amounts = [5, 10, pc.lay_on_hands_left()]
            r = enc.lay_on_hands(pc, allies[ti], amounts[amt])
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice == "攻击":
            return self._player_attack(enc, pc, foes)
        if choice == "施法":
            if self._cast_menu(enc, pc):
                flush()
                return True
            return False
        if choice.startswith("推撞"):
            ti = self.ui.choose("推撞目标", [f.name for f in foes])
            style = self.ui.choose("推撞方式", ["击倒（目标倒地）", "推开（脱离5尺纠缠）"])
            r = enc.do_shove(pc, foes[ti], push=(style == 1))
            flush()
            return True
        if choice.startswith("擒抱"):
            ti = self.ui.choose("擒抱目标", [f.name for f in foes])
            r = enc.do_grapple(pc, foes[ti])
            flush()
            return True
        if choice.startswith("挣脱擒抱"):
            r = enc.do_escape_grapple(pc)
            if not r.get("ok") and "error" in r:
                self.ui.system_line(r["error"])
                return False
            flush()
            return True
        if choice.startswith("助攻"):
            ti = self.ui.choose("为对哪名敌人的下一次攻击创造优势？", [f.name for f in foes])
            r = enc.do_help(pc, foes[ti])
            flush()
            return True
        if choice == "防御":
            r = enc.do_dodge(pc)
            flush()
            return True
        if choice == "躲藏":
            r = enc.do_hide(pc, self.state.rng)
            flush()
            return True
        if choice == "撤离":
            r = enc.do_disengage(pc)
            flush()
            return True
        if choice.startswith("移动远离"):
            r = enc.move_away(pc)
            flush()
            return True
        if choice.startswith("喝"):
            r = enc.use_item(pc, self.best_potion(pc))
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice.startswith("喂药"):
            downed = [a for a in enc.party()
                      if isinstance(a, Character) and a.hp_cur <= 0
                      and "dead" not in a.conditions]
            ti = self.ui.choose("喂药给谁？", [a.name for a in downed])
            r = enc.use_item(pc, self.best_potion(pc), downed[ti])
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice.startswith("医疗包"):
            downed = [a for a in enc.party()
                      if isinstance(a, Character) and a.hp_cur <= 0
                      and "dead" not in a.conditions and "stable" not in a.conditions]
            ti = self.ui.choose("用医疗包稳定谁？", [a.name for a in downed])
            r = enc.use_item(pc, "healer_kit", downed[ti])
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice.startswith("稳定伤势"):
            downed = [a for a in enc.party()
                      if isinstance(a, Character) and a.hp_cur <= 0
                      and "dead" not in a.conditions and "stable" not in a.conditions]
            ti = self.ui.choose("稳定哪位队友？", [a.name for a in downed])
            r = enc.stabilize(pc, downed[ti])
            if not r.get("ok"):
                self.ui.system_line(r.get("error", "失败"))
                return False
            flush()
            return True
        if choice == "说话":
            text = self.ui.free_input("（说什么？）")
            if text:
                self.ui.companion_line(pc.name, text)
            return False   # 免费动作，不消耗回合
        if choice.startswith("查看状态"):
            self.ui.raw(self.state.party_snapshot())
            return False
        return True   # 结束动作阶段

    def _player_attack(self, enc: Encounter, pc: Character, foes: list) -> bool:
        wi = self.ui.choose("选择武器", [self._weapon_label(pc, w) for w in pc.weapons])
        weapon = pc.weapons[wi]
        reckless = False
        if pc.has_feature("reckless") and not D.WEAPONS[weapon].get("ranged"):
            reckless = self.ui.choose("鲁莽攻击？（你本回合攻击有优势，敌人对你也有优势）",
                                      ["不鲁莽", "鲁莽攻击"]) == 1
        foes_sorted = sorted(foes, key=lambda f: f.hp_cur)
        ti = self.ui.choose("选择目标", [f"{f.name}（HP {f.hp_cur}/{f.hp_max} AC {f.ac()}）"
                                         for f in foes_sorted])
        target = foes_sorted[ti]
        # 战斗大师：选择战技（精确攻击 / 推撞攻击 / 恐吓攻击，消耗战技骰）
        maneuver = None
        if pc.has_feature("battlemaster") and pc.superiority_dice_left() > 0:
            mi = self.ui.choose(f"战斗大师战技（战技骰 {pc.superiority_dice_left()}/4）",
                                ["不用战技", "精确攻击（攻击骰+1d8）",
                                 "推撞攻击（+1d8 伤害并推开5尺）",
                                 "恐吓攻击（+1d8 伤害，感知豁免否则恐惧）"])
            maneuver = (None, "precision", "pushing", "menacing")[mi]
        power = False
        winfo = D.WEAPONS.get(weapon, {})
        if ((not winfo.get("ranged") and "heavy" in winfo.get("props", [])
                and pc.has_feature("great_weapon_master"))
                or (winfo.get("ranged") and pc.has_feature("sharpshooter"))):
            power = self.ui.choose("大力攻击？（−5 命中 / +10 伤害）",
                                   ["不使用", "使用"]) == 1
        smite_slot = None
        if pc.has_feature("divine_smite") and any(v > 0 for v in pc.spell_slots().values()):
            avail = [lv for lv in (3, 2, 1) if pc.spell_slots().get(lv, 0) > 0]
            if avail:
                opts = ["不施展"] + [f"{lv}环（{min(2 + lv - 1, 5)}d8）" for lv in avail]
                si = self.ui.choose("命中后消耗法术位施展神佑斩？", opts)
                if si > 0:
                    smite_slot = avail[si - 1]
        mark = (len(enc.log), len(enc.rolls))
        enc.attack_action(pc, target, weapon, self.state.rng,
                          reckless=reckless, smite_slot=smite_slot, maneuver=maneuver,
                          power_attack=power)
        self._flush_enc(enc, mark)
        return True

    def _bonus_options(self, pc: Character) -> list[str]:
        enc = self.encounter
        opts = []
        if pc.klass == "fighter" and not pc.second_wind_used:
            opts.append("回气（1d10+等级 治疗）")
        spells = {s: D.SPELLS[s] for s in pc.known_spells()}
        bonus_spells = [k for k, sp in spells.items()
                        if sp.get("bonus_action") and (sp["level"] == 0 or
                                                       pc.spell_slots().get(sp["level"], 0) > 0)]
        for k in bonus_spells:
            opts.append(f"施法：{D.SPELLS[k]['name']}（附赠动作）")
        if pc.klass == "rogue" and pc.level >= 2:
            opts += ["狡诈动作：撤离", "狡诈动作：躲藏"]
        if pc.klass == "monk" and pc.ki_left() > 0:
            opts.append("疾风连击（1气：附赠动作2次徒手）")
            hit_target = enc.find(enc.last_melee_hit.get(pc.name, "")) if enc else None
            if pc.has_feature("stunning_strike") and hit_target is not None \
                    and enc.last_melee_hit.get(pc.name):
                opts.append(f"震慑拳（1气：对 {hit_target.name}）")
        if pc.klass == "monk" and enc is not None \
                and enc.last_attack_round.get(pc.name) == enc.round:
            opts.append("武术：附赠徒手打击（不耗气）")
        if pc.has_feature("frenzy") and COND_RAGING in pc.conditions:
            opts.append("狂暴攻击（狂战士附赠动作近战，狂暴结束力竭+1）")
        light = [w for w in pc.weapons if "light" in D.WEAPONS.get(w, {}).get("props", [])]
        if light and not pc.in_wild_shape():
            opts.append(f"双武器副手攻击（{D.WEAPONS[light[0]]['name']}）")
        if "misty_step" in pc.known_spells() and (pc.can_use_innate("misty_step")
                                                  or pc.spell_slots().get(2, 0) > 0):
            opts.append("迷踪步（附赠动作传送，摆脱纠缠）")
        if pc.klass == "barbarian" and pc.rage_left() and COND_RAGING not in pc.conditions:
            opts.append("狂暴")
        if pc.in_wild_shape():
            opts.append("解除野变（变回本体）")
        if pc.klass == "bard":
            opts.append("吟游诗人激励（给友方1颗激励骰）")
        return opts

    def _execute_bonus(self, enc: Encounter, pc: Character, idx: int,
                        opts: list[str]) -> bool:
        choice = opts[idx]
        mark = (len(enc.log), len(enc.rolls))
        def flush():
            self._flush_enc(enc, mark)
        foes = enc.active_enemies()
        allies = enc.active_party()
        if choice.startswith("回气"):
            r = enc.second_wind(pc, self.state.rng)
        elif choice.startswith("施法："):
            spell = next(k for k, v in D.SPELLS.items()
                         if v["name"] in choice)
            if D.SPELLS[spell]["kind"] in ("heal", "buff", "cleanse") \
                    and D.SPELLS[spell].get("effect") != "mark+1d6":
                ti = self.ui.choose("选择目标（队友）", [a.name for a in allies])
                targets = [allies[ti].name]
            else:
                ti = self.ui.choose("选择目标（敌人）", [f.name for f in foes])
                targets = [foes[ti].name]
            r = enc.cast(pc, spell, targets, self.state.rng)
        elif choice.startswith("狡诈动作：撤离"):
            enc.use_bonus(pc)
            enc.do_disengage_quiet(pc)
            r = {"ok": True}
            self.ui.system_line(f"  {pc.name} 狡诈动作：灵巧撤离。")
            return True
        elif choice.startswith("狡诈动作：躲藏"):
            r = enc.do_hide_bonus(pc, self.state.rng)
        elif choice.startswith("疾风连击"):
            ti = self.ui.choose("疾风连击目标", [f.name for f in foes])
            r = enc.flurry_of_blows(pc, foes[ti])
        elif choice.startswith("武术：附赠徒手"):
            ti = self.ui.choose("徒手打击目标", [f.name for f in foes])
            r = enc.martial_bonus_attack(pc, foes[ti])
        elif choice.startswith("狂暴攻击"):
            ti = self.ui.choose("狂暴攻击目标", [f.name for f in foes])
            r = enc.frenzy_attack(pc, foes[ti], pc.weapons[0] if pc.weapons else None)
        elif choice.startswith("震慑拳"):
            target = enc.find(enc.last_melee_hit.get(pc.name, ""))
            r = enc.stunning_strike(pc, target) if target else \
                {"ok": False, "error": "本回合还没有近战命中"}
        elif choice.startswith("双武器副手"):
            light = next(w for w in pc.weapons
                         if "light" in D.WEAPONS.get(w, {}).get("props", []))
            ti = self.ui.choose("副手攻击目标", [f.name for f in foes])
            enc.use_bonus(pc)
            r = enc.attack(pc, foes[ti], light, self.state.rng, offhand=True)
        elif choice.startswith("迷踪步"):
            if pc.can_use_innate("misty_step"):
                pc.innate_used.add("misty_step")
            else:
                enc._consume_slot(pc, 2)
            enc.do_disengage_quiet(pc)
            r = {"ok": True}
            self.ui.system_line(f"  {pc.name} 化作薄雾短距传送（摆脱纠缠）。")
            return True
        elif choice == "狂暴":
            r = enc.start_rage(pc)
        elif choice.startswith("解除野变"):
            r = enc.revert_shape(pc)
        elif choice.startswith("吟游诗人激励"):
            ti = self.ui.choose("激励目标（队友）", [a.name for a in allies])
            r = enc.bardic_inspiration(pc, allies[ti])
        else:
            return True
        if not r.get("ok"):
            self.ui.system_line(r.get("error", "失败"))
            return False
        flush()
        return True

    def _cast_menu(self, enc: Encounter, pc: Character) -> bool:
        spells = pc.known_spells()
        slots = pc.spell_slots()
        usable = [s for s in spells
                  if D.SPELLS[s]["level"] == 0 or slots.get(D.SPELLS[s]["level"], 0) > 0
                  or pc.can_use_innate(s)]
        if not usable:
            self.ui.system_line("没有可用的法术位。")
            return False
        names = [f"{D.SPELLS[s]['name']}（{'戏法' if D.SPELLS[s]['level']==0 else str(D.SPELLS[s]['level'])+'环'}，"
                 f"{D.SPELLS[s]['kind']}）" for s in usable]
        si = self.ui.choose("选择法术", names)
        spell = usable[si]
        sp = D.SPELLS[spell]
        # 上环施法（PHB）：可用更高环阶法术位增强伤害/治疗/目标数
        slot_level = None
        if sp["level"] > 0:
            higher = [lv for lv in (3, 2, 1)
                      if lv >= sp["level"] and slots.get(lv, 0) > 0]
            if len(higher) > 1:
                ui_opts = [f"{lv}环" + ("（基础环阶）" if lv == sp["level"] else "（上环）")
                           for lv in sorted(higher)]
                u = self.ui.choose("用哪个环阶的法术位？", ui_opts)
                slot_level = sorted(higher)[u]
            elif higher:
                slot_level = higher[0]
        foes = enc.active_enemies()
        allies = enc.active_party()
        if sp["kind"] in ("heal", "buff", "cleanse", "cleanse_magical") \
                and sp.get("effect") != "mark+1d6" and sp.get("effect") != "teleport":
            ti = self.ui.choose("选择目标（队友）", [a.name for a in allies])
            targets = [allies[ti].name]
        elif sp["kind"] == "buff" and sp.get("effect") in ("mark+1d6", "teleport"):
            if sp.get("effect") == "teleport":
                targets = []
            else:
                ti = self.ui.choose("印记目标（敌人）", [f.name for f in foes])
                targets = [foes[ti].name]
        else:
            ti = self.ui.choose("选择目标（敌人，多个用逗号分隔编号）",
                                [f.name for f in foes])
            targets = [foes[ti].name]
            extra = self.ui.free_input("额外目标编号（可空）> ")
            for part in extra.replace("，", ",").split(","):
                if part.strip().isdigit() and 1 <= int(part) <= len(foes):
                    targets.append(foes[int(part) - 1].name)
        r = enc.cast(pc, spell, targets, self.state.rng, slot_level=slot_level)
        if not r.get("ok"):
            self.ui.system_line(r.get("error", "施法失败"))
            return False
        return True

    def companion_turn(self, enc: Encounter, pc: Character) -> None:
        acted = False
        mark = (len(enc.log), len(enc.rolls))
        if self.ai_mode:
            agent = self.pc_agents.get(pc.name)
            if agent is not None:
                acted = self._pc_agent_turn(enc, pc, agent)
        if not acted:
            self._scripted_pc_turn(enc, pc)
        self._flush_enc(enc, mark)

    def _pc_agent_turn(self, enc: Encounter, pc: Character, agent) -> bool:
        reg = ToolRegistry()
        self.tools.register_pc_tools(reg, pc)
        session = self.pc_session(pc.name)
        foes = "；".join(f"{f.name} HP{f.hp_cur}/{f.hp_max} AC{f.ac()}"
                         for f in enc.active_enemies())
        allies = "；".join(f"{a.name} HP{a.hp_cur}/{a.hp_max}"
                           for a in enc.active_party())
        extra = ""
        if pc.klass == "fighter":
            extra = " 回气与动作如潮可用。" if not pc.second_wind_used else ""
        task = (f"【第{enc.round}轮 · {pc.name} 的回合】\n敌人：{foes}\n队伍：{allies}\n"
                f"你 HP {pc.hp_cur}/{pc.hp_max}，法术位 {pc.spell_slots()}，"
                f"资源 {'；'.join(pc.resource_lines())}。{extra}"
                f"想喊话就先 say 一句简短的战吼/提醒/嘲讽（可选），"
                f"再用 combat_action 执行你的回合（每回合一个动作+一个附赠动作）。")
        try:
            result: AgentResult = self.loop.run(agent, session, task, registry=reg)
            self._record_usage(agent, result)
        except RuntimeError as e:  # LLM 不可达 → 启发式兜底
            self.ui.system_line(f"（Agent 调用失败，{pc.name} 依靠战斗直觉行动：{e}）")
            return False
        return any(t["name"] == "combat_action" for t in result.tool_trace)

    def _scripted_pc_turn(self, enc: Encounter, pc: Character) -> None:
        """启发式队友 AI（剧本模式 / LLM 兜底）：完整动作+附赠动作。"""
        if not enc.alive(pc):
            return
        foes = enc.active_enemies()
        if not foes:
            return
        weakest = min(foes, key=lambda f: f.hp_cur)
        # 救援决策要看全队伍（含倒地/稳定者）；增益与激励只给存活者
        all_party = [a for a in enc.party() if isinstance(a, Character)]
        allies = [a for a in all_party if enc.alive(a)]
        hurt_ally = min((a for a in all_party if a.hp_cur < a.hp_max),
                        key=lambda a: a.hp_cur, default=None)
        downed = [a for a in all_party
                  if a.hp_cur <= 0 and "dead" not in a.conditions
                  and "stable" not in a.conditions]
        slots = pc.spell_slots()
        rng = self.state.rng
        kls = pc.klass
        has_slot = bool(slots)

        # --- 附赠动作先行（治疗真言/激励/印记/回气/狂暴/噬魂咒）
        if kls in ("cleric", "druid") and hurt_ally \
                and hurt_ally.hp_cur < hurt_ally.hp_max * 0.6 and slots.get(1, 0) > 0:
            enc.cast(pc, "healing_word", [hurt_ally.name], rng)
        elif kls == "bard" and allies and enc.bardic_inspiration(
                pc, min(allies, key=lambda a: a.hp_cur)).get("ok"):
            pass
        elif kls == "fighter" and pc.hp_cur < pc.hp_max * 0.4 and not pc.second_wind_used:
            enc.second_wind(pc, rng)
        elif kls == "barbarian" and pc.rage_left() and COND_RAGING not in pc.conditions:
            enc.start_rage(pc)
        elif kls == "ranger" and slots.get(1, 0) > 0 \
                and pc.concentration != "Hunter's Mark":
            enc.cast(pc, "hunters_mark", [weakest.name], rng)
        elif kls == "warlock" and has_slot and not pc.concentration:
            enc.cast(pc, "hex", [weakest.name], rng)
        elif kls == "paladin" and hurt_ally and hurt_ally.hp_cur < hurt_ally.hp_max * 0.4 \
                and pc.lay_on_hands_left() >= 5:
            enc.lay_on_hands(pc, hurt_ally, min(10, pc.lay_on_hands_left()))
        # --- 倒地队友：喂药拉起（治疗者法术位耗尽后的兜底）
        if downed and self.best_potion(pc):
            enc.use_item(pc, self.best_potion(pc), downed[0])
            return
        # --- 德鲁伊：野变（占用动作，变身当回合结束）
        if kls == "druid" and pc.level >= 2 and pc.wild_shape_used < 2 \
                and not pc.in_wild_shape() and pc.hp_cur >= pc.hp_max * 0.5 \
                and len(foes) >= 1:
            form = "black_bear" if pc.level >= 4 else "wolf"
            if enc.wild_shape(pc, form).get("ok"):
                return
        # --- 动作
        if downed and not (hurt_ally and hurt_ally.hp_cur <= 0):
            enc.stabilize(pc, downed[0])
            return
        if kls == "cleric":
            if slots.get(1, 0) > 0 and not any(
                    COND_BLESSED in a.conditions for a in allies):
                enc.cast(pc, "bless", [a.name for a in allies[:3]], rng)
                return
            # （祝福目标仅取存活者，见 allies 定义）
            if "sacred_flame" in pc.class_spells():
                enc.cast(pc, "sacred_flame", [weakest.name], rng)
                return
            enc.attack_action(pc, weakest, pc.weapons[0], rng)
            return
        if kls == "druid":
            if pc.in_wild_shape():
                enc.attack_action(pc, weakest, None, rng)   # 兽形天然武器
                return
            if len(foes) >= 3 and slots.get(1, 0) > 0:
                enc.cast(pc, "entangle", [f.name for f in foes], rng)
                return
            if "thorn_whip" in pc.class_spells():
                enc.cast(pc, "thorn_whip", [weakest.name], rng)
                return
            enc.attack_action(pc, weakest, pc.weapons[0], rng)
            return
        if kls == "wizard" or kls == "sorcerer":
            if pc.hp_cur < pc.hp_max * 0.4 and (slots.get(1, 0) > 0 or has_slot):
                enc.cast(pc, "shield", [], rng)
                return
            if kls == "sorcerer" and len(foes) >= 3 and slots.get(3, 0) > 0 \
                    and "fireball" in pc.class_spells():
                enc.cast(pc, "fireball", [f.name for f in foes], rng)
                return
            if len(foes) >= 2 and slots.get(2, 0) > 0 \
                    and "scorching_ray" in pc.class_spells():
                enc.cast(pc, "scorching_ray", [weakest.name], rng)
                return
            if len(foes) >= 2 and slots.get(1, 0) > 0 \
                    and "magic_missile" in pc.class_spells():
                enc.cast(pc, "magic_missile", [weakest.name], rng)
                return
            cantrips = [s for s in ("fire_bolt", "ray_of_frost", "sacred_flame")
                        if s in pc.class_spells()]
            enc.cast(pc, cantrips[0] if cantrips else pc.class_spells()[0],
                     [weakest.name], rng)
            return
        if kls == "warlock":
            enc.cast(pc, "eldritch_blast", [weakest.name], rng)
            return
        if kls == "bard":
            if "vicious_mockery" in pc.class_spells():
                enc.cast(pc, "vicious_mockery", [weakest.name], rng)
                return
            enc.attack_action(pc, weakest, pc.weapons[0], rng)
            return
        if kls == "paladin":
            target = weakest
            smite = None
            if slots and target.hp_max >= 25:
                smite = min(slots)          # 厚血目标：神佑斩（最低可用环阶）
            engaged = any(enc.alive(enc.find(n))
                          for n in enc.engagement.get(pc.name, set()))
            weapon = pc.weapons[0]
            if not engaged:
                weapon = next((w for w in pc.weapons
                               if D.WEAPONS.get(w, {}).get("ranged")), weapon)
            enc.attack_action(pc, target, weapon, rng, smite_slot=smite)
            return
        if kls == "monk":
            enc.attack_action(pc, weakest, pc.weapons[0], rng)
            if pc.level >= 2 and pc.ki_left() > 0 and enc.alive(weakest) \
                    and enc.has_bonus(pc):
                enc.flurry_of_blows(pc, weakest)
            if pc.level >= 5 and pc.ki_left() > 0 \
                    and enc.last_melee_hit.get(pc.name) and enc.has_bonus(pc):
                hit_target = enc.find(enc.last_melee_hit[pc.name])
                if hit_target is not None:
                    enc.stunning_strike(pc, hit_target)
            return
        if kls == "ranger":
            bow = next((w for w in pc.weapons if D.WEAPONS.get(w, {}).get("ranged")),
                       pc.weapons[0])
            enc.attack_action(pc, weakest, bow, rng)
            return
        if kls == "barbarian" and COND_RAGING in pc.conditions \
                and pc.has_feature("reckless"):
            enc.attack_action(pc, weakest, pc.weapons[0], rng, reckless=True)
            return
        target = min(foes, key=lambda f: (f.hp_cur - f.hp_max))
        enc.attack_action(pc, target, pc.weapons[0], rng)

    def _aftermath(self, enc: Encounter) -> None:
        xp = enc.defeated_xp()
        if xp:
            self.tools._tool_award_xp(xp, enc.title)
        # 战利品
        for eid, spec in self.module.encounters.items():
            if spec["title"] == enc.title:
                loot = spec.get("loot", {})
                if loot.get("gold"):
                    self.tools._tool_give_loot(gold=loot["gold"],
                                               items=loot.get("items", {}))
        self.autosave()

    # ================================================================ 通用
    def party_short_rest(self) -> None:
        song = any(c.klass == "bard" and c.level >= 2 for c in self.state.alive_party())
        if song:
            self.ui.system_line("（吟游诗人的休憩之歌：每次生命骰回复额外 +1d6）")
        for c in self.state.alive_party():
            spend = None
            if c is self.state.player and c.hp_die_count() > 0 and c.hp_cur < c.hp_max \
                    and self.aipc is None:
                # AI PC 模式：spend_dice=None（引擎自动花到回满/骰尽，与同伴同款，
                # 零 LLM、零 UI——character.short_rest 的既有语义）
                opts = [str(i) for i in range(0, c.hp_die_count() + 1)]
                i = self.ui.choose(f"{c.name}：消耗几颗生命骰？（共 {c.hp_die_count()} 颗，"
                                   "每颗回复 1d生命骰+体质）", opts)
                spend = int(opts[i])
            msg = c.short_rest(self.state.rng, spend_dice=spend, song_of_rest=song)
            self.ui.system_line(f"{c.name} 短休：{msg}")

    def party_long_rest(self) -> None:
        for c in self.state.party():
            self.ui.system_line(c.long_rest())
        self.autosave()

    def show_inventory(self) -> None:
        pc = self.state.player
        self.ui.raw(f"金币：{pc.gold}")
        for item, n in pc.inventory.items():
            name = D.ITEMS.get(item, {}).get("name", item)
            self.ui.raw(f"  {name} ×{n}")

    def autosave(self):
        """自动存档。返回存档路径（失败返回 None）——供 AI PC 的 explore_act
        ("save") 把结果回填给模型。"""
        try:
            return G.save_game(self.state, SAVE_FILE)
        except OSError:
            return None

    def _weapon_label(self, pc: Character, wkey: str) -> str:
        w = D.WEAPONS.get(wkey, {})
        return (f"{w.get('name', wkey)}（{w.get('dmg', '?')}"
                f"{'，远程' if w.get('ranged') else ''}）")

    def buy_good(self, key: str) -> dict:
        """商店非交互购买（docs/aipc-design.md §2.3）：校验场景/货品/金币，
        扣款入包、autosave、返回 {ok/note|error}。人类菜单（open_shop）与
        AI PC 的 explore_act("buy") 共用——全部毫秒级完成，绝不触达 ui.choose，
        因此不会在 Agent 工具分发内被 ToolTimeoutGuard 的 10s 常规超时砍断。"""
        key = str(key or "").strip()
        scene = self.module.scene_by_id(self.state.scene_id) or {}
        if not scene.get("shop"):
            return {"ok": False, "error": "这里没有商队。"}
        price = next((p for k, p in SHOP_GOODS if k == key), None)
        if price is None:
            return {"ok": False, "error": "货摊上没有这种货物。"}
        pc = self.state.player
        if pc.gold < price:
            return {"ok": False, "error": f"金币不够（需 {price}，持有 {pc.gold}）。"}
        pc.gold -= price
        pc.inventory[key] = pc.inventory.get(key, 0) + (10 if key == "healer_kit" else 1)
        self.ui.system_line(f"买下{D.ITEMS[key]['name']}（−{price} 金币，余 {pc.gold}）。")
        self.autosave()
        return {"ok": True, "note": f"买下{D.ITEMS[key]['name']}（−{price} 金币）。"}

    def open_shop(self) -> None:
        """商店：金币购买补给（scene.shop = true 的场景开放）。人类菜单，
        购买结算经 buy_good（行为不变：金币不足保留原提示文案）。"""
        pc = self.state.player
        while True:
            self.ui.raw(f"你摸了摸钱袋：{pc.gold} 金币。")
            opts = [f"{D.ITEMS[k]['name']}——{D.ITEMS[k]['desc']}（{price} 金币）"
                    for k, price in SHOP_GOODS] + ["（离开货摊）"]
            i = self.ui.choose("商队货摊——要买点什么？", opts)
            if i >= len(SHOP_GOODS):
                return
            key, _price = SHOP_GOODS[i]
            if pc.gold < _price:
                self.ui.system_line("金币不够，掌柜摇了摇头。")
                continue
            self.buy_good(key)

    def drink_potion_out_of_combat(self) -> None:
        """探索阶段喝药（引擎代掷回复骰，药效最强者优先）。"""
        pc = self.state.player
        key = self.best_potion(pc)
        if not key:
            self.ui.system_line("背包里没有药水。")
            return
        expr = D.ITEMS[key].get("heal", "0")
        r = roll(self.state.rng, expr)
        amt = pc.heal(r.total)
        pc.inventory[key] -= 1
        self.ui.system_line(f"你喝下{D.ITEMS[key]['name']}{r.describe()} → "
                            f"回复 {amt} 点（HP {pc.hp_cur}/{pc.hp_max}）。")

    @staticmethod
    def best_potion(pc: Character) -> str | None:
        """背包里药效最强的药水 key（无则 None）。医疗包的用次=持有数量。"""
        strength = {"potion_greater_healing": 2, "healing_potion": 1}
        avail = [(k, v) for k, v in pc.inventory.items()
                 if v > 0 and D.ITEMS.get(k, {}).get("heal")]
        if not avail:
            return None
        avail.sort(key=lambda kv: -strength.get(kv[0], 0))
        return avail[0][0]
