"""ContextAssembler 端到端测试。

覆盖：
- 21 字段全填对
- 不同角色拿到不同的 private_events
- 红线：truth_state / role_map 不进 AgentContext
- v1 belief 注入（含空 BeliefState）
- Day 2+ 历史 SPEECH 进 public_memory_summary 而非 raw events
- rule_hints["fallback_targets"] 计算正确
- 序列化边界（JSON dump/load 后内容一致）
"""

from __future__ import annotations

import json

import pytest

from contracts import (
    ActionType,
    EventType,
    Phase,
    PlayerStatus,
    Role,
    Visibility,
)

from context.context_assembler import ContextAssembler
from stores.belief_state_store import InMemoryBeliefStateStore
from stores.event_store import InMemoryEventStore
from tests.context.conftest import (
    FakeSessionProvider,
    make_6p_session,
    make_event,
    make_player,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assembler(session, events=None, belief_store=None):
    provider = FakeSessionProvider(session)
    event_store = InMemoryEventStore()
    for ev in events or []:
        event_store.append(ev)
    return ContextAssembler(
        session_provider=provider,
        event_store=event_store,
        belief_store=belief_store,
    )


# ---------------------------------------------------------------------------
# 基础字段填写
# ---------------------------------------------------------------------------


class TestBasicFields:
    def test_identity_fields_from_session(self):
        session = make_6p_session(round_num=3, phase=Phase.DAY_VOTE)
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_VOTE)
        assert ctx.game_id == "g001"
        assert ctx.agent_id == "P3"
        assert ctx.role == Role.SEER
        assert ctx.round == 3
        assert ctx.phase == Phase.DAY_VOTE

    def test_unknown_agent_id_raises(self):
        session = make_6p_session()
        assembler = _make_assembler(session)
        with pytest.raises(ValueError, match="P99"):
            assembler.build_context("g001", "P99", Phase.DAY_VOTE)

    def test_tie_candidates_from_round_state(self):
        session = make_6p_session(
            phase=Phase.DAY_TIE_REVOTE,
            tie_candidates=["P3", "P4"],
            is_secondary_stage=True,
        )
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_TIE_REVOTE)
        assert ctx.tie_candidates == ["P3", "P4"]
        assert ctx.is_secondary_stage is True
        assert ctx.secondary_stage_type == "tie_revote"

    def test_previous_vote_summary_from_round_state(self):
        session = make_6p_session(previous_vote_summary={"P3": 2, "P1": 1})
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P1", Phase.DAY_VOTE)
        assert ctx.previous_vote_summary == {"P3": 2, "P1": 1}


# ---------------------------------------------------------------------------
# Visible players
# ---------------------------------------------------------------------------


class TestVisiblePlayers:
    def test_visible_players_include_all_six(self):
        session = make_6p_session()
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P1", Phase.DAY_DISCUSSION)
        assert len(ctx.visible_players) == 6
        # 不暴露 role
        for vp in ctx.visible_players:
            assert not hasattr(vp, "role")


# ---------------------------------------------------------------------------
# Allowed actions
# ---------------------------------------------------------------------------


class TestAllowedActions:
    def test_seer_in_night_phase(self):
        session = make_6p_session(phase=Phase.NIGHT_SEER)
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P3", Phase.NIGHT_SEER)
        assert ActionType.CHECK in ctx.allowed_actions


# ---------------------------------------------------------------------------
# Events 过滤
# ---------------------------------------------------------------------------


