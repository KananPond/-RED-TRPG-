"""ModelAdapter 插件：把具体 LLM 端点与 harness 解耦。

- OpenAICompatibleAdapter：DeepSeek / 任何 OpenAI 兼容端点。
  端点与凭据均来自操作者自己的环境变量（见 config.py）。
  SSRF 加固：协议白名单 + 主机解析边界校验（默认阻断私网/环回/链路本地/
  保留地址，防 DNS rebinding 首跳）+ 重定向逐跳复检。
  需要连接本机模型服务（Ollama/LM Studio 等）时，操作者可显式设置
  TRPG_LLM_ALLOW_PRIVATE=1 自担风险放行。
- 出网代理**默认直连**：进程常继承「给别人准备的」HTTP(S)_PROXY（终端工具、
  策略代理、已失效的 VPN 代理），带上它只会把请求送进一条不认识的隧道
  （典型症状 ``Tunnel connection failed: 403 Forbidden``，请求根本没到供应商）。
  需要代理时用 TRPG_LLM_PROXY 显式声明：direct（默认）/ env / 代理地址；
  env 模式下隧道失败还会自动用直连重试一次。
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


# ---------------------------------------------------------------- 出网代理
# 默认**直连**供应商端点：进程里的 HTTP_PROXY / HTTPS_PROXY 通常是给别的工具
# 准备的（终端代理、策略代理、VPN 退出后残留的配置），urllib 会照单全收，把
# 请求塞进一条不认识的隧道。典型症状：
#     LLM 请求失败（URLError）：Tunnel connection failed: 403 Forbidden
# 这条 403 来自代理，请求根本没到供应商——所以「密钥/模型/协议」怎么调都没用。
#
# 需要代理时显式声明（改完重启服务）：
#   TRPG_LLM_PROXY=direct                   直连，忽略环境/系统代理（默认）
#   TRPG_LLM_PROXY=env                      沿用环境变量 + 系统（注册表）代理
#   TRPG_LLM_PROXY=http://127.0.0.1:7890    只走这个代理（host:port 也认）
PROXY_ENV = "TRPG_LLM_PROXY"
_DIRECT_WORDS = frozenset({"", "direct", "none", "off", "no", "0", "false",
                           "disable", "disabled"})
_ENV_WORDS = frozenset({"env", "environment", "system", "auto", "default",
                        "inherit", "1", "true", "yes", "on"})
# 这些字样说明失败发生在代理隧道层（而不是供应商那一头）
_PROXY_FAILURE_HINTS = ("tunnel connection failed", "cannot connect to proxy",
                        "proxy error", "proxyerror", "cannot connect to the proxy",
                        "proxy authentication", "407 proxy")


def proxy_setting() -> tuple[str, str]:
    """解析 TRPG_LLM_PROXY → (mode, address)。

    mode ∈ {"direct", "env", "explicit"}；address 只有 explicit 模式非空。
    """
    raw = os.environ.get(PROXY_ENV, "").strip()
    lowered = raw.lower()
    if lowered in _DIRECT_WORDS:
        return "direct", ""
    if lowered in _ENV_WORDS:
        return "env", ""
    address = raw if "://" in raw else f"http://{raw}"   # 允许只写 host:port
    return "explicit", address


def _env_proxies() -> dict[str, str]:
    """环境变量 + 系统（注册表）里当前生效的代理；仅用于展示与诊断。"""
    try:
        return {str(k).lower(): str(v) for k, v in urllib.request.getproxies().items()}
    except Exception:  # noqa: BLE001 —— 读不到代理设置不是错误
        return {}


def _proxy_label(proxies: dict[str, str]) -> str:
    return proxies.get("https") or proxies.get("http") or ""


def network_info() -> dict:
    """当前出网方式（设置页展示 + 排错用）。"""
    mode, address = proxy_setting()
    proxies = _env_proxies()
    found = _proxy_label(proxies)
    if mode == "direct":
        return {"mode": "direct", "label": "直连（不走代理）", "address": "",
                # 环境里有代理但被忽略：正是「Tunnel 403」那类故障的来源，
                # 如实显示出来，用户才知道该改哪一个开关
                "ignored_env_proxy": found, "fallback_to_direct": False}
    if mode == "env":
        return {"mode": "env",
                "label": f"沿用系统/环境代理（{found}）" if found
                         else "沿用系统/环境代理（当前未检测到）",
                "address": found, "ignored_env_proxy": "",
                "fallback_to_direct": True}
    return {"mode": "explicit", "label": f"经指定代理（{address}）",
            "address": address, "ignored_env_proxy": "", "fallback_to_direct": False}


def _build_opener(proxy: urllib.request.ProxyHandler):
    return urllib.request.build_opener(proxy, _PinnedRedirectHandler)


def _build_openers(mode: str, address: str):
    """按模式构建 (主 opener, 回退 opener 或 None)。

    回退只在「代理是继承来的」时候启用：环境代理常常已失效（VPN 关掉、终端退出），
    隧道失败说明请求没到供应商，直连重试一次不会重复计费。显式指定的代理不回落
    ——用户既然点名了，失败就该如实报错。
    """
    if mode == "direct":
        return _build_opener(urllib.request.ProxyHandler({})), None
    if mode == "explicit":
        return _build_opener(urllib.request.ProxyHandler(
            {"http": address, "https": address})), None
    return (_build_opener(urllib.request.ProxyHandler()),
            _build_opener(urllib.request.ProxyHandler({})))


_OPENER_KEY: tuple[str, str] = proxy_setting()
_OPENER, _FALLBACK_OPENER = _build_openers(*_OPENER_KEY)


def _current_openers():
    """TRPG_LLM_PROXY 变了就重建（测试与热改环境变量都不必重启进程）。"""
    global _OPENER, _FALLBACK_OPENER, _OPENER_KEY
    key = proxy_setting()
    if key != _OPENER_KEY or _OPENER is None:
        _OPENER_KEY = key
        _OPENER, _FALLBACK_OPENER = _build_openers(*key)
    return _OPENER, _FALLBACK_OPENER


def _is_proxy_failure(err: BaseException) -> bool:
    reason = str(getattr(err, "reason", err)).lower()
    return any(hint in reason for hint in _PROXY_FAILURE_HINTS)


def validate_endpoint(url: str) -> None:
    """公开的端点校验（balance 等只读旁路复用同一套 SSRF 边界）。"""
    _validate_endpoint(url)


def open_url(req: urllib.request.Request, timeout: float = 30.0):
    """公开的 opener 入口：逐跳重定向复检 + 代理隧道失败时直连重试。"""
    opener, fallback = _current_openers()
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise                       # 供应商真实回了 4xx/5xx：不是网络问题
    except urllib.error.URLError as e:
        if fallback is None or not _is_proxy_failure(e):
            raise
        return fallback.open(req, timeout=timeout)


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


def _tool_pair_error(detail: str) -> bool:
    """是不是「工具调用 ↔ 结果没配对」的 400（DeepSeek Responses 的严格相邻校验）。"""
    lowered = str(detail or "").lower()
    return ("no tool output found" in lowered
            or "no tool call found" in lowered)


def _tool_pair_hint(detail: str) -> str:
    """识别「工具调用 ↔ 结果没配对」的 400，给出可操作的解释。"""
    if _tool_pair_error(detail):
        return ("工具调用和结果没配对（历史里有半截工具链：工具执行中断、或上下文"
                "被截断切在调用与结果之间）。适配器已会自动修补这类历史；"
                "若仍反复出现，把接口协议切到 Chat Completions 或重开一局。")
    return ""


def _status_hint(status: int | None, detail: str = "") -> str:
    """按 HTTP 状态码给出可操作的下一步（用于界面提示）。"""
    pair = _tool_pair_hint(detail)
    if status == 400 and pair:
        return pair
    return {
        400: "请求被拒绝：多为模型名不存在、参数不被该模型支持，或思考模式未正确回传思维链。",
        401: "认证失败：密钥无效或已过期，请到供应商控制台重新生成。",
        402: "余额不足：请充值后重试（数据看板可查余额）。",
        403: "无权访问：该密钥没有此模型/接口的权限。",
        404: "接口不存在：API 地址写错了（多半是少了 /v1 或多了路径），请用「拉取模型列表」验证。",
        407: "代理要求认证（407）：请求被代理拦在了门外。把 TRPG_LLM_PROXY 设为 "
             "direct 直连，或换一个不需要认证的代理后重启服务。",
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


#: 思考模式的最低输出预算。思维链也算输出 token，而本项目里 NPC 台词只有
#: 300、随身 Agent 500、DM 1200 的 cap——思考一开，思维链先把小 cap 吃光，
#: 正文还没开始写就被截断（界面表现：模型输出被 max_tokens 截断且没有正文）。
#: 所以 thinking 打开时给一个下限兜底；`max_tokens` 是上限不是账单，抬上去
#: 只允许模型写得更完整，不会凭空多花。
THINKING_MIN_TOKENS = 1024


def effective_max_tokens(max_tokens: int, thinking: str) -> int:
    """思考模式下的实际输出预算（不低于 ``THINKING_MIN_TOKENS``）。"""
    try:
        budget = int(max_tokens)
    except (TypeError, ValueError):
        budget = 0
    if str(thinking or "").strip().lower() in ("on", "enabled"):
        return max(budget, THINKING_MIN_TOKENS)
    return budget


_TOOL_ROLES = ("tool", "function")


def sanitize_tool_messages(messages: list[dict]) -> list[dict]:
    """保证「工具调用 ↔ 工具结果」成对，丢掉半截工具链。

    两条现实路径会留下缺口：工具执行中途抛异常/进程中断（assistant 的
    ``tool_calls`` 已进历史、结果没写），以及上下文压缩把一对消息切到两边。
    严格校验的供应商会直接拒绝：

    - DeepSeek Responses：``400 No tool output found for tool call call_01_…``
    - chat/completions：「tool 消息必须紧跟 tool_calls」

    这里统一修形：有结果的调用保留、缺结果的调用剔除；没有对应调用的 tool
    消息丢弃；一条 assistant 的调用全被剔除时只留正文（没正文就整条丢掉）。
    """
    out: list[dict] = []
    i, n = 0, len(messages)
    while i < n:
        msg = messages[i]
        if msg.get("role") in _TOOL_ROLES:
            i += 1                      # 孤儿结果：没有调用可配
            continue
        calls = msg.get("tool_calls") or []
        if not calls:
            out.append(msg)
            i += 1
            continue
        i += 1
        results: dict[str, dict] = {}
        while i < n and messages[i].get("role") in _TOOL_ROLES:
            results.setdefault(str(messages[i].get("tool_call_id") or ""), messages[i])
            i += 1
        kept = [c for c in calls if str(c.get("id") or "") in results]
        if kept:
            paired = dict(msg)
            paired["tool_calls"] = kept
            out.append(paired)
            out.extend(results[str(c.get("id") or "")] for c in kept)
        elif msg.get("content"):
            out.append({k: v for k, v in msg.items() if k != "tool_calls"})
    return out


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
        # Responses 的原始 reasoning item 是另一个协议的字段；chat 通道只认
        # reasoning_content，必须剥掉，否则严格网关会 400。
        messages = [{k: v for k, v in m.items() if k != "reasoning_items"}
                    for m in messages]
        messages = sanitize_tool_messages(messages)      # 修掉半截工具链
        payload: dict = {
            "model": model, "messages": messages,
            "temperature": temperature,
            # 思考模式的思维链也占输出预算：给短任务（NPC 台词 300）兜个底
            "max_tokens": effective_max_tokens(max_tokens, self.thinking),
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
                        f"｜{_status_hint(e.code, detail)}") from e
                time.sleep(self.backoff * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt == self.max_retries - 1:
                    break
                time.sleep(self.backoff * (attempt + 1))
        kind = type(last_err).__name__
        detail = str(getattr(last_err, "reason", last_err))
        proxy_hint = ("｜代理在隧道层就把请求挡了（请求没到供应商）：把 TRPG_LLM_PROXY "
                      "设为 direct 可直连，或换一个可用的代理地址后重启服务。"
                      if last_err is not None and _is_proxy_failure(last_err) else "")
        raise RuntimeError(f"LLM 请求失败（{kind}）：{detail}{proxy_hint}")

    def _post(self, body: bytes) -> ModelResponse:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            })
        with open_url(req, timeout=self.timeout) as resp:
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
        with open_url(req, timeout=timeout or min(self.timeout, 20.0)) as resp:
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
