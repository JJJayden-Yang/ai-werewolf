"""按角色拆分的策略入口。"""

from agent_policy.roles.strategy_base import BaseRuleBasedStrategy, RoleStrategy
from agent_policy.roles.hunter import HunterStrategy
from agent_policy.roles.seer import SeerStrategy
from agent_policy.roles.villager import VillagerStrategy
from agent_policy.roles.werewolf import WerewolfStrategy
from agent_policy.roles.witch import WitchStrategy

__all__ = [
    "RoleStrategy",
    "BaseRuleBasedStrategy",
    "WerewolfStrategy",
    "SeerStrategy",
    "WitchStrategy",
    "HunterStrategy",
    "VillagerStrategy",
]
