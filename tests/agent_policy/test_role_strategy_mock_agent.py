import asyncio

from contracts import ActionType, AgentAction, Role
from tests.fixtures.agent_contexts import (
    core_phase_contexts,
    seer_context,
    vote_context,
    werewolf_context,
    witch_context,
)

from agent_policy.mock_agents import RoleStrategyMockAgent


def test_role_strategy_mock_agent_dispatches_werewolf_strategy():
    agent = RoleStrategyMockAgent()

    action = asyncio.run(agent.act(werewolf_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.NIGHT_KILL_NOMINATE.value
    assert action["target"] == "P3"
    assert action["metadata"]["strategy"] == "WerewolfStrategy"


def test_role_strategy_mock_agent_dispatches_seer_strategy():
    agent = RoleStrategyMockAgent()

    action = asyncio.run(agent.act(seer_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.CHECK.value
    assert action["target"] in {"P2", "P4"}
    assert action["metadata"]["strategy"] == "SeerStrategy"


def test_role_strategy_mock_agent_dispatches_witch_strategy():
    agent = RoleStrategyMockAgent()

    action = asyncio.run(agent.act(witch_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.SKIP.value
    assert action["target"] is None
    assert action["metadata"]["strategy"] == "WitchStrategy"


def test_role_strategy_mock_agent_dispatches_villager_strategy():
    agent = RoleStrategyMockAgent()

    action = asyncio.run(agent.act(vote_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.VOTE.value
    assert action["target"] != vote_context().agent_id
    assert action["metadata"]["strategy"] == "VillagerStrategy"


def test_role_strategy_mock_agent_outputs_valid_agent_action_for_core_phase_contexts():
    agent = RoleStrategyMockAgent()

    for context in core_phase_contexts():
        action_dict = asyncio.run(agent.act(context.model_dump(mode="json")))
        action = AgentAction.model_validate(action_dict)

        assert action.game_id == context.game_id
        assert action.agent_id == context.agent_id
        assert action.role == context.role
        assert action.phase == context.phase
        assert action.action_type in context.allowed_actions
        assert action.metadata["strategy"].endswith("Strategy")


def test_role_strategy_mock_agent_can_receive_custom_registry():
    class VillagerOnlyRegistry:
        def get(self, role: Role):
            assert role == Role.VILLAGER
            return RoleStrategyMockAgent().registry.get(Role.VILLAGER)

    agent = RoleStrategyMockAgent(registry=VillagerOnlyRegistry())

    action = asyncio.run(agent.act(vote_context().model_dump(mode="json")))

    assert action["action_type"] == ActionType.VOTE.value
