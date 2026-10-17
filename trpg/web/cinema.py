"""CinemaUI —— 纯 AI 剧场（全 AI 跑团）的 Web 适配层。

它不是一个新的规则引擎，而是把已有的 ``GameSession`` 包成一场**没有人类玩家**
的演出：

- 产品路径 ``strict_ai=True``：所有 PC 行动由 ``PartyDirector`` 消化；
  ``choose / free_input / ask_map_move`` 一旦漏到 UI 就直接报错，本地导演已禁用。
  （未接 LLM 时 ``/api/start {cinema:true}`` 会直接拒绝开局。）
- 每一条事件先过「暂停闸门」与节奏器，再进入 SSE，因此暂停是真的暂停，
  不是前端把字幕藏起来；
- 通过 ``PsycheEngine`` 追加 ``inner`` 事件（技能之声），这是纯本地叙事，
  不参与结算；
- 观众可以发「低语」：注入下一名 PC Agent 的上下文；低语不能直接改数值，
  也不保证被采纳。

设计边界：不改 ``GameSession`` 的任何数值路径；所有新事件都是向后兼容的
SSE 载荷。旧的 WebUI 行为逐字节不变。
"""

from __future__ import annotations

import hashlib
import random
import threading
import time

from ..ui.webui import AbortGame, WebUI
from ..game.psyche import PsycheEngine

#: 每种事件之后的停顿（秒，1× 速度）。这些停顿是演出节奏，不是逻辑等待。
_PACE: dict[str, float] = {
    "scene": 1.25,
    "narration": 0.55,
    "prose": 0.35,
    "npc": 0.45,
    "companion": 0.35,
    "dice": 0.55,
    "roll": 0.28,
    "crit": 0.8,
    "system": 0.15,
    "board": 0.15,
    "map": 0.1,
    "inner": 0.2,
    "cinema_choice": 0.3,
    "cinema_action": 0.35,
    "agent_state": 0.0,
    "whisper": 0.0,
    "choice_made": 0.0,
    "game_over": 0.0,
}

#: 场外动作模板。AIPC 不可用时，玩家角色靠这些动作继续推进（不会卡住）。
_SCENE_ACTIONS: list[tuple[str, str]] = [
    ("安全屋", "{name} 把一次性手机按在桌上，开了免提。{name} 没有坐下。"),
    ("路上", "{name} 让 Mover 把车停在两条街外。{name} 先下车，站在雨里看了一会儿巷口。"),
    ("小巷", "{name} 没有伸手去接箱子。{name} 先看那个男人的手，再看他的身后。"),
    ("小房间", "{name} 蹲下去，把声音压得很低：「Lazlo，看着我。谁干的？」"),
    ("仓库", "{name} 贴着集装箱边缘走，向 Mover 打了个向下的手势。"),
    ("尾声", "{name} 点了一支烟，没有抽。{name} 看着天边那层脏兮兮的橙色。"),
]
_GENERIC_ACTION = "{name} 没有急着开口。{name} 先看了一圈，把所有人的位置记下来。"

#: 旧离线剧场里主角的开场台词（只作测试兼容，不进入产品路径）。
#: 接入 LLM 后由 AIPC 真正扮演，这些模板全部短路，避免抢角色的声音。
_PLAYER_LINES: dict[str, str] = {
    "south_night_city_loft": "都别出声。让它响完。",
    "prep_and_drive": "车停远点。我不想让人看见我们是一起来的。",
    "heywood_meet": "箱子先别接。钥匙呢？",
    "lazlo_rescue": "Lazlo，看着我。谁干的？",
    "warehouse_approach": "我数到三。能谈就谈，不能谈就散。",
    "aftermath": "钱先别分。先把名字从名单上划掉。",
}

#: 换场时的走位动作（由引擎真的切换了场景后才会发出；不是无中生有）。
#: 用 ``{name}`` 而不是代词：五个预设角色性别不同，剧场不该替他们改口。
_PLAYER_MOVES: dict[str, str] = {
    "south_night_city_loft": "{name} 起身，把手机揣进夹克内袋，先走到门边。",
    "prep_and_drive": "车在两条街外停下。{name} 推开车门，踩进积水里。",
    "heywood_meet": "{name} 沿着墙根往前走，脚步压得很轻。",
    "lazlo_rescue": "{name} 跨过地上的血，走到椅子前蹲下。",
    "warehouse_approach": "{name} 贴着集装箱的边缘移动，停在卷帘门的阴影里。",
    "aftermath": "{name} 走到码头边缘，停下，让海风吹散身上的硝烟味。",
}

