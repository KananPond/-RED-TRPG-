"""Agent Harness —— 参考 deepseek-harness 的"一切皆插件"架构。

核心概念对照（详见 HARNESS.md）：
- Session 事件日志：模型可见 ⟺ 已记录（model-visible ⟺ logged）。
- ModelAdapter：模型适配器插件（OpenAI 兼容端点 / 测试用脚本适配器）。
- ToolRegistry：工具注册表插件，register() 返回 disposer。
- AgentLoop：可替换的 agent 循环插件（默认循环：观察→模型→工具→回填）。
- Guard：循环卫生插件（步数预算、工具超时、重复调用抑制）。
- Compaction：上下文压缩插件。
"""
