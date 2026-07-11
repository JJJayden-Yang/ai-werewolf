"""agent_runtime —— 人 C 负责的运行时层。

LLM 接入 + 解析 + 安全闸门 + 重试与兜底。对外只通过 contracts/ 中的 schema 交互。
"""

from agent_runtime.action_canonicalizer import ActionCanonicalizer
from agent_runtime.action_parser import ActionParser
from agent_runtime.ark_llm_provider import ArkLLMError, ArkLLMProvider
from agent_runtime.exceptions import (
    AgentRuntimeError,
    FakeLLMExhaustedError,
    LLMProviderNotFoundError,
    PromptTemplateNotFoundError,
)
from agent_runtime.fallback_policy import FallbackPolicy
from agent_runtime.human_input import HumanAgent, HumanInputChannel
from agent_runtime.llm_adapter import LLMAdapter
from agent_runtime.llm_agent import LLMAgent
from agent_runtime.llm_provider import (
    FakeLLMProvider,
    LLMProvider,
    LLMProviderRegistry,
    generate_sync,
)
from agent_runtime.prompt_template_loader import DEFAULT_SOUL_ID, PromptTemplateLoader
from agent_runtime.retry_policy import RetryPolicy
from agent_runtime.seat_soul_agent import SeatSoulAgent
from agent_runtime.per_seat_agent import PerSeatAgent
from agent_runtime.types import LLMResponse, PromptTemplate

__all__ = [
    # LLM 接入
    "LLMProvider",
    "FakeLLMProvider",
    "ArkLLMProvider",
    "ArkLLMError",
    "LLMProviderRegistry",
    "LLMAdapter",
    "LLMAgent",
    "SeatSoulAgent",
    "HumanAgent",
    "HumanInputChannel",
    "PerSeatAgent",
    "generate_sync",
    # Prompt / Action
    "PromptTemplateLoader",
    "DEFAULT_SOUL_ID",
    "ActionParser",
    "ActionCanonicalizer",
    # Retry / Fallback
    "RetryPolicy",
    "FallbackPolicy",
    # 类型
    "LLMResponse",
    "PromptTemplate",
    # 异常
    "AgentRuntimeError",
    "LLMProviderNotFoundError",
    "FakeLLMExhaustedError",
    "PromptTemplateNotFoundError",
]