class TestEventsFilter:
    def test_public_events_visible_to_all(self):
        session = make_6p_session()
        events = [
            make_event(
                EventType.SPEECH,
                actor="P1",
                payload={"public_message": "I think P3 is sus."},
            )
        ]
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert len(ctx.public_events) == 1
        assert ctx.public_events[0].actor == "P1"

    def test_wolf_sees_teammate_in_private_events(self):
        session = make_6p_session()
        events = [
            make_event(
                EventType.WOLF_NOMINATION,
                actor="P2",
                target="P3",
                visibility=Visibility.PRIVATE_TO_WOLVES,
                payload={"teammates": ["P1", "P2"]},
            )
        ]
        assembler = _make_assembler(session, events=events)
        # P1 是狼
        ctx_wolf = assembler.build_context("g001", "P1", Phase.NIGHT_WEREWOLF)
        assert len(ctx_wolf.private_events) == 1
        # P6 是村民
        ctx_villager = assembler.build_context("g001", "P6", Phase.NIGHT_WEREWOLF)
        assert ctx_villager.private_events == []

    def test_role_assigned_filtered_out(self):
        """Role.20:52 A 提到的方案 1：role_assigned 即使 PUBLIC 也不进 AI 简报。"""
        session = make_6p_session()
        events = [
            make_event(
                EventType.ROLE_ASSIGNED,
                payload={"role_map": {"P1": "werewolf"}},
                visibility=Visibility.PUBLIC,
            )
        ]
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert ctx.public_events == []
        assert ctx.private_events == []


# ---------------------------------------------------------------------------
# current_round_events vs recent_public_events
# ---------------------------------------------------------------------------


class TestRoundFiltering:
    def test_current_round_events_only_current(self):
        session = make_6p_session(round_num=2)
        events = [
            make_event(EventType.EXILE, round_num=1, target="P5"),
            make_event(EventType.SPEECH, round_num=2, actor="P1",
                       payload={"public_message": "hi"}),
        ]
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        # current_round_events 只含 round=2 事件
        assert all(ev.round == 2 for ev in ctx.current_round_events)

    def test_historical_speech_excluded_from_recent(self):
        """Day 2+ 历史 SPEECH 不能进 recent_public_events 原文。"""
        session = make_6p_session(round_num=2)
        events = [
            make_event(EventType.SPEECH, round_num=1, actor="P1",
                       payload={"public_message": "I am seer"}),
            make_event(EventType.EXILE, round_num=1, target="P5"),  # 非 SPEECH 可保留
        ]
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        # 历史 SPEECH 被剥离
        for ev in ctx.recent_public_events:
            if ev.round < 2:
                assert ev.event_type != EventType.SPEECH


# ---------------------------------------------------------------------------
# public_memory_summary (Day 2+)
# ---------------------------------------------------------------------------


class TestPublicMemorySummary:
    def test_day_1_no_summary(self):
        session = make_6p_session(round_num=1)
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert ctx.public_memory_summary == []

    def test_day_2_summary_includes_round_1(self):
        session = make_6p_session(round_num=2)
        events = [
            make_event(
                EventType.DEATH_CONFIRMED,
                round_num=1,
                target="P4",
                payload={"death_cause": "night_kill"},
            ),
            make_event(EventType.EXILE, round_num=1, target="P5"),
        ]
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        # round 1 应该被压成 fact stream
        assert len(ctx.public_memory_summary) == 1
        fs = ctx.public_memory_summary[0]
        assert fs["round"] == 1
        # facts 含 daybreak + result
        joined = " ".join(fs["facts"])
        assert "P4 confirmed dead" in joined
        assert "P5 executed" in joined


# ---------------------------------------------------------------------------
# Belief 注入
# ---------------------------------------------------------------------------


