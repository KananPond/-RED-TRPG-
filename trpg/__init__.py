"""TRPG —— 单人 AI 跑团引擎（《赛博朋克 RED》· 2045 年夜之城）。

- 规则引擎：《赛博朋克 RED》轻松模式口径（十项属性、41 项技能、难度值 DV、
  技能检定与暴击、生命值 PW 与护甲 SP 磨损、伤势与致命伤、生存检定、稳定与休整、
  义体与人性、改进点 IP）。
- 剧情模组：原子化模组 JSON（`modules/<id>/module.json`），内置《赛博朋克 RED：轻松模式》。
- 剧本模式：互动剧引擎（自写分支剧本 · 多结局 · 结局图鉴）。
- Agent Harness：参考 deepseek-harness 的"一切皆插件"架构
  （Session 事件日志 / ModelAdapter / ToolRegistry / AgentLoop / Guard / Compaction）。
"""

__version__ = "2.3.1"
