"""角色策略注册表。

具体角色策略已经拆到 `agent_policy.roles.*`，本文件只保留稳定导入入口和
RoleStrategyRegistry，方便 A/C 继续从 `agent_policy.role_strategies` 导入。
"""

from __future__ import annotations

from contracts import Role

from agent_policy.roles import (
    HunterStrategy,
    RoleStrategy,
    SeerStrategy,
    VillagerStrategy,
    WerewolfStrategy,
    WitchStrategy,
)


class RoleStrategyRegistry:
    """按角色获取策略实例。"""

    def __init__(self) -> None:
        self._strategies: dict[Role, RoleStrategy] = {
            Role.WEREWOLF: WerewolfStrategy(),
            Role.SEER: SeerStrategy(),
            Role.WITCH: WitchStrategy(),
            Role.HUNTER: HunterStrategy(),
            Role.VILLAGER: VillagerStrategy(),
        }

    def get(self, role: Role) -> RoleStrategy:
        return self._strategies[role]


__all__ = [
    "RoleStrategy",
    "RoleStrategyRegistry",
    "WerewolfStrategy",
    "SeerStrategy",
    "WitchStrategy",
    "HunterStrategy",
    "VillagerStrategy",
]
