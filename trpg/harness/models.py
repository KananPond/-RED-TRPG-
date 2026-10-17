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
    # Responses API 的原始 reasoning item（含 id / summary / content /
    # encrypted_content）。纯文本不够：供应商会校验“是不是同一条思维链”，
    # 丢掉 id 就可能报 “reasoning_text ... must be passed back”。
    reasoning_items: list[dict] = field(default_factory=list)


def system(content: str) -> dict:
    return {"role": "system", "content": content}


def user(content: str) -> dict:
    return {"role": "user", "content": content}


def assistant(content: str | None = None, tool_calls: list[ToolCall] | None = None,
              reasoning: str | None = None,
              reasoning_items: list[dict] | None = None) -> dict:
    """assistant 消息。

    ``reasoning``（思维链）在思考模式下必须随历史轮次回传：DeepSeek 的规则是
    「请求带 tools 时，历史轮次的 reasoning_content 均须回传，否则返回 400」。
    ``reasoning_items`` 是 Responses 的原始 reasoning item，用于按原 id 回传；
    chat/completions 适配器会把它剥掉，只留 ``reasoning_content``。
    """
    msg: dict = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if reasoning_items:
        msg["reasoning_items"] = list(reasoning_items)
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
