"""Tests for run_batch-backed metrics endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from api.main import app
from api.runtime import (
    get_belief_store,
    get_event_store,
    get_replay_truth_store,
    get_trace_store,
)
from contracts.enums import Camp, EventType, Phase, Role, Visibility
from contracts.schemas import AgentDecisionTrace, BeliefState, GameEvent, RoleBelief
from stores.belief_state_store import InMemoryBeliefStateStore
from stores.event_store import InMemoryEventStore
from stores.replay_truth_store import InMemoryReplayTruthStore
from stores.trace_store import InMemoryTraceStore


def _event(
    event_id: str,
    game_id: str,
    *,
    event_type: EventType,
    created_at: str,
    round_: int = 1,
    winner: str | None = None,
    player_count: int = 9,
) -> GameEvent:
    payload = {}
    if event_type == EventType.ROLE_ASSIGNED:
        payload = {"player_count": player_count}
    if event_type == EventType.GAME_OVER:
        payload = {"winner": winner}
    return GameEvent(
        event_id=event_id,
        game_id=game_id,
        round=round_,
        phase=Phase.GAME_OVER if event_type == EventType.GAME_OVER else Phase.NIGHT_WEREWOLF,
        event_type=event_type,
        visibility=Visibility.PUBLIC,
        payload=payload,
        created_at=created_at,
    )


def _trace(
    trace_id: str,
    game_id: str,
    *,
    agent_version: str,
    model_name: str = "deepseek-chat",
    outcome: str = "ok",
    parse_error: bool = False,
    llm_error: bool = False,
    retry_count: int = 0,
    canonicalize_triggered: list[str] | None = None,
    latency_ms: float | None = None,
    total_tokens: int | None = None,
) -> AgentDecisionTrace:
    flags = {
        "outcome": outcome,
        "parse_error": parse_error,
        "llm_error": llm_error,
        "retry_count": retry_count,
        "canonicalize_triggered": canonicalize_triggered or [],
    }
    if latency_ms is not None:
        flags["llm_latency_ms"] = latency_ms
    if total_tokens is not None:
        flags["token_usage"] = {"total_tokens": total_tokens}
    return AgentDecisionTrace(
        trace_id=trace_id,
        game_id=game_id,
        round=1,
        phase=Phase.DAY_VOTE,
        agent_id="P1",
        role=Role.VILLAGER,
        agent_version=agent_version,
        model_name=model_name,
        input_summary={},
        decision_output={"action_type": "vote", "target": "P2"},
        decision_quality_flags=flags,
    )


def _belief(game_id: str, agent_id: str, *, p2_wolf: float, p3_wolf: float) -> BeliefState:
    return BeliefState(
        game_id=game_id,
        agent_id=agent_id,
        round=2,
        phase=Phase.DAY_DISCUSSION,
        is_shadow=False,
        beliefs={
            "P2": RoleBelief(werewolf=p2_wolf, villager=1.0 - p2_wolf),
            "P3": RoleBelief(werewolf=p3_wolf, villager=1.0 - p3_wolf),
        },
        last_updated_event_id=f"{game_id}_belief",
    )


def _seed_stores():
    events = InMemoryEventStore()
    traces = InMemoryTraceStore()
    beliefs = InMemoryBeliefStateStore()
    truth = InMemoryReplayTruthStore()

    # Completed v2, arm detected from trace. Werewolves win.
    events.append(_event("v2_e1", "batch_v2_00001", event_type=EventType.ROLE_ASSIGNED, created_at="2026-06-06T00:00:00+00:00"))
    events.append(_event("v2_e2", "batch_v2_00001", event_type=EventType.GAME_OVER, winner="werewolves", round_=4, created_at="2026-06-06T00:02:00+00:00"))
    traces.append(_trace("v2_t1", "batch_v2_00001", agent_version="v2", model_name="deepseek-chat", latency_ms=100.0, total_tokens=10))
    traces.append(
        _trace(
            "v2_t2",
            "batch_v2_00001",
            agent_version="v2",
            model_name="deepseek-chat",
            outcome="parse_error",
            parse_error=True,
            retry_count=2,
            canonicalize_triggered=["meta_ai", "cot_leak", "role_leak"],
            latency_ms=300.0,
            total_tokens=30,
        )
    )
    beliefs.save(_belief("batch_v2_00001", "P1", p2_wolf=0.8, p3_wolf=0.2))
    truth.save_players(
        "batch_v2_00001",
        [
            {"player_id": "P1", "role": Role.VILLAGER.value, "camp": Camp.VILLAGER.value, "status": "alive", "public_claim": None, "vote_weight": 1},
            {"player_id": "P2", "role": Role.WEREWOLF.value, "camp": Camp.WEREWOLF.value, "status": "alive", "public_claim": None, "vote_weight": 1},
            {"player_id": "P3", "role": Role.VILLAGER.value, "camp": Camp.VILLAGER.value, "status": "alive", "public_claim": None, "vote_weight": 1},
        ],
    )

    # Completed v1, villagers win, belief exists but truth missing so quality stays null.
    events.append(_event("v1_e1", "g-v1", event_type=EventType.ROLE_ASSIGNED, created_at="2026-06-06T01:00:00+00:00"))
    events.append(_event("v1_e2", "g-v1", event_type=EventType.GAME_OVER, winner="villagers", round_=3, created_at="2026-06-06T01:01:00+00:00"))
    traces.append(_trace("v1_t1", "g-v1", agent_version="v1", model_name="doubao-pro", outcome="llm_error", llm_error=True))
    beliefs.save(_belief("g-v1", "P1", p2_wolf=0.9, p3_wolf=0.1))

    # Running v2, no traces, arm falls back to game_id prefix and is excluded from winrate denominator.
    events.append(_event("run_e1", "batch_v2_00002", event_type=EventType.ROLE_ASSIGNED, created_at="2026-06-06T02:00:00+00:00"))

    # Completed v0 with no belief.
    events.append(_event("v0_e1", "batch_v0_00001", event_type=EventType.ROLE_ASSIGNED, created_at="2026-06-06T03:00:00+00:00"))
    events.append(_event("v0_e2", "batch_v0_00001", event_type=EventType.GAME_OVER, winner="werewolves", round_=2, created_at="2026-06-06T03:00:30+00:00"))

    return events, traces, beliefs, truth


def test_games_metrics_flatten_run_batch_data_and_detect_v2() -> None:
    from api.metrics_service import list_metric_games

    events, traces, beliefs, truth = _seed_stores()

    rows = list_metric_games(events, traces, beliefs, truth)
    by_id = {row.game_id: row for row in rows}

    v2 = by_id["batch_v2_00001"]
    assert v2.arm == "v2"
    assert v2.model_name == "deepseek-chat"
    assert v2.arm_model == "v2 / deepseek-chat"
    assert v2.status == "completed"
    assert v2.is_wolf_win == 1
    assert v2.duration_ms == pytest.approx(120000.0)
    assert v2.decision_count == 2
    assert v2.ok_count == 1
    assert v2.ok_rate == pytest.approx(0.5)
    assert v2.parse_error == 1
    assert v2.retry_count == 2
    assert v2.canonicalize_meta_ai == 1
    assert v2.canonicalize_cot_leak == 1
    assert v2.canonicalize_role_leak == 1
    assert v2.avg_llm_latency_ms == pytest.approx(200.0)
    assert v2.total_tokens == 40
    assert v2.belief_update_count == 1
    assert v2.belief_final_brier == pytest.approx(0.04)
    assert v2.belief_final_wolf_villager_separation == pytest.approx(0.6)
    assert v2.belief_final_entropy is not None

    running = by_id["batch_v2_00002"]
    assert running.arm == "v2"
    assert running.model_name == "unknown"
    assert running.status == "running"
    assert running.winner is None

    v0 = by_id["batch_v0_00001"]
    assert v0.arm == "v0"
    assert v0.belief_update_count == 0
    assert v0.belief_final_brier is None

    v1 = by_id["g-v1"]
    assert v1.arm == "v1"
    assert v1.belief_update_count == 1
    assert v1.belief_final_brier is None


def test_winrate_uses_completed_games_only() -> None:
    from api.metrics_service import list_metric_games, summarize_winrate

    events, traces, beliefs, truth = _seed_stores()

    rows = list_metric_games(events, traces, beliefs, truth)
    summary = {row.arm: row for row in summarize_winrate(rows)}

    assert summary["v2"].n == 2
    assert summary["v2"].completed == 1
    assert summary["v2"].win_rate_wolf == pytest.approx(1.0)
    assert summary["v1"].win_rate_villager == pytest.approx(1.0)
    assert summary["v0"].win_rate_wolf == pytest.approx(1.0)


def test_winrate_by_model_groups_arm_and_model_name() -> None:
    from api.metrics_service import list_metric_games, summarize_winrate_by_model

    events, traces, beliefs, truth = _seed_stores()

    rows = list_metric_games(events, traces, beliefs, truth)
    summary = {row.arm_model: row for row in summarize_winrate_by_model(rows)}

    assert summary["v2 / deepseek-chat"].arm == "v2"
    assert summary["v2 / deepseek-chat"].model_name == "deepseek-chat"
    assert summary["v2 / deepseek-chat"].n == 1
    assert summary["v2 / deepseek-chat"].completed == 1
    assert summary["v1 / doubao-pro"].win_rate_villager == pytest.approx(1.0)
    assert "v2 / unknown" in summary


def test_summary_counts_all_games_and_arms() -> None:
    from api.metrics_service import list_metric_games, summarize_metrics

    events, traces, beliefs, truth = _seed_stores()

    summary = summarize_metrics(list_metric_games(events, traces, beliefs, truth))

    assert summary.total_games == 4
    assert summary.completed_games == 3
    assert summary.by_arm == {"v0": 1, "v1": 1, "v2": 2, "unknown": 0}
    assert summary.latest_created_at == "2026-06-06T03:00:00+00:00"


def test_metrics_endpoints_use_dependency_injected_stores() -> None:
    from api.metrics_service import _clear_metrics_cache

    _clear_metrics_cache()
    events, traces, beliefs, truth = _seed_stores()
    app.dependency_overrides[get_event_store] = lambda: events
    app.dependency_overrides[get_trace_store] = lambda: traces
    app.dependency_overrides[get_belief_store] = lambda: beliefs
    app.dependency_overrides[get_replay_truth_store] = lambda: truth
    try:
        with TestClient(app) as client:
            games = client.get("/api/metrics/games")
            assert games.status_code == 200
            assert {row["game_id"] for row in games.json()} == {
                "batch_v2_00001",
                "g-v1",
                "batch_v2_00002",
                "batch_v0_00001",
            }

            winrate = client.get("/api/metrics/winrate")
            assert winrate.status_code == 200
            assert {row["arm"] for row in winrate.json()} == {"v0", "v1", "v2"}

            winrate_by_model = client.get("/api/metrics/winrate/by-model")
            assert winrate_by_model.status_code == 200
            assert {row["arm_model"] for row in winrate_by_model.json()} >= {
                "v2 / deepseek-chat",
                "v1 / doubao-pro",
            }

            summary = client.get("/api/metrics/summary")
            assert summary.status_code == 200
            assert summary.json()["completed_games"] == 3
    finally:
        app.dependency_overrides.clear()
        _clear_metrics_cache()


def test_games_endpoint_honors_limit() -> None:
    from api.metrics_service import _clear_metrics_cache

    _clear_metrics_cache()
    events, traces, beliefs, truth = _seed_stores()
    app.dependency_overrides[get_event_store] = lambda: events
    app.dependency_overrides[get_trace_store] = lambda: traces
    app.dependency_overrides[get_belief_store] = lambda: beliefs
    app.dependency_overrides[get_replay_truth_store] = lambda: truth
    try:
        with TestClient(app) as client:
            response = client.get("/api/metrics/games?limit=2")
            assert response.status_code == 200
            assert len(response.json()) == 2
    finally:
        app.dependency_overrides.clear()
        _clear_metrics_cache()


def test_running_games_includes_registry_and_incomplete_batch_games() -> None:
    from api.game_registry import GameRegistry
    from api.metrics_service import list_running_games

    events, traces, beliefs, truth = _seed_stores()
    registry = GameRegistry()
    registry.create(
        game_id="live-game-1",
        player_count=9,
        arm="v1",
        mode="llm",
        event_store=events,
    )
    registry.update_status("live-game-1", "running")

    rows = list_running_games(events, traces, registry=registry)
    by_id = {row.game_id: row for row in rows}

    live = by_id["live-game-1"]
    assert live.status == "running"
    assert live.arm == "v1"
    assert live.model_name == "unknown"
    assert live.player_count == 9

    batch = by_id["batch_v2_00002"]
    assert batch.status == "running_or_incomplete"
    assert batch.arm == "v2"
    assert batch.current_round == 1
    assert batch.current_phase == Phase.NIGHT_WEREWOLF.value
    assert batch.event_count == 1
    assert batch.trace_count == 0

    assert "batch_v2_00001" not in by_id


def test_running_games_endpoint_uses_dependency_injected_stores() -> None:
    from api.game_service import GameRegistry, get_game_registry
    from api.metrics_service import _clear_metrics_cache

    _clear_metrics_cache()
    events, traces, beliefs, truth = _seed_stores()
    app.dependency_overrides[get_event_store] = lambda: events
    app.dependency_overrides[get_trace_store] = lambda: traces
    app.dependency_overrides[get_belief_store] = lambda: beliefs
    app.dependency_overrides[get_replay_truth_store] = lambda: truth
    # 注入隔离的空 registry，避免其它用例（如 test_game_api 的 POST /api/games）
    # 在全局单例里留下的游戏泄漏进来。
    app.dependency_overrides[get_game_registry] = lambda: GameRegistry()
    try:
        with TestClient(app) as client:
            response = client.get("/api/metrics/running-games")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 1
            assert payload["by_arm"] == {"v0": 0, "v1": 0, "v2": 1, "unknown": 0}
            assert payload["games"][0]["game_id"] == "batch_v2_00002"
    finally:
        app.dependency_overrides.clear()
        _clear_metrics_cache()