class TestBeliefInjection:
    def test_v0_no_belief_store(self):
        session = make_6p_session()
        assembler = _make_assembler(session, belief_store=None)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert ctx.belief_state == {}
        assert ctx.belief_top_suspects == []

    def test_v1_empty_belief_when_not_yet_saved(self):
        session = make_6p_session()
        belief_store = InMemoryBeliefStateStore()
        assembler = _make_assembler(session, belief_store=belief_store)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        # 没存过 belief —— 空占位
        assert ctx.belief_state == {}

    def test_v1_belief_loaded_with_top_suspects(self):
        from contracts import BeliefState, RoleBelief

        session = make_6p_session()
        belief_store = InMemoryBeliefStateStore()
        belief = BeliefState(
            game_id="g001",
            agent_id="P3",
            beliefs={
                "P1": RoleBelief(werewolf=0.7),
                "P2": RoleBelief(werewolf=0.6),
                "P4": RoleBelief(werewolf=0.1),
            },
        )
        belief_store.save(belief)
        assembler = _make_assembler(session, belief_store=belief_store)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        # belief_state 应该被填
        assert ctx.belief_state != {}
        # top suspects 前 3 按概率降序
        assert len(ctx.belief_top_suspects) == 3
        assert ctx.belief_top_suspects[0]["player_id"] == "P1"
        assert ctx.belief_top_suspects[0]["werewolf_prob"] == 0.7

    # === PR-FD-C: belief_inject_filter（混合实验注入过滤）===
    # filter 只控制"是否注入"，
    # 不影响后台 belief 更新；只看 agent_id、不接 role。

    @staticmethod
    def _saved_belief(agent_id: str):
        from contracts import BeliefState, RoleBelief

        return BeliefState(
            game_id="g001",
            agent_id=agent_id,
            beliefs={
                "P1": RoleBelief(werewolf=0.7),
                "P2": RoleBelief(werewolf=0.6),
            },
        )

    def test_filter_none_is_regression_equivalent(self):
        """filter=None（默认）时行为与不传 filter 完全等价：belief_store 在场即注入。"""
        session = make_6p_session()
        store = InMemoryBeliefStateStore()
        store.save(self._saved_belief("P3"))

        baseline = _make_assembler(session, belief_store=store)
        filtered = ContextAssembler(
            session_provider=FakeSessionProvider(session),
            event_store=InMemoryEventStore(),
            belief_store=store,
            belief_inject_filter=None,
        )
        ctx_baseline = baseline.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        ctx_filtered = filtered.build_context("g001", "P3", Phase.DAY_DISCUSSION)

        assert ctx_filtered.belief_state == ctx_baseline.belief_state
        assert ctx_filtered.belief_top_suspects == ctx_baseline.belief_top_suspects
        assert ctx_filtered.belief_state != {}  # 确实注入了（非空对照）

    def test_filter_selects_subset(self):
        """filter 只让放行的 agent 拿非空 belief；被挡的退化成空（v0）。

        模拟混合实验"只给狼注入"：P1=狼放行、P3=预言家挡掉。两者都有 belief
        存盘，验证差异只来自 filter。
        """
        session = make_6p_session()
        store = InMemoryBeliefStateStore()
        store.save(self._saved_belief("P1"))
        store.save(self._saved_belief("P3"))

        wolves = {"P1", "P2"}
        assembler = ContextAssembler(
            session_provider=FakeSessionProvider(session),
            event_store=InMemoryEventStore(),
            belief_store=store,
            belief_inject_filter=lambda agent_id: agent_id in wolves,
        )
        ctx_wolf = assembler.build_context("g001", "P1", Phase.DAY_DISCUSSION)
        ctx_seer = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)

        assert ctx_wolf.belief_state != {}  # 放行 → 注入
        assert ctx_seer.belief_state == {}  # 挡掉 → 退化空
        assert ctx_seer.belief_top_suspects == []


# ---------------------------------------------------------------------------
# rule_hints
# ---------------------------------------------------------------------------


class TestRuleHints:
    def test_fallback_targets_excludes_self_and_dead(self):
        session = make_6p_session(
            overrides={"P5": make_player(Role.HUNTER, PlayerStatus.DEAD)}
        )
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P1", Phase.DAY_VOTE)
        targets = ctx.rule_hints["fallback_targets"]
        assert "P1" not in targets  # 自己
        assert "P5" not in targets  # 死人
        assert sorted(targets) == ["P2", "P3", "P4", "P6"]


# ---------------------------------------------------------------------------
# 红线：信息隔离
# ---------------------------------------------------------------------------


