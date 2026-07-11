"""C(Context)自己拥有的内部类型。

这些类型不在 contracts/ 冻结清单内，由 C 定义和维护：
- AgentContextDraft：ContextAssembler 中间产物，未经预算裁剪的"原始上下文"。
  ContextWindowPolicy.apply 把 Draft 压成 contracts.AgentContext。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contracts.enums import ActionType, Phase, Role
    from contracts.schemas import ClaimRecord, PrivateEvent, PublicEvent, VisiblePlayer, VoteRecord


@dataclass
class AgentContextDraft:
    """未经预算裁剪的上下文中间表示。

    与 contracts.AgentContext 字段对齐，但允许超出 ContextBudgetConfig 的上限。
    ContextWindowPolicy.apply(draft, budget) 负责裁剪并输出 JSON-serializable
    contracts.AgentContext。
    """

    game_id: str
    agent_id: str
    role: Role
    round: int
    phase: Phase
    is_secondary_stage: bool = False
    secondary_stage_type: str | None = None
    tie_candidates: list[str] = field(default_factory=list)
    previous_vote_summary: dict[str, int] = field(default_factory=dict)
    visible_players: list[VisiblePlayer] = field(default_factory=list)
    current_round_events: list[PublicEvent] = field(default_factory=list)
    recent_public_events: list[PublicEvent] = field(default_factory=list)
    public_memory_summary: list[Any] = field(default_factory=list)
    public_events: list[PublicEvent] = field(default_factory=list)
    private_events: list[PrivateEvent] = field(default_factory=list)
    belief_state: dict[str, Any] = field(default_factory=dict)
    belief_top_suspects: list[Any] = field(default_factory=list)
    strategy_memory: list[Any] = field(default_factory=list)
    allowed_actions: list[ActionType] = field(default_factory=list)
    rule_hints: dict[str, Any] = field(default_factory=dict)
    # v2.2 typed 台账（ContextAssembler 从公开事件流投影；与字符串 FactStream 并存）
    claim_records: list[ClaimRecord] = field(default_factory=list)
    vote_records: list[VoteRecord] = field(default_factory=list)
