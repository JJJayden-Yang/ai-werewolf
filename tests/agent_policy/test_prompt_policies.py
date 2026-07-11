from contracts import ActionType, Phase, Role
from tests.fixtures.agent_contexts import (
    day_discussion_context,
    seer_context,
    vote_context,
    werewolf_context,
    witch_context,
)

from agent_policy.prompt_policies import (
    PromptPolicy,
    PromptPolicyRegistry,
    PromptPolicySpec,
)


def test_prompt_policy_registry_returns_role_phase_specific_policy():
    registry = PromptPolicyRegistry()

    policy = registry.get(Role.WEREWOLF, Phase.NIGHT_WEREWOLF)

    assert isinstance(policy, PromptPolicy)
    assert policy.role == Role.WEREWOLF
    assert policy.phase == Phase.NIGHT_WEREWOLF
    assert "狼人" in policy.strategy_prompt
    assert ActionType.NIGHT_KILL_NOMINATE.value in policy.strategy_prompt


def test_prompt_policy_registry_returns_day_speech_policy():
    registry = PromptPolicyRegistry()

    policy = registry.get(Role.VILLAGER, Phase.DAY_DISCUSSION)

    assert policy.role == Role.VILLAGER
    assert policy.phase == Phase.DAY_DISCUSSION
    assert ActionType.SPEAK.value in policy.strategy_prompt
    assert "白天发言" in policy.strategy_prompt


def test_all_prompt_policies_include_shared_output_constraints():
    registry = PromptPolicyRegistry()

    for context in [
        werewolf_context(),
        seer_context(),
        witch_context(),
        day_discussion_context(),
        vote_context(),
    ]:
        prompt = registry.build_prompt(context)

        assert "allowed_actions" in prompt
        assert "AgentAction JSON" in prompt
        assert "只能" in prompt
        assert "reason_summary" in prompt
        assert "不要输出完整隐藏推理链" in prompt


def test_build_prompt_includes_context_action_options():
    prompt = PromptPolicyRegistry().build_prompt(vote_context())

    assert ActionType.VOTE.value in prompt
    assert "P2" in prompt
    assert "DAY_VOTE" in prompt


def test_prompt_policy_spec_is_small_serializable_descriptor():
    policy = PromptPolicyRegistry().get(Role.SEER, Phase.NIGHT_SEER)
    spec = policy.to_spec()

    assert isinstance(spec, PromptPolicySpec)
    assert spec.prompt_policy_id == "seer_night_v1"
    assert spec.role == Role.SEER
    assert spec.phase == Phase.NIGHT_SEER
    assert spec.metadata["owner"] == "B"
