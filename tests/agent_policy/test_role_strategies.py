from contracts import (
    ActionType,
    AgentAction,
    AgentContext,
    ClaimedAlignment,
    ClaimResult,
    EventType,
    Phase,
    PlayerStatus,
    PrivateEvent,
    PublicEvent,
    Role,
    Visibility,
    VisiblePlayer,
)
from tests.fixtures.agent_contexts import (
    core_phase_contexts,
    hunter_shoot_context,
    public_check_claim_context,
    seer_context,
    tie_revote_context,
    visible_players as default_visible_players,
    vote_context,
    werewolf_context,
    witch_context,
)

from agent_policy.role_strategies import (
    HunterStrategy,
    RoleStrategyRegistry,
    SeerStrategy,
    VillagerStrategy,
    WerewolfStrategy,
    WitchStrategy,
)


# --- Seer-specific test helpers ---

def _seer_context(
    phase: Phase,
    *,
    round: int = 1,
    private_events: list[PrivateEvent] | None = None,
    public_events: list[PublicEvent] | None = None,
    visible_players: list[VisiblePlayer] | None = None,
    tie_candidates: list[str] | None = None,
    allowed_actions: list[ActionType] | None = None,
) -> AgentContext:
    """构造预言家在指定 phase 的 AgentContext（默认 agent_id=P3）。"""
    default_allowed = {
        Phase.NIGHT_SEER: [ActionType.CHECK],
        Phase.DAY_DISCUSSION: [ActionType.SPEAK],
        Phase.DAY_TIE_DISCUSSION: [ActionType.SPEAK],
        Phase.DAY_VOTE: [ActionType.VOTE],
        Phase.DAY_TIE_REVOTE: [ActionType.VOTE],
        Phase.EXILE_LAST_WORDS: [ActionType.SPEAK],
    }
    return AgentContext(
        game_id="g_seer_test",
        agent_id="P3",
        role=Role.SEER,
        round=round,
        phase=phase,
        visible_players=visible_players or default_visible_players(),
        private_events=private_events or [],
        public_events=public_events or [],
        tie_candidates=tie_candidates or [],
        allowed_actions=allowed_actions or default_allowed.get(phase, [ActionType.SPEAK]),
    )


def _seer_check_event(target: str, alignment: ClaimedAlignment) -> PrivateEvent:
    return PrivateEvent(
        event_type=EventType.SEER_CHECK_RESULT,
        target=target,
        result=alignment.value,
        visibility=Visibility.PRIVATE_TO_SEER,
    )


def _hunter_context(
    phase: Phase,
    *,
    public_events: list[PublicEvent] | None = None,
    visible_players: list[VisiblePlayer] | None = None,
    tie_candidates: list[str] | None = None,
    allowed_actions: list[ActionType] | None = None,
    belief_top_suspects: list[dict] | None = None,
) -> AgentContext:
    default_allowed = {
        Phase.DAY_DISCUSSION: [ActionType.SPEAK],
        Phase.DAY_TIE_DISCUSSION: [ActionType.SPEAK],
        Phase.DAY_VOTE: [ActionType.VOTE],
        Phase.DAY_TIE_REVOTE: [ActionType.VOTE],
        Phase.EXILE_LAST_WORDS: [ActionType.SPEAK],
    }
    return AgentContext(
        game_id="g_hunter_test",
        agent_id="P4",
        role=Role.HUNTER,
        round=1,
        phase=phase,
        visible_players=visible_players or default_visible_players(),
        public_events=public_events or [],
        tie_candidates=tie_candidates or [],
        allowed_actions=allowed_actions or default_allowed.get(phase, [ActionType.SPEAK]),
        belief_top_suspects=belief_top_suspects or [],
    )


