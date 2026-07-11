"""C 的 API 层 —— FastAPI backend。

按总体阶段规划：
- S2  ReplayData 初版：`GET /replay/{game_id}` —— 本目录的主线。
- S2.5 Azure 部署：`/health` + `/replay` 作为最小可部署端点；
  正式 `/run`、`/state`、`/api/games` 等端点在 S2/S5 时陆续加。

设计要点：
- `replay_service.assemble_replay()` 是纯函数，可以脱离 HTTP 单测。
- FastAPI 应用通过 `dependency_overrides` 注入 `EventStore` /
  `SessionProvider`，本地默认 InMemory，部署时切到 JSONL / 真 Engine。
"""

from api.main import app
from api.replay_service import ReplayNotFoundError, assemble_replay

__all__ = ["app", "assemble_replay", "ReplayNotFoundError"]
