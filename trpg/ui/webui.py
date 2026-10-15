"""WebUI：与 ConsoleUI 同接口的 Web 适配器。

- 输出类方法把游戏事件推入事件总线（SSE 广播 + 历史重放）。
- choose / free_input 阻塞游戏线程，等待浏览器经 HTTP 提交答案。
- abort() 允许服务端安全终止一局（重开新局）。
"""

from __future__ import annotations

import itertools
import queue
import threading


def _num(value):
    """宽松取数：int/float 原样，其余（含 None）返回 None。"""
    return value if isinstance(value, (int, float)) else None


class AbortGame(Exception):
    """服务端要求终止当前对局（重开/关闭）。"""


class WebUI:
    def __init__(self):
        self._seq = itertools.count(1)
        self.history: list[dict] = []          # 全量事件（重连重放）
        self.subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._pending: dict | None = None      # 等待浏览器应答的请求
        self._command: str | None = None       # AI PC 旁听指令槽（非阻塞）
        self._cmd_event = threading.Event()    # 旁听指令到达信号
        self.aborted = False
        self._dice: dict[str, int] = {"rolls": 0, "crit": 0, "fumble": 0,
                                      "passed": 0, "failed": 0}

    # ------------------------------------------------------------ 事件总线
    def _emit(self, t: str, **payload) -> dict:
        if t in ("dice", "roll"):
            self._count_dice(payload.get("d10"),
                             payload.get("success", payload.get("hit")))
        ev = {"id": next(self._seq), "t": t, **payload}
        with self._lock:
            self.history.append(ev)
            subs = list(self.subscribers)
        for q in subs:
            q.put(ev)
        return ev

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    # ------------------------------------------------------------ 输出接口
    def dm_narration(self, text: str) -> dict:
        self._emit("narration", text=text)
        return {"ok": True}

    def npc_line(self, npc: str, text: str) -> dict:
        self._emit("npc", who=npc, text=text)
        return {"ok": True}

    def companion_line(self, name: str, text: str, interrupt: bool = False) -> dict:
        self._emit("companion", who=name, text=text, interrupt=bool(interrupt))
        return {"ok": True}

    def check_line(self, result: dict, context: str = "") -> dict:
        """技能检定骰子事件（RED §9：单颗 d10 + DV，暴击成功/失败各追加一颗）。"""
        label = self._label_cn(result.get("check") or result.get("save") or "")
        self._emit("dice", kind="check" if result.get("check") else "save",
                   detail=result.get("detail", ""),
                   context=context or None, **self._dice_payload(result, label))
        return result

    @staticmethod
    def _label_cn(label: str) -> str:
        """检定标签转中文（骰子动画标题用）；已是中文则原样返回。"""
        from ..rules import data as D
        if label in D.SKILLS:
            return D.SKILLS[label]["name"]
        return label

    @staticmethod
    def _dice_payload(ev: dict, label: str = "") -> dict:
        """把引擎骰子记录规整成 PORT_SPEC §9 的 SSE 载荷（字段恒定存在）。"""
        rolls = [int(v) for v in (ev.get("rolls") or []) if isinstance(v, int)]
        first = ev.get("d10")
        if not isinstance(first, int):
            first = rolls[0] if rolls else None
        value = _num(ev.get("dv", ev.get("dc")))
        return {
            "who": ev.get("who", ""),
            "label": label or ev.get("label") or ev.get("skill") or ev.get("weapon") or "",
            "rolls": rolls,
            "expr": ev.get("expr", "1d10"),
            "d10": first,
            "total": _num(ev.get("total")),
            "dv": value,
            "mods": list(ev.get("mods") or []),
            "success": bool(ev.get("success", ev.get("hit", False))),
            "crit_success": bool(ev.get("crit_success")),
            "crit_failure": bool(ev.get("crit_failure")),
            "luck_spent": int(ev.get("luck_spent") or 0),
            "damage": _num(ev.get("damage")) or 0,
            "armor": _num(ev.get("armor", ev.get("sp"))) or 0,
            "armor_left": _num(ev.get("armor_left", ev.get("sp"))),
            "crit_injury": ev.get("crit_injury"),
        }

    def roll_event(self, ev: dict) -> None:
        """战斗中的攻击/检定骰子事件（供前端播放掷骰动画）。"""
        self._emit("roll", **{**ev, **self._dice_payload(ev)})

    def dice_stats(self) -> dict:
        """本局骰子统计（终局卡用）。"""
        with self._lock:
            return dict(self._dice)

    def crit_injury(self, who: str, injury: dict, text: str = "") -> dict:
        """致命伤事件（RED：2d6 表）。injury 形如 {"roll","name","effects"}。"""
        self._emit("crit", who=who, name=(injury or {}).get("name", ""),
                   roll=(injury or {}).get("roll"),
                   text=text or (injury or {}).get("text", ""))
        return {"ok": True}

    def _count_dice(self, d10, success) -> None:
        if not isinstance(d10, int):
            return
        self._dice["rolls"] += 1
        if d10 == 10:
            self._dice["crit"] += 1
        elif d10 == 1:
            self._dice["fumble"] += 1
        if success:
            self._dice["passed"] += 1
        else:
            self._dice["failed"] += 1

    def save_line(self, result: dict) -> dict:
        return self.check_line(result)

    def system_line(self, text: str) -> dict:
        self._emit("system", text=text)
        return {"ok": True}

    def raw(self, text: str) -> None:
        if text.strip():
            self._emit("prose", text=text)

    def scene_header(self, act: int, act_title: str, location: str) -> None:
        self._emit("scene", act=act, title=act_title, location=location)

    def map_update(self, brief: dict | None) -> None:
        """战术地图全量快照（docs/battlemap-design.md §4）。

        每次棋子变动重发整张地图（客户端 latest-wins + CSS 过渡）；
        历史重放天然重建地图，无需增量事件。brief=None 表示本局无地图。"""
        self._emit("map", **(brief or {"grid": None}))

    def status_board(self, lines: list[str]) -> None:
        self._emit("board", lines=lines)

    # ------------------------------------------------------------ 阻塞式输入
    def choose(self, prompt: str, options: list[str]) -> int:
        idx = self._ask("choose", prompt, options)
        self._emit("choice_made", index=idx, label=options[idx])
        return idx

    def ask_roll(self, prompt: str) -> bool:
        """掷骰确认：轮到玩家掷骰时阻塞游戏线程，直到浏览器点击骰子按钮。

        返回 True（按钮语义只有"掷"）。终端 UI 无此方法——检定自动掷骰。"""
        self._ask("roll", prompt, ["掷骰子"])
        self._emit("choice_made", index=0, label="掷骰子")
        return True

    def free_input(self, prompt: str) -> str:
        return self._ask("input", prompt, None)

    def ask_map_move(self, prompt: str) -> str:
        """点图移动（docs/battlemap-design.md §5）：阻塞游戏线程，玩家在地图上
        点击落子后以 "x,y" 文本应答（前端把点击翻译成 /api/answer text）。
        终端 UI 无此方法——session 回退 free_input 输入坐标。"""
        return self._ask("map_move", prompt, None)

    # ------------------------------------------------------------ AI PC 旁听指令
    def post_command(self, text: str) -> dict:
        """写入旁听指令槽（/api/answer 在无 pending 且 AI PC 对局时转投此处）。
        读写全程持锁；poll_command 取走即清，不存在双消费。"""
        cmd = str(text or "").strip()
        with self._lock:
            self._command = cmd or None
        if cmd:
            self._cmd_event.set()
        return {"ok": True}

    def poll_command(self, timeout: float = 0.0) -> "str | None":
        """AI PC 探索循环每节拍调用（非阻塞）：取走并清空旁听指令。

        timeout>0 时最多等这么久（探索循环传 0.4，避免空转烧 CPU）；
        ConsoleUI/AutoUI 无此方法——控制器经 getattr 防御转发恒得 None。"""
        if timeout and timeout > 0:
            self._cmd_event.wait(timeout)
        with self._lock:
            cmd, self._command = self._command, None
            self._cmd_event.clear()
        return cmd

    def _ask(self, kind: str, prompt: str, options: list[str] | None):
        ev = threading.Event()
        pending = {"kind": kind, "prompt": prompt, "options": options or [],
                   "event": ev, "answer": None}
        with self._lock:
            if self.aborted:
                raise AbortGame()
            self._pending = pending
        self._emit("ask", kind=kind, prompt=prompt, options=options or [])
        ev.wait()
        with self._lock:
            answer = pending["answer"]
            aborted = self.aborted
        if aborted or answer is None:
            raise AbortGame()
        index, text = answer
        if kind == "choose":
            return index
        if kind == "roll":
            return True
        return text or ""

    # ------------------------------------------------------------ 应答与终止
    def resolve(self, *, index: int | None = None, text: str | None = None) -> tuple[bool, str | None]:
        """浏览器提交答案。返回 (是否成功, 错误信息)。"""
        with self._lock:
            p = self._pending
            if p is None:
                return False, "当前没有等待回答的请求"
            if p["kind"] in ("choose", "roll"):
                if index is None or not isinstance(index, int) \
                        or not (0 <= index < max(1, len(p["options"]))):
                    return False, "无效的选项编号"
            else:
                if text is None or not str(text).strip():
                    return False, "回复内容不能为空"
            p["answer"] = (index, text)
            # 立即摘下 pending 再唤醒：避免唤醒后到摘下前的空窗期内，
            # 客户端读到已作答的旧问题而答错题（双击/轮询竞态）
            self._pending = None
        p["event"].set()
        return True, None

    def abort(self) -> None:
        with self._lock:
            self.aborted = True
            p = self._pending
        if p is not None:
            p["event"].set()
        self._emit("game_over", reason="reset")

    def pending_view(self) -> dict | None:
        with self._lock:
            p = self._pending
            if p is None:
                return None
            return {"kind": p["kind"], "prompt": p["prompt"], "options": p["options"]}
