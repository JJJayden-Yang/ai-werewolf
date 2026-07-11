"""ActionResolver —— Task A4。

把合法 action 应用到 TruthState 并产出 GameEvent（经 EventEmitter）。
所有结算结果只通过 EventEmitter 产生事件，Engine/Supervisor 不手写结算事件。
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from contracts.enums import ActionType, Camp, DeathCause, EventType, PlayerStatus, Visibility
from game_core.event_emitter import EventEmitter

if TYPE_CHECKING:
    from contracts.schemas import AgentAction, GameEvent

    from game_core.types import GameSession


class ActionResolver:
    def __init__(self, event_emitter: EventEmitter | None = None) -> None:
        self._events = event_emitter or EventEmitter()

    def resolve_wolf_nomination(
        self, session: GameSession, actions: list[AgentAction]
    ) -> list[GameEvent]:
        """方案 A：各狼各自提名；多数一致则杀，不一致则在提名目标中稳定选一个。

        文档说“随机选择”；A4 先用稳定顺序，保证 smoke test/replay 可复现。
        后续如果需要真实随机，应通过可注入 RNG 实现，而不是使用全局 random。
        """
        events: list[GameEvent] = []
        nominations: dict[str, str] = {}
        for action in actions:
            if action.target is None:
                continue
            nominations[action.agent_id] = action.target
            events.append(
                self._events.emit(
                    session,
                    EventType.WOLF_NOMINATION.value,
                    {"actor": action.agent_id, "target": action.target},
                )
            )

        session.truth_state.night_state.wolf_nominations = nominations
        if nominations:
            counts = Counter(nominations.values())
            max_votes = max(counts.values())
            candidates = sorted(target for target, count in counts.items() if count == max_votes)
            kill_target = candidates[0]
            session.truth_state.night_state.kill_target = kill_target
            events.append(
                self._events.emit(
                    session,
                    EventType.NIGHT_KILL_ANNOUNCED.value,
                    {"target": kill_target},
                )
            )
        return events

    def resolve_seer_check(self, session: GameSession, action: AgentAction) -> list[GameEvent]:
        """产出 private 的 seer_check_result（仅预言家可见）。"""
        target = session.truth_state.players[action.target]
        result = Camp.WEREWOLF.value if target.camp == Camp.WEREWOLF else Camp.VILLAGER.value
        return [
            self._events.emit(
                session,
                EventType.SEER_CHECK_RESULT.value,
                {
                    "actor": action.agent_id,
                    "target": action.target,
                    "visibility": Visibility.PRIVATE_TO_SEER.value,
                    "result": result,
                },
            )
        ]

    def resolve_witch_action(self, session: GameSession, action: AgentAction) -> list[GameEvent]:
        """save / poison / skip；同一晚不可同时救与毒；用过的药不可再用。"""
        if action.action_type == ActionType.SKIP:
            return [
                self._events.emit(
                    session,
                    EventType.AGENT_ACTION.value,
                    {"actor": action.agent_id, "action_type": ActionType.SKIP.value},
                )
            ]
        if action.action_type == ActionType.SAVE:
            session.truth_state.witch_state.antidote_used = True
            session.truth_state.night_state.saved_target = action.target
            return [
                self._events.emit(
                    session,
                    EventType.WITCH_SAVE.value,
                    {
                        "actor": action.agent_id,
                        "target": action.target,
                        "visibility": Visibility.PRIVATE_TO_WITCH.value,
                    },
                )
            ]
        if action.action_type == ActionType.POISON:
            session.truth_state.witch_state.poison_used = True
            session.truth_state.night_state.poison_target = action.target
            return [
                self._events.emit(
                    session,
                    EventType.WITCH_POISON.value,
                    {
                        "actor": action.agent_id,
                        "target": action.target,
                        "visibility": Visibility.PRIVATE_TO_WITCH.value,
                    },
                )
            ]
        return []

    def resolve_speech(self, session: GameSession, action: AgentAction) -> list[GameEvent]:
        player = session.truth_state.players[action.agent_id]
        if action.role_claim is not None:
            player.public_claim = action.role_claim.value
        return [
            self._events.emit(
                session,
                EventType.SPEECH.value,
                {
                    "actor": action.agent_id,
                    "public_message": action.public_message,
                    "role_claim": action.role_claim.value if action.role_claim else None,
                    "claim_result": action.claim_result.model_dump(mode="json")
                    if action.claim_result
                    else None,
                },
            )
        ]

    def resolve_vote(
        self,
        session: GameSession,
        actions: list[AgentAction],
        *,
        include_vote_events: bool = True,
    ) -> list[GameEvent]:
        """统计票数；平票产出 tie_detected 并交由 PhaseController 进入二次流程。"""
        events, vote_summary = self._vote_events_and_summary(
            session, actions, include_vote_events=include_vote_events
        )
        session.truth_state.round_state.previous_vote_summary = vote_summary
        session.truth_state.round_state.tie_vote_round = 0
        top = self._top_vote_targets(vote_summary)
        if len(top) > 1:
            session.truth_state.round_state.tie_candidates = top
            session.truth_state.round_state.last_exiled_player = None
            events.append(
                self._events.emit(
                    session,
                    EventType.TIE_DETECTED.value,
                    {"tie_candidates": top, "vote_summary": vote_summary},
                )
            )
        elif top:
            session.truth_state.round_state.tie_candidates = []
            session.truth_state.round_state.last_exiled_player = top[0]
        return events

    def resolve_exile(self, session: GameSession) -> list[GameEvent]:
        """放逐最高票者，标记死亡（death_cause='exile'）。"""
        target = session.truth_state.round_state.last_exiled_player
        if target is None:
            return []
        session.truth_state.players[target].status = PlayerStatus.DEAD
        # 新出局者重获遗言资格：last_words_done 在 resolve_last_words 里置 True 后从不复位，
        # 不在此处复位会导致只有第一轮被放逐者能发遗言（后续轮 EXILE_LAST_WORDS 被
        # phase_controller 的 `not last_words_done` 守卫跳过）。每次真正有人出局都重置一次。
        session.truth_state.round_state.last_words_done = False
        return [
            self._events.emit(session, EventType.EXILE.value, {"target": target}),
            self._events.emit(
                session,
                EventType.DEATH_CONFIRMED.value,
                {"target": target, "death_cause": DeathCause.EXILE.value},
            ),
        ]

    def resolve_last_words(self, session: GameSession, action: AgentAction) -> list[GameEvent]:
        session.truth_state.round_state.last_words_done = True
        return [
            self._events.emit(
                session,
                EventType.LAST_WORDS.value,
                {"actor": action.agent_id, "public_message": action.public_message},
            )
        ]

    def resolve_tie_revote(
        self,
        session: GameSession,
        actions: list[AgentAction],
        *,
        include_vote_events: bool = True,
    ) -> list[GameEvent]:
        """二次投票，只能投 tie_candidates；再平票产出 no_exile_due_to_second_tie。"""
        events, vote_summary = self._vote_events_and_summary(
            session, actions, include_vote_events=include_vote_events
        )
        session.truth_state.round_state.previous_vote_summary = vote_summary
        session.truth_state.round_state.tie_vote_round = 1
        top = self._top_vote_targets(vote_summary)
        if len(top) > 1:
            session.truth_state.round_state.tie_candidates = top
            session.truth_state.round_state.last_exiled_player = None
            events.append(
                self._events.emit(
                    session,
                    EventType.NO_EXILE_DUE_TO_SECOND_TIE.value,
                    {"tie_candidates": top, "vote_summary": vote_summary},
                )
            )
        elif top:
            session.truth_state.round_state.tie_candidates = []
            session.truth_state.round_state.last_exiled_player = top[0]
        return events

    def resolve_hunter_shoot(self, session: GameSession, action: AgentAction) -> list[GameEvent]:
        """猎人开枪结算；产出 hunter_shot + death_confirmed，随后必须立即 WinChecker。"""
        session.truth_state.hunter_state.shot_used = True
        if action.target is None:
            return [
                self._events.emit(
                    session,
                    EventType.HUNTER_SHOT.value,
                    {"actor": action.agent_id, "target": None, "pass": True},
                )
            ]
        session.truth_state.players[action.target].status = PlayerStatus.DEAD
        return [
            self._events.emit(
                session,
                EventType.HUNTER_SHOT.value,
                {"actor": action.agent_id, "target": action.target},
            ),
            self._events.emit(
                session,
                EventType.DEATH_CONFIRMED.value,
                {"target": action.target, "death_cause": DeathCause.HUNTER_SHOT.value},
            ),
        ]

    def resolve_vote_cast(self, session: GameSession, action: AgentAction) -> list[GameEvent]:
        if action.target is None:
            return []
        return [
            self._events.emit(
                session,
                EventType.VOTE_CAST.value,
                {"actor": action.agent_id, "target": action.target},
            )
        ]

    def _vote_events_and_summary(
        self,
        session: GameSession,
        actions: list[AgentAction],
        *,
        include_vote_events: bool = True,
    ) -> tuple[list[GameEvent], dict[str, int]]:
        events: list[GameEvent] = []
        vote_summary: dict[str, int] = {}
        for action in actions:
            if action.target is None:
                continue
            vote_summary[action.target] = vote_summary.get(action.target, 0) + 1
            if include_vote_events:
                events.extend(self.resolve_vote_cast(session, action))
        return events, vote_summary

    @staticmethod
    def _top_vote_targets(vote_summary: dict[str, int]) -> list[str]:
        if not vote_summary:
            return []
        max_votes = max(vote_summary.values())
        return sorted(target for target, count in vote_summary.items() if count == max_votes)
