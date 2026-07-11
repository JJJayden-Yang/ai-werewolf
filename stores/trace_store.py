"""TraceStore —— Task C / S1。

`AgentDecisionTrace` 的持久化层。每次 Agent 决策（mock 或 LLM）都可以
emit 一条 trace 落盘，用于 Replay / Evaluation / Debug / S10 PostGameAnalyzer。

模块结构：

- ``TraceStore``  对外接口（ABC）；任何后端都必须实现下列方法。
- ``InMemoryTraceStore``  dict 实现；测试 / Mock smoke 默认。
- ``JsonlTraceStore``  按 ``game_id`` 一个 ``.jsonl`` 文件落盘；本地 debug / Replay 用。
- ``AgentTuningTraceStore``  S10 prompt 调优证据链使用，本阶段先留 NotImplementedError
  占位（schema 已在 contracts，但 PostGameAnalyzer / PromptVersionRegistry 还没启动）。
- 异常见 ``stores.exceptions``。

接口语义（与 ``finalPlan/Interface_v2_1.md`` §6.4 一致）：

```python
class TraceStore:
    def append(self, trace: AgentDecisionTrace) -> None
    def append_many(self, traces: list[AgentDecisionTrace]) -> None
    def list_by_game(self, game_id: str) -> list[AgentDecisionTrace]
    def list_by_agent(self, game_id: str, agent_id: str) -> list[AgentDecisionTrace]
    def get(self, trace_id: str) -> AgentDecisionTrace
```

约定：

- **append-only**：trace 一旦写入即不可变；删除/修改不在接口内。
- **唯一 trace_id**：重复 append 抛 ``DuplicateTraceError``，绝不静默覆盖。
- **插入序**：``list_by_game`` / ``list_by_agent`` 按插入时间返回。
- **缺失 trace_id** 抛 ``TraceNotFoundError``；
  **未知 game_id / (game_id, agent_id)** 返回 ``[]``（合法状态）。
- **不存 TruthState**：``input_summary`` 应是 AgentContext 的派生摘要，不含真相态。
  调用方（Supervisor / Runtime）负责保证。
- **线程**：第一阶段串行优先，本层 not thread-safe。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from stores.exceptions import DuplicateTraceError, TraceNotFoundError

if TYPE_CHECKING:
    from contracts.schemas import AgentDecisionTrace, AgentTuningTrace


# ---------- 接口 ----------


class TraceStore(ABC):
    """AgentDecisionTraceStore 抽象接口。

    所有具体后端（InMemory / JSONL / 未来的 SQLite）必须实现下列方法。
    """

    @abstractmethod
    def append(self, trace: AgentDecisionTrace) -> None:
        """追加一条决策 trace。

        Raises:
            DuplicateTraceError: ``trace.trace_id`` 已存在。
        """

    @abstractmethod
    def append_many(self, traces: list[AgentDecisionTrace]) -> None:
        """批量追加；语义上等价于逐个 ``append``。

        若中途遇到 ``DuplicateTraceError``，前面已写入的 trace 不会回滚
        （append-only 不需要事务）。
        """

    @abstractmethod
    def list_by_game(self, game_id: str) -> list[AgentDecisionTrace]:
        """按 ``game_id`` 列出所有 trace，按插入顺序。

        未知 ``game_id`` 返回 ``[]``。
        """

    @abstractmethod
    def list_by_agent(self, game_id: str, agent_id: str) -> list[AgentDecisionTrace]:
        """按 ``(game_id, agent_id)`` 列出所有 trace，按插入顺序。

        未知组合返回 ``[]``。
        """

    @abstractmethod
    def get(self, trace_id: str) -> AgentDecisionTrace:
        """按 ``trace_id`` 取单条 trace。

        Raises:
            TraceNotFoundError: ``trace_id`` 不存在。
        """


# ---------- 实现 1：In-Memory ----------


class InMemoryTraceStore(TraceStore):
    """纯内存实现。

    适用于：
    - 单元测试（pytest 默认）；
    - Mock smoke run（不需要落盘）；
    - JsonlTraceStore 的内部索引。

    数据结构：

    - ``_by_trace_id: dict[str, AgentDecisionTrace]``  O(1) ``get``。
    - ``_trace_ids_by_game: dict[str, list[str]]``  保留插入序。
    - ``_trace_ids_by_agent: dict[(game_id, agent_id), list[str]]``  保留插入序。
    """

    def __init__(self) -> None:
        self._by_trace_id: dict[str, AgentDecisionTrace] = {}
        self._trace_ids_by_game: dict[str, list[str]] = defaultdict(list)
        self._trace_ids_by_agent: dict[tuple[str, str], list[str]] = defaultdict(list)

    # --- TraceStore 接口 ---

    def append(self, trace: AgentDecisionTrace) -> None:
        if trace.trace_id in self._by_trace_id:
            raise DuplicateTraceError(trace.trace_id)
        self._by_trace_id[trace.trace_id] = trace
        self._trace_ids_by_game[trace.game_id].append(trace.trace_id)
        self._trace_ids_by_agent[(trace.game_id, trace.agent_id)].append(trace.trace_id)

    def append_many(self, traces: list[AgentDecisionTrace]) -> None:
        for trace in traces:
            self.append(trace)

    def list_by_game(self, game_id: str) -> list[AgentDecisionTrace]:
        ids = self._trace_ids_by_game.get(game_id, [])
        return [self._by_trace_id[tid] for tid in ids]

    def list_by_agent(self, game_id: str, agent_id: str) -> list[AgentDecisionTrace]:
        ids = self._trace_ids_by_agent.get((game_id, agent_id), [])
        return [self._by_trace_id[tid] for tid in ids]

    def get(self, trace_id: str) -> AgentDecisionTrace:
        try:
            return self._by_trace_id[trace_id]
        except KeyError as exc:
            raise TraceNotFoundError(trace_id) from exc

    # --- 便利方法（不在接口里，仅用于内部 / 测试） ---

    def __contains__(self, trace_id: str) -> bool:
        return trace_id in self._by_trace_id

    def __len__(self) -> int:
        return len(self._by_trace_id)


# ---------- 实现 2：JSONL ----------


class JsonlTraceStore(TraceStore):
    """文件持久化实现。

    布局：

    ```
    <root_dir>/
    ├── <game_id_1>.jsonl   一行一个 AgentDecisionTrace，model_dump_json 序列化
    ├── <game_id_2>.jsonl
    └── ...
    ```

    实现策略：

    - 构造时扫描 ``root_dir`` 下所有 ``*.jsonl``，按行 hydrate 进内存索引。
    - ``append`` 双写：先 mem 校验（dup 检测），通过后 append-only 写文件；
      磁盘写失败回滚内存。
    - 每行 ``AgentDecisionTrace.model_dump_json()`` + ``\\n``。

    注意：
    - 不是 thread-safe；调用方自行保证。
    - 不做文件锁。
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._index = InMemoryTraceStore()
        # 不在启动时全量 hydrate：历史数据走 list_by_game 实时读盘，避免 OOM

    # --- TraceStore 接口 ---

    def append(self, trace: AgentDecisionTrace) -> None:
        # 1) 先写内存索引（dup 检测）
        self._index.append(trace)
        # 2) 再 append-only 落盘；失败则回滚内存
        path = self._path_for(trace.game_id)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(trace.model_dump_json() + "\n")
        except OSError:
            self._rollback(trace.trace_id, trace.game_id, trace.agent_id)
            raise

    def append_many(self, traces: list[AgentDecisionTrace]) -> None:
        grouped: dict[str, list[AgentDecisionTrace]] = defaultdict(list)
        for trace in traces:
            self._index.append(trace)  # dup 检测；抛错就中断
            grouped[trace.game_id].append(trace)

        for game_id, batch in grouped.items():
            path = self._path_for(game_id)
            try:
                with path.open("a", encoding="utf-8") as f:
                    for trace in batch:
                        f.write(trace.model_dump_json() + "\n")
            except OSError:
                for trace in batch:
                    self._rollback(trace.trace_id, trace.game_id, trace.agent_id)
                raise

    def list_by_game(self, game_id: str) -> list[AgentDecisionTrace]:
        # 先查内存索引（当局运行时 append 写入的）；历史局直接读盘，不回填索引。
        cached = self._index.list_by_game(game_id)
        if cached:
            return cached
        path = self._path_for(game_id)
        if not path.exists():
            return []
        from contracts.schemas import AgentDecisionTrace as _Trace  # noqa: PLC0415
        from stores.exceptions import DuplicateTraceError  # noqa: PLC0415
        traces: list[AgentDecisionTrace] = []
        seen_ids: set[str] = set()
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    trace = _Trace.model_validate_json(line)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"corrupt trace log at {path}:{line_no}: {exc}"
                    ) from exc
                if trace.trace_id in seen_ids:
                    raise DuplicateTraceError(trace.trace_id)
                seen_ids.add(trace.trace_id)
                traces.append(trace)
        return traces

    def list_by_agent(self, game_id: str, agent_id: str) -> list[AgentDecisionTrace]:
        return self._index.list_by_agent(game_id, agent_id)

    def get(self, trace_id: str) -> AgentDecisionTrace:
        return self._index.get(trace_id)

    # --- 内部辅助 ---

    def _path_for(self, game_id: str) -> Path:
        if "/" in game_id or "\\" in game_id or game_id in ("", ".", ".."):
            raise ValueError(f"invalid game_id for filesystem: {game_id!r}")
        return self.root_dir / f"{game_id}.jsonl"

    def _hydrate(self) -> None:
        """启动时把 root_dir 下所有 *.jsonl 回读入内存索引。"""
        from contracts.schemas import AgentDecisionTrace  # noqa: PLC0415

        for jsonl_file in sorted(self.root_dir.glob("*.jsonl")):
            with jsonl_file.open(encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    try:
                        trace = AgentDecisionTrace.model_validate_json(line)
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            f"corrupt trace log at {jsonl_file}:{line_no}: {exc}"
                        ) from exc
                    # hydrate 阶段碰到 dup 也直接抛 —— 同一 trace_id 出现两次 = 日志损坏
                    self._index.append(trace)

    def _rollback(self, trace_id: str, game_id: str, agent_id: str) -> None:
        """从内存索引里抽掉一条 trace（仅供磁盘写失败回滚使用）。"""
        if trace_id in self._index._by_trace_id:  # noqa: SLF001
            del self._index._by_trace_id[trace_id]  # noqa: SLF001
        ids = self._index._trace_ids_by_game.get(game_id, [])  # noqa: SLF001
        if trace_id in ids:
            ids.remove(trace_id)
        agent_ids = self._index._trace_ids_by_agent.get((game_id, agent_id), [])  # noqa: SLF001
        if trace_id in agent_ids:
            agent_ids.remove(trace_id)


# ---------- AgentTuningTraceStore：S10 占位 ----------


class AgentTuningTraceStore:
    """S10 prompt 调优证据链使用。本阶段保留 NotImplementedError 占位。

    Schema 已在 ``contracts.schemas.AgentTuningTrace``，但 PostGameAnalyzer /
    PromptVersionRegistry 还没启动 —— 等 S10 一起实装时再补 ABC + 后端。
    """

    def save(self, trace: AgentTuningTrace) -> None:  # noqa: ARG002
        raise NotImplementedError("AgentTuningTraceStore will be implemented in S10")

    def list_by_role(self, role: str) -> list[AgentTuningTrace]:  # noqa: ARG002
        raise NotImplementedError("AgentTuningTraceStore will be implemented in S10")
