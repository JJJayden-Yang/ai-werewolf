from contracts import BeliefState, Phase, RoleBelief

from agent_policy.belief_math import (
    ROLE_FIELDS,
    apply_delta_and_normalize,
    belief_total,
    create_empty_belief_state,
    normalize_role_belief,
)


def test_apply_delta_clamps_low_values_and_normalizes():
    belief = RoleBelief(werewolf=0.10, seer=0.20, witch=0.20, hunter=0.20, villager=0.30)

    updated = apply_delta_and_normalize(
        belief,
        {"werewolf": -9.0, "villager": 0.40},
    )

    assert updated.werewolf >= 0.0
    assert updated.villager <= 1.0
    assert belief_total(updated) == 1.0


def test_apply_delta_clamps_high_values_and_normalizes():
    belief = RoleBelief(werewolf=0.20, seer=0.20, witch=0.20, hunter=0.20, villager=0.20)

    updated = apply_delta_and_normalize(
        belief,
        {"werewolf": 9.0, "seer": -0.10},
    )

    assert updated.werewolf <= 1.0
    assert updated.seer >= 0.0
    assert belief_total(updated) == 1.0


def test_normalize_distributes_evenly_when_unlocked_sum_is_zero():
    belief = RoleBelief(werewolf=0.0, seer=0.0, witch=0.0, hunter=0.0, villager=0.0)

    normalized = normalize_role_belief(belief)

    for role in ROLE_FIELDS:
        assert getattr(normalized, role) == 0.2
    assert belief_total(normalized) == 1.0


def test_locked_belief_is_not_modified_by_common_delta():
    belief = RoleBelief(
        werewolf=0.95,
        seer=0.01,
        witch=0.01,
        hunter=0.01,
        villager=0.02,
        locked=True,
        lock_reason="seer_private_check_result",
    )

    updated = apply_delta_and_normalize(
        belief,
        {"werewolf": -0.80, "villager": 0.80},
    )

    assert updated == belief


def test_conflicting_claim_delta_does_not_create_negative_probabilities():
    belief = RoleBelief(werewolf=0.18, seer=0.10, witch=0.20, hunter=0.20, villager=0.32)

    updated = apply_delta_and_normalize(
        belief,
        {"seer": -0.50, "werewolf": 0.12},
    )

    assert min(getattr(updated, role) for role in ROLE_FIELDS) >= 0.0
    assert belief_total(updated) == 1.0


def test_create_empty_shadow_belief_state_for_v0_deviation():
    belief_state = create_empty_belief_state(
        game_id="g001",
        agent_id="P1",
        is_shadow=True,
        round=1,
        phase=Phase.DAY_VOTE,
    )

    assert isinstance(belief_state, BeliefState)
    assert belief_state.is_shadow is True
    assert belief_state.beliefs == {}
    assert belief_state.phase == Phase.DAY_VOTE


def test_belief_rules_yaml_contains_first_stage_required_rules():
    rules_text = open("agent_policy/belief_rules_v1.yaml", encoding="utf-8").read()

    for required_text in [
        "version: belief_rules_v1",
        "private_confirmations:",
        "seer_check_werewolf:",
        "seer_check_villager:",
        "wolf_teammate:",
        "public_claims:",
        "claim_seer:",
        "claim_witch:",
        "claim_hunter:",
        "claim_villager:",
        "claim_check_werewolf:",
        "claim_check_villager:",
        "conflicting_claims:",
        "claim_observers_own_unique_role:",
        "second_claim_same_unique_role:",
        "public_events:",
        "night_killed:",
        "vote_cast:",
        'scope: "post_game_only"',
    ]:
        assert required_text in rules_text
