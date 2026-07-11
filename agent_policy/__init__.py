"""agent_policy —— B 负责的 Agent 策略层。"""

from agent_policy.actions import (
    ACTION_BUILDERS,
    ROLE_ACTIONS,
    available_actions_for_context,
    build_check_action,
    build_hunter_shoot_action,
    build_poison_action,
    build_save_action,
    build_skip_action,
    build_speak_action,
    build_vote_action,
    build_wolf_nomination_action,
)
from agent_policy.base import BaseAgent
from agent_policy.belief_math import (
    ROLE_FIELDS,
    apply_delta_and_normalize,
    belief_total,
    clamp_probability,
    create_empty_belief_state,
    normalize_role_belief,
)
from agent_policy.factorized_belief import (
    FactorizedEvidence,
    apply_factorized_evidence,
    apply_log_odds_update,
    source_credibility_from_belief,
)
from agent_policy.mock_agents import HeuristicMockAgent, LegalRandomMockAgent, RoleStrategyMockAgent
from agent_policy.prompt_policies import (
    PromptPolicy,
    PromptPolicyRegistry,
    PromptPolicySpec,
)
from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater
from agent_policy.role_strategies import (
    HunterStrategy,
    RoleStrategy,
    RoleStrategyRegistry,
    SeerStrategy,
    VillagerStrategy,
    WerewolfStrategy,
    WitchStrategy,
)

__all__ = [
    "BaseAgent",
    "LegalRandomMockAgent",
    "HeuristicMockAgent",
    "RoleStrategyMockAgent",
    "ROLE_FIELDS",
    "clamp_probability",
    "belief_total",
    "normalize_role_belief",
    "apply_delta_and_normalize",
    "create_empty_belief_state",
    "FactorizedEvidence",
    "apply_factorized_evidence",
    "apply_log_odds_update",
    "source_credibility_from_belief",
    "RoleStrategy",
    "RoleStrategyRegistry",
    "PromptPolicy",
    "PromptPolicyRegistry",
    "PromptPolicySpec",
    "RuleBasedRealtimeBeliefUpdater",
    "WerewolfStrategy",
    "SeerStrategy",
    "WitchStrategy",
    "HunterStrategy",
    "VillagerStrategy",
    "ROLE_ACTIONS",
    "ACTION_BUILDERS",
    "available_actions_for_context",
    "build_speak_action",
    "build_vote_action",
    "build_wolf_nomination_action",
    "build_check_action",
    "build_save_action",
    "build_poison_action",
    "build_hunter_shoot_action",
    "build_skip_action",
]
