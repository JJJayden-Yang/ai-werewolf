"""Belief Curve 审计端点（A 的只读 belief 端点）。

把队友已落盘的单局 belief 历史（``BeliefStateStore.get_history``）派生成可画的质量曲线，
给前端 ``belief/`` 页画折线。派生口径见 ``scripts/_belief_curve.py``（与批量聚合同一套数学）。

红线自检：
- ✅ 不改 ``contracts/``：响应模型住本文件。
- ✅ 不读 TruthState 做实时 ``separation``（observer 视角置 None，真相版留赛后）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.runtime import get_belief_store
from scripts._belief_curve import belief_curve_series

if TYPE_CHECKING:
    from stores.belief_state_store import BeliefStateStore

router = APIRouter()

# 探测的座位上限：覆盖 6/9/12 人局所有可能 agent_id（与 audit_service 同口径）。
_MAX_SEATS = 12


def get_belief_store_for_curve() -> "BeliefStateStore":
    """端点专用 belief store —— 解决"外部进程写盘后端点读不到"的缓存陈旧问题。

    背景：``JsonlBeliefStateStore`` 构造时一次性 hydrate 进内存索引，``get_belief_store()``
    又缓存单例。所以 API 起过之后、``run_mixed_batch --belief-dir`` 另一进程写的新局，缓存
    单例看不到 → ``/belief_curve`` 会一直 404 直到重启。

    修法（不改 C 的 store / runtime）：
    - **jsonl 后端**：每请求**新建** ``JsonlBeliefStateStore``（重新 hydrate = 吃到外部新写），
      无需重启 API。代价是按请求重读 belief 根；审计规模（数十~数百局）可接受，必要时再加
      mtime 缓存。
    - **memory 后端**：内存单例才是真相源（in-process 跑的局写在那），仍用共享 ``get_belief_store()``。
    """
    backend = os.getenv("AI_WOLF_STORAGE_BACKEND", "memory").lower()
    if backend == "jsonl":
        from stores.belief_state_store import JsonlBeliefStateStore

        root = Path(os.getenv("AI_WOLF_DATA_DIR", "./data"))
        return JsonlBeliefStateStore(root / "belief_states")
    return get_belief_store()


# --------------------------------------------------------------------------- #
# 响应模型（api 自有，不碰 contracts/）
# --------------------------------------------------------------------------- #


class CurvePoint(BaseModel):
    step: int
    round: int | None = None
    phase: str = ""
    by_target: dict[str, dict[str, float]] = Field(default_factory=dict)
    top1_target: str | None = None
    top1_prob: float = 0.0
    top_margin: float = 0.0
    separation: float | None = None
    entropy: float = 0.0


class BeliefCurve(BaseModel):
    game_id: str
    lane: Literal["real", "shadow"]
    observers: list[str]
    series: dict[str, list[CurvePoint]]


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #


def _discover_observers(
    game_id: str, belief_store: "BeliefStateStore", *, is_shadow: bool
) -> list[str]:
    """探测该 lane 下有 belief 历史的 agent_id（P1..P_MAX）。"""
    found: list[str] = []
    for i in range(1, _MAX_SEATS + 1):
        agent_id = f"P{i}"
        if belief_store.get_history(game_id, agent_id, is_shadow=is_shadow):
            found.append(agent_id)
    return found


def get_belief_curve(
    game_id: str,
    belief_store: "BeliefStateStore",
    *,
    observer: str | None = None,
    lane: Literal["real", "shadow"] = "real",
) -> dict[str, Any] | None:
    """组装 BeliefCurve（dict 形态）。无任何 observer 历史返 None（404）。

    ``observer`` 省略 → 返回全部有历史的 observer。
    """
    is_shadow = lane == "shadow"
    if observer is not None:
        observers = (
            [observer]
            if belief_store.get_history(game_id, observer, is_shadow=is_shadow)
            else []
        )
    else:
        observers = _discover_observers(game_id, belief_store, is_shadow=is_shadow)

    if not observers:
        return None

    series: dict[str, list[dict[str, Any]]] = {}
    for obs in observers:
        history = belief_store.get_history(game_id, obs, is_shadow=is_shadow)
        series[obs] = belief_curve_series(history, self_id=obs)

    return {
        "game_id": game_id,
        "lane": lane,
        "observers": observers,
        "series": series,
    }


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


@router.get("/api/audit/runs/{game_id}/belief_curve", response_model=BeliefCurve)
def get_belief_curve_endpoint(
    game_id: str,
    observer: str | None = Query(default=None),
    lane: Literal["real", "shadow"] = Query(default="real"),
    belief_store: "BeliefStateStore" = Depends(get_belief_store_for_curve),
) -> dict[str, Any]:
    curve = get_belief_curve(game_id, belief_store, observer=observer, lane=lane)
    if curve is None:
        raise HTTPException(
            status_code=404,
            detail=f"no belief history for game {game_id} (lane={lane}, observer={observer})",
        )
    return curve
