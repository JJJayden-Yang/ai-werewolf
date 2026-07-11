"""Yuan：Supervisor shadow belief hook scaffold.

A 侧只负责把已落盘事件的 ``event_id`` 逐条交给可选 BeliefUpdater。
B 的 RealtimeBeliefUpdater 后续用同一个 EventStore 读取事件并写 shadow belief；
本测试不实现 belief 数学，也不把 belief 回流给 v0 AgentContext。
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from agent_policy import RoleStrategyMockAgent
from contracts import GameConfig, Phase
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class _RecordingBeliefUpdater:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def update(self, game_id: str, event_id: str) -> None:
        self.calls.append((game_id, event_id))


class _FailingBeliefUpdater:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def update(self, game_id: str, event_id: str) -> None:
        self.calls.append((game_id, event_id))
        raise RuntimeError("belief update boom")


class _RecordingAgent:
    def __init__(self) -> None:
        self._inner = RoleStrategyMockAgent()
        self.contexts: list[dict] = []

    async def act(self, context: dict) -> dict:
        self.contexts.append(context)
        return await self._inner.act(context)


def _make_6p_engine(seed: int, game_id: str) -> tuple[GameEngine, GameConfig]:
    config_data = json.loads(
        (FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8")
    )
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)
    return engine, config


def _run_game(*, seed: int, game_id: str, belief_updater=None):
    engine, config = _make_6p_engine(seed, game_id)
    store = InMemoryEventStore()
    assembler = ContextAssembler(
        session_provider=engine,
        event_store=store,
        belief_store=None,  # v0: belief 只允许后台 shadow 更新，不注入 AgentContext
    )
    agent = _RecordingAgent()
    supervisor = Supervisor(engine, assembler, agent, store, belief_updater=belief_updater)

    asyncio.run(supervisor.run_game(game_id))
    return config, engine, store, agent, supervisor


def test_yuan_belief_updater_called_once_per_appended_event_id():
    updater = _RecordingBeliefUpdater()
    config, _engine, store, _agent, _supervisor = _run_game(
        seed=0,
        game_id="yuan_shadow_belief_hook",
        belief_updater=updater,
    )

    event_ids = [event.event_id for event in store.list_by_game(config.game_id)]
    assert event_ids
    assert updater.calls == [(config.game_id, event_id) for event_id in event_ids]


def test_yuan_default_none_belief_updater_keeps_noop_behavior():
    config, engine, store, _agent, _supervisor = _run_game(
        seed=1,
        game_id="yuan_shadow_belief_none",
        belief_updater=None,
    )

    assert engine.get_session(config.game_id).current_phase == Phase.GAME_OVER
    assert store.list_by_game(config.game_id)


def test_yuan_v0_agent_context_does_not_receive_belief_from_hook():
    updater = _RecordingBeliefUpdater()
    config, _engine, _store, agent, _supervisor = _run_game(
        seed=2,
        game_id="yuan_shadow_belief_context",
        belief_updater=updater,
    )

    assert updater.calls
    assert agent.contexts
    for context in agent.contexts:
        assert context["game_id"] == config.game_id
        assert context["belief_state"] == {}
        assert context["belief_top_suspects"] == []


def test_yuan_belief_updater_error_is_non_blocking_shadow_failure():
    updater = _FailingBeliefUpdater()
    config, engine, store, _agent, supervisor = _run_game(
        seed=3,
        game_id="yuan_shadow_belief_error",
        belief_updater=updater,
    )

    events = store.list_by_game(config.game_id)
    assert engine.get_session(config.game_id).current_phase == Phase.GAME_OVER
    assert updater.calls == [(config.game_id, event.event_id) for event in events]
    assert supervisor._belief_update_errors == [
        {
            "game_id": config.game_id,
            "event_id": event.event_id,
            "error_type": "RuntimeError",
            "error_message": "belief update boom",
        }
        for event in events
    ]
