"""Tests for batch report aggregation + /api/audit/batches endpoints.

用假 report.json + report.extras.json 落到 tmp 目录，不依赖真 LLM / 真跑批。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.audit_batch_service import get_batch_report, list_batches
from api.main import app


def _extra(
    *,
    game_id: str,
    seed: int,
    arm: str,
    belief_kernel: str = "additive_v1",
    slow_think: bool = False,
    winner: str = "werewolves",
    rounds: int = 4,
    seer_disclosed: bool = False,
    llm_error: int = 0,
    fallback: dict | None = None,
    belief_signal: dict | None = None,
    slow_stats: dict | None = None,
) -> dict:
    """造一条 sidecar extra，字段形状对齐 compute_mixed_metrics 的输出。"""
    return {
        "game_id": game_id,
        "seed": seed,
        "arm": arm,
        "belief_kernel": belief_kernel,
        "slow_think": slow_think,
        "winner": winner,
        "rounds": rounds,
        "decision_stats": {"llm_error": llm_error},
        "key_scenes": {"seer_disclosed_check": seer_disclosed},
        "fallback_breakdown": fallback
        or {
            "llm_error": llm_error,
            "parse_error": 0,
            "rule_violation": {},
            "schema_invalid_events": 0,
        },
        "slow_think_stats": slow_stats
        or {"applied": 0, "reflect_parse_errors": 0},
        "belief_signal": belief_signal,
    }


def _belief_signal(
    *, sep: float, margin: float, entropy: float, acc: float, cons: float, seer: float,
    top2: float = 0.8, brier: float = 0.2,
) -> dict:
    return {
        "top_suspect_accuracy_rate": acc,
        "top2_accuracy_rate": top2,
        "decision_top_suspect_consistency_rate": cons,
        "belief_quality": {
            "avg_wolf_villager_separation": sep,
            "avg_top_margin": margin,
            "avg_suspicion_entropy": entropy,
            "avg_brier": brier,
        },
        "per_role_identification": {"seer_identification_accuracy": seer},
    }


def _write_batch(root: Path, run_id: str, extras: list[dict], *, created_at: str = "2026-06-04T10:00:00Z") -> None:
    d = root / run_id
    d.mkdir(parents=True, exist_ok=True)
    report = {
        "total": len(extras),
        "completed": len(extras),
        "failed": 0,
        "winner_distribution": {},
        "run_config_snapshot": {"created_at": created_at},
    }
    (d / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (d / "report.extras.json").write_text(json.dumps(extras), encoding="utf-8")


def _two_arm_extras() -> list[dict]:
    return [
        # baseline_v0：无 belief_signal，狼胜。
        _extra(game_id="g-1", seed=1, arm="baseline_v0", winner="werewolves", llm_error=0),
        _extra(game_id="g-2", seed=2, arm="baseline_v0", winner="villagers", llm_error=2),
        # villagers_only_v1：有 belief_signal + 慢思 + 报查。
        _extra(
            game_id="g-3",
            seed=3,
            arm="villagers_only_v1",
            belief_kernel="factorized_v2",
            slow_think=True,
            winner="villagers",
            seer_disclosed=True,
            llm_error=1,
            fallback={"llm_error": 1, "parse_error": 1, "rule_violation": {"bad_target": 2}, "schema_invalid_events": 1},
            slow_stats={"applied": 5, "reflect_parse_errors": 1},
            belief_signal=_belief_signal(sep=0.06, margin=0.08, entropy=0.97, acc=0.6, cons=0.5, seer=0.04),
        ),
        _extra(
            game_id="g-4",
            seed=4,
            arm="villagers_only_v1",
            belief_kernel="factorized_v2",
            slow_think=True,
            winner="werewolves",
            seer_disclosed=False,
            llm_error=3,
            fallback={"llm_error": 3, "parse_error": 0, "rule_violation": {}, "schema_invalid_events": 0},
            slow_stats={"applied": 7, "reflect_parse_errors": 0},
            belief_signal=_belief_signal(sep=0.04, margin=0.06, entropy=0.98, acc=0.5, cons=0.4, seer=0.02),
        ),
    ]


def test_list_batches_summarizes_arms_and_kernel(tmp_path: Path) -> None:
    _write_batch(tmp_path, "run_a", _two_arm_extras())

    summaries = list_batches(tmp_path)

    assert len(summaries) == 1
    s = summaries[0]
    assert s.run_id == "run_a"
    assert s.games == 4
    assert s.arms == ["baseline_v0", "villagers_only_v1"]
    # 混合 kernel → "mixed"
    assert s.belief_kernel == "mixed"
    assert s.slow_think is True
    assert s.created_at == "2026-06-04T10:00:00Z"


def test_list_batches_empty_root_returns_empty(tmp_path: Path) -> None:
    assert list_batches(tmp_path / "does_not_exist") == []


def test_get_batch_report_aggregates_per_arm(tmp_path: Path) -> None:
    _write_batch(tmp_path, "run_a", _two_arm_extras())

    detail = get_batch_report("run_a", tmp_path)
    assert detail is not None
    arms = detail.arms

    # v0 arm：无 belief（belief 块为 None），狼胜率 1/2。
    v0 = arms["baseline_v0"]
    assert v0.n == 2
    assert v0.win_rate_wolf == 0.5
    assert v0.belief is None
    assert v0.avg_llm_error_per_game == 1.0  # (0 + 2) / 2

    # v1 arm：belief 块取均值，仅对有 belief_signal 的局。
    v1 = arms["villagers_only_v1"]
    assert v1.n == 2
    assert v1.win_rate_wolf == 0.5
    assert v1.seer_disclosure_rate == 0.5
    assert v1.belief is not None
    assert abs(v1.belief.separation - 0.05) < 1e-9  # (0.06 + 0.04) / 2
    assert abs(v1.belief.top1_accuracy - 0.55) < 1e-9
    assert abs(v1.belief.seer_identification - 0.03) < 1e-9
    # §4.3：rank（top2）+ calibration（Brier）也聚合到 arm 级。
    assert abs(v1.belief.top2_accuracy - 0.8) < 1e-9
    assert abs(v1.belief.brier - 0.2) < 1e-9
    # fallback 求和：rule_violation dict 值累加，schema_invalid_events → schema_invalid。
    assert v1.fallback.llm_error == 4  # 1 + 3
    assert v1.fallback.parse_error == 1
    assert v1.fallback.rule_violation == 2
    assert v1.fallback.schema_invalid == 1
    assert v1.slow_think.applied == 12  # 5 + 7
    assert v1.slow_think.parse_errors == 1


def test_get_batch_report_game_rows(tmp_path: Path) -> None:
    _write_batch(tmp_path, "run_a", _two_arm_extras())

    detail = get_batch_report("run_a", tmp_path)
    assert detail is not None
    assert len(detail.games) == 4
    g3 = next(g for g in detail.games if g.game_id == "g-3")
    assert g3.arm == "villagers_only_v1"
    assert g3.seer_disclosed is True
    assert g3.llm_error == 1
    # fallback_total = 1(llm) + 1(parse) + 2(rule) + 1(schema)
    assert g3.fallback_total == 5


def test_get_batch_report_missing_returns_none(tmp_path: Path) -> None:
    assert get_batch_report("nope", tmp_path) is None


def test_get_batch_report_rejects_path_traversal(tmp_path: Path) -> None:
    # 在 batch 根的兄弟目录放一个 report.json，run_id 用 .. 试图逃逸读它。
    sibling = tmp_path.parent / "secret"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "report.json").write_text(json.dumps({"total": 1}), encoding="utf-8")
    for evil in ("../secret", "..", "a/b", "a\\b", ""):
        assert get_batch_report(evil, tmp_path) is None


def test_list_batches_skips_symlinked_dir_escaping_root(tmp_path: Path) -> None:
    import pytest

    root = tmp_path / "batches"
    root.mkdir()
    # 一个合法 batch。
    _write_batch(root, "real_run", _two_arm_extras())
    # 外部目录，含 report.json，通过 root 下的符号链接暴露。
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.json").write_text(json.dumps({"total": 99}), encoding="utf-8")
    (outside / "report.extras.json").write_text(json.dumps([]), encoding="utf-8")
    try:
        (root / "evil").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not permitted in this environment")

    summaries = list_batches(root)
    run_ids = {s.run_id for s in summaries}
    assert "real_run" in run_ids
    assert "evil" not in run_ids  # 逃逸出 root 的符号链接被排除


def test_avg_llm_error_skips_missing_decision_stats(tmp_path: Path) -> None:
    # 一局有 decision_stats（llm_error=4），一局完全缺 decision_stats（旧/坏 sidecar）。
    good = _extra(game_id="g-1", seed=1, arm="baseline_v0", llm_error=4)
    broken = _extra(game_id="g-2", seed=2, arm="baseline_v0")
    broken.pop("decision_stats")
    _write_batch(tmp_path, "run_a", [good, broken])

    detail = get_batch_report("run_a", tmp_path)
    assert detail is not None
    # 缺值不被当 0 → 均值只对实有的那一局 = 4.0（不是 (4+0)/2 = 2.0）。
    assert detail.arms["baseline_v0"].avg_llm_error_per_game == 4.0


def test_batches_endpoint_uses_env_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_WOLF_BATCH_DIR", str(tmp_path))
    _write_batch(tmp_path, "run_a", _two_arm_extras())

    with TestClient(app) as client:
        listed = client.get("/api/audit/batches")
        assert listed.status_code == 200
        body = listed.json()
        assert len(body) == 1
        assert body[0]["run_id"] == "run_a"

        detail = client.get("/api/audit/batches/run_a")
        assert detail.status_code == 200
        assert "villagers_only_v1" in detail.json()["arms"]

        missing = client.get("/api/audit/batches/nope")
        assert missing.status_code == 404
