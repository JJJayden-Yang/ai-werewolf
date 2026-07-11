"""RuleBasedRealtimeBeliefUpdater tests.

These tests focus on the runtime belief lane used by mock agents.  The updater
must keep belief agent-local: the same public event may produce different
belief for different observers, while private events must not leak.
"""

from __future__ import annotations

import inspect

import pytest

from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater
from contracts import EventType, Phase, Role, Visibility
from stores.belief_observability_store import InMemoryBeliefObservabilityStore
from stores.belief_state_store import InMemoryBeliefStateStore
from stores.event_store import InMemoryEventStore
from stores.exceptions import BeliefStateNotFoundError
from tests.context.conftest import FakeSessionProvider, make_6p_session, make_event


def _wire(session):
    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=event_store,
        belief_store=belief_store,
        session_provider=FakeSessionProvider(session),
    )
    return event_store, belief_store, updater


def _wire_with_rules(session, rules):
    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=event_store,
        belief_store=belief_store,
        session_provider=FakeSessionProvider(session),
        rules=rules,
    )
    return event_store, belief_store, updater


def _wire_shadow(session):
    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=event_store,
        belief_store=belief_store,
        session_provider=FakeSessionProvider(session),
        is_shadow=True,
    )
    return event_store, belief_store, updater


def _wire_observable(session):
    event_store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    observability_store = InMemoryBeliefObservabilityStore()
    updater = RuleBasedRealtimeBeliefUpdater(
        event_store=event_store,
        belief_store=belief_store,
        session_provider=FakeSessionProvider(session),
        observability_store=observability_store,
    )
    return event_store, belief_store, observability_store, updater


def _append_and_update(event_store, updater, event):
    event_store.append(event)
    updater.update(event.game_id, event.event_id)


def test_seer_check_result_only_updates_seer_belief():
    session = make_6p_session(game_id="belief_seer")
    event_store, belief_store, updater = _wire(session)
    event = make_event(
        EventType.SEER_CHECK_RESULT,
        game_id=session.game_id,
        phase=Phase.NIGHT_SEER,
        actor="P3",
        target="P1",
        visibility=Visibility.PRIVATE_TO_SEER,
        payload={"result": "werewolf"},
    )

    _append_and_update(event_store, updater, event)

    seer_belief = belief_store.get(session.game_id, "P3")
    assert seer_belief.beliefs["P1"].werewolf == pytest.approx(0.95)
    assert seer_belief.beliefs["P1"].locked is True
    with pytest.raises(BeliefStateNotFoundError):
        belief_store.get(session.game_id, "P6")


def test_updater_does_not_read_truth_roles_directly():
    """可见性/观察者自身身份由 VisibilityRuleSpec 投递层提供，updater 不直读真身。"""
    source = inspect.getsource(RuleBasedRealtimeBeliefUpdater)
    assert "truth_state.players" not in source


def test_shadow_updater_writes_shadow_lane_only():
    session = make_6p_session(game_id="belief_shadow_lane")
    event_store, belief_store, updater = _wire_shadow(session)
    event = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P3",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我查验 P1 是狼人。",
            "role_claim": "seer",
            "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
        },
    )

    _append_and_update(event_store, updater, event)

    shadow = belief_store.get(session.game_id, "P1", is_shadow=True)
    assert shadow.is_shadow is True
    with pytest.raises(BeliefStateNotFoundError):
        belief_store.get(session.game_id, "P1", is_shadow=False)


def test_observability_records_update_batches_and_curve_points():
    session = make_6p_session(game_id="belief_observable")
    event_store, _belief_store, observability_store, updater = _wire_observable(session)
    event = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P3",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我查验 P1 是狼人。",
            "role_claim": "seer",
            "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
        },
    )

    _append_and_update(event_store, updater, event)

    batches = observability_store.list_updates(session.game_id)
    assert batches
    p3_batch = next(batch for batch in batches if batch.agent_id == "P3")
    assert p3_batch.trigger_event_id == event.event_id
    assert p3_batch.no_update_reason is None
    assert any(
        delta.target_player_id == "P1"
        and delta.role == Role.WEREWOLF
        and delta.prob_after > delta.prob_before
        for delta in p3_batch.deltas
    )

    curve_points = observability_store.list_curve_points(session.game_id)
    assert any(
        point.agent_id == "P3"
        and point.target_player_id == "P1"
        and point.werewolf_prob > 0.2
        and point.derived_by == "realtime_belief_updater"
        for point in curve_points
    )


