"""按座位把 Agent 调用路由给不同实现。"""

from __future__ import annotations

from agent_policy.base import BaseAgent


class PerSeatAgent(BaseAgent):
    def __init__(self, default_agent: BaseAgent, overrides: dict[str, BaseAgent]) -> None:
        self._default_agent = default_agent
        self._overrides = overrides

    async def act(self, context: dict) -> dict:
        agent_id = context.get("agent_id")
        agent = self._overrides.get(agent_id, self._default_agent)
        return await agent.act(context)
