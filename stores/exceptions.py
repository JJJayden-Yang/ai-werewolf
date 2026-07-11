"""stores/ 共享异常。

各 Store 的"找不到"和"重复"用统一异常基类 StoreError，调用方可以一处 catch，
也可以按具体子类精细处理。
"""

from __future__ import annotations


class StoreError(Exception):
    """所有 Store 异常的共同基类。"""


class EventNotFoundError(StoreError):
    """EventStore.get 找不到指定 event_id 时抛出。"""

    def __init__(self, event_id: str) -> None:
        super().__init__(f"event not found: event_id={event_id!r}")
        self.event_id = event_id


class DuplicateEventError(StoreError):
    """EventStore.append 收到已存在的 event_id 时抛出。

    event_id 由 EventEmitter 生成；重复一般意味着上游 bug
    （比如同一 GameEvent 被 emit 两次），不应静默吞掉。
    """

    def __init__(self, event_id: str) -> None:
        super().__init__(f"duplicate event_id: {event_id!r}")
        self.event_id = event_id


class BeliefStateNotFoundError(StoreError):
    """BeliefStateStore.get 在 (game_id, agent_id, is_shadow) 维度找不到任何已保存的
    belief 时抛出。

    与 ``EventNotFoundError`` 一样，"没存过" 是程序逻辑错误（调用方在没人 save
    的情况下 get），不静默返回 None；而 ``get_history`` 在同样情况下返回 ``[]``。
    """

    def __init__(self, game_id: str, agent_id: str, *, is_shadow: bool = False) -> None:
        lane = "shadow" if is_shadow else "real"
        super().__init__(
            f"belief state not found: game_id={game_id!r} agent_id={agent_id!r} lane={lane}"
        )
        self.game_id = game_id
        self.agent_id = agent_id
        self.is_shadow = is_shadow


class TraceNotFoundError(StoreError):
    """TraceStore.get 找不到指定 trace_id 时抛出。"""

    def __init__(self, trace_id: str) -> None:
        super().__init__(f"trace not found: trace_id={trace_id!r}")
        self.trace_id = trace_id


class DuplicateTraceError(StoreError):
    """TraceStore.append 收到已存在的 trace_id 时抛出。

    trace_id 由 Supervisor / Runtime 生成；重复一般意味着上游 bug
    （比如同一次决策被记录两次），不应静默吞掉。
    """

    def __init__(self, trace_id: str) -> None:
        super().__init__(f"duplicate trace_id: {trace_id!r}")
        self.trace_id = trace_id
