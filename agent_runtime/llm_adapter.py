"""LLMAdapter skeleton —— Task C1 配套。

负责把 PromptTemplate + AgentContext 渲染成具体 Provider 的 messages 入参，
并把 LLMResponse 与上层 ActionParser / RetryPolicy / FallbackPolicy 串起来。

第一阶段仅留骨架；真实参数（temperature / max_tokens / model_id）通过 ModelConfig 注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.schemas import AgentContext, ModelConfig

    from agent_runtime.llm_provider import LLMProvider
    from agent_runtime.types import LLMResponse, PromptTemplate


class LLMAdapter:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def call(
        self,
        template: PromptTemplate,
        context: AgentContext,
        model_config: ModelConfig,
    ) -> LLMResponse:
        raise NotImplementedError
