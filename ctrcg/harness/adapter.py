"""ModelAdapter 插件：把具体 LLM 端点与 harness 解耦。

- OpenAICompatibleAdapter：DeepSeek / 任何 OpenAI 兼容端点。
  端点与凭据均来自操作者自己的环境变量（见 config.py）。
  SSRF 加固：协议白名单 + 主机解析边界校验（默认阻断私网/环回/链路本地/
  保留地址，防 DNS rebinding 首跳）+ 重定向逐跳复检。
  需要连接本机模型服务（Ollama/LM Studio 等）时，操作者可显式设置
  CTRCG_LLM_ALLOW_PRIVATE=1 自担风险放行。
- ScriptedAdapter：固定脚本/回调，用于测试与离线演示。
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

from .models import ModelResponse, ToolCall

_ALLOWED_SCHEMES = {"http", "https"}


def _allow_private() -> bool:
    return os.environ.get("CTRCG_LLM_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes")


def _assert_host_allowed(host: str) -> None:
    """解析主机名的全部地址，阻断私网/环回/链路本地/保留地址。"""
    if _allow_private():
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"LLM 端点主机无法解析：{host}（{e}）")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise ValueError(
                f"LLM 端点解析到受限地址 {ip}（私网/环回/链路本地）已阻断。"
                f"如需连接本机模型服务，请显式设置 CTRCG_LLM_ALLOW_PRIVATE=1。")


def _validate_endpoint(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"LLM 端点协议必须是 http/https：{parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("LLM 端点缺少主机名")
    if parsed.username or parsed.password:
        raise ValueError("LLM 端点不应携带用户信息（凭据请走 Authorization 头）")
    _assert_host_allowed(parsed.hostname)


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向目标逐跳复检，防止被重定向到受限地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(str(newurl))
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
            raise urllib.error.URLError(f"重定向目标不合法：{newurl}")
        _assert_host_allowed(parsed.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PinnedRedirectHandler)


class ModelAdapter(ABC):
    name = "base"

    @abstractmethod
    def chat(self, model: str, messages: list[dict], tools: list[dict] | None,
             temperature: float, max_tokens: int) -> ModelResponse: ...


class OpenAICompatibleAdapter(ModelAdapter):
    """标准 /chat/completions 客户端。api_key 只经构造参数传入（来自 env）。"""

    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, timeout: float = 90.0,
                 max_retries: int = 3, backoff: float = 2.0):
        _validate_endpoint(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None,
             temperature: float, max_tokens: int) -> ModelResponse:
        payload: dict = {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._post(body)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                status = getattr(e, "code", None)
                transient = status is None or status in (408, 429, 500, 502, 503, 504)
                if not transient or attempt == self.max_retries - 1:
                    break
                time.sleep(self.backoff * (attempt + 1))
        kind = type(last_err).__name__
        detail = str(getattr(last_err, "reason", last_err))
        raise RuntimeError(f"LLM 请求失败（{kind}）：{detail}")

    def _post(self, body: bytes) -> ModelResponse:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            })
        with _OPENER.open(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = data["choices"][0]
        msg = choice.get("message", {})
        tool_calls = [ToolCall.from_raw({
            "id": tc.get("id", f"call_{i}"),
            "name": tc.get("function", {}).get("name", ""),
            "arguments": tc.get("function", {}).get("arguments", "{}"),
        }) for i, tc in enumerate(msg.get("tool_calls") or [])]
        return ModelResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            usage=data.get("usage", {}),
        )


class ScriptedAdapter(ModelAdapter):
    """脚本适配器：按序返回预设响应；脚本耗尽后调用 fallback 或抛错。
    handler(messages, tools) -> ModelResponse 亦可。"""

    name = "scripted"

    def __init__(self, script: list[ModelResponse] | None = None,
                 handler=None):
        self.script = list(script or [])
        self.handler = handler
        self.calls: list[dict] = []  # 审计：收到的请求
        self._i = 0

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None,
             temperature: float, max_tokens: int) -> ModelResponse:
        self.calls.append({"model": model, "messages": [dict(m) for m in messages],
                           "tools": tools})
        if self._i < len(self.script):
            resp = self.script[self._i]
            self._i += 1
            return resp
        if self.handler is not None:
            return self.handler(messages, tools)
        raise RuntimeError("ScriptedAdapter 脚本耗尽且未提供 handler")
