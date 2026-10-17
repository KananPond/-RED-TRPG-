"""OpenAI **Responses API**（`POST {base}/responses`）适配器。

为什么需要它：DeepSeek / Kimi / 智谱都已提供 Responses 端点，而本项目的 Agent
循环产生的是 chat/completions 风格的消息（assistant + tool_calls + tool 结果）。
本模块负责两件事，且**只做协议转换、不做业务判断**：

1. **出站**：把 chat 风格消息列表折叠成 Responses 的 ``instructions`` + ``input``
   items（``message`` / ``function_call`` / ``function_call_output`` / ``reasoning``），
   工具表从 ``{"type":"function","function":{...}}`` 拍平成
   ``{"type":"function","name","description","parameters"}``。
2. **入站**：把 ``output`` items 还原成 harness 的 ``ModelResponse``
   （正文 / tool_calls / 思维链 / usage）。

无状态：不使用 ``previous_response_id`` / ``store``（DeepSeek 明确不支持会话延续，
gpt 系也无需依赖——历史消息由 harness 的 Session 重放提供）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse

from .adapter import (ModelAdapter, ModelResponse, ToolCall, _error_message,
                      _status_hint, _tool_pair_error, effective_max_tokens,
                      normalize_base_url, open_url, sanitize_tool_messages,
                      validate_endpoint)

#: 上下文里被视为「工具结果」的角色
_TOOL_ROLES = ("tool", "function")


def _reasoning_roundtrip_error(detail: str) -> bool:
    """供应商是否在抱怨 reasoning item 没有原样回传。"""
    low = str(detail or "").lower()
    return ("reasoning_text" in low and ("pass" in low or "thinking" in low)) \
        or ("reasoning" in low and "must be passed" in low)


# ------------------------------------------------------------------ 出站转换
def split_instructions(messages: list[dict]) -> tuple[str, list[dict]]:
    """第一条 system 消息 → ``instructions``；其余 system 保留为 item。

    DeepSeek 的约定是 ``instructions`` 会作为第一条 system 消息；把主提示词放进
    这里比重复塞 item 更省事，也让各家实现的行为一致。
    """
    instructions: list[str] = []
    rest: list[dict] = []
    primary_taken = False
    for msg in messages:
        if msg.get("role") == "system" and not primary_taken:
            instructions.append(str(msg.get("content") or ""))
            primary_taken = True
        else:
            rest.append(msg)
    return "\n\n".join(x for x in instructions if x), rest


def _tool_output_item(msg: dict) -> dict:
    content = msg.get("content")
    return {
        "type": "function_call_output",
        "call_id": str(msg.get("tool_call_id") or ""),
        "output": content if isinstance(content, str)
                  else json.dumps(content, ensure_ascii=False),
    }


def _function_call_item(call: dict) -> dict:
    func = call.get("function") or {}
    arguments = func.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments if arguments is not None else {},
                               ensure_ascii=False)
    return {
        "type": "function_call",
        "call_id": str(call.get("id") or func.get("name") or ""),
        "name": str(func.get("name") or ""),
        "arguments": arguments,
    }


def to_input_items(messages: list[dict]) -> list[dict]:
    """chat 风格消息 → Responses ``input`` items。

    **调用与结果必须紧邻**：DeepSeek 的 Responses 兼容端点按「output 紧跟自己的
    call」校验，只要两者之间夹了别的 item（另一个调用的 ``function_call``、
    开发者消息、reasoning……）就回

        400 No tool output found for tool call call_01_…

    而 OpenAI 自己只按 ``call_id`` 配对、不要求相邻。并行工具调用按
    ``[call A, call B, out A, out B]`` 平铺就会踩中这条——所以这里**交错输出**
    ``[call A, out A, call B, out B]``，两边都合法。

    另外，找不到结果的调用（工具执行中途崩溃、上下文被截断）一律不发送：
    半截工具链在严格校验下必被拒，且模型看到「调了没回」也毫无意义。
    """
    items: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role") or "user"
        if role in _TOOL_ROLES:
            # 孤儿工具结果（前面没有对应调用）：单发会被拒，直接丢弃
            i += 1
            continue
        # 思考模式的思维链要原样回传（DeepSeek：明文 content 会归并到相邻 assistant）
        raw_reasoning = msg.get("reasoning_items")
        if raw_reasoning:
            # Responses 的原始 reasoning item：保留 id/summary/encrypted_content，
            # 只补 type；供应商会校验它是不是上一轮的同一条思维链。
            for item in raw_reasoning:
                if isinstance(item, dict):
                    out = dict(item)
                    out.setdefault("type", "reasoning")
                    items.append(out)
        else:
            reasoning = msg.get("reasoning_content") or msg.get("reasoning")
            if reasoning:
                items.append({"type": "reasoning",
                              "content": [{"type": "reasoning_text",
                                           "text": str(reasoning)}]})
        content = msg.get("content")
        text = content if isinstance(content, str) else (
            "" if content is None else json.dumps(content, ensure_ascii=False))
        if text:
            items.append({"role": role, "content": text})
        elif role == "user":
            # 空 user 内容会被拒；给一个占位，保证 input 非空
            items.append({"role": "user", "content": "（继续）"})
        # 收集紧随其后的工具结果，按 call_id 配对；逐对交错输出
        results: dict[str, dict] = {}
        j = i + 1
        while j < len(messages) and messages[j].get("role") in _TOOL_ROLES:
            results.setdefault(str(messages[j].get("tool_call_id") or ""), messages[j])
            j += 1
        for call in msg.get("tool_calls") or []:
            call_id = str(call.get("id") or "")
            if call_id not in results:
                continue                      # 半截调用：不发送调用本身
            items.append(_function_call_item(call))
            items.append(_tool_output_item(results[call_id]))
        i = j if (results or msg.get("tool_calls")) else i + 1
    return items


def to_tools(tools: list[dict] | None) -> list[dict]:
    """chat 风格工具表 → Responses 工具表（拍平 ``function`` 一层）。"""
    out: list[dict] = []
    for tool in tools or []:
        if tool.get("type") != "function":
            continue
        func = tool.get("function") or {}
        item = {"type": "function", "name": str(func.get("name") or "")}
        if func.get("description"):
            item["description"] = str(func["description"])
        params = func.get("parameters")
        item["parameters"] = params if isinstance(params, dict) else {
            "type": "object", "properties": {}}
        out.append(item)
    return out


# ------------------------------------------------------------------ 入站解析
def _text_of(content) -> str:
    """``content`` 可能是字符串，也可能是内容块列表。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def parse_usage(usage: dict | None) -> dict:
    """Responses 的 usage → harness/TokenMeter 认的 prompt/completion/total。"""
    usage = usage if isinstance(usage, dict) else {}
    prompt = int(usage.get("input_tokens", 0) or 0)
    completion = int(usage.get("output_tokens", 0) or 0)
    total = int(usage.get("total_tokens", 0) or 0) or (prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": total,
            # 原文也留着：数据看板/日志排查时有用
            "input_tokens": prompt, "output_tokens": completion}


