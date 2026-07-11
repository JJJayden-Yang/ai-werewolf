"""VisibilityRuleSpec 测试。

覆盖：
- visible_public_events: 白名单 + PUBLIC visibility
- visible_private_events: 按 visibility + agent_role 匹配
- visible_players: 不暴露 role
- allowed_actions: 复用 RuleValidator
- 系统事件不进 AI 简报
- GameEvent → PublicEvent / PrivateEvent 字段映射
"""

from __future__ import annotations

import pytest

from contracts import (
    ActionType,
    ClaimedAlignment,
    EventType,
    Phase,
    PlayerStatus,
    Role,
    Visibility,
)

from context.visibility_rules import (
    PRIVATE_EVENT_TYPES,
    PUBLIC_EVENT_TYPES,
    VisibilityRuleSpec,
)
from tests.context.conftest import make_6p_session, make_event


# ---------------------------------------------------------------------------
# visible_public_events
# ---------------------------------------------------------------------------


class TestVisiblePublicEvents:
    def setup_method(self):
        self.vis = VisibilityRuleSpec()
        self.session = make_6p_session()

    def test_public_speech_visible_to_all(self):
        ev = make_event(
            EventType.SPEECH,
            actor="P1",
            payload={"public_message": "I think P3 is suspicious."},
            visibility=Visibility.PUBLIC,
        )
        result = self.vis.visible_public_events([ev], self.session, "P3")
        assert len(result) == 1
        assert result[0].actor == "P1"
        assert result[0].public_message == "I think P3 is suspicious."

    def test_role_assigned_filtered_out_even_if_public(self):
        """系统事件 ROLE_ASSIGNED 即使误标 PUBLIC，第一层白名单也拦住。"""
        ev = make_event(
            EventType.ROLE_ASSIGNED,
            payload={"role": "werewolf"},
            visibility=Visibility.PUBLIC,
        )
        result = self.vis.visible_public_events([ev], self.session, "P3")
        assert result == []

    def test_private_event_not_returned_as_public(self):
        ev = make_event(
            EventType.SEER_CHECK_RESULT,
            actor="P3",
            target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
        )
        result = self.vis.visible_public_events([ev], self.session, "P3")
        assert result == []

    def test_night_kill_target_not_returned_as_public(self):
        """夜刀刀口不是公开信息；平安夜时普通玩家不能反推出谁被刀/被救。"""
        ev = make_event(
            EventType.NIGHT_KILL_ANNOUNCED,
            target="P1",
            visibility=Visibility.PUBLIC,
        )
        result = self.vis.visible_public_events([ev], self.session, "P6")
        assert result == []

    def test_public_whitelist_size(self):
        # 夜刀刀口不是公开信息；公开事件只保留白天公告/发言/投票/死亡结果等。
        assert len(PUBLIC_EVENT_TYPES) == 10
        assert EventType.NIGHT_KILL_ANNOUNCED not in PUBLIC_EVENT_TYPES
        assert EventType.SPEECH in PUBLIC_EVENT_TYPES
        assert EventType.VOTE_CAST in PUBLIC_EVENT_TYPES
        assert EventType.EXILE in PUBLIC_EVENT_TYPES
        assert EventType.GAME_OVER in PUBLIC_EVENT_TYPES

    def test_payload_fields_extracted(self):
        ev = make_event(
            EventType.SPEECH,
            actor="P3",
            payload={
                "public_message": "I am the seer, P1 is wolf.",
                "role_claim": "seer",
                "claim_result": {"target": "P1", "claimed_alignment": "werewolf"},
                "summary": "self_claim seer + check P1 wolf",
            },
        )
        result = self.vis.visible_public_events([ev], self.session, "P2")
        assert len(result) == 1
        pe = result[0]
        assert pe.role_claim == Role.SEER
        assert pe.claim_result is not None
        assert pe.claim_result.target == "P1"
        assert pe.claim_result.claimed_alignment == ClaimedAlignment.WEREWOLF
        assert pe.summary == "self_claim seer + check P1 wolf"


