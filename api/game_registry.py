"""进程内实时对局表。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from contracts import Camp, EventType, Phase
from stores.event_store import EventStore

if TYPE_CHECKING:
    from agent_runtime import HumanInputChannel
    from game_core import GameEngine

GameStatus = Literal["pending", "running", "finished", "error"]


@dataclass
class GameRecord:
    game_id: str
    status: GameStatus
    player_count: int
    arm: str
    mode: str
    created_at: str
    event_store: EventStore
    task: asyncio.Task | None = None
    error: str | None = None
    # 上帝视角用：玩家真实身份 {player_id: role_value}。仅供观战展示，
    # 不进 AgentContext（信息隔离红线只约束 Agent 输入，观战端口不受限）。
    role_map: dict[str, str] | None = None
    human_seat: str | None = None
    human_channel: "HumanInputChannel | None" = None
    engine: "GameEngine | None" = None


class GameRegistry:
    """单进程内存 registry；MVP 重启丢失可接受。"""

    def __init__(self) -> None:
        self._records: dict[str, GameRecord] = {}

    def create(
        self,
        *,
        game_id: str,
        player_count: int,
        arm: str,
        mode: str = "llm",
        event_store: EventStore,
        role_map: dict[str, str] | None = None,
        human_seat: str | None = None,
        human_channel: "HumanInputChannel | None" = None,
        engine: "GameEngine | None" = None,
    ) -> GameRecord:
        record = GameRecord(
            game_id=game_id,
            status="pending",
            player_count=player_count,
            arm=arm,
            mode=mode,
            created_at=_utc_now(),
            event_store=event_store,
            role_map=role_map,
            human_seat=human_seat,
            human_channel=human_channel,
            engine=engine,
        )
        self._records[game_id] = record
        return record

    def get(self, game_id: str) -> GameRecord | None:
        return self._records.get(game_id)

    def list(self) -> list[GameRecord]:
        return list(self._records.values())

    def set_task(self, game_id: str, task: asyncio.Task) -> None:
        self._records[game_id].task = task

    def update_status(
        self,
        game_id: str,
        status: GameStatus,
        *,
        error: str | None = None,
    ) -> None:
        record = self._records[game_id]
        record.status = status
        record.error = error


def snapshot_record(record: GameRecord) -> dict:
    """返回前端契约需要的运行状态视图。"""
    latest = _latest_event(record)
    current_round = latest.round if latest is not None else 1
    current_phase = latest.phase if latest is not None else Phase.INIT
    current_actor = latest.actor if latest is not None else None
    winner = _winner(record)
    return {
        "game_id": record.game_id,
        "status": record.status,
        "player_count": record.player_count,
        "arm": record.arm,
        "mode": record.mode,
        "created_at": record.created_at,
        "current_round": current_round,
        "current_phase": current_phase.value,
        "current_actor": current_actor,
        "winner": winner,
        "error": record.error,
        "role_map": record.role_map,
    }


def _latest_event(record: GameRecord):
    events = record.event_store.list_by_game(record.game_id)
    return events[-1] if events else None


def _winner(record: GameRecord) -> str | None:
    for event in reversed(record.event_store.list_by_game(record.game_id)):
        if event.event_type != EventType.GAME_OVER:
            continue
        winner = event.payload.get("winner")
        if isinstance(winner, Camp):
            return winner.value
        if isinstance(winner, str):
            if winner == "werewolves":
                return "werewolf"
            if winner == "villagers":
                return "villager"
            return winner
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
