"""Run-batch metrics endpoints for Grafana.

This service reads existing JSONL-backed stores and exposes API-owned response
models. It does not modify ``contracts/`` and does not depend on
``run_mixed_batch`` sidecar files.
"""

from __future__ import annotations

import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.game_service import get_game_registry
from api.runtime import (
    get_belief_store,
    get_event_store,
    get_replay_truth_store,
    get_trace_store,
)
from contracts import EventType

if TYPE_CHECKING:
    from contracts.schemas import AgentDecisionTrace, BeliefState, GameEvent
    from stores.belief_state_store import BeliefStateStore
    from stores.event_store import EventStore
    from stores.replay_truth_store import ReplayTruthStore
    from stores.trace_store import TraceStore


router = APIRouter()
_WOLF_WINNER = "werewolves"
_VILLAGER_WINNER = "villagers"
_KNOWN_ARMS = ("v0", "v1", "v2")
_DEFAULT_GAMES_LIMIT = 500
_METRICS_CACHE_TTL_SECONDS = float(os.getenv("AI_WOLF_METRICS_CACHE_TTL_SECONDS", "30"))
_METRICS_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
# 已完成局（含 GAME_OVER）的统计行永不变，按 game_id 永久缓存：全量扫只对未缓存
# 的 game_id（在跑的几局 + 新完成的几局）真正读盘，把 O(全部局) 降到 O(新增局)。
_GAME_ROW_CACHE: dict[str, MetricGameRow] = {}


class MetricGameRow(BaseModel):
    game_id: str
    created_at: str | None = None
    arm: str
    model_name: str
    arm_model: str
    status: str
    winner: str | None = None
    is_wolf_win: int | None = None
    player_count: int | None = None
    rounds: int | None = None
    duration_ms: float | None = None
    event_count: int
    trace_count: int
    decision_count: int
    ok_count: int
    ok_rate: float | None = None
    parse_error: int
    llm_error: int
    retry_count: int
    canonicalize_meta_ai: int
    canonicalize_cot_leak: int
    canonicalize_role_leak: int
    avg_llm_latency_ms: float | None = None
    total_tokens: int
    belief_update_count: int
    belief_final_brier: float | None = None
    belief_final_wolf_villager_separation: float | None = None
    belief_final_entropy: float | None = None


class WinrateRow(BaseModel):
    arm: str
    model_name: str | None = None
    arm_model: str | None = None
    n: int
    completed: int
    win_rate_wolf: float | None = None
    win_rate_villager: float | None = None
    avg_rounds: float | None = None
    avg_duration_ms: float | None = None
    avg_ok_rate: float | None = None
    total_parse_error: int
    total_llm_error: int
    avg_llm_latency_ms: float | None = None
    belief_final_brier: float | None = None
    belief_final_wolf_villager_separation: float | None = None
    belief_final_entropy: float | None = None


class MetricsSummary(BaseModel):
    total_games: int
    completed_games: int
    by_arm: dict[str, int]
    latest_created_at: str | None = None


class RunningGameRow(BaseModel):
    game_id: str
    arm: str
    model_name: str
    arm_model: str
    status: str
    created_at: str | None = None
    latest_event_at: str | None = None
    player_count: int | None = None
    current_round: int | None = None
    current_phase: str | None = None
    event_count: int
    trace_count: int
    duration_ms: float | None = None


class RunningGamesResponse(BaseModel):
    total: int
    by_arm: dict[str, int]
    games: list[RunningGameRow]


def list_metric_games(
    event_store: "EventStore",
    trace_store: "TraceStore | None" = None,
    belief_store: "BeliefStateStore | None" = None,
    replay_truth_store: "ReplayTruthStore | None" = None,
) -> list[MetricGameRow]:
    list_game_ids = getattr(event_store, "list_game_ids", None)
    if not callable(list_game_ids):
        return []

    rows: list[MetricGameRow] = []
    for game_id in list_game_ids():
        cached = _GAME_ROW_CACHE.get(game_id)
        if cached is not None:
            rows.append(cached)
            continue
        try:
            events = event_store.list_by_game(game_id)
        except Exception:
            continue
        if not events:
            continue
        traces = _safe_traces(trace_store, game_id)
        row = _game_row(game_id, events, traces, belief_store, replay_truth_store)
        if row.status == "completed":
            # 终局不可变，永久缓存；running 局下次重算直到它完成。
            _GAME_ROW_CACHE[game_id] = row
        rows.append(row)
    return sorted(rows, key=lambda row: row.created_at or "", reverse=True)


