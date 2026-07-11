"""A1：GameSessionManager.create_game 发牌 + 初始 TruthState + 多局隔离。"""

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from contracts import Camp, GameConfig, Phase, PlayerStatus, Role, RoleCounts
from game_core import GameSessionManager

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _config(name: str) -> GameConfig:
    return GameConfig.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _manager() -> GameSessionManager:
    return GameSessionManager(rng=random.Random(0))


def test_create_6p_role_counts_and_camps():
    session = _manager().create_game(_config("game_config_6p_debug.json"))
    players = session.truth_state.players
    assert len(players) == 6
    role_counts = Counter(p.role for p in players.values())
    assert role_counts == {Role.WEREWOLF: 2, Role.SEER: 1, Role.WITCH: 1, Role.VILLAGER: 2}
    # 阵营：狼=werewolf camp，其余=villager camp
    for p in players.values():
        expected = Camp.WEREWOLF if p.role == Role.WEREWOLF else Camp.VILLAGER
        assert p.camp == expected
        assert p.status == PlayerStatus.ALIVE


def test_create_9p_role_counts():
    session = _manager().create_game(_config("game_config_9p_mvp.json"))
    players = session.truth_state.players
    assert len(players) == 9
    assert Counter(p.role for p in players.values()) == {
        Role.WEREWOLF: 3,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.VILLAGER: 3,
    }
    assert sum(1 for p in players.values() if p.camp == Camp.WEREWOLF) == 3


def test_initial_state_defaults():
    session = _manager().create_game(_config("game_config_9p_mvp.json"))
    ts = session.truth_state
    assert session.current_phase == Phase.NIGHT_WEREWOLF
    assert ts.phase == Phase.NIGHT_WEREWOLF
    assert session.round == 1 and ts.round == 1
    assert ts.witch_state.antidote_used is False and ts.witch_state.poison_used is False
    assert ts.hunter_state.shot_used is False
    assert ts.night_state.kill_target is None
    assert ts.round_state.tie_candidates == []


def test_roles_sum_mismatch_raises():
    bad = GameConfig(
        game_id="bad_001",
        player_count=10,  # 与 roles 之和 9 不一致
        roles=RoleCounts(werewolf=3, seer=1, witch=1, hunter=1, villager=3),
    )
    with pytest.raises(ValueError):
        _manager().create_game(bad)


def test_per_game_isolation():
    mgr = _manager()
    g6 = mgr.create_game(_config("game_config_6p_debug.json"))
    g9 = mgr.create_game(_config("game_config_9p_mvp.json"))

    assert mgr.get_game("debug_6p_001") is g6
    assert mgr.get_game("mvp_9p_001") is g9
    assert len(mgr.get_game("debug_6p_001").truth_state.players) == 6
    assert len(mgr.get_game("mvp_9p_001").truth_state.players) == 9
    assert {s.game_id for s in mgr.list_games()} == {"debug_6p_001", "mvp_9p_001"}

    # 改一局不影响另一局
    g6.truth_state.players["P1"].status = PlayerStatus.DEAD
    assert all(p.status == PlayerStatus.ALIVE for p in g9.truth_state.players.values())


def test_get_game_not_found_raises():
    with pytest.raises(KeyError):
        _manager().get_game("does_not_exist")


def test_deterministic_with_same_seed():
    cfg = _config("game_config_9p_mvp.json")
    a = GameSessionManager(rng=random.Random(42)).create_game(cfg)
    b = GameSessionManager(rng=random.Random(42)).create_game(cfg)
    assert {pid: p.role for pid, p in a.truth_state.players.items()} == {
        pid: p.role for pid, p in b.truth_state.players.items()
    }


def test_create_game_duplicate_game_id_raises():
    mgr = _manager()
    cfg = _config("game_config_6p_debug.json")
    mgr.create_game(cfg)
    with pytest.raises(ValueError):
        mgr.create_game(cfg)  # 同一 game_id 默认不覆盖


def test_create_game_can_fix_specific_seat_role():
    session = _manager().create_game(
        _config("game_config_9p_mvp.json"),
        fixed_roles={"P1": Role.SEER},
    )

    players = session.truth_state.players
    assert players["P1"].role == Role.SEER
    assert Counter(p.role for p in players.values()) == {
        Role.WEREWOLF: 3,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.VILLAGER: 3,
    }


def test_create_game_rejects_fixed_role_not_in_pool():
    with pytest.raises(ValueError, match="fixed role"):
        _manager().create_game(
            _config("game_config_6p_debug.json"),
            fixed_roles={"P1": Role.HUNTER},
        )


def test_negative_role_count_raises():
    bad = GameConfig(
        game_id="neg_001",
        player_count=5,
        roles=RoleCounts(werewolf=-1, seer=1, witch=1, hunter=1, villager=3),
    )
    with pytest.raises(ValueError):
        _manager().create_game(bad)


def test_current_phase_round_forward_to_truth_state():
    """P1-1：current_phase / round 只读转发，唯一真相是 truth_state。"""
    session = _manager().create_game(_config("game_config_6p_debug.json"))
    session.truth_state.phase = Phase.DAY_VOTE
    session.truth_state.round = 3
    assert session.current_phase == Phase.DAY_VOTE
    assert session.round == 3


def test_current_phase_and_round_are_read_only():
    """P1-1 回归防护：current_phase / round 只读，直接赋值应报 AttributeError。"""
    session = _manager().create_game(_config("game_config_6p_debug.json"))
    with pytest.raises(AttributeError):
        session.current_phase = Phase.DAY_VOTE
    with pytest.raises(AttributeError):
        session.round = 5
