"""Batch Report 审计端点（A 的只读 eval 端点）。

把 ``scripts/run_mixed_batch.py`` 落盘的 ``report.json`` + ``report.extras.json`` 聚合成
arm 对比视图，给前端 ``errors/`` 页渲染（负载护栏 + belief 质量对比）。

红线自检：
- ✅ 不改 ``contracts/``：响应模型住本文件（api 自有 Pydantic）。
- ✅ 数据全来自已产出的 sidecar，无新增落盘格式。
- ``belief`` 块仅对有 ``belief_signal`` 的局取均值（v0/未注入局跳过，不污染）。
- 所有比率用 ``n`` 做分母，空集返 ``None`` 不返 ``0``（避免假装「有数据且为 0」）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

_WOLF_WINNER = "werewolves"


# --------------------------------------------------------------------------- #
# 响应模型（api 自有，不碰 contracts/）
# --------------------------------------------------------------------------- #


class BatchSummary(BaseModel):
    run_id: str
    games: int
    completed: int
    arms: list[str]
    belief_kernel: str
    slow_think: bool
    created_at: str | None = None


class BeliefAgg(BaseModel):
    separation: float | None = None
    top_margin: float | None = None
    entropy: float | None = None
    top1_accuracy: float | None = None
    # top2 命中（狼框进前二）+ Brier（calibration）—— rank vs calibration 拆解的 arm 级视图。
    top2_accuracy: float | None = None
    brier: float | None = None
    consistency: float | None = None
    seer_identification: float | None = None


class FallbackAgg(BaseModel):
    llm_error: int = 0
    parse_error: int = 0
    rule_violation: int = 0
    schema_invalid: int = 0


class SlowThinkAgg(BaseModel):
    applied: int = 0
    parse_errors: int = 0


class ArmAgg(BaseModel):
    n: int
    win_rate_wolf: float | None = None
    seer_disclosure_rate: float | None = None
    belief: BeliefAgg | None = None
    fallback: FallbackAgg = Field(default_factory=FallbackAgg)
    slow_think: SlowThinkAgg = Field(default_factory=SlowThinkAgg)
    avg_llm_error_per_game: float | None = None


class BatchGameRow(BaseModel):
    game_id: str
    seed: int | None = None
    arm: str
    winner: str | None = None
    rounds: int | None = None
    llm_error: int = 0
    seer_disclosed: bool = False
    fallback_total: int = 0


class BatchReportDetail(BaseModel):
    run_id: str
    report: dict[str, Any]
    arms: dict[str, ArmAgg]
    games: list[BatchGameRow]


# --------------------------------------------------------------------------- #
# 取数辅助
# --------------------------------------------------------------------------- #


def _is_safe_run_id(run_id: str) -> bool:
    """run_id = 目录名，落文件系统。同 stores 口径拒路径分隔符 / 保留段，挡 ``..`` 穿越。"""
    return not ("/" in run_id or "\\" in run_id or run_id in ("", ".", ".."))


def _within_root(report_path: Path, root: Path) -> bool:
    """``report_path`` 解析后其所在 batch 目录必须仍直属 ``root``（挡符号链接逃逸）。

    ``<root>/<run_id>/report.json`` → ``parent.parent`` 即 ``root``；若 ``<run_id>`` 是指向
    外部的符号链接，``resolve()`` 后 ``samefile(root)`` 不成立 → 拒。
    """
    try:
        return report_path.resolve().parent.parent.samefile(root.resolve())
    except OSError:
        return False


def _batch_root() -> Path:
    """batch 根目录：``AI_WOLF_BATCH_DIR``，默认 ``<AI_WOLF_DATA_DIR>/batches``。"""
    explicit = os.getenv("AI_WOLF_BATCH_DIR")
    if explicit:
        return Path(explicit)
    return Path(os.getenv("AI_WOLF_DATA_DIR", "./data")) / "batches"


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _extras_path(report_path: Path) -> Path:
    """与 ``run_mixed_batch._default_extras_path`` 同口径：``<stem>.extras<suffix>``。"""
    return report_path.with_name(f"{report_path.stem}.extras{report_path.suffix}")


def _avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return (sum(present) / len(present)) if present else None


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def _dig(d: Any, *keys: str, default: Any = None) -> Any:
    """安全链式取 ``d[k1][k2]...``，任一层缺失 / 非 dict 返 default。"""
    cur = d
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _fallback_total(extra: dict[str, Any]) -> int:
    fb = extra.get("fallback_breakdown") or {}
    rule_violation = fb.get("rule_violation") or {}
    rule_sum = sum(rule_violation.values()) if isinstance(rule_violation, dict) else 0
    return (
        int(fb.get("llm_error", 0) or 0)
        + int(fb.get("parse_error", 0) or 0)
        + int(rule_sum)
        + int(fb.get("schema_invalid_events", 0) or 0)
    )


# --------------------------------------------------------------------------- #
# 聚合
# --------------------------------------------------------------------------- #


def _distinct_arms(extras: list[dict[str, Any]]) -> list[str]:
    return sorted({str(e.get("arm", "unknown")) for e in extras})


def _belief_kernel_label(extras: list[dict[str, Any]]) -> str:
    kernels = sorted({str(e.get("belief_kernel", "unknown")) for e in extras})
    if not kernels:
        return "unknown"
    return kernels[0] if len(kernels) == 1 else "mixed"


def _summarize(run_id: str, report: dict[str, Any], extras: list[dict[str, Any]]) -> BatchSummary:
    return BatchSummary(
        run_id=run_id,
        games=int(report.get("total", len(extras)) or 0),
        completed=int(report.get("completed", 0) or 0),
        arms=_distinct_arms(extras),
        belief_kernel=_belief_kernel_label(extras),
        slow_think=any(bool(e.get("slow_think")) for e in extras),
        created_at=_dig(report, "run_config_snapshot", "created_at"),
    )


def _aggregate_arm(games: list[dict[str, Any]]) -> ArmAgg:
    n = len(games)

    wolf_wins = sum(1 for e in games if e.get("winner") == _WOLF_WINNER)
    seer_disclosed = sum(1 for e in games if _dig(e, "key_scenes", "seer_disclosed_check"))

    # belief 块仅对有 belief_signal 的局聚合，否则整块 None。
    belief_games = [e for e in games if e.get("belief_signal")]
    belief: BeliefAgg | None = None
    if belief_games:
        belief = BeliefAgg(
            separation=_avg(
                [_dig(e, "belief_signal", "belief_quality", "avg_wolf_villager_separation") for e in belief_games]
            ),
            top_margin=_avg(
                [_dig(e, "belief_signal", "belief_quality", "avg_top_margin") for e in belief_games]
            ),
            entropy=_avg(
                [_dig(e, "belief_signal", "belief_quality", "avg_suspicion_entropy") for e in belief_games]
            ),
            top1_accuracy=_avg(
                [_dig(e, "belief_signal", "top_suspect_accuracy_rate") for e in belief_games]
            ),
            top2_accuracy=_avg(
                [_dig(e, "belief_signal", "top2_accuracy_rate") for e in belief_games]
            ),
            brier=_avg(
                [_dig(e, "belief_signal", "belief_quality", "avg_brier") for e in belief_games]
            ),
            consistency=_avg(
                [_dig(e, "belief_signal", "decision_top_suspect_consistency_rate") for e in belief_games]
            ),
            seer_identification=_avg(
                [_dig(e, "belief_signal", "per_role_identification", "seer_identification_accuracy") for e in belief_games]
            ),
        )

    fallback = FallbackAgg()
    for e in games:
        fb = e.get("fallback_breakdown") or {}
        rule_violation = fb.get("rule_violation") or {}
        rule_sum = sum(rule_violation.values()) if isinstance(rule_violation, dict) else 0
        fallback.llm_error += int(fb.get("llm_error", 0) or 0)
        fallback.parse_error += int(fb.get("parse_error", 0) or 0)
        fallback.rule_violation += int(rule_sum)
        fallback.schema_invalid += int(fb.get("schema_invalid_events", 0) or 0)

    slow = SlowThinkAgg()
    for e in games:
        sts = e.get("slow_think_stats") or {}
        slow.applied += int(sts.get("applied", 0) or 0)
        slow.parse_errors += int(sts.get("reflect_parse_errors", 0) or 0)

    return ArmAgg(
        n=n,
        win_rate_wolf=_rate(wolf_wins, n),
        seer_disclosure_rate=_rate(seer_disclosed, n),
        belief=belief,
        fallback=fallback,
        slow_think=slow,
        # default=None（非 0）：缺 decision_stats 的旧/坏 sidecar 不被当「0 错误」拉低均值，
        # 与「空集返 None 不返 0」一致；_avg 只对实有值取均。
        avg_llm_error_per_game=_avg(
            [_dig(e, "decision_stats", "llm_error", default=None) for e in games]
        ),
    )


def _game_row(extra: dict[str, Any]) -> BatchGameRow:
    return BatchGameRow(
        game_id=str(extra.get("game_id", "")),
        seed=extra.get("seed"),
        arm=str(extra.get("arm", "unknown")),
        winner=extra.get("winner"),
        rounds=extra.get("rounds"),
        llm_error=int(_dig(extra, "decision_stats", "llm_error", default=0) or 0),
        seer_disclosed=bool(_dig(extra, "key_scenes", "seer_disclosed_check")),
        fallback_total=_fallback_total(extra),
    )


# --------------------------------------------------------------------------- #
# 对外（端点 + 测试共用，root 注入便于单测）
# --------------------------------------------------------------------------- #


def list_batches(root: Path | None = None) -> list[BatchSummary]:
    """扫描 ``<root>/*/report.json`` 配对 extras，返回 batch 摘要列表。"""
    root = root or _batch_root()
    if not root.is_dir():
        return []
    summaries: list[BatchSummary] = []
    for report_path in sorted(root.glob("*/report.json")):
        # 跳过经符号链接逃逸出 root 的 batch 目录（与 get_batch_report 同一道防线）。
        if not _within_root(report_path, root):
            continue
        report = _read_json(report_path)
        if not isinstance(report, dict):
            continue
        extras = _read_json(_extras_path(report_path))
        extras = extras if isinstance(extras, list) else []
        summaries.append(_summarize(report_path.parent.name, report, extras))
    summaries.sort(key=lambda s: s.created_at or "", reverse=True)
    return summaries