def summarize_winrate(rows: list[MetricGameRow]) -> list[WinrateRow]:
    by_arm: dict[str, list[MetricGameRow]] = defaultdict(list)
    for row in rows:
        if row.arm in _KNOWN_ARMS:
            by_arm[row.arm].append(row)

    out: list[WinrateRow] = []
    for arm in sorted(by_arm):
        arm_rows = by_arm[arm]
        completed = [row for row in arm_rows if row.status == "completed"]
        wolf_wins = sum(1 for row in completed if row.winner == _WOLF_WINNER)
        villager_wins = sum(1 for row in completed if row.winner == _VILLAGER_WINNER)
        out.append(
            WinrateRow(
                arm=arm,
                model_name=None,
                arm_model=None,
                n=len(arm_rows),
                completed=len(completed),
                win_rate_wolf=_rate(wolf_wins, len(completed)),
                win_rate_villager=_rate(villager_wins, len(completed)),
                avg_rounds=_avg([row.rounds for row in completed]),
                avg_duration_ms=_avg([row.duration_ms for row in completed]),
                avg_ok_rate=_avg([row.ok_rate for row in arm_rows]),
                total_parse_error=sum(row.parse_error for row in arm_rows),
                total_llm_error=sum(row.llm_error for row in arm_rows),
                avg_llm_latency_ms=_avg([row.avg_llm_latency_ms for row in arm_rows]),
                belief_final_brier=_avg([row.belief_final_brier for row in arm_rows]),
                belief_final_wolf_villager_separation=_avg(
                    [row.belief_final_wolf_villager_separation for row in arm_rows]
                ),
                belief_final_entropy=_avg([row.belief_final_entropy for row in arm_rows]),
            )
        )
    return out


def summarize_winrate_by_model(rows: list[MetricGameRow]) -> list[WinrateRow]:
    by_key: dict[tuple[str, str], list[MetricGameRow]] = defaultdict(list)
    for row in rows:
        if row.arm in _KNOWN_ARMS:
            by_key[(row.arm, row.model_name)].append(row)

    out: list[WinrateRow] = []
    for arm, model_name in sorted(by_key):
        grouped = by_key[(arm, model_name)]
        completed = [row for row in grouped if row.status == "completed"]
        wolf_wins = sum(1 for row in completed if row.winner == _WOLF_WINNER)
        villager_wins = sum(1 for row in completed if row.winner == _VILLAGER_WINNER)
        out.append(
            WinrateRow(
                arm=arm,
                model_name=model_name,
                arm_model=_arm_model(arm, model_name),
                n=len(grouped),
                completed=len(completed),
                win_rate_wolf=_rate(wolf_wins, len(completed)),
                win_rate_villager=_rate(villager_wins, len(completed)),
                avg_rounds=_avg([row.rounds for row in completed]),
                avg_duration_ms=_avg([row.duration_ms for row in completed]),
                avg_ok_rate=_avg([row.ok_rate for row in grouped]),
                total_parse_error=sum(row.parse_error for row in grouped),
                total_llm_error=sum(row.llm_error for row in grouped),
                avg_llm_latency_ms=_avg([row.avg_llm_latency_ms for row in grouped]),
                belief_final_brier=_avg([row.belief_final_brier for row in grouped]),
                belief_final_wolf_villager_separation=_avg(
                    [row.belief_final_wolf_villager_separation for row in grouped]
                ),
                belief_final_entropy=_avg([row.belief_final_entropy for row in grouped]),
            )
        )
    return out