def test_observability_records_no_update_reason_for_visible_noop_event():
    session = make_6p_session(game_id="belief_observable_noop")
    event_store, _belief_store, observability_store, updater = _wire_observable(session)
    event = make_event(
        EventType.DAY_ANNOUNCEMENT,
        game_id=session.game_id,
        phase=Phase.DAY_ANNOUNCEMENT,
        actor=None,
        visibility=Visibility.PUBLIC,
        payload={},
    )

    _append_and_update(event_store, updater, event)

    batches = observability_store.list_updates(session.game_id)
    assert batches
    assert all(not batch.deltas for batch in batches)
    assert {batch.no_update_reason for batch in batches} == {"no_probability_change"}


def test_seer_check_result_uses_configured_rules():
    session = make_6p_session(game_id="belief_configured_rules")
    event_store, belief_store, updater = _wire_with_rules(
        session,
        {
            "private_confirmations": {
                "seer_check_werewolf": {
                    "target": {
                        "werewolf": 0.77,
                        "locked": True,
                        "lock_reason": "custom_rule",
                    }
                }
            }
        },
    )
    event = make_event(
        EventType.SEER_CHECK_RESULT,
        game_id=session.game_id,
        phase=Phase.NIGHT_SEER,
        actor="P3",
        target="P1",
        visibility=Visibility.PRIVATE_TO_SEER,
        payload={"result": "werewolf"},
    )

    _append_and_update(event_store, updater, event)

    seer_view = belief_store.get(session.game_id, "P3").beliefs["P1"]
    assert seer_view.werewolf == pytest.approx(0.77)
    assert seer_view.lock_reason == "custom_rule"


def test_wolf_teammate_event_only_updates_wolves():
    session = make_6p_session(game_id="belief_wolves")
    event_store, belief_store, updater = _wire(session)
    event = make_event(
        EventType.WOLF_NOMINATION,
        game_id=session.game_id,
        phase=Phase.NIGHT_WEREWOLF,
        visibility=Visibility.PRIVATE_TO_WOLVES,
        payload={"teammates": ["P1", "P2"]},
    )

    _append_and_update(event_store, updater, event)

    p1_belief = belief_store.get(session.game_id, "P1")
    p2_belief = belief_store.get(session.game_id, "P2")
    assert p1_belief.beliefs["P2"].werewolf == pytest.approx(1.0)
    assert p1_belief.beliefs["P2"].locked is True
    assert p2_belief.beliefs["P1"].werewolf == pytest.approx(1.0)
    assert p2_belief.beliefs["P1"].locked is True
    with pytest.raises(BeliefStateNotFoundError):
        belief_store.get(session.game_id, "P6")


def test_public_witch_claim_is_interpreted_by_observer_perspective():
    session = make_6p_session(game_id="belief_witch_claim")
    event_store, belief_store, updater = _wire(session)
    event = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P6",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我是女巫。",
            "role_claim": "witch",
            "claim_result": None,
        },
    )

    _append_and_update(event_store, updater, event)

    true_witch_view = belief_store.get(session.game_id, "P4").beliefs["P6"]
    hunter_view = belief_store.get(session.game_id, "P5").beliefs["P6"]
    assert true_witch_view.witch < hunter_view.witch
    assert true_witch_view.werewolf > hunter_view.werewolf


