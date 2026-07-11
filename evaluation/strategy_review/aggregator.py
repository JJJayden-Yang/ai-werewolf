"""聚合层（纯代码，无 LLM）。

把一批 game 的 event + trace + replay_truth + belief_states 压缩成**按角色**的复盘材料：
统计 + notability 抽样 + 脱敏 digest，外加一份 belief 命中率报告。喂给 LLM 的永远是这里的
派生摘要，不是 50 局原文（token 护栏，见 ``docs/strategy_review_loop.md §4/§6.1``）。

读取纪律（§3 坑）：
- trace **只用 ``list_by_game``**（历史局会回落读盘），按 agent 的索引在内存自建。
- replay_truth 是 ``<game_id>.json``，只含 players；**胜负从 ``GAME_OVER`` event 取**。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from contracts.enums import EventType
from evaluation.strategy_review.arm import resolve_arm
from evaluation.strategy_review.belief_accuracy import (
    BeliefAccuracyReport,
    GameBeliefInput,
    compute_belief_accuracy,
)
from evaluation.strategy_review.sanitize import position_label

ROLES = ("werewolf", "seer", "witch", "hunter", "villager")

# 抽样旋钮（§4）。
DEFAULT_TOP_K_FAILURES = 3
DEFAULT_POSITIVE_SAMPLES = 1
MAX_DECISIONS_PER_DIGEST = 8
REASON_SUMMARY_MAX_CHARS = 200
SPEECH_MAX_CHARS = 240  # 公开发言原文截断长度
DIGEST_MAX_CHARS = 2400  # 含发言原文后放宽


# --------------------------------------------------------------------------- #
# 数据结构（本地，不进 contracts）
# --------------------------------------------------------------------------- #


@dataclass
class RoleInstance:
    """一局里某玩家（某角色实例）的赛后画像。"""

    game_id: str
    arm: str
    agent_id: str
    role: str
    won: bool
    survived: bool
    voted_out: bool
    # 机械失败
    parse_error: bool = False
    llm_error: bool = False
    fallback: bool = False
    canonicalized: bool = False
    role_signal: dict[str, Any] = field(default_factory=dict)
    scene_tags: frozenset[str] = frozenset()
    traces: list[Any] = field(default_factory=list)
    # 该 agent 的公开发言原文：{(round, phase_value): public_message}（反映真实策略表达）
    speeches: dict[tuple[int, str], str] = field(default_factory=dict)
    notability: float = 0.0


@dataclass
class RoleSample:
    game_id: str
    arm: str
    notability: float
    kind: str  # "failure" | "positive"
    digest: str
    evidence: list[dict[str, Any]]


@dataclass
class RoleBatchReview:
    role: str
    n_instances: int
    stats: dict[str, Any]
    samples: list[RoleSample]


@dataclass
class GlobalBatchReview:
    n_games: int
    stats: dict[str, Any]
    samples: list[RoleSample]


@dataclass
class AggregateResult:
    role_reviews: dict[str, RoleBatchReview]
    global_review: GlobalBatchReview
    belief: BeliefAccuracyReport
    game_ids: list[str]
    arm_counts: dict[str, int]


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def aggregate(
    game_ids: list[str],
    *,
    event_store: Any,
    trace_store: Any,
    replay_truth_store: Any,
    belief_store: Any,
    top_k_failures: int = DEFAULT_TOP_K_FAILURES,
    positive_samples: int = DEFAULT_POSITIVE_SAMPLES,
) -> AggregateResult:
    instances_by_role: dict[str, list[RoleInstance]] = defaultdict(list)
    belief_inputs: list[GameBeliefInput] = []
    arm_counts: dict[str, int] = defaultdict(int)
    game_summaries: list[dict[str, Any]] = []

    for gid in game_ids:
        events = event_store.list_by_game(gid)
        traces = trace_store.list_by_game(gid)
        # 真实身份优先取 replay_truth；批量跑局通常不落 replay_truth，则从 trace.role 反推
        # （trace 自带每个 agent 的真实角色，是同样可靠的赛后真相来源）。
        players = replay_truth_store.get_players(gid) or _players_from_traces(traces, events)
        if not players:
            continue
        arm = resolve_arm(gid, traces)
        arm_counts[arm] += 1

        role_map = {p["player_id"]: p["role"] for p in players if p.get("player_id")}
        status_map = {p["player_id"]: p.get("status") for p in players if p.get("player_id")}
        ordered_ids = sorted(role_map.keys())
        winner_camp = _winner_camp(events)
        traces_by_agent = _index_traces(traces)
        speeches_by_agent = _speeches_by_agent(events)
        scene_tags = _scene_tags(events)
        exiled = _exiled_targets(events)

        belief_inputs.append(
            GameBeliefInput(
                game_id=gid, arm=arm, role_map=role_map, players=players, traces=traces, events=events
            )
        )
        game_summaries.append(
            _game_summary(gid, arm, events, role_map, winner_camp, scene_tags)
        )

        for pid, role in role_map.items():
            camp = "werewolf" if role == "werewolf" else "villager"
            inst = RoleInstance(
                game_id=gid,
                arm=arm,
                agent_id=pid,
                role=role,
                won=(winner_camp is not None and camp == winner_camp),
                survived=(status_map.get(pid) == "alive"),
                voted_out=(pid in exiled),
                scene_tags=scene_tags,
                traces=traces_by_agent.get(pid, []),
                speeches=speeches_by_agent.get(pid, {}),
            )
            _fill_mechanical(inst)
            _fill_role_signal(inst, pid, role, events, role_map, ordered_ids)
            inst.notability = _notability(inst)
            instances_by_role[role].append(inst)

    role_reviews: dict[str, RoleBatchReview] = {}
    for role in ROLES:
        insts = instances_by_role.get(role, [])
        role_reviews[role] = RoleBatchReview(
            role=role,
            n_instances=len(insts),
            stats=_role_stats(insts),
            samples=_select_samples(insts, role, top_k_failures, positive_samples),
        )

    global_review = _global_review(game_summaries, instances_by_role)
    belief = compute_belief_accuracy(belief_inputs, belief_store)

    return AggregateResult(
        role_reviews=role_reviews,
        global_review=global_review,
        belief=belief,
        game_ids=list(game_ids),
        arm_counts=dict(arm_counts),
    )


# --------------------------------------------------------------------------- #
# event / trace 解析
# --------------------------------------------------------------------------- #


def _players_from_traces(traces: list[Any], events: list[Any]) -> list[dict[str, Any]]:
    """replay_truth 缺失时，从 trace.role 反推 players（真实身份来源）。

    role 取该 agent 任一 trace 的 ``role``（同一局内恒定）；camp 由 role 推；
    status 由 death_confirmed 事件的 target 判定（出现即 dead）。
    """
    role_by_agent: dict[str, str] = {}
    for t in traces:
        aid = getattr(t, "agent_id", None)
        role = getattr(t, "role", None)
        role = getattr(role, "value", role)
        if aid and role and aid not in role_by_agent:
            role_by_agent[aid] = role
    if not role_by_agent:
        return []
    died: set[str] = set()
    for ev in events:
        if _etype(ev) == EventType.DEATH_CONFIRMED.value:
            tgt = getattr(ev, "target", None)
            if tgt:
                died.add(tgt)
    return [
        {
            "player_id": aid,
            "role": role,
            "camp": "werewolf" if role == "werewolf" else "villager",
            "status": "dead" if aid in died else "alive",
        }
        for aid, role in sorted(role_by_agent.items())
    ]


def _speeches_by_agent(events: list[Any]) -> dict[str, dict[tuple[int, str], str]]:
    """从 SPEECH 事件抽每个 agent 的公开发言原文：{pid: {(round, phase_value): message}}。

    这是 agent **真正说出口的话**，最能反映策略表达（跳身份/悍跳/带节奏/解释票型）。
    同一 (round, phase) 多次发言则拼接。
    """
    out: dict[str, dict[tuple[int, str], str]] = defaultdict(dict)
    for ev in events:
        if _etype(ev) != EventType.SPEECH.value:
            continue
        actor = getattr(ev, "actor", None)
        if not actor:
            continue
        msg = (getattr(ev, "payload", {}) or {}).get("public_message")
        if not msg:
            continue
        key = (getattr(ev, "round", 0) or 0, _phase_str(ev))
        prev = out[actor].get(key)
        out[actor][key] = f"{prev} / {msg}" if prev else msg
    return out


def _index_traces(traces: list[Any]) -> dict[str, list[Any]]:
    """自建 (agent_id)->traces 索引（按 round/phase 序）。不依赖 store 的内存索引。"""
    by_agent: dict[str, list[Any]] = defaultdict(list)
    for t in traces:
        aid = getattr(t, "agent_id", None)
        if aid:
            by_agent[aid].append(t)
    for aid in by_agent:
        by_agent[aid].sort(key=lambda t: (getattr(t, "round", 0) or 0, str(getattr(t, "phase", ""))))
    return by_agent


def _winner_camp(events: list[Any]) -> str | None:
    for ev in reversed(events):
        if _etype(ev) == EventType.GAME_OVER.value:
            winner = (getattr(ev, "payload", {}) or {}).get("winner")
            if not winner:
                return None
            # 注意：狼胜的 winner 值是 "werewolves"（were+wolves），不含子串 "wolf"！
            # 用 "werewol" 同时匹配 werewolves / werewolf，别再写 "wolf" in …。
            return "werewolf" if "werewol" in str(winner).lower() else "villager"
    return None


def _exiled_targets(events: list[Any]) -> set[str]:
    out: set[str] = set()
    for ev in events:
        if _etype(ev) == EventType.EXILE.value:
            tgt = getattr(ev, "target", None)
            if tgt:
                out.add(tgt)
    return out


def _scene_tags(events: list[Any]) -> frozenset[str]:
    tags: set[str] = set()
    max_round = 0
    for ev in events:
        et = _etype(ev)
        max_round = max(max_round, getattr(ev, "round", 0) or 0)
        if et == EventType.TIE_DETECTED.value:
            tags.add("tie_revote")
        elif et == EventType.HUNTER_SHOT.value:
            tags.add("hunter_shoot")
        elif et == EventType.WITCH_POISON.value:
            tags.add("witch_poison")
    if max_round >= 4:
        tags.add("endgame_long")
    return frozenset(tags)


def _etype(ev: Any) -> str:
    et = getattr(ev, "event_type", None)
    return getattr(et, "value", et)


def _phase_str(obj: Any) -> str:
    """取 phase 的可读值（``DAY_VOTE``），而非枚举 repr（``Phase.DAY_VOTE``）。"""
    ph = getattr(obj, "phase", obj)
    return str(getattr(ph, "value", ph))


def _fill_mechanical(inst: RoleInstance) -> None:
    for t in inst.traces:
        flags = getattr(t, "decision_quality_flags", {}) or {}
        if flags.get("parse_error"):
            inst.parse_error = True
        if flags.get("outcome") == "llm_error" or flags.get("llm_error"):
            inst.llm_error = True
        if (flags.get("retry_count") or 0) > 0:
            inst.fallback = True
        if flags.get("canonicalize_triggered"):
            inst.canonicalized = True


def _fill_role_signal(
    inst: RoleInstance,
    pid: str,
    role: str,
    events: list[Any],
    role_map: dict[str, str],
    ordered_ids: list[str],
) -> None:
    """角色专属赛后信号（用真相核对，只在聚合层算）。脱敏：只存命中/数量，不存身份。"""
    is_wolf = lambda x: role_map.get(x) == "werewolf"  # noqa: E731
    sig = inst.role_signal
    if role == "seer":
        checks = correct = 0
        for ev in events:
            if _etype(ev) == EventType.SEER_CHECK_RESULT.value and getattr(ev, "actor", None) == pid:
                tgt = getattr(ev, "target", None)
                result = (getattr(ev, "payload", {}) or {}).get("result")
                if tgt is None:
                    continue
                checks += 1
                said_wolf = str(result).lower() in {"werewolf", "wolf"}
                if said_wolf == is_wolf(tgt):
                    correct += 1
        sig["checks"] = checks
        sig["check_correct"] = correct
    elif role == "villager":
        votes = hit = 0
        for ev in events:
            if _etype(ev) == EventType.VOTE_CAST.value and getattr(ev, "actor", None) == pid:
                tgt = getattr(ev, "target", None)
                if tgt:
                    votes += 1
                    if is_wolf(tgt):
                        hit += 1
        sig["votes"] = votes
        sig["vote_hit_wolf"] = hit
    elif role == "witch":
        for ev in events:
            if _etype(ev) == EventType.WITCH_POISON.value and getattr(ev, "actor", None) == pid:
                tgt = getattr(ev, "target", None)
                sig["poisoned"] = True
                sig["poison_hit_wolf"] = bool(tgt and is_wolf(tgt))
    elif role == "hunter":
        for ev in events:
            if _etype(ev) == EventType.HUNTER_SHOT.value and getattr(ev, "actor", None) == pid:
                tgt = getattr(ev, "target", None)
                sig["shot"] = True
                sig["shot_hit_wolf"] = bool(tgt and is_wolf(tgt))


# --------------------------------------------------------------------------- #
# notability 打分 + 抽样
# --------------------------------------------------------------------------- #


def _notability(inst: RoleInstance) -> float:
    score = 0.0
    if inst.parse_error or inst.llm_error or inst.canonicalized:
        score += 2.0
    if inst.fallback:
        score += 1.0
    if not inst.won:
        score += 1.0
    sig = inst.role_signal
    if inst.role == "seer":
        if sig.get("checks") and sig.get("check_correct") == sig.get("checks") and not inst.won:
            score += 2.0  # 查验全对却带不动
        if inst.voted_out:
            score += 1.0
    elif inst.role == "villager":
        misvotes = (sig.get("votes", 0) or 0) - (sig.get("vote_hit_wolf", 0) or 0)
        score += min(misvotes, 3) * 0.5
    elif inst.role == "witch":
        if sig.get("poisoned") and not sig.get("poison_hit_wolf"):
            score += 2.0  # 毒到好人
    elif inst.role == "hunter":
        if sig.get("shot") and not sig.get("shot_hit_wolf"):
            score += 2.0  # 带走好人
    elif inst.role == "werewolf":
        if inst.voted_out:
            score += 1.0  # 暴露被票出
    return score


def _select_samples(
    insts: list[RoleInstance], role: str, top_k: int, positive: int
) -> list[RoleSample]:
    if not insts:
        return []
    failures = sorted(insts, key=lambda i: i.notability, reverse=True)
    picked: list[RoleInstance] = []
    seen_scenes: set[frozenset[str]] = set()
    for inst in failures:
        if inst.notability <= 0:
            break
        if inst.scene_tags in seen_scenes and len(picked) > 0:
            continue
        seen_scenes.add(inst.scene_tags)
        picked.append(inst)
        if len(picked) >= top_k:
            break

    positives = [i for i in insts if i.won and i.notability <= 1.0]
    positives.sort(key=lambda i: i.notability)
    pos_picked = positives[:positive]

    samples = [_to_sample(i, "failure") for i in picked]
    samples += [_to_sample(i, "positive") for i in pos_picked]
    return samples


def _to_sample(inst: RoleInstance, kind: str) -> RoleSample:
    return RoleSample(
        game_id=inst.game_id,
        arm=inst.arm,
        notability=inst.notability,
        kind=kind,
        digest=_build_digest(inst),
        evidence=_build_evidence(inst),
    )


def _build_digest(inst: RoleInstance) -> str:
    """该角色 POV 叙事 —— 脱敏：派生标签 + 相对位置，不写「它是谁 / Px 是狼」。"""
    ordered = sorted({getattr(t, "agent_id", "") for t in inst.traces} | {inst.agent_id})
    lines: list[str] = []
    outcome = "本方胜" if inst.won else "本方负"
    survive = "存活到终局" if inst.survived else "中途出局"
    extra = "（被投票放逐）" if inst.voted_out else ""
    lines.append(f"角色={inst.role} arm={inst.arm} 结果={outcome} {survive}{extra}")
    lines.append(f"场景标签={sorted(inst.scene_tags) or '无'}")
    lines.append(f"派生信号={_signal_labels(inst)}")

    mech = []
    if inst.parse_error:
        mech.append("解析失败")
    if inst.llm_error:
        mech.append("LLM错误")
    if inst.fallback:
        mech.append("触发兜底")
    if inst.canonicalized:
        mech.append("被清洗")
    if mech:
        lines.append(f"机械问题={mech}")

    lines.append("决策序列（speak 给公开发言原文，其余给决策理由）:")
    for t in inst.traces[:MAX_DECISIONS_PER_DIGEST]:
        out = getattr(t, "decision_output", {}) or {}
        action = out.get("action_type", "?")
        rnd = getattr(t, "round", "?")
        phase = _phase_str(t)
        if action == "speak":
            # 真实公开发言（反映策略表达）；取不到则回落 reason_summary。
            msg = inst.speeches.get((rnd if isinstance(rnd, int) else 0, phase), "")
            msg = (msg or out.get("reason_summary") or "").strip().replace("\n", " ")
            if len(msg) > SPEECH_MAX_CHARS:
                msg = msg[:SPEECH_MAX_CHARS] + "…"
            lines.append(f"  [R{rnd} {phase}] 发言: “{msg}”")
        else:
            target = out.get("target")
            tgt_label = position_label(target, ordered) if target else "-"
            reason = (out.get("reason_summary") or "").strip().replace("\n", " ")
            if len(reason) > REASON_SUMMARY_MAX_CHARS:
                reason = reason[:REASON_SUMMARY_MAX_CHARS] + "…"
            lines.append(f"  [R{rnd} {phase}] {action} -> {tgt_label} | 理由: {reason}")

    text = "\n".join(lines)
    if len(text) > DIGEST_MAX_CHARS:
        text = text[:DIGEST_MAX_CHARS] + "…"
    return text


def _signal_labels(inst: RoleInstance) -> str:
    sig = inst.role_signal
    if inst.role == "seer" and sig.get("checks"):
        return f"查验{sig['checks']}次命中{sig.get('check_correct', 0)}次"
    if inst.role == "villager" and sig.get("votes"):
        return f"投票{sig['votes']}次投中真凶{sig.get('vote_hit_wolf', 0)}次"
    if inst.role == "witch" and sig.get("poisoned"):
        return "用毒" + ("命中真凶" if sig.get("poison_hit_wolf") else "误伤好人")
    if inst.role == "hunter" and sig.get("shot"):
        return "开枪" + ("命中真凶" if sig.get("shot_hit_wolf") else "带走好人")
    return "无"


def _build_evidence(inst: RoleInstance) -> list[dict[str, Any]]:
    ev: list[dict[str, Any]] = []
    for t in inst.traces[:MAX_DECISIONS_PER_DIGEST]:
        ev.append(
            {
                "game_id": inst.game_id,
                "round": getattr(t, "round", None),
                "phase": _phase_str(t),
                "trace_id": getattr(t, "trace_id", None),
            }
        )
    return ev


# --------------------------------------------------------------------------- #
# 统计聚合
# --------------------------------------------------------------------------- #


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def _role_stats(insts: list[RoleInstance]) -> dict[str, Any]:
    if not insts:
        return {"n": 0}
    n = len(insts)
    overall = {
        "n": n,
        "win_rate": _rate(sum(i.won for i in insts), n),
        "survival_rate": _rate(sum(i.survived for i in insts), n),
        "voted_out_rate": _rate(sum(i.voted_out for i in insts), n),
        "parse_error_rate": _rate(sum(i.parse_error for i in insts), n),
        "llm_error_rate": _rate(sum(i.llm_error for i in insts), n),
        "fallback_rate": _rate(sum(i.fallback for i in insts), n),
        "canonicalize_rate": _rate(sum(i.canonicalized for i in insts), n),
    }
    overall.update(_role_specific_stats(insts))

    by_arm: dict[str, Any] = {}
    arms: dict[str, list[RoleInstance]] = defaultdict(list)
    for i in insts:
        arms[i.arm].append(i)
    for arm, group in arms.items():
        m = len(group)
        by_arm[arm] = {
            "n": m,
            "win_rate": _rate(sum(i.won for i in group), m),
            "survival_rate": _rate(sum(i.survived for i in group), m),
        }
    return {"overall": overall, "by_arm": by_arm}


def _role_specific_stats(insts: list[RoleInstance]) -> dict[str, Any]:
    role = insts[0].role
    s = [i.role_signal for i in insts]
    if role == "seer":
        checks = sum(x.get("checks", 0) or 0 for x in s)
        correct = sum(x.get("check_correct", 0) or 0 for x in s)
        return {"check_accuracy": _rate(correct, checks), "total_checks": checks}
    if role == "villager":
        votes = sum(x.get("votes", 0) or 0 for x in s)
        hit = sum(x.get("vote_hit_wolf", 0) or 0 for x in s)
        return {"vote_hit_rate": _rate(hit, votes), "total_votes": votes}
    if role == "witch":
        used = [x for x in s if x.get("poisoned")]
        hit = sum(1 for x in used if x.get("poison_hit_wolf"))
        return {"poison_uses": len(used), "poison_hit_rate": _rate(hit, len(used))}
    if role == "hunter":
        used = [x for x in s if x.get("shot")]
        hit = sum(1 for x in used if x.get("shot_hit_wolf"))
        return {"shot_uses": len(used), "shot_hit_rate": _rate(hit, len(used))}
    return {}


def _game_summary(
    game_id: str,
    arm: str,
    events: list[Any],
    role_map: dict[str, str],
    winner_camp: str | None,
    scene_tags: frozenset[str],
) -> dict[str, Any]:
    max_round = max((getattr(e, "round", 0) or 0 for e in events), default=0)
    return {
        "game_id": game_id,
        "arm": arm,
        "winner_camp": winner_camp,
        "rounds": max_round,
        "scene_tags": sorted(scene_tags),
        "timeline": _game_timeline_digest(events, role_map, winner_camp, max_round),
    }


_GOD_ROLES = frozenset({"seer", "witch", "hunter"})
# 夜间致死的事件类型（按出现的即视为该轮夜里出局）。
_NIGHT_DEATH_CAUSES = frozenset({"night_kill", "witch_poison"})


def _game_timeline_digest(
    events: list[Any], role_map: dict[str, str], winner_camp: str | None, max_round: int
) -> str:
    """整局时间线摘要（脱敏）：逐轮「夜间出局 / 跳身份 / 放逐命中或误放 / 平票」+ 结局。

    只给**轮级派生标签**（命中狼 / 误放好人 / 含神职）与公开票型,不写「Px 是<角色>」式真相归属
    （阵营只作轮级 hit/miss 标签,不挂到具体玩家 id 上）。见 §2.3。
    """
    is_wolf = lambda x: role_map.get(x) == "werewolf"  # noqa: E731

    # 按轮归类关键事件
    by_round: dict[int, dict[str, Any]] = defaultdict(lambda: {"night_dead": [], "exile": None, "claims": 0, "tie": False})
    for ev in events:
        r = getattr(ev, "round", 0) or 0
        et = _etype(ev)
        slot = by_round[r]
        if et == EventType.DEATH_CONFIRMED.value:
            cause = (getattr(ev, "payload", {}) or {}).get("death_cause")
            tgt = getattr(ev, "target", None)
            if cause in _NIGHT_DEATH_CAUSES and tgt:
                slot["night_dead"].append(tgt)
        elif et == EventType.EXILE.value:
            slot["exile"] = getattr(ev, "target", None)
        elif et == EventType.SPEECH.value:
            if (getattr(ev, "payload", {}) or {}).get("role_claim"):
                slot["claims"] += 1
        elif et == EventType.TIE_DETECTED.value:
            slot["tie"] = True

    lines: list[str] = []
    for r in range(1, max_round + 1):
        s = by_round.get(r)
        if not s:
            continue
        seg = [f"R{r}:"]
        nd = s["night_dead"]
        if nd:
            gods = sum(1 for pid in nd if role_map.get(pid) in _GOD_ROLES)
            seg.append(f"夜出局{len(nd)}人" + (f"(含神职{gods})" if gods else ""))
        if s["claims"]:
            seg.append(f"跳身份{s['claims']}次")
        if s["tie"]:
            seg.append("平票二投")
        if s["exile"] is not None:
            seg.append("放逐→" + ("命中狼" if is_wolf(s["exile"]) else "误放好人"))
        elif r <= max_round and not nd:
            seg.append("无人出局")
        lines.append(" ".join(seg))
    lines.append(f"结局: {winner_camp or '未知'}胜")
    return "\n".join(lines)


def _global_review(
    game_summaries: list[dict[str, Any]], instances_by_role: dict[str, list[RoleInstance]]
) -> GlobalBatchReview:
    n = len(game_summaries)
    wolf_wins = sum(1 for g in game_summaries if g["winner_camp"] == "werewolf")
    good_wins = sum(1 for g in game_summaries if g["winner_camp"] == "villager")
    avg_rounds = _rate(sum(g["rounds"] for g in game_summaries), n)
    tie_games = sum(1 for g in game_summaries if "tie_revote" in g["scene_tags"])

    stats = {
        "n_games": n,
        "wolf_win_rate": _rate(wolf_wins, n),
        "good_win_rate": _rate(good_wins, n),
        "avg_rounds": avg_rounds,
        "tie_rate": _rate(tie_games, n),
    }

    # 全局样本：碾压 / 翻盘 / 平票混战 各取代表，digest 用整局时间线摘要。
    samples: list[RoleSample] = []
    chosen = _pick_global_games(game_summaries)
    for g in chosen:
        samples.append(
            RoleSample(
                game_id=g["game_id"],
                arm=g["arm"],
                notability=0.0,
                kind="global",
                digest=(
                    f"arm={g['arm']} 胜方={g['winner_camp']} 轮数={g['rounds']} 场景={g['scene_tags']}\n"
                    f"整局时间线:\n{g.get('timeline', '')}"
                ),
                evidence=[{"game_id": g["game_id"]}],
            )
        )
    return GlobalBatchReview(n_games=n, stats=stats, samples=samples)


def _pick_global_games(game_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _take(pred):
        for g in game_summaries:
            if g["game_id"] in seen:
                continue
            if pred(g):
                picks.append(g)
                seen.add(g["game_id"])
                return

    _take(lambda g: g["winner_camp"] == "werewolf" and g["rounds"] <= 2)  # 碾压
    _take(lambda g: g["winner_camp"] == "villager" and g["rounds"] >= 4)  # 翻盘
    _take(lambda g: "tie_revote" in g["scene_tags"])  # 平票混战
    return picks
