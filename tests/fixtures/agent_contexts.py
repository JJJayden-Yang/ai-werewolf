from contracts import (
    ActionType,
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


def visible_players() -> list[VisiblePlayer]:
    return [
        VisiblePlayer(player_id="P1", status=PlayerStatus.ALIVE, public_claim=None),
        VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE, public_claim=None),
        VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE, public_claim=Role.SEER.value),
        VisiblePlayer(player_id="P4", status=PlayerStatus.ALIVE, public_claim=None),
        VisiblePlayer(player_id="P5", status=PlayerStatus.DEAD, public_claim=None),
    ]


def werewolf_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P1",
        role=Role.WEREWOLF,
        round=1,
        phase=Phase.NIGHT_WEREWOLF,
        visible_players=visible_players(),
        private_events=[
            PrivateEvent(
                event_type=EventType.ROLE_ASSIGNED,
                teammates=["P1", "P2"],
                visibility=Visibility.PRIVATE_TO_WOLVES,
            )
        ],
        allowed_actions=[ActionType.NIGHT_KILL_NOMINATE],
    )


def seer_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P3",
        role=Role.SEER,
        round=2,
        phase=Phase.NIGHT_SEER,
        visible_players=visible_players(),
        private_events=[
            PrivateEvent(
                event_type=EventType.SEER_CHECK_RESULT,
                target="P1",
                result=ClaimedAlignment.WEREWOLF.value,
                visibility=Visibility.PRIVATE_TO_SEER,
            )
        ],
        allowed_actions=[ActionType.CHECK],
    )


def witch_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P4",
        role=Role.WITCH,
        round=1,
        phase=Phase.NIGHT_WITCH,
        visible_players=visible_players(),
        allowed_actions=[ActionType.SAVE, ActionType.POISON, ActionType.SKIP],
    )


def day_discussion_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_DISCUSSION,
        visible_players=visible_players(),
        allowed_actions=[ActionType.SPEAK],
    )


def vote_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_VOTE,
        visible_players=visible_players(),
        allowed_actions=[ActionType.VOTE],
    )


def tie_discussion_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_TIE_DISCUSSION,
        visible_players=visible_players(),
        tie_candidates=["P3", "P4"],
        allowed_actions=[ActionType.SPEAK],
    )


def tie_revote_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_TIE_REVOTE,
        visible_players=visible_players(),
        tie_candidates=["P3", "P4"],
        allowed_actions=[ActionType.VOTE],
    )


def hunter_shoot_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P3",
        role=Role.HUNTER,
        round=1,
        phase=Phase.HUNTER_SHOOT,
        visible_players=visible_players(),
        allowed_actions=[ActionType.HUNTER_SHOOT],
    )


def last_words_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.EXILE_LAST_WORDS,
        visible_players=visible_players(),
        allowed_actions=[ActionType.SPEAK],
    )


def public_check_claim_context() -> AgentContext:
    return AgentContext(
        game_id="g001",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_VOTE,
        visible_players=visible_players(),
        public_events=[
            PublicEvent(
                event_id="evt_claim",
                round=1,
                phase=Phase.DAY_DISCUSSION,
                event_type=EventType.SPEECH,
                actor="P3",
                public_message="我查验 P4 是狼人。",
                role_claim=Role.SEER,
                claim_result=ClaimResult(
                    target="P4",
                    claimed_alignment=ClaimedAlignment.WEREWOLF,
                ),
            )
        ],
        allowed_actions=[ActionType.VOTE],
    )


def core_phase_contexts() -> list[AgentContext]:
    return [
        werewolf_context(),
        seer_context(),
        witch_context(),
        day_discussion_context(),
        vote_context(),
        tie_discussion_context(),
        tie_revote_context(),
        hunter_shoot_context(),
        last_words_context(),
    ]
