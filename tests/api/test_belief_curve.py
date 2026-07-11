"""Tests for belief_curve_series (pure) + /api/audit/runs/{id}/belief_curve endpoint."""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from api.audit_belief_curve import get_belief_store_for_curve
from api.main import app
from contracts import BeliefState, Phase, RoleBelief
from scripts._belief_curve import belief_curve_series
from stores.belief_state_store import InMemoryBeliefStateStore


def _bs(*, agent_id: str, round_: int, beliefs: dict[str, float], is_shadow: bool = False) -> BeliefState:
    return BeliefState(
        game_id="g-curve",
        agent_id=agent_id,
        round=round_,
        phase=Phase.DAY_DISCUSSION,
        is_shadow=is_shadow,
        beliefs={pid: RoleBelief(werewolf=w) for pid, w in beliefs.items()},
    )


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #


def test_belief_curve_series_excludes_self_and_ranks() -> None:
    history = [
        _bs(agent_id="P1", round_=1, beliefs={"P1": 0.9, "P2": 0.6, "P3": 0.2}),
    ]
    series = belief_curve_series(history)  # self_id 从 history[0] 推断 = P1

    assert len(series) == 1
    pt = series[0]
    # P1（自己）即便 werewolf=0.9 也不进排序。
    assert pt["top1_target"] == "P2"
    assert pt["top1_prob"] == 0.6
    assert abs(pt["top_margin"] - 0.4) < 1e-9  # 0.6 - 0.2
    # observer 视角永远不算 separation（红线）。
    assert pt["separation"] is None
    # by_target 仍包含自己（原始概率如实呈现）。
    assert "P1" in pt["by_target"]
    assert pt["entropy"] > 0.0


def test_belief_curve_series_entropy_matches_shared_math() -> None:
    history = [_bs(agent_id="P1", round_=1, beliefs={"P2": 0.6, "P3": 0.2})]
    pt = belief_curve_series(history)[0]
    # 与 _mixed_metrics._suspicion_entropy 同口径：归一 [0.75, 0.25] 的香农熵 / log2(2)。
    dist = [0.6 / 0.8, 0.2 / 0.8]
    expected = -sum(x * math.log2(x) for x in dist)
    assert abs(pt["entropy"] - expected) < 1e-9


def test_belief_curve_series_single_target_entropy_zero() -> None:
    history = [_bs(agent_id="P1", round_=1, beliefs={"P2": 0.5})]
    pt = belief_curve_series(history)[0]
    assert pt["top1_target"] == "P2"
    assert pt["top_margin"] == 0.5  # 无 top2 → 减 0
    assert pt["entropy"] == 0.0


def test_belief_curve_series_empty_history() -> None:
    assert belief_curve_series([]) == []


def test_belief_curve_series_tracks_steps() -> None:
    history = [
        _bs(agent_id="P1", round_=1, beliefs={"P2": 0.3, "P3": 0.3}),
        _bs(agent_id="P1", round_=2, beliefs={"P2": 0.7, "P3": 0.2}),
    ]
    series = belief_curve_series(history)
    assert [p["step"] for p in series] == [0, 1]
    assert series[1]["top1_target"] == "P2"
    assert series[1]["top1_prob"] == 0.7


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #


def _store_with_history() -> InMemoryBeliefStateStore:
    store = InMemoryBeliefStateStore()
    store.save(_bs(agent_id="P1", round_=1, beliefs={"P2": 0.6, "P3": 0.2}))
    store.save(_bs(agent_id="P1", round_=2, beliefs={"P2": 0.7, "P3": 0.2}))
    store.save(_bs(agent_id="P2", round_=1, beliefs={"P1": 0.4, "P3": 0.3}))
    # shadow lane
    store.save(_bs(agent_id="P4", round_=1, beliefs={"P2": 0.5}, is_shadow=True))
    return store


def test_belief_curve_endpoint_returns_all_observers() -> None:
    store = _store_with_history()
    app.dependency_overrides[get_belief_store_for_curve] = lambda: store
    try:
        with TestClient(app) as client:
            resp = client.get("/api/audit/runs/g-curve/belief_curve")
            assert resp.status_code == 200
            data = resp.json()
            assert data["lane"] == "real"
            assert set(data["observers"]) == {"P1", "P2"}
            assert len(data["series"]["P1"]) == 2
            assert data["series"]["P1"][1]["top1_target"] == "P2"
    finally:
        app.dependency_overrides.clear()


def test_belief_curve_endpoint_observer_filter() -> None:
    store = _store_with_history()
    app.dependency_overrides[get_belief_store_for_curve] = lambda: store
    try:
        with TestClient(app) as client:
            resp = client.get("/api/audit/runs/g-curve/belief_curve?observer=P1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["observers"] == ["P1"]
            assert "P2" not in data["series"]
    finally:
        app.dependency_overrides.clear()


def test_belief_curve_endpoint_shadow_lane() -> None:
    store = _store_with_history()
    app.dependency_overrides[get_belief_store_for_curve] = lambda: store
    try:
        with TestClient(app) as client:
            resp = client.get("/api/audit/runs/g-curve/belief_curve?lane=shadow")
            assert resp.status_code == 200
            data = resp.json()
            assert data["lane"] == "shadow"
            assert data["observers"] == ["P4"]
    finally:
        app.dependency_overrides.clear()


def test_belief_curve_endpoint_404_when_no_history() -> None:
    store = InMemoryBeliefStateStore()
    app.dependency_overrides[get_belief_store_for_curve] = lambda: store
    try:
        with TestClient(app) as client:
            resp = client.get("/api/audit/runs/g-missing/belief_curve")
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 缓存陈旧修复：jsonl 后端按请求读盘新鲜，memory 后端用单例
# --------------------------------------------------------------------------- #


def test_belief_store_for_curve_reads_fresh_jsonl(monkeypatch, tmp_path) -> None:
    from stores.belief_state_store import JsonlBeliefStateStore

    monkeypatch.setenv("AI_WOLF_STORAGE_BACKEND", "jsonl")
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))

    # 模拟另一进程（run_mixed_batch）把 belief 写到 canonical 根。
    writer = JsonlBeliefStateStore(tmp_path / "belief_states")
    writer.save(_bs(agent_id="P1", round_=1, beliefs={"P2": 0.6}))

    # 端点依赖每请求新建 store → 读到磁盘上的局（无需重启 API）。
    store1 = get_belief_store_for_curve()
    assert len(store1.get_history("g-curve", "P1")) == 1

    # 关键：首次读之后再追加一条，下一次依赖调用仍能吃到新写（新鲜，非陈旧缓存）。
    writer.save(_bs(agent_id="P1", round_=2, beliefs={"P2": 0.7}))
    store2 = get_belief_store_for_curve()
    assert len(store2.get_history("g-curve", "P1")) == 2


def test_belief_store_for_curve_memory_uses_singleton(monkeypatch) -> None:
    from api.runtime import get_belief_store

    monkeypatch.delenv("AI_WOLF_STORAGE_BACKEND", raising=False)
    # memory 后端：内存单例才是真相源（in-process 跑的局写在那），不能每请求新建空 store。
    assert get_belief_store_for_curve() is get_belief_store()
