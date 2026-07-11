"""PhaseController —— Task A2。

状态机流转 + 当前阶段需要哪些玩家行动。
空 required actors 表示该阶段自动跳过，不报错、不卡死。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts.enums import EventType, Phase, PlayerStatus, Role

if TYPE_CHECKING:
    from contracts.schemas import GameEvent

    from game_core.types import GameSession


class PhaseController:
    def get_required_actors(self, session: GameSession, phase: Phase) -> list[str]:
        """返回当前 phase 需要行动的存活玩家。

        A1.5/A2 最小实现：按 phase 取存活的应行动者（死亡角色因不在 alive 集合而天然跳过）。
        A2 会补全平票/遗言/猎人开枪等阶段与更细的跳过规则。
        """
        players = session.truth_state.players
        alive = sorted(pid for pid, p in players.items() if p.status == PlayerStatus.ALIVE)

        if phase == Phase.NIGHT_WEREWOLF:
            return [pid for pid in alive if players[pid].role == Role.WEREWOLF]
        if phase == Phase.NIGHT_SEER:
            return [pid for pid in alive if players[pid].role == Role.SEER]
        if phase == Phase.NIGHT_WITCH:
            return [pid for pid in alive if players[pid].role == Role.WITCH]
        if phase == Phase.HUNTER_SHOOT:
            rs = session.truth_state.round_state
            return [
                pid
                for pid, p in players.items()
                if p.role == Role.HUNTER
                and p.status == PlayerStatus.DEAD
                and not session.truth_state.hunter_state.shot_used
                and rs.hunter_death_cause != "witch_poison"
            ]
        if phase in (
            Phase.DAY_DISCUSSION,
            Phase.DAY_VOTE,
            Phase.DAY_TIE_DISCUSSION,
            Phase.DAY_TIE_REVOTE,
        ):
            return alive
        if phase == Phase.EXILE_LAST_WORDS:
            # 遗言对象 = 本轮被放逐者。A4 设置 round_state.last_exiled_player 后自动生效；
            # A2 该值恒 None → 返回 []（run_game 跳过遗言阶段）。
            rs = session.truth_state.round_state
            if (
                session.config.rules.last_words_enabled
                and rs.last_exiled_player is not None
                and not rs.last_words_done
                and rs.last_exiled_player in players
            ):
                return [rs.last_exiled_player]
            return []
        return []

    def next_phase(self, session: GameSession, latest_events: list[GameEvent]) -> Phase:
        """根据当前 phase + 最新事件决定下一个 phase（确定性、不死循环）。

        A2：MVP 6 人线性流程 + 平票/猎人预留分支：
        - 平票分支由 latest_events 中的 tie_detected 触发（A4 ActionResolver 产出后生效）；
        - 猎人分支预留（A4/A7 接 HunterShootResolver 后生效），A2 skeleton 恒不触发；
        - 终局以 round >= max_rounds 收敛（真实胜负判定在 A4/A6 WinChecker 接入）。
        """
        phase = session.truth_state.phase
        hunter_should_shoot = self._hunter_should_shoot(latest_events)
        if hunter_should_shoot:
            return Phase.HUNTER_SHOOT
        if any(e.event_type == EventType.GAME_OVER for e in latest_events):
            return Phase.GAME_OVER

        if phase in (Phase.INIT, Phase.ROLE_ASSIGNMENT):
            return Phase.NIGHT_WEREWOLF
        if phase == Phase.NIGHT_WEREWOLF:
            return Phase.NIGHT_SEER
        if phase == Phase.NIGHT_SEER:
            return Phase.NIGHT_WITCH
        if phase == Phase.NIGHT_WITCH:
            return Phase.DAY_ANNOUNCEMENT
        if phase == Phase.DAY_ANNOUNCEMENT:
            return Phase.DAY_DISCUSSION
        if phase == Phase.DAY_DISCUSSION:
            return Phase.DAY_VOTE
        if phase == Phase.DAY_VOTE:
            return (
                Phase.DAY_TIE_DISCUSSION
                if self._is_tie(latest_events)
                else Phase.EXILE_RESOLUTION
            )
        if phase == Phase.DAY_TIE_DISCUSSION:
            return Phase.DAY_TIE_REVOTE
        if phase == Phase.DAY_TIE_REVOTE:
            return (
                Phase.NO_EXILE_RESOLUTION
                if self._is_second_tie(latest_events)
                else Phase.EXILE_RESOLUTION
            )
        if phase == Phase.EXILE_RESOLUTION:
            return Phase.EXILE_LAST_WORDS
        if phase == Phase.HUNTER_SHOOT:
            return session.hunter_shoot_return_phase or Phase.DAY_DISCUSSION
        if phase == Phase.NO_EXILE_RESOLUTION:
            return Phase.WIN_CHECK
        if phase == Phase.EXILE_LAST_WORDS:
            return Phase.WIN_CHECK
        if phase == Phase.WIN_CHECK:
            if session.truth_state.round >= session.config.max_rounds:
                return Phase.GAME_OVER
            return Phase.NIGHT_WEREWOLF
        return Phase.GAME_OVER

    def should_skip_phase(self, session: GameSession, phase: Phase) -> bool:
        """无 required actors 的阶段可自动跳过（死亡角色夜晚、无遗言对象、纯结算阶段等）。"""
        return len(self.get_required_actors(session, phase)) == 0

    @staticmethod
    def _is_tie(latest_events: list[GameEvent]) -> bool:
        return any(e.event_type == EventType.TIE_DETECTED for e in latest_events)

    @staticmethod
    def _is_second_tie(latest_events: list[GameEvent]) -> bool:
        return any(e.event_type == EventType.NO_EXILE_DUE_TO_SECOND_TIE for e in latest_events)

    @staticmethod
    def _hunter_should_shoot(latest_events: list[GameEvent]) -> bool:
        return any(
            e.event_type == EventType.DEATH_CONFIRMED
            and e.payload.get("hunter_can_shoot") is True
            for e in latest_events
        )
