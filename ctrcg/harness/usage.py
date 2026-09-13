"""Token 用量记录：跨 Agent 汇总并持久化到 saves/token_usage.json。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MAX_RECORDS = 500


class TokenMeter:
    def __init__(self, path: str | Path = "saves/token_usage.json"):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.records: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = data.get("records", [])[-MAX_RECORDS:]
        except (OSError, json.JSONDecodeError):
            self.records = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"records": self.records[-MAX_RECORDS:]},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError:
            pass

    def record(self, agent: str, model: str, usage: dict, steps: int = 0) -> dict:
        """usage 为 OpenAI usage 字典（prompt_tokens / completion_tokens / total_tokens）。"""
        rec = {
            "ts": round(time.time(), 3),
            "agent": agent, "model": model, "steps": steps,
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
        with self._lock:
            self.records.append(rec)
            self.records = self.records[-MAX_RECORDS:]
            self._save()
        return rec

    def clear(self) -> None:
        with self._lock:
            self.records = []
            self._save()

    def summary(self, recent: int = 100) -> dict:
        with self._lock:
            recs = list(self.records)
        totals = {"records": len(recs), "prompt_tokens": 0,
                  "completion_tokens": 0, "total_tokens": 0}
        by_agent: dict[str, int] = {}
        for r in recs:
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                totals[k] += r.get(k, 0)
            by_agent[r["agent"]] = by_agent.get(r["agent"], 0) + r.get("total_tokens", 0)
        return {"totals": totals, "by_agent": by_agent,
                "recent": list(reversed(recs[-recent:]))}