def summarize_metrics(rows: list[MetricGameRow]) -> MetricsSummary:
    counts = {arm: 0 for arm in (*_KNOWN_ARMS, "unknown")}
    for row in rows:
        counts[row.arm if row.arm in counts else "unknown"] += 1
    return MetricsSummary(
        total_games=len(rows),
        completed_games=sum(1 for row in rows if row.status == "completed"),
        by_arm=counts,
        latest_created_at=max((row.created_at for row in rows if row.created_at), default=None),
    )


def list_running_games(
    event_store: "EventStore",
    trace_store: "TraceStore | None" = None,
    *,
    registry: Any | None = None,
) -> list[RunningGameRow]:
    rows_by_id: dict[str, RunningGameRow] = {}
    if registry is not None:
        for record in registry.list():
            if getattr(record, "status", None) not in {"pending", "running", "error"}:
                continue
            game_id = str(record.game_id)
            events = _safe_events(event_store, game_id)
            traces = _safe_traces(trace_store, game_id)
            rows_by_id[game_id] = _running_row(
                game_id,
                events,
                traces,
                status=str(record.status),
                fallback_arm=str(record.arm),
                fallback_created_at=str(record.created_at),
                fallback_player_count=int(record.player_count),
            )

    list_game_ids = getattr(event_store, "list_game_ids", None)
    if callable(list_game_ids):
        for game_id in list_game_ids():
            if game_id in rows_by_id:
                continue
            events = _safe_events(event_store, game_id)
            if not events or _has_game_over(events):
                continue
            traces = _safe_traces(trace_store, game_id)
            rows_by_id[game_id] = _running_row(game_id, events, traces, status="running_or_incomplete")

    return sorted(
        rows_by_id.values(),
        key=lambda row: row.latest_event_at or row.created_at or "",
        reverse=True,
    )


def summarize_running_games(rows: list[RunningGameRow]) -> RunningGamesResponse:
    counts = {arm: 0 for arm in (*_KNOWN_ARMS, "unknown")}
    for row in rows:
        counts[row.arm if row.arm in counts else "unknown"] += 1
    return RunningGamesResponse(total=len(rows), by_arm=counts, games=rows)


@router.get("/api/metrics/games", response_model=list[MetricGameRow])
def list_metric_games_endpoint(
    limit: int = Query(_DEFAULT_GAMES_LIMIT, ge=1, le=5000),
    event_store: "EventStore" = Depends(get_event_store),
    trace_store: "TraceStore" = Depends(get_trace_store),
    belief_store: "BeliefStateStore" = Depends(get_belief_store),
    replay_truth_store: "ReplayTruthStore" = Depends(get_replay_truth_store),
) -> list[MetricGameRow]:
    rows = _cached_metric_games(event_store, trace_store, belief_store, replay_truth_store)
    return rows[:limit]


@router.get("/api/metrics/winrate", response_model=list[WinrateRow])
def list_winrate_endpoint(
    event_store: "EventStore" = Depends(get_event_store),
    trace_store: "TraceStore" = Depends(get_trace_store),
    belief_store: "BeliefStateStore" = Depends(get_belief_store),
    replay_truth_store: "ReplayTruthStore" = Depends(get_replay_truth_store),
) -> list[WinrateRow]:
    rows = _cached_metric_games(event_store, trace_store, belief_store, replay_truth_store)
    return summarize_winrate(rows)


@router.get("/api/metrics/winrate/by-model", response_model=list[WinrateRow])
def list_winrate_by_model_endpoint(
    event_store: "EventStore" = Depends(get_event_store),
    trace_store: "TraceStore" = Depends(get_trace_store),
    belief_store: "BeliefStateStore" = Depends(get_belief_store),
    replay_truth_store: "ReplayTruthStore" = Depends(get_replay_truth_store),
) -> list[WinrateRow]:
    rows = _cached_metric_games(event_store, trace_store, belief_store, replay_truth_store)
    return summarize_winrate_by_model(rows)


@router.get("/api/metrics/summary", response_model=MetricsSummary)
def get_summary_endpoint(
    event_store: "EventStore" = Depends(get_event_store),
    trace_store: "TraceStore" = Depends(get_trace_store),
    belief_store: "BeliefStateStore" = Depends(get_belief_store),
    replay_truth_store: "ReplayTruthStore" = Depends(get_replay_truth_store),
) -> MetricsSummary:
    rows = _cached_metric_games(event_store, trace_store, belief_store, replay_truth_store)
    return summarize_metrics(rows)


