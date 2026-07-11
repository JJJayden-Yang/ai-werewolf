"""Shared game assembly factory.

phase5 三方向并行地基 §10 —— A 抽出共享装配函数，避免 A 的批跑
(``scripts/run_v0_batch.py``)和 B 的实时观战(``api/.../POST /games``)各写一份将来漂移。

Typical usage::

    from runner import build_game

    built = build_game(config, agent, arm="v1", seed=42)
    asyncio.run(built.supervisor.run_game(config.game_id))
"""

from runner.builder import BuiltGame, GameStores, build_game

__all__ = ["BuiltGame", "GameStores", "build_game"]
