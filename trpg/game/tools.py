"""把游戏引擎暴露为 Agent 工具（DM 工具组 / PC 战斗动作 / AI 队友探索动作）。

原则：LLM 只做"决策"，所有掷骰与数值结算都在引擎里完成，
保证规则公平、可审计（模型可见 ⟺ 已记录）。

契约（PORT_SPEC §5/§6/§7）：

* DM 工具：narrate / speak_as_npc / npc_dialogue / get_state / set_flag /
  advance_scene / start_encounter / map_update / give_loot（保留）
  + skill_check / opposed_check（检定改造）+ award_ip / confrontation /
  request_roll（新增）。**没有** ``save_roll``（属性豁免）与 ``award_xp``（经验）。
* PC 战斗动作：move/approach/dash/attack/grab/choke/throw/escape_grapple/
  reload/stand/stabilize/first_aid/use_item/delay/confrontation/flee/end_turn。
* AI 队友探索动作：rest_day/first_aid/stabilize/buy/save/state/inventory/
  repair_armor（没有短休/长休/喝药水）。
"""

from __future__ import annotations

from ..harness.registry import ToolRegistry
from ..rules import data as D
from ..rules import dice as dice_mod
from ..rules.character import Character
from ..rules.combat import Encounter, METERS_PER_CELL
from . import state as G


