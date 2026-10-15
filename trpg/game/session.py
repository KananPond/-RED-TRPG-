"""GameSession：游戏主编排器（《赛博朋克 RED》规则口径）。

- AI 模式：DM Agent / PC Agent 经 harness 循环决策，引擎执行规则。
- 剧本模式（离线）：模组数据驱动 + 启发式队友 AI，无需任何 API。
两种模式共用同一套 RED 规则引擎（``trpg.rules``）与 UI。

战斗采用 RED 动作经济：每回合 **一个移动动作 + 一个其他动作**；
「附赠」槽被重新定义为**武器的第二发**（武器 LA/ROF ≥ 2 时可用，
LA 本身就是「一次攻击动作打几发」，因此第二发是可选的一次追加单发）。
玩家回合只有**一层菜单**：其他动作走完即结束回合，第二发再单独询问一次。
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
    Character, COND_CONTROLLED, COND_DEAD, COND_PRONE, COND_STABILIZED,
)
from ..rules.combat import BattleGrid, Encounter, METERS_PER_CELL
from . import state as G
from .social import TableSocial
from .tools import GameTools

SAVE_FILE = "saves/game.json"

#: 战斗收尾时无人施救的濒死者最多掷几次生存检定（门槛每次都 +1，必须封顶）
_DEATH_SAVE_AFTERMATH_LIMIT = 3

#: 远程武器类别（决定按射程带查难度值）
_RANGED_KINDS = ("pistol", "shotgun", "rifle")
#: 白刃战/斗殴类别
_MELEE_KINDS = ("melee", "brawling")

# ------------------------------------------------------------------ 商店
#: 一盒弹药的发数（按盒购买）
AMMO_BOX = 10
#: 义体安装费（手术与调试）
CYBERWARE_INSTALL_FEE = 100

#: 夜市场货摊：(类别, key, 名称, 价格 eb, 说明)
SHOP_GOODS: list[tuple[str, str, str, int, str]] = [
    ("gear", "ammo", "弹药（一盒 10 发）", D.GEAR["ammo"]["cost"] * AMMO_BOX,
     "按武器口径购买，装填时消耗。"),
    ("gear", "first_aid_kit", "急救包", D.GEAR["first_aid_kit"]["cost"],
     "稳定伤者、急救固定致命伤。"),
    ("gear", "medkit", "医疗包", D.GEAR["medkit"]["cost"], "治疗致命伤。"),
    ("gear", "flashlight", "手电", D.GEAR["flashlight"]["cost"], "黑暗中的光源。"),
    ("gear", "rope", "绳索", D.GEAR["rope"]["cost"], "攀爬与捆人。"),
    ("gear", "radio", "对讲机", D.GEAR["radio"]["cost"], "同频道通讯。"),
    ("gear", "agent", "智能体", D.GEAR["agent"]["cost"], "掌上个人电子设备。"),
    ("gear", "handcuffs", "手铐", D.GEAR["handcuffs"]["cost"], "塑料扎带或金属手铐。"),
    ("gear", "fake_id", "伪造证件", D.GEAR["fake_id"]["cost"], "需要伪造技能配合。"),
    ("gear", "binoculars", "望远镜", D.GEAR["binoculars"]["cost"], "远距离观察。"),
    ("weapon", "medium_pistol", "中型手枪", D.WEAPONS["medium_pistol"]["cost"],
     "2d6，LA 2，弹匣 12。"),
    ("weapon", "heavy_pistol", "重型手枪", D.WEAPONS["heavy_pistol"]["cost"],
     "3d6，LA 2，弹匣 8。"),
    ("weapon", "shotgun", "霰弹枪", D.WEAPONS["shotgun"]["cost"],
     "5d6，LA 1，弹匣 4。"),
    ("weapon", "assault_rifle", "突击步枪", D.WEAPONS["assault_rifle"]["cost"],
     "5d6，LA 1，弹匣 25。"),
    ("weapon", "heavy_melee", "重型近战武器", D.WEAPONS["heavy_melee"]["cost"],
     "3d6，白刃战无视一半护甲。"),
    ("armor", "kevlar", "凯夫拉", D.ARMORS["kevlar"]["cost"], "SP 7 防弹衣。"),
    ("armor", "light_armorjack", "轻型防弹夹克", D.ARMORS["light_armorjack"]["cost"],
     "SP 11，外观日常。"),
    ("armor", "medium_armorjack", "中型防弹衣", D.ARMORS["medium_armorjack"]["cost"],
     "SP 12，看得出是护甲。"),
    ("cyber", "pain_editor", "赛博痛觉编辑器",
     D.CYBERWARE["pain_editor"]["cost"] + CYBERWARE_INSTALL_FEE,
     "无视重伤 -2；人性 -7。"),
    ("cyber", "subdermal_armor", "皮下护甲",
     D.CYBERWARE["subdermal_armor"]["cost"] + CYBERWARE_INSTALL_FEE,
     "全身 SP 7；人性 -7。"),
    ("service", "repair_armor", "修复护甲", D.ARMOR_REPAIR_COST,
     "把身上护甲的 SP 补满。"),
]

#: 战斗结束后清除的临时战斗状态（RED 只有战斗内定位状态；
#: 重伤/濒死/被稳定/致命伤是持久状态，必须保留）。
_POST_COMBAT_CLEAR = {COND_PRONE, COND_CONTROLLED, "aimed", "facedown",
                      "choke_rounds", "cover", "stunned", "grappled"}


def _reply_err(r: dict, fallback: str = "失败") -> str:
    return str((r or {}).get("error") or (r or {}).get("reason") or fallback)


def _is_ranged(weapon_key: str | None) -> bool:
    info = D.WEAPONS.get(str(weapon_key or "")) or {}
    return info.get("kind") in _RANGED_KINDS


def _wound_of(c: Character) -> str:
    return c.wound_name()


def _eb(n) -> str:
    return f"{int(n)} eb"


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
        self._ip_ask_scene: str | None = None  # 已就本场景询问过改进点花费
        self._ip_given: set[str] = set()       # 已发放 IP 的场景（防重复）
        self._fled: list[Character] = []       # 本场战斗已撤退的成员
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
            # NPC 子智能体调度器（NPC = DM 控制的 SubAgent）：剧本模式不构建，
            # npc_dialogue 工具返回不可用
            from .npcagent import NPCDirector
            self.npc_director: NPCDirector | None = NPCDirector(self, loop)
        # AI PC 控制器：构造后由 AIPCController.attach 赋值；
        # 为 None 时全部新分支短路，行为与基线一致
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

    def broadcast_map(self) -> None:
        """把当前战术地图快照推给 UI（Web 为 SSE map 事件，终端静默记录）。
        变更点：enter_scene 换图、DM map_update 工具、战斗布子/走位/写回。"""
        try:
            self.ui.map_update(self.state.map_brief())
        except Exception:  # noqa: BLE001 —— UI 广播不阻塞对局
            pass

    # ================================================================ 主流程
    def run(self) -> None:
        mode = "AI 模式（DM 与队友由 Agent 扮演）" if self.ai_mode \
            else "经典模式（离线 · 规则引擎结算）"
        self.ui.raw(f"\n《{self.module.title}》 —— {self.module.en_title}")
        self.ui.raw(f"模式：{mode}")
        self.ui.raw(self.module.setting)
        self.enter_scene(self.state.scene_id)
        # 开局结算：队伍可能已带着改进点进场（读档/预设），先给一次花费机会
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
            self.ui.raw("夜之城的雨把血迹冲进了下水道……可使用存档重新来过（菜单：读取）。")

    def _epilogue(self) -> None:
        # 终章：先把最终场景的改进点结清（离开场景的同一套口径）
        final = self.module.scene_by_id(self.state.scene_id) or {}
        self._award_scene_ip(final, f"{final.get('location', '终局')} 的了结")
        self.ui.raw("\n" + "=" * 62)
        for line in self.module.epilogue.split("\n"):
            self.ui.raw(line)
        self.ui.raw(f"\n最终队伍：\n{self.state.party_snapshot()}")
        self.ui.raw(f"欧元：{_eb(self.state.player.eurobucks)}    改进点："
                    + "、".join(f"{c.name} {c.ip}" for c in self.state.party()))

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
        for para in str(scene.get("intro", "")).split("\n"):
            self.ui.raw(para)
        # 换场照料：濒死者若无人稳定，就地做几次生存检定；队友则施救
        for c in self.state.party():
            if c.is_dead:
                continue
            if c.hp_cur < 1:
                self._stabilize_aftermath(c)
                if not c.is_dead and c.hp_cur < 1:
                    c.revive(1)
                    who = "你" if c is self.state.player else c.name
                    if not c.is_dead:
                        self.ui.system_line(
                            f"休整与照料后，{who} 恢复了意识（PW 1 归队）。")
        # 场景声明 rest_day：这一天过去（剧本节奏允许队伍包扎、补给、踩点）。
        # 按手册的休整规则，每个完整休息日恢复等于体格的 PW；顺带把护甲修补好
        # （磨损的 SP 是打不下去的主因），并让队里的医疗师治疗一处致命伤。
        if scene.get("rest_day"):
            healed = []
            for c in self.state.party():
                if c.is_dead:
                    continue
                n = c.rest_day()
                before_sp = c.armor_at()
                c.repair_armor()
                if n:
                    healed.append(f"{c.name} +{n} PW")
                if c.armor_at() > before_sp:
                    healed.append(f"{c.name} 护甲修回 SP {c.armor_at()}")
            # 医疗师用一整天治疗致命伤（每次尝试四小时，一天够处理一处）
            medics = [c for c in self.state.party()
                      if not c.is_dead and c.skill("paramedic") >= 5]
            hurt = [c for c in self.state.party()
                    if not c.is_dead and c.crit_injuries and c not in medics]
            if medics and hurt:
                medic, patient = medics[0], hurt[0]
                inj = patient.crit_injuries[0]
                info = (D.CRIT_INJURIES.get(inj.get("roll", 7)) or {}).get("treatment") or {}
                skill = info.get("skill", "paramedic")
                dv = int(info.get("dv", 15))
                res = medic.check(self.state.rng, skill, dv=dv)
                if res.success and patient.treat_crit_injury(0):
                    healed.append(f"{medic.name} 治好了 {patient.name} 的「{inj['name']}」")
            self.ui.system_line("☾ 一天过去了：整队得到休整"
                                + ("（" + "、".join(healed) + "）" if healed else "。"))
        self._flavor_line(scene)
        # 战术地图：按场景声明换图布子；无 map 声明的场景 → None（剧场模式）
        new_map = G.build_map_state(self.module, scene, self.state.party())
        if new_map is not None or self.state.map is not None:
            self.state.map = new_map
            self.broadcast_map()
        # AI PC：场景变化 → 重写卡 scenario 并重建 agent
        if self.aipc is not None:
            self.aipc.refresh()
        # 改进点花费（须先有 IP；同一场景只问一次）
        if any(c.ip > 0 for c in self.state.party()) and self._ip_ask_scene != scene_id:
            self._ip_ask_scene = scene_id
            self.resolve_pending_choices()
        self.autosave()

    # ---------------------------------------------------------------- 改进点
    def resolve_pending_choices(self) -> None:
        """结算全队待花的改进点（IP）。

        沿用 Web UI 已理解的 ``choose`` 交互协议（``ask``/``choice_made`` 事件流）：
        玩家逐项选择提升技能／提升职业特殊能力，同伴与 AI 代打的玩家走默认策略。
        RED 无等级、无经验，属性不可提升（规则层亦未暴露 raise_stat）。"""
        for c in self.state.party():
            self._resolve_member_ip(c)

    def _resolve_member_ip(self, c: Character) -> None:
        if c.ip <= 0 or c.is_dead:
            return
        if c is not self.state.player or self.aipc is not None:
            self._auto_spend_ip(c)
            return
        self._ask_ip_choice(c)

    def _ask_ip_choice(self, c: Character) -> None:
        """IP 消费菜单：提升技能 / 提升职业能力 / 查看 / 跳过。

        买了东西才继续循环；一整轮没花出去（比如 IP 不够任何一项）就收摊，
        避免菜单原地空转。"""
        if not hasattr(self.ui, "choose"):
            self._auto_spend_ip(c)
            return
        while c.ip > 0:
            role_cost = c.ip_cost_role()
            can_skill = any(c.skill(s) < D.SKILL_MAX
                            for s in D.SKILL_ORDER)
            opts = [
                f"提升技能（当前 {c.ip} IP，技能 = 20 × 新等级，×2 技能翻倍）"
                if can_skill else "技能都已到顶",
                f"提升职业特殊能力 → {c.role_rank + 1} 级（{role_cost} IP）"
                if c.role_rank < D.ROLE_ABILITY_MAX else "职业特殊能力已满级",
                f"查看可用改进点（现有 {c.ip} IP）",
                "稍后再花（保留改进点）",
            ]
            i = self.ui.choose(
                f"—— {c.name} 的改进点（IP {c.ip}，{c.role_name} {c.role_rank} 级）——", opts)
            if i == 0:
                if not can_skill:
                    self.ui.system_line("所有技能都已到 10 级上限。")
                    continue
                if self._raise_skill_menu(c) > 0:
                    continue
                return
            if i == 1:
                if c.role_rank >= D.ROLE_ABILITY_MAX:
                    self.ui.system_line("职业特殊能力已满级。")
                    continue
                ok, msg = c.raise_role()
                self.ui.system_line(("✔ " if ok else "✘ ") + msg)
                if not ok:
                    return
            elif i == 2:
                self.ui.system_line(
                    f"{c.name} 现有改进点 {c.ip}；"
                    f"技能提升 20 × 新等级（×2 技能翻倍），"
                    f"职业特殊能力提升 {c.ip_cost_role()} IP。")
            else:
                return

    def _raise_skill_menu(self, c: Character) -> int:
        """先选挂钩属性分组，再选技能（41 项技能对菜单太长）。

        返回 1 = 花掉了改进点，0 = 什么都没花（调用方据此收摊）。"""
        groups = [(k, [s for s in D.SKILL_ORDER if D.SKILLS[s]["stat"] == k])
                  for k in D.STAT_ORDER]
        groups = [(k, ss) for k, ss in groups if ss]
        labels = [f"{D.STATS[k]['name']}（{len(ss)} 项）" for k, ss in groups]
        gi = self.ui.choose("提升哪一类技能？", labels + ["（返回）"])
        if gi >= len(groups):
            return 0
        key, skills = groups[gi]
        # 已掌握且还付得起的排前面；其余置灰附注（不可选）
        known = [s for s in skills if c.skill(s) > 0 and c.skill(s) < D.SKILL_MAX]
        known.sort(key=lambda s: (c.ip_cost_skill(s), D.SKILLS[s]["name"]))
        new = [s for s in skills if c.skill(s) == 0]
        new.sort(key=lambda s: (c.ip_cost_skill(s), D.SKILLS[s]["name"]))
        affordable = [s for s in known + new if c.ip >= c.ip_cost_skill(s)]
        if not affordable:
            cheapest = min((s for s in known + new), default=None,
                           key=lambda s: c.ip_cost_skill(s))
            need = c.ip_cost_skill(cheapest) if cheapest else 0
            self.ui.system_line(
                f"改进点不足：{D.STATS[key]['name']}类里最便宜的一项也要 {need} IP"
                f"（现有 {c.ip}）。")
            return 0
        opts = []
        for s in affordable:
            lv = c.skill(s)
            cost = c.ip_cost_skill(s)
            mark = "★×2" if D.SKILLS[s].get("x2") else ""
            tag = "" if lv > 0 else "（新技能）"
            opts.append(f"{D.SKILLS[s]['name']}{mark}{tag} {lv}→{lv + 1}"
                        f"（{cost} IP，检定加值 {c.skill_total(s)}→"
                        f"{c.skill_total(s) + 1}）")
        si = self.ui.choose(f"{D.STATS[key]['name']}类技能（现有 {c.ip} IP）",
                            opts + ["（返回）"])
        if si >= len(affordable):
            return 0
        skill = affordable[si]
        if c.skill(skill) >= D.SKILL_MAX:
            self.ui.system_line(f"{c.skill_name(skill)} 已达上限 {D.SKILL_MAX}。")
            return 0
        ok, msg = c.raise_skill(skill)
        self.ui.system_line(("✔ " if ok else "✘ ") + msg)
        return 1 if ok else 0

    def _auto_spend_ip(self, c: Character) -> None:
        """同伴 / AI 代打玩家的默认花费策略：先提升职业能力，再补主用技能。"""
        spendable = [s for s in ("handgun", "shoulder_arms", "melee_weapon", "brawling",
                                 "evasion", "stealth", "perception", "first_aid",
                                 "paramedic", "athletics", "persuasion")
                     if s in D.SKILLS]
        while c.ip > 0:
            if c.role_rank < D.ROLE_ABILITY_MAX and c.ip >= c.ip_cost_role():
                ok, msg = c.raise_role()
                if ok:
                    self.ui.system_line(f"（{c.name}）{msg}")
                    continue
            target = min((s for s in spendable if c.skill(s) < D.SKILL_MAX),
                         key=lambda s: (c.skill(s), s), default=None)
            if target is None:
                return
            cost = c.ip_cost_skill(target)
            if c.ip < cost:
                lower = [s for s in spendable
                         if c.skill(s) < D.SKILL_MAX and c.ip_cost_skill(s) <= c.ip]
                if not lower:
                    return
                target = min(lower, key=lambda s: (c.skill(s), s))
            ok, msg = c.raise_skill(target)
            if not ok:
                return
            self.ui.system_line(f"（{c.name}）{msg}")

    def award_ip(self, amount, reason: str = "",
                 who: str | None = None) -> dict:
        """给队伍（或指定成员）发放改进点。DM 的 award_ip 工具可直接复用。

        ``amount`` 是**每人**获得的改进点（RED 口径：改进点按人计，不做队伍平摊），
        场景 ``ip`` 字段与 ``award_ip`` 工具都走这里。"""
        amount = int(amount or 0)
        if amount <= 0:
            return {"ok": False, "error": "改进点必须为正数"}
        members = self.state.party()
        if who:
            one = self.state.find_member(who)
            members = [one] if one is not None else members
        for c in members:
            c.gain_ip(amount)
        tag = f"（{reason}）" if reason else ""
        self.ui.system_line(f"✦ 改进点 +{amount}/人{tag}"
                            + "；".join(f"{c.name} {c.ip} IP" for c in members))
        if self.aipc is None and not self.ai_mode:
            self.resolve_pending_choices()
        return {"ok": True, "each": amount,
                "ip": {c.name: c.ip for c in members}}

    def _award_scene_ip(self, scene: dict, reason: str = "") -> None:
        """离开场景时发放该场景的改进点（每人 ip 点，只发一次）。"""
        ip = int(scene.get("ip") or 0)
        sid = scene.get("id") or self.state.scene_id
        if ip <= 0 or sid in self._ip_given:
            return
        self._ip_given.add(sid)
        self.award_ip(ip, reason or f"{scene.get('location', sid)} 的历练")

    # ---------------------------------------------------------------- 台词
    def _flavor_line(self, scene: dict) -> None:
        """剧本模式下同伴的应景发言（夜之城语域，按职业取台词池）。"""
        if self.ai_mode:
            return
        generic = {
            "solo": ["我走前面。有人动手，先看我的枪口。", "这地方适合设伏。保持间距。",
                     "别背对巷口。"],
            "netrunner": ["这里的信号很脏……有人在盯着这条线。", "让我扫一遍本地网络。",
                          "别用公共终端付账。"],
            "tech": ["这面墙的电路接得很业余。", "有东西能拆——也有东西能修。",
                     "护甲该补了，回去找我。"],
            "medtech": ["有人受伤就喊我，别硬撑。", "你的脸色比昨天差。",
                        "义体排异不看黄历。"],
            "media": ["这画面值得录下来。", "我闻到故事的味道了。",
                      "谁在撒谎，镜头看得见。"],
            "cop": ["按规矩来，先亮身份。", "别让我在报告里写你的名字。",
                    "这一带归 NCPD 管——名义上。"],
            "corporate": ["先谈条件，再谈交情。", "这栋楼的价格我大概知道。",
                          "别在这里提那个名字。"],
            "fixer": ["一切都能谈，只要价钱合适。", "这条街我熟，跟我走。",
                      "你想见的人，我大概能约到。"],
            "nomad": ["引擎声不对。都上车。", "荒原上没人替你收尸。",
                      "家族的车还在外面等着。"],
            "rockerboy": ["这地方该配上更好的音乐。", "嘘——听，人群在躁动。",
                          "别挡镜头，我在录。"],
        }
        for c in self.state.companions:
            if c.hp_cur < 1:
                continue
            pool = generic.get(c.role, generic["solo"])
            h = sum((scene.get("id", "") + c.name).encode("utf-8"))
            self.ui.companion_line(c.name, pool[h % len(pool)])

    def advance_scene(self, scene_id: str) -> dict:
        cur = self.module.scene_by_id(self.state.scene_id)
        if cur is None:
            return {"ok": False, "error": "当前场景不存在"}
        valid = {cur["next"]} if cur.get("next") else set()
        if scene_id != self.state.scene_id and scene_id not in valid:
            return {"ok": False,
                    "error": f"不能直接跳到 {scene_id}。当前可推进到："
                             f"{sorted(valid) or '（已是最终场景）'}"}
        if scene_id == self.state.scene_id:
            return {"ok": True, "note": "已在该场景。"}
        # 离开本场景：发放本场景的改进点（每人 ip 点，只发一次）
        self._award_scene_ip(cur)
        self.enter_scene(scene_id)
        if self.module.scene_by_id(scene_id)["next"] is None:
            return {"ok": True, "note": "已进入最终场景。"}
        return {"ok": True, "scene": scene_id}

    # ================================================================ 检定入口
    def skill_check(self, who: str, skill: str, dv, context: str = "") -> dict:
        """技能检定（剧本模式 / DM 工具共用入口）。

        ``who`` 为角色全名或「队伍」；玩家本人会先等其点击掷骰按钮再结算
        （与 GameTools 的玩家掷骰闸门一致）。"""
        from ..rules.dice import DiceResult
        if skill not in D.SKILLS:
            return {"ok": False, "error": f"未知技能 {skill}"}
        member = self.state.find_member(who) if who and who != "队伍" else None
        if member is None:
            member = self.state.player
        try:
            dv_i = int(dv)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"难度值非法：{dv!r}"}
        if member is self.state.player and hasattr(self.ui, "ask_roll") \
                and self.aipc is None:
            self.ui.ask_roll(
                f"轮到你掷骰 —— {D.SKILLS[skill]['name']}检定（DV {dv_i}）")
        res: DiceResult = member.check(self.state.rng, skill, dv_i)
        payload = _check_payload(member, skill, res, dv_i, context)
        try:
            # UI 的 check_line 吃**字典**（ConsoleUI 的 detail / WebUI 的
            # §9 dice 载荷都由它出），这里传结构化载荷而非 DiceResult
            self.ui.check_line(payload, context)
        except Exception:  # noqa: BLE001 —— UI 呈现不阻塞结算
            pass
        return payload

    def opposed_check(self, who: str, skill: str, defender: str,
                      defender_skill: str, reason: str = "") -> dict:
        """对抗检定（说服 vs 读心、擒抱 vs 斗殴……）；平手防守方胜。"""
        if skill not in D.SKILLS or defender_skill not in D.SKILLS:
            return {"ok": False, "error": "未知技能"}
        atk = self.state.find_member(who) or self.state.player
        dfd = self.state.find_member(defender)
        if dfd is None:
            return {"ok": False, "error": f"找不到角色 {defender}"}
        a, b, win = atk.opposed_check(self.state.rng, skill, dfd, defender_skill)
        self.ui.system_line(
            f"⚖ {reason or '对抗检定'}：{atk.name} {D.SKILLS[skill]['name']} "
            f"{a.total} vs {dfd.name} {D.SKILLS[defender_skill]['name']} "
            f"{b.total} → {'成功' if win else '失败'}")
        return {"ok": True, "success": bool(win), "attacker_total": a.total,
                "defender_total": b.total, "rolls": list(a.rolls),
                "dv": b.total}

    # ================================================================ 探索：剧本模式
    def scripted_explore_turns(self, scene: dict) -> str:
        encounter_done = False
        checks_done: set[int] = set()
        used: set[str] = set()      # 每个非推进项只提供一次（避免原地空转）
        guard = 0                   # 硬上限：任何输入都不允许把探索循环变成死循环
        while True:
            guard += 1
            if guard > 200:
                self.ui.system_line("⚠ 探索循环超出上限（200 次），自动收尾。")
                if scene.get("next"):
                    self.advance_scene(scene["next"])
                    return "advanced"
                return "quit"
            # 终局场景（无 next、也没有待打的遭遇）：给玩家一次行动机会后交给 epilogue。
            # 脚本模式在这里没有「推进」项可选，不结束就会原地空转。
            if guard > 1 and not scene.get("next") \
                    and not (scene.get("encounter") and not encounter_done):
                return "end"
            checks = list(scene.get("checks") or [])
            options = [
                f"检定机会：{D.SKILLS[c['skill']]['name']}（DV {int(c.get('dv', 15))}）"
                for i, c in enumerate(checks)
                if i not in checks_done and c.get("skill") in D.SKILLS]
            can_forward = bool(scene.get("encounter")) and not encounter_done
            base = []
            if can_forward:
                base.append("前进（触发剧情）")
            elif scene.get("next"):
                base.append("前往下一场景")
            base += ["查看状态", "查看背包", "查看义体", "存档", "退出游戏"]
            pc = self.state.player
            # 待救援名单：PW < 1、未死、且尚未被稳定（已稳定者不需要再救）
            wounded = [c for c in self.state.party()
                       if c.hp_cur < 1 and not c.is_dead
                       and COND_STABILIZED not in c.conditions]
            if wounded and "稳定" not in used:
                base.insert(0, "稳定（急救，把濒死者拉回 1 PW）")
            # 致命伤处理：只有「还能急救固定」的伤才该出现在菜单里，
            # 否则同一处伤会被反复固定，脚本模式直接变成死循环。
            fixable = pc.fixable_crit_indices()
            if fixable and "急救固定" not in used:
                base.insert(0, "急救固定（处理一处致命伤）")
            if pc.crit_injuries and "治疗致命伤" not in used:
                base.insert(1, "治疗致命伤（四小时）")
            # 休整：只对「已被稳定且 PW 未满」的成员有意义，且每个场景只提供一次
            if "休整" not in used and any(
                    COND_STABILIZED in c.conditions and c.hp_cur < c.hp_max
                    for c in self.state.party()):
                base.insert(0, "休整一天（先被稳定者恢复体格点 PW）")
            if scene.get("shop"):
                base.insert(2, "夜市场（购买装备）")
            opts = options + base
            idx = self.ui.choose(f"—— {scene['location']} —— 你要做什么？", opts)
            if idx < len(options):
                orig = [i for i, c in enumerate(checks)
                        if i not in checks_done and c.get("skill") in D.SKILLS]
                sk_idx = orig[idx]
                sk = checks[sk_idx]
                r = self.skill_check("队伍", sk["skill"], sk.get("dv", 15),
                                     context=str(sk.get("skill", "")))
                self.ui.raw("  " + str(sk.get("success") if r.get("success")
                                       else sk.get("fail", "")))
                checks_done.add(sk_idx)
                # 潜行成功 → 下一场遭遇敌人措手不及（RED 首轮无法行动）
                if sk["skill"] == "stealth" and r.get("success"):
                    self.state.flags["party_stealth"] = self.state.scene_id
                continue
            choice = opts[idx]
            if choice.startswith("休整"):
                used.add("休整")
            elif choice.startswith(("前进", "前往")):
                if can_forward:
                    encounter_done = True
                    self.launch_encounter(scene["encounter"])
                    outcome = self.run_combat()
                    if outcome == "tpk":
                        raise GameOver()
                    if scene.get("outro"):
                        self.ui.raw("")
                        for para in str(scene["outro"]).split("\n"):
                            self.ui.raw(para)
                    if scene.get("next") is None:
                        return "end"
                    self.advance_scene(scene["next"])
                    return "advanced"
                self.advance_scene(scene["next"])
                return "advanced"
            if choice.startswith("休整"):
                self.party_rest_day()
            elif choice.startswith("稳定"):
                used.add("稳定")
                self.stabilize_in_explore(party_only=True)
            elif choice.startswith("急救固定"):
                used.add("急救固定")     # 失败也不重试，避免原地空转
                self.crit_first_aid_in_explore(self.state.player)
            elif choice.startswith("治疗致命伤"):
                used.add("治疗致命伤")
                self.treat_crit_in_explore(self.state.player)
            elif choice.startswith("夜市场"):
                self.open_shop()
            elif choice == "查看状态":
                self.ui.raw(self.state.party_snapshot())
            elif choice == "查看背包":
                self.show_inventory()
            elif choice == "查看义体":
                self.show_cyberware()
            elif choice.startswith("存档"):
                path = G.save_game(self.state, SAVE_FILE)
                self.ui.system_line(f"已保存到 {path}")
            else:
                return "quit"

    # ================================================================ 探索：AI 模式
    def ai_explore_turns(self, scene: dict) -> str:
        self.ui.system_line(
            "输入行动或对白；/状态 /背包 /义体 /急救 /稳定 /休整 /购买 /存档 /退出 为指令。")
        guard = 0
        while True:
            guard += 1
            if guard > 500:
                self.ui.system_line("⚠ 本节拍循环超出上限（500 次），自动收尾。")
                if scene.get("next"):
                    self.advance_scene(scene["next"])
                    return "advanced"
                return "quit"
            if self.aipc is not None:
                # 1) 人类旁听指令（非阻塞）
                cmd = self.aipc.poll_command(0.4)
                if cmd is not None:
                    cmd = cmd.strip()
                    if cmd == "/退出":
                        return "quit"
                    self._handle_explore_command(cmd)
                    continue
                # 2) AI 代打本节拍；False = LLM 故障 → 本轮交还人类
                if self.aipc.explore_beat():
                    if self._post_beat():
                        return "end"
                    continue
                self.ui.system_line("（AI 代打暂不可用，本轮请亲自输入行动。）")
            # 3) 人类输入
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

    _EXPLORE_COMMANDS = ("/状态", "/背包", "/义体", "/急救", "/稳定", "/休整",
                         "/购买", "/存档", "/保存", "/地图")

    def _handle_explore_command(self, text: str) -> bool:
        """探索期斜杠指令分发。返回 True = 已消费；False = 非指令。"""
        text = str(text or "").strip()
        if not text.startswith("/") or text == "/退出":
            return False
        if text == "/状态":
            self.ui.raw(self.state.party_snapshot())
            return True
        if text == "/背包":
            self.show_inventory()
            return True
        if text == "/义体":
            self.show_cyberware()
            return True
        if text == "/急救":
            self.crit_first_aid_in_explore(self.state.player)
            return True
        if text == "/稳定":
            self.stabilize_in_explore(party_only=True)
            return True
        if text == "/休整":
            self.party_rest_day()
            return True
        if text == "/购买":
            scene = self.module.scene_by_id(self.state.scene_id) or {}
            if scene.get("shop"):
                self.open_shop()
            else:
                self.ui.system_line("这一带没有夜市摊子。")
            return True
        if text in ("/存档", "/保存"):
            path = G.save_game(self.state, SAVE_FILE)
            self.ui.system_line(f"已保存到 {path}")
            return True
        if text == "/地图":
            for ln in (getattr(self.ui, "map_lines", list)() or []):
                self.ui.raw(ln)
            return True
        if text.startswith("map:move"):
            # 探索期点图移动意图：坐标交 DM 裁定
            raw = text[len("map:move"):].strip().replace("，", ",")
            try:
                x_s, y_s = [p for p in raw.split(",") if p.strip()][:2]
                x, y = int(x_s), int(y_s)
            except (ValueError, IndexError):
                self.ui.system_line("（坐标无效）")
                return True
            self.ui.system_line(f"（你想移动到 ({x},{y})）")
            self.dm_turn(f"我想移动到地图上的（{x},{y}）附近",
                         input_label="玩家行动")
            return True
        if self.aipc is not None:
            self.ui.system_line("（旁听指令仅支持：/状态 /背包 /义体 /急救 /稳定 "
                                "/休整 /购买 /存档 /退出）")
            return True
        return False

    def _post_beat(self) -> bool:
        """节拍/战斗收尾。返回 True = 最终场景战斗完结（触发 epilogue）。"""
        if self.encounter is None:
            return False
        outcome = self.run_combat()
        if outcome == "tpk":
            raise GameOver()
        scene = self.module.scene_by_id(self.state.scene_id)
        if scene.get("outro"):
            self.ui.raw("")
            for para in str(scene["outro"]).split("\n"):
                self.ui.raw(para)
        return scene.get("next") is None

    def dm_turn(self, player_text: str, lead: str = "玩家回合，请推进剧情",
                input_label: str = "玩家输入") -> str | None:
        """跑一次 DM Agent。桌面上同伴的插话（table_log）先消化进其上下文。"""
        scene = self.module.scene_by_id(self.state.scene_id)
        table = self._digest_table()
        lines = [f"【场景】{scene['id']}：{scene['location']}",
                 f"【队伍】{self.state.party_snapshot()}",
                 f"【旗标】{self.state.flags or '无'}"]
        if self.state.map:
            lines.append("【地图】\n" + self.state.map_digest())
        if table:
            lines.append(f"【桌上插话】\n{table}")
        window = "\n".join([player_text or "", table or ""])
        wi_before, wi_depth = self._dm_wi(window)
        if wi_before:
            lines.append("【世界书】\n" + wi_before)
        lines.append(f"【{input_label}】{player_text}")
        context = [{"role": "user", "content": "\n".join(lines)}]
        result = self.loop.run(self.dm_agent, self._dm_session,
                               f"{lead}：{player_text}",
                               context_messages=context, registry=self.registry,
                               post_build=self._dm_post_build(wi_depth))
        self._record_usage(self.dm_agent, result)
        if result.content and result.content.strip():
            self.ui.dm_narration(result.content.strip())
            return result.content.strip()
        return None

    def _dm_wi(self, window: str) -> tuple[str, list]:
        """DM 的世界书加餐：engine WI（场景/旗标/检定/NPC）合并模组 dm.book。"""
        from ..st import engine_worldinfo, scan
        wi = engine_worldinfo(self.state)
        book_raw = (getattr(self.module, "dm", {}) or {}).get("book")
        if isinstance(book_raw, dict) and book_raw:
            from ..st import merge, wi_from_book
            wi = merge(wi, wi_from_book(book_raw))
        if not (window and window.strip()):
            return "", []
        try:
            res = scan(wi, window, rng=self.state.rng)
        except Exception:  # noqa: BLE001 —— 世界书故障不阻塞 DM 回合
            return "", []
        text = "\n".join(x for x in (res.before, res.after) if x and x.strip())
        return text, list(res.at_depth)

    def _dm_post_build(self, wi_depth: list):
        """post_build 钩子工厂：DM 卡 depth_prompt + @D 世界书条目真按深度注入。"""
        injections = list(wi_depth or [])
        dp = getattr(self.dm_agent, "depth_prompt", "") if self.dm_agent else ""
        if dp and dp.strip():
            from ..st import Injection
            injections.append(Injection(depth=4, role="system", order=90, text=dp))
        if not injections:
            return None
        from ..st import inject_at_depth
        return lambda messages: inject_at_depth(messages, injections)

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
            return {"ok": False, "error": "未知遭遇 " + str(encounter_id)}
        enc = self._build_encounter(encounter_id, spec)
        self._fled = []
        # 潜行成功 → 敌人措手不及：先攻顺序里排到最后（首轮被跳过）
        if self.state.flags.get("party_stealth") == self.state.scene_id:
            self._apply_surprise(enc)
            self.state.flags.pop("party_stealth", None)
            self.ui.system_line("你们悄然就位——敌人措手不及，第一轮来不及反应！")
        if hasattr(self.ui, "sw_used"):
            try:
                self.ui.sw_used = False
            except Exception:  # noqa: BLE001
                pass
        self.encounter = enc
        self.tools.encounter = enc
        if enc.grid is not None:
            self._sync_map_from_encounter(enc)
        self.ui.raw("")
        for para in str(spec.get("intro", "")).split("\n"):
            self.ui.raw("  " + para)
        return {"ok": True, "encounter": encounter_id,
                "note": "战斗即将开始，编排器接管回合流程。"}

    def _build_encounter(self, encounter_id: str, spec: dict) -> Encounter:
        """组建遭遇：优先用 state.build_encounter（含地图布子），失败则本地兜底。"""
        builder = getattr(G, "build_encounter", None)
        if builder is not None:
            try:
                enc = builder(encounter_id, self.state)
                if isinstance(enc, Encounter):
                    self._attach_entry(enc, spec)
                    return enc
            except Exception:  # noqa: BLE001 —— 兜底：本地组建
                pass
        return self._local_encounter(encounter_id, spec)

    def _local_encounter(self, encounter_id: str, spec: dict) -> Encounter:
        """本地兜底组建（state.build_encounter 不可用/失败时使用）。

        ``enemies: [[npc_key, 数量], ...]`` 经 ``module.npc_table``
        （``{**D.NPCS, **custom_npcs}``）解析为 Character 数据块。"""
        enc = Encounter(title=spec.get("title", encounter_id),
                        intro=spec.get("intro", ""), entry=dict(spec),
                        rng=self.state.rng)
        for c in self.state.party():
            if c.hp_cur > 0 and not c.is_dead:
                enc.allies.append(c)
        known = dict(getattr(self.module, "npc_table", None)
                     or {**D.NPCS, **(getattr(self.module, "custom_npcs", None)
                                     or getattr(self.module, "custom_monsters", {})
                                     or {})})
        for entry in (spec.get("enemies") or spec.get("monsters") or []):
            if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
                continue
            key, count = str(entry[0]), int(entry[1])
            block = known.get(key)
            if block is None:
                block = {"name": key}
            for i in range(max(1, count)):
                tag = f"（{i + 1}）" if count > 1 else ""
                npc = Character.from_statblock(key, block, tag=tag)
                enc.enemies.append(npc)
        if self.state.map:
            grid = self.state.map.get("grid") or [16, 12]
            enc.grid = BattleGrid(int(grid[0]), int(grid[1]),
                                  self.state.map.get("obstacles") or [])
        self._attach_entry(enc, spec)
        return enc

    def _attach_entry(self, enc: Encounter, spec: dict) -> None:
        """把遭遇声明挂在 Encounter 上（布点用），并补齐 rng。"""
        base = dict(spec)
        tokens = list(base.get("_tokens") or [])
        if self.state.map:
            for tid, tok in (self.state.map.get("tokens") or {}).items():
                cell = tok.get("cell")
                if cell and str(tok.get("kind")) not in ("pc", "monster"):
                    tokens.append({"name": tok.get("name"), "cell": cell})
        if tokens:
            base["_tokens"] = tokens
        enc.entry = base
        if getattr(enc, "rng", None) is None:
            enc.rng = self.state.rng
        if enc.grid is not None and not enc.positions:
            enc._deploy()

    def _apply_surprise(self, enc: Encounter) -> None:
        """措手不及：敌人首轮不能行动（先攻顺位沉底 + 全队行动/移动已用尽）。"""
        roster = [c for c in enc.combatants if c in enc.enemies]
        others = [c for c in enc.order if c not in roster]
        enc.order = others + roster
        for c in roster:
            c.action_used = True
            c.bonus_used = True
            enc.speed_left[c.name] = 0

    def run_combat(self) -> str:
        enc = self.encounter
        if enc is None:
            return "win"
        self._fled = []
        enc.begin()
        if hasattr(self.ui, "mode"):
            try:
                self.ui.mode = "combat"
            except Exception:  # noqa: BLE001
                pass
        self.ui.raw("\n—— 战斗开始 ——")
        self.ui.status_board(enc.status_lines())
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
            actor = enc.next_turn()
            if actor is None:
                outcome = enc.is_over() or "retreat"
                break
            if not enc.alive(actor):
                self._sync_map_from_encounter(enc)   # 击倒者棋子即时撤下
                continue
            if actor is self.state.player:
                self.player_combat_turn(enc)
            elif actor.kind in ("companion",) or actor in self.state.companions:
                self.companion_turn(enc, actor)
            else:
                self.monster_turn(enc, actor)
            self._sync_map_from_encounter(enc)       # 走位/击倒实时上棋盘
            outcome = enc.is_over()
            if outcome is None and self._fled and not enc.alive("party"):
                outcome = "retreat"
        self.ui.raw("—— 战斗结束 ——")
        if hasattr(self.ui, "mode"):
            try:
                self.ui.mode = "explore"
            except Exception:  # noqa: BLE001
                pass
        self._end_combat_map(enc)
        # 战后收尾：先救濒死者（战斗外时间充裕，盟友反复尝试稳定），
        # 无人施救则本人继续掷生存检定（低于门槛 10 + 累积即活，掷出 10 即死）
        for d in [c for c in self.state.party() if c.hp_cur < 1 and not c.is_dead]:
            self._stabilize_aftermath(d)
        # 战斗结束清理：临时战斗状态消失，重伤/濒死/被稳定/致命伤保留
        for c in self.state.party():
            c.conditions -= _POST_COMBAT_CLEAR
            if c.is_dead:
                c.conditions = {COND_DEAD}       # 规范死亡状态显示
        # 玩家死亡 → 本局结束（RED 无复活义体可用）
        if self.state.player.is_dead:
            self.encounter = None
            self.tools.encounter = None
            return "tpk"
        self.encounter = None
        self.tools.encounter = None
        # 结算口径以「收尾之后」的实际状态为准：战斗中 enemy 侧先倒下全场时
        # enc.is_over() 会先报 "tpk"（队伍此刻全部倒地），但战后的稳定救援
        # 可能把人拉回 1 PW——那时队伍并没有全灭，不能误判 TPK。
        if outcome == "tpk":
            if not enc.alive("enemy"):
                outcome = "party"
            elif enc.alive("party"):
                self.ui.system_line("队伍拖着伤员退出了战场。")
                outcome = "retreat"
            else:
                return "tpk"
        if outcome == "party":
            self._aftermath(enc)
        return outcome if outcome in ("retreat",) else "win"

    def _stabilize_aftermath(self, dying: Character) -> None:
        """战斗外的稳定尝试：盟友反复检定；无人施救则本人掷生存检定。

        生存检定门槛每掷一次 +1（RED 口径），所以无人施救的濒死者不能无限掷下去。
        这里最多掷 :data:`_DEATH_SAVE_AFTERMATH_LIMIT` 次，之后视作「撑到救援赶来」
        而被稳定——这是战斗收尾的终止性保证，也避免收尾阶段抽成必死。"""
        if dying.hp_cur >= 1 or dying.is_dead:
            return
        helpers = [h for h in self.state.party()
                   if h is not dying and h.hp_cur > 0 and not h.is_dead]
        if helpers:
            helper = max(helpers, key=lambda h: max(h.skill("paramedic"),
                                                    h.skill("first_aid")))
            skill = "paramedic" if helper.skill("paramedic") >= helper.skill("first_aid") \
                else "first_aid"
            dv = dying.stabilize_dv()
            for _ in range(5):
                r = helper.check(self.state.rng, skill, dv)
                if r.success:
                    dying.stabilize()
                    self.ui.system_line(
                        f"{helper.name} 用{helper.skill_name(skill)}稳定了 {dying.name}"
                        f"（{r.total} vs DV {dv}）：PW 回到 1，生存检定重新开始。")
                    return
            dying.stabilize()
            self.ui.system_line(
                f"{helper.name} 反复按压止血，终于稳住了 {dying.name} 的伤势"
                f"（PW 回到 1）。")
            return
        # 无人施救：本人继续掷生存检定（每次门槛 +1）
        for _ in range(_DEATH_SAVE_AFTERMATH_LIMIT):
            res = dying.death_save(self.state.rng)
            roll = res.get("roll")
            if roll is not None:
                faces = list(getattr(roll, "rolls", []) or [])
                self.ui.roll_event({
                    "kind": "save", "who": dying.name, "label": "生存检定",
                    "rolls": faces, "d10": faces[0] if faces else None,
                    "total": getattr(roll, "total", res.get("face")),
                    "dv": res.get("dv"), "success": bool(res.get("ok")),
                    "crit_success": False, "crit_failure": False,
                    "mods": list(getattr(roll, "breakdown", []) or []),
                })
            if res.get("dead"):
                self.ui.system_line(
                    f"☠ {dying.name} 的生存检定失败（1d10={res.get('face')} "
                    f"vs {res.get('dv')}）——死亡。")
                return
            self.ui.system_line(
                f"✚ {dying.name} 的生存检定通过（1d10={res.get('face')} "
                f"< {res.get('dv')}），还吊着一口气。")
            if dying.hp_cur >= 1:
                return
        if not dying.is_dead:
            dying.stabilize()
            self.ui.system_line(
                f"{dying.name} 在血泊里撑到了救援赶来（PW 回到 1）。")

    # ================================================================ 战术地图
    def _sync_map_from_encounter(self, enc: Encounter) -> None:
        """战斗中：把 Encounter 的位置结算写回地图棋子并广播。"""
        if not self.state.map or getattr(enc, "grid", None) is None:
            return
        tokens = self.state.map["tokens"]
        icons = getattr(self.module, "token_icons", {}) or {}
        seen: set[str] = set()
        for c in enc.combatants:
            cell = enc.positions.get(c.name)
            if cell is None:
                continue
            if c.kind == "npc":
                tid = f"npc:{c.name}"
                seen.add(tid)
                if c.hp_cur < 1 or c.is_dead:
                    tokens.pop(tid, None)
                    continue
                icon = icons.get(c.name) or icons.get(str(c.name).split(" ")[0])
                if tid in tokens:
                    tokens[tid]["cell"] = [cell[0], cell[1]]
                else:
                    tokens[tid] = {"name": c.name, "kind": "npc",
                                   "cell": [cell[0], cell[1]], "icon": icon}
            else:
                tid = f"pc:{c.name}"
                tok = tokens.get(tid)
                if tok is not None:
                    tok["cell"] = [cell[0], cell[1]]
                else:
                    tokens[tid] = {"name": c.name, "kind": "pc",
                                   "cell": [cell[0], cell[1]], "icon": None}
                seen.add(tid)
        for tid in [k for k in tokens if k.startswith("npc:") and k not in seen]:
            del tokens[tid]
        self.broadcast_map()

    def _end_combat_map(self, enc: Encounter) -> None:
        """战斗收尾：最终位置写回 + 敌人棋子全部撤下。"""
        if not self.state.map:
            return
        self._sync_map_from_encounter(enc)
        for tid in [k for k in self.state.map["tokens"] if k.startswith("npc:")]:
            del self.state.map["tokens"][tid]
        self.broadcast_map()

    def battle_map_text(self, enc: Encounter, viewer=None) -> str:
        """ASCII 战况图（LLM 参战者的位置感知；剧场模式返回空）。"""
        if getattr(enc, "grid", None) is None:
            return ""
        g = enc.grid
        occ = {}
        for c in enc.combatants:
            cell = enc.positions.get(c.name)
            if cell is not None and enc.alive(c):
                occ[cell] = c
        rows = ["   " + "".join(str(x % 10) for x in range(g.w))]
        for y in range(g.h):
            line = ""
            for x in range(g.w):
                c = occ.get((x, y))
                if c is not None:
                    if viewer is not None and c is viewer:
                        line += "你"
                    else:
                        line += "P" if c in enc.allies else "m"
                elif (x, y) in g.obstacles:
                    line += "#"
                else:
                    line += "."
            rows.append(f"{y:>2} {line}")
        return ("（x→右，y→下，1格=2米；#=障碍，P=队伍，m=敌人，你=自己）\n"
                + "\n".join(rows))

    # ---------------------------------------------------------------- 走位启发
    def _ai_move(self, enc: Encounter, actor, target, ranged: bool) -> None:
        """启发式走位：近战逼近；远程被贴身时后撤。"""
        if getattr(enc, "grid", None) is None or target is None:
            return
        mine = enc.positions.get(actor.name)
        if mine is None:
            return
        if ranged:
            tp = enc.positions.get(target.name)
            if tp is not None and enc.grid.dist(mine, tp) <= 1:
                self._retreat_move(enc, actor)
            return
        if _melee_reach_cells(enc) and enc.grid.dist(
                mine, enc.positions.get(target.name) or mine) > _melee_reach_cells(enc):
            enc.approach(actor, target)

    def _retreat_move(self, enc: Encounter, actor) -> None:
        """后撤：在移速可达格中选与所有敌人最小距离最大者（引发借机攻击照 RED）。"""
        grid = getattr(enc, "grid", None)
        if grid is None:
            return
        start = enc.positions.get(actor.name)
        budget = int(enc.speed_left.get(actor.name, 0)) // METERS_PER_CELL
        if start is None or budget <= 0:
            return
        occupied = {enc.positions[o.name] for o in enc.combatants
                    if o is not actor and enc.alive(o) and enc.positions.get(o.name)}
        foes = [enc.positions[f.name] for f in enc.alive("enemy")
                if enc.positions.get(f.name)]
        if not foes:
            return
        here = min(max(abs(start[0] - fx), abs(start[1] - fy)) for fx, fy in foes)
        best, best_score = None, here
        for x in range(grid.w):
            for y in range(grid.h):
                cell = (x, y)
                if max(abs(x - start[0]), abs(y - start[1])) > budget:
                    continue
                if grid.blocked(cell) or cell in occupied:
                    continue
                score = min(max(abs(x - fx), abs(y - fy)) for fx, fy in foes)
                if score > best_score:
                    best, best_score = cell, score
        if best is not None:
            enc.move(actor, best)

    def _ensure_in_reach(self, enc: Encounter, attacker, target,
                         weapon_key: str | None = None) -> None:
        """网格模式：白刃战/斗殴攻击前自动逼近。"""
        if getattr(enc, "grid", None) is None or target is None:
            return
        if _is_ranged(weapon_key):
            return                      # 远程：射程闸门自行拒绝/放行
        a, t = enc.positions.get(attacker.name), enc.positions.get(target.name)
        if a and t and enc.grid.dist(a, t) > _melee_reach_cells(enc):
            enc.approach(attacker, target)

    def monster_turn(self, enc: Encounter, mon) -> None:
        act = enc.npc_choose_action(mon)
        action = act.get("action")
        if action == "wait" or action == "flee" and act.get("hard"):
            return
        mark = (len(enc.log_lines), len(enc.rolls))
        if action == "escape_grapple":
            target = enc.find(act.get("target") or "")
            if target is not None:
                enc.escape_grab(mon, target)
            self._flush_enc(enc, mark)
            return
        if action == "flee":
            self._flee_actor(enc, mon, quiet=True)
            self._flush_enc(enc, mark)
            return
        target = enc.find(act.get("target") or "")
        if target is None:
            return
        weapon = act.get("weapon")
        if getattr(enc, "grid", None) is not None:
            self._ai_move(enc, mon, target, ranged=_is_ranged(weapon))
        if act.get("approach"):
            enc.approach(mon, target)
        if enc.has_action(mon):
            enc.attack_action(mon, target, weapon, luck=0)
            if enc.has_bonus(mon) and self._second_shot(mon, weapon):
                enc.use_bonus(mon)
                res = enc.attack(mon, target, weapon)
                _ = res
        self._flush_enc(enc, mark)

    def _second_shot(self, actor: Character, weapon_key: str | None) -> bool:
        """是否该打武器第二发（LA/ROF ≥ 2 且目标尚在）。"""
        info = D.WEAPONS.get(str(weapon_key or "")) or {}
        return int(info.get("rof", 1)) >= 2 and actor.ammo.get(str(weapon_key), 1) != 0

    def _flee_actor(self, enc: Encounter, actor: Character, quiet: bool = False) -> None:
        """撤退/脱离战斗：把成员移出战场并标记（队伍全撤 = 撤退收场）。"""
        if actor in enc.allies:
            enc.allies.remove(actor)
            if actor not in self._fled:
                self._fled.append(actor)
        if actor in enc.order:
            enc.order.remove(actor)
            enc.idx = max(0, min(enc.idx, len(enc.order) - 1))
        if not quiet:
            self.ui.system_line(f"  {actor.name} 脱离战斗，退入街巷。")

    # ---------------------------------------------------------------- 玩家回合
    def _flush_enc(self, enc: Encounter, mark: tuple[int, int]) -> None:
        """把自 mark 以来累积的骰子事件与战斗日志按序推给 UI。"""
        b_log, b_rolls = mark
        for ev in enc.rolls[b_rolls:]:
            self.ui.roll_event(ev)
        for line in enc.log_lines[b_log:]:
            self.ui.raw("  " + line)

    def player_combat_turn(self, enc: Encounter) -> None:
        # AI PC：走同伴回合链（pc_agents 命中 → _pc_agent_turn → combat_action
        # 工具；LLM 故障落 _scripted_pc_turn 启发式）
        if self.aipc is not None:
            self.aipc.combat_turn(enc)
            return
        pc = self.state.player
        if not enc.alive(pc):
            return
        stall = 0
        move_refused = 0
        ended = False
        while enc.has_action(pc) and enc.is_over() is None and not ended:
            foes = enc.alive("enemy")
            if not foes:
                return
            self.ui.status_board(enc.status_lines())
            opts = self._turn_options(enc, pc, foes)
            if move_refused >= 2:
                opts = [o for o in opts if not o.startswith("移动/靠近")]
            idx = self.ui.choose(
                f"—— {pc.name} 的回合 · 移动 {enc.speed_left.get(pc.name, 0)} 米 / "
                f"其他动作 1 次 ——", opts)
            choice = opts[idx]
            if choice.startswith("（结束回合）"):
                enc.use_action(pc)
                break
            if choice.startswith("移动/靠近") and not self._player_move(enc, pc, foes):
                move_refused += 1
                stall += 1
                if stall >= 6:
                    self.ui.system_line("（连续多次无效行动，回合收束。）")
                    enc.use_action(pc)
                    break
                continue
            if choice.startswith("移动/靠近"):
                continue
            if not self._execute_turn_action(enc, pc, choice, foes):
                if choice not in ("说话", "查看状态"):
                    stall += 1
                    if stall >= 6:
                        self.ui.system_line("（连续多次无效行动，回合收束。）")
                        enc.use_action(pc)
                        break
                continue
            stall = 0
        # 「附赠」槽 = 武器的第二发（LA/ROF ≥ 2 时可选的一次追加单发）
        if enc.is_over() is None and enc.has_bonus(pc) and enc.alive(pc):
            shot = self._second_shot_options(pc, enc.alive("enemy"))
            if shot:
                self.ui.status_board(enc.status_lines())
                idx = self.ui.choose(f"—— {pc.name} · 武器的第二发 ——",
                                     shot + ["（不必了）"])
                if idx < len(shot):
                    self._resolve_choice(enc, pc, self._choice_for_second_shot(shot[idx]),
                                         enc.alive("enemy"))
                enc.use_bonus(pc)          # 无论打不打，第二发机会都用掉

    def _choice_for_second_shot(self, label: str) -> str:
        """把「第二发：武器 → 目标」标签还原成回合动作标签。"""
        body = label.split("：", 1)[-1]
        return "攻击：" + body.split(" → ", 1)[0] + " → " + \
            body.split(" → ", 1)[-1].split("（")[0]

    def _second_shot_options(self, pc: Character, foes: list) -> list[str]:
        out = []
        for w in pc.weapons:
            info = D.WEAPONS.get(w) or {}
            if int(info.get("rof", 1)) < 2:
                continue
            if pc.ammo.get(w, 1) == 0:
                continue
            for f in foes:
                out.append(f"第二发：{info.get('name', w)} → {f.name}"
                           f"（PW {f.hp_cur}/{f.hp_max}）")
        return out

    def _turn_options(self, enc: Encounter, pc: Character, foes: list) -> list[str]:
        """RED 回合菜单：一次「其他动作」+ 一次「移动动作」（顺序即优先级）。

        攻击类排在前面：AI PC / 自动演示 UI / 剧本模式都按「先打后走」取默认项。
        重伤或濒死时「撤退」升到最前（保命优先；也保证自动演示不会站着被打死）。"""
        opts: list[str] = []
        desperate = pc.hp_cur < 1 or (
            COND_STABILIZED not in pc.conditions
            and pc.hp_cur <= pc.seriously_wounded_threshold)
        if desperate:
            opts.append("撤退（脱离战斗）")
        # 其他动作
        if pc.weapons:
            opts.append("攻击")
            if any(_is_ranged(w) for w in pc.weapons):
                opts.append("瞄准射击（-8 命中，穿甲部位）")
        opts.append("斗殴（两次攻击；可擒抱/锁喉/投掷）")
        if COND_CONTROLLED in pc.conditions:
            opts.append("挣脱（对抗擒抱）")
        if any(D.WEAPONS.get(w, {}).get("ammo") for w in pc.weapons):
            opts.append("装填")
        # 支援类
        wounded = [a for a in self._all_party_in(enc)
                   if a.hp_cur < 1 and not a.is_dead]
        if wounded:
            opts.append("稳定（把濒死者拉回 1 PW）")
        critted = [a for a in self._all_party_in(enc) if a.fixable_crit_indices()]
        if critted:
            opts.append("急救固定（处理一处致命伤）")
        opts.append("使用物品")
        opts.append("对峙（魅力 + 声望）")
        # 移动类
        if COND_PRONE in pc.conditions:
            opts.append("起身（消耗移动力的一半）")
        if enc.speed_left.get(pc.name, 0) > 0 and COND_PRONE not in pc.conditions:
            opts += ["移动/靠近", "奔跑（消耗动作，追加一次移动）"]
        if enc.grid is not None:
            opts.append("延迟（把先攻顺位挪后）")
        if not desperate:
            opts.append("撤退（脱离战斗）")
        opts += ["说话", "查看状态", "（结束回合）"]
        return opts

    def _all_party_in(self, enc: Encounter) -> list:
        """参战队伍成员（含已被打倒在地、仍留在战场的）。"""
        return [c for c in enc.allies]

    def _execute_turn_action(self, enc: Encounter, pc: Character, choice: str,
                             foes: list) -> bool:
        if choice == "攻击":
            return self._player_attack(enc, pc, foes, aimed=False)
        if choice.startswith("瞄准射击"):
            return self._player_attack(enc, pc, foes, aimed=True)
        if choice.startswith("斗殴"):
            return self._player_brawl(enc, pc, foes)
        if choice.startswith("挣脱"):
            return self._resolve_choice(enc, pc, "挣脱", foes)
        if choice.startswith("装填"):
            return self._resolve_choice(enc, pc, "装填", foes)
        if choice.startswith("稳定"):
            return self._resolve_choice(enc, pc, "稳定", foes)
        if choice.startswith("急救固定"):
            return self._resolve_choice(enc, pc, "急救固定", foes)
        if choice.startswith("使用物品"):
            return self._resolve_choice(enc, pc, "使用物品", foes)
        if choice.startswith("移动/靠近"):
            return self._player_move(enc, pc, foes)
        if choice.startswith("奔跑"):
            return self._resolve_choice(enc, pc, "奔跑", foes)
        if choice.startswith("起身"):
            return self._resolve_choice(enc, pc, "起身", foes)
        if choice.startswith("延迟"):
            return self._resolve_choice(enc, pc, "延迟", foes)
        if choice.startswith("对峙"):
            return self._resolve_choice(enc, pc, "对峙", foes)
        if choice.startswith("撤退"):
            return self._resolve_choice(enc, pc, "撤退", foes)
        if choice == "说话":
            text = self.ui.free_input("（说什么？）")
            if text:
                self.ui.companion_line(pc.name, text)
            return False          # 免费，不耗动作
        if choice.startswith("查看状态"):
            self.ui.raw(self.state.party_snapshot())
            return False
        return False

    # ---------------------------------------------------------------- 动作结算
    def _resolve_choice(self, enc: Encounter, pc: Character, kind: str,
                        foes: list) -> bool:
        """回合菜单的单一动作分派（返回 False = 未消耗动作，可重选）。"""
        mark = (len(enc.log_lines), len(enc.rolls))
        allies = self._all_party_in(enc)
        r: dict = {"ok": True}
        if kind == "挣脱":
            target = self._controlled_by(enc, pc)
            if target is None:
                self.ui.system_line("并没有被谁控制住。")
                return False
            r = enc.escape_grab(pc, target)
        elif kind == "装填":
            keys = [w for w in pc.weapons if (D.WEAPONS.get(w) or {}).get("ammo")]
            if not keys:
                self.ui.system_line("身上没有需要装填的武器。")
                return False
            labels = [f"{D.WEAPONS[w]['name']}（弹匣 {pc.ammo.get(w, D.WEAPONS[w]['ammo'])}"
                      f"/{D.WEAPONS[w]['ammo']}）" for w in keys]
            wi = self.ui.choose("给哪把武器装填？", labels)
            r = enc.reload(pc, keys[wi])
        elif kind == "稳定":
            hurt = [a for a in allies if a.hp_cur < 1 and not a.is_dead]
            if not hurt:
                self.ui.system_line("没有需要稳定的人。")
                return False
            ti = self._choose_target("稳定谁？", hurt)
            if ti is None:
                return False
            r = enc.stabilize_action(pc, hurt[ti])
        elif kind == "急救固定":
            hurt = [a for a in allies if a.crit_injuries]
            if not hurt:
                self.ui.system_line("队里没有人带着致命伤。")
                return False
            ti = self._choose_target("处理谁的致命伤？", hurt)
            if ti is None:
                return False
            target = hurt[ti]
            inj = self._choose_injury(target)
            if inj is None:
                return False
            if not self._spend_action(enc, pc):
                return False
            r = enc.first_aid_fix(pc, target, inj)
        elif kind == "使用物品":
            items = [k for k, n in (pc.gear or {}).items() if n > 0]
            if not items:
                self.ui.system_line("背包里没有可用物品。")
                return False
            labels = [f"{D.GEAR.get(k, {}).get('name', k)} ×{pc.gear[k]}" for k in items]
            ii = self.ui.choose("使用什么？", labels)
            r = enc.use_item(pc, items[ii])
        elif kind == "奔跑":
            r = enc.dash(pc)
        elif kind == "起身":
            r = enc.stand(pc)
        elif kind == "延迟":
            before = None
            roster = [c for c in enc.order if c is not pc and enc.alive(c)]
            if roster:
                labels = [f"{c.name} 之前" for c in roster] + ["（本轮最后）"]
                bi = self.ui.choose("推迟到谁之前行动？", labels)
                before = roster[bi].name if bi < len(roster) else None
            r = enc.delay(pc, before)
            self._flush_enc(enc, mark)
            if r.get("ok"):
                enc.use_action(pc)
                self.ui.system_line(f"  {pc.name} 按兵不动，等待时机。")
            self._sync_map_from_encounter(enc)
            return True
        elif kind == "对峙":
            if not foes:
                return False
            ti = self._choose_target("与谁对峙？", foes)
            if ti is None:
                return False
            r = enc.confrontation(pc, foes[ti])
        elif kind == "撤退":
            r = {"ok": True}
            self._flee_actor(enc, pc)
            enc.use_action(pc)
        else:
            return False
        self._flush_enc(enc, mark)
        if not r.get("ok", True):
            self.ui.system_line(_reply_err(r))
            return False
        self._sync_map_from_encounter(enc)
        return True

    def _spend_action(self, enc: Encounter, pc: Character) -> bool:
        if not enc.has_action(pc):
            self.ui.system_line("本回合的其他动作已经用掉了。")
            return False
        enc.use_action(pc)
        return True

    def _controlled_by(self, enc: Encounter, pc: Character):
        for name in enc.engagement.get(pc.name, set()):
            other = enc.find(name)
            if other is not None and COND_CONTROLLED in other.conditions:
                return other
        others = [c for c in enc.combatants
                  if c is not pc and COND_CONTROLLED in c.conditions]
        return others[0] if others else None

    def _choose_target(self, prompt: str, pool: list) -> int | None:
        labels = [f"{a.name}（PW {a.hp_cur}/{a.hp_max}，{_wound_of(a)}"
                  f"{('，致命伤×' + str(len(a.crit_injuries))) if a.crit_injuries else ''}）"
                  for a in pool]
        i = self.ui.choose(prompt, labels)
        return i if 0 <= i < len(pool) else None

    def _choose_injury(self, target: Character) -> int | None:
        if not target.crit_injuries:
            return None
        labels = [f"{inj['name']}（{inj['text'][:24]}……）"
                  f"{'（已固定）' if inj.get('stabilized') else ''}"
                  for inj in target.crit_injuries]
        i = self.ui.choose(f"{target.name} 的致命伤", labels)
        return i if 0 <= i < len(target.crit_injuries) else None

    def _player_move(self, enc: Encounter, pc: Character, foes: list | None = None) -> bool:
        """移动动作：网格点图落子，剧场模式就近逼近。

        返回 True = 已移动（调用方消耗这个「移动动作」，本回合不能再移动）；
        False = 未移动（回菜单，不耗动作）。"""
        mark = (len(enc.log_lines), len(enc.rolls))
        if enc.grid is None:
            foes = list(foes or enc.alive("enemy"))
            if not foes:
                return False
            ti = self._choose_target("逼近谁？", foes)
            if ti is None:
                return False
            r = enc.approach(pc, foes[ti])
            self._flush_enc(enc, mark)
            if not r.get("ok", True):
                self.ui.system_line(_reply_err(r))
                return False
            self._sync_map_from_encounter(enc)
            enc.use_action(pc)
            return True
        prompt = (f"移动：{pc.name} 剩余移动力 {enc.speed_left.get(pc.name, 0)} 米"
                  "（1 格 = 2 米）。点击地图落子，或输入 x,y（留空退回菜单）")
        if hasattr(self.ui, "ask_map_move"):
            raw = self.ui.ask_map_move(prompt)
        else:
            raw = self.ui.free_input("移动到（x,y，留空退回菜单）> ")
        raw = str(raw or "").strip().replace("，", ",").replace(" ", ",")
        if not raw or raw.lower() in ("cancel", "q", "取消"):
            return False
        parts = [p for p in raw.split(",") if p != ""]
        if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
            self.ui.system_line("坐标格式应为 x,y（如 5,7）。")
            return False
        dest = (int(parts[0]), int(parts[1]))
        before = enc.positions.get(pc.name)
        r = enc.move(pc, dest)
        self._flush_enc(enc, mark)
        moved = bool(r.get("ok")) and enc.positions.get(pc.name) != before
        if moved:
            # 引擎的 move() 只花移动力；网格模式下抽签落子就是本回合的移动动作
            enc.use_action(pc)
        elif not r.get("ok", True):
            self.ui.system_line(_reply_err(r))
            return False
        self._sync_map_from_encounter(enc)
        return moved

    def _player_attack(self, enc: Encounter, pc: Character, foes: list,
                       aimed: bool = False) -> bool:
        if not pc.weapons:
            self.ui.system_line("你身上没有武器。")
            return False
        wi = self.ui.choose("选择武器", [self._weapon_label(pc, w) for w in pc.weapons])
        weapon = pc.weapons[wi]
        ti = self._choose_target("选择目标", self._order_foes(enc, pc, foes))
        if ti is None:
            return False
        target = self._order_foes(enc, pc, foes)[ti]
        self._ensure_in_reach(enc, pc, target, weapon)
        mark = (len(enc.log_lines), len(enc.rolls))
        r = enc.attack_action(pc, target, weapon, aimed=aimed)
        self._flush_enc(enc, mark)
        if not r.get("ok", True):
            self.ui.system_line(_reply_err(r))
            return False
        self._sync_map_from_encounter(enc)
        return True

    def _player_brawl(self, enc: Encounter, pc: Character, foes: list) -> bool:
        labels = ["斗殴（两次徒手攻击）", "擒抱（控制对方）", "锁喉（须先控制对方）",
                  "投掷（须先控制对方，倒地）", "（返回）"]
        i = self.ui.choose("斗殴：怎么做？", labels)
        if i >= 4:
            return False
        marked = (len(enc.log_lines), len(enc.rolls))
        if i == 0:
            r = enc.attack_action(pc, self._pick_foe(enc, pc, foes), "brawling")
        elif i == 1:
            r = enc.grab(pc, self._pick_foe(enc, pc, foes))
        elif i == 2:
            target = self._pick_foe(enc, pc, foes)
            if target is None:
                return False
            r = enc.choke(pc, target)
        else:
            target = self._pick_foe(enc, pc, foes)
            if target is None:
                return False
            r = enc.throw_target(pc, target)
        self._flush_enc(enc, marked)
        if not r.get("ok", True):
            self.ui.system_line(_reply_err(r))
            return False
        self._sync_map_from_encounter(enc)
        return True

    def _pick_foe(self, enc: Encounter, pc: Character, foes: list):
        if not foes:
            return None
        ordered = self._order_foes(enc, pc, foes)
        ti = self._choose_target("选择目标", ordered)
        return ordered[ti] if ti is not None else None

    def _order_foes(self, enc: Encounter, pc: Character, foes: list) -> list:
        """网格模式按距离排序（远程射程闸门的现实约束）。"""
        if enc.grid is None:
            return sorted(foes, key=lambda f: f.hp_cur)
        mine = enc.positions.get(pc.name)
        if mine is None:
            return foes
        return sorted(foes, key=lambda f: (enc.grid.dist(
            mine, enc.positions.get(f.name) or mine), f.hp_cur))

    # ---------------------------------------------------------------- 同伴/敌人
    def companion_turn(self, enc: Encounter, pc: Character) -> None:
        if not enc.alive(pc):
            return
        acted = False
        mark = (len(enc.log_lines), len(enc.rolls))
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
        foes = "；".join(f"{f.name} PW{f.hp_cur}/{f.hp_max} SP{f.armor_at('torso')}"
                         for f in enc.alive("enemy"))
        allies = "；".join(f"{a.name} PW{a.hp_cur}/{a.hp_max}（{_wound_of(a)}）"
                           for a in enc.alive("party"))
        grid_note = ""
        if enc.grid is not None:
            mine = enc.positions.get(pc.name) or (0, 0)
            left = enc.speed_left.get(pc.name, 0)
            grid_note = (f"\n【战场网格】\n{self.battle_map_text(enc, viewer=pc)}\n"
                         f"你 @ ({mine[0]},{mine[1]})，剩余移动力 {left} 米（每格 2 米）。"
                         "距离不够就先 move(x,y) 再 attack。")
        task = (f"【第{enc.round}轮 · {pc.name} 的回合】\n敌人：{foes or '无'}\n"
                f"队伍：{allies or '无'}\n"
                f"你 PW {pc.hp_cur}/{pc.hp_max}（{_wound_of(pc)}），"
                f"幸运 {pc.luck_cur}/{pc.stat('luck')}，"
                f"护甲 {pc.armor_name()} SP {pc.armor_at('torso')}，"
                f"武器：{'、'.join(self._weapon_label(pc, w) for w in pc.weapons) or '徒手'}。"
                f"{grid_note}"
                f"想喊话就先 say 一句简短的战吼/提醒/嘲讽（可选），"
                f"再用 combat_action 执行你的回合（一个移动动作 + 一个其他动作）。")
        try:
            result: AgentResult = self.loop.run(agent, session, task, registry=reg)
            self._record_usage(agent, result)
        except RuntimeError as e:  # LLM 不可达 → 启发式兜底
            self.ui.system_line(f"（Agent 调用失败，{pc.name} 依靠战斗直觉行动：{e}）")
            return False
        return any(t["name"] == "combat_action" for t in result.tool_trace)

    def _scripted_pc_turn(self, enc: Encounter, pc: Character) -> None:
        """启发式队友 AI（剧本模式 / LLM 兜底）：RED 动作经济。"""
        if not enc.alive(pc):
            return
        foes = enc.alive("enemy")
        if not foes:
            return
        # 0) 保命撤退（防团灭的保险，不是逃跑倾向）：
        #    · PW 掉到上限四分之一以下 → 这一仗打不下去了，先脱离；
        #    · 或者自己已重伤、有人倒下、且站着的人不比敌人多。
        aware = [a for a in enc.alive("party") if a.hp_cur > 0]
        downed = [a for a in self._all_party_in(enc)
                  if a.hp_cur < 1 and not a.is_dead]
        nearly_dead = pc.hp_cur <= max(1, pc.hp_max // 4)
        outmatched = bool(downed) and aware and len(aware) <= len(foes)
        if enc.has_action(pc) and (nearly_dead
                                  or (pc.hp_cur <= pc.seriously_wounded_threshold
                                      and outmatched)):
            self._flee_actor(enc, pc)
            enc.use_action(pc)
            self.ui.system_line(f"  {pc.name} 见势不妙，拖着伤退出了战场。")
            return
        if enc.grid is not None:
            def _d(f):
                mine = enc.positions.get(pc.name) or (0, 0)
                cell = enc.positions.get(f.name)
                return enc.grid.dist(mine, cell) if cell else 999
            foes = sorted(foes, key=lambda f: (_d(f), f.hp_cur))
        target = foes[0]
        party = self._all_party_in(enc)
        # 1) 拉起濒死者：稳定检定（技术 + 急救/医疗救援）
        dying = [a for a in party if a.hp_cur < 1 and not a.is_dead]
        if dying and enc.has_action(pc):
            enc.stabilize_action(pc, dying[0])
            return
        # 2) 自己/队友的致命伤：急救固定（每次一个动作，只挑「还能固定」的伤）
        if enc.has_action(pc):
            cands = [(a, a.fixable_crit_indices())
                     for a in party if a.fixable_crit_indices()]
            if cands and pc.hp_cur > pc.seriously_wounded_threshold:
                a, ix = cands[0]
                enc.use_action(pc)
                enc.first_aid_fix(pc, a, ix[0])
                return
        # 3) 选武器：《赛博朋克 RED》没有「远程被贴身就打不了」这条规则
        #    （那是 5e 的劣势概念）。所以有枪就用枪，只有实在没有远程武器才用近战/徒手；
        #    否则枪手贴脸就退化成徒手 1d6，被 SP 7 全挡，永远打不赢。
        weapon = pc.best_weapon("ranged") or pc.best_weapon() or "brawling"
        info = D.WEAPONS.get(weapon) or {}
        # 集火：优先补刀已经重伤的敌人（比永远打最近的更有效）
        weak = [f for f in foes if f.hp_cur <= f.seriously_wounded_threshold]
        if weak:
            target = min(weak, key=lambda f: f.hp_cur)
        if info.get("kind") in _MELEE_KINDS:
            self._ensure_in_reach(enc, pc, target, weapon)
        elif enc.grid is not None:
            self._ai_move(enc, pc, target, ranged=True)
        # 4) 没弹药了先装填
        if info.get("ammo") and pc.ammo.get(weapon, info["ammo"]) <= 0 \
                and enc.has_action(pc):
            enc.reload(pc, weapon)
            return
        if enc.has_action(pc):
            enc.attack_action(pc, target, weapon, luck=self._scripted_luck(pc))
            if enc.has_bonus(pc) and int(info.get("rof", 1)) >= 2 and enc.alive(target):
                enc.use_bonus(pc)
                enc.attack(pc, target, weapon)
        # 5) 移动：追不上就靠近；远程被贴身则后撤
        if enc.speed_left.get(pc.name, 0) > 0 and enc.alive(target):
            if info.get("kind") in _MELEE_KINDS:
                if enc.grid is None or (
                        enc.positions.get(pc.name) and enc.positions.get(target.name)
                        and enc.grid.dist(enc.positions[pc.name],
                                          enc.positions[target.name]) > 1):
                    enc.approach(pc, target)
            elif enc.grid is not None:
                self._ai_move(enc, pc, target, ranged=True)

    def _scripted_luck(self, pc: Character) -> int:
        """启发式幸运投入：重伤时把剩下的幸运压上去（每轮幸运池会回满）。"""
        if pc.hp_cur <= pc.seriously_wounded_threshold and pc.luck_cur > 0:
            return min(pc.luck_cur, 3)
        return 0

    # ---------------------------------------------------------------- 收尾
    def _aftermath(self, enc: Encounter) -> None:
        ip = 0
        try:
            ip = int(enc.defeated_ip())
        except Exception:  # noqa: BLE001 —— 战利品结算不阻塞对局
            ip = 0
        if ip > 0:
            # defeated_ip() 是这场战斗的**总量**，按人头平摊后发放
            # （场景的 ip 字段才是「每人多少」的口径）。
            share = max(1, ip // max(1, len(self.state.party())))
            self.award_ip(share, f"{enc.title} 的战斗")
        spec = None
        for _eid, candidate in (self.module.encounters or {}).items():
            if candidate.get("title") == enc.title:
                spec = candidate
                break
        if spec:
            loot = spec.get("loot") or {}
            eb = int(loot.get("eurobucks") or loot.get("eb") or 0)
            items = loot.get("items") or {}
            if eb or items:
                self.give_loot(eb, items, enc.title)
        self.autosave()

    def give_loot(self, eurobucks=0, items=None, reason: str = "") -> dict:
        """战利品入账（DM 的 give_loot 工具落点同形）。"""
        items = items or {}
        pc = self.state.player
        pc.eurobucks += int(eurobucks or 0)
        got = []
        for item, n in items.items():
            try:
                count = int(n)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            if item == "ammo":
                # 弹匣不能超过容量：装不下的按「备用弹药」进背包
                spare = count
                for w in pc.weapons:
                    cap = (D.WEAPONS.get(w) or {}).get("ammo")
                    if not cap or spare <= 0:
                        continue
                    have = int(pc.ammo.get(w, cap))
                    put = min(spare, int(cap) - have)
                    if put > 0:
                        pc.ammo[w] = have + put
                        spare -= put
                if spare > 0:
                    pc.gear["ammo"] = int(pc.gear.get("ammo", 0)) + spare
                got.append(f"弹药×{count}")
                continue
            pc.give(item, count)
            name = (D.WEAPONS.get(item) or D.ARMORS.get(item)
                    or D.CYBERWARE.get(item) or D.GEAR.get(item) or {}).get("name", item)
            got.append(f"{name}×{count}")
        msg = f"✦ 获得 {int(eurobucks or 0)} eb"
        if got:
            msg += "、" + "、".join(got)
        if reason:
            msg += f"（{reason}）"
        self.ui.system_line(msg)
        return {"ok": True, "eurobucks": int(eurobucks or 0), "items": items}

    def _epilogue_eb(self) -> int:
        return int(getattr(self.state.player, "eurobucks", 0))

    # ================================================================ 通用
    def party_rest_day(self) -> None:
        """休整一天：已被稳定的成员恢复等于体格的 PW（须先被稳定）。"""
        acted = False
        for c in self.state.party():
            if c.is_dead:
                continue
            if COND_STABILIZED not in c.conditions:
                self.ui.system_line(
                    f"{c.name} 还没有被稳定下来，休整无从谈起（需先做稳定检定）。")
                continue
            healed = c.rest_day()
            acted = True
            self.ui.system_line(
                f"{c.name} 休整一天：恢复 {healed} 点 PW"
                f"（PW {c.hp_cur}/{c.hp_max}）。")
        if acted:
            self.autosave()

    def stabilize_in_explore(self, who=None, party_only: bool = False) -> None:
        """探索期稳定（人类菜单入口）：选目标后交给 stabilize_action。"""
        if party_only:
            pool = [a for a in self.state.party()
                    if a.hp_cur < 1 and not a.is_dead
                    and COND_STABILIZED not in a.conditions]
            if not pool:
                self.ui.system_line("队里没有需要稳定的人。")
                return
            ti = self._choose_target("稳定谁？", pool)
            if ti is None:
                return
            target = pool[ti]
        else:
            target = who or self.state.player
        self.stabilize_action(self.state.player, target)

    def stabilize_action(self, actor: Character | None = None,
                         target: Character | None = None) -> dict:
        """稳定动作（非交互）：技术 + 急救/医疗救援 对抗按伤势等级定的难度值。

        AI 队友的 explore_act("stabilize") 与人类菜单共用同一落点。"""
        actor = actor or self.state.player
        target = target or actor
        if target.hp_cur >= 1 and not target.crit_injuries:
            self.ui.system_line(f"{target.name} 并不需要稳定。")
            return {"ok": False, "error": f"{target.name} 并不需要稳定"}
        skill = "paramedic" if actor.skill("paramedic") >= actor.skill("first_aid") \
            else "first_aid"
        dv = target.stabilize_dv()
        r = actor.check(self.state.rng, skill, dv)
        out = _check_payload(actor, skill, r, dv, f"稳定 {target.name}")
        try:
            self.ui.check_line(out, f"稳定 {target.name}")
        except Exception:  # noqa: BLE001 —— UI 呈现不阻塞结算
            pass
        if r.success:
            target.stabilize()
            self.ui.system_line(
                f"✔ {actor.name} 用{actor.skill_name(skill)}稳定了 {target.name}"
                f"（{r.total} vs DV {dv}）：PW 回到 1，生存检定重新开始。")
        else:
            self.ui.system_line(
                f"✘ {actor.name} 没能稳定 {target.name}（{r.total} vs DV {dv}）。")
        out.update({"ok": True, "success": bool(r.success), "target": target.name,
                    "result": bool(r.success)})
        return out

    def crit_first_aid_in_explore(self, target: Character | None = None) -> None:
        """急救固定（人类菜单入口）：选致命伤后交给 first_aid_action。"""
        target = target or self.state.player
        if not target.crit_injuries:
            self.ui.system_line(f"{target.name} 没有致命伤。")
            return
        idx = self._choose_injury(target)
        if idx is None:
            return
        self.first_aid_action(self.state.player, target, idx)

    def first_aid_action(self, actor: Character | None = None,
                         target: Character | None = None, index: int = 0) -> dict:
        """急救固定（非交互）：当天消除一处致命伤（耗时一分钟，可对自己用）。

        AI 队友的 explore_act("first_aid") 与人类菜单共用同一落点。"""
        actor = actor or self.state.player
        target = target or actor
        if not target.crit_injuries:
            return {"ok": False, "error": f"{target.name} 没有致命伤"}
        index = max(0, min(int(index or 0), len(target.crit_injuries) - 1))
        inj = target.crit_injuries[index]
        dv = target.crit_first_aid_dv(index)
        if dv is None:
            self.ui.system_line(
                f"「{inj['name']}」只能手术处理，急救固定无能为力。")
            return {"ok": False,
                    "error": f"「{inj['name']}」需要外科手术，急救固定无能为力"}
        skill = "paramedic" if actor.skill("paramedic") >= actor.skill("first_aid") \
            else "first_aid"
        r = actor.check(self.state.rng, skill, dv)
        out = _check_payload(actor, skill, r, dv, f"急救固定 {target.name}")
        try:
            self.ui.check_line(out, f"急救固定 {target.name}")
        except Exception:  # noqa: BLE001
            pass
        if r.success:
            target.first_aid_fix(index)
            self.ui.system_line(
                f"✔ {actor.name} 急救固定了 {target.name} 的「{inj['name']}」"
                f"（{r.total} vs DV {dv}），当天不再受其影响。")
        else:
            self.ui.system_line(f"✘ 急救固定失败（{r.total} vs DV {dv}）。")
        out.update({"ok": True, "success": bool(r.success), "dv": dv,
                    "injury": inj["name"], "target": target.name,
                    "result": inj if r.success else None})
        return out

    def repair_armor(self) -> list[str]:
        """修复全队护甲 SP（AI 队友的 explore_act("repair_armor") 落点）。"""
        done = []
        for c in self.state.party():
            if c.armor_key:
                c.repair_armor()
                done.append(c.name)
        if done:
            self.ui.system_line("护甲修复完毕：" + "、".join(done) + "。")
        return done

    def treat_crit_in_explore(self, target: Character | None = None) -> None:
        """治疗致命伤：永久消除一处（每次四小时，无法治疗自己）。"""
        pc = self.state.player
        target = target or pc
        if target is pc:
            pool = [a for a in self.state.party()
                    if a is not pc and a.crit_injuries]
            if not pool:
                self.ui.system_line("无法治疗自己；队里也没有可治疗的致命伤。")
                return
            ti = self._choose_target("治疗谁？", pool)
            if ti is None:
                return
            target = pool[ti]
        if not target.crit_injuries:
            self.ui.system_line(f"{target.name} 没有致命伤。")
            return
        idx = self._choose_injury(target)
        if idx is None:
            return
        inj = target.crit_injuries[idx]
        info = (D.CRIT_INJURIES.get(inj.get("roll", 7)) or {}).get("treatment") or {}
        skill = info.get("skill", "first_aid")
        dv = int(info.get("dv", 13))
        if skill == "paramedic" and pc.skill("paramedic") == 0:
            self.ui.system_line("该致命伤需要外科手术，只有医疗师能处理。")
            return
        r = pc.check(self.state.rng, skill, dv)
        if r.success:
            target.treat_crit_injury(idx)
            self.ui.system_line(
                f"✔ {pc.name} 用{pc.skill_name(skill)}治好了 {target.name} 的"
                f"「{inj['name']}」（{r.total} vs DV {dv}，耗时四小时）。")
        else:
            self.ui.system_line(f"✘ 治疗失败（{r.total} vs DV {dv}），需要再试一次。")

    def show_inventory(self) -> None:
        pc = self.state.player
        self.ui.raw(f"欧元：{pc.eurobucks} eb    改进点：{pc.ip}")
        self.ui.raw(f"护甲：{pc.armor_name()}（SP {pc.armor_at('torso')}）")
        if pc.weapons:
            self.ui.raw("武器：" + "、".join(
                f"{D.WEAPONS.get(w, {}).get('name', w)}"
                f"（{D.WEAPONS.get(w, {}).get('dmg', '?')}，"
                f"弹 {pc.ammo.get(w, D.WEAPONS.get(w, {}).get('ammo') or '—')}）"
                for w in pc.weapons))
        if not pc.gear:
            self.ui.raw("背包：（空）")
            return
        for item, n in pc.gear.items():
            name = (D.GEAR.get(item) or {}).get("name", item)
            self.ui.raw(f"  {name} ×{n}")

    def show_cyberware(self) -> None:
        pc = self.state.player
        self.ui.raw(f"人性：{pc.humanity_cur}/{pc.humanity_max}"
                    f"（共情 {pc.stat('emp')}）")
        if not pc.cyberware:
            self.ui.raw("义体：（无）")
            return
        for key in pc.cyberware:
            info = D.CYBERWARE.get(key) or {}
            self.ui.raw(f"  {info.get('name', key)}（人性 -{info.get('humanity', 0)}）："
                        f"{info.get('text', '')}")

    def autosave(self):
        """自动存档。返回存档路径（失败返回 None）。"""
        try:
            return G.save_game(self.state, SAVE_FILE)
        except OSError:
            return None

    def _weapon_label(self, pc: Character, wkey: str) -> str:
        w = D.WEAPONS.get(wkey, {})
        ammo = w.get("ammo")
        ammo_txt = f"弹 {pc.ammo.get(wkey, ammo)}/{ammo}" if ammo else "无需装填"
        return (f"{w.get('name', wkey)}（{w.get('dmg', '?')}，LA {w.get('rof', 1)}，"
                f"{ammo_txt}）")

    # ---------------------------------------------------------------- 商店
    def _find_good(self, key: str):
        for good in SHOP_GOODS:
            if good[1] == key:
                return good
        return None

    def buy_good(self, key: str) -> dict:
        """非交互购买：校验场景/货品/欧元，扣款入包、autosave。
        人类菜单（open_shop）与 AI PC 的 explore_act("buy") 共用。"""
        key = str(key or "").strip()
        scene = self.module.scene_by_id(self.state.scene_id) or {}
        if not scene.get("shop"):
            return {"ok": False, "error": "这一带没有夜市摊子。"}
        good = self._find_good(key)
        if good is None:
            return {"ok": False, "error": "货摊上没有这种货物。"}
        _cat, gkey, name, price, _desc = good
        pc = self.state.player
        if pc.eurobucks < price:
            return {"ok": False,
                    "error": f"欧元不够（需 {price} eb，持有 {pc.eurobucks} eb）。"}
        pc.eurobucks -= price
        note = self._deliver_good(pc, gkey)
        self.ui.system_line(f"买下{name}（−{price} eb，余 {pc.eurobucks} eb）。{note}")
        self.autosave()
        return {"ok": True, "note": f"买下{name}（−{price} eb）。{note}"}

    def _deliver_good(self, pc: Character, key: str) -> str:
        if key in D.WEAPONS:
            if key not in pc.weapons:
                pc.give(key)
            return ""
        if key in D.ARMORS:
            pc.equip_armor(key)
            return "已换上。"
        if key in D.CYBERWARE:
            loss = pc.install_cyberware(key)
            return f"义体植入完成：人性 -{loss}（现 {pc.humanity_cur}）。"
        if key == "ammo":
            for w in pc.weapons:
                if (D.WEAPONS.get(w) or {}).get("ammo"):
                    pc.ammo[w] = int(pc.ammo.get(w, D.WEAPONS[w]["ammo"])) + AMMO_BOX
                    break
            pc.gear["ammo"] = int(pc.gear.get("ammo", 0)) + AMMO_BOX
            return f"共 {AMMO_BOX} 发。"
        if key == "repair_armor":
            pc.repair_armor()
            return "护甲 SP 已补满。"
        pc.gear[key] = int(pc.gear.get(key, 0)) + 1
        return ""

    def open_shop(self) -> None:
        """夜市场：欧元购买装备/弹药/护甲/义体（scene.shop = true 的场景开放）。"""
        pc = self.state.player
        while True:
            self.ui.raw(f"你数了数手里的欧元：{pc.eurobucks} eb。")
            opts = [f"{name}（{price} eb）—— {desc}"
                    for _cat, _key, name, price, desc in SHOP_GOODS] + ["（离开货摊）"]
            i = self.ui.choose("夜市场——要买点什么？", opts)
            if i >= len(SHOP_GOODS):
                return
            _cat, key, _name, price, _desc = SHOP_GOODS[i]
            if pc.eurobucks < price:
                self.ui.system_line("欧元不够，摊主把货收了回去。")
                continue
            self.buy_good(key)


# ------------------------------------------------------------------ 工具函数
def _melee_reach_cells(enc: Encounter) -> int:
    """贴身判定：一格（2 米）以内为白刃战射程。"""
    return 1


def _check_payload(member: Character, skill: str, res, dv: int,
                   context: str = "") -> dict:
    """检定的结构化结果（UI 的 check_line 与 DM 工具共用同一形状）。

    兼容两种消费方：ConsoleUI 读 ``check``/``detail``/``dv``/``who``；
    WebUI 走 §9 的 dice 载荷（``rolls/expr/total/dv/mods/success/...``）。"""
    info = D.SKILLS.get(skill) or {}
    rolls = [v for v in (getattr(res, "rolls", []) or []) if isinstance(v, int)]
    return {
        "ok": True, "kind": "check", "who": member.name,
        "check": skill, "skill": skill, "label": info.get("name", skill),
        "context": context,
        # ConsoleUI 的 check_line 摘要行
        "detail": getattr(res, "detail", lambda: "")(),
        "d10": rolls[0] if rolls else None,
        "rolls": rolls,
        "expr": getattr(res, "expr", "1d10"),
        "total": getattr(res, "total", 0),
        "dv": dv, "dc": dv,
        "mods": list(getattr(res, "breakdown", []) or []),
        "success": bool(getattr(res, "success", False)),
        "crit_success": bool(getattr(res, "crit_success", False)),
        "crit_failure": bool(getattr(res, "crit_failure", False)),
        "luck_spent": int(getattr(res, "luck_spent", 0) or 0),
        "member": member.name,
    }
