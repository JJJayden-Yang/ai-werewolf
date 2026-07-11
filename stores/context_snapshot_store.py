"""ContextSnapshotStore —— Task C / Phase 3 占位骨架。

按 ``finalPlan/phase3_contract_reservation_audit.md §5.1`` 预留：
``ContextSnapshot`` 是 evaluator/debug-only 的"角色视角忠实复盘"，存放每次决策
点喂给 LLM 的 ``AgentContext`` 完整 dump（加可选 ``rendered_prompt_ref``）。

落库策略（C 拍板 2026-05-25）：

- **LLM 局**（v0/v1，S7/S8）：每决策点全落，估算 9 人 * 5 轮 * 4 phase ≈
  180 snapshot/局 * 2-3KB/snapshot ≈ 500KB/局，可接受。
- **Mock 压测**（S2/S5）：默认不落；仅 BadCase 触发时回填最近 3 个 snapshot。

落点（与 EventStore / TraceStore 同 root 平级）：

```
<AI_WOLF_DATA_DIR>/
└── context_snapshots/
    └── <game_id>/
        └── <agent_id>/
            └── <round>_<phase>.json
```

**当前状态**：``ContextSnapshot`` schema 尚未在 ``contracts/schemas.py`` 落地
（待 A 起 contract MR 一并 merge，见 phase3_contract_reservation_audit §5.1）。
因此本模块仅留 ABC + 占位实现，等 schema 进 main 后补 InMemory / JSONL
完整实装，与 ``stores/trace_store.py`` 同款 pattern。

参考已实装的同型 store：

- ``stores/event_store.py``  ABC + InMemory + JSONL
- ``stores/trace_store.py``  ABC + InMemory + JSONL + AgentTuningTraceStore 占位
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 实装时切换为：from contracts.schemas import ContextSnapshot
    # 现在 schema 还没 merge，先用 Any 占位避免循环或 fail import。
    from typing import Any as ContextSnapshot  # type: ignore[assignment]


class ContextSnapshotStore(ABC):
    """ContextSnapshot 持久化抽象接口。

    与 ``TraceStore`` 同款 append-only 语义：每次决策前装配的 ``AgentContext``
    dump + 可选 prompt 引用，落库后不再修改；同 ``context_id`` 重复 append
    应抛 ``DuplicateContextSnapshotError``（与现有 StoreError 体系一致）。

    实装时需要补：

    - ``InMemoryContextSnapshotStore``  dict 索引，单测/mock 用
    - ``JsonlContextSnapshotStore``  按 ``<game_id>/<agent_id>/<round>_<phase>.json``
      落盘；构造时 hydrate；append 双写失败回滚
    - ``stores/exceptions.py``  补 ``ContextSnapshotNotFoundError`` /
      ``DuplicateContextSnapshotError``
    - ``stores/__init__.py``  补 export
    """

    @abstractmethod
    def append(self, snapshot: ContextSnapshot) -> None:
        """追加一条 context snapshot。append-only，重复 context_id 抛错。"""

    @abstractmethod
    def list_by_game(self, game_id: str) -> list[ContextSnapshot]:
        """按 game_id 列出所有 snapshot，按插入顺序；未知 game_id 返回 []。"""

    @abstractmethod
    def list_by_agent(
        self, game_id: str, agent_id: str
    ) -> list[ContextSnapshot]:
        """按 (game_id, agent_id) 列出；未知组合返回 []。"""

    @abstractmethod
    def get(self, context_id: str) -> ContextSnapshot:
        """按 context_id 取单条；不存在抛 ContextSnapshotNotFoundError。"""


class _PlaceholderContextSnapshotStore(ContextSnapshotStore):
    """占位实现 —— 调用任何方法都抛 NotImplementedError。

    A 在 contract MR 把 ``ContextSnapshot`` schema merge 进 ``contracts/`` 之后，
    本类应替换为 InMemory + JSONL 真实实装（参考 trace_store.py:103+ 模式）。
    """

    _PENDING = (
        "ContextSnapshotStore pending: ContextSnapshot schema not yet merged. "
        "See phase3_contract_reservation_audit.md §5.1."
    )

    def append(self, snapshot: ContextSnapshot) -> None:  # noqa: ARG002
        raise NotImplementedError(self._PENDING)

    def list_by_game(self, game_id: str) -> list[ContextSnapshot]:  # noqa: ARG002
        raise NotImplementedError(self._PENDING)

    def list_by_agent(
        self, game_id: str, agent_id: str  # noqa: ARG002
    ) -> list[ContextSnapshot]:
        raise NotImplementedError(self._PENDING)

    def get(self, context_id: str) -> ContextSnapshot:  # noqa: ARG002
        raise NotImplementedError(self._PENDING)
