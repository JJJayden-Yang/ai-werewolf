"""store 往返 + reviewer 两道闸门（无 LLM）的单测。"""

from __future__ import annotations

from datetime import datetime, timezone

from evaluation.strategy_review.models import ReviewMeta, StrategyInsightDraft
from evaluation.strategy_review.reviewer import _extract_json_array, _to_drafts
from evaluation.strategy_review.store import StrategyReviewStore


# --------------------------------------------------------------------------- #
# reviewer 闸门
# --------------------------------------------------------------------------- #

_EDITABLE = {"/p/seer/v0_free_llm.md", "/p/advanced/seer/x.md"}


def test_to_drafts_keeps_valid():
    items = [
        {
            "target_layer": "role",
            "target_file": "/p/seer/v0_free_llm.md",
            "current_excerpt": "",
            "observed_issue": "查杀后不跳身份",
            "proposed_change": "补充：查杀后主动跳预言家",
            "supporting_evidence": [{"game_id": "g1", "round": 1, "phase": "NIGHT_SEER"}],
            "potential_risk": "可能被刀",
        }
    ]
    out = _to_drafts("seer", items, _EDITABLE)
    assert len(out.drafts) == 1
    assert out.dropped == 0
    assert out.drafts[0].supporting_evidence[0].game_id == "g1"


def test_to_drafts_drops_out_of_scope_layer_and_file():
    items = [
        {"target_layer": "game_knowledge", "target_file": "/p/seer/v0_free_llm.md",
         "observed_issue": "a", "proposed_change": "b"},  # 越界 layer
        {"target_layer": "role", "target_file": "/p/shared/output_contract.md",
         "observed_issue": "a", "proposed_change": "b"},  # 文件不在可改集
    ]
    out = _to_drafts("seer", items, _EDITABLE)
    assert out.drafts == []
    assert out.dropped == 2


def test_to_drafts_allows_new_advanced_snippet():
    from evaluation.strategy_review.prompt_assets import RolePromptBundle

    snip_dir = "/repo/agent_policy/advanced_strategy/snippets/seer"
    bundle = RolePromptBundle(role="seer", new_snippet_dirs=[snip_dir])
    items = [
        # 新增 advanced snippet（不在 editable，但在允许目录下）→ 保留
        {"target_layer": "advanced", "target_file": f"{snip_dir}/early_claim_timing.md",
         "current_excerpt": "", "observed_issue": "缺少早跳时机指引", "proposed_change": "新增片段…"},
        # 新增到 role 层 → 不允许新建，丢弃
        {"target_layer": "role", "target_file": f"{snip_dir}/x.md",
         "observed_issue": "a", "proposed_change": "b"},
        # 路径穿越 → 丢弃
        {"target_layer": "advanced", "target_file": f"{snip_dir}/../../../etc/x.md",
         "observed_issue": "a", "proposed_change": "b"},
        # 别的角色目录 → 丢弃
        {"target_layer": "advanced", "target_file": "/repo/agent_policy/advanced_strategy/snippets/witch/y.md",
         "observed_issue": "a", "proposed_change": "b"},
    ]
    out = _to_drafts("seer", items, set(), bundle)
    assert len(out.drafts) == 1
    assert out.drafts[0].target_file.endswith("early_claim_timing.md")
    assert out.dropped == 3


def test_to_drafts_drops_truth_leak():
    items = [
        {"target_layer": "role", "target_file": "/p/seer/v0_free_llm.md",
         "observed_issue": "P3 是狼，应直接投票", "proposed_change": "投 P3"},
    ]
    out = _to_drafts("seer", items, _EDITABLE)
    assert out.drafts == []
    assert out.dropped == 1


def test_to_drafts_drops_missing_fields():
    items = [
        {"target_layer": "role", "target_file": "/p/seer/v0_free_llm.md",
         "observed_issue": "", "proposed_change": "x"},  # 缺 observed
        "not a dict",
    ]
    out = _to_drafts("seer", items, _EDITABLE)
    assert out.drafts == []
    assert out.dropped == 2


def test_extract_json_array_handles_code_fence():
    raw = "好的，结果如下:\n```json\n[{\"a\": 1}]\n```\n"
    assert _extract_json_array(raw) == [{"a": 1}]
    assert _extract_json_array("not json") == []
    assert _extract_json_array("[1, 2, 3]") == [1, 2, 3]


# --------------------------------------------------------------------------- #
# store 往返
# --------------------------------------------------------------------------- #


def _draft(draft_id: str) -> StrategyInsightDraft:
    return StrategyInsightDraft(
        draft_id=draft_id,
        role="seer",
        target_layer="role",
        target_file="/p/seer/v0_free_llm.md",
        observed_issue="x",
        proposed_change="y",
    )


def test_store_roundtrip_and_decision(tmp_path):
    store = StrategyReviewStore(tmp_path)
    meta = ReviewMeta(
        review_id="r1",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_game_ids=["g1", "g2"],
        n_games=2,
        draft_count=2,
    )
    store.save_review(meta, [_draft("d1"), _draft("d2")])

    assert [m.review_id for m in store.list_reviews()] == ["r1"]
    assert store.get_meta("r1").n_games == 2
    drafts = store.get_drafts("r1")
    assert {d.draft_id for d in drafts} == {"d1", "d2"}
    assert all(d.review_status == "pending" for d in drafts)

    updated = store.update_decision("r1", "d2", "approved", note="同意")
    assert updated.review_status == "approved"

    # 重新读盘，状态已持久化、且 d1 不受影响。
    reload = {d.draft_id: d for d in store.get_drafts("r1")}
    assert reload["d2"].review_status == "approved"
    assert reload["d2"].review_note == "同意"
    assert reload["d1"].review_status == "pending"


def test_store_unknown_review_empty(tmp_path):
    store = StrategyReviewStore(tmp_path)
    assert store.list_reviews() == []
    assert store.get_meta("nope") is None
    assert store.get_drafts("nope") == []
