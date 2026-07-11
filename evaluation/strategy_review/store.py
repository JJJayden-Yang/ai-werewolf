"""复盘 draft 的 JSONL 存储 + 人审决策写回。

落盘布局（``$AI_WOLF_DATA_DIR/strategy_reviews/<review_id>/``）：

    meta.json            ReviewMeta（含并排的 belief_accuracy）
    drafts.jsonl         一行一个 StrategyInsightDraft

只有 ``update_decision`` 是写端点（改 review_status / note）；其余只读。
``contracts/`` 不动，模型住 ``evaluation/strategy_review/models.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.strategy_review.models import ReviewMeta, StrategyInsightDraft


class StrategyReviewStore:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    # --- 写 ---

    def save_review(self, meta: ReviewMeta, drafts: list[StrategyInsightDraft]) -> None:
        rdir = self._review_dir(meta.review_id)
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "meta.json").write_text(
            meta.model_dump_json(indent=2), encoding="utf-8"
        )
        with (rdir / "drafts.jsonl").open("w", encoding="utf-8") as f:
            for d in drafts:
                f.write(d.model_dump_json() + "\n")

    def update_decision(
        self, review_id: str, draft_id: str, status: str, note: str | None = None
    ) -> StrategyInsightDraft:
        if status not in {"approved", "rejected", "pending"}:
            raise ValueError(f"invalid status: {status!r}")
        drafts = self.get_drafts(review_id)
        found: StrategyInsightDraft | None = None
        for d in drafts:
            if d.draft_id == draft_id:
                d.review_status = status  # type: ignore[assignment]
                d.review_note = note
                found = d
        if found is None:
            raise KeyError(f"draft not found: {review_id}/{draft_id}")
        # 幂等全量重写。
        with (self._review_dir(review_id) / "drafts.jsonl").open("w", encoding="utf-8") as f:
            for d in drafts:
                f.write(d.model_dump_json() + "\n")
        return found

    # --- 读 ---

    def list_reviews(self) -> list[ReviewMeta]:
        out: list[ReviewMeta] = []
        for rdir in sorted(self.root_dir.iterdir()):
            meta_path = rdir / "meta.json"
            if rdir.is_dir() and meta_path.exists():
                out.append(ReviewMeta.model_validate_json(meta_path.read_text(encoding="utf-8")))
        out.sort(key=lambda m: m.created_at, reverse=True)
        return out

    def get_meta(self, review_id: str) -> ReviewMeta | None:
        meta_path = self._review_dir(review_id) / "meta.json"
        if not meta_path.exists():
            return None
        return ReviewMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def get_drafts(self, review_id: str) -> list[StrategyInsightDraft]:
        path = self._review_dir(review_id) / "drafts.jsonl"
        if not path.exists():
            return []
        drafts: list[StrategyInsightDraft] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                drafts.append(StrategyInsightDraft.model_validate_json(line))
        return drafts

    # --- 内部 ---

    def _review_dir(self, review_id: str) -> Path:
        if "/" in review_id or "\\" in review_id or review_id in {"", ".", ".."}:
            raise ValueError(f"invalid review_id: {review_id!r}")
        return self.root_dir / review_id