@router.get("/api/metrics/running-games", response_model=RunningGamesResponse)
def list_running_games_endpoint(
    event_store: "EventStore" = Depends(get_event_store),
    trace_store: "TraceStore" = Depends(get_trace_store),
    game_registry: Any = Depends(get_game_registry),
) -> RunningGamesResponse:
    rows = _cached_running_games(event_store, trace_store, registry=game_registry)
    return summarize_running_games(rows)


def _cached_metric_games(
    event_store: "EventStore",
    trace_store: "TraceStore | None",
    belief_store: "BeliefStateStore | None",
    replay_truth_store: "ReplayTruthStore | None",
) -> list[MetricGameRow]:
    key = ("metric_games", id(event_store), id(trace_store), id(belief_store), id(replay_truth_store))
    return _cached(key, lambda: list_metric_games(event_store, trace_store, belief_store, replay_truth_store))


def _cached_running_games(
    event_store: "EventStore",
    trace_store: "TraceStore | None",
    *,
    registry: Any | None,
) -> list[RunningGameRow]:
    key = ("running_games", id(event_store), id(trace_store), id(registry))
    return _cached(key, lambda: list_running_games(event_store, trace_store, registry=registry))


def _cached(key: tuple[Any, ...], loader):
    now = time.monotonic()
    cached = _METRICS_CACHE.get(key)
    if cached is not None:
        loaded_at, value = cached
        if now - loaded_at < _METRICS_CACHE_TTL_SECONDS:
            return value
    value = loader()
    _METRICS_CACHE[key] = (now, value)
    return value


def _clear_metrics_cache() -> None:
    _METRICS_CACHE.clear()
    _GAME_ROW_CACHE.clear()


def warm_metrics_cache(
    event_store: "EventStore",
    trace_store: "TraceStore | None" = None,
    belief_store: "BeliefStateStore | None" = None,
    replay_truth_store: "ReplayTruthStore | None" = None,
) -> int:
    """一次性预热 per-game 缓存（启动时后台调用），返回已完成局数量。"""
    list_metric_games(event_store, trace_store, belief_store, replay_truth_store)
    return len(_GAME_ROW_CACHE)


def _game_row(
    game_id: str,
    events: list["GameEvent"],
    traces: list["AgentDecisionTrace"],
    belief_store: "BeliefStateStore | None",
    replay_truth_store: "ReplayTruthStore | None",
) -> MetricGameRow:
    first_event = events[0]
    game_over = next((event for event in reversed(events) if event.event_type == EventType.GAME_OVER), None)
    role_assigned = next((event for event in events if event.event_type == EventType.ROLE_ASSIGNED), None)
    winner = _winner_from(game_over)
    completed = game_over is not None
    quality = _trace_quality(traces)
    belief = _belief_quality(game_id, belief_store, replay_truth_store)
    arm = _infer_arm(game_id, traces)
    model_name = _infer_model_name(traces)

    return MetricGameRow(
        game_id=game_id,
        created_at=first_event.created_at,
        arm=arm,
        model_name=model_name,
        arm_model=_arm_model(arm, model_name),
        status="completed" if completed else "running",
        winner=winner,
        is_wolf_win=(1 if winner == _WOLF_WINNER else 0) if completed else None,
        player_count=_player_count(role_assigned),
        rounds=max((event.round for event in events), default=None),
        duration_ms=_duration_ms(events),
        event_count=len(events),
        trace_count=len(traces),
        decision_count=quality["decision_count"],
        ok_count=quality["ok_count"],
        ok_rate=_rate(quality["ok_count"], quality["decision_count"]),
        parse_error=quality["parse_error"],
        llm_error=quality["llm_error"],
        retry_count=quality["retry_count"],
        canonicalize_meta_ai=quality["canonicalize_meta_ai"],
        canonicalize_cot_leak=quality["canonicalize_cot_leak"],
        canonicalize_role_leak=quality["canonicalize_role_leak"],
        avg_llm_latency_ms=_avg(quality["latencies"]),
        total_tokens=quality["total_tokens"],
        belief_update_count=belief["update_count"],
        belief_final_brier=belief["brier"],
        belief_final_wolf_villager_separation=belief["separation"],
        belief_final_entropy=belief["entropy"],
    )


