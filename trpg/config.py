"""运行配置。所有凭据只从环境变量或本服务运行时内存读取，绝不写入源码、
示例、测试，也不落盘持久化。

供应商预设的**地址与模型名以各家官方文档为准**（2026-10 核对）：
- DeepSeek：base `https://api.deepseek.com`（**不带 /v1**），模型
  `deepseek-flash` / `deepseek-v4-pro`；V4 起**默认开启思考模式**。
- 智谱 GLM：base `https://open.bigmodel.cn/api/paas/v4`，
  模型 `glm-5.3` / `glm-5.3-flash` / `glm-5.3-flashx` / `glm-5.2`；
  GLM-5.3 强制思考，只接受 `thinking.type=enabled`。
- Kimi（月之暗面）：base `https://api.moonshot.cn/v1`（国内）/
  `https://api.moonshot.ai/v1`（国际），模型 `kimi-k3` / `kimi-k2.7-code` /
  `kimi-k2.6`；kimi-k2 与 moonshot-v1 系列已于 2026 年下线。
- MiniMax：base `https://api.minimaxi.com/v1`（国内）/ `api.minimax.io/v1`（国际）。

模型名会随供应商迭代变化，所以界面提供「拉取模型列表」（GET /api/models），
用你自己的密钥列出账号真正可用的模型——预设只是兜底与默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 供应商
# 统一走 OpenAI 兼容 /chat/completions 协议（各官方端点均提供该协议）。
# 字段说明：
#   base_url         —— 可再拼 /chat/completions 的前缀（别写到端点为止）
#   models           —— 兜底模型名（真实可用列表由 /api/models 现拉）
#   key_envs         —— 密钥环境变量（按序取第一个非空）
#   supports_thinking—— 是否支持 {"thinking": {"type": ...}} 开关
#   thinking_default —— 新建对局时思考模式的默认值（on/off/""=不发该键）
#   models_path      —— 模型列表接口路径（OpenAI 风格为 /models）
#   responses        —— 是否提供 OpenAI Responses API（POST {base}/responses）
#   docs / note      —— 界面里展示的文档链接与提示
LLM_PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "key_envs": ["TRPG_LLM_API_KEY", "DEEPSEEK_API_KEY"],
        "default": True,
        "supports_thinking": True,
        "thinking_default": "off",
        "models_path": "/models",
        "responses": True,
        "docs": "https://api-docs.deepseek.com/zh-cn/guides/responses_api",
        "note": "V4 起默认开启思考模式：本应用默认关闭（更快更省）；"
                "开启后带工具的请求必须回传思维链，适配器已代劳。",
    },
    "glm": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "glm-5.2"],
        "key_envs": ["TRPG_LLM_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY"],
        "supports_thinking": True,
        "thinking_default": "off",
        "models_path": "/models",
        "responses": True,
        "docs": "https://docs.bigmodel.cn/cn/guide/start/model-overview",
        "note": "GLM-5.3 强制思考且只接受 enabled；若报错请把思考模式设为开。",
    },
    "kimi": {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.6"],
        "key_envs": ["TRPG_LLM_API_KEY", "MOONSHOT_API_KEY"],
        "models_path": "/models",
        "responses": True,
        "docs": "https://platform.kimi.com/docs/models",
        "note": "国内站 base 用 api.moonshot.cn/v1，国际站换成 api.moonshot.ai/v1"
                "（Kimi 平台已迁至 platform.kimi.com）。",
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimaxi.com/v1",
        "models": ["MiniMax-M2.1", "MiniMax-M2"],
        "key_envs": ["TRPG_LLM_API_KEY", "MINIMAX_API_KEY"],
        "models_path": "/models",
        "responses": False,
        "docs": "https://platform.minimaxi.com/document",
        "note": "模型名变化较快，请用「拉取模型列表」确认账号可用的型号。",
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "models": [],
        "key_envs": ["TRPG_LLM_API_KEY"],
        "models_path": "/models",
        "responses": False,
        "docs": "",
        "note": "任意 OpenAI 兼容网关：填到 /v1 为止（或整段端点，保存时会自动整形）。",
    },
}


@dataclass
class LLMConfig:
    """OpenAI 兼容接口配置。api_key 只在内存中流转。"""

    base_url: str
    api_key: str
    model: str
    provider: str = "deepseek"
    temperature: float = 0.8
    max_tokens: int = 1600
    request_timeout: float = 90.0
    max_retries: int = 3
    # 思考模式：on/off/""（空 = 不下发该键，交给供应商默认值）
    thinking: str = "off"
    reasoning_effort: str = ""       # low/high/max（空 = 不下发）
    # 协议：auto = 供应商支持就用 Responses API，否则 chat/completions；
    #      responses / chat 为强制指定（不支持时直接报错，便于排查）
    api_style: str = "auto"

    @property
    def uses_responses(self) -> bool:
        """按配置与供应商能力解析出「实际使用的协议」。"""
        style = (self.api_style or "auto").strip().lower()
        preset = LLM_PROVIDERS.get(self.provider) or {}
        if style in ("responses", "response", "openai-responses"):
            return True
        if style in ("chat", "chat_completions", "completions"):
            return False
        return bool(preset.get("responses"))     # auto

    @classmethod
    def from_env(cls, provider: str = "deepseek") -> "LLMConfig | None":
        """按供应商从环境变量读取；未配置时返回 None。"""
        preset = LLM_PROVIDERS.get(provider)
        if preset is None:
            return None
        base_url = os.environ.get("TRPG_LLM_BASE_URL", "").strip() or preset["base_url"]
        api_key = ""
        for env in preset["key_envs"]:
            api_key = os.environ.get(env, "").strip()
            if api_key:
                break
        if not base_url or not api_key:
            return None
        model = (os.environ.get("TRPG_LLM_MODEL", "").strip()
                 or (preset["models"][0] if preset["models"] else ""))
        thinking = os.environ.get("TRPG_LLM_THINKING", "").strip().lower()
        if not thinking:
            thinking = str(preset.get("thinking_default") or "")
        style = os.environ.get("TRPG_LLM_API_STYLE", "").strip().lower() or "auto"
        return cls(base_url=base_url.rstrip("/"), api_key=api_key, model=model,
                   provider=provider,
                   thinking=thinking,
                   api_style=style,
                   reasoning_effort=os.environ.get(
                       "TRPG_LLM_REASONING_EFFORT", "").strip().lower())


@dataclass
class GameConfig:
    """全局游戏配置。"""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("TRPG_DATA_DIR", "saves")))
    # harness 保守值：单次 Agent 调用内最多循环步数、上下文压缩阈值（粗略 token 估算）
    max_agent_steps: int = 12
    context_token_limit: int = 12000
    tool_timeout_seconds: float = 10.0


GAME_CONFIG = GameConfig()
