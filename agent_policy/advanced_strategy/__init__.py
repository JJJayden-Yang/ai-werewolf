"""静态高级策略库（Phase 3）。

按 ``role + phase + scene_tag`` 在场景命中时，往 LLM system prompt 注入人工维护的
markdown 策略片段。不是 RAG、不自动学习——人工维护、确定性场景检测、opt-in。
"""

from __future__ import annotations

from agent_policy.advanced_strategy import scene_detector
from agent_policy.advanced_strategy.frontmatter import parse_markdown_frontmatter
from agent_policy.advanced_strategy.strategy_library import StrategyLibrary, StrategySnippet
from agent_policy.advanced_strategy.strategy_selector import StrategySelector

__all__ = [
    "StrategyLibrary",
    "StrategySnippet",
    "StrategySelector",
    "scene_detector",
    "parse_markdown_frontmatter",
]