def _running_row(
    game_id: str,
    events: list["GameEvent"],
    traces: list["AgentDecisionTrace"],
    *,
    status: str,
    fallback_arm: str | None = None,
    fallback_created_at: str | None = None,
    fallback_player_count: int | None = None,
) -> RunningGameRow:
    first_event = events[0] if events else None
    latest_event = events[-1] if events else None
    role_assigned = next((event for event in events if event.event_type == EventType.ROLE_ASSIGNED), None)
    arm = _infer_arm(game_id, traces)
    if arm == "unknown" and fallback_arm:
        arm = _normalize_arm(fallback_arm)
    model_name = _infer_model_name(traces)
    return RunningGameRow(
        game_id=game_id,
        arm=arm,
        model_name=model_name,
        arm_model=_arm_model(arm, model_name),
        status=status,
        created_at=(first_event.created_at if first_event is not None else fallback_created_at),
        latest_event_at=(latest_event.created_at if latest_event is not None else None),
        player_count=_player_count(role_assigned) or fallback_player_count,
        current_round=(latest_event.round if latest_event is not None else None),
        current_phase=_phase_value(latest_event.phase) if latest_event is not None else None,
        event_count=len(events),
        trace_count=len(traces),
        duration_ms=_duration_ms(events),
    )


def _safe_events(event_store: "EventStore", game_id: str) -> list["GameEvent"]:
    try:
        return event_store.list_by_game(game_id)
    except Exception:
        return []


def _safe_traces(
    trace_store: "TraceStore | None",
    game_id: str,
) -> list["AgentDecisionTrace"]:
    if trace_store is None:
        return []
    try:
        return trace_store.list_by_game(game_id)
    except Exception:
        return []


def _has_game_over(events: list["GameEvent"]) -> bool:
    return any(event.event_type == EventType.GAME_OVER for event in events)


def _infer_arm(game_id: str, traces: list["AgentDecisionTrace"]) -> str:
    for trace in traces:
        arm = _normalize_arm(getattr(trace, "agent_version", None))
        if arm != "unknown":
            return arm
    lower = game_id.lower()
    for arm in _KNOWN_ARMS:
        if lower.startswith(f"batch_{arm}_"):
            return arm
    return "unknown"


def _infer_model_name(traces: list["AgentDecisionTrace"]) -> str:
    names = {
        str(getattr(trace, "model_name", None) or "").strip()
        for trace in traces
        if str(getattr(trace, "model_name", None) or "").strip()
    }
    if not names:
        return "unknown"
    return next(iter(names)) if len(names) == 1 else "mixed"


def _arm_model(arm: str, model_name: str) -> str:
    return f"{arm} / {model_name}"


def _normalize_arm(value: Any) -> str:
    text = str(value or "").strip().lower()
    for arm in _KNOWN_ARMS:
        if text.startswith(arm):
            return arm
    return "unknown"


def _trace_quality(traces: list["AgentDecisionTrace"]) -> dict[str, Any]:
    canonicalize = Counter()
    latencies: list[float | None] = []
    total_tokens = 0
    ok_count = 0
    parse_error = 0
    llm_error = 0
    retry_count = 0

    for trace in traces:
        flags = trace.decision_quality_flags or {}
        if flags.get("outcome") == "ok":
            ok_count += 1
        if bool(flags.get("parse_error")):
            parse_error += 1
        if bool(flags.get("llm_error")):
            llm_error += 1
        retry_count += int(flags.get("retry_count", 0) or 0)
        for item in flags.get("canonicalize_triggered") or []:
            canonicalize[str(item)] += 1
        latency = flags.get("llm_latency_ms")
        if isinstance(latency, int | float):
            latencies.append(float(latency))
        usage = flags.get("token_usage")
        if isinstance(usage, dict):
            total_tokens += int(usage.get("total_tokens", 0) or 0)

    return {
        "decision_count": len(traces),
        "ok_count": ok_count,
        "parse_error": parse_error,
        "llm_error": llm_error,
        "retry_count": retry_count,
        "canonicalize_meta_ai": canonicalize["meta_ai"],
        "canonicalize_cot_leak": canonicalize["cot_leak"],
        "canonicalize_role_leak": canonicalize["role_leak"],
        "latencies": latencies,
        "total_tokens": total_tokens,
    }


