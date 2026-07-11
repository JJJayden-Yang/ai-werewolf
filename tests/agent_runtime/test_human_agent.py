from __future__ import annotations

import asyncio

from agent_runtime.human_input import HumanAgent, HumanInputChannel
from agent_runtime.per_seat_agent import PerSeatAgent
from contracts import ActionType, Phase, Role


def _context() -> dict:
    return {
        "game_id": "g_human",
        "agent_id": "P1",
        "role": "seer",
        "round": 1,
        "phase": "NIGHT_SEER",
        "visible_players": [
            {"player_id": "P1", "status": "alive"},
            {"player_id": "P2", "status": "alive"},
        ],
        "allowed_actions": ["check"],
    }


def test_human_agent_waits_for_submitted_action_and_fills_identity():
    async def scenario():
        channel = HumanInputChannel()
        agent = HumanAgent("P1", channel, timeout_seconds=1)

        task = asyncio.create_task(agent.act(_context()))
        await asyncio.sleep(0)
        assert channel.pending_context is not None
        assert channel.pending_context["agent_id"] == "P1"

        await channel.submit({"action_type": "check", "target": "P2"})
        action = await task

        assert action["game_id"] == "g_human"
        assert action["agent_id"] == "P1"
        assert action["role"] == "seer"
        assert action["phase"] == "NIGHT_SEER"
        assert action["action_type"] == "check"
        assert action["target"] == "P2"
        assert channel.pending_context is None

    asyncio.run(scenario())


def test_human_agent_timeout_falls_back_to_legal_policy():
    async def scenario():
        channel = HumanInputChannel()
        agent = HumanAgent("P1", channel, timeout_seconds=0.01)

        action = await agent.act(_context())

        assert action["game_id"] == "g_human"
        assert action["agent_id"] == "P1"
        assert action["role"] == "seer"
        assert action["phase"] == "NIGHT_SEER"
        assert action["action_type"] in {ActionType.CHECK.value, ActionType.SKIP.value}
        assert channel.pending_context is None

    asyncio.run(scenario())


def test_per_seat_agent_routes_override_by_agent_id():
    class EchoAgent:
        async def act(self, context: dict) -> dict:
            return {"agent": "default", "seat": context["agent_id"]}

    class OverrideAgent:
        async def act(self, context: dict) -> dict:
            return {"agent": "human", "seat": context["agent_id"]}

    async def scenario():
        agent = PerSeatAgent(default_agent=EchoAgent(), overrides={"P1": OverrideAgent()})

        assert await agent.act({"agent_id": "P1"}) == {"agent": "human", "seat": "P1"}
        assert await agent.act({"agent_id": "P2"}) == {"agent": "default", "seat": "P2"}

    asyncio.run(scenario())
