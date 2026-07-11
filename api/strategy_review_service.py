"""策略复盘 draft 的审计端点（列表 / 详情 / 人审决策）。

读 ``$AI_WOLF_DATA_DIR/strategy_reviews/`` 下由 ``scripts/run_strategy_review.py`` 落盘的
review 批次，给前端 ``admin/strategy/reviews`` 渲染。唯一写端点是 decision（采纳/驳回）。

红线自检：
- ✅ 不改 ``contracts/``：draft / meta 模型住 ``evaluation/strategy_review/models.py``。
- ✅ 数据全来自已落盘的 review 目录，无新增落盘格式。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from evaluation.strategy_review.models import ReviewMeta, StrategyInsightDraft
from evaluation.strategy_review.store import StrategyReviewStore

router = APIRouter(prefix="/api/strategy/reviews", tags=["strategy-review"])


def _store() -> StrategyReviewStore:
    root = Path(os.getenv("AI_WOLF_DATA_DIR", "./data")) / "strategy_reviews"
    return StrategyReviewStore(root)


class DecisionRequest(BaseModel):
    status: str  # approved | rejected | pending
    note: str | None = None


class ReviewDetail(BaseModel):
    meta: ReviewMeta
    drafts_by_role: dict[str, list[StrategyInsightDraft]]


@router.get("")
def list_reviews() -> dict[str, list[ReviewMeta]]:
    return {"reviews": _store().list_reviews()}


@router.get("/{review_id}")
def get_review(review_id: str) -> ReviewDetail:
    store = _store()
    meta = store.get_meta(review_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"review not found: {review_id}")
    grouped: dict[str, list[StrategyInsightDraft]] = {}
    for d in store.get_drafts(review_id):
        grouped.setdefault(d.role, []).append(d)
    return ReviewDetail(meta=meta, drafts_by_role=grouped)


@router.post("/{review_id}/drafts/{draft_id}/decision")
def decide(review_id: str, draft_id: str, body: DecisionRequest) -> StrategyInsightDraft:
    store = _store()
    if store.get_meta(review_id) is None:
        raise HTTPException(status_code=404, detail=f"review not found: {review_id}")
    try:
        return store.update_decision(review_id, draft_id, body.status, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
