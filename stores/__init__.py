"""stores —— 人 C 负责的持久化层。

EventStore / BeliefStateStore / PromptVersionRegistry / Trace stores /
StrategyMemoryStore。第一阶段后端可用 JSONL / SQLite / in-memory，
但对外接口固定，不允许跨模块绕过。
"""

from stores.belief_state_store import (
    BeliefStateStore,
    InMemoryBeliefStateStore,
    JsonlBeliefStateStore,
)
from stores.belief_observability_store import (
    BeliefObservabilityStore,
    InMemoryBeliefObservabilityStore,
)
from stores.context_snapshot_store import ContextSnapshotStore
from stores.event_store import EventStore, InMemoryEventStore, JsonlEventStore
from stores.exceptions import (
    BeliefStateNotFoundError,
    DuplicateEventError,
    DuplicateTraceError,
    EventNotFoundError,
    StoreError,
    TraceNotFoundError,
)
from stores.prompt_version_registry import PromptVersionRegistry
from stores.replay_truth_store import (
    InMemoryReplayTruthStore,
    JsonReplayTruthStore,
    ReplayTruthStore,
    build_player_snapshots,
)
from stores.strategy_memory_store import StrategyMemoryStore
from stores.trace_store import (
    AgentTuningTraceStore,
    InMemoryTraceStore,
    JsonlTraceStore,
    TraceStore,
)

__all__ = [
    # EventStore
    "EventStore",
    "InMemoryEventStore",
    "JsonlEventStore",
    # BeliefStateStore
    "BeliefStateStore",
    "InMemoryBeliefStateStore",
    "JsonlBeliefStateStore",
    # Belief observability
    "BeliefObservabilityStore",
    "InMemoryBeliefObservabilityStore",
    # TraceStore
    "TraceStore",
    "InMemoryTraceStore",
    "JsonlTraceStore",
    "AgentTuningTraceStore",
    # ReplayTruthStore
    "ReplayTruthStore",
    "InMemoryReplayTruthStore",
    "JsonReplayTruthStore",
    "build_player_snapshots",
    # ContextSnapshotStore (Phase 3 占位，待 A merge schema 后实装)
    "ContextSnapshotStore",
    # 其它 stores
    "PromptVersionRegistry",
    "StrategyMemoryStore",
    # 异常
    "StoreError",
    "EventNotFoundError",
    "DuplicateEventError",
    "BeliefStateNotFoundError",
    "TraceNotFoundError",
    "DuplicateTraceError",
]