def parse_response(data: dict) -> ModelResponse:
    """把 ``response`` 对象解析成 ``ModelResponse``。"""
    if not isinstance(data, dict):
        raise RuntimeError(f"Responses 响应格式异常：{str(data)[:200]}")
    if data.get("error"):
        err = data["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"LLM 请求失败：{message or '未知错误'}")

    texts: list[str] = []
    reasoning: list[str] = []
    reasoning_items: list[dict] = []
    calls: list[ToolCall] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "message":
            texts.append(_text_of(item.get("content")))
        elif kind == "reasoning":
            piece = _text_of(item.get("content"))
            if not piece:
                # 摘要形式（summary 里是 summary_text）
                piece = _text_of(item.get("summary"))
            if piece:
                reasoning.append(piece)
            # 原始 item 必须原样回传：id / summary / encrypted_content 都可能
            # 是供应商校验“同一条思维链”的依据。
            reasoning_items.append(dict(item))
        elif kind == "function_call":
            args = item.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args if args is not None else {}, ensure_ascii=False)
            calls.append(ToolCall.from_raw({
                "id": str(item.get("call_id") or item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "arguments": args,
            }))
    content = (data.get("output_text") or "".join(texts) or None)
    status = str(data.get("status") or "")
    incomplete = data.get("incomplete_details") or {}
    reason = ""
    if isinstance(incomplete, dict):
        reason = str(incomplete.get("reason") or "")
    finish = "length" if status == "incomplete" or reason else "stop"
    if calls:
        finish = "tool_calls"
    if not content and not calls and finish == "length":
        raise RuntimeError(
            "模型输出被 max_output_tokens 截断且没有正文：思考模式会先消耗输出预算。"
            "请在设置里关闭思考模式，或调大 max_tokens。")
    return ModelResponse(content=content, tool_calls=calls,
                         finish_reason=finish,
                         usage=parse_usage(data.get("usage")),
                         reasoning="\n".join(r for r in reasoning if r) or None,
                         reasoning_items=reasoning_items)


# ------------------------------------------------------------------ 适配器
class OpenAIResponsesAdapter(ModelAdapter):
    """``POST {base}/responses`` 客户端（OpenAI Responses API 兼容）。"""

    name = "openai_responses"

    def __init__(self, base_url: str, api_key: str, timeout: float = 90.0,
                 max_retries: int = 3, backoff: float = 2.0,
                 thinking: str = "", reasoning_effort: str = "",
                 fallback_chat=None, fallback_on_404: bool = True):
        base_url = normalize_base_url(base_url)
        validate_endpoint(base_url)
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.thinking = str(thinking or "").strip().lower()
        self.reasoning_effort = str(reasoning_effort or "").strip().lower()
        #: 404 时是否自动降级到 chat/completions（auto 模式才降；显式选
        #: Responses 时直接报 404，便于排查）。工具配对 400 的兜底不受此限。
        self.fallback_on_404 = bool(fallback_on_404)
        #: 404 时自动降级到 chat/completions（由 server 侧注入同参适配器）
        self._fallback_chat = fallback_chat
        self.fell_back = False

    # ---------------------------------------------------------- 请求体
    def build_payload(self, model: str, messages: list[dict],
                      tools: list[dict] | None, temperature: float,
                      max_tokens: int) -> dict:
        instructions, rest = split_instructions(messages)
        items = to_input_items(rest)
        payload: dict = {
            "model": model,
            "input": items or [{"role": "user", "content": "（继续）"}],
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if instructions:
            payload["instructions"] = instructions
        tool_specs = to_tools(tools)
        if tool_specs:
            payload["tools"] = tool_specs
            payload["tool_choice"] = "auto"
        if self.thinking in ("on", "enabled", "off", "disabled"):
            # Responses 用 reasoning.effort 表达强度；关闭用 enabled 之外的方式：
            # DeepSeek 在 Responses 下没有 "thinking.type"，用 effort=none 关闭
            # 不被支持，因此关闭时索性不下发 reasoning（服务端默认值）。
            if self.thinking in ("on", "enabled"):
                payload["reasoning"] = {"effort": self.reasoning_effort or "high"}
            elif self.reasoning_effort:
                payload["reasoning"] = {"effort": self.reasoning_effort}
        return payload

    # ---------------------------------------------------------- 调用
    def chat(self, model: str, messages: list[dict], tools: list[dict] | None,
             temperature: float, max_tokens: int) -> ModelResponse:
        # 思考模式的思维链也占输出预算：给短任务兜个底（见 adapter.effective_max_tokens）
        max_tokens = effective_max_tokens(max_tokens, self.thinking)
        messages = sanitize_tool_messages(messages)     # 修掉半截工具链
        if self.fell_back and self._fallback_chat is not None:
            return self._fallback_chat.chat(model, messages, tools, temperature, max_tokens)
        payload = self.build_payload(model, messages, tools, temperature, max_tokens)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._post(body)
            except urllib.error.HTTPError as e:
                detail = _error_message(e)
                if self._fallback_chat is not None:
                    # 404：该供应商没有 /responses（仅 auto 模式静默降级）。
                    # 工具配对 400：DeepSeek 的严格相邻校验不接受我们补好的历史时，
                    # 也不能让对局就死在这——同一条对话换 chat/completions 重发
                    # （那边 4xx 已经被 sanitize 修过形，且是应用完整支持过的通道）。
                    if ((e.code == 404 and self.fallback_on_404)
                            or (e.code == 400 and _tool_pair_error(detail))
                            or (e.code == 400
                                and _reasoning_roundtrip_error(detail))):
                        self.fell_back = True
                        return self._fallback_chat.chat(model, messages, tools,
                                                        temperature, max_tokens)
                last_err = e
                if e.code not in (408, 429) or attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"LLM 请求失败（HTTP {e.code}）：{detail or e.reason}"
                        f"｜{_status_hint(e.code, detail)}") from e
                import time
                time.sleep(self.backoff * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt == self.max_retries - 1:
                    break
                import time
                time.sleep(self.backoff * (attempt + 1))
        kind = type(last_err).__name__
        detail = str(getattr(last_err, "reason", last_err))
        raise RuntimeError(f"LLM 请求失败（{kind}）：{detail}")

    def _post(self, body: bytes) -> ModelResponse:
        req = urllib.request.Request(
            f"{self.base_url}/responses", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        with open_url(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return parse_response(data)

    # ---------------------------------------------------------- 模型列表
    def list_models(self, timeout: float | None = None) -> list[str]:
        """``GET {base}/models``（与 chat 适配器同一口径）。"""
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


def responses_endpoint(base_url: str) -> str:
    """供诊断展示：Responses 端点（不发起请求）。"""
    return urllib.parse.urljoin(normalize_base_url(base_url) + "/", "responses")
