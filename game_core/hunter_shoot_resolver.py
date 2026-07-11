"""HunterShootResolver —— Task A7。

猎人技能确定性流转的统一入口。所有死亡结算后都经此判定是否进入 HUNTER_SHOOT，
禁止在各 phase 中零散判断。Phase 1 可仅 skeleton：6 人局不触发，9 人局可被调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts.enums import DeathCause, Phase, Role

if TYPE_CHECKING:
    from contracts.schemas import GameEvent

    from game_core.types import GameSession


class HunterShootResolver:
    def maybe_enter_hunter_shoot(
        self, session: GameSession, death_event: GameEvent
    ) -> Phase | None:
        """规则：
        - 死者非猎人 → None；
        - 猎人已开过枪 → None；
        - death_cause == 'witch_poison' → None（毒死不能开枪）；
        - death_cause ∈ {'night_kill', 'exile', 'hunter_shot'} → Phase.HUNTER_SHOOT。

        调用位置：DAY_ANNOUNCEMENT / EXILE_RESOLUTION / HUNTER_SHOOT 各自 death_confirmed 之后。
        """
        if death_event.target is None:
            return None
        dead_player = session.truth_state.players.get(death_event.target)
        if dead_player is None or dead_player.role != Role.HUNTER:
            return None
        if session.truth_state.hunter_state.shot_used:
            return None

        death_cause = death_event.payload.get("death_cause")
        if death_cause == DeathCause.WITCH_POISON.value:
            return None
        if death_cause in {
            DeathCause.NIGHT_KILL.value,
            DeathCause.EXILE.value,
            DeathCause.HUNTER_SHOT.value,
        }:
            return Phase.HUNTER_SHOOT
        return None
