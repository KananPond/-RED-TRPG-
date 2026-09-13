"""WebUI：与 ConsoleUI 同接口的 Web 适配器。

- 输出类方法把游戏事件推入事件总线（SSE 广播 + 历史重放）。
- choose / free_input 阻塞游戏线程，等待浏览器经 HTTP 提交答案。
- abort() 允许服务端安全终止一局（重开新局）。
"""

from __future__ import annotations

import itertools
import queue
import threading


class AbortGame(Exception):
    """服务端要求终止当前对局（重开/关闭）。"""


class WebUI:
    def __init__(self):
        self._seq = itertools.count(1)
        self.history: list[dict] = []          # 全量事件（重连重放）
        self.subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._pending: dict | None = None      # 等待浏览器应答的请求
        self.aborted = False

    # ------------------------------------------------------------ 事件总线
    def _emit(self, t: str, **payload) -> dict:
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

    def companion_line(self, name: str, text: str) -> dict:
        self._emit("companion", who=name, text=text)
        return {"ok": True}

    def check_line(self, result: dict, context: str = "") -> dict:
        self._emit("dice", who=result.get("who", ""),
                   label=result.get("check") or result.get("save") or "",
                   dc=result.get("dc"), detail=result.get("detail", ""),
                   success=bool(result.get("success")), context=context or None)
        return result

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

    def status_board(self, lines: list[str]) -> None:
        self._emit("board", lines=lines)

    # ------------------------------------------------------------ 阻塞式输入
    def choose(self, prompt: str, options: list[str]) -> int:
        idx = self._ask("choose", prompt, options)
        self._emit("choice_made", index=idx, label=options[idx])
        return idx

    def free_input(self, prompt: str) -> str:
        return self._ask("input", prompt, None)

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
        return index if kind == "choose" else (text or "")

    # ------------------------------------------------------------ 应答与终止
    def resolve(self, *, index: int | None = None, text: str | None = None) -> tuple[bool, str | None]:
        """浏览器提交答案。返回 (是否成功, 错误信息)。"""
        with self._lock:
            p = self._pending
            if p is None:
                return False, "当前没有等待回答的请求"
            if p["kind"] == "choose":
                if index is None or not isinstance(index, int) or not (0 <= index < len(p["options"])):
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
