"""实时观战对局 HTTP 服务。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agent_runtime import HumanInputChannel
from api.game_builder import MissingCredentialsError, build_game
from api.game_registry import GameRegistry, snapshot_record
from api.runtime import (
    get_belief_store,
    get_event_store,
    get_replay_truth_store,
    get_trace_store,
)
from api.soul_service import SoulLibrary
from stores.replay_truth_store import build_player_snapshots
from supervisor import GameRunError
from contracts import EventType, Role, Visibility
from context.visibility_rules import VisibilityRuleSpec

if TYPE_CHECKING:
    from stores.belief_state_store import BeliefStateStore
    from stores.event_store import EventStore
    from stores.replay_truth_store import ReplayTruthStore
    from stores.trace_store import TraceStore

router = APIRouter()
registry = GameRegistry()


def get_game_registry() -> GameRegistry:
    """FastAPI 依赖：返回进程内共享的 GameRegistry 单例。

    端点通过 Depends 拿 registry（而非函数内 import 全局），测试才能用
    ``app.dependency_overrides`` 注入隔离实例，避免跨用例污染全局单例。
    """
    return registry


class GameStartRequest(BaseModel):
    player_count: Literal[6, 9]
    arm: Literal["v0", "v1", "v2"]
    seed: int = Field(ge=0)
    temperature: float = Field(ge=0.0, le=1.0)
    mode: Literal["mock", "llm"] = "llm"
    model_flavor: Literal["PRO", "CODE", "DEEPSEEK"] = "PRO"
    max_rounds: int | None = Field(default=None, ge=1, le=20)
    seat_souls: dict[str, str] | None = None
    human_seat: str | None = None
    human_role: Role | None = None


class GameStartResponse(BaseModel):
    game_id: str
    status: Literal["running"]


class HumanActionRequest(BaseModel):
    player_id: str
    action_type: str
    target: str | None = None
    public_message: str | None = None
    role_claim: Role | None = None
    claim_result: dict | None = None
    reason_summary: str | None = None
    confidence: float | None = None
    metadata: dict | None = None


@router.post("/games", response_model=GameStartResponse)
async def start_game(
    request: GameStartRequest,
    event_store: "EventStore" = Depends(get_event_store),
    trace_store: "TraceStore" = Depends(get_trace_store),
    belief_store: "BeliefStateStore" = Depends(get_belief_store),
    replay_truth_store: "ReplayTruthStore" = Depends(get_replay_truth_store),
) -> GameStartResponse:
    try:
        _validate_human_player(request)
        _validate_seat_souls(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    human_channel = HumanInputChannel() if request.human_seat else None
    try:
        built, game_id = build_game(
            request.player_count,
            request.arm,
            request.seed,
            request.temperature,
            mode=request.mode,
            model_flavor=request.model_flavor,
            max_rounds=request.max_rounds,
            seat_souls=request.seat_souls if request.mode == "llm" else None,
            event_store=event_store,
            trace_store=trace_store,
            belief_store=belief_store,
            human_seat=request.human_seat,
            human_role=request.human_role,
            human_channel=human_channel,
        )
    except MissingCredentialsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    role_map = _extract_role_map(built, game_id)
    players = _extract_replay_players(built, game_id)
    if players:
        replay_truth_store.save_players(game_id, players)
    registry.create(
        game_id=game_id,
        player_count=request.player_count,
        arm=request.arm,
        mode=request.mode,
        event_store=built.stores.event_store,
        role_map=role_map,
        human_seat=request.human_seat,
        human_channel=human_channel,
        engine=built.engine,
    )
    task = asyncio.create_task(_run_game(game_id, built, replay_truth_store))
    registry.set_task(game_id, task)
    return GameStartResponse(game_id=game_id, status="running")


@router.get("/games")
def list_games() -> dict:
    return {"games": [snapshot_record(record) for record in registry.list()]}


@router.get("/games/{game_id}/status")
def get_game_status(game_id: str) -> dict:
    record = _get_record_or_404(game_id)
    snapshot = snapshot_record(record)
    return {
        "game_id": snapshot["game_id"],
        "status": snapshot["status"],
        "current_round": snapshot["current_round"],
        "current_phase": snapshot["current_phase"],
        "current_actor": snapshot["current_actor"],
        "winner": snapshot["winner"],
        "error": snapshot["error"],
        "role_map": snapshot["role_map"],
    }


@router.get("/games/{game_id}/events")
def get_game_events(game_id: str, since: int = Query(0, ge=0)) -> dict:
    record = _get_record_or_404(game_id)
    events = record.event_store.list_by_game(game_id)
    sliced = events[since:]
    return {
        "events": [event.model_dump(mode="json") for event in sliced],
        "next_cursor": len(events),
        "status": "finished" if record.status in {"finished", "error"} else "running",
    }


@router.get("/games/{game_id}/pending")
def get_human_pending(game_id: str, player_id: str = Query(...)) -> dict:
    record = _get_record_or_404(game_id)
    _ensure_human_player(record, player_id)
    context = record.human_channel.pending_context if record.human_channel else None
    if context is None:
        return {"pending": False}
    return {"pending": True, "context": context}


@router.get("/games/{game_id}/player-events")
def get_human_player_events(
    game_id: str,
    player_id: str = Query(...),
    since: int = Query(0, ge=0),
) -> dict:
    record = _get_record_or_404(game_id)
    _ensure_human_player(record, player_id)
    if record.engine is None:
        raise HTTPException(status_code=404, detail="game engine is unavailable")
    try:
        session = record.engine.get_session(game_id)
    except Exception as exc:  # noqa: BLE001 - registry/engine mismatch should surface as 404
        raise HTTPException(status_code=404, detail=f"game session not found: {game_id}") from exc

    raw_events = record.event_store.list_by_game(game_id)
    visibility = VisibilityRuleSpec()
    events_since_cursor = raw_events[since:]
    public_events = visibility.visible_public_events(events_since_cursor, session, player_id)
    public_by_id = {event.event_id: event for event in public_events}
    private_events = visibility.visible_private_events(raw_events[since:], session, player_id)
    visible_players = visibility.visible_players(session, player_id)
    role = visibility.observer_role(session, player_id)
    visible_event_payloads = []
    for event in events_since_cursor:
        payload = _event_to_player_visible_event(game_id, event, public_by_id)
        if payload is not None:
            visible_event_payloads.append(payload)
    return {
        "game_id": game_id,
        "player_id": player_id,
        "role": role.value if role else None,
        "events": visible_event_payloads,
        "private_events": [event.model_dump(mode="json") for event in private_events],
        "visible_players": [player.model_dump(mode="json") for player in visible_players],
        "next_cursor": len(raw_events),
    }


@router.post("/games/{game_id}/action")
async def submit_human_action(game_id: str, request: HumanActionRequest) -> dict:
    record = _get_record_or_404(game_id)
    _ensure_human_player(record, request.player_id)
    assert record.human_channel is not None
    payload = request.model_dump(exclude_none=True)
    payload.pop("player_id", None)
    await record.human_channel.submit(payload)
    return {"accepted": True}


def _public_event_to_player_event(game_id: str, event) -> dict:
    payload = {
        "public_message": event.public_message,
        "role_claim": event.role_claim.value if event.role_claim else None,
        "claim_result": event.claim_result.model_dump(mode="json") if event.claim_result else None,
        "summary": event.summary,
    }
    return {
        "event_id": event.event_id,
        "game_id": game_id,
        "round": event.round,
        "phase": event.phase.value if hasattr(event.phase, "value") else event.phase,
        "event_type": event.event_type.value if hasattr(event.event_type, "value") else event.event_type,
        "actor": event.actor,
        "target": event.target,
        "visibility": "public",
        "payload": {key: value for key, value in payload.items() if value is not None},
        "created_at": None,
    }


def _event_to_player_visible_event(game_id: str, event, public_by_id: dict[str, object]) -> dict | None:
    if event.event_type == EventType.GAME_OVER and event.visibility == Visibility.PUBLIC:
        return {
            "event_id": event.event_id,
            "game_id": game_id,
            "round": event.round,
            "phase": event.phase.value if hasattr(event.phase, "value") else event.phase,
            "event_type": EventType.GAME_OVER.value,
            "actor": None,
            "target": None,
            "visibility": "public",
            "payload": {
                key: value
                for key, value in {
                    "winner": event.payload.get("winner"),
                    "reason": event.payload.get("reason"),
                }.items()
                if value is not None
            },
            "created_at": event.created_at,
        }
    # DAY_ANNOUNCEMENT / DEATH_CONFIRMED 携带死亡信息（deaths / death_cause），
    # 但 AI 可见的 PublicEvent 故意把这些字段剥掉（防止 AI context 泄漏谁被刀/被毒）。
    # 人类玩家端不能复用那份被剥过的 payload，否则前端永远显示「平安夜」。
    # 这里直接从原始 GameEvent payload 重建，保留死亡名单。
    if event.event_id in public_by_id and event.event_type in (
        EventType.DAY_ANNOUNCEMENT,
        EventType.DEATH_CONFIRMED,
    ):
        raw_payload = event.payload or {}
        if event.event_type == EventType.DAY_ANNOUNCEMENT:
            payload = {"deaths": raw_payload.get("deaths", [])}
        else:
            payload = {
                key: value
                for key, value in {
                    "death_cause": raw_payload.get("death_cause"),
                    "hunter_can_shoot": raw_payload.get("hunter_can_shoot"),
                }.items()
                if value is not None
            }
        return {
            "event_id": event.event_id,
            "game_id": game_id,
            "round": event.round,
            "phase": event.phase.value if hasattr(event.phase, "value") else event.phase,
            "event_type": event.event_type.value
            if hasattr(event.event_type, "value")
            else event.event_type,
            "actor": event.actor,
            "target": event.target or raw_payload.get("target"),
            "visibility": "public",
            "payload": payload,
            "created_at": event.created_at,
        }
    public_event = public_by_id.get(event.event_id)
    if public_event is not None:
        return _public_event_to_player_event(game_id, public_event)
    if event.event_type == EventType.PHASE_STARTED and event.visibility == Visibility.PUBLIC:
        return {
            "event_id": event.event_id,
            "game_id": game_id,
            "round": event.round,
            "phase": event.phase.value if hasattr(event.phase, "value") else event.phase,
            "event_type": EventType.PHASE_STARTED.value,
            "actor": None,
            "target": None,
            "visibility": "public",
            "payload": {},
            "created_at": event.created_at,
        }
    return None


async def _run_game(game_id: str, built, replay_truth_store: "ReplayTruthStore") -> None:
    registry.update_status(game_id, "running")
    try:
        await built.supervisor.run_game(game_id)
    except GameRunError as exc:
        _save_replay_truth_snapshot(game_id, built, replay_truth_store)
        registry.update_status(game_id, "error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - API task must surface summary, not crash loop
        _save_replay_truth_snapshot(game_id, built, replay_truth_store)
        registry.update_status(game_id, "error", error=f"{type(exc).__name__}: {exc}")
    else:
        _save_replay_truth_snapshot(game_id, built, replay_truth_store)
        registry.update_status(game_id, "finished")


def _extract_role_map(built, game_id: str) -> dict[str, str] | None:
    """从引擎 TruthState 提取 {player_id: role} 供上帝视角观战展示。

    红线说明：信息隔离只约束 Agent 输入（AgentContext）。这里读真实身份是为了观战端口
    的 god-view，不进任何 AgentContext，故合规。取不到时返回 None，前端回退到隐藏身份。
    """
    try:
        session = built.engine.get_session(game_id)
        players = session.truth_state.players
    except Exception:  # noqa: BLE001 - 提取失败不应阻断开局，god-view 退化为隐藏
        return None
    role_map: dict[str, str] = {}
    for pid, state in players.items():
        role = getattr(state, "role", None)
        role_map[pid] = getattr(role, "value", str(role)) if role is not None else "unknown"
    return role_map or None


def _extract_replay_players(built, game_id: str) -> list[dict]:
    try:
        session = built.engine.get_session(game_id)
        players = session.truth_state.players
    except Exception:  # noqa: BLE001 - truth snapshot failure should not block game start
        return []
    return build_player_snapshots(players)


def _save_replay_truth_snapshot(game_id: str, built, replay_truth_store: "ReplayTruthStore") -> None:
    players = _extract_replay_players(built, game_id)
    if not players:
        return
    try:
        replay_truth_store.save_players(game_id, players)
    except Exception:  # noqa: BLE001 - replay snapshot failure should not alter game outcome
        return


def _get_record_or_404(game_id: str):
    record = registry.get(game_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"game not found: {game_id!r}")
    return record


def _ensure_human_player(record, player_id: str) -> None:
    if record.human_channel is None or record.human_seat is None:
        raise HTTPException(status_code=404, detail="game has no human player")
    if player_id != record.human_seat:
        raise HTTPException(status_code=403, detail="player_id does not match human seat")


def _validate_human_player(request: GameStartRequest) -> None:
    if request.human_seat is None and request.human_role is None:
        return
    if not request.human_seat or request.human_role is None:
        raise ValueError("human_seat and human_role must be provided together")
    expected = {f"P{i}" for i in range(1, request.player_count + 1)}
    if request.human_seat not in expected:
        raise ValueError(f"human_seat must be one of {sorted(expected)}")
    role_counts = request_role_counts(request)
    if role_counts.get(request.human_role, 0) <= 0:
        raise ValueError(f"human_role {request.human_role.value!r} is not available in this game")


def _validate_seat_souls(request: GameStartRequest) -> None:
    if request.mode != "llm":
        return
    if not request.seat_souls:
        raise ValueError("seat_souls is required for llm mode")
    expected = {f"P{i}" for i in range(1, request.player_count + 1)}
    if request.human_seat:
        expected.remove(request.human_seat)
    actual = set(request.seat_souls)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"seat_souls must cover seats {sorted(expected)}; missing={missing} extra={extra}")
    library = SoulLibrary()
    known_souls = {record.id for record in library.list()}
    unknown = sorted(set(request.seat_souls.values()) - known_souls)
    if unknown:
        raise ValueError(f"unknown soul ids in seat_souls: {unknown}")


def request_role_counts(request: GameStartRequest) -> dict[Role, int]:
    if request.player_count == 6:
        return {
            Role.WEREWOLF: 2,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.HUNTER: 0,
            Role.VILLAGER: 2,
        }
    return {
        Role.WEREWOLF: 3,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.VILLAGER: 3,
    }
