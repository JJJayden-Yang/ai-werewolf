"""Yuan：Supervisor event_observer hook scaffold.

Phase 5 三方向并行地基条款 §2.3：Supervisor 暴露一个只读旁观者回调，给 C 的
实时上帝视角（SSE）接事件流用。本测试只保证四件事：
1. 默认 None 行为不变（回归）；
2. observer 收到所有 append 的事件，**按 append 顺序**；
3. observer 抛异常被吞掉，**绝不影响游戏走向**；
4. observer 误改 event.payload / event.target 时，sink 落盘事件**不受污染**
   ——"observer 只读" 由 model_copy(deep=True) 从代码层强制，不靠约定。

不耦合 SSE / 异步队列实现 —— 那是 C 在自己 owner 目录的事。
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


class _RecordingObserver:
    """收集事件顺序，验证 append_events 把全部事件按序回调。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def __call__(self, event) -> None:  # GameEvent 不在运行期导入
        self.events.append((event.event_id, event.event_type.value))


class _FailingObserver:
    """每次都抛异常 —— 验证旁观者炸了不影响游戏。"""

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, _event) -> None:
        self.call_count += 1
        raise RuntimeError("observer boom")


class _MutatingObserver:
    """恶意 observer：每次回调都改 event.payload + event.target，验证深拷贝隔离。"""

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, event) -> None:
        self.call_count += 1
        # 尝试污染 payload（dict）+ target（标量）—— InMemoryEventStore 保的是引用，
        # 没深拷贝就会直接污染 sink 落盘的对象。
        try:
            event.payload["__poisoned__"] = True  # type: ignore[index]
        except Exception:  # noqa: BLE001
            pass
        try:
            event.target = "POISONED"
        except Exception:  # noqa: BLE001
            pass


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


def _run_game(*, seed: int, game_id: str, event_observer=None):
    engine, config = _make_6p_engine(seed, game_id)
    store = InMemoryEventStore()
    assembler = ContextAssembler(
        session_provider=engine,
        event_store=store,
        belief_store=None,
    )
    agent = RoleStrategyMockAgent()
    supervisor = Supervisor(
        engine,
        assembler,
        agent,
        store,
        event_observer=event_observer,
    )

    asyncio.run(supervisor.run_game(game_id))
    return config, engine, store, supervisor


def test_yuan_event_observer_default_none_keeps_noop_behavior():
    """默认 event_observer=None：游戏正常跑完，store 落事件，行为零回归。"""
    config, engine, store, _supervisor = _run_game(
        seed=0,
        game_id="yuan_event_observer_default",
        event_observer=None,
    )

    assert engine.get_session(config.game_id).current_phase == Phase.GAME_OVER
    assert store.list_by_game(config.game_id)


def test_yuan_event_observer_receives_all_appended_events_in_order():
    """observer 收到全部 append 的事件，且顺序与 sink 一致（SSE 流的关键不变量）。"""
    observer = _RecordingObserver()
    config, _engine, store, _supervisor = _run_game(
        seed=1,
        game_id="yuan_event_observer_record",
        event_observer=observer,
    )

    sink_events = store.list_by_game(config.game_id)
    assert sink_events, "sink 必须落到事件"
    expected = [(ev.event_id, ev.event_type.value) for ev in sink_events]
    assert observer.events == expected


def test_yuan_event_observer_exception_is_swallowed_not_blocking():
    """observer 每次都抛异常，游戏照样跑完、sink 不丢事件 —— 旁观者永远不能影响游戏。"""
    observer = _FailingObserver()
    config, engine, store, _supervisor = _run_game(
        seed=2,
        game_id="yuan_event_observer_failing",
        event_observer=observer,
    )

    assert engine.get_session(config.game_id).current_phase == Phase.GAME_OVER
    sink_events = store.list_by_game(config.game_id)
    assert sink_events
    # observer 被调用次数 == sink 事件数（异常被吞但调用没跳过）
    assert observer.call_count == len(sink_events)


def test_yuan_event_observer_mutation_does_not_poison_sink():
    """observer 改 event.payload / event.target，sink 落盘事件 **不受影响** ——
    "observer 只读" 由 model_copy(deep=True) 从代码层强制，不靠约定。"""
    observer = _MutatingObserver()
    config, _engine, store, _supervisor = _run_game(
        seed=3,
        game_id="yuan_event_observer_mutating",
        event_observer=observer,
    )

    sink_events = store.list_by_game(config.game_id)
    assert sink_events
    assert observer.call_count == len(sink_events)
    # 关键不变量：sink 里没有任何事件被恶意 observer 染色
    for ev in sink_events:
        assert ev.target != "POISONED", f"target leak on {ev.event_id}"
        assert "__poisoned__" not in ev.payload, f"payload leak on {ev.event_id}"
