"""A 对外暴露给 C 的只读接口：get_session / SessionProvider / allowed_actions。"""

import json
from pathlib import Path

from contracts import ActionType, GameConfig, Phase
from game_core import GameEngine, GameSession, RuleValidator, SessionProvider

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _engine_with_game() -> tuple[GameEngine, str]:
    config = GameConfig.model_validate(
        json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    )
    engine = GameEngine()
    engine.sessions.create_game(config)
    return engine, config.game_id


def test_get_session_returns_session():
    engine, game_id = _engine_with_game()
    session = engine.get_session(game_id)
    assert isinstance(session, GameSession)
    assert session.game_id == game_id
    # 与内部 sessions.get_game 是同一个对象（只读门面，不复制）
    assert session is engine.sessions.get_game(game_id)


def test_game_engine_satisfies_session_provider_protocol():
    engine, _ = _engine_with_game()
    assert isinstance(engine, SessionProvider)


def test_allowed_actions_is_canonical_per_phase():
    assert RuleValidator.allowed_actions(Phase.NIGHT_WEREWOLF) == {ActionType.NIGHT_KILL_NOMINATE}
    assert RuleValidator.allowed_actions(Phase.NIGHT_SEER) == {ActionType.CHECK}
    assert RuleValidator.allowed_actions(Phase.NIGHT_WITCH) == {
        ActionType.SAVE,
        ActionType.POISON,
        ActionType.SKIP,
    }
    assert RuleValidator.allowed_actions(Phase.DAY_VOTE) == {ActionType.VOTE}
    assert RuleValidator.allowed_actions(Phase.HUNTER_SHOOT) == {ActionType.HUNTER_SHOOT}
    # 无行动者 / 系统结算阶段返回空集（不报错）
    assert RuleValidator.allowed_actions(Phase.DAY_ANNOUNCEMENT) == set()
    assert RuleValidator.allowed_actions(Phase.WIN_CHECK) == set()
