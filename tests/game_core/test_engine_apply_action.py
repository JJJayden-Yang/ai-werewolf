"""A3：GameEngine.apply_action 也必须作为规则校验最后防线。"""

import json
from pathlib import Path

from contracts import ActionType, AgentAction, EventType, GameConfig, Phase, PlayerStatus, Role
from game_core import GameEngine

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _engine_with_6p_game():
    engine = GameEngine()
    config = GameConfig.model_validate(
        json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    )
    session = engine.sessions.create_game(config)
    return engine, session


def test_engine_apply_action_rejects_invalid_action():
    engine, session = _engine_with_6p_game()
    wolf = next(pid for pid, p in session.truth_state.players.items() if p.role == Role.WEREWOLF)
    invalid = AgentAction(
        game_id=session.game_id,
        agent_id=wolf,
        role=Role.WEREWOLF,
        phase=Phase.NIGHT_WEREWOLF,
        action_type=ActionType.SPEAK,
        target=None,
    )

    events = engine.apply_action(session.game_id, invalid)

    assert len(events) == 1
    assert events[0].event_type == EventType.RULE_VALIDATION
    assert events[0].payload["is_valid"] is False
    assert events[0].payload["violation_type"] == "action_type_not_allowed"


def test_engine_apply_actions_invalid_vote_does_not_void_whole_batch():
    """一张废票只作废自己（记 rule_validation），合法票照常统计——不再整批作废。"""
    engine, session = _engine_with_6p_game()
    session.truth_state.phase = Phase.DAY_VOTE
    players = list(session.truth_state.players)  # P1..P6，全存活
    target = players[2]

    def _vote(pid, tgt):
        return AgentAction(
            game_id=session.game_id,
            agent_id=pid,
            role=session.truth_state.players[pid].role,
            phase=Phase.DAY_VOTE,
            action_type=ActionType.VOTE,
            target=tgt,
        )

    actions = [
        _vote(players[0], target),  # 合法
        _vote(players[1], target),  # 合法
        _vote(players[3], players[3]),  # 非法：投自己
    ]

    events = engine.apply_actions(session.game_id, actions)

    # 非法票被记录
    assert any(
        e.event_type == EventType.RULE_VALIDATION and e.payload["violation_type"] == "target_self"
        for e in events
    )
    # 合法票照常结算：两张 vote_cast + 明确出局者
    assert sum(1 for e in events if e.event_type == EventType.VOTE_CAST) == 2
    assert session.truth_state.round_state.last_exiled_player == target


def test_engine_resolve_day_announcement_marks_night_death():
    engine, session = _engine_with_6p_game()
    victim = next(pid for pid, p in session.truth_state.players.items() if p.role != Role.WEREWOLF)
    session.truth_state.phase = Phase.DAY_ANNOUNCEMENT
    session.truth_state.night_state.kill_target = victim

    events = engine.resolve_phase(session.game_id)

    assert session.truth_state.players[victim].status == PlayerStatus.DEAD
    assert any(e.event_type == EventType.DEATH_CONFIRMED and e.target == victim for e in events)
    assert any(e.event_type == EventType.DAY_ANNOUNCEMENT for e in events)


def test_engine_resolve_exile_can_emit_game_over():
    engine, session = _engine_with_6p_game()
    session.truth_state.phase = Phase.EXILE_RESOLUTION
    wolf = next(pid for pid, p in session.truth_state.players.items() if p.role == Role.WEREWOLF)
    # 只剩一个狼，放逐后好人胜。
    for pid, player in session.truth_state.players.items():
        if player.role == Role.WEREWOLF and pid != wolf:
            player.status = PlayerStatus.DEAD
    session.truth_state.round_state.last_exiled_player = wolf

    events = engine.resolve_phase(session.game_id)

    assert session.truth_state.players[wolf].status == PlayerStatus.DEAD
    assert events[-1].event_type == EventType.GAME_OVER
    assert events[-1].payload["winner"] == "villagers"


def test_engine_tie_revote_second_tie_goes_to_no_exile_phase():
    engine, session = _engine_with_6p_game()
    session.truth_state.phase = Phase.DAY_TIE_REVOTE
    players = list(session.truth_state.players)
    targets = players[4:6]
    session.truth_state.round_state.tie_candidates = targets

    actions = [
        AgentAction(
            game_id=session.game_id,
            agent_id=players[0],
            role=session.truth_state.players[players[0]].role,
            phase=Phase.DAY_TIE_REVOTE,
            action_type=ActionType.VOTE,
            target=targets[0],
        ),
        AgentAction(
            game_id=session.game_id,
            agent_id=players[1],
            role=session.truth_state.players[players[1]].role,
            phase=Phase.DAY_TIE_REVOTE,
            action_type=ActionType.VOTE,
            target=targets[1],
        ),
    ]

    events = engine.apply_actions(session.game_id, actions)
    next_phase = engine.advance_phase(session.game_id, events)

    assert events[-1].event_type == EventType.NO_EXILE_DUE_TO_SECOND_TIE
    assert next_phase == Phase.NO_EXILE_RESOLUTION


def test_engine_win_check_max_rounds_emits_game_over_event():
    engine, session = _engine_with_6p_game()
    session.truth_state.phase = Phase.WIN_CHECK
    session.truth_state.round = session.config.max_rounds

    events = engine.resolve_phase(session.game_id)
    next_phase = engine.advance_phase(session.game_id, events)

    assert [event.event_type for event in events] == [EventType.WIN_CHECK, EventType.GAME_OVER]
    assert events[0].payload == {
        "game_over": True,
        "winner": None,
        "reason": "max_rounds_reached",
    }
    assert events[1].payload == {"winner": None, "reason": "max_rounds_reached"}
    assert next_phase == Phase.GAME_OVER
