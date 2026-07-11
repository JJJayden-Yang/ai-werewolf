"""MockAgent 实现。

MockAgent 用于 A 的 6 人 Debug MVP 和 100 局 smoke test。
它们不调用 LLM，只根据 AgentContext 生成标准 AgentAction。
"""

from __future__ import annotations

from contracts import AgentContext

from agent_policy.base import BaseAgent
from agent_policy.mock_policies import heuristic_policy, legal_random_policy
from agent_policy.role_strategies import RoleStrategyRegistry


class LegalRandomMockAgent(BaseAgent):
    """尽量永远输出合法动作的确定性 mock agent。"""

    async def act(self, context: dict) -> dict:
        agent_context = AgentContext.model_validate(context)
        action = legal_random_policy(agent_context)
        return action.model_dump(mode="json")


class HeuristicMockAgent(BaseAgent):
    """带简单狼人杀启发式的 mock agent。"""

    async def act(self, context: dict) -> dict:
        agent_context = AgentContext.model_validate(context)
        action = heuristic_policy(agent_context)
        return action.model_dump(mode="json")


class RoleStrategyMockAgent(BaseAgent):
    """通过 RoleStrategyRegistry 按角色分发的 mock agent。"""

    def __init__(self, registry: RoleStrategyRegistry | None = None) -> None:
        self.registry = registry or RoleStrategyRegistry()

    async def act(self, context: dict) -> dict:
        agent_context = AgentContext.model_validate(context)
        strategy = self.registry.get(agent_context.role)
        action = strategy.decide(agent_context)
        return action.model_dump(mode="json")
