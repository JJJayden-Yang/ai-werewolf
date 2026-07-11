"""A4/A7：猎人阶段优先级与返回路径。"""

import json
import random
from pathlib import Path

from contracts import ActionType, AgentAction, EventType, GameConfig, Phase, PlayerStatus, Role
from game_core import GameEngine

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _engine_with_9p_game(seed: int = 0):
    engine = GameEngine()
    config = GameConfig.model_validate(
        json.loads((FIXTURES / "game_config_9p_mvp.json").read_text(encoding="utf-8"))
    )
    session = engine.sessions.create_game(config)
    return engine, session


def _pid(session, role: Role) -> str:
    return next(pid for pid, p in session.truth_state.players.items() if p.role == role)


def _hunter_pass(session, hunter: str) -> AgentAction:
    return AgentAction(
        game_id=session.game_id,
        agent_id=hunter,
        role=Role.HUNTER,
        phase=Phase.HUNTER_SHOOT,
        action_type=ActionType.HUNTER_SHOOT,
        target=None,
    )


def test_night_killed_hunter_enters_hunter_shoot_when_game_continues():
    engine, session = _engine_with_9p_game()
    hunter = _pid(session, Role.HUNTER)
    session.truth_state.phase = Phase.DAY_ANNOUNCEMENT
    session.truth_state.night_state.kill_target = hunter

    events = engine.resolve_phase(session.game_id)
    next_phase = engine.advance_phase(session.game_id, events)

    assert any(
        e.event_type == EventType.DEATH_CONFIRMED
        and e.target == hunter
        and e.payload.get("hunter_can_shoot") is True
        for e in events
    )
    assert not any(e.event_type == EventType.GAME_OVER for e in events)
    assert next_phase == Phase.HUNTER_SHOOT


def test_night_killed_hunter_at_wolf_parity_still_enters_hunter_shoot_before_game_over():
    engine, session = _engine_with_9p_game()
    hunter = _pid(session, Role.HUNTER)
    good_dead = 0
    for pid, player in session.truth_state.players.items():
        if pid != hunter and player.role != Role.WEREWOLF and good_dead < 2:
            player.status = PlayerStatus.DEAD
            good_dead += 1
    session.truth_state.phase = Phase.DAY_ANNOUNCEMENT
    session.truth_state.night_state.kill_target = hunter

    events = engine.resolve_phase(session.game_id)
    next_phase = engine.advance_phase(session.game_id, events)

    assert any(e.payload.get("hunter_can_shoot") is True for e in events)
    assert not any(e.event_type == EventType.GAME_OVER for e in events)
    assert next_phase == Phase.HUNTER_SHOOT


def test_hunter_shoot_after_night_death_returns_to_day_discussion():
    engine, session = _engine_with_9p_game()
    hunter = _pid(session, Role.HUNTER)
    session.truth_state.phase = Phase.DAY_ANNOUNCEMENT
    session.truth_state.night_state.kill_target = hunter
    events = engine.resolve_phase(session.game_id)
    engine.advance_phase(session.game_id, events)

    shoot_events = engine.apply_action(session.game_id, _hunter_pass(session, hunter))
    next_phase = engine.advance_phase(session.game_id, shoot_events)

    assert next_phase == Phase.DAY_DISCUSSION


def test_hunter_shoot_after_exile_returns_to_last_words():
    engine, session = _engine_with_9p_game()
    hunter = _pid(session, Role.HUNTER)
    session.truth_state.phase = Phase.EXILE_RESOLUTION
    session.truth_state.round_state.last_exiled_player = hunter

    events = engine.resolve_phase(session.game_id)
    next_phase = engine.advance_phase(session.game_id, events)
    assert next_phase == Phase.HUNTER_SHOOT

    shoot_events = engine.apply_action(session.game_id, _hunter_pass(session, hunter))
    next_phase = engine.advance_phase(session.game_id, shoot_events)

    assert next_phase == Phase.EXILE_LAST_WORDS


def test_hunter_shot_can_trigger_immediate_game_over():
    engine, session = _engine_with_9p_game()
    hunter = _pid(session, Role.HUNTER)
    wolf = _pid(session, Role.WEREWOLF)
    for pid, player in session.truth_state.players.items():
        if player.role == Role.WEREWOLF and pid != wolf:
            player.status = PlayerStatus.DEAD
    session.truth_state.players[hunter].status = PlayerStatus.DEAD
    session.truth_state.phase = Phase.HUNTER_SHOOT

    action = AgentAction(
        game_id=session.game_id,
        agent_id=hunter,
        role=Role.HUNTER,
        phase=Phase.HUNTER_SHOOT,
        action_type=ActionType.HUNTER_SHOOT,
        target=wolf,
    )
    events = engine.apply_action(session.game_id, action)

    assert session.truth_state.players[wolf].status == PlayerStatus.DEAD
    assert events[-1].event_type == EventType.GAME_OVER
    assert events[-1].payload["winner"] == "villagers"
