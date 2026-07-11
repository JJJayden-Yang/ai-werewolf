from __future__ import annotations

import math

import pytest

from agent_policy.belief_math import ROLE_FIELDS
from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater
from contracts import BeliefState, Camp, EventType, Phase, Role, RoleBelief, Visibility
from agent_policy import realtime_belief_updater as updater_mod
from scripts._mixed_metrics import _snapshot_quality, _suspicion_entropy
from stores.belief_state_store import InMemoryBeliefStateStore
from stores.event_store import InMemoryEventStore
from tests.context.conftest import FakeSessionProvider, make_6p_session, make_event


def _uniform_role_belief() -> RoleBelief:
    return RoleBelief(werewolf=0.2, seer=0.2, witch=0.2, hunter=0.2, villager=0.2)


def _run_synthetic_claim_game(*, belief_kernel: str):
    session = make_6p_session(game_id=f"factorized_{belief_kernel}")
    for player in session.truth_state.players.values():
        player.camp = Camp.WEREWOLF if player.role.value == "werewolf" else Camp.VILLAGER
    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=event_store,
        belief_store=belief_store,
        session_provider=FakeSessionProvider(session),
        belief_kernel=belief_kernel,
    )

    events = [
        make_event(
            EventType.SPEECH,
            game_id=session.game_id,
            round_num=1,
            phase=Phase.DAY_DISCUSSION,
            actor="P3",
            visibility=Visibility.PUBLIC,
            payload={
                "public_message": "我是预言家，P1 是狼人。",
                "role_claim": "seer",
                "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
            },
        ),
        make_event(
            EventType.SPEECH,
            game_id=session.game_id,
            round_num=1,
            phase=Phase.DAY_DISCUSSION,
            actor="P1",
            visibility=Visibility.PUBLIC,
            payload={
                "public_message": "我是预言家，P2 是好人。",
                "role_claim": "seer",
                "claim_result": {"target": "P2", "claimed_alignment": "villager"},
            },
        ),
        make_event(
            EventType.VOTE_CAST,
            game_id=session.game_id,
            round_num=1,
            phase=Phase.DAY_VOTE,
            actor="P6",
            target="P1",
            visibility=Visibility.PUBLIC,
            payload={},
        ),
        make_event(
            EventType.SPEECH,
            game_id=session.game_id,
            round_num=2,
            phase=Phase.DAY_DISCUSSION,
            actor="P3",
            visibility=Visibility.PUBLIC,
            payload={
                "public_message": "第二天补充，P2 也是狼人。",
                "claim_result": {"target": "P2", "claimed_alignment": "werewolf"},
            },
        ),
    ]
    for event in events:
        event_store.append(event)
        updater.update(session.game_id, event.event_id)

    return session, belief_store.get(session.game_id, "P6")


def _assert_role_beliefs_are_valid(snapshot) -> None:
    for role_belief in snapshot.beliefs.values():
        probs = [float(getattr(role_belief, role)) for role in ROLE_FIELDS]
        assert all(math.isfinite(prob) for prob in probs)
        assert sum(probs) == pytest.approx(1.0)


def test_factorized_kernel_sharpens_synthetic_claim_scene():
    additive_session, additive = _run_synthetic_claim_game(belief_kernel="additive_v1")
    factorized_session, factorized = _run_synthetic_claim_game(belief_kernel="factorized_v2")
    alive = set(additive_session.truth_state.players)

    additive_quality = _snapshot_quality(
        additive,
        additive_session.truth_state,
        self_id="P6",
        alive=alive,
    )
    factorized_quality = _snapshot_quality(
        factorized,
        factorized_session.truth_state,
        self_id="P6",
        alive=alive,
    )

    assert additive_quality is not None
    assert factorized_quality is not None
    assert factorized_quality["entropy"] < additive_quality["entropy"]
    assert factorized_quality["separation"] > additive_quality["separation"]
    assert factorized_quality["brier"] <= additive_quality["brier"]

    additive_entropy = _suspicion_entropy(
        [belief.werewolf for pid, belief in additive.beliefs.items() if pid != "P6"]
    )
    factorized_entropy = _suspicion_entropy(
        [belief.werewolf for pid, belief in factorized.beliefs.items() if pid != "P6"]
    )
    assert factorized_entropy < additive_entropy
    _assert_role_beliefs_are_valid(additive)
    _assert_role_beliefs_are_valid(factorized)