def _villager_context(
    phase: Phase,
    *,
    public_events: list[PublicEvent] | None = None,
    visible_players: list[VisiblePlayer] | None = None,
    tie_candidates: list[str] | None = None,
    belief_top_suspects: list[dict] | None = None,
    allowed_actions: list[ActionType] | None = None,
) -> AgentContext:
    default_allowed = {
        Phase.DAY_DISCUSSION: [ActionType.SPEAK],
        Phase.DAY_TIE_DISCUSSION: [ActionType.SPEAK],
        Phase.DAY_VOTE: [ActionType.VOTE],
        Phase.DAY_TIE_REVOTE: [ActionType.VOTE],
        Phase.EXILE_LAST_WORDS: [ActionType.SPEAK],
    }
    return AgentContext(
        game_id="g_villager_test",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=phase,
        visible_players=visible_players or default_visible_players(),
        public_events=public_events or [],
        tie_candidates=tie_candidates or [],
        belief_top_suspects=belief_top_suspects or [],
        allowed_actions=allowed_actions or default_allowed.get(phase, [ActionType.SPEAK]),
    )


def _public_werewolf_claim_event(target: str = "P4") -> PublicEvent:
    return PublicEvent(
        event_id=f"evt_claim_{target}",
        round=1,
        phase=Phase.DAY_DISCUSSION,
        event_type=EventType.SPEECH,
        actor="P3",
        public_message=f"我查验 {target} 是狼人。",
        role_claim=Role.SEER,
        claim_result=ClaimResult(
            target=target,
            claimed_alignment=ClaimedAlignment.WEREWOLF,
        ),
    )


def test_role_strategy_registry_returns_role_specific_strategy():
    registry = RoleStrategyRegistry()

    assert isinstance(registry.get(Role.WEREWOLF), WerewolfStrategy)
    assert isinstance(registry.get(Role.SEER), SeerStrategy)
    assert isinstance(registry.get(Role.WITCH), WitchStrategy)
    assert isinstance(registry.get(Role.HUNTER), HunterStrategy)
    assert isinstance(registry.get(Role.VILLAGER), VillagerStrategy)


def test_role_specific_strategy_methods_live_on_concrete_role_classes():
    assert "decide_werewolf_night" in WerewolfStrategy.__dict__
    assert "decide_speech" in WerewolfStrategy.__dict__
    assert "decide_vote" in WerewolfStrategy.__dict__
    assert "decide_seer_night" in SeerStrategy.__dict__
    assert "decide_speech" in SeerStrategy.__dict__
    assert "decide_vote" in SeerStrategy.__dict__
    assert "decide_witch_night" in WitchStrategy.__dict__
    assert "decide_speech" in WitchStrategy.__dict__
    assert "decide_vote" in WitchStrategy.__dict__
    assert "decide_vote" in VillagerStrategy.__dict__
    assert "decide_speech" in VillagerStrategy.__dict__
    assert "decide_speech" in HunterStrategy.__dict__
    assert "decide_vote" in HunterStrategy.__dict__
    assert "decide_tie_revote" in HunterStrategy.__dict__
    assert "decide_last_words" in HunterStrategy.__dict__
    assert "decide_hunter_shoot" in HunterStrategy.__dict__


def test_werewolf_strategy_prefers_public_claimed_seer_then_avoids_teammates():
    action = WerewolfStrategy().decide(werewolf_context())

    assert action.action_type == ActionType.NIGHT_KILL_NOMINATE
    assert action.target == "P3"


def test_seer_strategy_checks_unchecked_alive_player():
    action = SeerStrategy().decide(seer_context())

    assert action.action_type == ActionType.CHECK
    assert action.target in {"P2", "P4"}


def test_witch_strategy_defaults_to_skip_in_mockable_skeleton():
    action = WitchStrategy().decide(witch_context())

    assert action.action_type == ActionType.SKIP
    assert action.target is None