# ---------------------------------------------------------------------------
# visible_private_events
# ---------------------------------------------------------------------------


class TestVisiblePrivateEvents:
    def setup_method(self):
        self.vis = VisibilityRuleSpec()
        self.session = make_6p_session()

    def test_wolf_sees_teammate_nomination(self):
        # P1 狼，P2 狼也是狼。P2 提名 P3 的事件 P1 能看到
        ev = make_event(
            EventType.WOLF_NOMINATION,
            actor="P2",
            target="P3",
            visibility=Visibility.PRIVATE_TO_WOLVES,
            payload={"teammates": ["P1", "P2"]},
        )
        result = self.vis.visible_private_events([ev], self.session, "P1")
        assert len(result) == 1
        assert result[0].target == "P3"
        assert result[0].teammates == ["P1", "P2"]

    def test_villager_does_not_see_wolf_nomination(self):
        ev = make_event(
            EventType.WOLF_NOMINATION,
            actor="P2",
            target="P3",
            visibility=Visibility.PRIVATE_TO_WOLVES,
        )
        result = self.vis.visible_private_events([ev], self.session, "P6")  # villager
        assert result == []

    def test_seer_sees_own_check_result(self):
        ev = make_event(
            EventType.SEER_CHECK_RESULT,
            actor="P3",
            target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "werewolf"},
        )
        result = self.vis.visible_private_events([ev], self.session, "P3")
        assert len(result) == 1
        assert result[0].target == "P1"
        assert result[0].result == "werewolf"

    def test_non_seer_does_not_see_seer_check_result(self):
        ev = make_event(
            EventType.SEER_CHECK_RESULT,
            actor="P3",
            target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
        )
        # P4 是 witch，看不到 seer 私密信息
        result = self.vis.visible_private_events([ev], self.session, "P4")
        assert result == []

    def test_witch_sees_kill_target_info(self):
        ev = make_event(
            EventType.WITCH_KILL_TARGET_INFO,
            target="P3",
            visibility=Visibility.PRIVATE_TO_WITCH,
        )
        result = self.vis.visible_private_events([ev], self.session, "P4")  # witch
        assert len(result) == 1
        assert result[0].target == "P3"

    def test_witch_save_visible_only_to_witch(self):
        ev = make_event(
            EventType.WITCH_SAVE,
            actor="P4",
            target="P3",
            visibility=Visibility.PRIVATE_TO_WITCH,
        )
        # 女巫能看到自己
        assert (
            len(self.vis.visible_private_events([ev], self.session, "P4")) == 1
        )
        # 别人看不到
        assert self.vis.visible_private_events([ev], self.session, "P3") == []
        assert self.vis.visible_private_events([ev], self.session, "P6") == []

    def test_other_wolf_seer_check_actor_filtered(self):
        """SEER_CHECK_RESULT 的 actor 不是自己 → 即使是 seer 角色也跳过。"""
        ev = make_event(
            EventType.SEER_CHECK_RESULT,
            actor="P2",  # P2 是狼，伪造了一个查验事件 (恶意 / bug)
            target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
        )
        # P3 是真预言家，但 actor 不是 P3 → 跳过
        result = self.vis.visible_private_events([ev], self.session, "P3")
        assert result == []

    def test_seer_check_result_with_no_actor_still_visible(self):
        """系统填的 SEER_CHECK_RESULT（actor=None）预言家能看到。"""
        ev = make_event(
            EventType.SEER_CHECK_RESULT,
            actor=None,
            target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "werewolf"},
        )
        result = self.vis.visible_private_events([ev], self.session, "P3")
        assert len(result) == 1

    def test_private_whitelist_size(self):
        assert len(PRIVATE_EVENT_TYPES) == 5
        assert EventType.WOLF_NOMINATION in PRIVATE_EVENT_TYPES
        assert EventType.SEER_CHECK_RESULT in PRIVATE_EVENT_TYPES
        assert EventType.WITCH_KILL_TARGET_INFO in PRIVATE_EVENT_TYPES

    def test_unknown_agent_id_returns_empty(self):
        ev = make_event(
            EventType.WOLF_NOMINATION,
            actor="P1",
            target="P3",
            visibility=Visibility.PRIVATE_TO_WOLVES,
        )
        result = self.vis.visible_private_events([ev], self.session, "P99")
        assert result == []

    # --- PrivateEvent.round passthrough (Phase 3 P0#1) ---------------------
    # 三个消费方的取数方式：女巫 max round / 预言家忽略 round / 狼 roster 跨轮共用

    def test_private_event_carries_round_for_witch(self):
        """女巫拿到的 WITCH_KILL_TARGET_INFO 带 round；多夜取 max round 锁定当晚刀口。"""
        n1 = make_event(
            EventType.WITCH_KILL_TARGET_INFO,
            round_num=1,
            phase=Phase.NIGHT_WITCH,
            target="P3",
            visibility=Visibility.PRIVATE_TO_WITCH,
        )
        n2 = make_event(
            EventType.WITCH_KILL_TARGET_INFO,
            round_num=2,
            phase=Phase.NIGHT_WITCH,
            target="P5",
            visibility=Visibility.PRIVATE_TO_WITCH,
        )
        result = self.vis.visible_private_events([n1, n2], self.session, "P4")
        assert [pe.round for pe in result] == [1, 2]
        latest = max(result, key=lambda pe: pe.round or 0)
        assert latest.target == "P5"

    def test_private_event_carries_round_for_seer_full_history(self):
        """预言家忽略 round 读全量查验史 —— 第 1/2 夜两条都进来。"""
        n1 = make_event(
            EventType.SEER_CHECK_RESULT,
            round_num=1,
            phase=Phase.NIGHT_SEER,
            actor="P3",
            target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "werewolf"},
        )
        n2 = make_event(
            EventType.SEER_CHECK_RESULT,
            round_num=2,
            phase=Phase.NIGHT_SEER,
            actor="P3",
            target="P6",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "villager"},
        )
        result = self.vis.visible_private_events([n1, n2], self.session, "P3")
        rounds = [pe.round for pe in result]
        assert rounds == [1, 2]
        targets = {pe.target: pe.result for pe in result}
        assert targets == {"P1": "werewolf", "P6": "villager"}

    def test_private_event_carries_round_for_wolf_roster(self):
        """狼 WOLF_NOMINATION 开局发一次（round=1），跨轮共用：round 字段透传即可。"""
        roster = make_event(
            EventType.WOLF_NOMINATION,
            round_num=1,
            actor=None,
            target=None,
            visibility=Visibility.PRIVATE_TO_WOLVES,
            payload={"teammates": ["P1", "P2"]},
        )
        result = self.vis.visible_private_events([roster], self.session, "P1")
        assert len(result) == 1
        assert result[0].round == 1
        assert result[0].teammates == ["P1", "P2"]