def _belief_quality(
    game_id: str,
    belief_store: "BeliefStateStore | None",
    replay_truth_store: "ReplayTruthStore | None",
) -> dict[str, float | int | None]:
    histories: list[list["BeliefState"]] = []
    update_count = 0
    if belief_store is not None:
        for idx in range(1, 13):
            history = belief_store.get_history(game_id, f"P{idx}", is_shadow=False)
            if history:
                histories.append(history)
                update_count += len(history)

    if not histories or replay_truth_store is None:
        return {"update_count": update_count, "brier": None, "separation": None, "entropy": None}

    truth = {
        str(player.get("player_id")): str(player.get("camp"))
        for player in replay_truth_store.get_players(game_id)
        if isinstance(player, dict) and player.get("player_id")
    }
    if not truth:
        return {"update_count": update_count, "brier": None, "separation": None, "entropy": None}

    briers: list[float | None] = []
    separations: list[float | None] = []
    entropies: list[float | None] = []
    for history in histories:
        snap = history[-1]
        briers.append(_snapshot_brier(snap, truth))
        separations.append(_snapshot_separation(snap, truth))
        entropies.append(_snapshot_entropy(snap, truth))
    return {
        "update_count": update_count,
        "brier": _avg(briers),
        "separation": _avg(separations),
        "entropy": _avg(entropies),
    }


def _snapshot_brier(snapshot: "BeliefState", truth: dict[str, str]) -> float | None:
    values = []
    for pid, belief in snapshot.beliefs.items():
        if pid == snapshot.agent_id or pid not in truth:
            continue
        target = 1.0 if truth[pid] == "werewolf" else 0.0
        values.append((float(belief.werewolf) - target) ** 2)
    return _avg(values)


def _snapshot_separation(snapshot: "BeliefState", truth: dict[str, str]) -> float | None:
    wolf_probs = []
    villager_probs = []
    for pid, belief in snapshot.beliefs.items():
        if pid == snapshot.agent_id or pid not in truth:
            continue
        if truth[pid] == "werewolf":
            wolf_probs.append(float(belief.werewolf))
        else:
            villager_probs.append(float(belief.werewolf))
    wolf_avg = _avg(wolf_probs)
    villager_avg = _avg(villager_probs)
    if wolf_avg is None or villager_avg is None:
        return None
    return wolf_avg - villager_avg


def _snapshot_entropy(snapshot: "BeliefState", truth: dict[str, str]) -> float | None:
    scores = [
        max(0.0, float(belief.werewolf))
        for pid, belief in snapshot.beliefs.items()
        if pid != snapshot.agent_id and pid in truth
    ]
    total = sum(scores)
    if not scores or total <= 0:
        return None
    probs = [score / total for score in scores if score > 0]
    if len(probs) <= 1:
        return 0.0
    entropy = -sum(prob * math.log(prob) for prob in probs)
    return entropy / math.log(len(scores))


def _winner_from(game_over: "GameEvent | None") -> str | None:
    if game_over is None:
        return None
    winner = game_over.payload.get("winner")
    return winner if isinstance(winner, str) else None


def _player_count(role_assigned: "GameEvent | None") -> int | None:
    if role_assigned is None:
        return None
    value = role_assigned.payload.get("player_count")
    return value if isinstance(value, int) else None


def _phase_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _duration_ms(events: list["GameEvent"]) -> float | None:
    times = [_parse_datetime(event.created_at) for event in events if event.created_at]
    times = [time for time in times if time is not None]
    if len(times) < 2:
        return None
    return (max(times) - min(times)).total_seconds() * 1000


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _avg(values: list[float | int | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return (sum(present) / len(present)) if present else None


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None
