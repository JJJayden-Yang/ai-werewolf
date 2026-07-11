"""A4：ActionResolver 真实结算 TruthState + 事件。"""

import json
import random
from pathlib import Path

from contracts import ActionType, AgentAction, EventType, GameConfig, Phase, PlayerStatus, Role
from game_core import GameSessionManager
from game_core.action_resolver import ActionResolver
from game_core.event_emitter import EventEmitter

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _session(config_name: str = "game_config_6p_debug.json", seed: int = 0):
    cfg = GameConfig.model_validate(json.loads((FIXTURES / config_name).read_text(encoding="utf-8")))
    return GameSessionManager(rng=random.Random(seed)).create_game(cfg)


def _pid(session, role: Role) -> str:
    return next(pid for pid, p in session.truth_state.players.items() if p.role == role)


def _action(session, agent_id: str, action_type: ActionType, target: str | None = None):
    player = session.truth_state.players[agent_id]
    return AgentAction(
        game_id=session.game_id,
        agent_id=agent_id,
        role=player.role,
        phase=session.current_phase,
        action_type=action_type,
        target=target,
    )


def test_wolf_nomination_sets_kill_target_and_emits_events():
    session = _session()
    resolver = ActionResolver(EventEmitter())
    wolves = [pid for pid, p in session.truth_state.players.items() if p.role == Role.WEREWOLF]
    target = next(pid for pid, p in session.truth_state.players.items() if p.role != Role.WEREWOLF)

    events = resolver.resolve_wolf_nomination(
        session,
        [_action(session, wolf, ActionType.NIGHT_KILL_NOMINATE, target) for wolf in wolves],
    )

    assert session.truth_state.night_state.kill_target == target
    assert [e.event_type for e in events].count(EventType.WOLF_NOMINATION) == len(wolves)
    assert events[-1].event_type == EventType.NIGHT_KILL_ANNOUNCED


def test_vote_tie_sets_tie_candidates_without_exile():
    session = _session()
    session.truth_state.phase = Phase.DAY_VOTE
    resolver = ActionResolver(EventEmitter())
    voters = list(session.truth_state.players)[:4]
    targets = list(session.truth_state.players)[4:6]

    events = resolver.resolve_vote(
        session,
        [
            _action(session, voters[0], ActionType.VOTE, targets[0]),
            _action(session, voters[1], ActionType.VOTE, targets[0]),
            _action(session, voters[2], ActionType.VOTE, targets[1]),
            _action(session, voters[3], ActionType.VOTE, targets[1]),
        ],
    )

    assert set(session.truth_state.round_state.tie_candidates) == set(targets)
    assert session.truth_state.round_state.last_exiled_player is None
    assert events[-1].event_type == EventType.TIE_DETECTED


def test_exile_marks_player_dead_and_emits_death_confirmed():
    session = _session()
    session.truth_state.phase = Phase.EXILE_RESOLUTION
    resolver = ActionResolver(EventEmitter())
    target = next(iter(session.truth_state.players))
    session.truth_state.round_state.last_exiled_player = target

    events = resolver.resolve_exile(session)

    assert session.truth_state.players[target].status == PlayerStatus.DEAD
    assert [e.event_type for e in events] == [EventType.EXILE, EventType.DEATH_CONFIRMED]


def test_exile_resets_last_words_done_so_every_exile_gets_last_words():
    """每轮被放逐者都应重获遗言资格：resolve_exile 必须复位 last_words_done。

    回归：旧实现里 last_words_done 一旦在 resolve_last_words 置 True 就永不复位，
    导致只有第一轮出局者能发遗言（后续轮 EXILE_LAST_WORDS 被守卫跳过）。
    """
    session = _session()
    session.truth_state.phase = Phase.EXILE_RESOLUTION
    resolver = ActionResolver(EventEmitter())
    # 模拟上一轮出局者已发表过遗言
    session.truth_state.round_state.last_words_done = True

    target = next(iter(session.truth_state.players))
    session.truth_state.round_state.last_exiled_player = target
    resolver.resolve_exile(session)

    assert session.truth_state.round_state.last_words_done is False


def test_witch_save_and_poison_resolver_updates_state_and_events():
    session = _session()
    session.truth_state.phase = Phase.NIGHT_WITCH
    resolver = ActionResolver(EventEmitter())
    witch = _pid(session, Role.WITCH)
    victim = next(pid for pid in session.truth_state.players if pid != witch)
    poison_target = next(pid for pid in session.truth_state.players if pid not in {witch, victim})

    save_events = resolver.resolve_witch_action(
        session, _action(session, witch, ActionType.SAVE, victim)
    )
    assert session.truth_state.witch_state.antidote_used is True
    assert session.truth_state.night_state.saved_target == victim
    assert save_events[0].event_type == EventType.WITCH_SAVE

    session.truth_state.witch_state.poison_used = False
    session.truth_state.night_state.saved_target = None
    poison_events = resolver.resolve_witch_action(
        session, _action(session, witch, ActionType.POISON, poison_target)
    )
    assert session.truth_state.witch_state.poison_used is True
    assert session.truth_state.night_state.poison_target == poison_target
    assert poison_events[0].event_type == EventType.WITCH_POISON


def test_tie_revote_second_tie_emits_no_exile_event():
    session = _session()
    session.truth_state.phase = Phase.DAY_TIE_REVOTE
    resolver = ActionResolver(EventEmitter())
    voters = list(session.truth_state.players)[:4]
    targets = list(session.truth_state.players)[4:6]
    session.truth_state.round_state.tie_candidates = targets

    events = resolver.resolve_tie_revote(
        session,
        [
            _action(session, voters[0], ActionType.VOTE, targets[0]),
            _action(session, voters[1], ActionType.VOTE, targets[0]),
            _action(session, voters[2], ActionType.VOTE, targets[1]),
            _action(session, voters[3], ActionType.VOTE, targets[1]),
        ],
    )

    assert events[-1].event_type == EventType.NO_EXILE_DUE_TO_SECOND_TIE
    assert session.truth_state.round_state.last_exiled_player is None
