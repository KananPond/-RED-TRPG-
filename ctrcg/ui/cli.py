"""终端 UI：ConsoleUI（人类玩家）与 AutoUI（--demo 自动演示）。"""

from __future__ import annotations

import sys


def _setup_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


class ConsoleUI:
    def __init__(self, input_fn=None, show_dice: bool = True):
        _setup_utf8()
        self.input_fn = input_fn or input
        self.show_dice = show_dice
        self.history: list[str] = []

    # ------------------------------------------------------------ 输出
    def _out(self, text: str) -> None:
        print(text)
        self.history.append(text)

    def dm_narration(self, text: str) -> dict:
        self._out(f"\n【DM】{text}")
        return {"ok": True}

    def npc_line(self, npc: str, text: str) -> dict:
        self._out(f"  ❖ {npc}：「{text}」")
        return {"ok": True}

    def companion_line(self, name: str, text: str, interrupt: bool = False) -> dict:
        mark = "（抢话）" if interrupt else ""
        self._out(f"  ◈ {name}{mark}：「{text}」")
        return {"ok": True}

    def check_line(self, result: dict, context: str = "") -> dict:
        verdict = "成功" if result.get("success") else "失败"
        label = result.get("check") or result.get("save") or ""
        self._out(f"  🎲 {result['who']} {label}检定（DC{result['dc']}）"
                  f"{result.get('detail', '')} → {verdict}"
                  + (f"（{context}）" if context else ""))
        return result

    def save_line(self, result: dict) -> dict:
        return self.check_line(result)

    def roll_event(self, ev: dict) -> None:
        """战斗骰子事件：终端下并入日志行（WebUI 会播放动画）。"""
        kind = ev.get("kind", "roll")
        d = ev.get("d20")
        if d is None:
            return
        tag = {"attack": "攻击", "save": "豁免", "death": "死亡豁免",
               "check": "检定"}.get(kind, "")
        self._out(f"  🎲 {ev.get('who', '')} {ev.get('label', '') or tag}"
                  f"：d20={d}（共 {ev.get('total', '?')}）")

    def system_line(self, text: str) -> dict:
        self._out(f"\n✦ {text}")
        return {"ok": True}

    def raw(self, text: str) -> None:
        self._out(text)

    def scene_header(self, act: int, act_title: str, location: str) -> None:
        self._out("\n" + "=" * 62)
        self._out(f"  第{act}幕 · {act_title.replace(f'第{act}幕：', '')}")
        self._out(f"  ◈ 场景：{location}")
        self._out("=" * 62)

    def status_board(self, lines: list[str]) -> None:
        self.last_status = list(lines)
        self._out("\n".join("  " + ln for ln in lines))

    # ------------------------------------------------------------ 输入
    def choose(self, prompt: str, options: list[str]) -> int:
        print(f"\n{prompt}")
        for i, opt in enumerate(options, 1):
            print(f"  [{i}] {opt}")
        while True:
            raw = self.input_fn("> ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            print("  请输入有效编号。")

    def free_input(self, prompt: str) -> str:
        return self.input_fn(prompt).strip()


class AutoUI(ConsoleUI):
    """自动演示/冒烟测试：按启发式自动选择，无需人工输入。"""

    def __init__(self):
        super().__init__(input_fn=lambda _p: "", show_dice=True)
        self.mode = "explore"   # explore | combat
        self.turn_count = 0
        self.last_status: list[str] = []

    def _own_hp(self, name: str) -> tuple[int, int] | None:
        for ln in self.last_status:
            if ln.strip().startswith("◆") and name in ln and "HP" in ln:
                try:
                    seg = ln.split("HP")[1].split(",")[0].strip()
                    cur, mx = seg.split("/")
                    return int(cur), int(mx)
                except (ValueError, IndexError):
                    return None
        return None

    def choose(self, prompt: str, options: list[str]) -> int:
        if prompt != getattr(self, "last_prompt", None):
            self.last_prompt = prompt
            self.rested = False
        # 战斗自保：残血优先回气（每场一次），再喝药
        if self.mode == "combat" and "攻击" in " ".join(options):
            hp = self._own_hp("凯尔")
            if hp and hp[0] < hp[1] * 0.5:
                if not getattr(self, "sw_used", False):
                    for i, opt in enumerate(options):
                        if "回气" in opt:
                            self.sw_used = True
                            return i
                for i, opt in enumerate(options):
                    if "喝" in opt:
                        return i
        # 探索：每个场景先休整一次再推进
        if self.mode == "explore" and not getattr(self, "rested", False):
            for i, opt in enumerate(options):
                if "队伍长休" in opt:
                    self.rested = True
                    return i
        # 队友倒地：优先喂药救起，其次稳定伤势
        for i, opt in enumerate(options):
            if "喂药" in opt:
                return i
        for i, opt in enumerate(options):
            if "稳定" in opt:
                return i
        # 优先级：攻击 > 前进/前往 > 检定 > 其余第一项
        for key in ("攻击", "前进", "前往", "调查", "施展", "帮助", "喝", "继续"):
            for i, opt in enumerate(options):
                if key in opt:
                    return i
        return 0

    def free_input(self, prompt: str) -> str:
        self.turn_count += 1
        if self.mode == "combat":
            return "攻击最近的敌人！"
        return "继续前进，留意四周。"
