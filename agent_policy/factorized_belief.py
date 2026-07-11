"""Factorized belief update scaffold for future v2 experiments.

v1 uses additive deltas from ``belief_rules_v1.yaml``.  This module is not
wired into realtime games by default; it provides the small, testable math
kernel described in the v2 design notes:

    effective_weight = base_weight * direction * credibility ** lambda
    logit(P(role)) += effective_weight

The remaining role probabilities are rescaled proportionally so the full
RoleBelief stays normalized without hand-written clamp tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log

from contracts import Role, RoleBelief

from agent_policy.belief_math import ROLE_FIELDS, normalize_role_belief


_EPSILON = 1e-6
_ROLE_TO_FIELD: dict[Role, str] = {
    Role.WEREWOLF: "werewolf",
    Role.SEER: "seer",
    Role.WITCH: "witch",
    Role.HUNTER: "hunter",
    Role.VILLAGER: "villager",
}


@dataclass(frozen=True)
class FactorizedEvidence:
    """One v2 evidence item after event parsing.

    ``base_weight`` is the expert/later-learned knob for the evidence type.
    ``direction`` should be +1 for supporting evidence and -1 for contradicting
    evidence.  ``source_credibility`` is computed from the observer's current
    BeliefState, not from TruthState.
    """

    target_role: Role
    base_weight: float
    direction: int = 1
    source_credibility: float = 1.0
    credibility_lambda: float = 0.5


def apply_factorized_evidence(
    belief: RoleBelief,
    evidence: FactorizedEvidence,
) -> RoleBelief:
    """Apply one factorized log-odds update to a RoleBelief.

    Locked beliefs are returned unchanged, matching v1 semantics for hard
    private confirmations.
    """
    if belief.locked:
        return belief.model_copy(deep=True)

    direction = 1 if evidence.direction >= 0 else -1
    credibility = _clamp_probability(evidence.source_credibility)
    lambda_ = max(0.0, float(evidence.credibility_lambda))
    effective_weight = float(evidence.base_weight) * direction * (credibility ** lambda_)
    return apply_log_odds_update(
        belief,
        target_role=evidence.target_role,
        log_odds_delta=effective_weight,
    )


def apply_log_odds_update(
    belief: RoleBelief,
    *,
    target_role: Role,
    log_odds_delta: float,
) -> RoleBelief:
    """Update one role probability in log-odds space and renormalize others.

    The target role gets ``logit(p) += log_odds_delta``.  Non-target roles keep
    their relative ratios while sharing the remaining probability mass.
    """
    if belief.locked:
        return belief.model_copy(deep=True)

    target_field = _ROLE_TO_FIELD[target_role]
    current_target = _clamp_probability(float(getattr(belief, target_field)))
    updated_target = _sigmoid(_logit(current_target) + float(log_odds_delta))

    old_remainder = max(_EPSILON, 1.0 - current_target)
    new_remainder = max(0.0, 1.0 - updated_target)
    values: dict[str, float] = {}
    for field in ROLE_FIELDS:
        if field == target_field:
            values[field] = updated_target
        else:
            values[field] = float(getattr(belief, field)) / old_remainder * new_remainder

    return normalize_role_belief(
        RoleBelief(
            **values,
            locked=belief.locked,
            lock_reason=belief.lock_reason,
        )
    )


def source_credibility_from_belief(
    speaker_belief: RoleBelief,
    *,
    claimed_role: Role | None = None,
) -> float:
    """Derive source credibility from the observer's current speaker belief.

    For role-specific claims, use the probability that the speaker has that
    role.  Otherwise use non-werewolf probability as a generic trust proxy.
    """
    if claimed_role is not None:
        return _clamp_probability(float(getattr(speaker_belief, _ROLE_TO_FIELD[claimed_role])))
    return _clamp_probability(1.0 - float(speaker_belief.werewolf))


def _logit(probability: float) -> float:
    p = min(1.0 - _EPSILON, max(_EPSILON, probability))
    return log(p / (1.0 - p))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


def _clamp_probability(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
