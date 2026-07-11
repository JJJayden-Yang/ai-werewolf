"""A 对外暴露的只读访问协议。

跨模块依赖应依赖结构化 Protocol，而非具体的 GameEngine 对象，这样 A 能在不破坏
下游（C 的 ContextAssembler 等）的前提下替换内部实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from game_core.types import GameSession


@runtime_checkable
class SessionProvider(Protocol):
    """按 game_id 只读取出 GameSession。GameEngine 天然满足。

    C 的 ContextAssembler 构造时依赖本协议（而非 import 具体 GameEngine），
    Supervisor 注入 GameEngine。实现方保证返回的是只读用途的 GameSession。
    """

    def get_session(self, game_id: str) -> GameSession: ...
