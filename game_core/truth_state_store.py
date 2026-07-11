"""TruthStateStore。

TruthState 的持久化/读取。只服务 Engine 与（游戏结束后的）Evaluator。
绝不暴露给 Agent —— 信息隔离红线。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.schemas import TruthState


class TruthStateStore:
    def get(self, game_id: str) -> TruthState:
        raise NotImplementedError

    def save(self, truth_state: TruthState) -> None:
        raise NotImplementedError
