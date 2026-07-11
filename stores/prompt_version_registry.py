"""PromptVersionRegistry skeleton —— 见 Interface_v2_1 6.3。

Prompt 完整内容不进 EventLog；AgentDecisionTrace 只记 prompt_version_id。
本注册表负责按 id 拿回完整的 PromptVersion 元数据（design_goal /
key_strategy_rules / few_shot_cases / known_risks 等），供 PostGameAnalyzer
做版本对比与 BadCase 归因。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.schemas import PromptVersion


class PromptVersionRegistry:
    def register(self, prompt_version: PromptVersion) -> None:
        raise NotImplementedError

    def get(self, prompt_version_id: str) -> PromptVersion:
        raise NotImplementedError

    def list_by_role(self, role: str) -> list[PromptVersion]:
        raise NotImplementedError
