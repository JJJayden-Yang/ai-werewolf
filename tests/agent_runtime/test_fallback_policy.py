"""FallbackPolicy 测试。

覆盖：
- ABC 行为（不可实例化、子类必须实现 apply）
- 9 个 phase happy path（含 DAY_TIE_DISCUSSION）
- target 选择规则（rule_hints 优先 / skip self / skip dead / 退到 visible_players）
- 错误路径（tie_revote 空 / 没合法 target / 非 agent phase）
- metadata 携带（fallback_used / fallback_reason / fallback_message 截断）
- supervisor 兼容性（action 必填字段从 context 转写）
"""

from __future__ import annotations

import pytest

from contracts import (
    ActionType,
    Phase,
    PlayerStatus,
    Role,
    VisiblePlayer,
)

from agent_runtime.exceptions import FallbackError
from agent_runtime.fallback_policy import (
    ContextAwareFallbackPolicy,
    FallbackPolicy,
)
from tests.fixtures.agent_contexts import (
    day_discussion_context,
    hunter_shoot_context,
    last_words_context,
    seer_context,
    tie_discussion_context,
    tie_revote_context,
    visible_players,
    vote_context,
    werewolf_context,
    witch_context,
)


# ---------------------------------------------------------------------------
# ABC 行为
# ---------------------------------------------------------------------------


class TestFallbackPolicyABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            FallbackPolicy()  # type: ignore[abstract]

    def test_subclass_must_implement_apply(self):
        class Incomplete(FallbackPolicy):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_with_apply_can_instantiate(self):
        class Minimal(FallbackPolicy):
            def apply(self, context, error=None):
                raise NotImplementedError

        Minimal()  # ok


# ---------------------------------------------------------------------------
# 9 个 phase happy path
# ---------------------------------------------------------------------------


class TestContextAwareFallbackPolicyPerPhase:
    """逐 phase 验证 fallback 输出形态。"""

    def setup_method(self):
        self.policy = ContextAwareFallbackPolicy()

    def test_night_werewolf_nominates_first_alive_non_self(self):
        # agent=P1，visible_players 第一个非 self 存活 = P2
        action = self.policy.apply(werewolf_context())
        assert action.action_type == ActionType.NIGHT_KILL_NOMINATE
        assert action.target == "P2"
        assert action.metadata["fallback_used"] is True

    def test_night_seer_checks_first_alive_non_self(self):
        # agent=P3，第一个非 self 存活 = P1
        action = self.policy.apply(seer_context())
        assert action.action_type == ActionType.CHECK
        assert action.target == "P1"

    def test_night_witch_skips(self):
        action = self.policy.apply(witch_context())
        assert action.action_type == ActionType.SKIP
        assert action.target is None
        assert action.public_message is None

    def test_hunter_shoot_passes_safely(self):
        action = self.policy.apply(hunter_shoot_context())
        assert action.action_type == ActionType.HUNTER_SHOOT
        assert action.target is None
        assert action.metadata["pass"] is True

    def test_day_discussion_speaks_default_message(self):
        action = self.policy.apply(day_discussion_context())
        assert action.action_type == ActionType.SPEAK
        assert (
            action.public_message
            == ContextAwareFallbackPolicy.DEFAULT_DAY_DISCUSSION_MESSAGE
        )
        assert action.target is None

    def test_tie_discussion_speaks_default_message(self):
        action = self.policy.apply(tie_discussion_context())
        assert action.action_type == ActionType.SPEAK
        assert (
            action.public_message
            == ContextAwareFallbackPolicy.DEFAULT_DAY_DISCUSSION_MESSAGE
        )

    def test_day_vote_votes_first_alive_non_self(self):
        # agent=P2，第一个非 self 存活 = P1
        action = self.policy.apply(vote_context())
        assert action.action_type == ActionType.VOTE
        assert action.target == "P1"

    def test_tie_revote_picks_first_tie_candidate_non_self(self):
        # agent=P2, tie=["P3","P4"]
        action = self.policy.apply(tie_revote_context())
        assert action.action_type == ActionType.VOTE
        assert action.target == "P3"

    def test_exile_last_words_speaks_default_last_words(self):
        action = self.policy.apply(last_words_context())
        assert action.action_type == ActionType.SPEAK
        assert (
            action.public_message
            == ContextAwareFallbackPolicy.DEFAULT_EXILE_LAST_WORDS
        )