class GameTools:
    """绑定 GameState 与可选的当前遭遇。UI 由编排器注入。"""

    def __init__(self, game_state: G.GameState, ui, session=None):
        self.state = game_state
        self.ui = ui
        self.harness_session = session   # orchestrator，用于推进遭遇
        self.encounter: Encounter | None = None

    # ================================================================ 内务
    def _member(self, who: str = "") -> Character:
        """按名字找队伍成员；找不到（含"队伍"）时落到玩家。"""
        member = self.state.find_member(who) if who else None
        return member or self.state.player

    def _emit_check(self, res, *, dv, who: str, label: str,
                    context: str = "") -> dict:
        """渲染一次技能检定：骰子明细给前端，结构化字段回填给 LLM。

        返回 :class:`DiceResult` 的可渲染字典（total/dv/success/mods/
        crit_success/crit_failure/rolls/expr/luck_spent/breakdown），前端
        掷骰动画直接吃这些字段（PORT_SPEC §9）。
        """
        out = _render(res, dv=dv, who=who, label=label,
                      kind="check", luck_spent=getattr(res, "luck_spent", 0))
        if self.ui is not None and hasattr(self.ui, "check_line"):
            self.ui.check_line(out, context)
        return out

    # ================================================================ DM
    def register_dm_tools(self, registry: ToolRegistry) -> None:
        registry.register(
            "narrate",
            "以 DM 身份输出场景叙述（中文，2-5 句）：夜之城 2045 的感官细节、"
            "NPC 的姿态与压力，不要替玩家做决定，也不要泄露敌人底牌。",
            {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: self.ui.dm_narration(text))
        registry.register(
            "speak_as_npc",
            "让某位 NPC 直接说一句话（一句话即止；多回合对话用 npc_dialogue）。"
            "npc=NPC 的名字或 id。",
            {"type": "object",
             "properties": {"npc": {"type": "string"}, "text": {"type": "string"}},
             "required": ["npc", "text"]},
            lambda npc, text: self.ui.npc_line(npc, text))
        registry.register(
            "npc_dialogue",
            "把话头交给某个 NPC 的子智能体：它依据自己的人设、目标与对局记忆"
            "即兴说 1~2 句（台词会出现在桌面上，同伴们听得见，并记入该 NPC 的"
            "长期记忆）。npc=模组 NPC 的 id 或名字；instruction=导演提示"
            "（这段对话的焦点/你的意图/NPC 的态度倾向，可空）。适合多回合的"
            "重要对话（讨价还价/对峙/盘问/诱降）；失败时改用 speak_as_npc。",
            {"type": "object",
             "properties": {"npc": {"type": "string"},
                            "instruction": {"type": "string",
                                            "description": "导演提示，可空"}},
             "required": ["npc"]},
            self._tool_npc_dialogue)
        registry.register(
            "skill_check",
            "让某位角色做一次技能检定：引擎掷 1d10 + 属性 + 技能等级 对抗难度值"
            "DV，结果必须**高于** DV 才算成功（掷出 10 追加一次、掷出 1 减去一次）。"
            "skill 必须是技能表里的 key（如 handgun / perception / persuasion / "
            "athletics）；dv 取 13/15/17/21/24 五档（日常/困难/专业/英雄/不可思议），"
            "也可给 9-30 的任意值；who 为角色名（默认玩家本人）；reason 写清"
            "为什么要检定（供玩家看）。轮到玩家本人时会先等他点击掷骰按钮。",
            {"type": "object",
             "properties": {"skill": {"type": "string"},
                            "dv": {"type": "number",
                                   "description": "难度值：13/15/17/21/24，或 9-30"},
                            "who": {"type": "string"},
                            "reason": {"type": "string"}},
             "required": ["skill", "dv"]},
            self._tool_skill_check)
        registry.register(
            "opposed_check",
            "让两名角色做对抗检定（各掷 1d10 + 属性 + 技能，高者胜；平手时防守方"
            "胜）。用于说服 vs 读心、潜行 vs 察觉、斗殴 vs 闪避这类拉锯。"
            "skill=进攻方技能 key；defender=防守方名字；defender_skill=防守方技能"
            " key（缺省 evasion）；who=进攻方名字（默认玩家本人）。",
            {"type": "object",
             "properties": {"skill": {"type": "string"},
                            "defender": {"type": "string"},
                            "defender_skill": {"type": "string"},
                            "who": {"type": "string"},
                            "reason": {"type": "string"}},
             "required": ["skill", "defender"]},
            self._tool_opposed_check)
        registry.register(
            "get_state",
            "查询游戏状态快照（场景、旗标、队伍 PW/伤势/护甲 SP/幸运/人性/欧元/IP、"
            "背包、进行中的遭遇）。无需参数。",
            {"type": "object", "properties": {}},
            lambda: self._tool_get_state())
        registry.register(
            "set_flag",
            "记录剧情旗标（key=value，值一律为字符串），供后续场景与 DM 自己查阅。",
            {"type": "object",
             "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
             "required": ["key", "value"]},
            self._tool_set_flag)
        registry.register(
            "advance_scene",
            "推进到指定场景（只能前往当前场景的 next，或紧随其后的下一幕）。",
            {"type": "object", "properties": {"scene_id": {"type": "string"}},
             "required": ["scene_id"]},
            self._tool_advance_scene)
        registry.register(
            "award_ip",
            "发放改进点 IP（每人 amount）。IP 是 RED 唯一的长线成长资源："
            "用来提升技能等级、职业特殊能力或买幸运，没有等级与经验值。",
            {"type": "object",
             "properties": {"amount": {"type": "number",
                                       "description": "每人获得的改进点"},
                            "who": {"type": "string",
                                    "description": "只发给某个角色；省略=全队"},
                            "reason": {"type": "string"}},
             "required": ["amount"]},
            self._tool_award_ip)
        registry.register(
            "give_loot",
            "发放战利品：欧元（eb）与物品。items 的 key 取武器/护甲/义体/装备表的"
            " key（如 heavy_pistol / light_armorjack / medkit / ammo），数量按 count。"
            "武器自动入携行列表，护甲自动穿上，义体自动植入（扣人性），其余进背包。",
            {"type": "object",
             "properties": {"eb": {"type": "number", "description": "欧元数额"},
                            "items": {"type": "object", "properties": {},
                                      "additionalProperties": {"type": "number"}}},
             "required": []},
            self._tool_give_loot)
        registry.register(
            "start_encounter",
            "触发指定遭遇进入战斗（encounter_id 见模组 encounters）。",
            {"type": "object", "properties": {"encounter_id": {"type": "string"}},
             "required": ["encounter_id"]},
            self._tool_start_encounter)
        registry.register(
            "confrontation",
            "发起对峙（RED 社交战斗）：双方各掷 1d10 + 魅力 + 声望，败者要么退让，"
            "要么此后对这位对手的所有动作 -2。适合街头比气势、逼问、劝退。",
            {"type": "object",
             "properties": {"who": {"type": "string", "description": "发起者，默认玩家"},
                            "target": {"type": "string", "description": "对手"}},
             "required": ["target"]},
            self._tool_confrontation)
        registry.register(
            "request_roll",
            "把掷骰权交给玩家本人：弹出掷骰按钮，由玩家亲手掷这一次技能检定"
            "（引擎照常结算与记录）。用于玩家主动宣告的行动（「我要撬锁」）。"
            "参数与 skill_check 相同。",
            {"type": "object",
             "properties": {"skill": {"type": "string"},
                            "dv": {"type": "number",
                                   "description": "难度值：13/15/17/21/24，或 9-30"},
                            "who": {"type": "string"},
                            "reason": {"type": "string"}},
             "required": ["skill", "dv"]},
            self._tool_request_roll)
        registry.register(
            "map_update",
            "更新战术地图棋子（探索/社交期谁站在哪由你调度；战斗期棋子由引擎接管）。"
            "ops=操作数组，每项形如：{\"op\":\"move\",\"id\":\"pc:Forty\",\"x\":5,\"y\":7}"
            "（移动到空格，1格=2米）；{\"op\":\"place\",\"id\":\"npc:houston\",\"name\":\"显示名\","
            "\"kind\":\"npc\",\"x\":..,\"y\":..}（放置，kind=npc/prop）；"
            "{\"op\":\"remove\",\"id\":\"prop:chest\"}（撤下）；"
            "{\"op\":\"label\",\"text\":\"...\"}（改地图注记）。"
            "棋子 id 以【地图】上下文为准；越界/障碍/占用格会被拒绝。",
            {"type": "object",
             "properties": {"ops": {"type": "array",
                                    "items": {"type": "object", "properties": {}}}},
             "required": ["ops"]},
            self._tool_map_update)

    # ---------------------------------------------------------- DM 工具实现
    def _ask_player_roll(self, member: Character, prompt: str) -> None:
        """轮到玩家本人掷骰时，先弹出掷骰按钮等待点击（桌面掷自己的骰子）。

        同伴与终端 UI（无 ask_roll）自动落骰，不打断节奏。
        AI PC 模式下玩家与同伴一样自动落骰：掷骰仍是引擎结算，
        仅跳过人类按钮。"""
        if member is self.state.player and hasattr(self.ui, "ask_roll") \
                and not (self.harness_session is not None
                         and getattr(self.harness_session, "aipc", None) is not None):
            self.ui.ask_roll(prompt)

    def _tool_npc_dialogue(self, npc: str, instruction: str = "") -> dict:
        if self.harness_session is None:
            return {"ok": False, "error": "编排器未就绪"}
        return self.harness_session.npc_dialogue(npc, instruction)

    def _tool_skill_check(self, skill: str, dv=None, who: str = "",
                          reason: str = "", context: str = "",
                          dc=None) -> dict:
        """技能检定：1d10 + 属性 + 技能 vs DV（必须高于 DV）。

        ``dc`` 为旧参数名，仅作等价别名保留（移植过渡期调用点兼容）。
        """
        if skill not in D.SKILLS:
            return {"ok": False, "error": f"未知技能 {skill}"}
        value = dv if dv is not None else dc
        if value is None:
            return {"ok": False, "error": "缺少难度值 dv"}
        dv_int = int(value)
        member = self._member(who)
        name = D.SKILLS[skill]["name"]
        self._ask_player_roll(
            member, f"轮到你掷骰 —— {name}（DV {dv_int}）")
        res = member.check(self.state.rng, skill, dv=dv_int)
        out = self._emit_check(res, dv=dv_int, who=member.name, label=name,
                               context=context or reason)
        out["member"] = member.name
        return out

    def _tool_opposed_check(self, skill: str, defender: str,
                            defender_skill: str = "evasion",
                            who: str = "", reason: str = "") -> dict:
        """对抗检定：双方各 1d10 + 属性 + 技能，高者胜（平手防守方胜）。"""
        if skill not in D.SKILLS:
            return {"ok": False, "error": f"未知技能 {skill}"}
        if defender_skill not in D.SKILLS:
            return {"ok": False, "error": f"未知技能 {defender_skill}"}
        att = self._member(who)
        dfd = self.state.find_member(defender)
        if dfd is None:
            return {"ok": False, "error": f"找不到角色 {defender}"}
        self._ask_player_roll(
            att, f"轮到你掷骰 —— {D.SKILLS[skill]['name']}"
                 f" 对抗 {dfd.name} 的{D.SKILLS[defender_skill]['name']}")
        res, def_res, win = att.opposed_check(
            self.state.rng, skill, dfd, defender_skill)
        out = _render(res, dv=def_res.total, who=att.name,
                      label=D.SKILLS[skill]["name"], kind="check", win=bool(win),
                      luck_spent=getattr(res, "luck_spent", 0))
        out.update({
            "ok": True, "kind": "opposed", "win": bool(win),
            "attacker": att.name, "defender": dfd.name,
            "skill": skill, "defender_skill": defender_skill,
            "reason": reason,
            "attack_total": res.total, "defense_total": def_res.total,
            "attack_rolls": list(res.rolls), "defense_rolls": list(def_res.rolls),
            "defense_mods": list(def_res.breakdown),
        })
        if self.ui is not None and hasattr(self.ui, "check_line"):
            self.ui.check_line(out, reason)
        return out

    def _tool_get_state(self) -> dict:
        scene = self.state.module.scene_by_id(self.state.scene_id) or {}
        brief = {
            "ruleset": G.RULESET,
            "scene": self.state.scene_id,
            "act": scene.get("act"),
            "location": scene.get("location"),
            "flags": dict(self.state.flags),
            "party": self.state.party_panel(),
            "party_eurobucks": int(getattr(self.state, "party_eurobucks", 0) or 0),
            "inventory": self._inventory(self.state.player),
        }
        if self.encounter is not None:
            brief["encounter"] = self.encounter.brief()
            brief["round"] = self.encounter.round
            brief["board"] = self.encounter.board()
        return brief

    def _tool_set_flag(self, key: str, value: str) -> dict:
        self.state.flags[key] = value
        return {"ok": True}

    def _tool_advance_scene(self, scene_id: str) -> dict:
        if self.harness_session is None:
            return {"ok": False, "error": "编排器未就绪"}
        return self.harness_session.advance_scene(scene_id)

    def _tool_award_ip(self, amount, who: str = "", reason: str = "") -> dict:
        """发放改进点（每人 amount；who 指定时只发一人）。"""
        amount = int(amount)
        share = max(0, amount)
        targets = [self._member(who)] if who else self.state.party()
        msgs = []
        for c in targets:
            c.gain_ip(share)
            msgs.append(f"{c.name} IP {c.ip}")
        self.ui.system_line(
            f"✦ 改进点 +{share}/人{'（' + reason + '）' if reason else ''}"
            + "；".join(msgs))
        # 改进点消费菜单（技能/职业能力/幸运）由编排器结算
        for hook in ("resolve_pending_ip", "resolve_pending_choices"):
            fn = getattr(self.harness_session, hook, None)
            if callable(fn):
                fn()
                break
        return {"ok": True, "each": share, "party": msgs}

    def _tool_give_loot(self, eb=0, items=None) -> dict:
        """发放战利品：欧元 + 物品（武器/护甲/义体/装备按各自表归位）。"""
        amount = int(eb or 0)
        items = items or {}
        got = []
        if amount:
            self.state.player.eurobucks += amount
            self.state.party_eurobucks = int(
                getattr(self.state, "party_eurobucks", 0) or 0) + amount
        for item, n in items.items():
            n = int(n)
            if n <= 0:
                continue
            key = str(item)
            if key in ("eb", "eurobucks", "欧元"):
                self.state.player.eurobucks += n
                self.state.party_eurobucks = int(
                    getattr(self.state, "party_eurobucks", 0) or 0) + n
                got.append(f"{D.CURRENCY}×{n}")
                continue
            if key not in D.WEAPONS and key not in D.ARMORS \
                    and key not in D.CYBERWARE and key not in D.GEAR:
                got.append(f"（未知物品 {key} 已忽略）")
                continue
            self.state.player.give(key, n)
            got.append(f"{self._item_name(key)}×{n}")
        msg = f"✦ 获得 {amount} {D.CURRENCY}（{D.CURRENCY_ABBR}）"
        if got:
            msg += "、" + "、".join(got)
        self.ui.system_line(msg)
        return {"ok": True, "eb": amount, "items": got,
                "eurobucks": self.state.player.eurobucks}

    def _tool_start_encounter(self, encounter_id: str) -> dict:
        if self.harness_session is None:
            return {"ok": False, "error": "编排器未就绪"}
        return self.harness_session.launch_encounter(encounter_id)

    def _tool_confrontation(self, target: str, who: str = "") -> dict:
        """对峙：魅力 + 声望 + 1d10 对抗（战斗中走 Encounter，探索期就地结算）。"""
        att = self._member(who)
        tgt = self.state.find_member(target)
        if tgt is None:
            return {"ok": False, "error": f"找不到对手 {target}"}
        if self.encounter is not None:
            inside = (self.encounter.find(att.name), self.encounter.find(tgt.name))
            if all(inside):
                return self.encounter.confrontation(*inside)
        return self._confront(att, tgt)

    def _confront(self, att: Character, tgt: Character) -> dict:
        """探索期对峙结算（与 Encounter.confrontation 同口径）。"""
        a = dice_mod.check_roll(self.state.rng, att.stat("cool") + att.reputation)
        b = dice_mod.check_roll(self.state.rng, tgt.stat("cool") + tgt.reputation)
        out = _render(a, dv=b.total, who=att.name, label="对峙", kind="check")
        out["defense_total"] = b.total
        out["defense_rolls"] = list(b.rolls)
        if a.total == b.total:
            out.update({"ok": True, "tie": True})
            text = f"→ 对峙：{att.name} 与 {tgt.name} 平分秋色，只能动真格的。"
        else:
            loser = tgt if a.total > b.total else att
            winner = att if loser is tgt else tgt
            loser.timers["facedown"] = {
                "against": winner.name, "penalty": D.GRAPPLE_PENALTY}
            out.update({"ok": True, "winner": winner.name, "loser": loser.name,
                        "penalty": D.GRAPPLE_PENALTY})
            text = (f"→ 对峙：{att.name} {a.total} vs {tgt.name} {b.total}——"
                    f"{loser.name} 要么退让，要么对 {winner.name} 的所有动作"
                    f"{D.GRAPPLE_PENALTY}。")
        self.ui.system_line(text)
        if self.ui is not None and hasattr(self.ui, "check_line"):
            self.ui.check_line(out, "对峙")
        return out

    def _tool_request_roll(self, skill: str, dv=None, who: str = "",
                           reason: str = "", dc=None) -> dict:
        """让玩家亲手掷骰（与 skill_check 同结算，只是强制弹按钮）。"""
        if skill not in D.SKILLS:
            return {"ok": False, "error": f"未知技能 {skill}"}
        value = dv if dv is not None else dc
        if value is None:
            return {"ok": False, "error": "缺少难度值 dv"}
        dv_int = int(value)
        member = self._member(who) if who else self.state.player
        name = D.SKILLS[skill]["name"]
        ask = getattr(self.ui, "ask_roll", None)
        if callable(ask):
            ask(f"轮到你掷骰 —— {name}（DV {dv_int}）"
                + (f"：{reason}" if reason else ""))
        res = member.check(self.state.rng, skill, dv=dv_int)
        out = self._emit_check(res, dv=dv_int, who=member.name, label=name,
                               context=reason)
        out["member"] = member.name
        return out

    def _tool_map_update(self, ops=None) -> dict:
        """DM 的 map_update 工具落点：逐条校验执行，任一成功即广播新快照。"""
        if self.state.map is None:
            return {"ok": False, "error": "本局没有战术地图"}
        if self.encounter is not None:
            return {"ok": False, "error": "战斗中棋子由引擎接管"}
        if not isinstance(ops, list):
            return {"ok": False, "error": "ops 必须是操作数组"}
        icons = getattr(self.state.module, "token_icons", {}) or {}

        def _cell(op) -> list[int] | None:
            try:
                return [int(op["x"]), int(op["y"])]
            except (KeyError, TypeError, ValueError):
                return None

        results = []
        for op in ops[:24]:
            if not isinstance(op, dict):
                results.append({"ok": False, "error": "操作必须是对象"})
                continue
            kind = str(op.get("op", ""))
            if kind == "move":
                cell = _cell(op)
                r = self.state.map_move(str(op.get("id", "")), cell) if cell \
                    else {"ok": False, "error": "缺少整数 x/y"}
            elif kind == "place":
                tid = str(op.get("id", "") or "")
                cell = _cell(op)
                k = str(op.get("kind", "prop"))
                r = self.state.map_place(
                    tid, str(op.get("name") or tid),
                    k if k in ("npc", "prop") else "prop", cell,
                    icon=icons.get(tid.split(":", 1)[-1])) if cell and tid \
                    else {"ok": False, "error": "缺少 id 或整数 x/y"}
            elif kind == "remove":
                r = self.state.map_remove(str(op.get("id", "")))
            elif kind == "label":
                self.state.map["label"] = str(op.get("text", ""))[:40]
                r = {"ok": True}
            else:
                r = {"ok": False, "error": "未知操作 " + kind}
            results.append(r)
        ok = any(r.get("ok") for r in results)
        if ok and self.harness_session is not None:
            self.harness_session.broadcast_map()
        return {"ok": ok, "results": results}

    # ---------------------------------------------------------- 文本工具
    @staticmethod
    def _item_name(key: str) -> str:
        for table in (D.WEAPONS, D.ARMORS, D.CYBERWARE, D.GEAR):
            if key in table:
                return table[key].get("name", key)
        return key

    def _inventory(self, c: Character) -> dict:
        """背包的可读形态：欧元、武器、护甲、义体、装备、弹药。"""
        return {
            D.CURRENCY_ABBR: c.eurobucks,
            "武器": [self._item_name(k) for k in c.weapons],
            "护甲": c.armor_name(),
            "义体": [self._item_name(k) for k in c.cyberware],
            "装备": {self._item_name(k): v for k, v in c.gear.items()},
            "弹药": {self._item_name(k): v for k, v in c.ammo.items()},
        }

    # ================================================================ PC 战斗
    def register_pc_tools(self, registry: ToolRegistry, pc: Character) -> None:
        registry.register(
            "say", f"以 {pc.name} 的口吻说一句话（中文，符合人设）。",
            {"type": "object",
             "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: self.ui.companion_line(pc.name, str(text)))
        registry.register(
            "combat_action",
            "战斗中执行一个动作（RED：每回合 1 个移动动作 + 1 个其他动作，"
            "移动距离 = 机动×2 米，1 格 = 2 米）。action 取值：\n"
            "· move(x,y)：走向目标格（逐格消耗移动力，不消耗动作；脱离贴身会吃借机攻击）\n"
            "· approach(target)：主动贴近到贴身距离\n"
            "· dash：奔跑，本回合必须先移动过，额外获得一个移动动作的距离\n"
            "· attack(weapon,target,aimed)：攻击。远程按射程带查 DV（反应+射击技能），"
            "白刃战对抗闪避并无视一半护甲；aimed=true 为瞄准射击（-8，命中头部）\n"
            "· grab(target)/choke(target)/throw(target)：斗殴三连——擒抱控住对手、"
            "锁喉造成等于体格的伤害、把被控住的对手摔倒在地\n"
            "· escape_grapple(target)：挣脱对手的控制\n"
            "· reload(weapon)：装满弹匣\n"
            "· stand：从倒地状态起身（消耗一半移动力）\n"
            "· stabilize(target)：技术+急救/医疗救援，把濒死同伴的 PW 拉回 1\n"
            "· first_aid(target,index)：急救固定一处致命伤（当天不再受影响）\n"
            "· use_item(item)：使用装备/取出武器\n"
            "· delay(before)：把行动顺位延后到某人之前\n"
            "· confrontation(target)：对峙（魅力+声望），败者退让或动作 -2\n"
            "· flee：脱离战斗/撤退\n"
            "· end_turn：结束本回合，把剩下的动作交出去",
            {"type": "object",
             "properties": {
                 "action": {"type": "string"},
                 "target": {"type": "string", "description": "目标名字"},
                 "weapon": {"type": "string", "description": "武器 key，省略=自动挑最好的"},
                 "cell": {"type": "array", "items": {"type": "number"},
                          "description": "move 的目标格 [x,y]"},
                 "x": {"type": "number"}, "y": {"type": "number"},
                 "aimed": {"type": "boolean", "description": "瞄准射击（-8）"},
                 "luck": {"type": "number", "description": "投入的幸运点"},
                 "item": {"type": "string"}, "before": {"type": "string"},
                 "index": {"type": "number", "description": "第几处致命伤（从 0 起）"}},
             "required": ["action"]},
            lambda **kw: self._pc_combat_action(pc, **kw))
        registry.register(
            "explore_intent",
            "非战斗时声明一个探索意图（如搜查、翻窗、找线人聊两句），"
            "由 DM 裁定成败与代价。",
            {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: {"ok": True, "intent": text})

    def _pc_combat_action(self, pc: Character, action: str, target: str = "",
                          weapon: str = "", item: str = "", before: str = "",
                          cell=None, x=None, y=None, aimed: bool = False,
                          luck=0, index=0, **kw) -> dict:
        """PC 战斗动作分发：每个 action 落到 ``Encounter`` 的对应方法。"""
        enc = self.encounter
        if enc is None:
            return {"ok": False, "error": "当前没有进行中的战斗"}
        action = str(action or "").strip()
        who = enc.find(pc.name) or pc
        if action == "move":
            dest = _cell_of(cell, x, y)
            if dest is None:
                return {"ok": False,
                        "error": f"move 需要目标格 cell=[x,y] 或 x,y（1格={METERS_PER_CELL}米）"}
            r = enc.move(who, dest)
            self._sync_map(r)
            return r
        if action in ("approach", "advance"):
            t = enc.find(target)
            if t is None:
                return {"ok": False, "error": "找不到目标 " + str(target)}
            r = enc.approach(who, t)
            self._sync_map(r)
            return r
        if action == "dash":
            r = enc.dash(who)
            self._sync_map(r)
            return r
        if action == "attack":
            t = enc.find(target)
            if t is None:
                return {"ok": False, "error": "找不到目标 " + str(target)}
            if weapon and weapon not in pc.weapons:
                return {"ok": False, "error": f"没有武器 {weapon}"
                        f"（拥有：{'、'.join(pc.weapons) or '徒手'}）"}
            r = enc.attack_action(who, t, weapon or None,
                                  aimed=bool(aimed), luck=int(luck or 0))
            # 命中可能被掩体挡下 → 掩体耐久变化，棋盘即时刷新
            self._sync_map(r)
            return r
        if action == "grab":
            t = _need(enc, target)
            return enc.grab(who, t) if isinstance(t, Character) else t
        if action == "choke":
            t = _need(enc, target)
            return enc.choke(who, t) if isinstance(t, Character) else t
        if action in ("throw", "throw_target"):
            t = _need(enc, target)
            return enc.throw_target(who, t) if isinstance(t, Character) else t
        if action in ("escape_grapple", "escape"):
            t = _need(enc, target)
            return enc.escape_grab(who, t) if isinstance(t, Character) else t
        if action == "reload":
            key = weapon or (pc.weapons[0] if pc.weapons else "")
            if not key:
                return {"ok": False, "error": "没有可装填的武器"}
            return enc.reload(who, key)
        if action == "stand":
            return enc.stand(who)
        if action == "stabilize":
            t = _need(enc, target)
            return enc.stabilize_action(who, t) if isinstance(t, Character) else t
        if action == "first_aid":
            t = _need(enc, target)
            return enc.first_aid_fix(who, t, int(index or 0)) \
                if isinstance(t, Character) else t
        if action == "use_item":
            return enc.use_item(who, str(item or ""))
        if action == "delay":
            return enc.delay(who, before or None)
        if action == "confrontation":
            t = _need(enc, target)
            return enc.confrontation(who, t) if isinstance(t, Character) else t
        if action in ("flee", "retreat"):
            return self._flee(enc, who)
        if action in ("end_turn", "pass", "wait"):
            return self._end_turn(enc, who)
        return {"ok": False, "error": "未知动作 " + action}

    def _sync_map(self, r: dict) -> None:
        """走位后把 Encounter 的坐标回写到战术地图（有编排器时）。"""
        if not isinstance(r, dict) or not r.get("ok"):
            return
        s = self.harness_session
        fn = getattr(s, "_sync_map_from_encounter", None)
        if callable(fn) and self.encounter is not None:
            fn(self.encounter)

    def _flee(self, enc: Encounter, who: Character) -> dict:
        """脱离战斗：标记撤退；队伍是否会因此团灭由编排器裁定。"""
        enc.retreated = True
        enc.use_action(who)
        enc.log(f"→ {who.name} 脱离战斗，且战且退。")
        self._sync_map({"ok": True})
        return {"ok": True, "retreat": True,
                "note": f"{who.name} 试图脱离战斗（撤退由 DM 裁定后续）。"}

    def _end_turn(self, enc: Encounter, who: Character) -> dict:
        """结束本回合：交出剩余移动力与动作（先攻推进由编排器负责）。"""
        enc.use_action(who)
        enc.use_bonus(who)
        enc.speed_left[who.name] = 0
        enc.log(f"→ {who.name} 结束回合。")
        return {"ok": True, "note": f"{who.name} 结束回合。"}

    # ================================================================ AI 队友
    def register_party_tools(self, registry: ToolRegistry, who: Character) -> None:
        """全 AI 跑团：所有 PC 共用的「自主玩家」工具面。

        与 ``register_aipc_tools`` 的差异：没有 OOC / 桌边笔记；PC 直接以角色
        身份说话、声明行动、执行休整/急救/采购等探索动作。工具只作用于
        ``who`` 自己（或明确指定的队友），不能改世界——世界仍由 DM 裁定。
        """
        def say(text: str, to: str = "") -> dict:
            body = str(text or "").strip()
            if not body:
                return {"ok": False, "error": "台词为空"}
            self.ui.companion_line(who.name, body)
            if self.harness_session is not None:
                self.harness_session.table_log.append(
                    {"who": who.name, "text": body, "to": str(to or ""),
                     "interrupt": False, "kind": "table"})
            return {"ok": True, "note": "已说出口，桌上都听见了。"}

        def hold(reason: str = "") -> dict:
            return {"ok": True, "note": "这一拍你不说话。"}

        registry.register(
            "say", f"以 {who.name} 的角色身份说一句话。to=对谁说（可空）。",
            {"type": "object",
             "properties": {"text": {"type": "string"},
                            "to": {"type": "string"}},
             "required": ["text"]},
            say)
        registry.register(
            "explore_intent",
            "声明一个探索行动（搜查、潜行、问路、拆锁、谈判等），由 DM 裁定"
            "结果与后果。这是自主玩家最常用的行动工具。",
            {"type": "object",
             "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: {"ok": True, "intent": str(text or "")})
        registry.register(
            "explore_act",
            "执行一次非战斗行动。kind 取值：rest_day / first_aid(target,index) / "
            "stabilize(target) / buy(item) / repair_armor / state / inventory / save。",
            {"type": "object",
             "properties": {"kind": {"type": "string"},
                            "item": {"type": "string"},
                            "target": {"type": "string"},
                            "index": {"type": "number"}},
             "required": ["kind"]},
            lambda kind, item="", target="", index=0: self._tool_explore_act(
                kind, item=item, target=target, index=index, helper=who))
        registry.register(
            "hold", "这一拍不行动（少用；大部分时候应当做点什么）。",
            {"type": "object", "properties": {"reason": {"type": "string"}},
             "required": []},
            hold)

    def register_aipc_tools(self, registry: ToolRegistry, who: Character) -> None:
        """AI 队友的探索期工具面。

        与 register_pc_tools 的差异：say 带桌面记账（探索期 AI 的话必须进
        table_log，DM 的 _digest_table 与同伴的 _recent 才"听得见"）；新增
        explore_act 非交互行动面（替代人类斜杠指令：休整/急救/稳定/购买/修甲/
        存档/看状态/看背包）。战斗期不用本组——走 register_pc_tools 的
        combat_action。"""
        registry.register(
            "say", f"以 {who.name} 的口吻说一句话（中文，符合人设）。to=对谁说（可空）。",
            {"type": "object",
             "properties": {"text": {"type": "string"},
                            "to": {"type": "string"}},
             "required": ["text"]},
            lambda text, to="": self._aipc_say(who, text, to))
        registry.register(
            "explore_intent",
            "非战斗时声明一个探索意图（如搜查、翻窗、找线人聊两句），由 DM 裁定。",
            {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: {"ok": True, "intent": text})
        registry.register(
            "explore_act",
            "执行一次非战斗行动。kind 取值：\n"
            "· rest_day：休整一整天（每个完整休息日恢复等于体格的 PW；须已被稳定）\n"
            "· first_aid(target,index)：急救固定一处致命伤（耗时一分钟，当天消除其影响）\n"
            "· stabilize(target)：稳定濒死的同伴（技术+急救/医疗救援对抗难度值）\n"
            "· buy(item)：在夜之城买装备（需当前场景有商店）\n"
            "· repair_armor：修好自己的护甲（恢复 SP 到满值，需欧元）\n"
            "· save：保存游戏\n· state：查看队伍状态\n· inventory：查看背包",
            {"type": "object",
             "properties": {"kind": {"type": "string"},
                            "item": {"type": "string", "description": "buy 的商品 key"},
                            "target": {"type": "string", "description": "急救/稳定的对象"},
                            "index": {"type": "number"}},
             "required": ["kind"]},
            lambda kind, item="", target="", index=0: self._tool_explore_act(
                kind, item=item, target=target, index=index, helper=who))

    def _aipc_say(self, who: Character, text: str, to: str = "") -> dict:
        """AI 队友的桌面发言：渲染 + 记入 table_log（行结构与 social.py 对齐）。"""
        body = str(text)
        self.ui.companion_line(who.name, body)
        if self.harness_session is not None:
            self.harness_session.table_log.append(
                {"who": who.name, "text": body, "to": str(to or ""),
                 "interrupt": False, "kind": "table"})
        return {"ok": True, "note": "已说出口，桌边都听见了。"}

    def _tool_explore_act(self, kind: str, item: str = "", target: str = "",
                          index=0, helper: Character | None = None) -> dict:
        """非交互探索行动面。全部毫秒级完成，绝不触达 ui.choose/free_input 阻塞点。"""
        s = self.harness_session
        if s is None:
            return {"ok": False, "error": "编排器未就绪"}
        kind = str(kind or "").strip()
        if kind == "save":
            path = s.autosave()
            return {"ok": True, "note": f"已保存到 {path}"}
        if kind == "state":
            self.ui.raw(self.state.party_snapshot())
            return {"ok": True, "note": "状态已呈现。"}
        if kind == "inventory":
            self._show_inventory()
            return {"ok": True, "note": "背包已呈现。"}
        if kind == "rest_day":
            fn = getattr(s, "party_rest_day", None) or getattr(s, "rest_day", None)
            if not callable(fn):
                return {"ok": False, "error": "编排器未提供休整日入口（party_rest_day）"}
            fn()
            return {"ok": True, "note": "队伍休整了一天。"}
        if kind == "repair_armor":
            done = []
            for c in self.state.party():
                if c.armor_key:
                    c.repair_armor()
                    done.append(c.name)
            fn = getattr(s, "repair_armor", None)
            if callable(fn):
                fn()
            note = "护甲已修复（" + "、".join(done) + "）。" if done else "没人穿护甲。"
            return {"ok": True, "note": note}
        if kind in ("first_aid", "stabilize"):
            tgt = self.state.find_member(target) if target else None
            if tgt is None:
                return {"ok": False, "error": f"找不到目标 {target or '（未指定）'}"}
            actor = helper or self.state.player
            if kind == "first_aid":
                fn = getattr(s, "first_aid_action", None)
                if callable(fn):
                    return fn(actor, tgt, int(index or 0))
                r = tgt.first_aid_fix(int(index or 0))
                if r is None:
                    return {"ok": False,
                            "error": f"{tgt.name} 没有可急救固定的致命伤"}
                self.ui.system_line(
                    f"✔ {actor.name} 急救固定了 {tgt.name} 的「{r['name']}」。")
                return {"ok": True, "result": r, "note": f"急救固定：{tgt.name}"}
            # stabilize：探索期无战斗引擎，就地按稳定难度值做一次急救检定
            dv = tgt.stabilize_dv()
            skill = "paramedic" if actor.skill("paramedic") >= actor.skill("first_aid") \
                else "first_aid"
            res = actor.check(self.state.rng, skill, dv=dv)
            out = self._emit_check(res, dv=dv, who=actor.name,
                                   label=f"稳定 {tgt.name}")
            if res.success:
                tgt.stabilize()
                self.ui.system_line(
                    f"✔ {actor.name} 稳定了 {tgt.name}（{res.total} vs DV {dv}），"
                    f"PW 回到 1。")
            else:
                self.ui.system_line(
                    f"✘ {actor.name} 没能稳定 {tgt.name}（{res.total} vs DV {dv}）。")
            out["ok"] = True
            return out
        if kind == "buy":
            fn = getattr(s, "buy_good", None)
            if not callable(fn):
                return {"ok": False, "error": "编排器未提供商店入口（buy_good）"}
            if not item:
                return {"ok": False, "error": "buy 需要 item（商品 key）"}
            return fn(str(item))
        return {"ok": False, "error": "未知行动 " + kind}

    def _show_inventory(self) -> None:
        """背包展示：优先用编排器的 show_inventory，否则就地渲染。"""
        fn = getattr(self.harness_session, "show_inventory", None)
        if callable(fn):
            fn()
            return
        c = self.state.player
        self.ui.raw(f"—— {c.name} 的背包 ——\n"
                    f"{D.CURRENCY} {c.eurobucks}（{D.CURRENCY_ABBR}）\n"
                    f"武器：{'、'.join(self._item_name(k) for k in c.weapons) or '徒手'}\n"
                    f"护甲：{c.armor_name()} SP {c.armor_at('torso')}\n"
                    f"义体：{'、'.join(self._item_name(k) for k in c.cyberware) or '无'}\n"
                    f"装备：{'、'.join(f'{self._item_name(k)}×{v}' for k, v in c.gear.items()) or '空'}")


# ------------------------------------------------------------------ 工具函数
def _render(res, *, dv=None, who: str = "", label: str = "", kind: str = "check",
            win=None, luck_spent=0) -> dict:
    """把 :class:`DiceResult` 摊平成前端/LLM 都能读的字典（PORT_SPEC §9）。

    同时携带两套键名：``dv``（RED）与 ``dc``（旧前端过渡读法），
    ``mods`` 与 ``breakdown``。"""
    dv = res.dv if dv is None else dv
    out = {
        "ok": True,
        "kind": kind,
        "who": who or "",
        "label": label,
        "check": label,
        "skill": label,
        "expr": res.expr,
        "rolls": list(res.rolls),
        "kept": list(res.kept),
        "modifier": res.modifier,
        "mods": list(res.breakdown),
        "breakdown": list(res.breakdown),
        "total": res.total,
        "dv": dv,
        "dc": dv,                       # 过渡别名（旧前端读 dc）
        "success": bool(res.success) if win is None else bool(win),
        "crit_success": bool(res.crit_success),
        "crit_failure": bool(res.crit_failure),
        "luck_spent": int(luck_spent),
        "detail": res.detail(),
    }
    return out


def _cell_of(cell, x, y):
    """统一 ``cell=[x,y]`` / ``cell={x,y}`` / ``x,y`` 三种写法。"""
    try:
        if isinstance(cell, dict):
            return (int(cell["x"]), int(cell["y"]))
        if isinstance(cell, (list, tuple)) and len(cell) == 2:
            return (int(cell[0]), int(cell[1]))
        if x is not None and y is not None:
            return (int(x), int(y))
    except (TypeError, ValueError, KeyError):
        return None
    return None


def _need(enc: Encounter, target: str):
    """取战斗中的目标；找不到时返回错误字典（调用方 isinstance 判定）。"""
    t = enc.find(target)
    if t is None:
        return {"ok": False, "error": "找不到目标 " + str(target)}
    return t
