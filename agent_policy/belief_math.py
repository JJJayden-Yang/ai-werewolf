"""Belief 概率数学工具。

本模块只负责 B 侧 belief 数学闭合：
- 应用 delta；
- clamp 到 [0, 1]；
- normalize 到总和为 1；
- locked belief 保护。

它不读取 TruthState，不读写 Store，也不做事件解析。
"""

from __future__ import annotations

from collections.abc import Mapping

from contracts import BeliefState, Phase, RoleBelief


ROLE_FIELDS: tuple[str, ...] = ("werewolf", "seer", "witch", "hunter", "villager")


def clamp_probability(value: float) -> float:
    """把概率限制在 [0, 1]。"""
    return max(0.0, min(1.0, value))


def belief_total(belief: RoleBelief) -> float:
    """返回角色概率总和，四舍五入避免浮点尾差影响测试和日志。"""
    return round(sum(getattr(belief, role) for role in ROLE_FIELDS), 10)


def normalize_role_belief(belief: RoleBelief) -> RoleBelief:
    """返回归一化后的 RoleBelief，不修改入参。"""
    if belief.locked:
        return belief.model_copy(deep=True)

    values = {
        role: clamp_probability(float(getattr(belief, role)))
        for role in ROLE_FIELDS
    }
    total = sum(values.values())
    if total == 0:
        normalized = {role: 1.0 / len(ROLE_FIELDS) for role in ROLE_FIELDS}
    else:
        normalized = {role: value / total for role, value in values.items()}

    return RoleBelief(
        **normalized,
        locked=belief.locked,
        lock_reason=belief.lock_reason,
    )


def apply_delta_and_normalize(
    belief: RoleBelief,
    delta: Mapping[str, float],
) -> RoleBelief:
    """应用 delta 后归一化，locked belief 保持完全不变。"""
    if belief.locked:
        return belief.model_copy(deep=True)

    values = {
        role: float(getattr(belief, role))
        for role in ROLE_FIELDS
    }
    for role, value in delta.items():
        if role in values:
            values[role] = clamp_probability(values[role] + float(value))

    return normalize_role_belief(
        RoleBelief(
            **values,
            locked=belief.locked,
            lock_reason=belief.lock_reason,
        )
    )


def create_empty_belief_state(
    *,
    game_id: str,
    agent_id: str,
    is_shadow: bool = False,
    round: int | None = None,
    phase: Phase | None = None,
    last_updated_event_id: str | None = None,
) -> BeliefState:
    """创建空 BeliefState，供 v0 shadow belief 或初始化测试使用。"""
    return BeliefState(
        game_id=game_id,
        agent_id=agent_id,
        round=round,
        phase=phase,
        is_shadow=is_shadow,
        beliefs={},
        last_updated_event_id=last_updated_event_id,
    )
