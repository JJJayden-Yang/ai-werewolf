"""Per-seat soul router for LLMAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_runtime.llm_agent import LLMAgent
from agent_runtime.prompt_template_loader import DEFAULT_SOUL_ID, PromptTemplateLoader

if TYPE_CHECKING:
    from agent_policy.advanced_strategy.strategy_selector import StrategySelector
    from agent_runtime.llm_provider import LLMProvider
    from stores.trace_store import TraceStore


class SeatSoulAgent:
    """Route each ``AgentContext`` to an LLMAgent configured with that seat's soul."""

    def __init__(
        self,
        provider: "LLMProvider",
        *,
        seat_souls: dict[str, str],
        model_config: dict | None = None,
        template_name: str = "v0_free_llm",
        trace_store: "TraceStore | None" = None,
        agent_version: str = "v0",
        strategy_selector: "StrategySelector | None" = None,
    ) -> None:
        self._provider = provider
        self._seat_souls = dict(seat_souls)
        self._model_config = dict(model_config) if model_config else {}
        self._template_name = template_name
        self._trace_store = trace_store
        self._agent_version = agent_version
        # phase3：策略库与按座位人格可叠加 —— 同一个 selector 透传给每个 seat 的 LLMAgent。
        self._strategy_selector = strategy_selector
        self._agents_by_soul: dict[str, LLMAgent] = {}

    async def act(self, context: dict) -> dict:
        agent_id = str(context.get("agent_id") or "")
        # 缺座位（没给该 agent_id 配 soul）→ 回退到中性默认人格，而不是报错。
        # 这样前端不必强制给 9 个座位都选 soul，未选的自动用默认。
        soul_id = self._seat_souls.get(agent_id) or DEFAULT_SOUL_ID
        agent = self._agents_by_soul.get(soul_id)
        if agent is None:
            agent = LLMAgent(
                self._provider,
                loader=PromptTemplateLoader(soul_id=soul_id),
                model_config=self._model_config,
                template_name=self._template_name,
                trace_store=self._trace_store,
                agent_version=self._agent_version,
                strategy_selector=self._strategy_selector,
            )
            self._agents_by_soul[soul_id] = agent
        return await agent.act(context)
