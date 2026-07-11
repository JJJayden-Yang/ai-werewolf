"""Belief 驱动的目标选择工具。

这些 selector 只读取 AgentContext 中已经注入的 belief_top_suspects，
不读取 BeliefStateStore / TruthState / GameSession。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from contracts import AgentContext, BeliefState, PlayerStatus, Role

_ROLE_TO_FIELD: dict[Role, str] = {
    Role.WEREWOLF: "werewolf",
    Role.SEER: "seer",
    Role.WITCH: "witch",
    Role.HUNTER: "hunter",
    Role.VILLAGER: "villager",
}


def select_top_belief_suspect(
    context: AgentContext,
    *,
    min_werewolf_prob: float,
    candidate_ids: Iterable[str] | None = None,
) -> str | None:
    """选择 belief_top_suspects 中最高嫌疑的存活非自己玩家。

    `belief_top_suspects` 由 C 的 ContextAssembler 注入，常见形状：
    `{"player_id": "P2", "werewolf_prob": 0.7}`。
    这里把 belief 当作参考信号，不把它视为真实身份。
    """
    alive_ids = {
        player.player_id
        for player in context.visible_players
        if player.status == PlayerStatus.ALIVE
    }
    allowed_candidates = set(candidate_ids) if candidate_ids is not None else None

    best_player: str | None = None
    best_prob = min_werewolf_prob

    for item in context.belief_top_suspects:
        player_id = _read_player_id(item)
        if not player_id or player_id == context.agent_id or player_id not in alive_ids:
            continue
        if allowed_candidates is not None and player_id not in allowed_candidates:
            continue

        werewolf_prob = _read_werewolf_prob(item)
        if werewolf_prob is None or werewolf_prob < min_werewolf_prob:
            continue
        if best_player is None or werewolf_prob > best_prob:
            best_player = player_id
            best_prob = werewolf_prob

    return best_player


def top_suspects_by_role(
    belief_state: BeliefState,
    role: Role,
    *,
    k: int,
    alive_set: set[str],
    exclude: set[str] | None = None,
) -> list[tuple[str, float]]:
    """Return top-k live player suspects for any role probability dimension."""
    if k <= 0:
        return []
    field = _ROLE_TO_FIELD[role]
    excluded = exclude or set()
    ranked: list[tuple[str, float]] = []
    for player_id, role_belief in belief_state.beliefs.items():
        if player_id not in alive_set or player_id in excluded:
            continue
        ranked.append((player_id, float(getattr(role_belief, field))))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked[:k]


# --------------------------------------------------------------------------- #
# 置信档派生（tier helper）
# --------------------------------------------------------------------------- #
#
# 实测结论（a4_effect_analysis §8.2）：belief 的 **rank 有信息**（top1 命中 0.64、
# top2 ~0.85），但 **calibration 平**（top_margin ~0.07、熵 ~0.97）。所以 belief 不该当
# "概率" 读，应当 "suspicion 排序 + 置信档" 读 —— 这个 helper 把一个 BeliefState 派生成
# top1/top2/margin + 一个离散置信档，供 prompt 用校准过的语言表达（见
# prompts/shared/v1_belief_guidance.md），而不是把 0.30 当 "30% 是狼" 念。

# margin 阈值默认值来自观测分布（典型 margin ~0.07）：多数局面落 "flat"（belief 诚实地
# 不确定），少数尖锐局面才到 "lean"/"strong"。阈值是旋钮，可由 caller 覆盖。
_DEFAULT_STRONG_MARGIN = 0.15
_DEFAULT_LEAN_MARGIN = 0.05


@dataclass(frozen=True)
class SuspicionTiers:
    """一个 observer 对某角色维度的 top-2 嫌疑 + 置信档。

    - ``top1`` / ``top2``：``(player_id, prob)`` 或 None（存活候选不足时）。
    - ``margin``：``top1_prob - top2_prob``（无 top2 记 top1_prob - 0）。
    - ``tier``：``"strong"``（margin 大、可据此果断）｜``"lean"``（有倾向但不强）｜
      ``"flat"``（分不开，belief 诚实地不确定 —— 别假装有嫌疑人）。
    """

    top1: tuple[str, float] | None
    top2: tuple[str, float] | None
    margin: float
    tier: str


def derive_suspicion_tiers(
    belief_state: BeliefState,
    role: Role,
    *,
    alive_set: set[str],
    exclude: set[str] | None = None,
    strong_margin: float = _DEFAULT_STRONG_MARGIN,
    lean_margin: float = _DEFAULT_LEAN_MARGIN,
) -> SuspicionTiers:
    """从 belief 派生 top1/top2/margin + 置信档（把 rank 信号转成可用的离散判断）。"""
    ranked = top_suspects_by_role(
        belief_state, role, k=2, alive_set=alive_set, exclude=exclude
    )
    top1 = ranked[0] if ranked else None
    top2 = ranked[1] if len(ranked) >= 2 else None
    margin = (top1[1] - (top2[1] if top2 is not None else 0.0)) if top1 is not None else 0.0

    if top1 is None:
        tier = "flat"
    elif margin >= strong_margin:
        tier = "strong"
    elif margin >= lean_margin:
        tier = "lean"
    else:
        tier = "flat"

    return SuspicionTiers(top1=top1, top2=top2, margin=margin, tier=tier)


def _read_player_id(item: Any) -> str | None:
    if isinstance(item, dict):
        value = item.get("player_id")
        return value if isinstance(value, str) else None
    value = getattr(item, "player_id", None)
    return value if isinstance(value, str) else None


def _read_werewolf_prob(item: Any) -> float | None:
    if isinstance(item, dict):
        value = item.get("werewolf_prob")
    else:
        value = getattr(item, "werewolf_prob", None)
    if isinstance(value, int | float):
        return float(value)
    return None
