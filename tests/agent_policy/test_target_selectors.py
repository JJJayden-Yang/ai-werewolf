from tests.fixtures.agent_contexts import (
    seer_context,
    tie_revote_context,
    vote_context,
    werewolf_context,
)

from agent_policy.target_selectors import (
    checked_targets_from_private_events,
    select_alive_non_self,
    select_tie_candidate,
    select_unchecked_player,
    select_wolf_kill_target,
)


def test_select_alive_non_self_never_returns_self_or_dead_player():
    context = vote_context()

    target = select_alive_non_self(context)

    assert target in {"P1", "P3", "P4"}


def test_select_wolf_kill_target_never_returns_teammate_or_dead_player():
    context = werewolf_context()

    target = select_wolf_kill_target(context)

    assert target in {"P3", "P4"}


def test_select_unchecked_player_skips_private_seer_history():
    context = seer_context()

    checked = checked_targets_from_private_events(context)
    target = select_unchecked_player(context)

    assert checked == {"P1"}
    assert target in {"P2", "P4"}


def test_select_tie_candidate_only_returns_tie_candidates():
    context = tie_revote_context()

    target = select_tie_candidate(context)

    assert target in {"P3", "P4"}
