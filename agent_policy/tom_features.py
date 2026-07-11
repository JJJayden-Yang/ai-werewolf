"""Lightweight rule-based Theory-of-Mind features for belief v2.

This module only derives cheap consistency signals from public claims. It does
not call an LLM and does not read TruthState.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts import ClaimedAlignment


@dataclass(frozen=True)
class TomEvidence:
    actor_id: str
    seer_delta: float = 0.0
    werewolf_delta: float = 0.0
    reason: str = ""


ClaimHistory = dict[str, dict[str, str]]


def seer_claim_result_consistency(
    *,
    actor_id: str,
    target_id: str,
    claimed_alignment: str,
    prior_claims: ClaimHistory,
) -> TomEvidence:
    """Score whether a claimed seer's new check is self-consistent.

    The strong, reliable signal here is **contradiction**: re-reporting the same
    target with the opposite alignment exposes a liar (seer-, werewolf+).

    Re-reporting the same target with the *same* alignment is only a weak signal:
    a genuine seer normally checks a *new* target each night and rarely re-reports
    an old one, so "repeat same" is not strongly seer-like — hence a small +.
    First claims carry no consistency signal (the "acting like a seer" prior is
    handled separately by the claim_result_actor rule).
    """
    prior_by_target = prior_claims.get(actor_id, {})
    prior_alignment = prior_by_target.get(target_id)
    if prior_alignment is None:
        return TomEvidence(actor_id=actor_id, reason="first_check_claim")
    if prior_alignment == claimed_alignment:
        return TomEvidence(actor_id=actor_id, seer_delta=0.10, reason="consistent_check_claim")
    if {
        prior_alignment,
        claimed_alignment,
    } <= {ClaimedAlignment.WEREWOLF.value, ClaimedAlignment.VILLAGER.value}:
        return TomEvidence(
            actor_id=actor_id,
            seer_delta=-0.90,
            werewolf_delta=0.90,
            reason="contradictory_check_claim",
        )
    return TomEvidence(actor_id=actor_id, reason="uninformative_check_claim")
