"""``runner.build_game`` 测试 —— phase5 §10 共享装配函数。

覆盖目标：
1. arm="v0" 基础路径：不建 belief lane，游戏跑完
2. arm="v1" 路径：belief stores + belief_updater 都已装配，ContextAssembler 拿到 belief_store
3. shadow 路径（arm="v0" + use_belief=True）：belief lane 启用但 assembler **不**拿 belief_store
4. ``event_observer`` 转发：observer 收到 append 的事件（呼应 PR-FD-A 的 hook）
5. ``belief_inject_filter`` 非空时**转发**给 ContextAssembler kwarg（PR-FD-B 对接位）
6. ``belief_inject_filter`` 为 None 时**不转发** kwarg（PR-FD-B 未合并也能用）
7. ``seed`` 决定性：同 seed 两局产生**完全一致**的事件流

不依赖 ARK / 真实 LLM —— 用 ``RoleStrategyMockAgent`` 跑 6 人 mock。
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from agent_policy import RoleStrategyMockAgent
from contracts import GameConfig, Phase
from runner import BuiltGame, GameStores, build_game

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _load_6p_config(game_id: str) -> GameConfig:
    data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    data["game_id"] = game_id
    return GameConfig.model_validate(data)


def _agent() -> RoleStrategyMockAgent:
    return RoleStrategyMockAgent()


# ---------------------------------------------------------------------------
# 1) arm="v0" 基础路径
# ---------------------------------------------------------------------------


def test_build_game_v0_default_no_belief_lane():
    built = build_game(_load_6p_config("g_v0_basic"), _agent(), arm="v0", seed=0)
    assert isinstance(built, BuiltGame)
    assert built.arm == "v0"
    assert built.belief_enabled is False
    assert built.belief_is_shadow is False
    assert built.stores.belief_store is None
    assert built.stores.belief_observability_store is None
    # 跑完一局确认装配正确
    asyncio.run(built.supervisor.run_game("g_v0_basic"))
    assert built.engine.get_session("g_v0_basic").current_phase == Phase.GAME_OVER
    assert built.stores.event_store.list_by_game("g_v0_basic")


# ---------------------------------------------------------------------------
# 2) arm="v1" —— belief lane 全装配
# ---------------------------------------------------------------------------


def test_build_game_v1_creates_belief_lane_and_updater():
    built = build_game(_load_6p_config("g_v1"), _agent(), arm="v1", seed=1)
    assert built.arm == "v1"
    assert built.belief_enabled is True
    assert built.belief_is_shadow is False
    assert built.stores.belief_store is not None
    assert built.stores.belief_observability_store is not None
    # Supervisor 持 belief_updater
    assert built.supervisor._belief_updater is not None
    # 跑完一局确认 belief 真在更新（observability store 拿到 batch）
    asyncio.run(built.supervisor.run_game("g_v1"))
    assert built.engine.get_session("g_v1").current_phase == Phase.GAME_OVER
    assert built.stores.belief_observability_store.list_updates("g_v1")


def test_build_game_forwards_factorized_belief_kernel():
    built = build_game(
        _load_6p_config("g_factorized_kernel"),
        _agent(),
        arm="v1",
        seed=11,
        belief_kernel="factorized_v2",
    )

    assert built.supervisor._belief_updater is not None
    assert built.supervisor._belief_updater._belief_kernel == "factorized_v2"


def test_build_game_wires_slow_think_policy_and_shared_belief_store():
    built = build_game(
        _load_6p_config("g_slow_think"),
        _agent(),
        arm="v1",
        seed=12,
        slow_think_policy="stub-policy",
    )

    # policy 透传给 Supervisor；Supervisor 持有的 belief_store 与 build_game 建的是同一个
    # （reflect 纯变换，由 Supervisor 用它读当前 belief + 落盘 enriched）。
    assert built.supervisor._slow_think_policy == "stub-policy"
    assert built.supervisor._belief_store is built.stores.belief_store


def test_build_game_slow_think_no_belief_store_in_v0():
    # v0 无 belief lane → Supervisor 的 belief_store 为 None，慢思无法落盘（零回归）。
    built = build_game(
        _load_6p_config("g_slow_think_v0"),
        _agent(),
        arm="v0",
        seed=13,
        slow_think_policy="stub",
    )

    assert built.supervisor._slow_think_policy == "stub"
    assert built.supervisor._belief_store is None


# ---------------------------------------------------------------------------
# 3) shadow 模式：arm="v0" + use_belief=True
# ---------------------------------------------------------------------------


def test_build_game_shadow_belief_keeps_assembler_unaware():
    """shadow 模式：belief stores 有，updater 写 belief，但 ContextAssembler **不**注 belief。"""
    built = build_game(
        _load_6p_config("g_shadow"), _agent(), arm="v0", use_belief=True, seed=2
    )
    assert built.arm == "v0"
    assert built.belief_enabled is True
    assert built.belief_is_shadow is True
    assert built.stores.belief_store is not None
    assert built.supervisor._belief_updater is not None
    # 关键：arm == "v0" 时 ContextAssembler 拿 None，不注 belief 给 agent
    assembler = built.supervisor._context
    assert assembler._belief_store is None


# ---------------------------------------------------------------------------
# 4) event_observer 转发
# ---------------------------------------------------------------------------


def test_build_game_forwards_event_observer_to_supervisor():
    received: list[str] = []

    def observer(event) -> None:
        received.append(event.event_id)

    built = build_game(
        _load_6p_config("g_observer"), _agent(), arm="v0", seed=3, event_observer=observer
    )
    asyncio.run(built.supervisor.run_game("g_observer"))
    sink_event_ids = [e.event_id for e in built.stores.event_store.list_by_game("g_observer")]
    assert received == sink_event_ids


# ---------------------------------------------------------------------------
# 5/6) belief_inject_filter 转发逻辑 —— 用 fake ContextAssembler 捕获 kwargs
# ---------------------------------------------------------------------------


class _FakeAssembler:
    """捕获 __init__ kwargs；其余照常工作（用真 build_context 转发到 truth state 不切实际，
    所以这里直接抛 NotImplementedError —— 测试只关心 build_game 怎么构造 assembler，
    不跑游戏）。"""

    captured_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        _FakeAssembler.captured_kwargs = dict(kwargs)

    def build_context(self, *_args, **_kwargs):  # pragma: no cover - 测试不调
        raise NotImplementedError


def test_build_game_passes_belief_inject_filter_when_provided(monkeypatch):
    """phase5 §2.1 对接位 —— filter 非 None 时本函数把它作为 ContextAssembler kwarg 转发。
    PR-FD-B 合并后这一支真生效；现在用 fake 验证 build_game 这边的转发逻辑。"""
    from runner import builder as builder_mod

    _FakeAssembler.captured_kwargs = {}
    monkeypatch.setattr(builder_mod, "ContextAssembler", _FakeAssembler)

    def my_filter(agent_id: str) -> bool:
        return agent_id.startswith("P")

    build_game(
        _load_6p_config("g_filter_set"),
        _agent(),
        arm="v1",
        seed=4,
        belief_inject_filter=my_filter,
    )
    assert "belief_inject_filter" in _FakeAssembler.captured_kwargs
    assert _FakeAssembler.captured_kwargs["belief_inject_filter"] is my_filter


def test_build_game_omits_belief_inject_filter_kwarg_when_none(monkeypatch):
    """filter=None 时**不**传 kwarg —— PR-FD-B 未合并时 ContextAssembler 不会因为多收
    一个 kwarg 而 TypeError。"""
    from runner import builder as builder_mod

    _FakeAssembler.captured_kwargs = {}
    monkeypatch.setattr(builder_mod, "ContextAssembler", _FakeAssembler)

    build_game(
        _load_6p_config("g_filter_none"),
        _agent(),
        arm="v1",
        seed=5,
        belief_inject_filter=None,
    )
    assert "belief_inject_filter" not in _FakeAssembler.captured_kwargs


# ---------------------------------------------------------------------------
# 7) seed 决定性 —— 同 seed 同事件流
# ---------------------------------------------------------------------------


def test_build_game_seed_determinism():
    built_a = build_game(_load_6p_config("g_seed_a"), _agent(), arm="v0", seed=42)
    built_b = build_game(_load_6p_config("g_seed_b"), _agent(), arm="v0", seed=42)
    asyncio.run(built_a.supervisor.run_game("g_seed_a"))
    asyncio.run(built_b.supervisor.run_game("g_seed_b"))
    events_a = built_a.stores.event_store.list_by_game("g_seed_a")
    events_b = built_b.stores.event_store.list_by_game("g_seed_b")
    # 同 seed → 相同 phase 序列 + 相同 actor / target（event_id 因带 game_id 会不同，
    # 但行为应一致）
    assert len(events_a) == len(events_b)
    for a, b in zip(events_a, events_b):
        assert a.event_type == b.event_type
        assert a.phase == b.phase
        assert a.actor == b.actor
        assert a.target == b.target


# ---------------------------------------------------------------------------
# GameStores dataclass 自检
# ---------------------------------------------------------------------------


def test_game_stores_dataclass_defaults():
    """``GameStores`` 默认 belief_* 和 trace_store 为 None。"""
    from stores.event_store import InMemoryEventStore

    stores = GameStores(event_store=InMemoryEventStore())
    assert stores.belief_store is None
    assert stores.belief_observability_store is None
    assert stores.trace_store is None


# ---------------------------------------------------------------------------
# 入口校验（复审 P1 / P2）
# ---------------------------------------------------------------------------


import pytest  # noqa: E402


def test_build_game_rejects_unknown_arm():
    """共享装配函数会被批跑 + API 多处调用；未来 v2 / 拼写错的 arm 应硬失败。"""
    with pytest.raises(ValueError, match="arm must be 'v0' or 'v1'"):
        build_game(_load_6p_config("g_bad_arm"), _agent(), arm="v2", seed=0)
    with pytest.raises(ValueError, match="arm must be 'v0' or 'v1'"):
        build_game(_load_6p_config("g_bad_arm2"), _agent(), arm="V1", seed=0)


def test_build_game_rejects_belief_store_without_belief_lane():
    """arm='v0' 且 use_belief=False 时不允许注 belief_store —— 否则 updater 会被
    隐式创建并写 real lane（is_shadow=False），污染 v0 baseline 实验语义。"""
    from stores.belief_state_store import InMemoryBeliefStateStore

    with pytest.raises(ValueError, match="belief stores require belief lane"):
        build_game(
            _load_6p_config("g_v0_with_belief_store"),
            _agent(),
            arm="v0",
            use_belief=False,
            belief_store=InMemoryBeliefStateStore(),
            seed=0,
        )


def test_build_game_rejects_belief_observability_without_belief_lane():
    """同上，observability store 也不能在 v0 默认路径下被注入。"""
    from stores.belief_observability_store import InMemoryBeliefObservabilityStore

    with pytest.raises(ValueError, match="belief stores require belief lane"):
        build_game(
            _load_6p_config("g_v0_with_belief_obs"),
            _agent(),
            arm="v0",
            use_belief=False,
            belief_observability_store=InMemoryBeliefObservabilityStore(),
            seed=0,
        )


def test_build_game_allows_belief_stores_when_lane_enabled():
    """正向回归：arm='v1' 时 caller 可以注 belief stores（一般是想共享 store 做跨局聚合）。"""
    from stores.belief_state_store import InMemoryBeliefStateStore
    from stores.belief_observability_store import InMemoryBeliefObservabilityStore

    shared_belief_store = InMemoryBeliefStateStore()
    shared_obs_store = InMemoryBeliefObservabilityStore()
    built = build_game(
        _load_6p_config("g_v1_inject"),
        _agent(),
        arm="v1",
        seed=6,
        belief_store=shared_belief_store,
        belief_observability_store=shared_obs_store,
    )
    # 注入的实例确实被复用，不是又创了新的
    assert built.stores.belief_store is shared_belief_store
    assert built.stores.belief_observability_store is shared_obs_store


def test_build_game_rejects_simultaneous_filter_and_factory():
    def my_filter(_agent_id: str) -> bool:
        return True

    def my_factory(_engine, _game_id: str):
        return my_filter

    with pytest.raises(ValueError, match="specify only one"):
        build_game(
            _load_6p_config("g_filter_and_factory"),
            _agent(),
            arm="v1",
            seed=7,
            belief_inject_filter=my_filter,
            belief_inject_filter_factory=my_factory,
        )


def test_build_game_factory_called_after_create_game():
    observed: dict[str, object] = {}

    def factory(engine, game_id: str):
        players = engine.sessions.get_game(game_id).truth_state.players
        observed["game_id"] = game_id
        observed["player_ids"] = tuple(players)
        return lambda _agent_id: False

    build_game(
        _load_6p_config("g_factory_after_create"),
        _agent(),
        arm="v1",
        seed=8,
        belief_inject_filter_factory=factory,
    )

    assert observed["game_id"] == "g_factory_after_create"
    assert observed["player_ids"]


def test_build_game_factory_none_preserves_default_behavior():
    built_default = build_game(
        _load_6p_config("g_factory_none_default"), _agent(), arm="v0", seed=9
    )
    built_explicit_none = build_game(
        _load_6p_config("g_factory_none_explicit"),
        _agent(),
        arm="v0",
        seed=9,
        belief_inject_filter_factory=None,
    )

    assert built_explicit_none.arm == built_default.arm
    assert built_explicit_none.belief_enabled is built_default.belief_enabled
    assert built_explicit_none.belief_is_shadow is built_default.belief_is_shadow
    assert built_explicit_none.stores.belief_store is None
    assert built_explicit_none.stores.belief_observability_store is None