# 低语关键词 → 选项关键词。只做「软引导」，不直接改状态。
_WHISPER_HINTS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("别打", "不要打", "谈", "谈判", "说服", "别开火", "冷静"), ("说服", "对峙", "谈判", "前进")),
    (("撤", "跑", "退", "离开"), ("撤退", "离开", "退")),
    (("救", "稳住", "别让他死", "止血"), ("稳定", "急救", "医疗", "救援")),
    (("看", "观察", "查", "检查", "搜", "线索"), ("感知", "察觉", "调查", "检定机会", "推理")),
    (("钱", "箱子", "假钞", "账"), ("伪造", "技术", "检定机会", "钱")),
    (("打", "开枪", "杀", "崩", "干"), ("攻击", "瞄准射击", "斗殴")),
    (("跑路", "藏", "潜行", "绕"), ("潜行", "躲", "移动", "前往")),
]


class CinemaUI(WebUI):
    """纯 AI 剧场 UI：所有 PC 由 Agent 自主行动；另加暂停/速度/低语/技能之声。

    ``strict_ai`` 是产品路径：产品里没有本地导演，所有阻塞式选项/自由输入都必须
    由 Agent 决策链消化；一旦漏到 UI 就直接报错，避免悄悄退回剧本模式。
    """

    is_cinema = True

    def __init__(self, player, module=None, *, llm_active: bool = False,
                 speed: float = 1.0, strict_ai: bool = False):
        super().__init__()
        self.player = player
        self.module = module
        self.llm_active = bool(llm_active)
        # 纯 AI 剧场：产品路径绝不允许本地导演接管 choose/free_input。
        self.strict_ai = bool(strict_ai)
        self._speed = self._clamp_speed(speed)
        self._paused = False
        self._gate = threading.Condition()
        self._aborting = False
        self._whisper: str | None = None
        self._whisper_lock = threading.Lock()
        self._scene_id = ""
        self._scene_location = ""
        self._choice_no = 0
        self._narration_no = 0
        self._npc_no = 0
        self._roll_no = 0
        self._map_brief: dict | None = None
        self._did_open_move = False
        self._started_at = time.time()
        seed = hashlib.sha1(
            f"{getattr(player, 'name', '')}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:16]
        self.psyche = PsycheEngine(player, seed=seed)
        self._resume_event = threading.Event()
        self._resume_event.set()

    # ------------------------------------------------------------ 状态
    @staticmethod
    def _clamp_speed(value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.0
        # 0.6× 给读字的人；2.4× 给想快速过剧情的人；再快就只剩残影了。
        return max(0.6, min(2.4, v))

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def paused(self) -> bool:
        with self._gate:
            return self._paused

    def set_speed(self, value) -> float:
        self._speed = self._clamp_speed(value)
        return self._speed

    def set_paused(self, paused: bool) -> bool:
        with self._gate:
            self._paused = bool(paused)
            if self._paused:
                self._resume_event.clear()
            else:
                self._resume_event.set()
                self._gate.notify_all()
            state, speed = self._paused, self._speed
        # 暂停/继续也记进事件流；这里绕过闸门，否则 pause 请求会被自己挡住。
        WebUI._emit(self, "cinema_status", paused=state, speed=speed)
        return state

    def abort(self) -> None:
        """中止对局：先放行被暂停闸门挡住的游戏线程，再走既有 abort。"""
        with self._gate:
            self._paused = False
            self._aborting = True
            self._resume_event.set()
            self._gate.notify_all()
        try:
            super().abort()
        finally:
            self._aborting = False

    def cinema_payload(self) -> dict:
        return {
            "active": True,
            "paused": self.paused,
            "speed": self.speed,
            "player": getattr(self.player, "name", ""),
            "llm": self.llm_active,
            "strict_ai": self.strict_ai,
            "autopilot": "llm" if (self.llm_active or self.strict_ai) else "local",
            "started_at": round(self._started_at),
            "psyche": self.psyche.payload(),
            "scene_id": self._scene_id,
            "location": self._scene_location,
        }

    # ------------------------------------------------------------ 暂停/节奏
    def _wait_gate(self) -> None:
        while True:
            with self._gate:
                if self.aborted and not self._aborting:
                    raise AbortGame()
                if not self._paused:
                    return
                self._resume_event.wait(0.25)

    def _emit(self, t: str, **payload) -> dict:
        self._wait_gate()
        delay = _PACE.get(t, 0.12) / max(0.6, self._speed)
        if delay > 0:
            # 分片睡眠：暂停发生在长停顿中间时，不必等完整段结束才响应
            end = time.monotonic() + delay
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._wait_gate()
                time.sleep(min(left, 0.08))
        return super()._emit(t, **payload)

    # ------------------------------------------------------------ 场景/叙事
    def scene_header(self, act: int, act_title: str, location: str) -> None:
        self._scene_location = str(location or "")
        self._scene_id = self._scene_for_location(self._scene_location)
        self._did_open_move = False
        self.psyche.set_scene(self._scene_id)
        super().scene_header(act, act_title, location)
        line = self.psyche.scene_line()
        if line:
            self._emit("inner", **line)
        # 旧离线剧场：给主角一句开场台词。纯 AI 剧场里 Agent 自己说，不能抢话。
        if not self.llm_active and not self.strict_ai:
            text = _PLAYER_LINES.get(self._scene_id)
            if text:
                self.companion_line(str(getattr(self.player, "name", "")), text)
            move = _PLAYER_MOVES.get(self._scene_id)
            if move:
                self._emit("cinema_action",
                           text=move.format(name=str(getattr(self.player, "name", "TA"))),
                           prompt="")

    def _scene_for_location(self, location: str) -> str:
        scenes = list(getattr(self.module, "scenes", []) or [])
        for s in scenes:
            if str(s.get("location") or "") == location:
                return str(s.get("id") or "")
        for s in scenes:
            loc = str(s.get("location") or "")
            if loc and (loc in location or location in loc):
                return str(s.get("id") or "")
        return ""

    def dm_narration(self, text: str) -> dict:
        out = super().dm_narration(text)
        self._narration_no += 1
        # 旁白很长时，技能之声只在有「可读的细节」时插一句，避免每段都抢戏。
        if self._narration_no % 3 == 2 or any(
                k in str(text or "") for k in ("枪", "血", "门", "眼睛", "沉默", "电话")):
            line = self.psyche.narration_line(text)
            if line:
                self._emit("inner", **line)
        return out

    def npc_line(self, npc: str, text: str) -> dict:
        out = super().npc_line(npc, text)
        self._npc_no += 1
        if self._npc_no % 2 == 0:
            line = self.psyche.npc_line(npc, text)
            if line:
                self._emit("inner", **line)
        return out

    def companion_line(self, name: str, text: str, interrupt: bool = False) -> dict:
        out = super().companion_line(name, text, interrupt)
        # 玩家角色的 AI 发言不追加读心；同伴说重话时偶尔让技能接一句。
        if str(name) != str(getattr(self.player, "name", "")) and any(
                k in str(text or "") for k in ("死", "枪", "钱", "Lazlo", "警察")):
            line = self.psyche.combat_line(name, text)
            if line:
                self._emit("inner", **line)
        return out

    # ------------------------------------------------------------ 检定/战斗
    def check_line(self, result: dict, context: str = "") -> dict:
        out = super().check_line(result, context)
        skill = str(result.get("check") or result.get("save") or "")
        success = bool(result.get("success", result.get("hit", False)))
        if skill == "save":
            skill = "concentration"
        elif not skill:
            skill = "perception"
        line = self.psyche.check_line(skill, success, str(result.get("detail") or ""))
        if line:
            self._emit("inner", **line)
        return out

    def roll_event(self, ev: dict) -> None:
        super().roll_event(ev)
        self._roll_no += 1
        if self._roll_no % 3 == 0:
            line = self.psyche.combat_line(str(ev.get("who") or ""),
                                           str(ev.get("detail") or ev.get("label") or ""))
            if line:
                self._emit("inner", **line)

    def crit_injury(self, who: str, injury: dict, text: str = "") -> dict:
        out = super().crit_injury(who, injury, text)
        line = self.psyche.crit_line(who)
        if line:
            self._emit("inner", **line)
        return out

    # ------------------------------------------------------------ 阻塞输入
    def _strict_block(self, what: str) -> None:
        if self.strict_ai:
            raise RuntimeError(
                f"纯 AI 剧场不应阻塞在 {what} 上：本地导演已禁用，"
                "请检查 LLM / PartyDirector 是否接管了这一拍。")

    def choose(self, prompt: str, options: list[str]) -> int:
        self._strict_block("choose")
        opts = [str(o) for o in (options or [])]
        idx = self._decide(prompt, opts)
        self._choice_no += 1
        label = opts[idx] if 0 <= idx < len(opts) else ""
        minor = self._is_minor_choice(str(prompt or ""))
        self._emit("cinema_choice", prompt=str(prompt or ""), options=opts,
                   selected=idx, label=label, reason=self._reason(label, prompt),
                   number=self._choice_no, minor=minor)
        # 兼容旧前端/测试的 choice_made 语义；cinema_choice 才是剧场主事件
        self._emit("choice_made", index=idx, label=label)
        return idx

    @staticmethod
    def _is_minor_choice(prompt: str) -> bool:
        """武器/目标/IP 等引擎子菜单不占满屏幕，只在状态条里带过。"""
        return any(k in prompt for k in (
            "选择武器", "选择目标", "给哪把", "处理谁的", "提升",
            "改进点", "使用什么", "推迟到", "武器的第二发", "的回合"))

    def free_input(self, prompt: str) -> str:
        self._strict_block("free_input")
        text = self._free_action(prompt)
        self._emit("cinema_action", text=text, prompt=str(prompt or ""))
        return text

    def ask_roll(self, prompt: str) -> bool:
        # 剧场没有「点按钮掷骰」这一步；让引擎直接掷，骰子仍由引擎结果驱动。
        return True

    def map_update(self, brief: dict | None) -> None:
        self._map_brief = brief or None
        super().map_update(brief)

    def ask_map_move(self, prompt: str) -> str:
        """网格战斗的自动落子：朝最近的敌人走一格；无路可走则取消。

        纯 AI 剧场里主角走位由 PC Agent 的 combat_action 完成，这里只保留
        给旧离线剧场/单元测试的兜底；strict_ai 下直接报错。
        """
        self._strict_block("ask_map_move")
        brief = self._map_brief or {}
        grid = brief.get("grid")
        if not grid or len(grid) != 2:
            return ""
        try:
            cw, ch = int(grid[0]), int(grid[1])
        except (TypeError, ValueError):
            return ""
        tokens = brief.get("tokens") or {}
        me = None
        enemies = []
        for t in tokens.values():
            if not isinstance(t, dict):
                continue
            cell = t.get("cell")
            if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
                continue
            if t.get("kind") == "pc" and t.get("name") == getattr(self.player, "name", ""):
                me = (int(cell[0]), int(cell[1]))
            elif t.get("kind") in ("monster", "npc"):
                enemies.append((int(cell[0]), int(cell[1])))
        if me is None:
            return ""
        obstacles = {(int(c[0]), int(c[1])) for c in (brief.get("obstacles") or [])
                     if isinstance(c, (list, tuple)) and len(c) == 2}
        occupied = {(int(t["cell"][0]), int(t["cell"][1]))
                    for t in tokens.values()
                    if isinstance(t, dict) and isinstance(t.get("cell"), (list, tuple))
                    and len(t.get("cell")) == 2}
        target = min(enemies, key=lambda e: max(abs(e[0] - me[0]), abs(e[1] - me[1]))) \
            if enemies else (cw // 2, ch // 2)

        def dist(c):
            return max(abs(c[0] - target[0]), abs(c[1] - target[1]))

        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                c = (me[0] + dx, me[1] + dy)
                if not (0 <= c[0] < cw and 0 <= c[1] < ch):
                    continue
                if c in obstacles or c in occupied:
                    continue
                candidates.append(c)
        if not candidates:
            return ""
        # 优先靠近目标，平手时优先横移（保持队形，视觉上也更像「走位」）
        candidates.sort(key=lambda c: (dist(c), abs(c[1] - me[1]), c[0]))
        return f"{candidates[0][0]},{candidates[0][1]}"

    # ------------------------------------------------------------ 低语
    def whisper(self, text: str) -> dict:
        t = str(text or "").strip()[:240]
        if not t:
            return {"ok": False, "error": "低语是空的"}
        with self._whisper_lock:
            self._whisper = t
        # 不经过 _emit 的暂停闸门：观众在暂停时也应该能低语，HTTP 不能卡住。
        WebUI._emit(self, "whisper", text=t, to=str(getattr(self.player, "name", "")))
        return {"ok": True}

    def take_whisper(self) -> str:
        with self._whisper_lock:
            text, self._whisper = self._whisper, None
        return text or ""

    # ------------------------------------------------------------ Agent 状态
    def agent_thinking(self, who: str, label: str = "正在思考") -> None:
        self._emit("agent_state", who=str(who), state="thinking", label=str(label))

    def agent_idle(self, who: str) -> None:
        self._emit("agent_state", who=str(who), state="idle", label="")

    # ------------------------------------------------------------ 旧本地导演（仅测试兼容）
    def _decide(self, prompt: str, opts: list[str]) -> int:
        """启发式自动选项：不会阻塞，不会选「退出」，尽量把戏演完。"""
        if not opts:
            return 0
        low = str(prompt or "")
        joined = " ".join(opts)
        # 0) IP 是桌面成长环节，不是剧场高潮：能稍后就稍后，避免把演出拖进
        #    「先升哪个技能」的菜单里。玩家想亲自规划可以退出剧场走建卡模式。
        if "改进点" in low or low.startswith("提升哪一类技能"):
            for key in ("稍后再花", "（返回）", "返回"):
                for i, o in enumerate(opts):
                    if key in o:
                        return i
        # 1) 观众低语优先：只做关键词匹配；匹配不到就正常走。
        #    武器/目标/IP 等内部子菜单不消耗低语，留给真正有叙事后果的选择。
        hint = "" if self._is_minor_choice(low) else self.take_whisper()
        if hint:
            for keys, wants in _WHISPER_HINTS:
                if not any(k in hint for k in keys):
                    continue
                for i, o in enumerate(opts):
                    if any(w in o for w in wants):
                        return i
                break
        # 2) 武器/目标子菜单：优先有子弹的武器、集火最虚弱的敌人。
        if "选择武器" in low or ("武器" in low and "弹" in joined):
            for i, o in enumerate(opts):
                if "弹 0/" not in o:
                    return i
            for i, o in enumerate(opts):
                if "近战" in o or "斗殴" in o or "徒手" in o:
                    return i
        if "选择目标" in low:
            import re as _re
            best = None
            for i, o in enumerate(opts):
                m = _re.search(r"PW\s*(\d+)", o)
                if not m:
                    continue
                score = int(m.group(1))
                if best is None or score < best[0]:
                    best = (score, i)
            if best is not None:
                return best[1]
        if "给哪把武器装填" in low:
            for i, o in enumerate(opts):
                if "弹 0/" in o:
                    return i
        # 3) 保命/救援永远优先。
        for key in ("稳定（", "急救固定（", "治疗致命伤"):
            for i, o in enumerate(opts):
                if o.startswith(key):
                    return i
        # 4) 战斗：先打，再处理装填/移动；重伤时引擎会把「撤退」排在最前，
        #    但剧场优先让角色做出有戏的选择，不选「查看/结束回合」。
        if "的回合" in low or "攻击" in joined:
            for key in ("攻击", "瞄准射击", "斗殴", "攻击（"):
                for i, o in enumerate(opts):
                    if o.startswith(key):
                        return i
            if "装填" in joined:
                return opts.index("装填")
            for key in ("移动/靠近", "靠近", "起身", "使用物品"):
                for i, o in enumerate(opts):
                    if o.startswith(key):
                        return i
        # 5) 探索：先把本场景的检定机会演完（失败也是戏），再推进。
        for i, o in enumerate(opts):
            if o.startswith("检定机会"):
                return i
        for key in ("前进（", "前往下一场景", "前进"):
            for i, o in enumerate(opts):
                if o.startswith(key):
                    return i
        for key in ("休整一天", "夜市场", "对峙（"):
            for i, o in enumerate(opts):
                if o.startswith(key):
                    return i
        # 6) 排除纯 UI/退出项，尽量选第一个有叙事后果的。
        for i, o in enumerate(opts):
            if not any(o.startswith(k) for k in
                       ("查看状态", "查看背包", "查看义体", "存档", "退出游戏",
                        "（结束回合）", "（不必了）", "（返回）")):
                return i
        return 0

    def _free_action(self, prompt: str) -> str:
        name = str(getattr(self.player, "name", "TA"))
        p = str(prompt or "")
        if any(k in p for k in ("回合", "攻击", "战斗", "掩体")):
            return "换弹，找掩体，等下一个枪口抬起来。"
        if "说什么" in p:
            return "别急着数钱。先告诉我，谁让你来的。"
        for key, tpl in _SCENE_ACTIONS:
            if key in self._scene_location or key in p:
                return tpl.format(name=name)
        return _GENERIC_ACTION.format(name=name)

    @staticmethod
    def _reason(label: str, prompt: str) -> str:
        if label.startswith(("稳定", "急救")):
            return "先救人。夜之城不缺死人，缺的是肯蹲下去的人。"
        if label.startswith(("前进", "前往")):
            return "再等下去只会更糟。让脚替脑子做一次决定。"
        if label.startswith("检定机会"):
            return "先相信自己的眼睛——或者至少，先试一次。"
        if label.startswith(("攻击", "瞄准", "斗殴")):
            return "对话结束了。剩下的交给枪口和反应。"
        if label.startswith(("撤退", "离开")):
            return "活下来比赢重要。至少今晚是这样。"
        if label.startswith(("休整", "夜市")):
            return "身体需要一点时间。城市不会因为你们喘口气就停下。"
        if "的回合" in str(prompt or ""):
            return "最直接的那种答案。"
        return "让直觉先走一步，理由随后再补。"


__all__ = ["CinemaUI"]
