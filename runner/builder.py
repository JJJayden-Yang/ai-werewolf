"""``build_game`` —— 把"建 engine + stores + assembler + supervisor"一次性封好。

phase5 三方向并行地基 §10：A 抽出共享装配函数，A 的批跑（``scripts/run_v0_batch.py``，
后续混合实验也会走这条）和 B 的实时观战（``api/`` 下 ``POST /games``）都调它，
不要各写一份装配代码将来漂移。

设计要点:

- ``agent`` 由 caller 构造好后传进来（LLMAgent / MockAgent / 混合实验的 per-player
  路由 adapter 都行）—— build_game 不替 caller 决定 agent_version / template_name /
  provider。
- ``belief_inject_filter`` 是 PR-FD-B（Jiangyi 给 ``ContextAssembler.__init__`` 加的
  kwarg）的对接位。**当 filter 为 None 时本函数不会向 ContextAssembler 传这个 kwarg**，
  所以 PR-FD-B 还没合并时 build_game 也能用（默认路径不依赖那个新 kwarg）。
- ``event_observer`` 是 PR-FD-A（已合 ``dd551c5``）的对接位，给 SSE 实时流用。

红线遵循:

- 不动 contracts schemas / enums。
- Supervisor / Engine / ContextAssembler 仍按各自 owner 的契约组合，本函数只做组合。
- 信息隔离：build_game 不向 agent 暴露 TruthState、不写 belief 数学。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater
from context.context_assembler import ContextAssembler
from context.context_window_policy import ContextWindowPolicy
from game_core import GameEngine, GameSessionManager
from stores.belief_observability_store import (
    BeliefObservabilityStore,
    InMemoryBeliefObservabilityStore,
)
from stores.belief_state_store import (
    BeliefStateStore,
    InMemoryBeliefStateStore,
)
from stores.event_store import EventStore, InMemoryEventStore
from supervisor import Supervisor

if TYPE_CHECKING:
    from contracts import GameConfig, GameEvent, Role
    from stores.trace_store import TraceStore
    from supervisor.protocols import AgentRuntime


@dataclass
class GameStores:
    """一局游戏的存储四件套 —— ``belief_*`` 为 None 表示未启用 belief lane。

    caller 可通过 ``BuiltGame.stores`` 拿到这些 store 做后续观测（看 belief
    曲线、查事件流、查决策 trace 等）。
    """

    event_store: EventStore
    belief_store: BeliefStateStore | None = None
    belief_observability_store: BeliefObservabilityStore | None = None
    trace_store: "TraceStore | None" = None


@dataclass
class BuiltGame:
    """``build_game`` 的返回包：engine + supervisor + stores + arm 元数据。

    ``window_policy`` 是装配时建的 ``ContextWindowPolicy``（已塞给 ``ContextAssembler``）；
    在此暴露引用，便于跑批后读 ``window_policy.stats``（裁剪/降级/超预算计数）做 sidecar
    指标，**无需穿透 ``ContextAssembler`` 私有属性**。
    """

    engine: GameEngine
    supervisor: Supervisor
    stores: GameStores
    arm: str
    belief_enabled: bool
    belief_is_shadow: bool
    window_policy: ContextWindowPolicy | None = None


def build_game(
    config: "GameConfig",
    agent: "AgentRuntime",
    *,
    arm: str = "v0",
    use_belief: bool = False,
    seed: int | None = None,
    event_observer: "Callable[[GameEvent], None] | None" = None,
    belief_inject_filter: Callable[[str], bool] | None = None,
    belief_inject_filter_factory: (
        Callable[["GameEngine", str], Callable[[str], bool]] | None
    ) = None,
    deliver_witch_kill_info: bool = True,
    belief_kernel: str = "additive_v1",
    slow_think_policy: "Any | None" = None,
    trace_store: "TraceStore | None" = None,
    event_store: EventStore | None = None,
    belief_store: BeliefStateStore | None = None,
    belief_observability_store: BeliefObservabilityStore | None = None,
    fixed_roles: "dict[str, Role] | None" = None,
) -> BuiltGame:
    """装配一局所需的全部组件，返回 ``BuiltGame``。

    Args:
        config: 已校验的 ``GameConfig``；本函数会调 ``engine.sessions.create_game`` 注册到引擎。
        agent: 任何满足 ``AgentRuntime`` Protocol 的对象（``LLMAgent`` / ``MockAgent`` /
            混合实验的 per-player 路由 adapter）。``agent_version`` / ``template_name``
            由 caller 在构造 agent 时设定。
        arm: ``"v0"`` 或 ``"v1"``。决定 ``ContextAssembler`` 是否拿 ``belief_store``。
        use_belief: ``arm="v0"`` + ``use_belief=True`` → shadow 模式：后台维护 belief
            但不注入 agent（``arm=="v1"`` 路径自动启 belief，不需要本 flag）。
        seed: ``GameSessionManager`` 的 RNG 种子；None 走默认（不确定）。
        event_observer: phase5 §2.3 —— ``Supervisor.append_events`` 后逐条回调。
            旁观者只读（深拷贝隔离 + 异常吞掉，红线由 ``Supervisor`` 强制）。
        belief_inject_filter: phase5 §2.1 —— ``ContextAssembler`` 按 player 决定
            是否注 belief（A 的混合实验用）。**依赖 PR-FD-B**（Jiangyi 给
            ``ContextAssembler.__init__`` 加的 kwarg）。filter 为 None 时本函数
            **不**向 ContextAssembler 传这个 kwarg，所以 PR-FD-B 未合并时也可用。
        belief_inject_filter_factory: Phase 6 A 线混合 belief 实验用的延迟 filter
            工厂。``build_game`` 会在 ``engine.sessions.create_game(config)`` 后调用，
            因此 factory 能读取刚发好牌的 ``truth_state`` 做 snapshot filter。
            与 ``belief_inject_filter`` 互斥。
        deliver_witch_kill_info: v0 LLM 默认 True（女巫看得到当晚刀口；详见
            ``contracts/README.md §8.A`` 2026-05-26 条目）。
        belief_kernel: belief updater 内核，默认 ``"additive_v1"`` 保持现有行为；
            ``"factorized_v2"`` 启用 log-odds + source credibility 实验内核。
        trace_store: 仅打包到 ``BuiltGame.stores``；agent 自己的 ``trace_store``
            字段由 caller 在构造 agent 时单独传。
        event_store / belief_store / belief_observability_store: 可选注入；
            None 时建 ``InMemory`` 版（批跑/MVP 路径默认走 InMemory）。

    Returns:
        ``BuiltGame``。``asyncio.run(built.supervisor.run_game(config.game_id))`` 即跑一局。

    Raises:
        ValueError: ``arm`` 不在 ``{"v0", "v1"}``；或 belief lane 未启用
            （``arm="v0" and use_belief=False``）却注入了 ``belief_store`` /
            ``belief_observability_store`` —— 后者会让 ``BuiltGame.belief_enabled``
            和 Supervisor 实际行为不一致（静默写 real lane），实验统计变脏，故硬失败。
        TypeError: 如果传了 ``belief_inject_filter`` 但 ``ContextAssembler`` 还没
            合并 PR-FD-B 那个 kwarg，会从 ``ContextAssembler.__init__`` 抛 TypeError；
            这是预期失败模式（清晰提示 PR-FD-B 未到位）。
    """
    if arm not in {"v0", "v1"}:
        raise ValueError(f"arm must be 'v0' or 'v1', got {arm!r}")
    if belief_inject_filter is not None and belief_inject_filter_factory is not None:
        raise ValueError("specify only one of belief_inject_filter / belief_inject_filter_factory")

    belief_enabled = use_belief or arm == "v1"
    belief_is_shadow = belief_enabled and arm == "v0"

    # belief lane 未启用时不允许 caller 注 belief stores —— 否则下面 belief_store is not None
    # 的判定会偷偷创建 updater（is_shadow=False），写到 real lane，污染混合实验语义。
    # 实验开关必须显式（arm='v1' 或 use_belief=True）；不接受 "我传 store 就当启用了" 的隐式语义。
    if not belief_enabled and (
        belief_store is not None or belief_observability_store is not None
    ):
        raise ValueError(
            "belief stores require belief lane to be enabled "
            "(arm='v1' or use_belief=True); got "
            f"arm={arm!r} use_belief={use_belief}"
        )

    engine = GameEngine()
    if seed is not None:
        engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config, fixed_roles=fixed_roles)

    if belief_inject_filter_factory is not None:
        belief_inject_filter = belief_inject_filter_factory(engine, config.game_id)

    if event_store is None:
        event_store = InMemoryEventStore()
    if belief_enabled and belief_store is None:
        belief_store = InMemoryBeliefStateStore()
    if belief_enabled and belief_observability_store is None:
        belief_observability_store = InMemoryBeliefObservabilityStore()

    belief_updater = (
        RuleBasedRealtimeBeliefUpdater(
            event_store=event_store,
            belief_store=belief_store,
            session_provider=engine,
            is_shadow=belief_is_shadow,
            observability_store=belief_observability_store,
            belief_kernel=belief_kernel,
        )
        if belief_store is not None
        else None
    )

    window_policy = ContextWindowPolicy()
    assembler_kwargs: dict[str, Any] = {
        "session_provider": engine,
        "event_store": event_store,
        "window_policy": window_policy,
        "belief_store": belief_store if arm == "v1" else None,
    }
    if belief_inject_filter is not None:
        # 只在调用方真要用 filter 时才传 kwarg —— PR-FD-B 未合并时 ContextAssembler
        # 不接 kwarg，会 TypeError；这是预期的清晰失败而不是隐式被吞。filter=None
        # 时不传 kwarg，所以默认路径不依赖 PR-FD-B。
        assembler_kwargs["belief_inject_filter"] = belief_inject_filter

    assembler = ContextAssembler(**assembler_kwargs)

    # System2 慢思（M4）：reflect 是纯变换，由 Supervisor 用同一个 belief_store 读当前 belief
    # 并把 enriched 落盘。slow_think_policy 默认 None → 零回归。
    supervisor = Supervisor(
        engine,
        assembler,
        agent,
        event_store,
        belief_updater=belief_updater,
        deliver_witch_kill_info=deliver_witch_kill_info,
        event_observer=event_observer,
        slow_think_policy=slow_think_policy,
        belief_store=belief_store,
    )

    stores = GameStores(
        event_store=event_store,
        belief_store=belief_store,
        belief_observability_store=belief_observability_store,
        trace_store=trace_store,
    )
    return BuiltGame(
        engine=engine,
        supervisor=supervisor,
        stores=stores,
        arm=arm,
        belief_enabled=belief_enabled,
        belief_is_shadow=belief_is_shadow,
        window_policy=window_policy,
    )
