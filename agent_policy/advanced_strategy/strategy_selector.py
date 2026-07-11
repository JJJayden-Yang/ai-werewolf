"""StrategySelector —— 给定运行时 AgentContext，选出 0-K 段高级策略片段。

流程：
  1. ``tags = scene_detector.detect(context)``
  2. 筛 snippet：``matches_role`` 且 ``matches_phase`` 且 ``scene_tags ∩ tags ≠ ∅``
  3. 按 ``priority`` 降序（同分按 id 稳定排序），取前 ``max_snippets``
  4. 预算保护：累计字符超 ``max_chars`` 时丢弃低优先级片段
     （system prompt 已含 output_contract + game_knowledge + role + soul，要给策略留有限配额）

LLMAgent opt-in 持有本 selector；不传则完全不注入策略（baseline 不变）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_policy.advanced_strategy import scene_detector
from agent_policy.advanced_strategy.strategy_library import StrategyLibrary, StrategySnippet

if TYPE_CHECKING:
    from contracts import AgentContext

_DEFAULT_MAX_SNIPPETS = 2  # 默认取 2，硬上限见 _HARD_CAP
_HARD_CAP = 3
_DEFAULT_MAX_CHARS = 2500


class StrategySelector:
    def __init__(
        self,
        library: StrategyLibrary | None = None,
        *,
        max_snippets: int = _DEFAULT_MAX_SNIPPETS,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> None:
        self._library = library if library is not None else StrategyLibrary()
        self._max_snippets = min(max(0, max_snippets), _HARD_CAP)
        self._max_chars = max_chars

    @property
    def library(self) -> StrategyLibrary:
        return self._library

    def detect_tags(self, context: "AgentContext") -> set[str]:
        return scene_detector.detect(context)

    def select(self, context: "AgentContext") -> list[StrategySnippet]:
        """返回命中的策略片段（已按 priority 降序、过预算裁剪、限 max_snippets）。"""
        tags = scene_detector.detect(context)
        if not tags:
            return []

        role_value = context.role.value
        phase_value = context.phase.value
        candidates = [
            s
            for s in self._library.snippets
            if s.matches_role(role_value)
            and s.matches_phase(phase_value)
            and (s.scene_tags & tags)
        ]
        # priority 降序；同分按 id 升序保证确定性（可测、可复现）。
        candidates.sort(key=lambda s: (-s.priority, s.id))

        selected: list[StrategySnippet] = []
        used_chars = 0
        for snippet in candidates:
            if len(selected) >= self._max_snippets:
                break
            if used_chars + len(snippet.text) > self._max_chars and selected:
                # 预算超了且已选过至少一条 → 跳过该低优先级片段（不让单条把预算撑爆）
                continue
            selected.append(snippet)
            used_chars += len(snippet.text)
        return selected
