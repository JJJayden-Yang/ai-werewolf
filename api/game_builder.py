"""实时对局 API 的装配入口 —— 薄壳，委托给 ``runner.launcher``。

历史上这里写死 ``RoleStrategyMockAgent``（前端按钮永远只跑 mock）。现统一到
``runner.launcher.assemble_game``，支持 mock / 真实 LLM 切换，与后端 CLI
（``scripts/start_game.py``）共享同一套开局逻辑。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from runner import BuiltGame
from runner.launcher import LaunchSpec, MissingCredentialsError, assemble_game

if TYPE_CHECKING:
    from agent_runtime import HumanInputChannel
    from contracts import Role
    from stores.belief_state_store import BeliefStateStore
    from stores.event_store import EventStore
    from stores.trace_store import TraceStore

__all__ = ["build_game", "MissingCredentialsError"]


def build_game(
    player_count: int,
    arm: str,
    seed: int,
    temperature: float,
    *,
    mode: str = "llm",
    model_flavor: str = "PRO",
    max_rounds: int | None = None,
    seat_souls: dict[str, str] | None = None,
    event_store: "EventStore | None" = None,
    trace_store: "TraceStore | None" = None,
    belief_store: "BeliefStateStore | None" = None,
    human_seat: str | None = None,
    human_role: "Role | None" = None,
    human_channel: "HumanInputChannel | None" = None,
) -> tuple[BuiltGame, str]:
    """装配一局实时观战用游戏。

    默认 ``mode="llm"`` 真实跑；``mode="mock"`` 不消耗 token，供本地验证链路。
    缺凭证时 ``assemble_game`` 抛 ``MissingCredentialsError``，由 game_service 转 400。
    """
    # 高级策略库（场景命中时注入专家打法片段，如首日平安夜/对跳/女巫毒/被指认）。
    # 默认对 UI 开的所有局（含 v0）启用 —— snippet 是阵营无关的通用打法增量，作为所有 arm
    # 共享的基础能力，v0/v1/v2 的对比仍只隔离 belief。设 AI_WOLF_USE_STRATEGY=0 可关掉。
    use_strategy = os.getenv("AI_WOLF_USE_STRATEGY", "1") == "1"
    spec = LaunchSpec(
        player_count=player_count,
        arm=arm,
        mode=mode,
        seed=seed,
        temperature=temperature,
        model_flavor=model_flavor,
        max_rounds=max_rounds,
        seat_souls=seat_souls,
        human_seat=human_seat,
        human_role=human_role,
        use_strategy=use_strategy,
    )
    # 只有当启用 Belief（v1 / v2）时才传入 belief_store
    # v2 = v1 内核 + factorized_v2 kernel + slow_think，同样需要 API 共享的 belief 落盘
    belief_store_to_use = belief_store if arm in ("v1", "v2") else None
    return assemble_game(
        spec,
        event_store=event_store,
        trace_store=trace_store,
        belief_store=belief_store_to_use,
        human_channel=human_channel,
    )