def test_factorized_kernel_keeps_private_locked_beliefs_locked():
    session = make_6p_session(game_id="factorized_locked")
    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=event_store,
        belief_store=belief_store,
        session_provider=FakeSessionProvider(session),
        belief_kernel="factorized_v2",
    )
    events = [
        make_event(
            EventType.SEER_CHECK_RESULT,
            game_id=session.game_id,
            phase=Phase.NIGHT_SEER,
            actor="P3",
            target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "werewolf"},
        ),
        make_event(
            EventType.SPEECH,
            game_id=session.game_id,
            phase=Phase.DAY_DISCUSSION,
            actor="P1",
            visibility=Visibility.PUBLIC,
            payload={"role_claim": "seer"},
        ),
    ]

    for event in events:
        event_store.append(event)
        updater.update(session.game_id, event.event_id)

    locked = belief_store.get(session.game_id, "P3").beliefs["P1"]
    assert locked.locked is True
    assert locked.lock_reason == "seer_private_check_result"
    assert locked.werewolf == pytest.approx(0.95)


def test_factorized_role_claim_is_discounted_by_source_credibility(monkeypatch):
    session = make_6p_session(game_id="factorized_role_claim_cred")
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=InMemoryEventStore(),
        belief_store=InMemoryBeliefStateStore(),
        session_provider=FakeSessionProvider(session),
        belief_kernel="factorized_v2",
    )
    event = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        actor="P1",
        visibility=Visibility.PUBLIC,
        payload={"role_claim": "seer"},
    )

    low = BeliefState(
        game_id=session.game_id,
        agent_id="P6",
        beliefs={"P1": _uniform_role_belief()},
    )
    high = low.model_copy(deep=True)
    monkeypatch.setattr(updater_mod, "source_credibility_from_belief", lambda *_args, **_kw: 0.0)
    updater._apply_role_claim(
        low,
        event,
        observer_id="P6",
        observer_role=Role.VILLAGER,
        claimed_role=Role.SEER,
    )
    monkeypatch.setattr(updater_mod, "source_credibility_from_belief", lambda *_args, **_kw: 1.0)
    updater._apply_role_claim(
        high,
        event,
        observer_id="P6",
        observer_role=Role.VILLAGER,
        claimed_role=Role.SEER,
    )

    assert high.beliefs["P1"].seer > low.beliefs["P1"].seer
    assert high.beliefs["P1"].werewolf > low.beliefs["P1"].werewolf


def test_factorized_own_role_counterclaim_is_not_discounted(monkeypatch):
    session = make_6p_session(game_id="factorized_own_counterclaim")
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=InMemoryEventStore(),
        belief_store=InMemoryBeliefStateStore(),
        session_provider=FakeSessionProvider(session),
        belief_kernel="factorized_v2",
    )
    event = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        actor="P1",
        visibility=Visibility.PUBLIC,
        payload={"role_claim": "seer"},
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("own-role counterclaim must not use source credibility")

    monkeypatch.setattr(updater_mod, "source_credibility_from_belief", fail_if_called)
    belief = BeliefState(
        game_id=session.game_id,
        agent_id="P3",
        beliefs={"P1": _uniform_role_belief()},
    )

    updater._apply_role_claim(
        belief,
        event,
        observer_id="P3",
        observer_role=Role.SEER,
        claimed_role=Role.SEER,
    )

    assert belief.beliefs["P1"].werewolf > 0.2


def test_unknown_belief_kernel_is_rejected():
    session = make_6p_session(game_id="factorized_bad_kernel")
    with pytest.raises(ValueError):
        RuleBasedRealtimeBeliefUpdater(
            event_store=InMemoryEventStore(),
            belief_store=InMemoryBeliefStateStore(),
            session_provider=FakeSessionProvider(session),
            belief_kernel="unknown",
        )
