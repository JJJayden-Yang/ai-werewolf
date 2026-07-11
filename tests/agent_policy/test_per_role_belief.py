from __future__ import annotations

import pytest

from agent_policy.belief_math import ROLE_FIELDS
from agent_policy.belief_selectors import top_suspects_by_role
from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater
from contracts import Camp, EventType, Phase, Role, Visibility
from scripts._mixed_metrics import compute_belief_signal
from stores.belief_state_store import InMemoryBeliefStateStore
from stores.event_store import InMemoryEventStore
from tests.context.conftest import FakeSessionProvider, make_6p_session, make_event


def _wire(*, game_id: str, belief_kernel: str = "factorized_v2"):
    session = make_6p_session(game_id=game_id)
    for player in session.truth_state.players.values():
        player.camp = Camp.WEREWOLF if player.role == Role.WEREWOLF else Camp.VILLAGER
    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=event_store,
        belief_store=belief_store,
        session_provider=FakeSessionProvider(session),
        belief_kernel=belief_kernel,
    )
    return session, event_store, belief_store, updater


def _append_and_update(event_store, updater, event):
    event_store.append(event)
    updater.update(event.game_id, event.event_id)
    return event


def _seer_claim_events(game_id: str):
    return [
        make_event(
            EventType.SPEECH,
            game_id=game_id,
            round_num=1,
            phase=Phase.DAY_DISCUSSION,
            actor="P3",
            visibility=Visibility.PUBLIC,
            payload={
                "role_claim": "seer",
                "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
            },
        ),
        make_event(
            EventType.SPEECH,
            game_id=game_id,
            round_num=2,
            phase=Phase.DAY_DISCUSSION,
            actor="P3",
            visibility=Visibility.PUBLIC,
            payload={"claim_result": {"target": "P1", "claimed_alignment": "werewolf"}},
        ),
    ]


def _assert_normalized(snapshot):
    for role_belief in snapshot.beliefs.values():
        total = sum(float(getattr(role_belief, field)) for field in ROLE_FIELDS)
        assert total == pytest.approx(1.0)


def test_factorized_seer_claim_makes_true_seer_top_role_suspect_and_improves_metric():
    session, event_store, belief_store, updater = _wire(game_id="m3_seer_signal")
    events = [
        _append_and_update(event_store, updater, event)
        for event in _seer_claim_events(session.game_id)
    ]
    p6_view = belief_store.get(session.game_id, "P6")

    ranked = top_suspects_by_role(
        p6_view,
        Role.SEER,
        k=1,
        alive_set=set(session.truth_state.players),
        exclude={"P6"},
    )
    assert ranked[0][0] == "P3"

    signal = compute_belief_signal(
        game_id=session.game_id,
        injected_agents=["P6"],
        belief_store=belief_store,
        truth_state=session.truth_state,
        traces=[],
        events=events,
    )
    assert signal is not None
    assert signal["per_role_identification"]["seer_identification_accuracy"] == 1.0

    baseline_session, baseline_events, baseline_store, baseline_updater = _wire(
        game_id="m3_seer_baseline"
    )
    noop = _append_and_update(
        baseline_events,
        baseline_updater,
        make_event(
            EventType.SPEECH,
            game_id=baseline_session.game_id,
            actor="P1",
            phase=Phase.DAY_DISCUSSION,
            visibility=Visibility.PUBLIC,
            payload={"public_message": "no role information"},
        ),
    )
    baseline_signal = compute_belief_signal(
        game_id=baseline_session.game_id,
        injected_agents=["P6"],
        belief_store=baseline_store,
        truth_state=baseline_session.truth_state,
        traces=[],
        events=[noop],
    )
    assert baseline_signal is not None
    assert (
        signal["per_role_identification"]["seer_identification_accuracy"]
        > baseline_signal["per_role_identification"]["seer_identification_accuracy"]
    )
    _assert_normalized(p6_view)


def test_wolf_view_can_rank_true_seer_for_hunting():
    session, event_store, belief_store, updater = _wire(game_id="m3_wolf_hunt_seer")
    for event in _seer_claim_events(session.game_id):
        _append_and_update(event_store, updater, event)

    wolf_view = belief_store.get(session.game_id, "P1")
    ranked = top_suspects_by_role(
        wolf_view,
        Role.SEER,
        k=1,
        alive_set=set(session.truth_state.players),
        exclude={"P1"},
    )

    assert ranked[0][0] == "P3"


def test_tom_contradictory_check_claim_lowers_seer_and_raises_werewolf():
    session, event_store, belief_store, updater = _wire(game_id="m3_tom_contradiction")
    first = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        actor="P1",
        visibility=Visibility.PUBLIC,
        payload={
            "role_claim": "seer",
            "claim_result": {"target": "P2", "claimed_alignment": "werewolf"},
        },
    )
    second = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        actor="P1",
        visibility=Visibility.PUBLIC,
        payload={"claim_result": {"target": "P2", "claimed_alignment": "villager"}},
    )

    _append_and_update(event_store, updater, first)
    after_first = belief_store.get(session.game_id, "P6").beliefs["P1"]
    _append_and_update(event_store, updater, second)
    after_second = belief_store.get(session.game_id, "P6").beliefs["P1"]

    assert after_second.seer < after_first.seer
    assert after_second.werewolf > after_first.werewolf


def test_hunter_shot_locks_actor_as_hunter():
    session, event_store, belief_store, updater = _wire(game_id="m3_hunter_lock")
    event = make_event(
        EventType.HUNTER_SHOT,
        game_id=session.game_id,
        actor="P5",
        target="P1",
        phase=Phase.HUNTER_SHOOT,
        visibility=Visibility.PUBLIC,
        payload={},
    )

    _append_and_update(event_store, updater, event)

    p6_view = belief_store.get(session.game_id, "P6").beliefs["P5"]
    assert p6_view.locked is True
    assert p6_view.lock_reason == "hunter_shot_confirmed"
    assert p6_view.hunter == pytest.approx(1.0)


def test_dead_player_excluded_from_live_role_suspects_via_alive_set():
    # 复审 F2 修复后口径：死者不再算"存活角色嫌疑"——由 selector 的 alive_set 过滤保证，
    # 而不是靠对死者 belief 动手脚（引擎真实死亡事件不带 payload.role，原 revealed-role
    # 分支在真实对局里永不触发，已删）。这里走真实 update()，再用 alive_set 排除死者。
    session, event_store, belief_store, updater = _wire(game_id="m3_dead_excluded")
    for event in _seer_claim_events(session.game_id):
        _append_and_update(event_store, updater, event)
    p6_view = belief_store.get(session.game_id, "P6")

    living = set(session.truth_state.players)
    ranked_all = top_suspects_by_role(
        p6_view, Role.SEER, k=1, alive_set=living, exclude={"P6"}
    )
    assert ranked_all[0][0] == "P3"  # P3 活着时是头号预言家嫌疑

    living_without_p3 = living - {"P3"}
    ranked_after_death = top_suspects_by_role(
        p6_view, Role.SEER, k=3, alive_set=living_without_p3, exclude={"P6"}
    )
    assert all(pid != "P3" for pid, _ in ranked_after_death)  # P3 死后不再入选