class TestInformationIsolation:
    def test_no_truth_state_keys_in_serialized(self):
        """AgentContext JSON 中绝不含 truth_state / role_map / hidden_roles 键。"""
        session = make_6p_session()
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        dumped = json.dumps(ctx.model_dump(mode="json"))
        assert "truth_state" not in dumped
        assert "role_map" not in dumped
        assert "hidden_roles" not in dumped

    def test_villager_does_not_see_wolf_roles_in_serialized(self):
        """P1, P2 都是狼，但村民 P6 拿到的 context 里看不到他们的 role。"""
        session = make_6p_session()
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P6", Phase.DAY_DISCUSSION)
        dumped = json.dumps(ctx.model_dump(mode="json"))
        # P6 拿到的 context 里只有自己的 role 字段（"role": "villager"）
        # 别人的 role 不应该出现
        # 我们检查"werewolf"这个字符串不应该出现在 visible_players 序列化里
        # （但 belief_state 里可能有 RoleBelief.werewolf 字段，因此不能简单检查全文）
        # 改为检查 visible_players 序列化里没有 role 字段
        for vp in ctx.visible_players:
            vp_json = json.dumps(vp.model_dump(mode="json"))
            assert "werewolf" not in vp_json
            assert "seer" not in vp_json

    def test_json_round_trip_clean(self):
        """序列化边界：json.loads(json.dumps(model_dump())) 不抛错。"""
        session = make_6p_session()
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P1", Phase.DAY_DISCUSSION)
        restored = json.loads(json.dumps(ctx.model_dump(mode="json")))
        assert restored["game_id"] == "g001"
        assert restored["agent_id"] == "P1"


# ---------------------------------------------------------------------------
# 21 字段完整性
# ---------------------------------------------------------------------------


class TestTwentyOneFields:
    def test_all_21_fields_present(self):
        session = make_6p_session()
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        dumped = ctx.model_dump()
        expected_keys = {
            "game_id", "agent_id", "role", "round", "phase",
            "is_secondary_stage", "secondary_stage_type",
            "tie_candidates", "previous_vote_summary", "compressed_context",
            "visible_players", "current_round_events", "recent_public_events",
            "public_memory_summary", "public_events", "private_events",
            "belief_state", "belief_top_suspects", "strategy_memory",
            "allowed_actions", "rule_hints",
        }
        assert expected_keys.issubset(set(dumped.keys()))


# ---------------------------------------------------------------------------
# v2.2 typed 台账投影（claim_records / vote_records）—— opt-in via enable_typed_records
# ---------------------------------------------------------------------------


def _make_assembler_with_typed(session, events=None):
    provider = FakeSessionProvider(session)
    event_store = InMemoryEventStore()
    for ev in events or []:
        event_store.append(ev)
    return ContextAssembler(
        session_provider=provider,
        event_store=event_store,
        enable_typed_records=True,
    )


class TestTypedRecordsDefaultOff:
    """默认 enable_typed_records=False —— 保 9p baseline 0 fallback。"""

    def test_default_assembler_produces_empty_typed_records(self):
        session = make_6p_session()
        events = [
            make_event(
                EventType.SPEECH,
                round_num=1,
                actor="P3",
                payload={
                    "public_message": "我是预言家",
                    "role_claim": "seer",
                    "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
                },
            ),
            make_event(EventType.VOTE_CAST, round_num=1, actor="P2", target="P1"),
        ]
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert ctx.claim_records == []
        assert ctx.vote_records == []


