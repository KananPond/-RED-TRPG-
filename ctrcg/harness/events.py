"""Session：带持久事件日志的对话会话。

不变量（借鉴 deepseek-harness）：模型可见 ⟺ 已记录。
任何送入模型的消息都能从事件日志重放重建。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class Session:
    def __init__(self, session_id: str, log_dir: str | Path | None = None):
        self.session_id = session_id
        self.events: list[dict] = []
        self._seq = 0
        self._file: Path | None = None
        if log_dir is not None:
            log_dir = Path(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            self._file = log_dir / f"{session_id}.jsonl"

    def log(self, etype: str, **payload) -> dict:
        self._seq += 1
        event = {"seq": self._seq, "ts": round(time.time(), 3), "type": etype, **payload}
        self.events.append(event)
        if self._file is not None:
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    # ------------------------------------------------------------ 便捷事件
    def log_message(self, msg: dict) -> dict:
        return self.log("message", message=msg)

    def log_tool_call(self, call) -> dict:
        return self.log("tool_call", id=call.id, name=call.name, arguments=call.arguments)

    def log_tool_result(self, call_id: str, name: str, result) -> dict:
        return self.log("tool_result", id=call_id, name=name, result=result)

    def log_final(self, agent_id: str, content: str, steps: int) -> dict:
        return self.log("agent_final", agent=agent_id, content=content, steps=steps)

    def log_guard(self, guard: str, detail: str) -> dict:
        return self.log("guard", guard=guard, detail=detail)

    # ------------------------------------------------------------ 重放
    def replay_messages(self) -> list[dict]:
        """从事件流重建消息序列（验证'模型可见⟺已记录'）。"""
        messages = [e["message"] for e in self.events if e["type"] == "message"]
        return messages

    def transcript(self) -> str:
        lines = []
        for e in self.events:
            if e["type"] == "message":
                m = e["message"]
                lines.append(f"[{m['role']}] {str(m.get('content'))[:400]}")
            elif e["type"] == "tool_call":
                lines.append(f"[tool_call] {e['name']}({json.dumps(e['arguments'], ensure_ascii=False)[:200]})")
            elif e["type"] == "tool_result":
                lines.append(f"[tool_result] {e['name']} → {json.dumps(e['result'], ensure_ascii=False)[:300]}")
            elif e["type"] == "agent_final":
                lines.append(f"[final:{e['agent']}] {e['content'][:400]}")
            elif e["type"] == "guard":
                lines.append(f"[guard:{e['guard']}] {e['detail']}")
        return "\n".join(lines)
