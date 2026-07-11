"""C 跨边界访问 ``game_core`` 的窄接口。

``ContextAssembler`` 通过 Protocol 拿 ``GameSession``（含 ``TruthState`` 引用），
但不依赖 ``game_core`` 的具体实现。

接口语义与 A 的 ``game_core.SessionProvider`` 对齐（方法名 ``get_session``）。
A 在 2026-05-22 21:19 群里的对接说明确认了这个签名：

    >>> from game_core import SessionProvider  # 待 A 推完后启用
    >>> # 当前 A 还没推 SessionProvider，鸭子类型也能满足

supervisor 注入 ``GameEngine`` 时，``GameEngine.get_session(game_id)`` 应该满足
此 Protocol（A 配合改）。

红线：本 Protocol 只允许"读" ``GameSession``；``ContextAssembler`` 永远不能
修改 ``GameSession`` 内任何字段，也不能把 ``GameSession`` 整个传给 Agent。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from game_core.types import GameSession


class GameSessionProvider(Protocol):
    """读 GameSession 的窄接口。

    实现示例：A 的 ``GameEngine`` 应该满足此 Protocol，签名约定为
    ``get_session(game_id) -> GameSession``。

    待 A 把 ``SessionProvider`` 推进 ``game_core`` 后，此处的 Protocol 可作
    类型别名直接用 ``from game_core import SessionProvider`` 替代；当前用本地
    Protocol 鸭子类型解耦。
    """

    def get_session(self, game_id: str) -> GameSession: ...
