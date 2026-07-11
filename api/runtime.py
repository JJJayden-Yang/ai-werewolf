"""API / CLI 共享的运行时存储构造。

本地和线上只通过环境变量切换后端与数据目录；业务入口不要各自写一套
InMemory / JSONL 选择逻辑，避免实时观战和历史回放读写到不同 store。
"""

from __future__ import annotations

import os
from pathlib import Path

from stores.belief_state_store import BeliefStateStore, InMemoryBeliefStateStore, JsonlBeliefStateStore
from stores.event_store import EventStore, InMemoryEventStore, JsonlEventStore
from stores.replay_truth_store import (
    InMemoryReplayTruthStore,
    JsonReplayTruthStore,
    ReplayTruthStore,
)
from stores.trace_store import InMemoryTraceStore, JsonlTraceStore, TraceStore


def build_event_store_from_env() -> EventStore:
    backend = os.getenv("AI_WOLF_STORAGE_BACKEND", "memory").lower()
    if backend == "jsonl":
        root = Path(os.getenv("AI_WOLF_DATA_DIR", "./data"))
        return JsonlEventStore(root / "events")
    return InMemoryEventStore()


def build_trace_store_from_env() -> TraceStore:
    backend = os.getenv("AI_WOLF_STORAGE_BACKEND", "memory").lower()
    if backend == "jsonl":
        root = Path(os.getenv("AI_WOLF_DATA_DIR", "./data"))
        return JsonlTraceStore(root / "traces")
    return InMemoryTraceStore()


def build_belief_store_from_env() -> BeliefStateStore:
    backend = os.getenv("AI_WOLF_STORAGE_BACKEND", "memory").lower()
    if backend == "jsonl":
        root = Path(os.getenv("AI_WOLF_DATA_DIR", "./data"))
        return JsonlBeliefStateStore(root / "belief_states")
    return InMemoryBeliefStateStore()


def build_replay_truth_store_from_env() -> ReplayTruthStore:
    backend = os.getenv("AI_WOLF_STORAGE_BACKEND", "memory").lower()
    if backend == "jsonl":
        root = Path(os.getenv("AI_WOLF_DATA_DIR", "./data"))
        return JsonReplayTruthStore(root / "replay_truth")
    return InMemoryReplayTruthStore()


_default_event_store: EventStore | None = None
_default_trace_store: TraceStore | None = None
_default_belief_store: BeliefStateStore | None = None
_default_replay_truth_store: ReplayTruthStore | None = None
_default_session_provider = None


def get_event_store() -> EventStore:
    global _default_event_store
    if _default_event_store is None:
        _default_event_store = build_event_store_from_env()
    return _default_event_store


def get_trace_store() -> TraceStore:
    global _default_trace_store
    if _default_trace_store is None:
        _default_trace_store = build_trace_store_from_env()
    return _default_trace_store


def get_belief_store() -> BeliefStateStore:
    global _default_belief_store
    if _default_belief_store is None:
        _default_belief_store = build_belief_store_from_env()
    return _default_belief_store


def get_replay_truth_store() -> ReplayTruthStore:
    global _default_replay_truth_store
    if _default_replay_truth_store is None:
        _default_replay_truth_store = build_replay_truth_store_from_env()
    return _default_replay_truth_store


def get_session_provider():
    return _default_session_provider
