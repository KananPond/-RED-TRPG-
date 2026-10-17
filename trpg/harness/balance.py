"""查询 LLM 供应商的账户余额（只读接口）。

安全边界与 ``adapter`` 一致：
- 端点协议白名单 + 主机解析校验（阻断私网/环回/链路本地/保留地址）；
- 密钥只进 ``Authorization`` 头，绝不回显、绝不落盘；
- 只读 GET，任何异常都归一成 ``{"ok": False, ...}``，不向界面抛栈。

各家的余额接口并不统一，这里按供应商分派：
- DeepSeek：``GET {root}/user/balance`` → ``balance_infos[]``
- Kimi / Moonshot：``GET {base}/users/me/balance`` → ``data.available_balance``
- 其它（GLM / MiniMax / 自定义）：官方没有公开的余额接口，
  可用 ``TRPG_BALANCE_URL`` 指定一个 OpenAI 兼容网关的余额地址；
  未指定时返回「不支持」，界面据此只展示本地 token 用量。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import LLM_PROVIDERS, LLMConfig
from .adapter import open_url, validate_endpoint

TIMEOUT = 10.0
CURRENCY_SIGNS = {"CNY": "¥", "RMB": "¥", "USD": "$", "EUR": "€", "HKD": "HK$"}

_PROVIDER_PATHS = {
    # provider: (是否以 API 根地址拼接, 路径)
    "deepseek": (True, "/user/balance"),
    "kimi": (False, "/users/me/balance"),
}


# ---------------------------------------------------------------- 端点
def _strip_suffix(url: str, suffix: str) -> str:
    return url[: -len(suffix)] if url.endswith(suffix) else url


def balance_endpoint(cfg: LLMConfig | None) -> str | None:
    """按供应商推导余额查询地址；不支持时返回 None。"""
    override = os.environ.get("TRPG_BALANCE_URL", "").strip()
    if override:
        return override
    if cfg is None:
        return None
    provider = (cfg.provider or "").lower()
    base = (cfg.base_url or "").rstrip("/")
    if provider == "deepseek":
        return _strip_suffix(base, "/v1") + _PROVIDER_PATHS["deepseek"][1]
    if provider == "kimi":
        # Kimi 的余额接口挂在 /v1 下
        return (base if base.endswith("/v1") else base + "/v1") + _PROVIDER_PATHS["kimi"][1]
    return None


# ---------------------------------------------------------------- 解析
def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_key(payload, keys: tuple[str, ...], depth: int = 0):
    """在嵌套字典里按优先级找第一个可转成数字的键（自定义网关兜底）。"""
    if depth > 3 or not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload:
            value = _num(payload[key])
            if value is not None:
                return value
    for value in payload.values():
        if isinstance(value, dict):
            found = _find_key(value, keys, depth + 1)
            if found is not None:
                return found
        elif isinstance(value, list):
            for item in value:
                found = _find_key(item, keys, depth + 1)
                if found is not None:
                    return found
    return None


def _find_text(payload, keys: tuple[str, ...], depth: int = 0) -> str | None:
    """在嵌套字典里找第一个非空字符串（币种等文本字段的兜底）。"""
    if depth > 3 or not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in payload.values():
        if isinstance(value, dict):
            found = _find_text(value, keys, depth + 1)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                found = _find_text(item, keys, depth + 1)
                if found:
                    return found
    return None


def parse_balance(provider: str, payload: dict) -> dict:
    """把各家响应归一成 ``{currency, total, available, items}``。"""
    payload = payload if isinstance(payload, dict) else {}
    items: list[dict] = []
    currency = "CNY"
    total: float | None = None
    available: bool | None = None

    infos = payload.get("balance_infos")
    if isinstance(infos, list) and infos:
        info = next((i for i in infos if isinstance(i, dict)
                     and str(i.get("currency", "")).upper() in ("CNY", "RMB")), infos[0])
        info = info if isinstance(info, dict) else {}
        currency = str(info.get("currency") or "CNY").upper()
        total = _num(info.get("total_balance"))
        granted = _num(info.get("granted_balance"))
        topped = _num(info.get("topped_up_balance"))
        if granted is not None:
            items.append({"label": "赠送余额", "value": granted})
        if topped is not None:
            items.append({"label": "充值余额", "value": topped})
        if "is_available" in payload:
            available = bool(payload.get("is_available"))
    else:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        total = _num(data.get("available_balance"))
        if total is None:
            total = _num(data.get("total_balance"))
        if total is None:
            total = _find_key(data, ("available_balance", "total_balance", "balance",
                                     "remaining", "credits", "available"))
        voucher = _num(data.get("voucher_balance"))
        cash = _num(data.get("cash_balance"))
        if voucher is not None:
            items.append({"label": "代金券", "value": voucher})
        if cash is not None:
            items.append({"label": "现金", "value": cash})
        if "status" in payload:
            available = bool(payload.get("status"))
        found_currency = _find_text(data, ("currency", "currency_code", "unit"))
        if found_currency:
            currency = found_currency.upper()

    if total is None:
        raise ValueError("响应中没有可识别的余额字段")
    return {
        "currency": currency,
        "currency_sign": CURRENCY_SIGNS.get(currency, ""),
        "total": round(total, 4),
        "available": available,
        "items": items,
    }


# ---------------------------------------------------------------- 请求
def _provider_name(provider: str) -> str:
    preset = LLM_PROVIDERS.get(provider) or {}
    return str(preset.get("name") or provider or "未知供应商")


def _http_error_message(err: urllib.error.HTTPError) -> tuple[str, str]:
    if err.code in (401, 403):
        return (f"密钥无效或无余额查询权限（HTTP {err.code}）",
                "请在「设置 → API 配置」确认密钥；部分供应商的余额接口需要单独的只读权限。")
    if err.code == 404:
        return ("该供应商未提供余额查询接口（HTTP 404）",
                "可设置环境变量 TRPG_BALANCE_URL 指向一个兼容的余额地址。")
    if err.code == 429:
        return ("查询过于频繁（HTTP 429）", "稍后再试。")
    return (f"余额接口返回 HTTP {err.code}", "稍后重试，或改用本地 token 用量观察消耗。")


def fetch_balance(cfg: LLMConfig | None) -> dict:
    """查询余额。永不抛异常：失败时返回 ``ok=False`` 与可读的原因。"""
    if cfg is None:
        return {"ok": False, "provider": "", "provider_name": "未接入",
                "error": "尚未接入 LLM",
                "hint": "到「设置 → API 配置」填入供应商与密钥后即可查询账户余额。"}
    provider = (cfg.provider or "").lower()
    endpoint = balance_endpoint(cfg)
    meta = {"provider": provider, "provider_name": _provider_name(provider),
            "model": cfg.model}
    if not endpoint:
        return {**meta, "ok": False, "unsupported": True,
                "error": f"{_provider_name(provider)} 没有公开的余额查询接口",
                "hint": "可设置环境变量 TRPG_BALANCE_URL 指向兼容的余额地址；"
                        "余额不可用时仍可用热力图观察 token 消耗。"}
    try:
        validate_endpoint(endpoint)
    except ValueError as exc:
        return {**meta, "ok": False, "error": f"余额端点校验失败：{exc}"}
    host = urllib.parse.urlparse(endpoint).hostname or ""
    req = urllib.request.Request(endpoint, method="GET", headers={
        "Authorization": f"Bearer {cfg.api_key}",
        "Accept": "application/json",
    })
    try:
        with open_url(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        message, hint = _http_error_message(exc)
        return {**meta, "ok": False, "error": message, "hint": hint}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        detail = str(getattr(exc, "reason", exc))
        return {**meta, "ok": False, "error": f"无法连接 {host}：{detail}",
                "hint": "检查网络，或在「设置」里确认 API 地址。"}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {**meta, "ok": False, "error": "余额接口返回了非 JSON 内容"}
    try:
        parsed = parse_balance(provider, payload)
    except ValueError as exc:
        return {**meta, "ok": False, "error": f"余额响应无法识别：{exc}"}
    return {**meta, "ok": True, "ts": round(time.time(), 3),
            "endpoint_host": host, **parsed}
