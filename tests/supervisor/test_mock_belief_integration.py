"""Mock + realtime belief integration tests."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from agent_policy import RoleStrategyMockAgent
from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater
from contracts import GameConfig, Phase
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.belief_state_store import InMemoryBeliefStateStore
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class _RecordingAgent:
    def __init__(self) -> None:
        self._inner = RoleStrategyMockAgent()
        self.contexts: list[dict] = []

    async def act(self, context: dict) -> dict:
        self.contexts.append(context)
        return await self._inner.act(context)


def test_mock_game_with_belief_injects_top_suspects_into_context():
    data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    data["game_id"] = "mock_belief_integration"
    config = GameConfig.model_validate(data)
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(0))
    engine.sessions.create_game(config)

    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    agent = _RecordingAgent()
    assembler = ContextAssembler(
        session_provider=engine,
        event_store=event_store,
        belief_store=belief_store,
    )
    supervisor = Supervisor(
        engine,
        assembler,
        agent,
        event_store,
        belief_updater=RuleBasedRealtimeBeliefUpdater(
            event_store=event_store,
            belief_store=belief_store,
            session_provider=engine,
        ),
    )

    asyncio.run(supervisor.run_game("mock_belief_integration"))

    assert engine.get_session("mock_belief_integration").current_phase == Phase.GAME_OVER
    assert any(context["belief_top_suspects"] for context in agent.contexts)
    players = engine.get_session("mock_belief_integration").truth_state.players
    assert any(
        belief_store.get_history("mock_belief_integration", player_id)
        for player_id in players
    )
