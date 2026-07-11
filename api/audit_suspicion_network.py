"""怀疑网 / 心证面板 审计端点（A 的只读 god-view 复盘端点）。

复用 ``scripts.belief_viz_export.export_game``——直接读 ``AI_WOLF_DATA_DIR`` 落盘的
events/traces/belief_states 文件（按 event_id 定序，永远最新，绕开 store 缓存陈旧），
派生出前端可直接画的：players + belief_curves + suspicion_network_frames(怀疑网 + 心证面板)
+ key_scenes。

⚠ **god-view**：返回真身份（players.role / nodes.camp），仅供 admin 赛后复盘 UI；
玩家实时视角不可用本端点（信息隔离红线）。与 ``/api/audit/runs/{id}`` 同为 admin 审计口径。

红线自检：
- ✅ 不改 ``contracts/``：直接返回 export_game 的 dict。
- ✅ 不碰 B/C 内部对象：只读盘 + 复用 A 自己的 scripts。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from scripts.belief_viz_export import export_game

router = APIRouter()


@router.get("/api/audit/runs/{game_id}/suspicion-network")
def get_suspicion_network(game_id: str) -> dict:
    """单局怀疑网 + 心证面板 + 戏剧节点（god-view 复盘）。"""
    data_dir = os.getenv("AI_WOLF_DATA_DIR", "./data")
    try:
        return export_game(data_dir, game_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
