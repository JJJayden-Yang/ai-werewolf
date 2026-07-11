"""A3：RuleValidator —— 阶段/action/actor/target/角色专属规则校验。"""

import json
import random
from pathlib import Path

from contracts import ActionType, DeathCause, GameConfig, Phase, PlayerStatus, Role
from contracts.schemas import AgentAction
from game_core import GameSessionManager, RuleValidator

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _session(config_name: str = "game_config_6p_debug.json", seed: int = 0):
    cfg = GameConfig.model_validate(json.loads((FIXTURES / config_name).read_text(encoding="utf-8")))
    return GameSessionManager(rng=random.Random(seed)).create_game(cfg)


def _pid(session, role: Role) -> str:
    return next(pid for pid, p in session.truth_state.players.items() if p.role == role)


def _pids(session, role: Role) -> list[str]:
    return [pid for pid, p in session.truth_state.players.items() if p.role == role]


def _alive_non_role(session, role: Role, *, exclude: str | None = None) -> str:
    return next(
        pid
        for pid, p in session.truth_state.players.items()
        if p.role != role and p.status == PlayerStatus.ALIVE and pid != exclude
    )


def _action(
    session,
    agent_id: str,
    action_type: ActionType,
    *,
    target: str | None = None,
    phase: Phase | None = None,
) -> AgentAction:
    player = session.truth_state.players[agent_id]
    return AgentAction(
        game_id=session.game_id,
        agent_id=agent_id,
        role=player.role,
        phase=phase or session.current_phase,
        action_type=action_type,
        target=target,
    )


def test_werewolf_nomination_valid_and_cannot_kill_teammate():
    session = _session()
    validator = RuleValidator()
    wolf = _pid(session, Role.WEREWOLF)
    villager_side = _alive_non_role(session, Role.WEREWOLF)

    result = validator.validate(
        session, _action(session, wolf, ActionType.NIGHT_KILL_NOMINATE, target=villager_side)
    )
    assert result.is_valid is True

    teammate = next(pid for pid in _pids(session, Role.WEREWOLF) if pid != wolf)
    result = validator.validate(
        session, _action(session, wolf, ActionType.NIGHT_KILL_NOMINATE, target=teammate)
    )
    assert result.is_valid is False
    assert result.violation_type == "wolf_cannot_kill_teammate"


def test_action_type_phase_and_required_actor_are_checked():
    session = _session()
    validator = RuleValidator()
    seer = _pid(session, Role.SEER)
    target = _alive_non_role(session, Role.SEER, exclude=seer)

    # 当前 phase 是 NIGHT_WEREWOLF，预言家既不是 required actor，check 也不是本阶段 action。
    result = validator.validate(session, _action(session, seer, ActionType.CHECK, target=target))
    assert result.is_valid is False
    assert result.violation_type == "action_type_not_allowed"

    wolf = _pid(session, Role.WEREWOLF)
    wrong_phase = _action(
        session, wolf, ActionType.NIGHT_KILL_NOMINATE, target=target, phase=Phase.DAY_VOTE
    )
    result = validator.validate(session, wrong_phase)
    assert result.is_valid is False
    assert result.violation_type == "phase_mismatch"


def test_action_role_mismatch_rejected():
    session = _session()
    validator = RuleValidator()
    wolf = _pid(session, Role.WEREWOLF)
    target = _alive_non_role(session, Role.WEREWOLF)

    action = _action(session, wolf, ActionType.NIGHT_KILL_NOMINATE, target=target)
    action.role = Role.SEER

    result = validator.validate(session, action)

    assert result.is_valid is False
    assert result.violation_type == "actor_role_mismatch"


def test_dead_actor_and_dead_target_are_rejected():
    session = _session()
    validator = RuleValidator()
    wolf = _pid(session, Role.WEREWOLF)
    target = _alive_non_role(session, Role.WEREWOLF)

    session.truth_state.players[wolf].status = PlayerStatus.DEAD
    result = validator.validate(
        session, _action(session, wolf, ActionType.NIGHT_KILL_NOMINATE, target=target)
    )
    assert result.is_valid is False
    assert result.violation_type == "actor_not_alive"

    session = _session()
    wolf = _pid(session, Role.WEREWOLF)
    target = _alive_non_role(session, Role.WEREWOLF)
    session.truth_state.players[target].status = PlayerStatus.DEAD
    result = validator.validate(
        session, _action(session, wolf, ActionType.NIGHT_KILL_NOMINATE, target=target)
    )
    assert result.is_valid is False
    assert result.violation_type == "target_not_alive"


def test_seer_cannot_check_self_and_can_check_alive_other():
    session = _session()
    session.truth_state.phase = Phase.NIGHT_SEER
    validator = RuleValidator()
    seer = _pid(session, Role.SEER)
    target = _alive_non_role(session, Role.SEER, exclude=seer)

    assert validator.validate(session, _action(session, seer, ActionType.CHECK, target=target)).is_valid

    result = validator.validate(session, _action(session, seer, ActionType.CHECK, target=seer))
    assert result.is_valid is False
    assert result.violation_type == "target_self"


