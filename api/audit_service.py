"""审计服务 —— 动态扫描 data 目录并返回对局摘要与详情。

从 EventStore + TraceStore 枚举历史对局：
1. `list_audit_runs()` — 返回对局列表摘要
2. `get_audit_run()` — 返回单对局详细审计数据（events + traces + metadata）
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from contracts import EventType

from api.replay_service import derive_replay_tags

if TYPE_CHECKING:
    from stores.belief_state_store import BeliefStateStore
    from stores.event_store import EventStore
    from stores.trace_store import TraceStore


def list_audit_runs(
    event_store: EventStore,
    trace_store: TraceStore | None = None,
    data_dir: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """从 EventStore 和 TraceStore 枚举审计对局摘要。

    Args:
        event_store: 事件存储后端（JSONL 或内存）
        trace_store: 可选的 trace 存储后端
        data_dir: 可选的数据目录（用于返回路径信息）

    Returns:
        按创建时间逆序的对局摘要列表，包含以下字段：
        - gameId: 对局 ID
        - createdAt: 创建时间 (ISO 8601)
        - playerCount: 玩家数
        - strategy: 策略描述 (v0/v1 + llm/mock)
        - winner: 赢家 (werewolves/villagers/None)
        - rounds: 进行的轮数
        - eventCount: 事件总数
        - traceCount: trace 总数
        - hasAuditPage: 是否有审计页（当前总是 false，待实现）
        - eventPath: event 文件路径
        - tracePath: trace 文件路径（如果存在）
    """
    # 获取所有 game_id，按 mtime 倒序后分页，避免全量读盘 OOM
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

        # 获取该对局的 traces
        trace_count = 0
        trace_path = None
        first_trace_agent_version: str | None = None
        if trace_store is not None:
            try:
                traces = trace_store.list_by_game(game_id)
                trace_count = len(traces)
                if trace_count > 0:
                    first_trace_agent_version = getattr(traces[0], "agent_version", None)
                    if data_dir:
                        trace_path = str(Path(data_dir) / "traces" / f"{game_id}.jsonl")
            except Exception:
                pass

        # 提取元数据
        summary = _summarize_audit_run(
            game_id,
            events,
            trace_count=trace_count,
            trace_path=trace_path,
            data_dir=data_dir,
            first_trace_agent_version=first_trace_agent_version,
        )
        summaries.append(summary)

    # 按创建时间逆序排列
    return sorted(summaries, key=lambda x: x.get("createdAt") or "", reverse=True)


def _summarize_audit_run(
    game_id: str,
    events: list,
    *,
    trace_count: int = 0,
    trace_path: str | None = None,
    data_dir: str | None = None,
    first_trace_agent_version: str | None = None,
) -> dict[str, Any]:
    """从事件流提取单个对局的审计摘要。"""
    first = events[0]
    role_assigned = next(
        (event for event in events if event.event_type == EventType.ROLE_ASSIGNED),
        None
    )
    game_over = next(
        (event for event in reversed(events) if event.event_type == EventType.GAME_OVER),
        None
    )

    player_count = None
    if role_assigned:
        player_count = role_assigned.payload.get("player_count")

    winner = None
    if game_over:
        w = game_over.payload.get("winner")
        if isinstance(w, str):
            winner = w

    strategy = _infer_strategy(game_id, trace_count, first_trace_agent_version, data_dir)

    # 构造 event 文件路径
    event_path = ""
    if data_dir:
        event_path = str(Path(data_dir) / "events" / f"{game_id}.jsonl")

    return {
        "gameId": game_id,
        "createdAt": str(first.created_at),
        "playerCount": player_count if isinstance(player_count, int) else None,
        "strategy": strategy,
        "winner": winner,
        "rounds": max((event.round for event in events), default=0),
        "tags": derive_replay_tags(events),
        "eventCount": len(events),
        "traceCount": trace_count,
        "hasAuditPage": trace_count > 0,  # 只有有 trace 数据的对局才能打开审计页
        "eventPath": event_path,
        "tracePath": trace_path,
    }


def get_audit_run(
    game_id: str,
    event_store: EventStore,
    trace_store: TraceStore | None = None,
    belief_store: "BeliefStateStore | None" = None,
) -> dict[str, Any] | None:
    """获取单个对局的完整审计数据（事件 + trace + belief + 统计）。

    Args:
        game_id: 对局 ID
        event_store: 事件存储
        trace_store: 可选的 trace 存储
        belief_store: 可选的 belief 存储

    Returns:
        RunAuditData 结构（供前端审计页展示）或 None（对局不存在）
    """
    events = event_store.list_by_game(game_id)
    if not events:
        return None

    # 收集 traces
    traces: list[dict[str, Any]] = []
    if trace_store is not None:
        try:
            raw_traces = trace_store.list_by_game(game_id)
            traces = [_serialize_trace(t) for t in raw_traces]
        except Exception:
            pass

    # 收集 beliefs
    beliefs: dict[str, dict[str, Any]] = {}
    if belief_store is not None:
        try:
            beliefs = _extract_beliefs(game_id, belief_store)
        except Exception:
            pass

    # 序列化 events
    serialized_events = [_serialize_event(e) for e in events]

    # 提取摘要
    summary = _extract_summary(game_id, events)

    # 计算阶段顺序和统计
    phase_order = _compute_phase_order(serialized_events)
    phase_counts = _compute_phase_counts(serialized_events, traces, phase_order)

    return {
        "summary": summary,
        "events": serialized_events,
        "traces": traces,
        "beliefs": beliefs,
        "phaseOrder": phase_order,
        "phaseCounts": phase_counts,
    }


def _serialize_event(event: Any) -> dict[str, Any]:
    """从 GameEvent 对象序列化为前端格式。"""
    # 处理枚举值转换为字符串
    def _enum_to_str(val: Any) -> str:
        if hasattr(val, 'value'):  # 枚举对象
            return val.value
        return str(val)

    return {
        "event_id": event.event_id,
        "game_id": event.game_id,
        "round": event.round,
        "phase": _enum_to_str(event.phase),
        "event_type": _enum_to_str(event.event_type),
        "actor": event.actor,
        "target": event.target,
        "visibility": event.visibility if hasattr(event, "visibility") else "public",
        "payload": event.payload,
        "created_at": str(event.created_at),
    }


def _serialize_trace(trace: Any) -> dict[str, Any]:
    """从 AgentDecisionTrace 对象序列化为前端格式。"""
    return {
        "trace_id": trace.trace_id,
        "game_id": trace.game_id,
        "round": trace.round,
        "phase": trace.phase,
        "agent_id": trace.agent_id,
        "role": getattr(trace, "role", None),
        "agent_version": getattr(trace, "agent_version", None),
        "prompt_version_id": getattr(trace, "prompt_version_id", None),
        "model_name": getattr(trace, "model_name", None),
        "input_summary": trace.input_summary if hasattr(trace, "input_summary") else {},
        "decision_output": trace.decision_output if hasattr(trace, "decision_output") else {},
        "decision_quality_flags": getattr(trace, "decision_quality_flags", {}),
    }


def _extract_summary(game_id: str, events: list) -> dict[str, Any]:
    """从事件流提取对局摘要。"""
    role_assigned = next(
        (e for e in events if e.event_type == EventType.ROLE_ASSIGNED),
        None
    )
    game_over = next(
        (e for e in reversed(events) if e.event_type == EventType.GAME_OVER),
        None
    )

    player_count = None
    if role_assigned:
        player_count = role_assigned.payload.get("player_count")

    winner = None
    if game_over:
        w = game_over.payload.get("winner")
        if isinstance(w, str):
            winner = w

    max_round = max((e.round for e in events), default=0)

    return {
        "game_id": game_id,
        "winner": winner,
        "rounds": max_round,
        "player_count": player_count,
        "event_count": len(events),
        "agent_stats": {"ok": len(events)},  # 简化版本，先返回全部
    }


def _compute_phase_order(events: list[dict[str, Any]]) -> list[str]:
    """根据事件流推断阶段出现顺序。"""
    seen: dict[str, int] = {}
    for event in events:
        phase = event.get("phase")
        if phase:
            # 将枚举转换为字符串（JSON 序列化用）
            phase_str = str(phase) if hasattr(phase, 'value') else str(phase)
            # 如果是 "Phase.NIGHT_WEREWOLF" 格式，提取 NIGHT_WEREWOLF
            if phase_str.startswith("Phase."):
                phase_str = phase_str[6:]  # 去掉 "Phase." 前缀
            if phase_str not in seen:
                seen[phase_str] = len(seen)
    return sorted(seen.keys(), key=lambda p: seen[p])


def _compute_phase_counts(
    events: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    phase_order: list[str],
) -> dict[str, dict[str, Any]]:
    """计算每个阶段的统计和关键信息（事件数、trace 数、涉及角色、主要事件类型）。"""
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "events": 0,
            "traces": 0,
            "actors": set(),
            "event_types": set(),
        }
    )

    # 处理枚举值转换为字符串的辅助函数
    def _enum_to_str(val: Any) -> str:
        if hasattr(val, 'value'):  # 枚举对象
            return val.value
        return str(val)

    # 从事件中提取信息
    for event in events:
        phase = event.get("phase")
        if phase:
            phase_str = _enum_to_str(phase)
            stats[phase_str]["events"] += 1

            # 收集涉及的角色
            actor = event.get("actor")
            if actor:
                stats[phase_str]["actors"].add(actor)

            # 收集事件类型
            event_type = event.get("event_type")
            if event_type:
                stats[phase_str]["event_types"].add(_enum_to_str(event_type))

    # 从 trace 中统计和提取 agent_id
    for trace in traces:
        phase = trace.get("phase")
        if phase:
            phase_str = _enum_to_str(phase)
            stats[phase_str]["traces"] += 1
            agent_id = trace.get("agent_id")
            if agent_id:
                stats[phase_str]["actors"].add(agent_id)

    # 转换为前端格式（set → list）
    result = {}
    for phase in phase_order:
        phase_stats = stats.get(phase, {
            "events": 0,
            "traces": 0,
            "actors": set(),
            "event_types": set(),
        })
        result[phase] = {
            "events": phase_stats["events"],
            "traces": phase_stats["traces"],
            "actors": sorted(list(phase_stats["actors"])),  # 排序便于读取
            "event_types": sorted(list(phase_stats["event_types"])),
        }

    return result


def _extract_beliefs(game_id: str, belief_store: "BeliefStateStore") -> dict[str, dict[str, Any]]:
    """提取对局的所有 belief 数据，按 agent_id 组织，包含完整历史。

    Returns:
        {
            "agent_id": {
                "is_shadow": bool,
                "history": [
                    {round, phase, beliefs, ...},  # 时间顺序
                    ...
                ],
                "update_count": int
            }
        }
    """
    result: dict[str, dict[str, Any]] = {}

    # 枚举所有 agent 的 belief（通过扫描 real 和 shadow lane）
    agent_ids = _discover_agent_ids_in_beliefs(game_id, belief_store)

    for agent_id in agent_ids:
        # 获取 real lane 的完整历史
        real_history = belief_store.get_history(game_id, agent_id, is_shadow=False)
        if real_history:
            result[f"{agent_id}_real"] = {
                "agent_id": agent_id,
                "is_shadow": False,
                "history": [_serialize_belief_state(bs) for bs in real_history],
                "update_count": len(real_history),
            }

        # 获取 shadow lane 的完整历史
        shadow_history = belief_store.get_history(game_id, agent_id, is_shadow=True)
        if shadow_history:
            result[f"{agent_id}_shadow"] = {
                "agent_id": agent_id,
                "is_shadow": True,
                "history": [_serialize_belief_state(bs) for bs in shadow_history],
                "update_count": len(shadow_history),
            }

    return result


def _discover_agent_ids_in_beliefs(game_id: str, belief_store: "BeliefStateStore") -> set[str]:
    """从 BeliefStateStore 中发现该对局的所有 agent_id。

    通过尝试获取每个可能的 agent_id 的历史来发现。
    """
    agent_ids = set()
    # 假设 agent_id 为 P1-P12（覆盖所有可能的局规模）
    for i in range(1, 13):
        agent_id = f"P{i}"
        history = belief_store.get_history(game_id, agent_id, is_shadow=False)
        if history:
            agent_ids.add(agent_id)
        shadow_history = belief_store.get_history(game_id, agent_id, is_shadow=True)
        if shadow_history:
            agent_ids.add(agent_id)

    return agent_ids


def _serialize_belief_state(belief_state: Any) -> dict[str, Any]:
    """将 BeliefState 对象序列化为 JSON 兼容格式。"""
    if hasattr(belief_state, "model_dump"):
        # Pydantic model
        return belief_state.model_dump(mode="json")
    return {
        "game_id": getattr(belief_state, "game_id", ""),
        "agent_id": getattr(belief_state, "agent_id", ""),
        "round": getattr(belief_state, "round", 0),
        "phase": str(getattr(belief_state, "phase", "")),
        "is_shadow": getattr(belief_state, "is_shadow", False),
        "beliefs": getattr(belief_state, "beliefs", {}),
        "last_updated_event_id": getattr(belief_state, "last_updated_event_id", ""),
    }


def _infer_strategy(
    game_id: str,
    trace_count: int,
    first_trace_agent_version: str | None = None,
    data_dir: str | None = None,
) -> str:
    """推断对局策略标签（V0 / V1 / V2）。

    判断优先级：
    1. game_id 以 batch_v0/v1/v2_ 开头 → 直接读（run_batch.py 生成）
    2. trace 的 agent_version 字段 → v0/v1 区分
    3. belief_states 目录是否存在 → 有 belief = V1 LLM
    4. 兜底返回 "V1 LLM"
    """
    if trace_count == 0:
        return "mock"

    # run_batch.py 生成的 game_id 格式：batch_{arm}_{seed}
    if game_id.startswith("batch_v2_"):
        return "V2 LLM"
    if game_id.startswith("batch_v1_"):
        return "V1 LLM"
    if game_id.startswith("batch_v0_"):
        return "V0 LLM"

    # 从 trace 的 agent_version 读
    if first_trace_agent_version == "v0":
        return "V0 LLM"

    if first_trace_agent_version in ("v1", "v2"):
        # 有 belief_states 说明是带 belief 的 v1/v2
        has_belief = False
        if data_dir:
            belief_dir = Path(data_dir) / "belief_states" / game_id
            has_belief = belief_dir.exists() and any(belief_dir.iterdir())
        if first_trace_agent_version == "v2":
            return "V2 LLM (Belief)" if has_belief else "V2 LLM"
        return "V1 LLM (Belief)" if has_belief else "V1 LLM"

    return "V1 LLM"


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