# ---------------------------------------------------------------------------
# Target 选择规则
# ---------------------------------------------------------------------------


class TestTargetSelection:
    def setup_method(self):
        self.policy = ContextAwareFallbackPolicy()

    def test_rule_hints_fallback_targets_takes_priority(self):
        ctx = vote_context().model_copy(
            update={"rule_hints": {"fallback_targets": ["P4"]}}
        )
        action = self.policy.apply(ctx)
        assert action.target == "P4"

    def test_rule_hints_skips_self_in_fallback_targets(self):
        # agent=P2 — fallback_targets 第一个是自己，必须跳过
        ctx = vote_context().model_copy(
            update={"rule_hints": {"fallback_targets": ["P2", "P4"]}}
        )
        action = self.policy.apply(ctx)
        assert action.target == "P4"

    def test_rule_hints_ignores_non_string_entries(self):
        ctx = vote_context().model_copy(
            update={"rule_hints": {"fallback_targets": [123, None, "P4"]}}
        )
        action = self.policy.apply(ctx)
        assert action.target == "P4"

    def test_rule_hints_non_list_falls_through_to_visible_players(self):
        # rule_hints["fallback_targets"] 不是 list → 走 visible_players 路径
        ctx = vote_context().model_copy(
            update={"rule_hints": {"fallback_targets": "not_a_list"}}
        )
        action = self.policy.apply(ctx)
        assert action.target == "P1"  # agent=P2 的第一个非 self 存活

    def test_visible_players_skips_dead(self):
        # 全场只有 P1 ALIVE，其余 DEAD（含自己）
        ctx = vote_context().model_copy(
            update={
                "visible_players": [
                    VisiblePlayer(
                        player_id="P1", status=PlayerStatus.ALIVE, public_claim=None
                    ),
                    VisiblePlayer(
                        player_id="P3", status=PlayerStatus.DEAD, public_claim=None
                    ),
                    VisiblePlayer(
                        player_id="P4", status=PlayerStatus.DEAD, public_claim=None
                    ),
                ]
            }
        )
        action = self.policy.apply(ctx)
        assert action.target == "P1"


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


