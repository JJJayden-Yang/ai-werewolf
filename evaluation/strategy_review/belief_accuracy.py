"""belief 命中率指标（纯计算，不走 LLM）。

口径**唯一真源**是 ``scripts/_mixed_metrics.py::compute_belief_signal``——它产出
``top_suspect_accuracy`` / ``avg_brier`` / ``top2_accuracy`` 等。本模块只做两件事：

1. 为赛后离线场景准备它的入参（从 ``replay_truth`` 重建 ``TruthState``；用真实 belief lane
   是否非空判定 ``injected_agents`` —— 这是「有没有 belief」的**真值**，不靠版本标签）。
2. 把逐局信号按 **角色 × arm** 聚合（按原始计数求和后再算率，避免对不同分母的率直接平均）。

见 ``docs/strategy_review_loop.md §2.2 / §6.1``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts.enums import Phase
from contracts.schemas import PlayerState, TruthState
from scripts._mixed_metrics import compute_belief_signal

ROLE_ALL = "all"


# --------------------------------------------------------------------------- #
# 赛后入参重建
# --------------------------------------------------------------------------- #


def build_truth_state(game_id: str, players: list[dict[str, Any]], events: list[Any]) -> TruthState:
    """从 ``replay_truth`` 的 players 列表重建 ``TruthState``（仅 belief 计算用）。

    ``compute_belief_signal`` 只读 players 的 role/camp（判真狼）与 round/phase（fallback），
    存活集由它内部按 events 的死亡点重建，不依赖 players 的 final status。
    """
    pmap: dict[str, PlayerState] = {}
    for p in players:
        pid = p.get("player_id")
        if not pid:
            continue
        pmap[pid] = PlayerState.model_validate(p)
    round_, phase = _final_round_phase(events)
    return TruthState(game_id=game_id, round=round_, phase=phase, players=pmap)


def _final_round_phase(events: list[Any]) -> tuple[int, Phase]:
    for ev in reversed(events):
        rnd = getattr(ev, "round", None)
        ph = getattr(ev, "phase", None)
        if rnd is not None and ph is not None:
            return int(rnd), ph
    return 1, Phase.INIT


def injected_agents(game_id: str, agent_ids: list[str], belief_store: Any) -> list[str]:
    """真有 belief 注入的 agent = 真实 lane（is_shadow=False）历史非空。

    v0 只写 shadow lane，real 为空 → 不计入；v1/v2 写 real → 计入。这是「有没有 belief」的真值。
    """
    out: list[str] = []
    for aid in agent_ids:
        try:
            history = belief_store.get_history(game_id, aid, is_shadow=False)
        except Exception:
            history = []
        if history:
            out.append(aid)
    return sorted(out)


# --------------------------------------------------------------------------- #
# 按 角色 × arm 聚合
# --------------------------------------------------------------------------- #


@dataclass
class BeliefRoleArmStat:
    """一个 (角色, arm) 桶的 belief 命中率累加器。"""

    role: str
    arm: str
    n_games: int = 0
    decisions: int = 0  # top_suspect_decisions
    top1_hits: int = 0  # top_suspect 实际是真狼
    top2_hits: int = 0
    consistency_matches: int = 0  # 决策 target 落在头号嫌疑
    _brier_weighted_sum: float = 0.0
    _brier_samples: int = 0

    def add_signal(self, sig: dict[str, Any]) -> None:
        self.n_games += 1
        self.decisions += int(sig.get("top_suspect_decisions") or 0)
        self.top1_hits += int(sig.get("top_suspect_hits_true_wolf") or 0)
        self.top2_hits += int(sig.get("top2_hits_true_wolf") or 0)
        self.consistency_matches += int(sig.get("decision_matches_top_suspect") or 0)
        quality = sig.get("belief_quality") or {}
        samples = int(quality.get("samples") or 0)
        avg_brier = quality.get("avg_brier")
        if samples and avg_brier is not None:
            self._brier_weighted_sum += float(avg_brier) * samples
            self._brier_samples += samples

    @staticmethod
    def _rate(num: int, den: int) -> float | None:
        return (num / den) if den else None

    @property
    def top1_accuracy(self) -> float | None:
        return self._rate(self.top1_hits, self.decisions)

    @property
    def top2_accuracy(self) -> float | None:
        return self._rate(self.top2_hits, self.decisions)

    @property
    def consistency_rate(self) -> float | None:
        return self._rate(self.consistency_matches, self.decisions)

    @property
    def avg_brier(self) -> float | None:
        return self._rate_float(self._brier_weighted_sum, self._brier_samples)

    @staticmethod
    def _rate_float(num: float, den: int) -> float | None:
        return (num / den) if den else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "arm": self.arm,
            "n_games": self.n_games,
            "decisions": self.decisions,
            "top1_accuracy": self.top1_accuracy,
            "top2_accuracy": self.top2_accuracy,
            "consistency_rate": self.consistency_rate,
            "avg_brier": self.avg_brier,
        }


@dataclass
class BeliefAccuracyReport:
    """belief 命中率报告：按 (角色, arm) 拆分 + 每 arm 的 all 汇总。"""

    rows: list[BeliefRoleArmStat] = field(default_factory=list)
    games_with_belief: int = 0
    games_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "games_total": self.games_total,
            "games_with_belief": self.games_with_belief,
            "rows": [r.to_dict() for r in self.rows],
        }


@dataclass
class GameBeliefInput:
    """一局算 belief 命中率所需的全部赛后输入。"""

    game_id: str
    arm: str
    role_map: dict[str, str]  # player_id -> role value
    players: list[dict[str, Any]]
    traces: list[Any]
    events: list[Any]


def compute_belief_accuracy(games: list[GameBeliefInput], belief_store: Any) -> BeliefAccuracyReport:
    """对多局算 belief 命中率，按 (角色, arm) 聚合。

    每局：判定注入 agent → 重建 truth_state → 对「该 arm 下、每个角色子集」各调一次
    ``compute_belief_signal``，再 add 进对应桶；额外算一个 role=all 的汇总。
    无注入的局自动跳过（不计入 games_with_belief）。
    """
    buckets: dict[tuple[str, str], BeliefRoleArmStat] = {}
    report = BeliefAccuracyReport(games_total=len(games))

    def _bucket(role: str, arm: str) -> BeliefRoleArmStat:
        key = (role, arm)
        if key not in buckets:
            buckets[key] = BeliefRoleArmStat(role=role, arm=arm)
        return buckets[key]

    for g in games:
        injected = injected_agents(g.game_id, list(g.role_map.keys()), belief_store)
        if not injected:
            continue
        report.games_with_belief += 1
        truth_state = build_truth_state(g.game_id, g.players, g.events)

        # role=all：全部注入 agent 一次
        sig_all = _signal(g, injected, belief_store, truth_state)
        if sig_all:
            _bucket(ROLE_ALL, g.arm).add_signal(sig_all)

        # 每个角色：仅该角色的注入 agent
        roles_present = {g.role_map.get(a) for a in injected}
        roles_present.discard(None)
        for role in sorted(r for r in roles_present if r):
            role_agents = [a for a in injected if g.role_map.get(a) == role]
            sig = _signal(g, role_agents, belief_store, truth_state)
            if sig:
                _bucket(role, g.arm).add_signal(sig)

    report.rows = sorted(buckets.values(), key=lambda r: (r.role, r.arm))
    return report


def _signal(
    g: GameBeliefInput, agents: list[str], belief_store: Any, truth_state: TruthState
) -> dict[str, Any] | None:
    if not agents:
        return None
    return compute_belief_signal(
        game_id=g.game_id,
        injected_agents=agents,
        belief_store=belief_store,
        truth_state=truth_state,
        traces=g.traces,
        events=g.events,
    )
