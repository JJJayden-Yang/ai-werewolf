"""Belief observability store.

This module persists the v2.2 belief audit shapes without changing contracts.
It is intentionally append-only and in-memory first; JSONL can be added later
when replay needs durable belief audit artifacts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict

from contracts import BeliefCurvePoint, BeliefUpdateBatch


class BeliefObservabilityStore(ABC):
    @abstractmethod
    def append_update(self, batch: BeliefUpdateBatch) -> None:
        """Append one event-triggered belief update batch."""

    @abstractmethod
    def append_curve_points(self, game_id: str, points: list[BeliefCurvePoint]) -> None:
        """Append curve points derived from a saved BeliefState snapshot."""

    @abstractmethod
    def list_updates(self, game_id: str) -> list[BeliefUpdateBatch]:
        """Return update batches for a game in append order."""

    @abstractmethod
    def list_curve_points(self, game_id: str) -> list[BeliefCurvePoint]:
        """Return curve points for a game in append order."""


class InMemoryBeliefObservabilityStore(BeliefObservabilityStore):
    def __init__(self) -> None:
        self._updates_by_game: dict[str, list[BeliefUpdateBatch]] = defaultdict(list)
        self._curves_by_game: dict[str, list[BeliefCurvePoint]] = defaultdict(list)

    def append_update(self, batch: BeliefUpdateBatch) -> None:
        self._updates_by_game[batch.game_id].append(batch)

    def append_curve_points(
        self, game_id: str, points: list[BeliefCurvePoint]
    ) -> None:
        self._curves_by_game[game_id].extend(points)

    def list_updates(self, game_id: str) -> list[BeliefUpdateBatch]:
        return list(self._updates_by_game.get(game_id, []))

    def list_curve_points(self, game_id: str) -> list[BeliefCurvePoint]:
        return list(self._curves_by_game.get(game_id, []))

    def __len__(self) -> int:
        return sum(len(items) for items in self._updates_by_game.values())
