import pytest

from agent_policy.factorized_belief import (
    FactorizedEvidence,
    apply_factorized_evidence,
    apply_log_odds_update,
    source_credibility_from_belief,
)
from agent_policy.belief_math import belief_total
from contracts import Role, RoleBelief


def _uniform() -> RoleBelief:
    return RoleBelief(
        werewolf=0.2,
        seer=0.2,
        witch=0.2,
        hunter=0.2,
        villager=0.2,
    )


def test_log_odds_update_increases_target_and_keeps_normalized():
    updated = apply_log_odds_update(
        _uniform(),
        target_role=Role.WEREWOLF,
        log_odds_delta=1.0,
    )

    assert updated.werewolf > 0.2
    assert belief_total(updated) == pytest.approx(1.0)
    assert updated.seer == pytest.approx(updated.witch)


def test_log_odds_update_can_reduce_target_probability():
    updated = apply_log_odds_update(
        _uniform(),
        target_role=Role.WEREWOLF,
        log_odds_delta=-1.0,
    )

    assert updated.werewolf < 0.2
    assert belief_total(updated) == pytest.approx(1.0)


def test_factorized_evidence_uses_source_credibility():
    high_cred = apply_factorized_evidence(
        _uniform(),
        FactorizedEvidence(
            target_role=Role.WEREWOLF,
            base_weight=1.0,
            source_credibility=0.9,
            credibility_lambda=1.0,
        ),
    )
    low_cred = apply_factorized_evidence(
        _uniform(),
        FactorizedEvidence(
            target_role=Role.WEREWOLF,
            base_weight=1.0,
            source_credibility=0.1,
            credibility_lambda=1.0,
        ),
    )

    assert high_cred.werewolf > low_cred.werewolf > 0.2


def test_factorized_evidence_respects_locked_belief():
    locked = RoleBelief(
        werewolf=1.0,
        seer=0.0,
        witch=0.0,
        hunter=0.0,
        villager=0.0,
        locked=True,
        lock_reason="test_lock",
    )

    updated = apply_factorized_evidence(
        locked,
        FactorizedEvidence(target_role=Role.WEREWOLF, base_weight=-10.0),
    )

    assert updated == locked


def test_source_credibility_uses_claimed_role_or_generic_trust():
    speaker = RoleBelief(
        werewolf=0.3,
        seer=0.45,
        witch=0.1,
        hunter=0.1,
        villager=0.05,
    )

    assert source_credibility_from_belief(speaker, claimed_role=Role.SEER) == pytest.approx(0.45)
    assert source_credibility_from_belief(speaker) == pytest.approx(0.7)
