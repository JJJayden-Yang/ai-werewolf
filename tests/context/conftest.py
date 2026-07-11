"""context 测试共享 fixture：构造 GameSession + GameEvent 用。

为避免依赖 A 的 GameEngine（跑全局 game flow 太重），用直接构造的方式
组装最小的 GameSession：

- 6 人局 (``make_6p_session``)：2 狼 + 1 预言家 + 1 女巫 + 1 猎人 + 1 村民
- 9 人局 (``make_9p_session``)：3 狼 + 1 预言家 + 1 女巫 + 1 猎人 + 3 村民
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pytest

from contracts import (
    EventType,
    GameConfig,
    GameEvent,
    GameRules,
    HunterState,
    NightState,
    Phase,
    PlayerState,
    PlayerStatus,
    Role,
    RoleCounts,
    RoundState,
    TruthState,
    Visibility,
    WitchState,
)
from game_core.types import GameSession


# ---------------------------------------------------------------------------
# Player / Session fixtures
# ---------------------------------------------------------------------------


def make_player(
    role: Role,
    status: PlayerStatus = PlayerStatus.ALIVE,
    public_claim: str | None = None,
) -> PlayerState:
    return PlayerState(role=role, status=status, public_claim=public_claim)


def make_6p_session(
    *,
    game_id: str = "g001",
    round_num: int = 1,
    phase: Phase = Phase.NIGHT_WEREWOLF,
    tie_candidates: list[str] | None = None,
    previous_vote_summary: dict[str, int] | None = None,
    is_secondary_stage: bool = False,
    overrides: dict[str, PlayerState] | None = None,
) -> GameSession:
    """6 人局：P1/P2 狼人, P3 预言家, P4 女巫, P5 猎人, P6 村民。"""
    players = {
        "P1": make_player(Role.WEREWOLF),
        "P2": make_player(Role.WEREWOLF),
        "P3": make_player(Role.SEER),
        "P4": make_player(Role.WITCH),
        "P5": make_player(Role.HUNTER),
        "P6": make_player(Role.VILLAGER),
    }
    if overrides:
        for pid, p in overrides.items():
            players[pid] = p

    config = GameConfig(
        game_id=game_id,
        player_count=6,
        roles=RoleCounts(werewolf=2, seer=1, witch=1, hunter=1, villager=1),
        rules=GameRules(),
    )
    truth_state = TruthState(
        game_id=game_id,
        round=round_num,
        phase=phase,
        players=players,
        witch_state=WitchState(),
        hunter_state=HunterState(),
        night_state=NightState(),
        round_state=RoundState(
            tie_candidates=tie_candidates or [],
            previous_vote_summary=previous_vote_summary or {},
            is_secondary_stage=is_secondary_stage,
        ),
    )
    return GameSession(game_id=game_id, config=config, truth_state=truth_state)


def make_9p_session(
    *,
    game_id: str = "g009",
    round_num: int = 1,
    phase: Phase = Phase.NIGHT_WEREWOLF,
    tie_candidates: list[str] | None = None,
    previous_vote_summary: dict[str, int] | None = None,
    is_secondary_stage: bool = False,
    overrides: dict[str, PlayerState] | None = None,
) -> GameSession:
    """9 人局：P1/P2/P3 狼人, P4 预言家, P5 女巫, P6 猎人, P7/P8/P9 村民。"""
    players = {
        "P1": make_player(Role.WEREWOLF),
        "P2": make_player(Role.WEREWOLF),
        "P3": make_player(Role.WEREWOLF),
        "P4": make_player(Role.SEER),
        "P5": make_player(Role.WITCH),
        "P6": make_player(Role.HUNTER),
        "P7": make_player(Role.VILLAGER),
        "P8": make_player(Role.VILLAGER),
        "P9": make_player(Role.VILLAGER),
    }
    if overrides:
        for pid, p in overrides.items():
            players[pid] = p

    config = GameConfig(
        game_id=game_id,
        player_count=9,
        roles=RoleCounts(werewolf=3, seer=1, witch=1, hunter=1, villager=3),
        rules=GameRules(),
    )
    truth_state = TruthState(
        game_id=game_id,
        round=round_num,
        phase=phase,
        players=players,
        witch_state=WitchState(),
        hunter_state=HunterState(),
        night_state=NightState(),
        round_state=RoundState(
            tie_candidates=tie_candidates or [],
            previous_vote_summary=previous_vote_summary or {},
            is_secondary_stage=is_secondary_stage,
        ),
    )
    return GameSession(game_id=game_id, config=config, truth_state=truth_state)


class FakeSessionProvider:
    """实现 GameSessionProvider Protocol —— 鸭子类型。

    方法名 ``get_session`` 跟 A 的 ``game_core.SessionProvider`` 对齐
    （A 2026-05-22 21:19 群里确认）。
    """

    def __init__(self, session: GameSession) -> None:
        self._session = session

    def get_session(self, game_id: str) -> GameSession:
        if game_id != self._session.game_id:
            raise ValueError(f"unknown game_id {game_id}")
        return self._session


# ---------------------------------------------------------------------------
# Event factory
# ---------------------------------------------------------------------------


_EVENT_SEQ = 0


def _next_event_id() -> str:
    global _EVENT_SEQ
    _EVENT_SEQ += 1
    return f"evt_test_{_EVENT_SEQ:04d}"


def make_event(
    event_type: EventType,
    *,
    round_num: int = 1,
    phase: Phase = Phase.DAY_DISCUSSION,
    actor: str | None = None,
    target: str | None = None,
    visibility: Visibility = Visibility.PUBLIC,
    payload: dict | None = None,
    game_id: str = "g001",
) -> GameEvent:
    return GameEvent(
        event_id=_next_event_id(),
        game_id=game_id,
        round=round_num,
        phase=phase,
        event_type=event_type,
        actor=actor,
        target=target,
        visibility=visibility,
        payload=payload or {},
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# 常用 fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def session_6p():
    return make_6p_session()


@pytest.fixture
def fake_provider(session_6p):
    return FakeSessionProvider(session_6p)


@pytest.fixture
def session_9p():
    return make_9p_session()


@pytest.fixture
def fake_provider_9p(session_9p):
    return FakeSessionProvider(session_9p)