def get_batch_report(run_id: str, root: Path | None = None) -> BatchReportDetail | None:
    """单批聚合：arm 对比 + 每局薄行。run_id 不存在返 None。"""
    root = root or _batch_root()
    if not _is_safe_run_id(run_id):
        return None
    report_path = root / run_id / "report.json"
    # 二道防线：解析后必须仍在 batch 根内，挡符号链接 / 边角穿越。
    if not _within_root(report_path, root):
        return None
    report = _read_json(report_path)
    if not isinstance(report, dict):
        return None
    extras = _read_json(_extras_path(report_path))
    extras = extras if isinstance(extras, list) else []

    by_arm: dict[str, list[dict[str, Any]]] = {}
    for extra in extras:
        if isinstance(extra, dict):
            by_arm.setdefault(str(extra.get("arm", "unknown")), []).append(extra)

    return BatchReportDetail(
        run_id=run_id,
        report=report,
        arms={arm: _aggregate_arm(games) for arm, games in by_arm.items()},
        games=[_game_row(e) for e in extras if isinstance(e, dict)],
    )


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


@router.get("/api/audit/batches", response_model=list[BatchSummary])
def list_batches_endpoint() -> list[BatchSummary]:
    return list_batches()


@router.get("/api/audit/batches/{run_id}", response_model=BatchReportDetail)
def get_batch_report_endpoint(run_id: str) -> BatchReportDetail:
    detail = get_batch_report(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"batch run not found: {run_id}")
    return detail
