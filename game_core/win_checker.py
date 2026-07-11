"""WinChecker —— Task A6。

胜负判断。放逐后、夜晚死亡后、猎人开枪后都必须立即调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts.enums import PlayerStatus, Role
from game_core.types import WinCheckResult

if TYPE_CHECKING:
    from game_core.types import GameSession


class WinChecker:
    def check(self, session: GameSession) -> WinCheckResult:
        """规则：
        - 所有狼人死亡 → 好人胜利；
        - 狼人数 >= 好人数 → 狼人胜利；
        - 否则继续。
        """
        alive = [
            player
            for player in session.truth_state.players.values()
            if player.status == PlayerStatus.ALIVE
        ]
        alive_wolves = [player for player in alive if player.role == Role.WEREWOLF]
        alive_good = [player for player in alive if player.role != Role.WEREWOLF]

        if not alive_wolves:
            return WinCheckResult(
                game_over=True,
                winner="villagers",
                reason="all_werewolves_dead",
            )
        if len(alive_wolves) >= len(alive_good):
            return WinCheckResult(
                game_over=True,
                winner="werewolves",
                reason="werewolves_reached_parity",
            )
        return WinCheckResult(game_over=False)
