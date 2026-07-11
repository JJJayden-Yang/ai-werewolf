"""Replay truth snapshot store.

This is intentionally outside ``contracts/`` and outside the Agent event stream.
It stores post-game/god-view replay data for UI rendering, while AgentContext
continues to receive only visibility-filtered events.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any


def build_player_snapshots(players: Mapping[str, Any]) -> list[dict[str, Any]]:
    """从 ``truth_state.players`` 生成 replay-only 玩家快照（统一格式，单一来源）。

    API 在线对局（``api.game_service``）与批量跑局（``scripts/run_batch``）都用此函数，
    保证落盘的快照字段一致。只读 PlayerState 属性，不依赖 contracts 导入。
    """
    snapshots: list[dict[str, Any]] = []
    for pid, state in players.items():
        role = state.role
        camp = state.camp
        snapshots.append(
            {
                "player_id": state.player_id or pid,
                "role": role.value,
                "camp": (
                    camp.value
                    if camp is not None
                    else ("werewolf" if role.value == "werewolf" else "villager")
                ),
                "status": state.status.value,
                "public_claim": state.public_claim,
                "vote_weight": state.vote_weight,
            }
        )
    return snapshots


class ReplayTruthStore(ABC):
    @abstractmethod
    def save_players(self, game_id: str, players: list[dict[str, Any]]) -> None:
        """Persist replay-visible player truth for one game."""

    @abstractmethod
    def get_players(self, game_id: str) -> list[dict[str, Any]]:
        """Return persisted players for ``game_id``; unknown games return ``[]``."""


class InMemoryReplayTruthStore(ReplayTruthStore):
    def __init__(self) -> None:
        self._players_by_game: dict[str, list[dict[str, Any]]] = {}

    def save_players(self, game_id: str, players: list[dict[str, Any]]) -> None:
        self._players_by_game[game_id] = deepcopy(players)

    def get_players(self, game_id: str) -> list[dict[str, Any]]:
        return deepcopy(self._players_by_game.get(game_id, []))


class JsonReplayTruthStore(ReplayTruthStore):
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_players(self, game_id: str, players: list[dict[str, Any]]) -> None:
        path = self._path_for(game_id)
        payload = {"game_id": game_id, "players": players}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_players(self, game_id: str) -> list[dict[str, Any]]:
        path = self._path_for(game_id)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        players = payload.get("players")
        if not isinstance(players, list):
            return []
        return [player for player in players if isinstance(player, dict)]

    def _path_for(self, game_id: str) -> Path:
        if "/" in game_id or "\\" in game_id or game_id in {"", ".", ".."}:
            raise ValueError(f"invalid game_id for replay truth path: {game_id!r}")
        return self.root_dir / f"{game_id}.json"