class TestFallbackErrorPaths:
    def setup_method(self):
        self.policy = ContextAwareFallbackPolicy()

    def test_tie_revote_with_empty_candidates_raises(self):
        ctx = tie_revote_context().model_copy(update={"tie_candidates": []})
        with pytest.raises(FallbackError, match="tie_candidates"):
            self.policy.apply(ctx)

    def test_tie_revote_with_only_self_in_candidates_raises(self):
        # agent=P2，candidates 全是自己
        ctx = tie_revote_context().model_copy(update={"tie_candidates": ["P2"]})
        with pytest.raises(FallbackError, match="tie_candidates"):
            self.policy.apply(ctx)

    def test_night_werewolf_with_no_alive_target_raises(self):
        # agent=P1，其他全 DEAD
        ctx = werewolf_context().model_copy(
            update={
                "visible_players": [
                    VisiblePlayer(
                        player_id="P1", status=PlayerStatus.ALIVE, public_claim=None
                    ),
                    VisiblePlayer(
                        player_id="P2", status=PlayerStatus.DEAD, public_claim=None
                    ),
                ]
            }
        )
        with pytest.raises(FallbackError, match="NIGHT_WEREWOLF"):
            self.policy.apply(ctx)

    def test_day_vote_with_no_alive_target_raises(self):
        ctx = vote_context().model_copy(
            update={
                "visible_players": [
                    VisiblePlayer(
                        player_id="P2", status=PlayerStatus.ALIVE, public_claim=None
                    ),
                    VisiblePlayer(
                        player_id="P1", status=PlayerStatus.DEAD, public_claim=None
                    ),
                ]
            }
        )
        with pytest.raises(FallbackError, match="DAY_VOTE"):
            self.policy.apply(ctx)

    def test_night_seer_with_no_alive_target_raises(self):
        ctx = seer_context().model_copy(
            update={
                "visible_players": [
                    VisiblePlayer(
                        player_id="P3", status=PlayerStatus.ALIVE, public_claim=None
                    )
                ]
            }
        )
        with pytest.raises(FallbackError, match="NIGHT_SEER"):
            self.policy.apply(ctx)

    @pytest.mark.parametrize(
        "phase",
        [
            Phase.INIT,
            Phase.ROLE_ASSIGNMENT,
            Phase.DAY_ANNOUNCEMENT,
            Phase.EXILE_RESOLUTION,
            Phase.NO_EXILE_RESOLUTION,
            Phase.WIN_CHECK,
            Phase.GAME_OVER,
        ],
    )
    def test_non_agent_phase_raises(self, phase):
        ctx = werewolf_context().model_copy(update={"phase": phase})
        with pytest.raises(FallbackError, match="不需要 agent 决策"):
            self.policy.apply(ctx)

    def test_fallback_error_carries_phase_and_role(self):
        ctx = werewolf_context().model_copy(update={"phase": Phase.GAME_OVER})
        try:
            self.policy.apply(ctx)
        except FallbackError as e:
            assert e.phase == Phase.GAME_OVER.value
            assert e.role == Role.WEREWOLF.value
        else:
            pytest.fail("FallbackError 未抛出")


# ---------------------------------------------------------------------------
# Metadata 携带
# ---------------------------------------------------------------------------


class TestFallbackMetadata:
    def setup_method(self):
        self.policy = ContextAwareFallbackPolicy()

    def test_always_marks_fallback_used(self):
        action = self.policy.apply(vote_context())
        assert action.metadata["fallback_used"] is True

    def test_no_error_means_no_reason_keys(self):
        action = self.policy.apply(vote_context())
        assert "fallback_reason" not in action.metadata
        assert "fallback_message" not in action.metadata

    def test_error_class_name_recorded(self):
        action = self.policy.apply(vote_context(), error=ValueError("boom"))
        assert action.metadata["fallback_reason"] == "ValueError"
        assert action.metadata["fallback_message"] == "boom"

    def test_long_error_message_truncated_to_200(self):
        action = self.policy.apply(
            vote_context(), error=ValueError("x" * 500)
        )
        assert len(action.metadata["fallback_message"]) == 200

    def test_hunter_shoot_records_pass_metadata(self):
        action = self.policy.apply(hunter_shoot_context())
        assert action.metadata["pass"] is True


# ---------------------------------------------------------------------------
# Supervisor 兼容性
# ---------------------------------------------------------------------------


class TestSupervisorCompatibility:
    """与 A 的 supervisor.py:_fallback_from_context 行为对齐的关键属性。"""

    def setup_method(self):
        self.policy = ContextAwareFallbackPolicy()

    def test_action_carries_required_identity_fields(self):
        ctx = werewolf_context()
        action = self.policy.apply(ctx)
        assert action.game_id == ctx.game_id
        assert action.agent_id == ctx.agent_id
        assert action.role == ctx.role
        assert action.phase == ctx.phase

    def test_action_serializes_to_json(self):
        # 跟 supervisor.append_events / EventStore 兼容性的关键：必须能 dump 到 JSON
        import json

        action = self.policy.apply(werewolf_context())
        dumped = json.dumps(action.model_dump(mode="json"))
        assert "P2" in dumped  # target 应该在 dump 里

    def test_returns_for_every_core_phase(self):
        """覆盖测试：所有 fixture 的 9 个 phase 都能产生合法 action。"""
        from tests.fixtures.agent_contexts import core_phase_contexts

        for ctx in core_phase_contexts():
            action = self.policy.apply(ctx)
            assert action.phase == ctx.phase
            assert action.metadata["fallback_used"] is True
