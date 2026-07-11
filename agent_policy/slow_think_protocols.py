"""Slow-think and diagnostic hook protocols.

These are Day-1 signatures only. The default implementations are no-op, so
injecting them must not change game behavior unless a caller provides real
logic.
"""

from __future__ import annotations

from typing import Any

from contracts import BadCaseReport, BeliefState, Phase
from supervisor.protocols import DiagnosticSink, SlowThinkPolicy


class NoOpSlowThinkPolicy:
    def should_reflect(self, game_id: str, phase: Phase, round: int | None) -> bool:
        return False

    async def reflect(
        self,
        game_id: str,
        agent_id: str,
        belief_state: BeliefState,
        context_view: Any,
    ) -> BeliefState:
        return belief_state


class NoOpDiagnosticSink:
    def on_game_end(self, game_id: str) -> list[BadCaseReport]:
        return []
