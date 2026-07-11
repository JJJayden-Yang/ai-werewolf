"""策略复盘的本地数据模型（不进 ``contracts/``，避免碰冻结 schema）。

``StrategyInsightDraft`` 是 LLM 产出的单条候选改进建议；``ReviewMeta`` 是一次复盘批次的元信息。
字段对齐 ``docs/strategy_review_loop.md §6.2``。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# AI 只能改这两层（§2.1 硬约束）。
TargetLayer = Literal["role", "advanced"]
ALLOWED_TARGET_LAYERS: frozenset[str] = frozenset({"role", "advanced"})

ReviewStatus = Literal["pending", "approved", "rejected"]


class EvidenceRef(BaseModel):
    game_id: str
    round: int | None = None
    phase: str | None = None
    trace_id: str | None = None


class StrategyInsightDraft(BaseModel):
    draft_id: str
    role: str  # werewolf/seer/witch/hunter/villager/global
    arm: str | None = None
    target_layer: TargetLayer
    target_file: str
    current_excerpt: str | None = None  # 拟修改的现有片段；空=新增
    observed_issue: str
    proposed_change: str
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list)
    potential_risk: str | None = None
    review_status: ReviewStatus = "pending"
    review_note: str | None = None


class ReviewMeta(BaseModel):
    review_id: str
    created_at: str
    source_game_ids: list[str] = Field(default_factory=list)
    n_games: int = 0
    arm_counts: dict[str, int] = Field(default_factory=dict)
    model_flavor: str | None = None
    model_name: str | None = None
    draft_count: int = 0
    drafts_by_role: dict[str, int] = Field(default_factory=dict)
    dropped_out_of_scope: int = 0  # 越界/泄漏被丢弃的建议数

    # 复用别处算好的 belief 命中率（并排展示，只读）。
    belief_accuracy: dict[str, Any] = Field(default_factory=dict)
