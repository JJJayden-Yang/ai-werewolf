import asyncio

from contracts import ActionType, AgentAction, AgentContext, EventType, Phase, PrivateEvent, Role, Visibility
from tests.fixtures.agent_contexts import (
    core_phase_contexts,
    public_check_claim_context,
    seer_context,
    vote_context,
    werewolf_context,
)

from agent_policy.mock_agents import HeuristicMockAgent, LegalRandomMockAgent


def test_legal_random_wolf_never_kills_teammate():
    agent = LegalRandomMockAgent()

    action = asyncio.run(agent.act(werewolf_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.NIGHT_KILL_NOMINATE.value
    assert action["target"] in {"P3", "P4"}


def test_legal_random_vote_never_targets_self():
    agent = LegalRandomMockAgent()

    action = asyncio.run(agent.act(vote_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.VOTE.value
    assert action["target"] != "P2"


def test_legal_random_seer_reads_private_events_checked_history():
    agent = LegalRandomMockAgent()

    action = asyncio.run(agent.act(seer_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.CHECK.value
    assert action["target"] in {"P2", "P4"}


def test_legal_random_witch_defaults_to_skip():
    agent = LegalRandomMockAgent()
    context = AgentContext(
        game_id="g001",
        agent_id="P4",
        role=Role.WITCH,
        round=1,
        phase=Phase.NIGHT_WITCH,
        allowed_actions=[ActionType.SAVE, ActionType.POISON, ActionType.SKIP],
    )

    action = asyncio.run(agent.act(context.model_dump(mode="json")))

    assert action["action_type"] == ActionType.SKIP.value
    assert action["target"] is None


def test_heuristic_witch_uses_current_round_kill_target():
    agent = HeuristicMockAgent()
    context = AgentContext(
        game_id="g001",
        agent_id="P4",
        role=Role.WITCH,
        round=3,
        phase=Phase.NIGHT_WITCH,
        allowed_actions=[ActionType.SAVE, ActionType.POISON, ActionType.SKIP],
        private_events=[
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
    )

    action = asyncio.run(agent.act(context.model_dump(mode="json")))

    assert action["action_type"] == ActionType.SAVE.value
    assert action["target"] == "P5"


def test_heuristic_wolf_prefers_public_claimed_seer():
    agent = HeuristicMockAgent()

    action = asyncio.run(agent.act(werewolf_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.NIGHT_KILL_NOMINATE.value
    assert action["target"] == "P3"


def test_heuristic_villager_votes_public_werewolf_claim_target():
    agent = HeuristicMockAgent()

    action = asyncio.run(agent.act(public_check_claim_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.VOTE.value
    assert action["target"] == "P4"


def test_legal_random_outputs_valid_agent_action_for_core_phase_contexts():
    agent = LegalRandomMockAgent()

    for context in core_phase_contexts():
        action_dict = asyncio.run(agent.act(context.model_dump(mode="json")))
        action = AgentAction.model_validate(action_dict)

        assert action.game_id == context.game_id
        assert action.agent_id == context.agent_id
        assert action.role == context.role
        assert action.phase == context.phase
        assert action.action_type in context.allowed_actions
