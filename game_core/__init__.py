"""game_core —— 人 A 负责的游戏引擎层。

Engine 管真相和规则；对外只通过 contracts/ 中的 schema 与接口交互。
"""

from game_core.action_resolver import ActionResolver
from game_core.engine import GameEngine
from game_core.event_emitter import EventEmitter
from game_core.hunter_shoot_resolver import HunterShootResolver
from game_core.phase_controller import PhaseController
from game_core.protocols import SessionProvider
from game_core.rule_validator import RuleValidator
from game_core.session_manager import GameSessionManager
from game_core.truth_state_store import TruthStateStore
from game_core.types import GameSession, ValidationResult, WinCheckResult
from game_core.win_checker import WinChecker

__all__ = [
    "GameEngine",
    "GameSessionManager",
    "PhaseController",
    "RuleValidator",
    "ActionResolver",
    "HunterShootResolver",
    "WinChecker",
    "EventEmitter",
    "TruthStateStore",
    "GameSession",
    "SessionProvider",
    "ValidationResult",
    "WinCheckResult",
]
