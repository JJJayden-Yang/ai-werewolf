"""Belief 曲线派生 —— 把 observer 的 BeliefState 历史压成可画的时间序列。

这是 ``api/audit_belief_curve.py`` 端点与离线分析**共用**的纯函数层。。纯函数、无 IO、可单测。

红线：
- ``separation`` 在 **observer 实时视角永远置 None** —— observer 不知真相，算 wolf/villager
  分离度会泄漏隐藏身份。赛后真相版由 ``PostGameAnalyzer`` 单独填（本层不做）。
- ``entropy`` / ``top_margin`` 复用 ``_mixed_metrics`` 的同名口径，保证曲线和批量聚合**同一套
  数学**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scripts._mixed_metrics import _suspicion_entropy

if TYPE_CHECKING:
    from contracts import BeliefState

_ROLE_DIMS = ("werewolf", "seer", "witch", "hunter", "villager")


def _phase_str(phase: Any) -> str:
    if phase is None:
        return ""
    return phase.value if hasattr(phase, "value") else str(phase)


def _role_belief_dims(role_belief: Any) -> dict[str, float]:
    """把一个 RoleBelief 读成 {dim: prob}，容忍 pydantic model 或裸 dict。"""
    out: dict[str, float] = {}
    for dim in _ROLE_DIMS:
        if isinstance(role_belief, dict):
            value = role_belief.get(dim, 0.0)
        else:
            value = getattr(role_belief, dim, 0.0)
        out[dim] = float(value) if isinstance(value, int | float) else 0.0
    return out


def belief_curve_series(
    history: list["BeliefState"],
    *,
    self_id: str | None = None,
) -> list[dict[str, Any]]:
    """把单个 observer 的 BeliefState 历史派生成 CurvePoint 列表。

    Args:
        history: 该 observer 的 belief 快照（时间序，real 或 shadow lane）。
        self_id: observer 自己的 agent_id；其对自己的 belief 不进 werewolf 维排序/熵。
            省略时从 ``history[0].agent_id`` 推断。

    Returns:
        list[CurvePoint]（dict 形态，JSON 友好），契约见接口文档 §2.3。

    注意：``by_target`` / 排序基于快照里 observer 持有 belief 的**全部 target**。本层只有
    history、没有事件流，无法在快照级别做存活过滤；dead player 的 werewolf 概率通常已被
    锁定/不再变化，按 observer 当时的认知如实呈现即可（observer 视角的诚实快照）。
    """
    if self_id is None and history:
        self_id = getattr(history[0], "agent_id", None)

    series: list[dict[str, Any]] = []
    for step, snapshot in enumerate(history):
        beliefs = getattr(snapshot, "beliefs", {}) or {}

        by_target: dict[str, dict[str, float]] = {}
        wolf_ranked: list[tuple[str, float]] = []
        for target_id, role_belief in beliefs.items():
            dims = _role_belief_dims(role_belief)
            by_target[target_id] = dims
            if target_id != self_id:
                wolf_ranked.append((target_id, dims["werewolf"]))

        wolf_ranked.sort(key=lambda item: (-item[1], item[0]))
        top1_target = wolf_ranked[0][0] if wolf_ranked else None
        top1_prob = wolf_ranked[0][1] if wolf_ranked else 0.0
        top2_prob = wolf_ranked[1][1] if len(wolf_ranked) >= 2 else 0.0
        top_margin = top1_prob - top2_prob

        wolf_probs = [prob for _, prob in wolf_ranked]
        entropy = _suspicion_entropy(wolf_probs)

        series.append(
            {
                "step": step,
                "round": getattr(snapshot, "round", None),
                "phase": _phase_str(getattr(snapshot, "phase", None)),
                "by_target": by_target,
                "top1_target": top1_target,
                "top1_prob": top1_prob,
                "top_margin": top_margin,
                # observer 视角不知真相 → 永远 None（红线）；赛后真相版另填。
                "separation": None,
                # _suspicion_entropy 在 <2 target 时返 None；曲线上用 0.0 表示「无可分散度」。
                "entropy": entropy if entropy is not None else 0.0,
            }
        )

    return series
