import asyncio

from agent_policy.slow_think_protocols import NoOpDiagnosticSink, NoOpSlowThinkPolicy
from contracts import BeliefState, Phase


def test_noop_slow_think_policy_preserves_belief_state():
    policy = NoOpSlowThinkPolicy()
    belief = BeliefState(
        game_id="g_noop",
        agent_id="P1",
        round=1,
        phase=Phase.DAY_DISCUSSION,
        beliefs={},
    )

    assert policy.should_reflect("g_noop", Phase.DAY_DISCUSSION, 1) is False
    # reflect 现为 async（M4）；no-op 仍原样返回。
    assert asyncio.run(policy.reflect("g_noop", "P1", belief, {"visible": True})) is belief


def test_noop_diagnostic_sink_returns_empty_reports():
    assert NoOpDiagnosticSink().on_game_end("g_noop") == []
