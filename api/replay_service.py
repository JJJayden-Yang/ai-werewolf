"""ReplayData 装配 —— C 的 S2 任务。

把 `EventStore` 的事件流 + （可选）`SessionProvider` 的终局 GameSession
打包成 `ReplayData`。纯函数，不依赖 HTTP，可以脱离 FastAPI 单测。

字段策略（已与 A 对齐 2026-05-23 18:34-18:37 群聊）：
- ``game_id`` / ``players`` / ``timeline`` / ``events``  现阶段填实数据
- ``belief_curves`` / ``deviation_points``  v1 阶段（S8）才填，留 schema 默认空
- ``bad_cases``  S10 PostGameAnalyzer 产，留空
- ``evaluation_summary``  S2 baseline 跑完 100 局才有，留空

A 提示：S2 → S3（9P Contract Freeze）时 Replay 会"整体大变"。本实现
是 S2 临时版，9 人前会重做。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from contracts import Camp, EventType, Phase, PlayerStatus, Role
from contracts.schemas import ReplayData

if TYPE_CHECKING:
    from contracts.schemas import GameEvent, PlayerState
    from game_core.protocols import SessionProvider
    from game_core.types import GameSession
    from stores.event_store import EventStore
    from stores.replay_truth_store import ReplayTruthStore
    from stores.trace_store import TraceStore


class ReplayNotFoundError(Exception):
    """指定 game_id 不存在任何事件、且无法通过 SessionProvider 获取
    GameSession 时抛出。

    端点层会转成 HTTP 404；纯函数调用方可以直接 catch。
    """

    def __init__(self, game_id: str) -> None:
        super().__init__(f"replay not found: game_id={game_id!r}")
        self.game_id = game_id


def assemble_replay(
    game_id: str,
    *,
    event_store: EventStore,
    session_provider: SessionProvider | None = None,
    replay_truth_store: ReplayTruthStore | None = None,
) -> ReplayData:
    """从 EventStore（可选 SessionProvider）装配 ReplayData。

    Args:
        game_id: 局 id（path param）。
        event_store: 必填，提供事件流。
        session_provider: 可选，提供终局 GameSession 以填 players。
            不提供或取不到时，会尝试从赛后事件流完整反推 players；
            事件流无法唯一确定所有身份时才留空。
        replay_truth_store: 可选，提供已持久化的 replay-only 真相快照。
            优先级低于 live session，高于事件流推导。

    Raises:
        ReplayNotFoundError: 既没有事件，session_provider 也没拿到
            session（或没提供）。
    """
    events = event_store.list_by_game(game_id)
    session = _try_get_session(session_provider, game_id)

    if not events and session is None:
        raise ReplayNotFoundError(game_id)

    players = (
        _extract_players(session)
        or _extract_players_from_truth_store(replay_truth_store, game_id)
        or _derive_players_from_events(events)
    )
    timeline = _build_timeline(events)

    return ReplayData(
        game_id=game_id,
        players=players,
        timeline=timeline,
        events=events,
        # v1 / S10 / S2 baseline 字段：schema 默认值（[] / {}）
    )


def list_replay_summaries(
    event_store: EventStore,
    *,
    trace_store: TraceStore | None = None,
    mode_overrides: dict[str, str] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """从 EventStore 枚举历史回放摘要。

    分页加载：只读 [offset, offset+limit) 的局，避免全量读盘 OOM。
    """
    list_game_ids = getattr(event_store, "list_game_ids", None)
    if not callable(list_game_ids):
        return []

    all_ids = list_game_ids()
    all_ids = _sort_game_ids_by_mtime(all_ids, event_store)
    page_ids = all_ids[offset : offset + limit]

    summaries: list[dict[str, Any]] = []
    for game_id in page_ids:
        events = event_store.list_by_game(game_id)
        if not events:
            continue
        summaries.append(
            _summarize_replay(
                game_id,
                events,
                trace_store=trace_store,
                mode_override=(mode_overrides or {}).get(game_id),
            )
        )
    return sorted(summaries, key=lambda item: item.get("createdAt") or "", reverse=True)


def _sort_game_ids_by_mtime(game_ids: list[str], event_store: object) -> list[str]:
    """按 event 文件 mtime 倒序排列；无法获取 mtime 时退化为字母逆序。"""
    root_dir = getattr(event_store, "root_dir", None)
    if not isinstance(root_dir, Path):
        return sorted(game_ids, reverse=True)

    def _mtime(gid: str) -> float:
        try:
            return (root_dir / f"{gid}.jsonl").stat().st_mtime
        except OSError:
            return 0.0

    return sorted(game_ids, key=_mtime, reverse=True)


# ---- 内部辅助 ----


def _try_get_session(
    provider: SessionProvider | None, game_id: str
) -> GameSession | None:
    """尽量取 session；任何异常都视为"拿不到"。

    SessionProvider Protocol 没明确规定 not_found 抛什么异常，所以
    保守 catch Exception。日后接入真实 GameEngine 时若有明确的
    not_found 异常类，再窄化。
    """
    if provider is None:
        return None
    try:
        return provider.get_session(game_id)
    except Exception:  # noqa: BLE001
        return None


def _extract_players(session: GameSession | None) -> list[dict[str, Any]]:
    """从 GameSession.truth_state.players 提取面向 Replay 的玩家信息。

    Replay 是赛后回放，可以暴露真实 role / camp（与 v0 Context 不注入
    belief 是两码事，后者是给 Agent 看的）。但仍然只取已 model 化的
    public 字段，不动 Engine 内部运行时字段。
    """
    if session is None:
        return []
    out: list[dict[str, Any]] = []
    for pid, ps in session.truth_state.players.items():
        out.append(_player_to_dict(pid, ps))
    return out


def _player_to_dict(pid: str, ps: PlayerState) -> dict[str, Any]:
    return {
        "player_id": ps.player_id or pid,
        "role": ps.role.value,
        "camp": ps.camp.value if ps.camp else None,
        "status": ps.status.value,
        "public_claim": ps.public_claim,
        "vote_weight": ps.vote_weight,
    }


def _extract_players_from_truth_store(
    replay_truth_store: ReplayTruthStore | None,
    game_id: str,
) -> list[dict[str, Any]]:
    if replay_truth_store is None:
        return []
    try:
        return replay_truth_store.get_players(game_id)
    except Exception:  # noqa: BLE001 - replay truth failure should fall back to events
        return []


def _derive_players_from_events(events: list[GameEvent]) -> list[dict[str, Any]]:
    """从赛后事件流反推 replay players。

    这里只在能完整、唯一推出每个座位身份时返回结果；遇到 9 人局
    "猎人从未开枪且剩余 1 猎 + 多民" 这类不可区分情况，返回空列表，
    避免把猜测写成上帝视角真相。
    """
    player_ids = _derive_player_ids(events)
    if not player_ids:
        return []

    role_by_player = _derive_role_map_from_events(events)
    role_counts = _derive_role_counts(events)
    if role_counts:
        role_by_player = _fill_unambiguous_remaining_roles(
            player_ids,
            role_by_player,
            role_counts,
        )

    if any(pid not in role_by_player for pid in player_ids):
        return []

    dead_players = {
        event.target
        for event in events
        if event.event_type == EventType.DEATH_CONFIRMED and event.target is not None
    }
    players: list[dict[str, Any]] = []
    for pid in player_ids:
        role = role_by_player[pid]
        status = PlayerStatus.DEAD if pid in dead_players else PlayerStatus.ALIVE
        players.append(
            {
                "player_id": pid,
                "role": role.value,
                "camp": _camp_for_role(role).value,
                "status": status.value,
                "public_claim": None,
                "vote_weight": 1.0,
            }
        )
    return players


def _derive_role_map_from_events(events: list[GameEvent]) -> dict[str, Role]:
    role_by_player: dict[str, Role] = {}
    for event in events:
        teammates = event.payload.get("teammates")
        if isinstance(teammates, list):
            for teammate in teammates:
                if isinstance(teammate, str):
                    role_by_player[teammate] = Role.WEREWOLF

        if event.actor is None:
            continue

        if event.phase == Phase.NIGHT_WEREWOLF or event.event_type == EventType.WOLF_NOMINATION:
            role_by_player[event.actor] = Role.WEREWOLF
        elif event.phase == Phase.NIGHT_SEER or event.event_type == EventType.SEER_CHECK_RESULT:
            role_by_player[event.actor] = Role.SEER
        elif event.phase == Phase.NIGHT_WITCH or event.event_type in {
            EventType.WITCH_SAVE,
            EventType.WITCH_POISON,
        }:
            role_by_player[event.actor] = Role.WITCH
        elif event.phase == Phase.HUNTER_SHOOT or event.event_type == EventType.HUNTER_SHOT:
            role_by_player[event.actor] = Role.HUNTER
    return role_by_player


def _derive_player_ids(events: list[GameEvent]) -> list[str]:
    role_assigned = next(
        (event for event in events if event.event_type == EventType.ROLE_ASSIGNED),
        None,
    )
    player_count = role_assigned.payload.get("player_count") if role_assigned else None
    if isinstance(player_count, int) and player_count > 0:
        return [f"P{i}" for i in range(1, player_count + 1)]

    seen: set[str] = set()
    for event in events:
        for value in (event.actor, event.target):
            if isinstance(value, str) and value.startswith("P"):
                seen.add(value)
        teammates = event.payload.get("teammates")
        if isinstance(teammates, list):
            seen.update(
                value
                for value in teammates
                if isinstance(value, str) and value.startswith("P")
            )
    return sorted(seen, key=_player_sort_key)


def _derive_role_counts(events: list[GameEvent]) -> dict[Role, int]:
    role_assigned = next(
        (event for event in events if event.event_type == EventType.ROLE_ASSIGNED),
        None,
    )
    raw_counts = role_assigned.payload.get("role_counts") if role_assigned else None
    if not isinstance(raw_counts, dict):
        return {}

    counts: dict[Role, int] = {}
    for role in Role:
        value = raw_counts.get(role.value)
        if isinstance(value, int):
            counts[role] = value
    return counts


def _fill_unambiguous_remaining_roles(
    player_ids: list[str],
    known_roles: dict[str, Role],
    role_counts: dict[Role, int],
) -> dict[str, Role]:
    out = dict(known_roles)
    remaining_counts = dict(role_counts)
    for role in known_roles.values():
        if role in remaining_counts:
            remaining_counts[role] -= 1

    if any(count < 0 for count in remaining_counts.values()):
        return out

    unknown_players = [pid for pid in player_ids if pid not in out]
    positive_remaining = {
        role: count for role, count in remaining_counts.items() if count > 0
    }
    if len(positive_remaining) == 1:
        role, count = next(iter(positive_remaining.items()))
        if count == len(unknown_players):
            for pid in unknown_players:
                out[pid] = role
    return out


def _camp_for_role(role: Role) -> Camp:
    return Camp.WEREWOLF if role == Role.WEREWOLF else Camp.VILLAGER


def _player_sort_key(player_id: str) -> tuple[int, str]:
    suffix = player_id[1:]
    return (int(suffix), player_id) if suffix.isdigit() else (10_000, player_id)


def _build_timeline(events: list[GameEvent]) -> list[dict[str, Any]]:
    """按事件顺序生成一个扁平的 timeline 视图。

    每条 entry 含 round / phase / event_type / actor / target / event_id /
    visibility，方便前端按时间线渲染。Phase 2.5 阶段做 debug 版，不做
    高度聚合（按 round/phase 分组的版本等 S3 或 UI 真上时再加）。
    """
    timeline: list[dict[str, Any]] = []
    for event in events:
        timeline.append(
            {
                "event_id": event.event_id,
                "round": event.round,
                "phase": event.phase.value,
                "event_type": event.event_type.value,
                "actor": event.actor,
                "target": event.target,
                "visibility": event.visibility.value,
            }
        )
    return timeline


def _summarize_replay(
    game_id: str,
    events: list[GameEvent],
    *,
    trace_store: TraceStore | None = None,
    mode_override: str | None = None,
) -> dict[str, Any]:
    first = events[0]
    role_assigned = next((event for event in events if event.event_type == EventType.ROLE_ASSIGNED), None)
    game_over = next((event for event in reversed(events) if event.event_type == EventType.GAME_OVER), None)
    player_count = role_assigned.payload.get("player_count") if role_assigned else None
    winner = game_over.payload.get("winner") if game_over else None
    if isinstance(winner, Camp):
        winner = winner.value
    return {
        "gameId": game_id,
        "createdAt": first.created_at,
        "playerCount": player_count if isinstance(player_count, int) else None,
        "mode": derive_replay_mode(game_id, trace_store=trace_store, mode_override=mode_override),
        "status": "completed" if game_over else "running",
        "winner": winner if isinstance(winner, str) else None,
        "rounds": max(event.round for event in events),
        "durationSec": None,
        "tags": derive_replay_tags(events),
    }


def derive_replay_tags(events: list[GameEvent]) -> list[str]:
    """从真实事件流派生回放筛选标签。

    输出顺序固定，且同一标签一局只出现一次，避免前端筛选项抖动。
    """
    seen = {event.event_type for event in events}
    tags: list[str] = []
    if EventType.TIE_DETECTED in seen:
        tags.append("平票")
    if EventType.NO_EXILE_DUE_TO_SECOND_TIE in seen:
        tags.append("二次平票")
    if any(
        event.event_type == EventType.HUNTER_SHOT
        and event.target is not None
        and event.payload.get("pass") is not True
        for event in events
    ):
        tags.append("猎人开枪")
    if EventType.WITCH_SAVE in seen:
        tags.append("女巫救人")
    if EventType.WITCH_POISON in seen:
        tags.append("女巫毒人")
    return tags


def derive_replay_mode(
    game_id: str,
    *,
    trace_store: TraceStore | None = None,
    mode_override: str | None = None,
) -> str:
    """把回放模式收敛为前端筛选用标签：Mock / LLM v0 / LLM v1 / LLM v2。"""
    if mode_override:
        return mode_override
    batch_mode = _derive_batch_arm_mode(game_id)
    if batch_mode is not None:
        return batch_mode
    if trace_store is None:
        return "Mock"
    try:
        traces = trace_store.list_by_game(game_id)
    except Exception:  # noqa: BLE001 - summary 列表不应被 trace 后端故障打穿
        return "Mock"
    versions = {str(trace.agent_version).strip().lower() for trace in traces}
    if any(version.startswith("v2") for version in versions):
        return "LLM v2"
    if any(version.startswith("v1") for version in versions):
        return "LLM v1"
    if any(version.startswith("v0") for version in versions):
        return "LLM v0"
    return "Mock"


def _derive_batch_arm_mode(game_id: str) -> str | None:
    normalized = game_id.strip().lower()
    if normalized.startswith("batch_v2_"):
        return "LLM v2"
    if normalized.startswith("batch_v1_"):
        return "LLM v1"
    if normalized.startswith("batch_v0_"):
        return "LLM v0"
    return None