def test_same_unique_role_counter_claim_applies_to_witch_claims():
    session = make_6p_session(game_id="belief_witch_counter_claim")
    event_store, belief_store, updater = _wire(session)
    first_claim = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P5",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我是女巫。",
            "role_claim": "witch",
            "claim_result": None,
        },
    )
    second_claim = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P6",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我才是女巫。",
            "role_claim": "witch",
            "claim_result": None,
        },
    )

    _append_and_update(event_store, updater, first_claim)
    _append_and_update(event_store, updater, second_claim)

    seer_view = belief_store.get(session.game_id, "P3")
    first_claimer = seer_view.beliefs["P5"]
    second_claimer = seer_view.beliefs["P6"]
    assert first_claimer.witch < second_claimer.witch
    assert first_claimer.werewolf > 0.2


def test_unique_role_counter_claim_does_not_rescan_event_history():
    session = make_6p_session(game_id="belief_counter_no_rescan")
    event_store, belief_store, updater = _wire(session)
    first_claim = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P5",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我是女巫。",
            "role_claim": "witch",
            "claim_result": None,
        },
    )
    second_claim = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P6",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我才是女巫。",
            "role_claim": "witch",
            "claim_result": None,
        },
    )
    event_store.append(first_claim)
    event_store.append(second_claim)

    def _boom(_game_id: str):
        raise AssertionError("counter-claim should not rescan event history")

    event_store.list_by_game = _boom
    updater.update(session.game_id, first_claim.event_id)
    updater.update(session.game_id, second_claim.event_id)

    seer_view = belief_store.get(session.game_id, "P3")
    assert seer_view.beliefs["P5"].werewolf > 0.2
    assert seer_view.beliefs["P6"].witch > seer_view.beliefs["P5"].witch


def test_public_check_claim_updates_all_alive_agents():
    session = make_6p_session(game_id="belief_claim_check")
    event_store, belief_store, updater = _wire(session)
    event = make_event(
        EventType.SPEECH,
        game_id=session.game_id,
        phase=Phase.DAY_DISCUSSION,
        actor="P3",
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": "我查验 P1 是狼人。",
            "role_claim": "seer",
            "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
        },
    )

    _append_and_update(event_store, updater, event)

    for agent_id in session.truth_state.players:
        belief = belief_store.get(session.game_id, agent_id)
        assert belief.beliefs["P1"].werewolf > 0.2


# ===========================================================================
# v1.最终(真人 1 校准)新规则:
#   claimed_seer_night_killed / claimed_seer_survives_night /
#   vote_against_claimed_seer / vote_follow_claimed_seer_black / hunter_shot_target
# ===========================================================================


def _emit_seer_claim_by(event_store, updater, *, game_id, actor, target_check=None, alignment=None):
    """辅助:发一条 SPEECH(role_claim=seer),可选附带 claim_result。"""
    payload = {"public_message": ".", "role_claim": "seer"}
    if target_check and alignment:
        payload["claim_result"] = {"target": target_check, "claimed_alignment": alignment}
    ev = make_event(
        EventType.SPEECH,
        game_id=game_id,
        phase=Phase.DAY_DISCUSSION,
        actor=actor,
        visibility=Visibility.PUBLIC,
        payload=payload,
    )
    _append_and_update(event_store, updater, ev)


def test_claimed_seer_night_killed_strongly_boosts_seer_and_drops_werewolf():
    """跳过预言家的玩家当晚被刀 → 该玩家 seer 强升、werewolf 强降(真人 1 排序 #1)。"""
    session = make_6p_session(game_id="belief_claimed_seer_nk")
    event_store, belief_store, updater = _wire(session)

    # P3 跳预言家
    _emit_seer_claim_by(event_store, updater, game_id=session.game_id, actor="P3")
    # 取一个观察者的当前 belief 作为基线
    pre = belief_store.get(session.game_id, "P6").beliefs["P3"]
    pre_w = pre.werewolf

    # P3 当晚被刀
    death_ev = make_event(
        EventType.DEATH_CONFIRMED,
        game_id=session.game_id,
        phase=Phase.DAY_ANNOUNCEMENT,
        target="P3",
        visibility=Visibility.PUBLIC,
        payload={"death_cause": "night_kill"},
    )
    _append_and_update(event_store, updater, death_ev)

    post = belief_store.get(session.game_id, "P6").beliefs["P3"]
    # werewolf 应该大幅下降(night_killed -0.35 叠加 claimed_seer_night_killed -0.80)
    assert post.werewolf < pre_w - 0.3, f"werewolf 应大幅下降:pre={pre_w}, post={post.werewolf}"
    # seer 应大幅上升
    assert post.seer > pre.seer + 0.1, f"seer 应大幅上升:pre={pre.seer}, post={post.seer}"