# ---------------------------------------------------------------------------
# visible_players
# ---------------------------------------------------------------------------


class TestVisiblePlayers:
    def setup_method(self):
        self.vis = VisibilityRuleSpec()

    def test_returns_all_players_with_status(self):
        session = make_6p_session()
        result = self.vis.visible_players(session, "P3")
        assert len(result) == 6
        for vp in result:
            assert vp.status == PlayerStatus.ALIVE

    def test_does_not_expose_role(self):
        """关键红线：VisiblePlayer 不能含 role 字段。"""
        session = make_6p_session()
        result = self.vis.visible_players(session, "P1")  # P1 是狼
        # P2 也是狼，但 visible 里看不到
        p2 = next(vp for vp in result if vp.player_id == "P2")
        # VisiblePlayer 的 schema 字段：player_id, status, public_claim
        # 完全没有 role —— 应该 raise AttributeError 或返回 None
        assert not hasattr(p2, "role") or getattr(p2, "role", None) is None

    def test_dead_player_status_visible(self):
        from contracts import PlayerStatus

        from tests.context.conftest import make_player

        session = make_6p_session(
            overrides={"P5": make_player(Role.HUNTER, PlayerStatus.DEAD)}
        )
        result = self.vis.visible_players(session, "P3")
        p5 = next(vp for vp in result if vp.player_id == "P5")
        assert p5.status == PlayerStatus.DEAD

    def test_public_claim_visible(self):
        from tests.context.conftest import make_player

        session = make_6p_session(
            overrides={"P3": make_player(Role.SEER, public_claim="seer")}
        )
        result = self.vis.visible_players(session, "P1")
        p3 = next(vp for vp in result if vp.player_id == "P3")
        assert p3.public_claim == "seer"

    def test_deterministic_order(self):
        session = make_6p_session()
        result = self.vis.visible_players(session, "P3")
        ids = [vp.player_id for vp in result]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# allowed_actions
