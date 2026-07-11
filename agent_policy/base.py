"""Agent Policy 基础接口。

B 侧 Agent 只接收 JSON dict，输出 AgentAction JSON dict。
不得接收 TruthState / GameSession / Store 等内部对象。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """所有 B 侧 Agent 的统一接口。"""

    @abstractmethod
    async def act(self, context: dict) -> dict:
        """根据 AgentContext JSON dict 返回 AgentAction JSON dict。"""