def test_claimed_seer_survives_night_raises_werewolf_suspicion():
    """跳过预言家的玩家当晚没死 → 该玩家 werewolf 上升(+0.45)。"""
    session = make_6p_session(game_id="belief_claimed_seer_survives")
    event_store, belief_store, updater = _wire(session)

    # P3 跳预言家
    _emit_seer_claim_by(event_store, updater, game_id=session.game_id, actor="P3")
    pre = belief_store.get(session.game_id, "P6").beliefs["P3"]

    # DAY_ANNOUNCEMENT:P5 死,P3 没死
    day_ev = make_event(
        EventType.DAY_ANNOUNCEMENT,
        game_id=session.game_id,
        phase=Phase.DAY_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        payload={"deaths": [{"player_id": "P5", "death_cause": "night_kill"}]},
    )
    _append_and_update(event_store, updater, day_ev)

    post = belief_store.get(session.game_id, "P6").beliefs["P3"]
    assert post.werewolf > pre.werewolf, f"P3 (跳预未死) 狼嫌应升:pre={pre.werewolf}, post={post.werewolf}"


def test_claimed_seer_survives_skips_dead_claimer():
    """跳过预言家但本次 deaths 已包含该玩家 → 不应触发 survives_night。"""
    session = make_6p_session(game_id="belief_claimed_seer_dead_in_deaths")
    event_store, belief_store, updater = _wire(session)

    _emit_seer_claim_by(event_store, updater, game_id=session.game_id, actor="P3")
    pre = belief_store.get(session.game_id, "P6").beliefs["P3"]

    day_ev = make_event(
        EventType.DAY_ANNOUNCEMENT,
        game_id=session.game_id,
        phase=Phase.DAY_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        payload={"deaths": [{"player_id": "P3", "death_cause": "night_kill"}]},
    )
    _append_and_update(event_store, updater, day_ev)
    post = belief_store.get(session.game_id, "P6").beliefs["P3"]
    # P3 在 deaths 里 → survives_night 不应触发(狼嫌不应增加)
    assert post.werewolf <= pre.werewolf + 0.01


def test_claimed_seer_survives_skips_already_dead_in_prior_round():
    """回归(Codex P1):跳预者第一夜被刀后,第二天 DAY_ANNOUNCEMENT 不含他时,
    不应错误触发 claimed_seer_survives_night 给他回加狼嫌。"""
    session = make_6p_session(game_id="belief_dead_seer_prior")
    event_store, belief_store, updater = _wire(session)

    # P3 跳预言家
    _emit_seer_claim_by(event_store, updater, game_id=session.game_id, actor="P3")
    # P3 第一夜被刀
    death_ev = make_event(
        EventType.DEATH_CONFIRMED, game_id=session.game_id,
        phase=Phase.DAY_ANNOUNCEMENT, target="P3",
        visibility=Visibility.PUBLIC, payload={"death_cause": "night_kill"},
    )
    _append_and_update(event_store, updater, death_ev)
    after_death = belief_store.get(session.game_id, "P6").beliefs["P3"].werewolf

    # 第二天 DAY_ANNOUNCEMENT,deaths 是别人(P5),P3 不在
    day2_ev = make_event(
        EventType.DAY_ANNOUNCEMENT, game_id=session.game_id,
        round_num=2, phase=Phase.DAY_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        payload={"deaths": [{"player_id": "P5", "death_cause": "night_kill"}]},
    )
    _append_and_update(event_store, updater, day2_ev)
    after_day2 = belief_store.get(session.game_id, "P6").beliefs["P3"].werewolf
    # P3 已死,不应再被 survives_night 回加 +0.45 → 狼嫌不应增加
    assert after_day2 <= after_death + 0.01, \
        f"P3 已死后狼嫌不应回升:after_death={after_death}, after_day2={after_day2}"


