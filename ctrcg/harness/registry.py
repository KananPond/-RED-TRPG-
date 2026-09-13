"""ToolRegistry 插件：工具注册、JSON Schema 校验、调用与错误回填。

register() 返回 disposer（借鉴 deepseek-harness 的注册即注销语义）。
工具异常不抛入模型循环，而是作为结构化错误结果回填给模型自愈。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

_TYPE_MAP = {"string": str, "number": (int, float), "integer": int,
             "boolean": bool, "array": list, "object": dict}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema 子集：{type, properties, required}
    fn: Callable              # 接收 **kwargs，返回 JSON 可序列化对象

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, parameters: dict,
                 fn: Callable) -> Callable[[], None]:
        if name in self._tools:
            raise ValueError(f"工具重复注册: {name}")
        self._tools[name] = Tool(name, description, parameters, fn)

        def disposer():
            self._tools.pop(name, None)
        return disposer

    def get(self, name: str) -> Tool:
        return self._tools.get(name)

    def schemas(self, names: list[str] | None = None) -> list[dict]:
        tools = [self._tools[n] for n in names if n in self._tools] if names \
            else list(self._tools.values())
        return [t.schema() for t in tools]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ------------------------------------------------------------ 校验与调用
    def _validate(self, tool: Tool, args: dict) -> str | None:
        if not isinstance(args, dict):
            return "参数必须是 JSON 对象"
        props = tool.parameters.get("properties", {})
        for key in tool.parameters.get("required", []):
            if key not in args:
                return "缺少必填参数: " + key
        for key, val in args.items():
            if key.startswith("_"):
                return "非法参数名: " + key
            spec = props.get(key)
            if spec and spec.get("type") in _TYPE_MAP:
                py = _TYPE_MAP[spec["type"]]
                if spec["type"] == "number" and isinstance(val, bool):
                    return "参数 " + key + " 应为 number"
                if not isinstance(val, py):
                    return "参数 " + key + " 类型应为 " + str(spec["type"])
        return None

    def invoke(self, name: str, args: dict) -> dict:
        """调用工具。永远返回 JSON 对象；错误以 ok=False 回填给模型自愈。"""
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": "未知工具: " + name}
        err = self._validate(tool, args or {})
        if err:
            return {"ok": False, "error": "参数校验失败：" + err}
        try:
            result = tool.fn(**(args or {}))
        except Exception as e:  # noqa: BLE001 —— 错误必须回填而非中断循环
            return {"ok": False, "error": type(e).__name__ + ": " + str(e)}
        if not isinstance(result, dict):
            result = {"ok": True, "result": _jsonable(result)}
        return result


def _jsonable(obj):
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except (TypeError, ValueError):
        return str(obj)
