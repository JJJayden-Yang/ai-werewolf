"""context —— 人 C 负责的信息边界层。

ContextAssembler 是 Agent 唯一的信息入口。
本层决定"谁能看到什么"，并把上下文压到 token 预算内。
"""

from context.context_assembler import ContextAssembler
from context.context_window_policy import (
    ContextBudgetExceededError,
    ContextWindowPolicy,
    HistoricalSpeechLeakError,
)
from context.protocols import GameSessionProvider
from context.speech_summarizer import SpeechSummarizer
from context.types import AgentContextDraft
from context.visibility_rules import (
    PRIVATE_EVENT_TYPES,
    PUBLIC_EVENT_TYPES,
    VisibilityRuleSpec,
)

__all__ = [
    "AgentContextDraft",
    "ContextAssembler",
    "ContextBudgetExceededError",
    "ContextWindowPolicy",
    "GameSessionProvider",
    "HistoricalSpeechLeakError",
    "PRIVATE_EVENT_TYPES",
    "PUBLIC_EVENT_TYPES",
    "SpeechSummarizer",
    "VisibilityRuleSpec",
]
