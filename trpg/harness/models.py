"""消息与响应协议（OpenAI 兼容格式的轻量封装）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

Role = str  # "system" | "user" | "assistant" | "tool"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict          # 已解析的 JSON 参数

    @classmethod
    def from_raw(cls, raw: dict) -> "ToolCall":
        args = raw.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {"_raw": args}
        return cls(id=raw.get("id", ""), name=raw.get("name", ""), arguments=args)

    def signature(self) -> str:
        return json.dumps([self.name, self.arguments], sort_keys=True, ensure_ascii=False)


@dataclass
class ModelResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)
    # 思考模式（DeepSeek V4 / GLM-5.x 等）返回的思维链。带 tools 的请求必须
    # 逐轮原样回传，否则 API 返回 400（见 adapter.chat / loop.run）。
    reasoning: str | None = None


def system(content: str) -> dict:
    return {"role": "system", "content": content}


def user(content: str) -> dict:
    return {"role": "user", "content": content}


def assistant(content: str | None = None, tool_calls: list[ToolCall] | None = None,
              reasoning: str | None = None) -> dict:
    """assistant 消息。

    ``reasoning``（思维链）在思考模式下必须随历史轮次回传：DeepSeek 的规则是
    「请求带 tools 时，历史轮次的 reasoning_content 均须回传，否则返回 400」。
    仅在非空时写入，避免给不支持该字段的供应商塞空键。
    """
    msg: dict = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if tool_calls:
        msg["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
            for tc in tool_calls
        ]
    return msg


def tool_result(call_id: str, name: str, payload) -> dict:
    return {
        "role": "tool", "tool_call_id": call_id, "name": name,
        "content": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
    }


def tool_schema(name: str, description: str, parameters: dict) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": description, "parameters": parameters}}
