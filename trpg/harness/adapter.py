"""ModelAdapter 插件：把具体 LLM 端点与 harness 解耦。

- OpenAICompatibleAdapter：DeepSeek / 任何 OpenAI 兼容端点。
  端点与凭据均来自操作者自己的环境变量（见 config.py）。
  SSRF 加固：协议白名单 + 主机解析边界校验（默认阻断私网/环回/链路本地/
  保留地址，防 DNS rebinding 首跳）+ 重定向逐跳复检。
  需要连接本机模型服务（Ollama/LM Studio 等）时，操作者可显式设置
  TRPG_LLM_ALLOW_PRIVATE=1 自担风险放行。
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
    return os.environ.get("TRPG_LLM_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes")


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
                f"如需连接本机模型服务，请显式设置 TRPG_LLM_ALLOW_PRIVATE=1。")


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


def validate_endpoint(url: str) -> None:
    """公开的端点校验（balance 等只读旁路复用同一套 SSRF 边界）。"""
    _validate_endpoint(url)


def open_url(req: urllib.request.Request, timeout: float = 30.0):
    """公开的 opener 入口：复用逐跳重定向复检。"""
    return _OPENER.open(req, timeout=timeout)


# ---------------------------------------------------------------- 端点整形
_ENDPOINT_TAILS = ("/chat/completions", "/completions", "/responses")


def normalize_base_url(base_url: str) -> str:
    """把用户手填的地址整形成「可再拼 /chat/completions 或 /responses」的 base_url。

    常见误填：整段端点（``.../v1/chat/completions``、``.../v1/responses``）、
    结尾斜杠、前后空格。这里只做与供应商无关的整形——``/v1``（或智谱的
    ``/api/paas/v4``）保留，因为它属于路径前缀，去掉会 404。
    """
    url = str(base_url or "").strip().rstrip("/")
    changed = True
    while changed:
        changed = False
        for tail in _ENDPOINT_TAILS:
            if url.endswith(tail):
                url = url[: -len(tail)].rstrip("/")
                changed = True
    return url


def _status_hint(status: int | None) -> str:
    """按 HTTP 状态码给出可操作的下一步（用于界面提示）。"""
    return {
        400: "请求被拒绝：多为模型名不存在、参数不被该模型支持，或思考模式未正确回传思维链。",
        401: "认证失败：密钥无效或已过期，请到供应商控制台重新生成。",
        402: "余额不足：请充值后重试（数据看板可查余额）。",
        403: "无权访问：该密钥没有此模型/接口的权限。",
        404: "接口不存在：API 地址写错了（多半是少了 /v1 或多了路径），请用「拉取模型列表」验证。",
        422: "参数错误：请检查模型名与请求参数。",
        429: "触发限速：稍后再试，或在设置里换一个模型。",
        500: "供应商服务器故障：稍后重试。",
        503: "供应商繁忙：稍后重试。",
    }.get(status, "请对照供应商文档检查 API 地址、模型名与密钥。")


def _error_message(err: urllib.error.HTTPError) -> str:
    """从错误响应体里取供应商给的 message（OpenAI 兼容格式），取不到就返回空。"""
    try:
        raw = err.read().decode("utf-8", "replace")[:800]
    except Exception:  # noqa: BLE001 —— 读不到响应体就只报状态码
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()[:200]
    if isinstance(data, dict):
        inner = data.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])[:400]
        if isinstance(inner, str):
            return inner[:400]
        if data.get("message"):
            return str(data["message"])[:400]
    return raw.strip()[:200]


class ModelAdapter(ABC):
    name = "base"

    @abstractmethod
    def chat(self, model: str, messages: list[dict], tools: list[dict] | None,
             temperature: float, max_tokens: int) -> ModelResponse: ...


class OpenAICompatibleAdapter(ModelAdapter):
    """标准 /chat/completions 客户端。api_key 只经构造参数传入（来自 env）。

    ``thinking`` / ``reasoning_effort``：思考模型的开关与强度（DeepSeek V4、
    GLM-5.x 等）。只对声明了 ``supports_thinking`` 的供应商下发，避免给老接口
    塞未知参数被 400。``thinking="off"`` 时显式发 ``{"type": "disabled"}``——
    DeepSeek V4 起思考是**默认开启**的，不发这个键关不掉。
    """

    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, timeout: float = 90.0,
                 max_retries: int = 3, backoff: float = 2.0,
                 thinking: str = "", reasoning_effort: str = ""):
        base_url = normalize_base_url(base_url)
        _validate_endpoint(base_url)
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.thinking = str(thinking or "").strip().lower()
        self.reasoning_effort = str(reasoning_effort or "").strip().lower()

    # ---------------------------------------------------------- 请求体
    def _extras(self) -> dict:
        out: dict = {}
        if self.thinking in ("on", "off", "enabled", "disabled"):
            on = self.thinking in ("on", "enabled")
            out["thinking"] = {"type": "enabled" if on else "disabled"}
        if self.reasoning_effort:
            out["reasoning_effort"] = self.reasoning_effort
        return out

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None,
             temperature: float, max_tokens: int) -> ModelResponse:
        payload: dict = {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        payload.update(self._extras())
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._post(body)
            except urllib.error.HTTPError as e:
                # 4xx 除了 408/429 都是配置问题，重试没意义——直接抛，附带原因
                last_err = e
                detail = _error_message(e)
                if e.code not in (408, 429) or attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"LLM 请求失败（HTTP {e.code}）：{detail or e.reason}"
                        f"｜{_status_hint(e.code)}") from e
                time.sleep(self.backoff * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt == self.max_retries - 1:
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
        if not isinstance(data, dict) or not data.get("choices"):
            raise RuntimeError(f"LLM 响应格式异常：{str(data)[:200]}")
        choice = data["choices"][0]
        msg = choice.get("message", {})
        tool_calls = [ToolCall.from_raw({
            "id": tc.get("id", f"call_{i}"),
            "name": tc.get("function", {}).get("name", ""),
            "arguments": tc.get("function", {}).get("arguments", "{}"),
        }) for i, tc in enumerate(msg.get("tool_calls") or [])]
        content = msg.get("content")
        finish = choice.get("finish_reason", "stop")
        if (not content) and not tool_calls and finish == "length":
            raise RuntimeError(
                "模型输出被 max_tokens 截断且没有正文：思考模式会先消耗输出预算。"
                "请在设置里关闭思考模式，或调大 max_tokens。")
        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=data.get("usage", {}),
            # 思考模式的思维链：必须随历史回传（带 tools 时 DeepSeek 会校验）
            reasoning=msg.get("reasoning_content") or msg.get("reasoning"),
        )

    # ---------------------------------------------------------- 模型列表
    def list_models(self, timeout: float | None = None) -> list[str]:
        """``GET {base}/models``：用当前密钥列出账号真正可用的模型。

        供应商兼容性：OpenAI 风格返回 ``{"data": [{"id": ...}]}``；也有返回
        ``{"models": [...]}`` 的网关，两种都认。"""
        req = urllib.request.Request(
            f"{self.base_url}/models", method="GET",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Accept": "application/json"})
        with _OPENER.open(req, timeout=timeout or min(self.timeout, 20.0)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("data") if isinstance(data, dict) else data
        if rows is None and isinstance(data, dict):
            rows = data.get("models")
        ids: list[str] = []
        for row in rows or []:
            if isinstance(row, str):
                ids.append(row)
            elif isinstance(row, dict):
                value = row.get("id") or row.get("model") or row.get("name")
                if value:
                    ids.append(str(value))
        return sorted(dict.fromkeys(ids))


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