def test_vote_against_claimed_seer_raises_voter_werewolf():
    """投票 target 曾跳过预言家 → 投票者 werewolf +0.35(真人 1 排序 #3)。"""
    session = make_6p_session(game_id="belief_vote_against_seer")
    event_store, belief_store, updater = _wire(session)

    _emit_seer_claim_by(event_store, updater, game_id=session.game_id, actor="P3")
    pre = belief_store.get(session.game_id, "P6").beliefs["P5"].werewolf

    vote_ev = make_event(
        EventType.VOTE_CAST,
        game_id=session.game_id,
        phase=Phase.DAY_VOTE,
        actor="P5",
        target="P3",
        visibility=Visibility.PUBLIC,
        payload={},
    )
    _append_and_update(event_store, updater, vote_ev)
    post = belief_store.get(session.game_id, "P6").beliefs["P5"].werewolf
    assert post > pre + 0.05, f"P5 投了跳预言家的 P3 → 狼嫌应升:pre={pre}, post={post}"


def test_vote_follow_claimed_seer_black_lowers_voter_werewolf():
    """投票 target 曾被某跳预言家公开查杀 → 投票者 werewolf -0.35(真人 1 排序 #6)。"""
    session = make_6p_session(game_id="belief_vote_follow_seer")
    event_store, belief_store, updater = _wire(session)

    # P3 跳预言家 + 查杀 P1
    _emit_seer_claim_by(
        event_store, updater, game_id=session.game_id, actor="P3",
        target_check="P1", alignment="werewolf",
    )
    pre = belief_store.get(session.game_id, "P6").beliefs["P5"].werewolf

    # P5 投 P1(跟刀)
    vote_ev = make_event(
        EventType.VOTE_CAST,
        game_id=session.game_id,
        phase=Phase.DAY_VOTE,
        actor="P5",
        target="P1",
        visibility=Visibility.PUBLIC,
        payload={},
    )
    _append_and_update(event_store, updater, vote_ev)
    post = belief_store.get(session.game_id, "P6").beliefs["P5"].werewolf
    assert post < pre, f"P5 跟 P3 的查杀投 P1 → 狼嫌应降:pre={pre}, post={post}"


def test_hunter_shot_target_raises_target_werewolf():
    """猎人开枪带人 → 被带的 werewolf +0.15(真人 1 排序 #9)。"""
    session = make_6p_session(game_id="belief_hunter_shot")
    event_store, belief_store, updater = _wire(session)

    shot_ev = make_event(
        EventType.HUNTER_SHOT,
        game_id=session.game_id,
        phase=Phase.HUNTER_SHOOT,
        actor="P5",
        target="P2",
        visibility=Visibility.PUBLIC,
        payload={},
    )
    _append_and_update(event_store, updater, shot_ev)
    post = belief_store.get(session.game_id, "P6").beliefs["P2"]
    # 初始 belief 是均匀 0.2,加 0.15 后应该明显高于 0.2
    assert post.werewolf > 0.25


def test_hunter_shot_pass_does_not_update():
    """猎人 pass(target=None 或 pass=True)→ 不应更新 belief。"""
    session = make_6p_session(game_id="belief_hunter_pass")
    event_store, belief_store, updater = _wire(session)

    pass_ev = make_event(
        EventType.HUNTER_SHOT,
        game_id=session.game_id,
        phase=Phase.HUNTER_SHOOT,
        actor="P5",
        target=None,
        visibility=Visibility.PUBLIC,
        payload={"pass": True},
    )
    _append_and_update(event_store, updater, pass_ev)
    # 因为没有 target,updater 不应改任何 target 的狼概率(belief 维持初始均匀 0.2)
    belief = belief_store.get(session.game_id, "P6")
    for pid in ["P1", "P2", "P3", "P4", "P5"]:
        assert belief.beliefs[pid].werewolf == pytest.approx(0.2), f"{pid} 狼概率应保持 0.2 未变"
