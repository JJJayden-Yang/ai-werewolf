"""策略复盘 API（列表 / 详情 / 决策）的端到端测试。

用 monkeypatch 把 AI_WOLF_DATA_DIR 指到 tmp，先用 store 落一批 review，再过 HTTP 验证。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from evaluation.strategy_review.models import ReviewMeta, StrategyInsightDraft
from evaluation.strategy_review.store import StrategyReviewStore


def _seed(root: Path) -> None:
    store = StrategyReviewStore(root / "strategy_reviews")
    meta = ReviewMeta(
        review_id="r1",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_game_ids=["g1"],
        n_games=1,
        draft_count=1,
        drafts_by_role={"seer": 1},
        belief_accuracy={
            "games_total": 1,
            "games_with_belief": 1,
            "rows": [{"role": "seer", "arm": "v1", "top1_accuracy": 0.6}],
        },
    )
    draft = StrategyInsightDraft(
        draft_id="d1",
        role="seer",
        target_layer="role",
        target_file="/p/seer/v0_free_llm.md",
        observed_issue="查杀后不跳",
        proposed_change="补充跳身份",
    )
    store.save_review(meta, [draft])


def test_strategy_review_api_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))
    _seed(tmp_path)

    with TestClient(app) as client:
        # 列表
        reviews = client.get("/api/strategy/reviews").json()["reviews"]
        assert [r["review_id"] for r in reviews] == ["r1"]

        # 详情 + belief 并排
        detail = client.get("/api/strategy/reviews/r1").json()
        assert detail["meta"]["belief_accuracy"]["rows"][0]["arm"] == "v1"
        assert detail["drafts_by_role"]["seer"][0]["review_status"] == "pending"

        # 决策回写
        r = client.post(
            "/api/strategy/reviews/r1/drafts/d1/decision",
            json={"status": "approved", "note": "ok"},
        )
        assert r.status_code == 200
        assert r.json()["review_status"] == "approved"

        # 重新拉，状态已持久化
        again = client.get("/api/strategy/reviews/r1").json()
        assert again["drafts_by_role"]["seer"][0]["review_status"] == "approved"

        # 错误路径
        assert client.get("/api/strategy/reviews/nope").status_code == 404
        assert (
            client.post(
                "/api/strategy/reviews/r1/drafts/d1/decision", json={"status": "weird"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/strategy/reviews/r1/drafts/nope/decision", json={"status": "approved"}
            ).status_code
            == 404
        )
