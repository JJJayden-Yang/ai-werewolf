"""按版本（arm）导出原始对局数据的端点。

给队友一个「选 V0/V1/V2 → 看各自多少局 → 一键下载」的入口。下载产物是 ZIP，**保留磁盘原结构**
（``events/<gid>.jsonl`` / ``traces/<gid>.jsonl`` / ``belief_states/<gid>/<agent>/{real,shadow}.jsonl``
/ ``replay_truth/<gid>.json``），忠实于数据源格式。

红线自检：
- ✅ 不改 ``contracts/``：只读已落盘数据，无新模型进冻结区。
- ✅ arm 只用于过滤，``types`` 对白名单校验，``gid`` 来自磁盘列举 —— 无路径穿越。
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

router = APIRouter(prefix="/api/data", tags=["data-export"])

# 可下载的数据类型（= 磁盘上的子目录名）。
DATA_TYPES = ("events", "traces", "belief_states", "replay_truth")
OTHER_ARM = "other"

_BATCH_PREFIX_RE = re.compile(r"^batch_(v[012])_", re.IGNORECASE)
_MIXED_PREFIX_RE = re.compile(r"^mixed_batch", re.IGNORECASE)


def _root() -> Path:
    return Path(os.getenv("AI_WOLF_DATA_DIR", "./data"))


def _arm_of(game_id: str) -> str:
    """仅按 game_id 前缀分（batch_v0/1/2_*、mixed_batch_*）；非批量局归 ``other``。

    导出按版本筛选，前缀就是版本的权威来源；不读 trace，保证 ``/arms`` 计数够快。
    """
    if _MIXED_PREFIX_RE.match(game_id):
        return "mixed"
    m = _BATCH_PREFIX_RE.match(game_id)
    if m:
        return m.group(1).lower()
    return OTHER_ARM


def _all_game_ids(root: Path) -> list[str]:
    events_dir = root / "events"
    if not events_dir.is_dir():
        return []
    return sorted(p.stem for p in events_dir.glob("*.jsonl"))


@router.get("/arms")
def list_arms() -> dict:
    """各 arm 有多少局（按 events 文件计；每局必有 events）。"""
    root = _root()
    counts: dict[str, int] = {}
    for gid in _all_game_ids(root):
        arm = _arm_of(gid)
        counts[arm] = counts.get(arm, 0) + 1
    arms = [{"arm": a, "games": c} for a, c in sorted(counts.items())]
    return {"arms": arms, "data_types": list(DATA_TYPES)}


def _add_game_files(zf: zipfile.ZipFile, root: Path, gid: str, dtype: str) -> None:
    if dtype in ("events", "traces"):
        f = root / dtype / f"{gid}.jsonl"
        if f.exists():
            zf.write(f, arcname=f"{dtype}/{gid}.jsonl")
    elif dtype == "replay_truth":
        f = root / dtype / f"{gid}.json"
        if f.exists():
            zf.write(f, arcname=f"{dtype}/{gid}.json")
    elif dtype == "belief_states":
        d = root / dtype / gid
        if d.is_dir():
            for sub in sorted(d.rglob("*.jsonl")):
                zf.write(sub, arcname=f"belief_states/{gid}/{sub.relative_to(d)}")


@router.get("/download")
def download(
    arm: str = Query(..., description="v0 / v1 / v2 / mixed / other"),
    types: str = Query(
        "events,traces,belief_states,replay_truth",
        description="逗号分隔的数据类型子集",
    ),
    limit: int | None = Query(None, description="只取最近 N 局（按 game_id 排序末 N 个）"),
) -> FileResponse:
    root = _root()
    sel = [t for t in types.split(",") if t in DATA_TYPES]
    if not sel:
        raise HTTPException(status_code=400, detail=f"无有效 types，可选: {list(DATA_TYPES)}")

    gids = [g for g in _all_game_ids(root) if _arm_of(g) == arm]
    if limit and limit > 0:
        gids = gids[-limit:]
    if not gids:
        raise HTTPException(status_code=404, detail=f"arm={arm} 没有可下载的对局")

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for gid in gids:
                for dtype in sel:
                    _add_game_files(zf, root, gid, dtype)
    finally:
        tmp.close()

    filename = f"{arm}_{'_'.join(sel)}_{len(gids)}games.zip"
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(os.unlink, tmp.name),
    )
