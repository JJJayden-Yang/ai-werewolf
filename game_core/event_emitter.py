"""EventEmitter —— Task A5。

Engine 内部所有关键结果都通过这里产出结构化 GameEvent。
只负责"生成"事件，不负责落盘——落盘由 C 的 EventLogger.append 完成。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from contracts.schemas import GameEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from game_core.types import GameSession


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventEmitter:
    def __init__(self, clock: Callable[[], str] | None = None) -> None:
        # 序号按 game_id 分桶：保证 event_id 跨局唯一（C 的 EventStore 对重复 id 抛
        # DuplicateEventError），同时每局从 0001 起算，replay 按局字节可复现。
        # 单一全局自增不行：每 new GameEngine() 会重置，多局共用一个 EventStore 必撞 id。
        self._seq_by_game: dict[str, int] = {}
        # created_at 时钟可注入：默认 wall-clock UTC（真实对局有意义）；注入逻辑/定值时钟
        # 可让同 seed 的 replay 做到字节级可复现（wall-clock 每次都不同，破坏可复现）。
        self._clock = clock or _utc_now_iso

    def emit(self, session: GameSession, event_type: str, payload: dict) -> GameEvent:
        """生成一个 GameEvent。

        从 session 取 game_id / round / phase；event_id = "{game_id}_evt_{每局自增}"；
        actor / target / visibility 若在 payload 中给出则提到顶层，其余留在 payload。
        """
        game_id = session.game_id
        seq = self._seq_by_game.get(game_id, 0) + 1
        self._seq_by_game[game_id] = seq
        data = dict(payload or {})
        return GameEvent(
            event_id=f"{game_id}_evt_{seq:04d}",
            game_id=game_id,
            round=session.round,
            phase=session.current_phase,
            event_type=event_type,
            actor=data.pop("actor", None),
            target=data.pop("target", None),
            visibility=data.pop("visibility", "public"),
            payload=data,
            created_at=self._clock(),
        )
