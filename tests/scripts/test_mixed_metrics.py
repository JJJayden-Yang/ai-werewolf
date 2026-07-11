"""Unit tests for scripts/_mixed_metrics.py (PR-A-2 followup 派生指标)。

纯函数用合成 GameEvent / TruthState / BeliefState / trace 直接喂，不依赖跑批。
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from contracts import (
    BeliefState,
    Camp,
    EventType,
    GameEvent,
    Phase,
    PlayerState,
    PlayerStatus,
    RoleBelief,
    TruthState,
    Visibility,
)
from contracts.enums import Role
from scripts import _mixed_metrics as mm
from stores.belief_state_store import InMemoryBeliefStateStore


def _ev(event_type, *, round_=1, phase=Phase.DAY_VOTE, actor=None, target=None, payload=None):
    return GameEvent(
        event_id=f"{event_type.value}:{round_}:{actor or target or 'x'}",
        game_id="g",
        round=round_,
        phase=phase,
        event_type=event_type,
        actor=actor,
        target=target,
        visibility=Visibility.PUBLIC,
        payload=payload or {},
    )


def _truth(roles: dict[str, Role], *, dead: set[str] | None = None) -> TruthState:
    dead = dead or set()
    camp_of = lambda r: Camp.WEREWOLF if r == Role.WEREWOLF else Camp.VILLAGER
    players = {
        pid: PlayerState(
            player_id=pid,
            role=role,
            camp=camp_of(role),
            status=PlayerStatus.DEAD if pid in dead else PlayerStatus.ALIVE,
        )
        for pid, role in roles.items()
    }
    return TruthState(game_id="g", round=1, phase=Phase.DAY_VOTE, players=players)


# --------------------------------------------------------------------------- #
# 工程视角
# --------------------------------------------------------------------------- #


def test_collect_decision_stats_from_stats_dict():
    agent = SimpleNamespace(
        stats={
            "ok": 9,
            "parse_error": 1,
            "llm_error": 0,
            "retry": 2,
            "canonicalize_role_leak": 3,
        }
    )
    out = mm.collect_decision_stats(agent)
    assert out["ok"] == 9
    assert out["parse_error"] == 1
    assert out["decisions"] == 10
    assert out["ok_rate"] == pytest.approx(0.9)
    assert out["canonicalize_role_leak"] == 3
    assert out["canonicalize_meta_ai"] == 0


def test_collect_decision_stats_without_stats_is_zero_skeleton():
    out = mm.collect_decision_stats(object())
    assert out["decisions"] == 0
    assert out["ok_rate"] is None
    assert out["ok"] == 0


def test_collect_pipeline_events_counts_fallback_and_rule_validation():
    events = [
        _ev(EventType.RULE_VALIDATION),
        _ev(EventType.FALLBACK_USED, payload={"degraded": True}),
        _ev(EventType.RULE_VALIDATION),
        _ev(EventType.FALLBACK_USED, payload={"fallback_failed": True}),
        _ev(EventType.SPEECH),
    ]
    out = mm.collect_pipeline_events(events)
    assert out["fallback_used"] == 2
    assert out["rule_validation"] == 2
    assert out["degraded_fallback"] == 1
    assert out["fallback_failed"] == 1


def test_fallback_breakdown_groups_rule_violations():
    events = [
        _ev(EventType.RULE_VALIDATION, payload={"violation_type": "schema_invalid"}),
        _ev(EventType.RULE_VALIDATION, payload={"violation_type": "schema_invalid"}),
        _ev(EventType.RULE_VALIDATION, payload={"violation_type": "dead_target"}),
        _ev(EventType.RULE_VALIDATION, payload={"violation_type": "wrong_phase"}),
        _ev(EventType.SPEECH),
    ]
    out = mm.collect_fallback_breakdown(
        events, {"llm_error": 3, "parse_error": 1}
    )

    assert out["llm_error"] == 3
    assert out["parse_error"] == 1
    assert out["rule_violation"] == {"dead_target": 1, "wrong_phase": 1}
    assert out["schema_invalid_events"] == 2


def test_fallback_breakdown_empty():
    out = mm.collect_fallback_breakdown(
        [_ev(EventType.SPEECH)], {"llm_error": 0, "parse_error": 0}
    )

    assert out == {
        "llm_error": 0,
        "parse_error": 0,
        "rule_violation": {},
        "schema_invalid_events": 0,
    }


def test_collect_context_stats_reads_window_chain():
    window = SimpleNamespace(
        stats={
            "applies": 10,
            "truncated_speech_events": 4,
            "progressive_degrade_triggered": 2,
            "budget_exceeded": 0,
        }
    )
    built = SimpleNamespace(window_policy=window)
    out = mm.collect_context_stats(built)
    assert out["applies"] == 10
    assert out["truncate"] == 4
    assert out["degrade"] == 2
    assert out["exceed"] == 0
    assert out["degrade_rate"] == pytest.approx(0.2)


def test_collect_context_stats_missing_window_is_zero():
    out = mm.collect_context_stats(SimpleNamespace())
    assert out["applies"] == 0
    assert out["degrade_rate"] is None


# --------------------------------------------------------------------------- #
# 产品视角：关键场景
# --------------------------------------------------------------------------- #


def test_derive_key_scenes_tie_hunter_double_death_seer():
    truth = _truth({"P1": Role.SEER, "P2": Role.WEREWOLF, "P3": Role.VILLAGER, "P4": Role.WITCH})
    truth.witch_state.antidote_used = True
    events = [
        _ev(EventType.TIE_DETECTED, round_=1),
        _ev(EventType.WITCH_SAVE, round_=1, phase=Phase.NIGHT_WITCH, actor="P4"),
        # 真实事件：target 在顶层（emit 已 pop 出 payload），payload 只剩 death_cause
        _ev(
            EventType.DEATH_CONFIRMED,
            round_=1,
            target="P1",
            payload={"death_cause": "night_kill"},
        ),
        _ev(
            EventType.DAY_ANNOUNCEMENT,
            round_=2,
            payload={"deaths": [{"player_id": "P1"}, {"player_id": "P3"}]},
        ),
        _ev(EventType.HUNTER_SHOT, round_=2, actor="P1"),
        _ev(EventType.EXILE, round_=2, target="P2"),
    ]
    out = mm.derive_key_scenes(events, truth)
    assert out["tie_hit"] is True
    assert out["tie_rounds"] == [1]
    assert out["hunter_shot_used"] is True
    assert out["hunter_shot_rounds"] == [2]
    assert out["double_death_rounds"] == [2]
    assert out["seer_killed_round"] == 1
    assert out["seer_killed_n1"] is True
    assert out["witch_save_used"] is True
    assert out["witch_save_round"] == 1
    assert out["witch_poison_used"] is False
    assert out["final_round"] == 2
    assert out["exile_rounds"] == [2]


def test_derive_key_scenes_seer_disclosed_check():
    truth = _truth({"P1": Role.SEER, "P2": Role.WEREWOLF, "P3": Role.VILLAGER})
    events = [
        _ev(
            EventType.SPEECH,
            round_=2,
            phase=Phase.DAY_DISCUSSION,
            actor="P1",
            payload={"role_claim": "seer", "claim_result": {"target": "P2", "claimed_alignment": "werewolf"}},
        ),
        _ev(EventType.SPEECH, round_=2, phase=Phase.DAY_DISCUSSION, actor="P3", payload={"claim_result": None}),
    ]
    out = mm.derive_key_scenes(events, truth)
    assert out["seer_disclosed_check"] is True
    assert out["seer_claim_round"] == 2


# --------------------------------------------------------------------------- #
# 数学视角 + belief snapshot 选取
# --------------------------------------------------------------------------- #


def test_suspicion_entropy_uniform_is_one_concentrated_is_low():
    assert mm._suspicion_entropy([0.5, 0.5]) == pytest.approx(1.0)
    assert mm._suspicion_entropy([0.99, 0.01]) < 0.2
    assert mm._suspicion_entropy([0.0]) is None


def test_belief_snapshot_at_strictly_before_decision():
    def snap(round_, phase):
        return BeliefState(game_id="g", agent_id="P2", round=round_, phase=phase, beliefs={})

    history = [
        snap(1, Phase.DAY_DISCUSSION),
        snap(2, Phase.DAY_DISCUSSION),
        snap(2, Phase.DAY_VOTE),
    ]
    # 决策在 (2, DAY_VOTE)：同 phase 的 (2, DAY_VOTE) 必须被排除（未来信息），取 (2, DAY_DISCUSSION)
    chosen = mm.belief_snapshot_at(history, round_=2, phase=Phase.DAY_VOTE)
    assert chosen.round == 2 and chosen.phase == Phase.DAY_DISCUSSION
    # 决策早于任何快照 → None（绝不退回未来快照）
    assert mm.belief_snapshot_at(history, round_=1, phase=Phase.NIGHT_WEREWOLF) is None
    assert mm.belief_snapshot_at([], round_=1, phase=Phase.DAY_VOTE) is None


# --------------------------------------------------------------------------- #
# belief 信号：一致率 + 命中率 + 分桶
# --------------------------------------------------------------------------- #


def test_compute_belief_signal_none_without_injection():
    truth = _truth({"P1": Role.WEREWOLF, "P2": Role.VILLAGER})
    store = InMemoryBeliefStateStore()
    assert (
        mm.compute_belief_signal(
            game_id="g",
            injected_agents=[],
            belief_store=store,
            truth_state=truth,
            traces=[],
            events=[],
        )
        is None
    )


def test_compute_belief_signal_consistency_accuracy_and_quality():
    # P1=真狼, P2=注入的好人(预言家), P3=好人
    truth = _truth({"P1": Role.WEREWOLF, "P2": Role.SEER, "P3": Role.VILLAGER})
    store = InMemoryBeliefStateStore()
    # 快照存在第一夜（NIGHT_WEREWOLF），严格早于后面的 check / vote 决策
    store.save(
        BeliefState(
            game_id="g",
            agent_id="P2",
            round=1,
            phase=Phase.NIGHT_WEREWOLF,
            beliefs={
                "P1": RoleBelief(werewolf=0.8),
                "P3": RoleBelief(werewolf=0.2),
            },
        )
    )
    traces = [
        SimpleNamespace(
            agent_id="P2",
            round=1,
            phase=Phase.DAY_VOTE,
            decision_output={"action_type": "vote", "target": "P1"},  # 跟 top suspect 一致
        ),
        SimpleNamespace(
            agent_id="P2",
            round=1,
            phase=Phase.NIGHT_SEER,
            decision_output={"action_type": "check", "target": "P3"},  # 偏离 top suspect
        ),
        # speak 不带 target，不计入
        SimpleNamespace(
            agent_id="P2",
            round=1,
            phase=Phase.DAY_DISCUSSION,
            decision_output={"action_type": "speak", "target": None},
        ),
    ]
    sig = mm.compute_belief_signal(
        game_id="g",
        injected_agents=["P2"],
        belief_store=store,
        truth_state=truth,
        traces=traces,
        events=[],
    )
    assert sig is not None
    assert sig["injected_agents"] == ["P2"]
    assert sig["top_suspect_decisions"] == 2
    assert sig["decision_matches_top_suspect"] == 1
    assert sig["decision_top_suspect_consistency_rate"] == pytest.approx(0.5)
    assert sig["deviation_count"] == 1
    # top suspect P1 是真狼 → 两次决策都命中
    assert sig["top_suspect_hits_true_wolf"] == 2
    assert sig["top_suspect_accuracy_rate"] == pytest.approx(1.0)
    assert sig["by_action_type"]["vote"] == {"decisions": 1, "matches": 1, "hits_wolf": 1}
    assert sig["by_action_type"]["check"] == {"decisions": 1, "matches": 0, "hits_wolf": 1}
    # 数学：Brier = ((0.8-1)^2 + (0.2-0)^2)/2 = 0.04；separation = 0.8 - 0.2 = 0.6
    assert sig["belief_quality"]["avg_brier"] == pytest.approx(0.04)
    assert sig["belief_quality"]["avg_wolf_villager_separation"] == pytest.approx(0.6)
    assert sig["belief_quality"]["avg_top_margin"] == pytest.approx(0.6)
    assert sig["final_belief_quality"]["agents_evaluated"] == 1


def test_belief_signal_uses_alive_at_decision_not_endgame():
    # 回归：P1 是真狼，赛末已死（round 2 被放逐）；但 P2 在 round 1 P1 还活着时投了 P1。
    # 用赛末 status 过滤会把 P1 错误剔除 → top suspect 变 P3、命中归零。按事件流重建
    # 决策时存活集才正确。
    truth = _truth(
        {"P1": Role.WEREWOLF, "P2": Role.SEER, "P3": Role.VILLAGER, "P4": Role.VILLAGER},
        dead={"P1"},
    )
    store = InMemoryBeliefStateStore()
    store.save(
        BeliefState(
            game_id="g",
            agent_id="P2",
            round=1,
            phase=Phase.NIGHT_WEREWOLF,
            beliefs={
                "P1": RoleBelief(werewolf=0.9),
                "P3": RoleBelief(werewolf=0.3),
                "P4": RoleBelief(werewolf=0.1),
            },
        )
    )
    events = [
        # 真实事件：target 在顶层（emit 已 pop），payload 只剩 death_cause
        _ev(
            EventType.DEATH_CONFIRMED,
            round_=2,
            phase=Phase.EXILE_RESOLUTION,
            target="P1",
            payload={"death_cause": "exile"},
        )
    ]
    traces = [
        SimpleNamespace(
            agent_id="P2",
            round=1,
            phase=Phase.DAY_VOTE,
            decision_output={"action_type": "vote", "target": "P1"},
        )
    ]
    sig = mm.compute_belief_signal(
        game_id="g",
        injected_agents=["P2"],
        belief_store=store,
        truth_state=truth,
        traces=traces,
        events=events,
    )
    assert sig["top_suspect_decisions"] == 1
    assert sig["decision_matches_top_suspect"] == 1
    assert sig["top_suspect_accuracy_rate"] == pytest.approx(1.0)


def test_death_points_reads_top_level_target():
    # 真实事件 target 在顶层、payload 无 target —— 必须仍能定位死亡点
    events = [
        _ev(
            EventType.DEATH_CONFIRMED,
            round_=1,
            phase=Phase.DAY_ANNOUNCEMENT,
            target="P3",
            payload={"death_cause": "night_kill"},
        )
    ]
    dp = mm._death_points(events)
    assert "P3" in dp
    alive = mm._alive_at(["P1", "P2", "P3"], dp, round_=2, phase=Phase.DAY_VOTE)
    assert alive == {"P1", "P2"}  # P3 round1 已死，round2 决策时不在存活集


def test_alive_at_decision_vs_after_event_semantics():
    # P1 在 round2 EXILE_RESOLUTION 死亡
    dp = {"P1": (2, mm._PHASE_ORDER[Phase.EXILE_RESOLUTION])}
    players = ["P1", "P2"]
    # 决策口径：同 phase 死亡发生在决策之后 → 仍算存活（防未来泄漏）
    assert mm._alive_at(players, dp, round_=2, phase=Phase.EXILE_RESOLUTION) == {"P1", "P2"}
    # 事件后口径（final 快照）：同 phase 死亡已被处理 → 剔除
    assert mm._alive_at(
        players, dp, round_=2, phase=Phase.EXILE_RESOLUTION, same_phase_dead=True
    ) == {"P2"}


def test_belief_signal_excludes_player_dead_before_decision():
    # P3 round1 夜里被刀；belief 给 P3 最高怀疑(0.9)。但 P2 在 round2 投票时 P3 已死，
    # 必须从候选剔除 → top suspect 应是活着的 P1(真狼)。若 _death_points 读错字段（拿不到
    # 死亡点），P3 会被当活人 → top 误判为 P3。
    truth = _truth(
        {"P1": Role.WEREWOLF, "P2": Role.SEER, "P3": Role.VILLAGER, "P4": Role.VILLAGER},
        dead={"P3"},
    )
    store = InMemoryBeliefStateStore()
    store.save(
        BeliefState(
            game_id="g",
            agent_id="P2",
            round=1,
            phase=Phase.NIGHT_WEREWOLF,
            beliefs={
                "P1": RoleBelief(werewolf=0.5),
                "P3": RoleBelief(werewolf=0.9),
                "P4": RoleBelief(werewolf=0.2),
            },
        )
    )
    events = [
        _ev(
            EventType.DEATH_CONFIRMED,
            round_=1,
            phase=Phase.DAY_ANNOUNCEMENT,
            target="P3",
            payload={"death_cause": "night_kill"},
        )
    ]
    traces = [
        SimpleNamespace(
            agent_id="P2",
            round=2,
            phase=Phase.DAY_VOTE,
            decision_output={"action_type": "vote", "target": "P1"},
        )
    ]
    sig = mm.compute_belief_signal(
        game_id="g",
        injected_agents=["P2"],
        belief_store=store,
        truth_state=truth,
        traces=traces,
        events=events,
    )
    assert sig["top_suspect_decisions"] == 1
    assert sig["decision_matches_top_suspect"] == 1  # top=P1（P3 已剔除），vote P1 命中
    assert sig["top_suspect_hits_true_wolf"] == 1
