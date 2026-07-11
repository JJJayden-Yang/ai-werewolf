"""Supervisor 的注入边界（Protocol）。

把跨边界依赖定义成结构化协议，fake（测试）和真实实现（C 的 ContextAssembler / EventStore、
B/C 的 AgentRuntime）都满足它们。这样 supervisor/ 不必硬 import context / agent_runtime 的具体实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from typing import Any

    from contracts import AgentContext, BadCaseReport, BeliefState, GameEvent, Phase


class ContextAssembler(Protocol):
    def build_context(self, game_id: str, agent_id: str, phase: Phase) -> AgentContext: ...


class AgentRuntime(Protocol):
    async def act(self, context: dict) -> dict: ...


class EventSink(Protocol):
    def append_many(self, events: list[GameEvent]) -> None: ...


class BeliefUpdater(Protocol):
    def update(self, game_id: str, event_id: str) -> None: ...


class SlowThinkPolicy(Protocol):
    def should_reflect(self, game_id: str, phase: Phase, round: int | None) -> bool: ...

    async def reflect(
        self,
        game_id: str,
        agent_id: str,
        belief_state: BeliefState,
        context_view: "Any",
    ) -> BeliefState: ...


class DiagnosticSink(Protocol):
    def on_game_end(self, game_id: str) -> list[BadCaseReport]: ...
