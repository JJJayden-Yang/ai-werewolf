"""A0：contracts fixtures 能被对应 schema 加载。"""

import json
from pathlib import Path

import pytest

from contracts import AgentAction, GameConfig, GameEvent, TruthState

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["game_config_6p_debug.json", "game_config_9p_mvp.json"])
def test_game_config_fixtures_load(name):
    cfg = GameConfig.model_validate(_load(name))
    assert cfg.player_count == sum(
        v for v in cfg.roles.model_dump().values() if isinstance(v, int)
    )


@pytest.mark.parametrize("name", ["truth_state_6p_initial.json", "truth_state_9p_initial.json"])
def test_truth_state_fixtures_load(name):
    ts = TruthState.model_validate(_load(name))
    assert len(ts.players) == (6 if "6p" in name else 9)
    assert all(p.status.value == "alive" for p in ts.players.values())


@pytest.mark.parametrize(
    "name",
    [
        "action_wolf_nominate.json",
        "action_seer_check.json",
        "action_day_vote.json",
        "action_hunter_shoot.json",
    ],
)
def test_action_fixtures_load(name):
    action = AgentAction.model_validate(_load(name))
    assert action.action_type is not None


def test_event_fixture_loads():
    event = GameEvent.model_validate(_load("event_phase_started.json"))
    assert event.event_type.value == "phase_started"


def test_9p_config_model_config_alias():
    """关键兼容点：JSON 键 `model_config` → python 字段 `model_settings`（别名）。"""
    cfg = GameConfig.model_validate(_load("game_config_9p_mvp.json"))
    assert cfg.agent_version == "v1"
    assert cfg.model_settings is not None
    assert cfg.model_settings.model_id == "${ARK_MODEL_ID}"
    assert cfg.model_settings.provider == "volcengine_ark"
