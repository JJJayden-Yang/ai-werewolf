"""Supervisor 接入 agent_policy MockAgent 的集成 smoke。

这个测试专门验证 B 侧的 MockAgent 可以接进 A/C 边界：
- A：真实 GameEngine + Supervisor
- B：agent_policy.LegalRandomMockAgent / RoleStrategyMockAgent
- C：测试版 ContextAssembler + EventSink
"""

import asyncio
import json
import random
from pathlib import Path

import pytest

from agent_policy import LegalRandomMockAgent, RoleStrategyMockAgent
from contracts import (
    ActionType,
    AgentContext,
    EventType,
    GameConfig,
    Phase,
    PlayerStatus,
    PrivateEvent,
    Role,
    Visibility,
    VisiblePlayer,
)
from game_core import GameEngine, GameSessionManager
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class AgentPolicySmokeContextAssembler:
    """测试版 C：只装配 agent_policy MockAgent 需要的最小合法上下文。"""

    def __init__(self, engine: GameEngine) -> None:
        self._engine = engine
        self.built_contexts: list[AgentContext] = []

    def build_context(self, game_id: str, agent_id: str, phase: Phase) -> AgentContext:
        session = self._engine.sessions.get_game(game_id)
        truth = session.truth_state
        player = truth.players[agent_id]
        context = AgentContext(
            game_id=game_id,
            agent_id=agent_id,
            role=player.role,
            round=session.round,
            phase=phase,
            is_secondary_stage=truth.round_state.is_secondary_stage,
            tie_candidates=truth.round_state.tie_candidates,
            previous_vote_summary=truth.round_state.previous_vote_summary,
            visible_players=[
                VisiblePlayer(player_id=pid, status=p.status, public_claim=p.public_claim)
                for pid, p in truth.players.items()
            ],
            private_events=self._private_events_for(game_id, agent_id),
            allowed_actions=self._allowed_actions_for(phase),
            rule_hints={"source": "test_agent_policy_integration"},
        )
        self.built_contexts.append(context)
        return context

    def _private_events_for(self, game_id: str, agent_id: str) -> list[PrivateEvent]:
        session = self._engine.sessions.get_game(game_id)
        player = session.truth_state.players[agent_id]
        if player.role != Role.WEREWOLF:
            return []
        teammates = [
            pid
            for pid, state in session.truth_state.players.items()
            if state.role == Role.WEREWOLF
        ]
        return [
            PrivateEvent(
                event_type=EventType.ROLE_ASSIGNED,
                teammates=teammates,
                visibility=Visibility.PRIVATE_TO_WOLVES,
            )
        ]

    @staticmethod
    def _allowed_actions_for(phase: Phase) -> list[ActionType]:
        return list(
            {
                Phase.NIGHT_WEREWOLF: {ActionType.NIGHT_KILL_NOMINATE},
                Phase.NIGHT_SEER: {ActionType.CHECK},
                Phase.NIGHT_WITCH: {ActionType.SAVE, ActionType.POISON, ActionType.SKIP},
                Phase.DAY_DISCUSSION: {ActionType.SPEAK},
                Phase.DAY_VOTE: {ActionType.VOTE},
                Phase.DAY_TIE_DISCUSSION: {ActionType.SPEAK},
                Phase.DAY_TIE_REVOTE: {ActionType.VOTE},
                Phase.EXILE_LAST_WORDS: {ActionType.SPEAK},
                Phase.HUNTER_SHOOT: {ActionType.HUNTER_SHOOT},
            }.get(phase, set())
        )


class AgentPolicySmokeEventSink:
    def __init__(self) -> None:
        self.events = []

    def append_many(self, events) -> None:
        self.events.extend(events)


def _build_game(seed: int, game_id: str, agent):
    config_data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)
    context_assembler = AgentPolicySmokeContextAssembler(engine)
    sink = AgentPolicySmokeEventSink()
    supervisor = Supervisor(engine, context_assembler, agent, sink)
    return config, engine, context_assembler, supervisor, sink


@pytest.mark.parametrize(
    ("agent_name", "agent"),
    [
        ("legal_random", LegalRandomMockAgent()),
        ("role_strategy", RoleStrategyMockAgent()),
    ],
)
def test_supervisor_runs_full_game_with_agent_policy_mock_agent(agent_name, agent):
    config, engine, context_assembler, supervisor, sink = _build_game(
        seed=0,
        game_id=f"agent_policy_integration_6p_{agent_name}",
        agent=agent,
    )

    asyncio.run(supervisor.run_game(config.game_id))
    session = engine.sessions.get_game(config.game_id)

    assert session.current_phase == Phase.GAME_OVER
    assert session.round <= config.max_rounds
    assert context_assembler.built_contexts
    assert all(context.allowed_actions for context in context_assembler.built_contexts)
    assert any(
        context.role == Role.WEREWOLF
        and any(event.visibility == Visibility.PRIVATE_TO_WOLVES for event in context.private_events)
        for context in context_assembler.built_contexts
    )
    assert any(event.event_type == EventType.GAME_OVER for event in sink.events)
    assert not any(event.event_type == EventType.RULE_VALIDATION for event in sink.events)
    assert not any(event.event_type == EventType.FALLBACK_USED for event in sink.events)
