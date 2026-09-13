"""运行配置。所有凭据只从环境变量或本服务运行时内存读取，绝不写入源码、
示例、测试，也不落盘持久化。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 供应商
# 统一走 OpenAI 兼容 /chat/completions 协议（各官方端点均提供该协议）。
LLM_PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_envs": ["CTRCG_LLM_API_KEY", "DEEPSEEK_API_KEY"],
        "default": True,
    },
    "glm": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.6", "glm-4.5", "glm-4-flash"],
        "key_envs": ["CTRCG_LLM_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY"],
    },
    "kimi": {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2-turbo-preview", "kimi-k2-preview", "moonshot-v1-8k"],
        "key_envs": ["CTRCG_LLM_API_KEY", "MOONSHOT_API_KEY"],
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimaxi.com/v1",
        "models": ["MiniMax-Text-01", "MiniMax-M1"],
        "key_envs": ["CTRCG_LLM_API_KEY", "MINIMAX_API_KEY"],
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "models": [],
        "key_envs": ["CTRCG_LLM_API_KEY"],
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

    @classmethod
    def from_env(cls, provider: str = "deepseek") -> "LLMConfig | None":
        """按供应商从环境变量读取；未配置时返回 None。"""
        preset = LLM_PROVIDERS.get(provider)
        if preset is None:
            return None
        base_url = os.environ.get("CTRCG_LLM_BASE_URL", "").strip() or preset["base_url"]
        api_key = ""
        for env in preset["key_envs"]:
            api_key = os.environ.get(env, "").strip()
            if api_key:
                break
        if not base_url or not api_key:
            return None
        model = (os.environ.get("CTRCG_LLM_MODEL", "").strip()
                 or (preset["models"][0] if preset["models"] else ""))
        return cls(base_url=base_url.rstrip("/"), api_key=api_key, model=model,
                   provider=provider)


@dataclass
class GameConfig:
    """全局游戏配置。"""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CTRCG_DATA_DIR", "saves")))
    # harness 保守值：单次 Agent 调用内最多循环步数、上下文压缩阈值（粗略 token 估算）
    max_agent_steps: int = 12
    context_token_limit: int = 12000
    tool_timeout_seconds: float = 10.0


GAME_CONFIG = GameConfig()
