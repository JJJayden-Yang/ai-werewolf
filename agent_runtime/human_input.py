"""真人玩家输入通道与 Agent 适配器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from agent_policy.base import BaseAgent
from agent_policy.mock_policies import legal_random_policy
from contracts import AgentContext


@dataclass
class HumanInputChannel:
    """单真人 MVP 的异步输入通道。"""

    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    pending_context: dict[str, Any] | None = None

    async def submit(self, action: dict[str, Any]) -> None:
        await self.queue.put(action)


class HumanAgent(BaseAgent):
    """轮到真人座位时暂停等待前端提交动作。"""

    def __init__(
        self,
        human_seat: str,
        channel: HumanInputChannel,
        *,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._human_seat = human_seat
        self._channel = channel
        self._timeout_seconds = timeout_seconds

    async def act(self, context: dict) -> dict:
        parsed_context = AgentContext.model_validate(context)
        self._channel.pending_context = parsed_context.model_dump(mode="json")
        try:
            submitted = await asyncio.wait_for(
                self._channel.queue.get(),
                timeout=self._timeout_seconds,
            )
            return {
                **submitted,
                "game_id": parsed_context.game_id,
                "agent_id": self._human_seat,
                "role": parsed_context.role.value,
                "phase": parsed_context.phase.value,
            }
        except asyncio.TimeoutError:
            action = legal_random_policy(parsed_context)
        finally:
            self._channel.pending_context = None
        return action.model_dump(mode="json")