# ---------------------------------------------------------------------------


class TestAllowedActions:
    def setup_method(self):
        self.vis = VisibilityRuleSpec()
        self.session = make_6p_session()

    @pytest.mark.parametrize(
        "phase,expected_action",
        [
            (Phase.NIGHT_WEREWOLF, ActionType.NIGHT_KILL_NOMINATE),
            (Phase.NIGHT_SEER, ActionType.CHECK),
            (Phase.DAY_DISCUSSION, ActionType.SPEAK),
            (Phase.DAY_VOTE, ActionType.VOTE),
            (Phase.DAY_TIE_DISCUSSION, ActionType.SPEAK),
            (Phase.DAY_TIE_REVOTE, ActionType.VOTE),
            (Phase.EXILE_LAST_WORDS, ActionType.SPEAK),
            (Phase.HUNTER_SHOOT, ActionType.HUNTER_SHOOT),
        ],
    )
    def test_each_phase_has_expected_action(self, phase, expected_action):
        result = self.vis.allowed_actions(self.session, "P1", phase)
        assert expected_action in result

    def test_witch_phase_has_three_actions(self):
        result = self.vis.allowed_actions(self.session, "P4", Phase.NIGHT_WITCH)
        assert ActionType.SAVE in result
        assert ActionType.POISON in result
        assert ActionType.SKIP in result

    def test_non_agent_phase_returns_empty(self):
        result = self.vis.allowed_actions(self.session, "P1", Phase.INIT)
        assert result == []

    # 按 witch_state 收窄（P0 #2，2026-05-25）：解药/毒药用过即从集合移除对应动作。
    def test_witch_antidote_used_drops_save(self):
        self.session.truth_state.witch_state.antidote_used = True
        result = self.vis.allowed_actions(self.session, "P4", Phase.NIGHT_WITCH)
        assert ActionType.SAVE not in result
        assert ActionType.POISON in result
        assert ActionType.SKIP in result

    def test_witch_poison_used_drops_poison(self):
        self.session.truth_state.witch_state.poison_used = True
        result = self.vis.allowed_actions(self.session, "P4", Phase.NIGHT_WITCH)
        assert ActionType.POISON not in result
        assert ActionType.SAVE in result
        assert ActionType.SKIP in result

    def test_witch_both_potions_used_leaves_only_skip(self):
        self.session.truth_state.witch_state.antidote_used = True
        self.session.truth_state.witch_state.poison_used = True
        result = self.vis.allowed_actions(self.session, "P4", Phase.NIGHT_WITCH)
        assert result == [ActionType.SKIP]

    def test_non_witch_in_witch_phase_unaffected_by_witch_state(self):
        # 非女巫 agent 在 NIGHT_WITCH 阶段拿到的 allowed_actions 仍是 phase 级原集合，
        # 即使 truth_state.witch_state.antidote_used=True。
        # （理论上非女巫不会在 NIGHT_WITCH 被调度，但 spec 不依赖该假设。）
        self.session.truth_state.witch_state.antidote_used = True
        result = self.vis.allowed_actions(self.session, "P1", Phase.NIGHT_WITCH)
        assert ActionType.SAVE in result
        assert ActionType.POISON in result
        assert ActionType.SKIP in result
