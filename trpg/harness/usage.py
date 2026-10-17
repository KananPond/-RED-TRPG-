"""Token 用量记录：跨 Agent 汇总、按日聚合，并产出 GitHub 贡献图式的热力图数据。

设计要点：
- ``records``：逐次调用明细（供「最近调用」表），只保留最近 ``MAX_RECORDS`` 条；
- ``daily``：按日聚合（``YYYY-MM-DD`` → 计数），热力图与年度统计的**唯一**数据源，
  不随明细裁剪而丢失——一次游玩散落在几十天里也能画出完整的一年;
- 存储向前兼容：旧存档只有 ``records``，加载时自动回填 ``daily``。

价格：默认按「元 / 百万 token」给出参考价（可用环境变量覆盖），
只用于界面上的**估算花费**，不参与任何结算。
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_RECORDS = 2000
MAX_DAYS = 420                     # 保留约 14 个月，够画一整年 + 余量
STORAGE_VERSION = 2
HEATMAP_DAYS = 371                 # 53 周：GitHub 贡献图一屏的行数

PRICE_IN_DEFAULT = 2.0             # 元 / 百万 token（输入）
PRICE_OUT_DEFAULT = 3.0            # 元 / 百万 token（输出）

TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


# ---------------------------------------------------------------- 工具
def day_key(ts: float) -> str:
    """时间戳 → 本地日期字符串（YYYY-MM-DD）。"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _days_since_sunday(d: date) -> int:
    """周日起算的星期序号（周日 0 … 周六 6）。"""
    return (d.weekday() + 1) % 7


def prices() -> tuple[float, float]:
    """参考价（输入, 输出）：元 / 百万 token，可用环境变量覆盖。"""
    def _read(name: str, default: float) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return value if value >= 0 else default
    return _read("TRPG_PRICE_IN", PRICE_IN_DEFAULT), _read("TRPG_PRICE_OUT", PRICE_OUT_DEFAULT)


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """估算花费（元）。仅用于展示，四舍五入到 4 位小数。"""
    p_in, p_out = prices()
    cost = (prompt_tokens / 1_000_000) * p_in + (completion_tokens / 1_000_000) * p_out
    return round(cost, 4)


def _empty_day() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}