def test_witch_strategy_saves_known_kill_target_when_save_allowed():
    context = witch_context().model_copy(
        update={
            "private_events": [
                PrivateEvent(
                    event_type=EventType.WITCH_KILL_TARGET_INFO,
                    target="P3",
                    visibility=Visibility.PRIVATE_TO_WITCH,
                )
            ]
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.SAVE
    assert action.target == "P3"


def test_witch_strategy_saves_current_round_kill_target_not_historical_one():
    context = witch_context().model_copy(
        update={
            "round": 3,
            "private_events": [
                PrivateEvent(
                    event_type=EventType.WITCH_KILL_TARGET_INFO,
                    round=1,
                    target="P2",
                    visibility=Visibility.PRIVATE_TO_WITCH,
                ),
                PrivateEvent(
                    event_type=EventType.WITCH_KILL_TARGET_INFO,
                    round=3,
                    target="P5",
                    visibility=Visibility.PRIVATE_TO_WITCH,
                ),
            ],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.SAVE
    assert action.target == "P5"


def test_witch_strategy_saves_self_when_first_night_kill_target_is_self():
    context = witch_context().model_copy(
        update={
            "round": 1,
            "private_events": [
                PrivateEvent(
                    event_type=EventType.WITCH_KILL_TARGET_INFO,
                    round=1,
                    target="P4",
                    visibility=Visibility.PRIVATE_TO_WITCH,
                )
            ]
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.SAVE
    assert action.target == "P4"


def test_witch_strategy_skips_self_save_after_first_night():
    context = witch_context().model_copy(
        update={
            "round": 2,
            "private_events": [
                PrivateEvent(
                    event_type=EventType.WITCH_KILL_TARGET_INFO,
                    round=2,
                    target="P4",
                    visibility=Visibility.PRIVATE_TO_WITCH,
                )
            ],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.SKIP
    assert action.target is None


def test_witch_strategy_ignores_only_historical_kill_target():
    context = witch_context().model_copy(
        update={
            "round": 3,
            "private_events": [
                PrivateEvent(
                    event_type=EventType.WITCH_KILL_TARGET_INFO,
                    round=1,
                    target="P2",
                    visibility=Visibility.PRIVATE_TO_WITCH,
                )
            ],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.SKIP
    assert action.target is None


def test_witch_strategy_poisons_public_werewolf_claim_when_no_save_target():
    context = witch_context().model_copy(
        update={
            "public_events": [
                PublicEvent(
                    event_id="evt_witch_poison_claim",
                    round=1,
                    phase=Phase.DAY_DISCUSSION,
                    event_type=EventType.SPEECH,
                    actor="P3",
                    public_message="我查验 P2 是狼人。",
                    role_claim=Role.SEER,
                    claim_result=ClaimResult(
                        target="P2",
                        claimed_alignment=ClaimedAlignment.WEREWOLF,
                    ),
                )
            ],
            "allowed_actions": [ActionType.POISON, ActionType.SKIP],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.POISON
    assert action.target == "P2"
    assert action.metadata["selected_by"] == "witch_public_claim_poison"


def test_villager_strategy_votes_public_werewolf_claim_target():
    action = VillagerStrategy().decide(public_check_claim_context())

    assert action.action_type == ActionType.VOTE
    assert action.target == "P4"


def test_villager_strategy_can_fallback_to_legal_vote_target():
    action = VillagerStrategy().decide(vote_context())

    assert action.action_type == ActionType.VOTE
    assert action.target != vote_context().agent_id


def test_villager_strategy_votes_top_belief_suspect_when_no_public_claim():
    context = vote_context().model_copy(
        update={
            "belief_top_suspects": [
                {"player_id": "P4", "werewolf_prob": 0.72},
                {"player_id": "P1", "werewolf_prob": 0.61},
            ]
        },
        deep=True,
    )

    action = VillagerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P4"


def test_villager_speech_mentions_public_werewolf_claim_without_claiming_role():
    context = _villager_context(
        Phase.DAY_DISCUSSION,
        public_events=[_public_werewolf_claim_event("P4")],
    )

    action = VillagerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert "P4" in (action.public_message or "")
    assert "无脑跟票" in (action.public_message or "")
    assert action.role_claim is None
    assert action.metadata["selected_by"] == "villager_public_claim_speech"


def test_villager_speech_mentions_belief_as_suspicion_only():
    context = _villager_context(
        Phase.DAY_DISCUSSION,
        belief_top_suspects=[{"player_id": "P4", "werewolf_prob": 0.70}],
    )

    action = VillagerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert "P4" in (action.public_message or "")
    assert "嫌疑" in (action.public_message or "")
    assert "一定是狼人" not in (action.public_message or "")
    assert action.metadata["selected_by"] == "villager_belief_speech"


def test_villager_tie_discussion_mentions_tie_candidates():
    context = _villager_context(
        Phase.DAY_TIE_DISCUSSION,
        tie_candidates=["P3", "P4"],
    )

    action = VillagerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert "P3" in (action.public_message or "")
    assert "P4" in (action.public_message or "")
    assert action.metadata["selected_by"] == "villager_tie_discussion_speech"


def test_villager_vote_ignores_dead_public_claim_target_and_uses_belief():
    visible = [
        VisiblePlayer(player_id="P1", status=PlayerStatus.ALIVE),
        VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE),
        VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE),
        VisiblePlayer(player_id="P4", status=PlayerStatus.DEAD),
    ]
    context = _villager_context(
        Phase.DAY_VOTE,
        visible_players=visible,
        public_events=[_public_werewolf_claim_event("P4")],
        belief_top_suspects=[{"player_id": "P3", "werewolf_prob": 0.70}],
    )

    action = VillagerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P3"
    assert action.metadata["selected_by"] == "villager_belief_vote"


def test_villager_tie_revote_uses_belief_within_tie_candidates():
    context = tie_revote_context().model_copy(
        update={
            "belief_top_suspects": [
                {"player_id": "P1", "werewolf_prob": 0.95},
                {"player_id": "P4", "werewolf_prob": 0.60},
                {"player_id": "P3", "werewolf_prob": 0.20},
            ]
        },
        deep=True,
    )

    action = VillagerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P4"


def test_witch_strategy_poisons_high_belief_suspect_when_no_save_target_or_claim():
    context = witch_context().model_copy(
        update={
            "allowed_actions": [ActionType.POISON, ActionType.SKIP],
            "belief_top_suspects": [{"player_id": "P2", "werewolf_prob": 0.82}],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.POISON
    assert action.target == "P2"
    assert action.metadata["selected_by"] == "witch_belief_poison"


def test_witch_speech_mentions_public_claim_without_revealing_identity():
    context = witch_context().model_copy(
        update={
            "phase": Phase.DAY_DISCUSSION,
            "allowed_actions": [ActionType.SPEAK],
            "public_events": [_public_werewolf_claim_event("P2")],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert "P2" in (action.public_message or "")
    assert "女巫" not in (action.public_message or "")
    assert "刀口" not in (action.public_message or "")
    assert action.metadata["selected_by"] == "witch_public_claim_speech"


def test_witch_vote_uses_belief_when_no_alive_public_claim():
    context = witch_context().model_copy(
        update={
            "phase": Phase.DAY_VOTE,
            "allowed_actions": [ActionType.VOTE],
            "belief_top_suspects": [{"player_id": "P2", "werewolf_prob": 0.72}],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P2"
    assert action.metadata["selected_by"] == "witch_belief_vote"


def test_witch_tie_revote_uses_belief_within_candidates():
    context = witch_context().model_copy(
        update={
            "phase": Phase.DAY_TIE_REVOTE,
            "allowed_actions": [ActionType.VOTE],
            "tie_candidates": ["P2", "P3"],
            "belief_top_suspects": [
                {"player_id": "P1", "werewolf_prob": 0.95},
                {"player_id": "P3", "werewolf_prob": 0.63},
            ],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P3"
    assert action.metadata["selected_by"] == "witch_tie_belief_vote"


def test_witch_strategy_skips_low_belief_suspect_without_public_claim():
    context = witch_context().model_copy(
        update={
            "allowed_actions": [ActionType.POISON, ActionType.SKIP],
            "belief_top_suspects": [{"player_id": "P2", "werewolf_prob": 0.60}],
        },
        deep=True,
    )

    action = WitchStrategy().decide(context)

    assert action.action_type == ActionType.SKIP
    assert action.target is None


def test_hunter_strategy_shoots_top_belief_suspect():
    context = hunter_shoot_context().model_copy(
        update={"belief_top_suspects": [{"player_id": "P2", "werewolf_prob": 0.78}]},
        deep=True,
    )

    action = HunterStrategy().decide(context)

    assert action.action_type == ActionType.HUNTER_SHOOT
    assert action.target == "P2"


def test_hunter_strategy_passes_without_high_belief_suspect():
    context = hunter_shoot_context().model_copy(
        update={"belief_top_suspects": [{"player_id": "P2", "werewolf_prob": 0.30}]},
        deep=True,
    )

    action = HunterStrategy().decide(context)

    assert action.action_type == ActionType.HUNTER_SHOOT
    assert action.target is None


def test_hunter_strategy_passes_medium_belief_suspect_to_avoid_random_shot():
    context = hunter_shoot_context().model_copy(
        update={"belief_top_suspects": [{"player_id": "P2", "werewolf_prob": 0.62}]},
        deep=True,
    )

    action = HunterStrategy().decide(context)

    assert action.action_type == ActionType.HUNTER_SHOOT
    assert action.target is None


def test_hunter_strategy_day_speech_stays_hidden():
    action = HunterStrategy().decide(_hunter_context(Phase.DAY_DISCUSSION))

    assert action.action_type == ActionType.SPEAK
    assert "猎人" not in (action.public_message or "")
    assert "枪" not in (action.public_message or "")
    assert action.metadata["selected_by"] == "hunter_hidden_day_speech"


def test_hunter_vote_ignores_dead_public_claim_target_and_uses_belief():
    visible = [
        VisiblePlayer(player_id="P1", status=PlayerStatus.ALIVE),
        VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE),
        VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE),
        VisiblePlayer(player_id="P4", status=PlayerStatus.ALIVE),
        VisiblePlayer(player_id="P5", status=PlayerStatus.DEAD),
    ]
    context = _hunter_context(
        Phase.DAY_VOTE,
        visible_players=visible,
        public_events=[_public_werewolf_claim_event("P5")],
        belief_top_suspects=[{"player_id": "P2", "werewolf_prob": 0.70}],
    )

    action = HunterStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P2"
    assert action.metadata["selected_by"] == "hunter_belief_vote"


def test_hunter_strategy_votes_top_belief_suspect_when_no_public_claim():
    context = _hunter_context(
        Phase.DAY_VOTE,
        belief_top_suspects=[{"player_id": "P2", "werewolf_prob": 0.76}],
    )

    action = HunterStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P2"


def test_hunter_tie_revote_uses_belief_within_tie_candidates():
    context = _hunter_context(
        Phase.DAY_TIE_REVOTE,
        tie_candidates=["P2", "P3"],
        belief_top_suspects=[
            {"player_id": "P1", "werewolf_prob": 0.95},
            {"player_id": "P3", "werewolf_prob": 0.60},
        ],
    )

    action = HunterStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P3"


def test_hunter_last_words_speaks_with_role_info():
    action = HunterStrategy().decide(_hunter_context(Phase.EXILE_LAST_WORDS))

    assert action.action_type == ActionType.SPEAK
    assert "猎人" in (action.public_message or "")


def test_all_role_strategies_output_contract_agent_actions_for_core_contexts():
    registry = RoleStrategyRegistry()

    for context in core_phase_contexts():
        action = registry.get(context.role).decide(context)
        validated = AgentAction.model_validate(action.model_dump(mode="json"))

        assert validated.game_id == context.game_id
        assert validated.agent_id == context.agent_id
        assert validated.role == context.role
        assert validated.phase == context.phase
        assert validated.action_type in context.allowed_actions


# --- SeerStrategy: 白天发言 ---

def test_seer_speech_jumps_when_known_werewolf_alive():
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=1,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    assert action.claim_result is not None
    assert action.claim_result.target == "P1"
    assert action.claim_result.claimed_alignment == ClaimedAlignment.WEREWOLF
    assert "P1" in (action.public_message or "")


def test_seer_speech_stays_subtle_when_only_villager_known_round_1():
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=1,
        private_events=[_seer_check_event("P2", ClaimedAlignment.VILLAGER)],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert action.role_claim is None
    assert action.claim_result is None


def test_seer_speech_jumps_with_villager_align_round_2_plus():
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=2,
        private_events=[_seer_check_event("P2", ClaimedAlignment.VILLAGER)],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    assert action.claim_result is not None
    assert action.claim_result.target == "P2"
    assert action.claim_result.claimed_alignment == ClaimedAlignment.VILLAGER


def test_seer_speech_stays_subtle_when_no_check_yet():
    context = _seer_context(Phase.DAY_DISCUSSION, round=1)

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert action.role_claim is None
    assert action.claim_result is None


def test_seer_speech_ignores_dead_werewolf():
    visible = [
        VisiblePlayer(player_id="P1", status=PlayerStatus.DEAD, public_claim=None),
        VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE, public_claim=None),
        VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE, public_claim=None),
        VisiblePlayer(player_id="P4", status=PlayerStatus.ALIVE, public_claim=None),
    ]
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=2,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
        visible_players=visible,
    )

    action = SeerStrategy().decide(context)

    # 已查到的狼死了：不应再以查杀为由跳明
    assert action.action_type == ActionType.SPEAK
    assert action.role_claim is None
    assert action.claim_result is None


# --- SeerStrategy: 白天投票 ---

def test_seer_vote_targets_known_werewolf():
    context = _seer_context(
        Phase.DAY_VOTE,
        round=1,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P1"


def test_seer_vote_falls_back_when_known_werewolf_dead():
    visible = [
        VisiblePlayer(player_id="P1", status=PlayerStatus.DEAD, public_claim=None),
        VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE, public_claim=None),
        VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE, public_claim=None),
        VisiblePlayer(player_id="P4", status=PlayerStatus.ALIVE, public_claim=None),
    ]
    context = _seer_context(
        Phase.DAY_VOTE,
        round=2,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
        visible_players=visible,
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target != "P1"
    assert action.target != "P3"  # not self


def test_seer_vote_falls_back_to_public_check_claim():
    context = _seer_context(
        Phase.DAY_VOTE,
        round=1,
        public_events=[
            PublicEvent(
                event_id="evt_claim",
                round=1,
                phase=Phase.DAY_DISCUSSION,
                event_type=EventType.SPEECH,
                actor="P2",
                public_message="我查 P4 是狼。",
                role_claim=Role.SEER,
                claim_result=ClaimResult(
                    target="P4",
                    claimed_alignment=ClaimedAlignment.WEREWOLF,
                ),
            )
        ],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P4"


# --- SeerStrategy: 平票二次投票 ---

def test_seer_tie_revote_prefers_known_werewolf_in_candidates():
    context = _seer_context(
        Phase.DAY_TIE_REVOTE,
        round=1,
        private_events=[_seer_check_event("P4", ClaimedAlignment.WEREWOLF)],
        tie_candidates=["P2", "P4"],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target == "P4"


def test_seer_tie_revote_falls_back_to_first_candidate():
    context = _seer_context(
        Phase.DAY_TIE_REVOTE,
        round=1,
        tie_candidates=["P2", "P4"],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.VOTE
    assert action.target in {"P2", "P4"}


# --- SeerStrategy: 遗言 ---

def test_seer_last_words_full_disclosure():
    context = _seer_context(
        Phase.EXILE_LAST_WORDS,
        round=2,
        private_events=[
            _seer_check_event("P1", ClaimedAlignment.WEREWOLF),
            _seer_check_event("P2", ClaimedAlignment.VILLAGER),
        ],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    message = action.public_message or ""
    assert "P1" in message  # 已查狼
    assert "P2" in message  # 已查金水
    assert "P4" in message  # 漏验（default_visible_players 中 alive 非 self 非 P1/P2）
    # 元数据要写清楚
    assert action.metadata.get("known_werewolves") == ["P1"]
    assert action.metadata.get("known_villagers") == ["P2"]
    assert "P4" in action.metadata.get("unchecked_alive", [])


def _visible_9p(
    alive_ids: set[str] | None = None,
    public_claims: dict[str, str] | None = None,
) -> list[VisiblePlayer]:
    """构造 9 人 visible_players。默认 P1-P9 全活。"""
    alive_ids = alive_ids if alive_ids is not None else set(f"P{i}" for i in range(1, 10))
    public_claims = public_claims or {}
    return [
        VisiblePlayer(
            player_id=f"P{i}",
            status=PlayerStatus.ALIVE if f"P{i}" in alive_ids else PlayerStatus.DEAD,
            public_claim=public_claims.get(f"P{i}"),
        )
        for i in range(1, 10)
    ]


# --- SeerStrategy: 9 人 D1 hold + 被悍跳应对（S4 Phase 3）---

def test_seer_9p_d1_holds_even_with_known_werewolf():
    """9 人 D1 + 查到狼 + 无悍跳 → 默认不跳明（避免秒死）。"""
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=1,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
        visible_players=_visible_9p(),
    )
    action = SeerStrategy().decide(context)
    assert action.action_type == ActionType.SPEAK
    assert action.role_claim is None
    assert action.claim_result is None
    assert action.metadata.get("selected_by") == "seer_9p_d1_hold"


def test_seer_9p_d1_counter_claims_when_other_jumps_seer():
    """9 人 D1 + 查到狼 + 公开有他人跳预言家 → 对跳查杀（不能让狼牌染色）。"""
    fake_seer_speech = PublicEvent(
        event_id="evt_fake_seer",
        round=1,
        phase=Phase.DAY_DISCUSSION,
        event_type=EventType.SPEECH,
        actor="P5",
        public_message="我是预言家。",
        role_claim=Role.SEER,
    )
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=1,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
        public_events=[fake_seer_speech],
        visible_players=_visible_9p(),
    )
    action = SeerStrategy().decide(context)
    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    assert action.claim_result is not None
    assert action.claim_result.target == "P1"
    assert action.claim_result.claimed_alignment == ClaimedAlignment.WEREWOLF
    assert action.metadata.get("selected_by") == "seer_counter_claim_with_werewolf_kill"


def test_seer_9p_d1_counter_claims_with_villager_when_no_werewolf():
    """9 人 D1 + 仅查到金水 + 被悍跳 → 对跳报金水兜底（不让狼牌染色）。"""
    fake_seer_speech = PublicEvent(
        event_id="evt_fake_seer",
        round=1,
        phase=Phase.DAY_DISCUSSION,
        event_type=EventType.SPEECH,
        actor="P5",
        public_message="我是预言家。",
        role_claim=Role.SEER,
    )
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=1,
        private_events=[_seer_check_event("P2", ClaimedAlignment.VILLAGER)],
        public_events=[fake_seer_speech],
        visible_players=_visible_9p(),
    )
    action = SeerStrategy().decide(context)
    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    assert action.claim_result is not None
    assert action.claim_result.target == "P2"
    assert action.claim_result.claimed_alignment == ClaimedAlignment.VILLAGER
    assert action.metadata.get("selected_by") == "seer_counter_claim_with_villager_align"


def test_seer_9p_d2_jumps_with_known_werewolf():
    """9 人 D2+ + 查到狼 → 必跳查杀（不再"永远不跳"）。"""
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=2,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
        visible_players=_visible_9p(),
    )
    action = SeerStrategy().decide(context)
    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    assert action.claim_result is not None
    assert action.claim_result.target == "P1"
    assert action.claim_result.claimed_alignment == ClaimedAlignment.WEREWOLF
    assert action.metadata.get("selected_by") == "seer_claim_with_werewolf_kill"


def test_seer_9p_d2_jumps_with_villager_when_no_werewolf():
    """9 人 D2 + 无狼 + 仅金水 → 跳明报金水（同 6 人 D2+ 行为）。"""
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=2,
        private_events=[_seer_check_event("P2", ClaimedAlignment.VILLAGER)],
        visible_players=_visible_9p(),
    )
    action = SeerStrategy().decide(context)
    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    assert action.claim_result is not None
    assert action.claim_result.target == "P2"
    assert action.claim_result.claimed_alignment == ClaimedAlignment.VILLAGER


def test_seer_9p_d1_no_check_holds_silent():
    """9 人 D1 + 没查到任何信息 → 默认 hold（含蓄发言，不跳）。"""
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=1,
        visible_players=_visible_9p(),
    )
    action = SeerStrategy().decide(context)
    assert action.action_type == ActionType.SPEAK
    assert action.role_claim is None
    assert action.metadata.get("selected_by") == "seer_9p_d1_hold"


def test_seer_9p_own_seer_claim_does_not_trigger_counter_claim():
    """自己上一轮的 self_claim Seer 不应被识别为"他人悍跳"。"""
    own_speech = PublicEvent(
        event_id="evt_own_claim",
        round=1,
        phase=Phase.DAY_DISCUSSION,
        event_type=EventType.SPEECH,
        actor="P3",  # 自己就是 P3（_seer_context 默认 agent_id=P3）
        public_message="我是预言家。",
        role_claim=Role.SEER,
    )
    context = _seer_context(
        Phase.DAY_DISCUSSION,
        round=1,
        private_events=[_seer_check_event("P1", ClaimedAlignment.WEREWOLF)],
        public_events=[own_speech],
        visible_players=_visible_9p(),
    )
    action = SeerStrategy().decide(context)
    # 9 人 D1 + 没有他人悍跳 → 默认 hold（即使自己已 claim 过）
    assert action.metadata.get("selected_by") == "seer_9p_d1_hold"


def test_seer_last_words_handles_no_info():
    context = _seer_context(
        Phase.EXILE_LAST_WORDS,
        round=1,
        # 把所有非自己的玩家都设为已查过，并标记为 villager（不会触发跳金水但消化漏验）
        # 这里反而构造"全部已查、无狼无遗漏"场景
        private_events=[
            _seer_check_event("P1", ClaimedAlignment.VILLAGER),
            _seer_check_event("P2", ClaimedAlignment.VILLAGER),
            _seer_check_event("P4", ClaimedAlignment.VILLAGER),
        ],
        visible_players=[
            VisiblePlayer(player_id="P1", status=PlayerStatus.DEAD, public_claim=None),
            VisiblePlayer(player_id="P2", status=PlayerStatus.DEAD, public_claim=None),
            VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE, public_claim=None),
            VisiblePlayer(player_id="P4", status=PlayerStatus.DEAD, public_claim=None),
        ],
    )

    action = SeerStrategy().decide(context)

    assert action.action_type == ActionType.SPEAK
    assert action.role_claim == Role.SEER
    # 没有任何存活的狼/金水/漏验 → 说兜底句
    assert "没有查到关键信息" in (action.public_message or "")