def test_witch_save_and_poison_resource_rules():
    session = _session()
    session.truth_state.phase = Phase.NIGHT_WITCH
    validator = RuleValidator()
    witch = _pid(session, Role.WITCH)
    victim = next(pid for pid in session.truth_state.players if pid != witch)
    session.truth_state.night_state.kill_target = victim

    assert validator.validate(session, _action(session, witch, ActionType.SAVE, target=victim)).is_valid

    session.truth_state.witch_state.antidote_used = True
    result = validator.validate(session, _action(session, witch, ActionType.SAVE, target=victim))
    assert result.is_valid is False
    assert result.violation_type == "antidote_already_used"

    session.truth_state.witch_state.antidote_used = False
    session.truth_state.witch_state.poison_used = True
    poison_target = next(pid for pid in session.truth_state.players if pid not in {witch, victim})
    result = validator.validate(session, _action(session, witch, ActionType.POISON, target=poison_target))
    assert result.is_valid is False
    assert result.violation_type == "poison_already_used"


def test_witch_can_self_save_first_night_only():
    """女巫第一夜被刀可自救；第二夜起刀口是自己只能 skip（target_self）。"""
    session = _session()
    session.truth_state.phase = Phase.NIGHT_WITCH
    validator = RuleValidator()
    witch = _pid(session, Role.WITCH)
    session.truth_state.night_state.kill_target = witch

    # 第一夜（round 默认 1）：自救合法
    assert session.truth_state.round == 1
    result = validator.validate(session, _action(session, witch, ActionType.SAVE, target=witch))
    assert result.is_valid is True

    # 第二夜：刀口仍是女巫自己，但已不可自救
    session.truth_state.round = 2
    result = validator.validate(session, _action(session, witch, ActionType.SAVE, target=witch))
    assert result.is_valid is False
    assert result.violation_type == "target_self"


def test_witch_cannot_save_after_poison_same_night():
    session = _session()
    session.truth_state.phase = Phase.NIGHT_WITCH
    validator = RuleValidator()
    witch = _pid(session, Role.WITCH)
    victim = next(pid for pid in session.truth_state.players if pid != witch)
    poison_target = next(pid for pid in session.truth_state.players if pid not in {witch, victim})
    session.truth_state.night_state.kill_target = victim
    session.truth_state.night_state.poison_target = poison_target

    result = validator.validate(session, _action(session, witch, ActionType.SAVE, target=victim))

    assert result.is_valid is False
    assert result.violation_type == "save_and_poison_same_night_forbidden"


def test_vote_rules_and_tie_revote_scope():
    session = _session()
    session.truth_state.phase = Phase.DAY_VOTE
    validator = RuleValidator()
    voter = next(iter(session.truth_state.players))
    target = next(pid for pid in session.truth_state.players if pid != voter)

    assert validator.validate(session, _action(session, voter, ActionType.VOTE, target=target)).is_valid

    result = validator.validate(session, _action(session, voter, ActionType.VOTE, target=voter))
    assert result.is_valid is False
    assert result.violation_type == "target_self"

    session.truth_state.phase = Phase.DAY_TIE_REVOTE
    session.truth_state.round_state.tie_candidates = [target]
    other = next(pid for pid in session.truth_state.players if pid not in {voter, target})
    result = validator.validate(session, _action(session, voter, ActionType.VOTE, target=other))
    assert result.is_valid is False
    assert result.violation_type == "target_not_in_tie_candidates"


def test_last_words_only_last_exiled_player_can_speak():
    session = _session()
    session.truth_state.phase = Phase.EXILE_LAST_WORDS
    validator = RuleValidator()
    exiled = next(iter(session.truth_state.players))
    other = next(pid for pid in session.truth_state.players if pid != exiled)
    session.truth_state.round_state.last_exiled_player = exiled
    session.truth_state.players[exiled].status = PlayerStatus.DEAD

    assert validator.validate(session, _action(session, exiled, ActionType.SPEAK)).is_valid

    result = validator.validate(session, _action(session, other, ActionType.SPEAK))
    assert result.is_valid is False
    assert result.violation_type == "actor_not_last_exiled"


def test_hunter_shoot_red_lines():
    session = _session("game_config_9p_mvp.json")
    session.truth_state.phase = Phase.HUNTER_SHOOT
    validator = RuleValidator()
    hunter = _pid(session, Role.HUNTER)
    target = next(pid for pid in session.truth_state.players if pid != hunter)

    session.truth_state.players[hunter].status = PlayerStatus.DEAD
    assert validator.validate(
        session, _action(session, hunter, ActionType.HUNTER_SHOOT, target=target)
    ).is_valid

    assert validator.validate(
        session, _action(session, hunter, ActionType.HUNTER_SHOOT, target=None)
    ).is_valid

    session.truth_state.round_state.hunter_death_cause = DeathCause.WITCH_POISON.value
    result = validator.validate(
        session, _action(session, hunter, ActionType.HUNTER_SHOOT, target=target)
    )
    assert result.is_valid is False
    assert result.violation_type == "hunter_poisoned_cannot_shoot"
