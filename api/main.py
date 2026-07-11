"""FastAPI 应用入口 —— C 的 API 层。

启动方式（本地）：

```bash
uvicorn api.main:app --reload
```

启动方式（容器）：

```bash
docker run -p 8000:8000 \
  -e AI_WOLF_STORAGE_BACKEND=jsonl \
  -e AI_WOLF_DATA_DIR=/data \
  -v ai_wolf_data:/data \
  ai_wolf:0.1
```

环境变量（见 ``.env.example``）：

- ``AI_WOLF_STORAGE_BACKEND``：``memory``（默认，开发/单元测试）｜ ``jsonl``
  （EventStore + TraceStore 落盘到 ``$AI_WOLF_DATA_DIR``）
- ``AI_WOLF_DATA_DIR``：JSONL 后端的数据根目录（默认 ``./data``）。
  实际落盘：``<dir>/events/*.jsonl`` + ``<dir>/traces/*.jsonl``。

依赖注入策略：
- ``Depends(get_event_store)`` / ``Depends(get_session_provider)`` /
  ``Depends(get_trace_store)`` 让测试通过 ``app.dependency_overrides`` 替换。
- 模块 import 时根据环境变量构造默认后端。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.audit_batch_service import router as audit_batch_router
from api.audit_belief_curve import router as audit_belief_curve_router
from api.audit_suspicion_network import router as audit_suspicion_network_router
from api.audit_service import get_audit_run, list_audit_runs
from api.game_service import registry as game_registry
from api.game_service import router as game_router
from api.data_export_service import router as data_export_router
from api.metrics_service import router as metrics_router
from api.strategy_review_service import router as strategy_review_router
from api.replay_service import ReplayNotFoundError, assemble_replay, list_replay_summaries
from api.runtime import (
    get_belief_store,
    get_event_store,
    get_replay_truth_store,
    get_session_provider,
    get_trace_store,
)
from api.soul_service import router as soul_router
from contracts.schemas import ReplayData
from runner.launcher import load_env_file
from stores.event_store import EventStore

if TYPE_CHECKING:
    from game_core.protocols import SessionProvider
    from stores.replay_truth_store import ReplayTruthStore


# 启动即加载本地凭证文件（.env / scripts/Yuan_local/.env.local），让 POST /games 的
# 真实 LLM 模式读到 ARK_* 凭证。os.environ.setdefault 不覆盖已 export 的变量。
load_env_file()


# --- 应用本体 ---

app = FastAPI(
    title="AI Wolf API",
    version="0.1.0",
    description=(
        "C 的 backend service。Phase 2.5 提供最小可部署端点："
        "/health 与 /replay/{game_id}。S2/S5 起陆续加 /run、/state、"
        "/api/games 等。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3010",
        "http://127.0.0.1:3010",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(game_router)
app.include_router(soul_router)
# A 的只读 belief/eval 端点（V2 审计数据接口 §1/§2）。唯一碰 B 的 main.py 的两行。
app.include_router(audit_batch_router)
app.include_router(audit_belief_curve_router)
app.include_router(audit_suspicion_network_router)
app.include_router(metrics_router)
app.include_router(data_export_router)
app.include_router(strategy_review_router)


@app.on_event("startup")
def _warm_metrics_cache_on_startup() -> None:
    """后台预热 metrics per-game 缓存，避免重启后第一次看板请求冷扫全量超时。"""
    import threading

    from api.metrics_service import warm_metrics_cache

    def _warm() -> None:
        try:
            warm_metrics_cache(
                get_event_store(),
                get_trace_store(),
                get_belief_store(),
                get_replay_truth_store(),
            )
        except Exception:
            pass  # 预热失败不影响启动，端点会按需懒算

    threading.Thread(target=_warm, name="metrics-cache-warm", daemon=True).start()


@app.get("/health")
def health() -> dict[str, str]:
    """轻量 health check，给 Azure / 监控用。

    按 总体阶段规划 §Azure检查表 通过标准：返回 status / version / time /
    storage_backend，让运维确认服务状态与后端配置。
    """
    from datetime import datetime, timezone

    return {
        "status": "ok",
        "version": app.version,
        "time": datetime.now(timezone.utc).isoformat(),
        "storage_backend": os.getenv("AI_WOLF_STORAGE_BACKEND", "memory").lower(),
    }


@app.get("/replays")
def list_replays(
    event_store: EventStore = Depends(get_event_store),
    trace_store=Depends(get_trace_store),
    limit: int = 200,
    offset: int = 0,
) -> dict[str, list[dict]]:
    """列出历史局摘要，分页加载避免全量读盘 OOM。"""
    return {
        "replays": list_replay_summaries(
            event_store,
            trace_store=trace_store,
            mode_overrides=_replay_mode_overrides(),
            limit=limit,
            offset=offset,
        )
    }


@app.get("/api/audit/runs")
def list_audit_runs_endpoint(
    event_store: EventStore = Depends(get_event_store),
    trace_store=Depends(get_trace_store),
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """动态扫描 data 目录并返回审计对局列表，分页加载避免全量读盘 OOM。

    ``total`` 为盘上对局总数（只 glob 文件名，不读盘），供前端展示全量对局数；
    ``audit_runs`` 仅含 ``[offset, offset+limit)`` 这一页（默认最近 limit 局）。
    """
    data_dir = os.getenv("AI_WOLF_DATA_DIR", "./data")
    list_game_ids = getattr(event_store, "list_game_ids", None)
    total = len(list_game_ids()) if callable(list_game_ids) else 0
    return {
        "total": total,
        "audit_runs": list_audit_runs(
            event_store,
            trace_store=trace_store,
            data_dir=data_dir,
            limit=limit,
            offset=offset,
        ),
    }


@app.get("/api/audit/runs/{game_id}")
def get_audit_run_endpoint(
    game_id: str,
    event_store: EventStore = Depends(get_event_store),
    trace_store=Depends(get_trace_store),
    belief_store=Depends(get_belief_store),
) -> dict[str, Any]:
    """按 game_id 返回完整审计数据（事件 + trace + belief + 统计）。

    返回 RunAuditData 结构供前端审计页展示（本局指标、阶段索引、事件时间线、信念数据等）。

    Raises:
        HTTPException 404: 对局不存在或没有事件。
    """
    audit = get_audit_run(game_id, event_store, trace_store=trace_store, belief_store=belief_store)
    if not audit:
        raise HTTPException(status_code=404, detail=f"对局 {game_id} 不存在")
    return {"audit": audit}


@app.get("/replay/{game_id}", response_model=ReplayData)
def get_replay(
    game_id: str,
    event_store: EventStore = Depends(get_event_store),
    session_provider: "SessionProvider | None" = Depends(get_session_provider),
    replay_truth_store: "ReplayTruthStore" = Depends(get_replay_truth_store),
) -> ReplayData:
    """按 game_id 返回完整 ReplayData。

    数据来源：``event_store.list_by_game`` + （可选）``session_provider.
    get_session`` 拿到 GameSession 提取 players。

    Raises:
        HTTPException 404: 该 game_id 既没有事件，session_provider 也
            没拿到 session（实际上不存在或还没开始）。
    """
    try:
        replay = assemble_replay(
            game_id,
            event_store=event_store,
            session_provider=session_provider,
            replay_truth_store=replay_truth_store,
        )
        return _with_registry_role_map(replay)
    except ReplayNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _with_registry_role_map(replay: ReplayData) -> ReplayData:
    if replay.players:
        return replay
    record = game_registry.get(replay.game_id)
    if record is None or not record.role_map:
        return replay
    players = [
        {
            "player_id": player_id,
            "role": role,
            "camp": "werewolf" if role == "werewolf" else "villager",
            "status": "alive",
            "public_claim": None,
            "vote_weight": 1,
        }
        for player_id, role in sorted(record.role_map.items())
    ]
    return replay.model_copy(update={"players": players})


def _replay_mode_overrides() -> dict[str, str]:
    return {
        record.game_id: _mode_label_for_record(record)
        for record in game_registry.list()
    }


def _mode_label_for_record(record) -> str:
    if record.mode == "mock":
        return "Mock"
    if record.arm == "v2":
        return "LLM v2"
    if record.arm == "v1":
        return "LLM v1"
    return "LLM v0"
