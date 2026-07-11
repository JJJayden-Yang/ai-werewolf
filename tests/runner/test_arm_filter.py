from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from contracts import Camp, GameConfig, Role
from game_core import GameEngine, GameSessionManager
from runner.arm_filter import make_arm_filter, make_arm_filter_factory

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _load_6p_config(game_id: str) -> GameConfig:
    data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    data["game_id"] = game_id
    return GameConfig.model_validate(data)


def _engine_with_6p_game(game_id: str, *, seed: int = 0) -> GameEngine:
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(_load_6p_config(game_id))
    return engine


def _players(engine: GameEngine, game_id: str):
    return engine.sessions.get_game(game_id).truth_state.players


def _matching_player_ids(engine: GameEngine, game_id: str, scope: str) -> set[str]:
    players = _players(engine, game_id)
    if scope == "wolves":
        return {pid for pid, player in players.items() if player.camp == Camp.WEREWOLF}
    if scope == "villagers":
        return {pid for pid, player in players.items() if player.camp == Camp.VILLAGER}
    if scope == "gods":
        return {
            pid
            for pid, player in players.items()
            if player.role in {Role.SEER, Role.WITCH, Role.HUNTER}
        }
    if scope == "civilians":
        return {pid for pid, player in players.items() if player.role == Role.VILLAGER}
    if scope == "all":
        return set(players)
    if scope == "none":
        return set()
    raise AssertionError(f"unknown test scope: {scope}")


def _assert_filter_matches_scope(scope: str, *, game_id: str, seed: int = 0) -> None:
    engine = _engine_with_6p_game(game_id, seed=seed)
    filt = make_arm_filter(scope, engine, game_id)  # type: ignore[arg-type]
    expected = _matching_player_ids(engine, game_id, scope)
    assert expected or scope == "none"
    for player_id in _players(engine, game_id):
        assert filt(player_id) is (player_id in expected)


def test_make_arm_filter_wolves():
    _assert_filter_matches_scope("wolves", game_id="g_arm_wolves", seed=1)


def test_make_arm_filter_villagers():
    _assert_filter_matches_scope("villagers", game_id="g_arm_villagers", seed=2)


def test_make_arm_filter_gods():
    _assert_filter_matches_scope("gods", game_id="g_arm_gods", seed=3)


def test_make_arm_filter_civilians():
    _assert_filter_matches_scope("civilians", game_id="g_arm_civilians", seed=4)


def test_make_arm_filter_all():
    _assert_filter_matches_scope("all", game_id="g_arm_all", seed=5)


def test_make_arm_filter_none():
    _assert_filter_matches_scope("none", game_id="g_arm_none", seed=6)


def test_make_arm_filter_unknown_scope():
    engine = _engine_with_6p_game("g_arm_invalid", seed=7)
    with pytest.raises(ValueError, match="scope"):
        make_arm_filter("invalid", engine, "g_arm_invalid")  # type: ignore[arg-type]


def test_make_arm_filter_is_snapshot():
    game_id = "g_arm_snapshot"
    engine = _engine_with_6p_game(game_id, seed=8)
    players = _players(engine, game_id)
    filt = make_arm_filter("wolves", engine, game_id)
    expected_before = {player_id: filt(player_id) for player_id in players}

    first_player = next(iter(players.values()))
    first_player.role = Role.WEREWOLF
    first_player.camp = Camp.WEREWOLF
    removed_player_id, _removed_player = players.popitem()

    for player_id, expected in expected_before.items():
        assert filt(player_id) is expected
    assert filt(removed_player_id) is expected_before[removed_player_id]


def test_gods_plus_civilians_equals_villagers():
    game_id = "g_arm_partition"
    engine = _engine_with_6p_game(game_id, seed=9)
    villagers = make_arm_filter("villagers", engine, game_id)
    gods = make_arm_filter("gods", engine, game_id)
    civilians = make_arm_filter("civilians", engine, game_id)
    for player_id in _players(engine, game_id):
        assert villagers(player_id) is (gods(player_id) or civilians(player_id))


def test_make_arm_filter_factory_delegates_to_make_arm_filter():
    game_id = "g_arm_factory"
    engine = _engine_with_6p_game(game_id, seed=10)
    factory_filter = make_arm_filter_factory("villagers")(engine, game_id)
    direct_filter = make_arm_filter("villagers", engine, game_id)
    for player_id in _players(engine, game_id):
        assert factory_filter(player_id) is direct_filter(player_id)
