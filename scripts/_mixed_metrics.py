"""混合 belief 实验的派生指标（PR-A-2 followup）。

> 设计前提：**一行不动 ``contracts/``**。本模块全部读现有接口 / 现有 dict 字段 /
> 派生于已冻结的 ``GameEvent`` + ``TruthState``，产物只写进 ``run_mixed_batch.py``
> 的 sidecar（``report.extras.json``），不进任何 schema。
>
> 红线遵循：
> - 全部在 ``scripts/`` 域（A 自有），不踩 B/C 的 owner 目录。
> - belief 相关派生在游戏**结束后**做（PostGameAnalyzer 性质），用真身做 outcome
>   分析是允许的；``RealtimeBeliefUpdater`` 本身一行不碰。
> - 只读冻结枚举值（``EventType`` / ``Phase`` / ``ActionType`` / ``Role`` / ``Camp``）。

指标按四个视角组织：

工程视角（pipeline 健康度）
  ``decision_stats``      LLM 决策 ok/parse_error/llm_error/retry + canonicalize 三类泄漏
  ``context_stats``       ContextWindowPolicy 的 truncate/degrade/exceed 压力
  ``pipeline``            fallback / rule_validation 兜底次数（红线穿透监控）

产品视角（对局形态 / belief 能否被「读到」）
  ``key_scenes``          tie / hunter / 一夜双死 / 首夜预言家被刀 / 女巫用药 / 预言家报查
  ``belief_signal.decision_top_suspect_consistency_rate``   注入玩家「听不听」belief
  ``belief_signal.top_suspect_accuracy_rate``               注入玩家「判断准不准」

数学视角（belief 分布质量 —— proper scoring）
  ``belief_signal.belief_quality.avg_brier``                Brier 分（越低越准，∈[0,1]）
  ``belief_signal.belief_quality.avg_suspicion_entropy``    怀疑集中度（归一化熵，越低越「果断」）
  ``belief_signal.belief_quality.avg_top_margin``           头号 vs 次号怀疑差（决断余量）
  ``belief_signal.belief_quality.avg_wolf_villager_separation``  对真狼/真民的判别力

算法视角（决策利用度 / 命中分解）
  ``belief_signal.top2_accuracy_rate``                      放宽到前二命中
  ``belief_signal.by_action_type``                          vote/check/poison/nominate/shoot 分桶
  ``belief_signal.deviation_count``                         决策偏离头号嫌疑人的次数

精度限制：``belief_signal`` 用 ``get_history`` 取
**决策时刻 round/phase 之前的最近 belief 快照**（比"永远取最终版"精确，已规避覆盖式存储的
epsilon 误差）；``final_belief_quality`` 用每个注入玩家的**最终**快照，不依赖 trace，
所以即使没有 trace（mock 局）也能产出 belief 数学指标。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING, Any, Iterable

from contracts import (
    ActionType,
    Camp,
    EventType,
    Phase,
    Role,
)
from agent_policy.belief_selectors import top_suspects_by_role

if TYPE_CHECKING:
    from contracts import BeliefState, GameEvent, TruthState
    from stores.belief_observability_store import BeliefObservabilityStore
    from stores.belief_state_store import BeliefStateStore

__all__ = [
    "collect_decision_stats",
    "collect_context_stats",
    "collect_pipeline_events",
    "collect_fallback_breakdown",
    "derive_key_scenes",
    "collect_belief_audit",
    "belief_snapshot_at",
    "compute_belief_signal",
    "compute_mixed_metrics",
]


# Phase 在状态机里的先后序号，用来在 history 里挑「决策时刻或之前」的 belief 快照。
_PHASE_ORDER: dict[Phase, int] = {phase: idx for idx, phase in enumerate(Phase)}

# 带 target 的决策动作（speak / skip 不算 —— 它们不指向某个嫌疑人）。
_DECISION_TARGET_TYPES = frozenset(
    {
        ActionType.VOTE,
        ActionType.CHECK,
        ActionType.POISON,
        ActionType.NIGHT_KILL_NOMINATE,
        ActionType.HUNTER_SHOOT,
    }
)
_DECISION_TARGET_VALUES = frozenset(a.value for a in _DECISION_TARGET_TYPES)

_GOD_ROLES = frozenset({Role.SEER, Role.WITCH, Role.HUNTER})


def _event_target(ev: "GameEvent") -> str | None:
    """事件的死亡/作用目标。

    ``EventEmitter.emit`` 会把 payload 里的 ``target`` **提到顶层** ``ev.target``
    （见 ``game_core/event_emitter.py``），真实事件 payload 里只剩 ``death_cause`` 等。
    所以优先读 ``ev.target``，再兜底 payload（合成/不经 emit 的事件）。
    """
    return ev.target or ev.payload.get("target")


# --------------------------------------------------------------------------- #
# 工程视角：pipeline 健康度
# --------------------------------------------------------------------------- #


def collect_decision_stats(agent: Any) -> dict[str, Any]:
    """从 agent 的 ``stats`` dict（``LLMAgent`` 暴露的运行时计数）派生决策质量。

    ``RoleStrategyMockAgent`` 没有 ``stats`` —— 返回全 0 骨架，``ok_rate`` 为 None。
    """
    raw = getattr(agent, "stats", None) or {}
    ok = int(raw.get("ok", 0))
    parse_error = int(raw.get("parse_error", 0))
    llm_error = int(raw.get("llm_error", 0))
    decided = ok + parse_error + llm_error
    return {
        "ok": ok,
        "parse_error": parse_error,
        "llm_error": llm_error,
        "retry": int(raw.get("retry", 0)),
        "canonicalize_meta_ai": int(raw.get("canonicalize_meta_ai", 0)),
        "canonicalize_cot_leak": int(raw.get("canonicalize_cot_leak", 0)),
        "canonicalize_role_leak": int(raw.get("canonicalize_role_leak", 0)),
        "decisions": decided,
        "ok_rate": (ok / decided) if decided else None,
    }


def collect_context_stats(built: Any) -> dict[str, Any]:
    """读 ``ContextWindowPolicy.stats``（裁剪/降级/超预算计数）。

    window policy 由 ``build_game`` 显式挂在 ``BuiltGame.window_policy`` 上（不穿透
    ``ContextAssembler`` 私有属性）。取不到则返回全 0（mock/Fake 装配也不炸）。
    """
    window = getattr(built, "window_policy", None)
    raw = getattr(window, "stats", None) or {}
    applies = int(raw.get("applies", 0))
    degrade = int(raw.get("progressive_degrade_triggered", 0))
    return {
        "applies": applies,
        "truncate": int(raw.get("truncated_speech_events", 0)),
        "degrade": degrade,
        "exceed": int(raw.get("budget_exceeded", 0)),
        "degrade_rate": (degrade / applies) if applies else None,
    }


def collect_pipeline_events(events: Iterable["GameEvent"]) -> dict[str, Any]:
    """数兜底相关事件 —— 监控有没有红线穿透 / 安全闸门触发频率。

    ``FALLBACK_USED`` 与 ``RULE_VALIDATION`` 由 supervisor 成对 emit（原非法动作 +
    已用兜底替换）；``degraded`` / ``fallback_failed`` 是兜底里更严重的两级。
    """
    fallback_used = 0
    rule_validation = 0
    degraded_fallback = 0
    fallback_failed = 0
    for ev in events:
        if ev.event_type == EventType.FALLBACK_USED:
            fallback_used += 1
            if ev.payload.get("degraded"):
                degraded_fallback += 1
            if ev.payload.get("fallback_failed"):
                fallback_failed += 1
        elif ev.event_type == EventType.RULE_VALIDATION:
            rule_validation += 1
    return {
        "fallback_used": fallback_used,
        "rule_validation": rule_validation,
        "degraded_fallback": degraded_fallback,
        "fallback_failed": fallback_failed,
    }


def collect_fallback_breakdown(
    events: Iterable["GameEvent"], decision_stats: dict
) -> dict[str, Any]:
    """按根因拆 fallback。

    LLM / parse 失败以 agent ``decision_stats`` 为权威；规则违规从
    ``RULE_VALIDATION.payload['violation_type']`` 聚合。schema-invalid 是解析失败后
    空动作兜底的交叉核对信号，单独列出，不混进策略违规。
    """
    rule_violation: dict[str, int] = {}
    schema_invalid = 0
    for ev in events:
        if ev.event_type != EventType.RULE_VALIDATION:
            continue
        violation_type = ev.payload.get("violation_type") or "unknown"
        if violation_type == "schema_invalid":
            schema_invalid += 1
        else:
            rule_violation[violation_type] = rule_violation.get(violation_type, 0) + 1
    return {
        "llm_error": int(decision_stats.get("llm_error", 0)),
        "parse_error": int(decision_stats.get("parse_error", 0)),
        "rule_violation": rule_violation,
        "schema_invalid_events": schema_invalid,
    }


# --------------------------------------------------------------------------- #
# 产品视角：关键场景命中
# --------------------------------------------------------------------------- #


def _seer_id(truth_state: "TruthState") -> str | None:
    for pid, player in truth_state.players.items():
        if player.role == Role.SEER:
            return pid
    return None


def derive_key_scenes(
    events: Iterable["GameEvent"], truth_state: "TruthState"
) -> dict[str, Any]:
    """派生关键场景命中 —— 看 belief 在「哪种局面」起作用。

    全部派生于冻结的 ``GameEvent`` 流 + ``TruthState``（真身只用于 seer 身份交叉，
    属于赛后分析，红线允许）。
    """
    events = list(events)
    seer = _seer_id(truth_state)

    tie_rounds: list[int] = []
    second_tie_rounds: list[int] = []
    hunter_shot_rounds: list[int] = []
    exile_rounds: list[int] = []
    night_death_rounds: list[int] = []
    double_death_rounds: list[int] = []
    seer_killed_round: int | None = None
    witch_save_round: int | None = None
    witch_poison_round: int | None = None
    seer_claim_round: int | None = None
    seer_disclosed_check = False
    final_round = 0

    for ev in events:
        final_round = max(final_round, ev.round)
        etype = ev.event_type
        if etype == EventType.TIE_DETECTED:
            tie_rounds.append(ev.round)
        elif etype == EventType.NO_EXILE_DUE_TO_SECOND_TIE:
            second_tie_rounds.append(ev.round)
        elif etype == EventType.HUNTER_SHOT and not ev.payload.get("pass"):
            hunter_shot_rounds.append(ev.round)
        elif etype == EventType.EXILE:
            exile_rounds.append(ev.round)
        elif etype == EventType.WITCH_SAVE and witch_save_round is None:
            witch_save_round = ev.round
        elif etype == EventType.WITCH_POISON and witch_poison_round is None:
            witch_poison_round = ev.round
        elif etype == EventType.DEATH_CONFIRMED:
            cause = ev.payload.get("death_cause")
            if cause == "night_kill":
                night_death_rounds.append(ev.round)
                if _event_target(ev) == seer and seer_killed_round is None:
                    seer_killed_round = ev.round
        elif etype == EventType.DAY_ANNOUNCEMENT:
            deaths = ev.payload.get("deaths") or []
            if len(deaths) >= 2:
                double_death_rounds.append(ev.round)
        elif etype == EventType.SPEECH and ev.actor == seer:
            # 预言家报查率：真预言家公开了一次查验结果（claim_result 非空）。
            if ev.payload.get("claim_result") is not None:
                seer_disclosed_check = True
                if seer_claim_round is None:
                    seer_claim_round = ev.round

    witch = truth_state.witch_state
    return {
        "final_round": final_round,
        "tie_hit": bool(tie_rounds),
        "tie_rounds": tie_rounds,
        "second_tie_no_exile_rounds": second_tie_rounds,
        "hunter_shot_used": bool(hunter_shot_rounds),
        "hunter_shot_rounds": hunter_shot_rounds,
        "exile_rounds": exile_rounds,
        "night_death_rounds": night_death_rounds,
        "double_death_rounds": double_death_rounds,
        "seer_killed_round": seer_killed_round,
        "seer_killed_n1": seer_killed_round == 1,
        "witch_save_used": bool(witch.antidote_used),
        "witch_save_round": witch_save_round,
        "witch_poison_used": bool(witch.poison_used),
        "witch_poison_round": witch_poison_round,
        "seer_disclosed_check": seer_disclosed_check,
        "seer_claim_round": seer_claim_round,
    }


# --------------------------------------------------------------------------- #
# belief lane 端到端审计
# --------------------------------------------------------------------------- #


def collect_belief_audit(
    observability_store: "BeliefObservabilityStore | None", game_id: str
) -> dict[str, Any]:
    """读 ``BeliefObservabilityStore`` 现有方法，聚合本局 belief lane 运行情况。"""
    if observability_store is None:
        return {"saves": 0, "curve_points": 0, "observers": 0}
    batches = observability_store.list_updates(game_id)
    curves = observability_store.list_curve_points(game_id)
    observers = len({getattr(b, "agent_id", None) for b in batches} - {None})
    return {
        "saves": len(batches),
        "curve_points": len(curves),
        "observers": observers,
    }


# --------------------------------------------------------------------------- #
# 数学视角：单个 belief 快照的分布质量
# --------------------------------------------------------------------------- #


def _suspicion_entropy(probs: list[float]) -> float | None:
    """把各活人的 werewolf 概率归一成分布后算香农熵，归一到 [0,1]。

    越低 → 怀疑越集中（belief 越「果断」）；越高 → 怀疑摊平（没主意）。
    """
    total = sum(probs)
    if total <= 0 or len(probs) < 2:
        return None
    dist = [p / total for p in probs]
    entropy = -sum(x * math.log2(x) for x in dist if x > 0)
    return entropy / math.log2(len(probs))


def _death_points(events: Iterable["GameEvent"]) -> dict[str, tuple[int, int]]:
    """每个玩家**首次** DEATH_CONFIRMED 的时点 ``(round, phase_order)``。

    用来在任意决策时刻重建存活集 —— 绝不用赛末 ``TruthState.status``（那会把决策之后
    才死的玩家错误地从候选里剔除，污染 top suspect / Brier / 熵 / margin）。
    """
    deaths: dict[str, tuple[int, int]] = {}
    for ev in events:
        if ev.event_type != EventType.DEATH_CONFIRMED:
            continue
        pid = _event_target(ev)
        if pid is None:
            continue
        key = (ev.round, _PHASE_ORDER.get(ev.phase, -1))
        if pid not in deaths or key < deaths[pid]:
            deaths[pid] = key
    return deaths


def _alive_at(
    players: Iterable[str],
    death_points: dict[str, tuple[int, int]],
    *,
    round_: int,
    phase: Phase,
    same_phase_dead: bool = False,
) -> set[str]:
    """某个 ``(round, phase)`` 时点的存活集，两种口径：

    - ``same_phase_dead=False``（**决策口径**，默认）：death 发生在**同 phase 或更晚**
      视为仍存活（``dp >= point``）。决策由 Supervisor 在落事件**之前**做出，同 phase 的
      死亡发生在决策之后，保守地不剔除 —— 与 belief 快照「严格早于」一致，防未来泄漏。
    - ``same_phase_dead=True``（**事件后口径**）：用于 ``final_belief_quality`` 的最终
      belief 快照。该快照是 ``RealtimeBeliefUpdater.update()`` 处理完事件**之后**保存的，
      若最终快照来自同 phase 的 ``DEATH_CONFIRMED``，该死亡目标此刻已死，应剔除
      （``dp > point`` 才算活）。
    """
    point = (round_, _PHASE_ORDER.get(phase, -1))
    alive: set[str] = set()
    for pid in players:
        dp = death_points.get(pid)
        if dp is None:
            alive.add(pid)
        elif same_phase_dead:
            if dp > point:
                alive.add(pid)
        elif dp >= point:
            alive.add(pid)
    return alive


def _snapshot_quality(
    snapshot: "BeliefState",
    truth_state: "TruthState",
    self_id: str,
    alive: set[str],
) -> dict[str, Any] | None:
    """对一个 belief 快照算 proper-scoring 质量指标（需真身，赛后分析）。

    ``alive`` 是**决策/快照时刻**的存活集（由事件流重建），不是赛末状态。
    role/camp 是固定真身，用最终 ``truth_state`` 读没问题；只有 status 才时变。
    """
    items: list[tuple[str, float, bool]] = []
    for pid in alive:
        if pid == self_id:
            continue
        role_belief = snapshot.beliefs.get(pid)
        player = truth_state.players.get(pid)
        if role_belief is None or player is None:
            continue
        items.append((pid, float(role_belief.werewolf), player.camp == Camp.WEREWOLF))
    if len(items) < 2:
        return None

    probs = [w for _, w, _ in items]
    wolf_probs = [w for _, w, is_wolf in items if is_wolf]
    villager_probs = [w for _, w, is_wolf in items if not is_wolf]

    ranked = sorted(probs, reverse=True)
    top_margin = ranked[0] - ranked[1]
    brier = sum((w - (1.0 if is_wolf else 0.0)) ** 2 for _, w, is_wolf in items) / len(
        items
    )
    separation = (
        (sum(wolf_probs) / len(wolf_probs) - sum(villager_probs) / len(villager_probs))
        if wolf_probs and villager_probs
        else None
    )
    return {
        "entropy": _suspicion_entropy(probs),
        "top_margin": top_margin,
        "brier": brier,
        "separation": separation,
    }


def _avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return (sum(present) / len(present)) if present else None


def belief_snapshot_at(
    history: list["BeliefState"], *, round_: int, phase: Phase
) -> "BeliefState | None":
    """从 history 里挑**严格早于**决策时刻 ``(round, phase)`` 的最近 belief 快照。

    Supervisor 先让 agent 决策、再落事件并触发 belief update，所以 belief 快照按
    **触发事件**的 (round, phase) 入账。要还原"agent 决策当时看到的 belief"，必须排除
    与决策**同 phase**（及更晚）的快照 —— 它们是本玩家行动之后 / 后续玩家行动之后才
    产生的，算进来就是未来信息泄漏。

    代价：同 phase 内靠后行动者会用到略陈旧的 belief（拿不到本 phase 早于自己的更新），
    但 trace 只记到 (round, phase) 粒度、无法定位玩家在 phase 内的次序，故取「严格早于」
    这个**保证零未来泄漏**的保守口径。没有任何严格更早的快照（决策早于首次 save）时返回
    None —— 由调用方计入 ``no_belief_decisions``，**绝不**退回未来快照。
    """
    if not history:
        return None
    target = (round_, _PHASE_ORDER.get(phase, -1))
    chosen: "BeliefState | None" = None
    for snap in history:
        key = (
            snap.round if snap.round is not None else 0,
            _PHASE_ORDER.get(snap.phase, -1) if snap.phase is not None else -1,
        )
        if key < target:
            chosen = snap
    return chosen


# --------------------------------------------------------------------------- #
# 产品 + 算法 + 数学视角：belief 信号（注入玩家专属）
# --------------------------------------------------------------------------- #


def _ranked_suspects(snapshot: "BeliefState", self_id: str, alive: set[str]) -> list[str]:
    """决策时存活的玩家按 werewolf 概率降序（排除自己）。``alive`` 由事件流重建。"""
    candidates: list[tuple[str, float]] = []
    for pid in alive:
        if pid == self_id:
            continue
        role_belief = snapshot.beliefs.get(pid)
        if role_belief is None:
            continue
        candidates.append((pid, float(role_belief.werewolf)))
    candidates.sort(key=lambda kv: kv[1], reverse=True)
    return [pid for pid, _ in candidates]


def _is_true_wolf(truth_state: "TruthState", pid: str | None) -> bool:
    if pid is None:
        return False
    player = truth_state.players.get(pid)
    return player is not None and player.camp == Camp.WEREWOLF


def _is_true_role(truth_state: "TruthState", pid: str | None, role: Role) -> bool:
    if pid is None:
        return False
    player = truth_state.players.get(pid)
    return player is not None and player.role == role


def compute_belief_signal(
    *,
    game_id: str,
    injected_agents: list[str],
    belief_store: "BeliefStateStore | None",
    truth_state: "TruthState",
    traces: list[Any],
    events: Iterable["GameEvent"],
) -> dict[str, Any] | None:
    """注入玩家的 belief 信号：被读取率（听不听）+ 命中率（准不准）+ 分布质量。

    返回 None 表示这一局没有任何注入玩家（baseline-v0 / 未启 belief lane）。

    - ``decision_*``：注入玩家做带 target 决策时，target 是否落在决策当时的头号嫌疑人上。
    - ``top_suspect_accuracy``：头号嫌疑人实际是真狼的频率（判断准不准）。
    - ``top2_accuracy``：放宽到前二命中。
    - ``by_action_type``：vote/check/poison/nominate/shoot 各自的一致 + 命中分解。
    - ``belief_quality``：决策点上 belief 分布的 Brier / 熵 / margin / 判别力（数学视角）。
    - ``final_belief_quality``：每个注入玩家**最终** belief 的质量均值（不依赖 trace，
      mock 局也能算）。
    """
    if belief_store is None or not injected_agents:
        return None

    injected = sorted(injected_agents)
    injected_set = set(injected)
    death_points = _death_points(events)

    top_decisions = 0
    matches = 0
    hits_wolf = 0
    top2_hits = 0
    deviation = 0
    no_belief_decisions = 0
    by_action: dict[str, dict[str, int]] = {}
    quality_samples: list[dict[str, Any] | None] = []

    for trace in traces:
        if getattr(trace, "agent_id", None) not in injected_set:
            continue
        output = getattr(trace, "decision_output", None) or {}
        action_type = output.get("action_type")
        target = output.get("target")
        if action_type not in _DECISION_TARGET_VALUES or not target:
            continue

        history = belief_store.get_history(game_id, trace.agent_id, is_shadow=False)
        snapshot = belief_snapshot_at(history, round_=trace.round, phase=trace.phase)
        if snapshot is None:
            no_belief_decisions += 1
            continue
        alive = _alive_at(
            truth_state.players, death_points, round_=trace.round, phase=trace.phase
        )
        ranked = _ranked_suspects(snapshot, trace.agent_id, alive)
        if not ranked:
            no_belief_decisions += 1
            continue

        top_suspect = ranked[0]
        bucket = by_action.setdefault(
            action_type, {"decisions": 0, "matches": 0, "hits_wolf": 0}
        )
        top_decisions += 1
        bucket["decisions"] += 1
        if target == top_suspect:
            matches += 1
            bucket["matches"] += 1
        else:
            deviation += 1
        if _is_true_wolf(truth_state, top_suspect):
            hits_wolf += 1
            bucket["hits_wolf"] += 1
        if any(_is_true_wolf(truth_state, pid) for pid in ranked[:2]):
            top2_hits += 1

        quality_samples.append(
            _snapshot_quality(snapshot, truth_state, trace.agent_id, alive)
        )

    # final_belief_quality：用每个注入玩家最终 belief（trace 无关 → mock 局也能算）。
    # 存活集按**该最终快照**的 (round, phase) 重建，不是赛末全局状态。
    final_quality: list[dict[str, Any] | None] = []
    agents_evaluated = 0
    per_role_hits = {Role.SEER: 0, Role.WITCH: 0}
    per_role_samples = {Role.SEER: 0, Role.WITCH: 0}
    for agent_id in injected:
        history = belief_store.get_history(game_id, agent_id, is_shadow=False)
        if not history:
            continue
        final_snap = history[-1]
        # 事件后口径：最终快照在 update 处理完事件后保存，同 phase 死亡此刻已死，剔除。
        alive = _alive_at(
            truth_state.players,
            death_points,
            round_=final_snap.round if final_snap.round is not None else truth_state.round,
            phase=final_snap.phase if final_snap.phase is not None else truth_state.phase,
            same_phase_dead=True,
        )
        q = _snapshot_quality(final_snap, truth_state, agent_id, alive)
        if q is not None:
            agents_evaluated += 1
            final_quality.append(q)
        for role in (Role.SEER, Role.WITCH):
            if _is_true_role(truth_state, agent_id, role):
                # 观察者自己就是该角色 → 让其"识别另一个该角色"无意义（唯一角色），
                # 跳过以免引入必然 miss 的噪声。
                continue
            ranked_role = top_suspects_by_role(
                final_snap,
                role,
                k=1,
                alive_set=alive,
                exclude={agent_id},
            )
            if not ranked_role:
                continue
            per_role_samples[role] += 1
            if _is_true_role(truth_state, ranked_role[0][0], role):
                per_role_hits[role] += 1

    def _rate(num: int, den: int) -> float | None:
        return (num / den) if den else None

    return {
        "injected_agents": injected,
        "decision_target_types": sorted(_DECISION_TARGET_VALUES),
        "top_suspect_decisions": top_decisions,
        "decision_matches_top_suspect": matches,
        "decision_top_suspect_consistency_rate": _rate(matches, top_decisions),
        "deviation_count": deviation,
        "no_belief_decisions": no_belief_decisions,
        "top_suspect_hits_true_wolf": hits_wolf,
        "top_suspect_accuracy_rate": _rate(hits_wolf, top_decisions),
        "top2_hits_true_wolf": top2_hits,
        "top2_accuracy_rate": _rate(top2_hits, top_decisions),
        "by_action_type": by_action,
        "belief_quality": {
            "samples": len([q for q in quality_samples if q is not None]),
            "avg_suspicion_entropy": _avg([q["entropy"] for q in quality_samples if q]),
            "avg_top_margin": _avg([q["top_margin"] for q in quality_samples if q]),
            "avg_brier": _avg([q["brier"] for q in quality_samples if q]),
            "avg_wolf_villager_separation": _avg(
                [q["separation"] for q in quality_samples if q]
            ),
        },
        "final_belief_quality": {
            "agents_evaluated": agents_evaluated,
            "avg_suspicion_entropy": _avg([q["entropy"] for q in final_quality if q]),
            "avg_top_margin": _avg([q["top_margin"] for q in final_quality if q]),
            "avg_brier": _avg([q["brier"] for q in final_quality if q]),
            "avg_wolf_villager_separation": _avg(
                [q["separation"] for q in final_quality if q]
            ),
        },
        # per-role 识别准确率。注意：seer 有强公开信号（跳预言家 + 报查 + 言行一致性），
        # 而 **witch 只有 claim_witch 一个可观测信号** —— 女巫的 save/poison 是
        # PRIVATE_TO_WITCH，别的玩家观察不到，无法据此识别女巫（强行用就是 no-op 或信息
        # 泄漏，故 M3 复审已移除该路径）。因此 witch_identification_accuracy 偏低是**诚实**的，
        # 不要再加"女巫用药→抬 witch"的私有事件信号。
        "per_role_identification": {
            "seer_samples": per_role_samples[Role.SEER],
            "seer_hits": per_role_hits[Role.SEER],
            "seer_identification_accuracy": _rate(
                per_role_hits[Role.SEER], per_role_samples[Role.SEER]
            ),
            "witch_samples": per_role_samples[Role.WITCH],
            "witch_hits": per_role_hits[Role.WITCH],
            "witch_identification_accuracy": _rate(
                per_role_hits[Role.WITCH], per_role_samples[Role.WITCH]
            ),
        },
    }


# --------------------------------------------------------------------------- #
# 编排：一局算齐所有派生指标，返回挂进 sidecar extra 的 block
# --------------------------------------------------------------------------- #


def compute_mixed_metrics(
    *,
    built: Any,
    agent: Any,
    game_id: str,
    injected_agents: list[str],
    arm: str,
) -> dict[str, Any]:
    """跑批主循环每局结束后调用，返回要 merge 进 sidecar extra 的指标 block。"""
    event_store = built.stores.event_store
    events = event_store.list_by_game(game_id)
    truth_state = built.engine.get_session(game_id).truth_state
    trace_store = getattr(built.stores, "trace_store", None)
    traces = trace_store.list_by_game(game_id) if trace_store is not None else []

    block: dict[str, Any] = {
        "decision_stats": collect_decision_stats(agent),
        "context_stats": collect_context_stats(built),
        "pipeline": collect_pipeline_events(events),
        "key_scenes": derive_key_scenes(events, truth_state),
        "belief_audit": collect_belief_audit(
            getattr(built.stores, "belief_observability_store", None) if arm == "v1" else None,
            game_id,
        ),
    }
    block["fallback_breakdown"] = collect_fallback_breakdown(
        events, block["decision_stats"]
    )
    if arm == "v1" and injected_agents:
        block["belief_signal"] = compute_belief_signal(
            game_id=game_id,
            injected_agents=injected_agents,
            belief_store=getattr(built.stores, "belief_store", None),
            truth_state=truth_state,
            traces=traces,
            events=events,
        )
    else:
        block["belief_signal"] = None
    return block
