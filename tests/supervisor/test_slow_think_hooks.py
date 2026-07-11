from __future__ import annotations

import asyncio

from contracts import BeliefState, Phase
from supervisor import Supervisor

from tests.supervisor.test_supervisor_flow import FakeContextAssembler, FakeEventSink, _build


class _ReflectingPolicy:
    def __init__(self) -> None:
        self.should_calls: list[tuple[str, Phase, int | None]] = []
        self.reflect_calls: list[tuple[str, str, BeliefState, object]] = []

    def should_reflect(self, game_id: str, phase: Phase, round: int | None) -> bool:
        self.should_calls.append((game_id, phase, round))
        return True

    async def reflect(
        self,
        game_id: str,
        agent_id: str,
        belief_state: BeliefState,
        context_view: object,
    ) -> BeliefState:
        self.reflect_calls.append((game_id, agent_id, belief_state, context_view))
        return belief_state.model_copy(update={"last_updated_event_id": "slow-think"})


class _DiagnosticSink:
    def __init__(self) -> None:
        self.game_ids: list[str] = []

    def on_game_end(self, game_id: str) -> list:
        self.game_ids.append(game_id)
        return []


def test_default_slow_think_and_diagnostics_none_keep_run_phase_behavior():
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")

    events = asyncio.run(supervisor.run_phase(config.game_id))

    assert sink.events == events
    assert supervisor._slow_think_results == {}
    assert supervisor._diagnostic_reports == []


def test_injected_slow_think_policy_is_called_before_decision():
    config, base_supervisor, agent, _sink = _build("game_config_6p_debug.json")
    sink = FakeEventSink()
    policy = _ReflectingPolicy()
    supervisor = Supervisor(
        base_supervisor._engine,
        FakeContextAssembler(base_supervisor._engine),
        agent,
        sink,
        slow_think_policy=policy,
    )

    asyncio.run(supervisor.run_phase(config.game_id))

    assert policy.should_calls
    assert policy.reflect_calls
    game_id, agent_id, belief_state, context_view = policy.reflect_calls[0]
    assert game_id == config.game_id
    assert agent_id
    assert belief_state.game_id == config.game_id
    assert belief_state.agent_id == agent_id
    assert belief_state.phase == Phase.NIGHT_WEREWOLF
    assert getattr(context_view, "game_id") == config.game_id
    assert supervisor._slow_think_results[(config.game_id, agent_id)].last_updated_event_id == (
        "slow-think"
    )


def test_diagnostic_sink_called_when_game_reaches_game_over():
    config, supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    diagnostic_sink = _DiagnosticSink()
    supervisor._diagnostic_sink = diagnostic_sink
    supervisor._engine.sessions.get_game(config.game_id).truth_state.phase = Phase.GAME_OVER

    asyncio.run(supervisor.run_game(config.game_id))

    assert diagnostic_sink.game_ids == [config.game_id]


def test_diagnostic_sink_called_once_for_same_finished_game():
    config, supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    diagnostic_sink = _DiagnosticSink()
    supervisor._diagnostic_sink = diagnostic_sink
    supervisor._engine.sessions.get_game(config.game_id).truth_state.phase = Phase.GAME_OVER

    asyncio.run(supervisor.run_game(config.game_id))
    asyncio.run(supervisor.run_game(config.game_id))

    assert diagnostic_sink.game_ids == [config.game_id]