class TestClaimRecordsProjection:
    def test_speech_with_role_claim_projects_record(self):
        session = make_6p_session()
        events = [
            make_event(
                EventType.SPEECH,
                round_num=1,
                phase=Phase.DAY_DISCUSSION,
                actor="P3",
                payload={
                    "public_message": "我是预言家",
                    "role_claim": "seer",
                    "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
                },
            ),
        ]
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert len(ctx.claim_records) == 1
        cr = ctx.claim_records[0]
        assert cr.actor == "P3"
        assert cr.round == 1
        assert cr.phase == Phase.DAY_DISCUSSION
        assert cr.claimed_role == Role.SEER
        assert cr.claim_target == "P1"
        assert cr.claimed_alignment.value == "werewolf"
        assert cr.source_event_id == events[0].event_id
        assert cr.derived_by == "context_assembler"

    def test_speech_without_claim_skipped(self):
        session = make_6p_session()
        events = [
            make_event(
                EventType.SPEECH,
                round_num=1,
                actor="P3",
                payload={"public_message": "hi"},
            ),
        ]
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert ctx.claim_records == []

    def test_counter_claim_detected_when_second_actor_claims_same_role(self):
        session = make_6p_session()
        events = [
            make_event(
                EventType.SPEECH,
                round_num=1,
                actor="P3",
                payload={"public_message": "我是预言家", "role_claim": "seer"},
            ),
            make_event(
                EventType.SPEECH,
                round_num=1,
                actor="P5",
                payload={"public_message": "我才是预言家", "role_claim": "seer"},
            ),
        ]
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert len(ctx.claim_records) == 2
        assert ctx.claim_records[0].is_counter_claim is False
        assert ctx.claim_records[1].is_counter_claim is True

    def test_non_speech_events_ignored(self):
        session = make_6p_session()
        events = [
            make_event(EventType.EXILE, round_num=1, target="P5"),
            make_event(
                EventType.DEATH_CONFIRMED,
                round_num=1,
                target="P4",
                payload={"death_cause": "night_kill"},
            ),
        ]
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert ctx.claim_records == []


class TestVoteRecordsProjection:
    def test_vote_cast_primary_phase(self):
        session = make_6p_session(phase=Phase.DAY_VOTE)
        events = [
            make_event(
                EventType.VOTE_CAST,
                round_num=1,
                phase=Phase.DAY_VOTE,
                actor="P2",
                target="P1",
            ),
        ]
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_VOTE)
        assert len(ctx.vote_records) == 1
        vr = ctx.vote_records[0]
        assert vr.voter == "P2"
        assert vr.target == "P1"
        assert vr.stage == "primary"
        assert vr.is_revote is False
        assert vr.is_tie_candidate_vote is False
        assert vr.source_event_id == events[0].event_id
        assert vr.derived_by == "context_assembler"

    def test_vote_cast_tie_revote_phase(self):
        session = make_6p_session(
            phase=Phase.DAY_TIE_REVOTE,
            tie_candidates=["P3", "P4"],
            is_secondary_stage=True,
        )
        events = [
            make_event(
                EventType.VOTE_CAST,
                round_num=1,
                phase=Phase.DAY_TIE_REVOTE,
                actor="P1",
                target="P3",
            ),
        ]
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_TIE_REVOTE)
        assert len(ctx.vote_records) == 1
        vr = ctx.vote_records[0]
        assert vr.stage == "revote"
        assert vr.is_revote is True
        assert vr.is_tie_candidate_vote is True

    def test_non_vote_events_ignored(self):
        session = make_6p_session()
        events = [
            make_event(EventType.SPEECH, round_num=1, actor="P1",
                       payload={"public_message": "hi"}),
            make_event(EventType.EXILE, round_num=1, target="P5"),
        ]
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        assert ctx.vote_records == []


class TestTypedRecordsBudgetCap:
    def test_window_policy_caps_to_15(self):
        """ContextWindowPolicy 把 typed records 截到 15（与 max_recent_public_events 同档）。"""
        session = make_6p_session()
        # 20 条 VOTE_CAST，actor 轮换避免 self-vote 等问题
        events = []
        for i in range(20):
            events.append(
                make_event(
                    EventType.VOTE_CAST,
                    round_num=1,
                    phase=Phase.DAY_VOTE,
                    actor=f"P{(i % 5) + 1}",
                    target=f"P{((i + 1) % 5) + 1}",
                )
            )
        assembler = _make_assembler_with_typed(session, events=events)
        ctx = assembler.build_context("g001", "P3", Phase.DAY_DISCUSSION)
        # 保留最近 15 条
        assert len(ctx.vote_records) == 15
