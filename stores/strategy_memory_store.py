"""StrategyMemoryStore skeleton —— 见 Interface_v2_1 6.5。

v2 optional 使用：把往局的策略教训（StrategyMemoryItem）按角色召回少量条目
注入 AgentContext.strategy_memory。第一阶段只留接口，不进入核心交付。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.schemas import StrategyMemoryItem


class StrategyMemoryStore:
    def get_for_role(self, role: str, limit: int = 3) -> list[StrategyMemoryItem]:
        raise NotImplementedError

    def save(self, item: StrategyMemoryItem) -> None:
        raise NotImplementedError
