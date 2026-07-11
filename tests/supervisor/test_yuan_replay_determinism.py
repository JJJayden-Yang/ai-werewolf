"""次要项（Yuan）：replay 字节级可复现。

要做到同一局可字节复现，需要两件事都确定：
- 发牌随机性：`GameSessionManager(seed=...)` 定种，并把 seed 记录在 `GameSession.seed`。
  seed **只供赛后 replay/export 直接读取**复现发牌，**绝不进事件或 AgentContext**
  （否则 agent 可结合公开 config + 发牌算法反推 pid→role，破坏信息隔离）；
- created_at 时间戳：默认 wall-clock 每次都不同 → 注入逻辑时钟使其确定。

本测试用真实 B `LegalRandomMockAgent`（确定性）+ 真实 InMemoryEventStore，跑同 seed +
逻辑时钟两遍，断言事件流逐条 model_dump_json 完全相同。
"""

import asyncio
import itertools
import json
from pathlib import Path

from agent_policy import LegalRandomMockAgent
from contracts import (
    AgentContext,
    EventType,
    GameConfig,
    Phase,
    PrivateEvent,
    Role,
    Visibility,
    VisiblePlayer,
)
from game_core import GameEngine, GameSessionManager, RuleValidator
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class _TruthAssembler:
    """最小真相态装配器（够喂真实 LegalRandomMockAgent 出合法动作）。"""

    def __init__(self, engine: GameEngine) -> None:
        self._engine = engine

    def build_context(self, game_id: str, agent_id: str, phase: Phase) -> AgentContext:
        session = self._engine.get_session(game_id)
        ts = session.truth_state
        me = ts.players[agent_id]
        private: list[PrivateEvent] = []
        if me.role == Role.WEREWOLF:
            private.append(
                PrivateEvent(
                    event_type=EventType.ROLE_ASSIGNED,
                    teammates=[pid for pid, p in ts.players.items() if p.role == Role.WEREWOLF],
                    visibility=Visibility.PRIVATE_TO_WOLVES,
                )
            )
        return AgentContext(
            game_id=game_id,
            agent_id=agent_id,
            role=me.role,
            round=session.round,
            phase=phase,
            visible_players=[
                VisiblePlayer(player_id=pid, status=p.status, public_claim=p.public_claim)
                for pid, p in ts.players.items()
            ],
            private_events=private,
            tie_candidates=ts.round_state.tie_candidates,
            allowed_actions=list(RuleValidator.allowed_actions(phase)),
        )


def _run(seed: int, game_id: str):
    config_data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    ticks = itertools.count()
    engine = GameEngine(clock=lambda: f"t{next(ticks):06d}")  # 注入逻辑时钟
    engine.sessions = GameSessionManager(seed=seed)  # 定种发牌
    engine.sessions.create_game(config)
    store = InMemoryEventStore()
    supervisor = Supervisor(engine, _TruthAssembler(engine), LegalRandomMockAgent(), store)
    asyncio.run(supervisor.run_game(game_id))
    return store.list_by_game(game_id)


def test_same_seed_and_logical_clock_produce_byte_identical_event_stream():
    first = _run(777, "yuan_determinism")
    second = _run(777, "yuan_determinism")  # 同 game_id + 同 seed + 同逻辑时钟
    assert first  # 非空
    assert [e.model_dump_json() for e in first] == [e.model_dump_json() for e in second]


def test_role_assigned_event_does_not_leak_seed():
    """红线：seed 绝不能进默认 public 的 role_assigned，否则 agent 可反推 pid→role。"""
    events = _run(4242, "yuan_seed_no_leak")
    role_ev = next(e for e in events if e.event_type == EventType.ROLE_ASSIGNED)
    assert "seed" not in role_ev.payload
    assert set(role_ev.payload.keys()) == {"player_count", "role_counts"}


def test_session_manager_seed_recorded_on_session_for_postgame_only():
    """seed 留在 GameSession.seed，供赛后 replay/export 复现发牌（不经事件、不经 AgentContext）。"""
    config = GameConfig.model_validate(
        json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    )
    manager = GameSessionManager(seed=99)
    session = manager.create_game(config)
    assert manager.seed == 99
    assert session.seed == 99