class TokenMeter:
    def __init__(self, path: str | Path = "saves/token_usage.json"):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.records: list[dict] = []
        self.daily: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------ 持久化
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("用量文件不是对象")
        except (OSError, ValueError):
            self.records, self.daily = [], {}
            return
        raw_records = data.get("records")
        self.records = [r for r in (raw_records or []) if isinstance(r, dict)][-MAX_RECORDS:]
        raw_daily = data.get("daily")
        if isinstance(raw_daily, dict) and raw_daily:
            self.daily = {str(k): self._norm_day(v) for k, v in raw_daily.items()}
        else:
            # 旧存档：用明细回填按日聚合
            self.daily = {}
            for rec in self.records:
                self._bump_daily(rec)

    @staticmethod
    def _norm_day(value) -> dict:
        day = _empty_day()
        if isinstance(value, dict):
            for key in TOKEN_KEYS:
                day[key] = max(0, int(value.get(key, 0) or 0))
            day["calls"] = max(0, int(value.get("calls", 0) or 0))
        return day

    def _save(self) -> None:
        self._prune_daily()
        payload = {
            "version": STORAGE_VERSION,
            "records": self.records[-MAX_RECORDS:],
            "daily": self.daily,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except OSError:
            pass

    def _prune_daily(self) -> None:
        if len(self.daily) <= MAX_DAYS:
            return
        keep = sorted(self.daily)[-MAX_DAYS:]
        self.daily = {k: self.daily[k] for k in keep}

    def _bump_daily(self, rec: dict) -> None:
        key = day_key(float(rec.get("ts") or time.time()))
        day = self.daily.setdefault(key, _empty_day())
        for field in TOKEN_KEYS:
            day[field] += int(rec.get(field, 0) or 0)
        day["calls"] += 1

    # ------------------------------------------------------------ 记账
    def record(self, agent: str, model: str, usage: dict, steps: int = 0,
               ts: float | None = None) -> dict:
        """usage 为 OpenAI usage 字典（prompt_tokens / completion_tokens / total_tokens）。

        ``ts`` 仅供测试与历史导入使用，缺省取当前时间。"""
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        total = int(usage.get("total_tokens", 0) or 0) or (prompt + completion)
        rec = {
            "ts": round(float(ts) if ts is not None else time.time(), 3),
            "agent": str(agent), "model": str(model), "steps": int(steps or 0),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }
        with self._lock:
            self.records.append(rec)
            self.records = self.records[-MAX_RECORDS:]
            self._bump_daily(rec)
            self._save()
        return rec

    def clear(self) -> None:
        """清空明细与按日聚合（热力图随之归零）。"""
        with self._lock:
            self.records = []
            self.daily = {}
            self._save()

    # ------------------------------------------------------------ 汇总
    def _snapshot(self) -> tuple[list[dict], dict[str, dict]]:
        with self._lock:
            return list(self.records), {k: dict(v) for k, v in self.daily.items()}

    def summary(self, recent: int = 100, days: int = HEATMAP_DAYS) -> dict:
        recs, daily = self._snapshot()
        totals = {"records": len(recs), "calls": 0, "prompt_tokens": 0,
                  "completion_tokens": 0, "total_tokens": 0}
        by_agent: dict[str, int] = {}
        by_model: dict[str, dict] = {}
        for r in recs:
            for key in TOKEN_KEYS:
                totals[key] += int(r.get(key, 0) or 0)
            agent = str(r.get("agent") or "?")
            by_agent[agent] = by_agent.get(agent, 0) + int(r.get("total_tokens", 0) or 0)
            model = str(r.get("model") or "?")
            slot = by_model.setdefault(model, {"prompt_tokens": 0,
                                               "completion_tokens": 0,
                                               "total_tokens": 0, "calls": 0})
            slot["prompt_tokens"] += int(r.get("prompt_tokens", 0) or 0)
            slot["completion_tokens"] += int(r.get("completion_tokens", 0) or 0)
            slot["total_tokens"] += int(r.get("total_tokens", 0) or 0)
            slot["calls"] += 1
        totals["calls"] = sum(d.get("calls", 0) for d in daily.values())

        today = date.today()
        heat = self.heatmap(days=days, daily=daily, today=today)
        p_in, p_out = prices()
        return {
            "totals": totals,
            "by_agent": by_agent,
            "by_model": by_model,
            "recent": list(reversed(recs[-max(1, recent):])),
            "heatmap": heat,
            "today": heat["today"],
            "last_7": self._window(daily, today, 7),
            "last_30": self._window(daily, today, 30),
            "all_daily": dict(sorted(daily.items())),
            "cost": {
                "cny": estimate_cost(totals["prompt_tokens"], totals["completion_tokens"]),
                "price_in": p_in, "price_out": p_out,
                "currency": "CNY",
                "note": f"按输入 ¥{p_in:g} / 输出 ¥{p_out:g} 每百万 token 估算",
            },
        }

    @staticmethod
    def _window(daily: dict[str, dict], today: date, days: int) -> dict:
        start = (today - timedelta(days=days - 1)).isoformat()
        out = {"days": days, "calls": 0, "prompt_tokens": 0,
               "completion_tokens": 0, "total_tokens": 0, "active_days": 0}
        for key, day in daily.items():
            if key < start:
                continue
            for field in TOKEN_KEYS:
                out[field] += int(day.get(field, 0) or 0)
            out["calls"] += int(day.get("calls", 0) or 0)
            if int(day.get("total_tokens", 0) or 0) > 0:
                out["active_days"] += 1
        return out

    # ------------------------------------------------------------ 热力图
    def heatmap(self, days: int = HEATMAP_DAYS, today: date | None = None,
                daily: dict[str, dict] | None = None) -> dict:
        """GitHub 贡献图布局：列 = 周，行 = 周日…周六。

        - ``weeks``：二维数组，每列 7 格，格为 ``None``（范围外）或
          ``{"d", "v", "p", "c", "n", "l"}``（日期/合计/输入/输出/调用/等级）；
        - ``months``：月份标签应落在哪一列；
        - ``levels``：等级阈值（按 sqrt 缩放，少量极端值不会把其余日子压成同一档）。
        """
        end = today or date.today()
        span = max(7, min(int(days or HEATMAP_DAYS), MAX_DAYS))
        start = end - timedelta(days=span - 1)
        first_sunday = start - timedelta(days=_days_since_sunday(start))
        last_saturday = end + timedelta(days=6 - _days_since_sunday(end))
        total_weeks = (last_saturday - first_sunday).days // 7 + 1
        daily = daily if daily is not None else self._snapshot()[1]

        cells: dict[str, dict] = {}
        values: list[int] = []
        for key, day in daily.items():
            iso = str(key)[:10]
            if not (start.isoformat() <= iso <= end.isoformat()):
                continue
            value = int(day.get("total_tokens", 0) or 0)
            cells[iso] = {
                "d": iso, "v": value,
                "p": int(day.get("prompt_tokens", 0) or 0),
                "c": int(day.get("completion_tokens", 0) or 0),
                "n": int(day.get("calls", 0) or 0),
            }
            if value > 0:
                values.append(value)

        top = max(values) if values else 0
        root_top = math.sqrt(top)
        for cell in cells.values():
            cell["l"] = _level(cell["v"], root_top)

        weeks: list[list[dict | None]] = []
        months: list[dict] = []
        seen_month: str | None = None
        for w in range(total_weeks):
            column: list[dict | None] = []
            for row in range(7):
                d = first_sunday + timedelta(days=w * 7 + row)
                if d < start or d > end:
                    column.append(None)
                    continue
                iso = d.isoformat()
                column.append(cells.get(iso) or {"d": iso, "v": 0, "p": 0, "c": 0,
                                                 "n": 0, "l": 0})
            weeks.append(column)
            label_day = first_sunday + timedelta(days=w * 7)
            if label_day <= end:
                label = f"{label_day.month}月"
                if label != seen_month:
                    months.append({"week": w, "label": label})
                    seen_month = label

        best = {"date": "", "total": 0}
        for iso, cell in cells.items():
            if cell["v"] > best["total"]:
                best = {"date": iso, "total": cell["v"]}

        return {
            "days": span,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "weeks": weeks,
            "months": months,
            "max": top,
            "total": sum(values),
            "active_days": len(values),
            "best": best,
            "streak": _streaks(cells, end)[0],
            "longest": _streaks(cells, end)[1],
            "today": cells.get(end.isoformat()) or {"d": end.isoformat(), "v": 0,
                                                    "p": 0, "c": 0, "n": 0, "l": 0},
            "unit": "tok",
        }


def _level(value: int, root_top: float) -> int:
    if value <= 0 or root_top <= 0:
        return 0
    ratio = math.sqrt(value) / root_top
    if ratio <= 0.25:
        return 1
    if ratio <= 0.5:
        return 2
    if ratio <= 0.75:
        return 3
    return 4


def _streaks(cells: dict[str, dict], end: date) -> tuple[int, int]:
    """返回（当前连续天数, 历史最长连续天数）。当天无用量时从昨天起算。"""
    current, longest, run = 0, 0, 0
    ordered = sorted(cells)
    prev: date | None = None
    for iso in ordered:
        value = int(cells[iso].get("v", 0) or 0)
        d = date.fromisoformat(iso)
        if value > 0:
            run = run + 1 if prev and (d - prev).days == 1 else 1
            longest = max(longest, run)
            prev = d
        else:
            run = 0
            prev = None
    cursor = end
    if not int(cells.get(end.isoformat(), {}).get("v", 0) or 0):
        cursor = end - timedelta(days=1)
    while True:
        cell = cells.get(cursor.isoformat())
        if not cell or int(cell.get("v", 0) or 0) <= 0:
            break
        current += 1
        cursor -= timedelta(days=1)
    return current, longest
