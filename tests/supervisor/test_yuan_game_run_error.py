"""A5（Yuan）：失败可定位 —— run_game 异常携带 game_id/phase/actor，供 C 填 GameRunResult。

压测时一局崩了,C 的 runner catch GameRunError 即可定位"哪局/哪阶段/哪个 actor/什么异常",
填进 GameRunResult.{error_phase, error_actor, error_type, error_message}。
"""

import asyncio
import json
import random
from pathlib import Path

import pytest

from agent_policy import RoleStrategyMockAgent
from contracts import AgentContext, GameConfig, Phase, Role
from game_core import GameEngine, GameSessionManager, RuleValidator
from supervisor import GameRunError, Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _make_engine(seed: int, game_id: str) -> GameEngine:
    config_data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(GameConfig.model_validate(config_data))
    return engine


class _ListSink:
    def __init__(self) -> None:
        self.events: list = []

    def append_many(self, events) -> None:
        self.events.extend(events)


class _BoomAssembler:
    """build_context 永远抛 —— 模拟 C 的装配在 NIGHT_WEREWOLF 第一个 actor 处失败。"""

    def build_context(self, game_id, agent_id, phase):
        raise ValueError("boom in context assembly")


class _BoomAgent:
    """context 正常,但 agent.act 抛 —— 模拟 B/LLM agent 失败。"""

    def __init__(self, engine) -> None:
        self._engine = engine

    async def act(self, context: dict) -> dict:
        raise RuntimeError("boom in agent.act")


class _RealishAssembler:
    """最小真实接口装配器（给 _BoomAgent 用,让 build_context 成功、act 失败）。"""

    def __init__(self, engine: GameEngine) -> None:
        self._engine = engine

    def build_context(self, game_id, agent_id, phase) -> AgentContext:
        ts = self._engine.get_session(game_id).truth_state
        return AgentContext(
            game_id=game_id,
            agent_id=agent_id,
            role=ts.players[agent_id].role,
            round=self._engine.get_session(game_id).round,
            phase=phase,
            allowed_actions=list(RuleValidator.allowed_actions(phase)),
        )


def _wolves(engine: GameEngine, gid: str) -> set[str]:
    players = engine.get_session(gid).truth_state.players
    return {pid for pid, p in players.items() if p.role == Role.WEREWOLF}


def test_context_assembly_failure_localizes_phase_and_actor():
    gid = "yuan_err_ctx"
    engine = _make_engine(0, gid)
    sup = Supervisor(engine, _BoomAssembler(), RoleStrategyMockAgent(), _ListSink())

    with pytest.raises(GameRunError) as ei:
        asyncio.run(sup.run_game(gid))

    err = ei.value
    assert err.game_id == gid
    assert err.phase == Phase.NIGHT_WEREWOLF  # 第一个有 actor 的阶段
    assert err.actor in _wolves(engine, gid)  # 定位到具体狼
    assert isinstance(err.__cause__, ValueError)  # 原始异常可取 error_type


def test_agent_failure_localizes_phase_and_actor():
    gid = "yuan_err_agent"
    engine = _make_engine(0, gid)
    sup = Supervisor(engine, _RealishAssembler(engine), _BoomAgent(engine), _ListSink())

    with pytest.raises(GameRunError) as ei:
        asyncio.run(sup.run_game(gid))

    err = ei.value
    assert err.game_id == gid
    assert err.phase == Phase.NIGHT_WEREWOLF
    assert err.actor in _wolves(engine, gid)
    assert isinstance(err.__cause__, RuntimeError)
    assert "boom in agent.act" in str(err.__cause__)


def test_game_run_error_is_runtimeerror_subclass():
    """向后兼容：此前 phase_stuck 抛 RuntimeError，GameRunError 仍是其子类。"""
    assert issubclass(GameRunError, RuntimeError)
    err = GameRunError(game_id="g", phase=Phase.DAY_VOTE, actor="P1", reason="phase_stuck")
    assert err.game_id == "g" and err.phase == Phase.DAY_VOTE
    assert err.actor == "P1" and err.reason == "phase_stuck"


def test_system_phase_exception_localizes_with_actor_none():
    """系统结算阶段（非单 actor）异常：actor=None，仍带 game_id/phase/cause。"""
    gid = "yuan_err_sys"
    engine = _make_engine(0, gid)

    def _boom(game_id):  # emit_phase_started 在 run_game try 内、actors 判定之前
        raise ValueError("boom in emit_phase_started")

    engine.emit_phase_started = _boom
    sup = Supervisor(engine, _RealishAssembler(engine), RoleStrategyMockAgent(), _ListSink())

    with pytest.raises(GameRunError) as ei:
        asyncio.run(sup.run_game(gid))

    err = ei.value
    assert err.game_id == gid
    assert err.actor is None  # 系统阶段无单个 actor
    assert err.phase == Phase.NIGHT_WEREWOLF
    assert isinstance(err.__cause__, ValueError)


class _StuckEngine:
    """永远停在非终局、无 actor 的假引擎 —— 触发 run_game 的 phase_stuck 上限。"""

    def get_current_phase(self, game_id):
        return Phase.DAY_DISCUSSION

    def emit_role_assigned(self, game_id):
        return None

    def emit_wolf_teammates(self, game_id):
        return []

    def emit_phase_started(self, game_id):
        return None

    def get_required_actors(self, game_id, phase):
        return []

    def resolve_phase(self, game_id):
        return []

    def advance_phase(self, game_id, events):
        return Phase.DAY_DISCUSSION


def test_phase_stuck_raises_game_run_error_with_reason():
    """达到 _MAX_PHASE_STEPS 仍未终局：GameRunError(reason='phase_stuck', actor=None)。"""
    sup = Supervisor(_StuckEngine(), _BoomAssembler(), RoleStrategyMockAgent(), _ListSink())

    with pytest.raises(GameRunError) as ei:
        asyncio.run(sup.run_game("yuan_err_stuck"))

    err = ei.value
    assert err.reason == "phase_stuck"
    assert err.actor is None
    assert err.game_id == "yuan_err_stuck"
