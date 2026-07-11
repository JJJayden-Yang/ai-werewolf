"""A6：WinChecker 胜负判定。"""

import json
import random
from pathlib import Path

from contracts import GameConfig, PlayerStatus, Role
from game_core import GameSessionManager, WinChecker

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _session():
    cfg = GameConfig.model_validate(
        json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    )
    return GameSessionManager(rng=random.Random(0)).create_game(cfg)


def test_villagers_win_when_all_wolves_dead():
    session = _session()
    for player in session.truth_state.players.values():
        if player.role == Role.WEREWOLF:
            player.status = PlayerStatus.DEAD

    result = WinChecker().check(session)

    assert result.game_over is True
    assert result.winner == "villagers"
    assert result.reason == "all_werewolves_dead"


def test_wolves_win_at_parity():
    session = _session()
    good_to_kill = [
        player for player in session.truth_state.players.values() if player.role != Role.WEREWOLF
    ][:2]
    for player in good_to_kill:
        player.status = PlayerStatus.DEAD

    result = WinChecker().check(session)

    assert result.game_over is True
    assert result.winner == "werewolves"
    assert result.reason == "werewolves_reached_parity"


def test_game_continues_without_win_condition():
    result = WinChecker().check(_session())

    assert result.game_over is False
    assert result.winner is None
