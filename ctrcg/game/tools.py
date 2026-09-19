"""把游戏引擎暴露为 Agent 工具（DM 工具组 / PC 工具组）。

原则：LLM 只做"决策"，所有掷骰与数值结算都在引擎里完成，
保证规则公平、可审计（模型可见 ⟺ 已记录）。
"""

from __future__ import annotations

from ..harness.registry import ToolRegistry
from ..rules import data as D
from ..rules.character import Character
from ..rules.combat import Encounter
from . import state as G


class GameTools:
    """绑定 GameState 与可选的当前遭遇。UI 由编排器注入。"""

    def __init__(self, game_state: G.GameState, ui, session=None):
        self.state = game_state
        self.ui = ui
        self.harness_session = session   # orchestrator，用于推进遭遇
        self.encounter: Encounter | None = None

    # ================================================================ DM
    def register_dm_tools(self, registry: ToolRegistry) -> None:
        registry.register(
            "narrate", "以 DM 身份输出场景叙述（每次 2-5 句，中文）。",
            {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: self.ui.dm_narration(text))
        registry.register(
            "speak_as_npc", "让 NPC 说一句话。",
            {"type": "object",
             "properties": {"npc": {"type": "string"}, "text": {"type": "string"}},
             "required": ["npc", "text"]},
            lambda npc, text: self.ui.npc_line(npc, text))
        registry.register(
            "skill_check",
            "让某位角色进行技能检定（引擎掷骰）。who 为角色全名或'队伍'。",
            {"type": "object",
             "properties": {"who": {"type": "string"}, "skill": {"type": "string"},
                            "dc": {"type": "number"}, "context": {"type": "string"}},
             "required": ["who", "skill", "dc"]},
            self._tool_skill_check)
        registry.register(
            "save_roll", "让某位角色进行属性豁免（引擎掷骰）。",
            {"type": "object",
             "properties": {"who": {"type": "string"}, "ability": {"type": "string"},
                            "dc": {"type": "number"}},
             "required": ["who", "ability", "dc"]},
            self._tool_save)
        registry.register(
            "get_state", "查询游戏状态快照（队伍、场景、旗标、遭遇）。无需参数。",
            {"type": "object", "properties": {}},
            lambda: self._tool_get_state())
        registry.register(
            "set_flag", "记录剧情旗标（key=value）。",
            {"type": "object",
             "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
             "required": ["key", "value"]},
            self._tool_set_flag)
        registry.register(
            "advance_scene", "推进到指定场景（仅限当前场景的 next，或按顺序的下一幕）。",
            {"type": "object", "properties": {"scene_id": {"type": "string"}},
             "required": ["scene_id"]},
            self._tool_advance_scene)
        registry.register(
            "award_xp", "奖励队伍经验值（每人获得 amount/队伍人数）。",
            {"type": "object",
             "properties": {"amount": {"type": "number"}, "reason": {"type": "string"}},
             "required": ["amount"]},
            self._tool_award_xp)
        registry.register(
            "give_loot", "发放战利品。",
            {"type": "object",
             "properties": {"gold": {"type": "number"},
                            "items": {"type": "object",
                                      "properties": {}, "additionalProperties": {"type": "number"}}},
             "required": []},
            self._tool_give_loot)
        registry.register(
            "start_encounter", "触发指定遭遇进入战斗。",
            {"type": "object", "properties": {"encounter_id": {"type": "string"}},
             "required": ["encounter_id"]},
            self._tool_start_encounter)

    def _tool_skill_check(self, who: str, skill: str, dc, context: str = "") -> dict:
        if skill not in D.SKILLS:
            return {"ok": False, "error": f"未知技能 {skill}"}
        member = self.state.find_member(who) if who else None
        if member is None:
            member = self.state.player  # '队伍'默认由玩家执行
        r = member.check(self.state.rng, skill, int(dc))
        self.ui.check_line(r, context)
        return {**r, "member": member.name}

    def _tool_save(self, who: str, ability: str, dc) -> dict:
        if ability not in D.ABILITIES:
            return {"ok": False, "error": f"未知属性 {ability}"}
        member = self.state.find_member(who) or self.state.player
        r = member.save(self.state.rng, ability, int(dc))
        self.ui.save_line(r)
        return r

    def _tool_get_state(self) -> dict:
        scene = self.state.module.scene_by_id(self.state.scene_id) or {}
        brief = {
            "scene": self.state.scene_id,
            "act": scene.get("act"),
            "flags": dict(self.state.flags),
            "party": [{"name": c.name, "hp": f"{c.hp_cur}/{c.hp_max}", "ac": c.ac(),
                       "level": c.level, "xp": c.xp} for c in self.state.party()],
            "inventory": dict(self.state.player.inventory),
        }
        if self.encounter:
            brief["encounter"] = self.encounter.brief()
        return brief

    def _tool_set_flag(self, key: str, value: str) -> dict:
        self.state.flags[key] = value
        return {"ok": True}

    def _tool_advance_scene(self, scene_id: str) -> dict:
        return self.harness_session.advance_scene(scene_id)

    def _tool_award_xp(self, amount, reason: str = "") -> dict:
        amount = int(amount)
        msgs = []
        share = max(1, amount // len(self.state.party()))
        for c in self.state.party():
            m = c.gain_xp(share)
            if m:
                msgs.append(m)
        self.ui.system_line(f"✦ 经验 +{share}/人{'（' + reason + '）' if reason else ''}"
                            + ("；".join(msgs) if msgs else ""))
        # 经验升级可能带来职业特性选择（ASI/专长/子职业等）——即时结算
        if self.harness_session is not None:
            self.harness_session.resolve_pending_choices()
        return {"ok": True, "each": share, "levelups": msgs}

    def _tool_give_loot(self, gold=0, items=None) -> dict:
        items = items or {}
        self.state.player.gold += int(gold)
        got, attuned = [], []
        for item, n in items.items():
            if item in D.ITEMS:
                first = self.state.player.inventory.get(item, 0) == 0
                self.state.player.inventory[item] =                     self.state.player.inventory.get(item, 0) + int(n)
                got.append(f"{D.ITEMS[item]['name']}×{n}")
                # 魔法武器拾取即同调（引擎简化：唯一魔法装备无需装备动作）
                magic = D.ITEMS[item].get("magic") or {}
                if magic and first and n > 0:
                    self.state.player.magic_attack_bonus += int(magic.get("attack_bonus", 0))
                    self.state.player.magic_damage_bonus += int(magic.get("damage_bonus", 0))
                    attuned.append(D.ITEMS[item]["name"])
        msg = f"✦ 获得 {int(gold)} 金币" + ("、" + "、".join(got) if got else "")
        if attuned:
            msg += f"（{('、'.join(attuned))} 已同调生效）"
        self.ui.system_line(msg)
        return {"ok": True}

    def _tool_start_encounter(self, encounter_id: str) -> dict:
        if self.harness_session is None:
            return {"ok": False, "error": "编排器未就绪"}
        return self.harness_session.launch_encounter(encounter_id)

    # ================================================================ PC
    def register_pc_tools(self, registry: ToolRegistry, pc: Character) -> None:
        registry.register(
            "say", f"以 {pc.name} 的口吻说一句话（中文，符合人设）。",
            {"type": "object",
             "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: self.ui.companion_line(pc.name, text))
        registry.register(
            "combat_action",
            "战斗中执行一个动作或附赠动作。动作类 action ∈ attack/cast/shove/grapple/"
            "escape_grapple/help/dodge/hide/disengage/move_away/use_item/breath_weapon/"
            "wild_shape/revert/lay_on_hands/action_surge；附赠动作类 second_wind/rage/"
            "flurry/martial_bonus/frenzy_attack/stunning_strike/bardic_inspiration。"
            "attack 需 weapon+target（5级武人自动获得额外攻击）；cast 需 spell+target"
            "（治疗真言/猎人印记等自动按附赠动作结算）；use_item 需 item；wild_shape 需 form"
            "（wolf/giant_badger/riding_horse/black_bear）；lay_on_hands 需 target+amount。",
            {"type": "object",
             "properties": {
                 "action": {"type": "string"},
                 "target": {"type": "string"}, "weapon": {"type": "string"},
                 "spell": {"type": "string"}, "slot_level": {"type": "number"},
                 "item": {"type": "string"}, "form": {"type": "string"},
                 "amount": {"type": "number"}},
             "required": ["action"]},
            lambda **kw: self._pc_combat_action(pc, **kw))
        registry.register(
            "explore_intent", "非战斗时声明一个探索意图（如搜查、攀爬、谈话对象）。",
            {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
            lambda text: {"ok": True, "intent": text})

    def _pc_combat_action(self, pc: Character, action: str, target: str = "",
                          weapon: str = "", spell: str = "", slot_level=0,
                          item: str = "", form: str = "", amount=0) -> dict:
        enc = self.encounter
        if enc is None:
            return {"ok": False, "error": "当前没有进行中的战斗"}
        rng = self.state.rng
        if action == "attack":
            t = enc.find(target)
            if t is None:
                return {"ok": False, "error": "找不到目标 " + target}
            if weapon and weapon not in pc.weapons:
                return {"ok": False, "error": "没有武器 " + weapon +
                        f"（拥有：{'、'.join(pc.weapons)}）"}
            return enc.attack_action(pc, t, weapon or None, rng)
        if action == "cast":
            sp = D.SPELLS.get(spell)
            if sp is None or (pc.klass not in sp["classes"]
                              and not pc.can_use_innate(spell)):
                return {"ok": False, "error": "不会法术 " + str(spell)}
            names = [n.strip() for n in target.replace("，", ",").split(",") if n.strip()] \
                if target else []
            return enc.cast(pc, spell, names, rng)
        if action == "shove":
            t = enc.find(target)
            return enc.do_shove(pc, t) if t else {"ok": False, "error": "找不到目标"}
        if action == "grapple":
            t = enc.find(target)
            return enc.do_grapple(pc, t) if t else {"ok": False, "error": "找不到目标"}
        if action == "escape_grapple":
            return enc.do_escape_grapple(pc)
        if action == "help":
            t = enc.find(target)
            if t is None:
                return {"ok": False, "error": "找不到目标 " + target}
            return enc.do_help(pc, t)
        if action == "dodge":
            return enc.do_dodge(pc)
        if action == "hide":
            return enc.do_hide(pc, rng)
        if action == "disengage":
            return enc.do_disengage(pc)
        if action == "move_away":
            return enc.move_away(pc)
        if action == "use_item":
            return enc.use_item(pc, item or "healing_potion")
        if action == "second_wind":
            if pc.klass != "fighter":
                return {"ok": False, "error": "只有战士会回气"}
            return enc.second_wind(pc, rng)
        if action == "action_surge":
            return enc.action_surge(pc)
        if action == "rage":
            return enc.start_rage(pc)
        if action == "flurry":
            t = enc.find(target)
            return enc.flurry_of_blows(pc, t) if t else {"ok": False, "error": "找不到目标"}
        if action == "martial_bonus":
            t = enc.find(target)
            return enc.martial_bonus_attack(pc, t) if t else {"ok": False, "error": "找不到目标"}
        if action == "frenzy_attack":
            t = enc.find(target)
            return enc.frenzy_attack(pc, t, pc.weapons[0] if pc.weapons else None) \
                if t else {"ok": False, "error": "找不到目标"}
        if action == "stunning_strike":
            t = enc.find(target)
            return enc.stunning_strike(pc, t) if t else {"ok": False, "error": "找不到目标"}
        if action == "bardic_inspiration":
            t = enc.find(target) if target else pc
            return enc.bardic_inspiration(pc, t)
        if action == "breath_weapon":
            t = enc.find(target)
            return enc.breath_weapon(pc, [t], rng) if t else {"ok": False, "error": "找不到目标"}
        if action == "wild_shape":
            return enc.wild_shape(pc, form)
        if action == "revert":
            return enc.revert_shape(pc)
        if action == "lay_on_hands":
            t = enc.find(target) if target else pc
            return enc.lay_on_hands(pc, t, int(amount or 5))
        return {"ok": False, "error": "未知动作 " + action}
