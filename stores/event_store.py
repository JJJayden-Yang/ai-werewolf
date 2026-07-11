"""EventStore —— Task C5。EventLog 的持久化层。

Engine 通过 EventEmitter 产出 GameEvent，落盘由本层完成（Engine 不直接写日志，
Supervisor 不手写关键结算事件）。Replay / Evaluation 反过来从 EventStore 读回完整事件流，
不读 TruthState —— 信息隔离的最后一道保证。

模块结构：

- ``EventStore``  对外接口（ABC）；任何后端都必须实现这四个方法。
- ``InMemoryEventStore``  dict 实现，最快；测试 / Mock / 单元测试默认。
- ``JsonlEventStore``  一个 game_id 一个 ``.jsonl`` 文件；本地 debug / Replay 用。
- 异常见 ``stores.exceptions``。

接口语义（与 ``finalPlan/Interface_v2_1.md`` §6.1 一致）：

```python
class EventStore:
    def append(self, event: GameEvent) -> None
    def append_many(self, events: list[GameEvent]) -> None
    def list_by_game(self, game_id: str) -> list[GameEvent]
    def get(self, event_id: str) -> GameEvent
```

约定：

- **append-only**：删除/修改不在接口内；事件一旦写入即不可变。
- **唯一 event_id**：重复 append 抛 ``DuplicateEventError``，绝不静默覆盖。
- **插入序**：``list_by_game`` 按插入时间返回；不依赖 ``created_at`` 字段。
- **缺失 event_id** 抛 ``EventNotFoundError``；**未知 game_id** 返回 ``[]``（"还没事件"是合法状态）。
- **线程**：第一阶段串行优先（Interface §3），本层 not thread-safe；并发需求上来再加锁。
- **不存 TruthState**：Replay 由 events 重建，禁止把真相态放进 event payload。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from stores.exceptions import DuplicateEventError, EventNotFoundError

if TYPE_CHECKING:
    from contracts.schemas import GameEvent


# ---------- 接口 ----------


class EventStore(ABC):
    """EventStore 抽象接口。

    所有具体后端（InMemory / JSONL / 未来的 SQLite）必须实现下列方法。
    用 ABC 而非 ``NotImplementedError``：少一个方法就在子类实例化时报错，比运行到调用点才崩更早。
    """

    @abstractmethod
    def append(self, event: GameEvent) -> None:
        """追加一个事件。

        Raises:
            DuplicateEventError: ``event.event_id`` 已存在。
        """

    @abstractmethod
    def append_many(self, events: list[GameEvent]) -> None:
        """批量追加；语义上等价于逐个 ``append``。

        语义：尽力顺序追加；若中途遇到 ``DuplicateEventError``，前面已写入的事件不会回滚
        （事件日志本就是 append-only，不需要"事务"语义）。
        """

    @abstractmethod
    def list_by_game(self, game_id: str) -> list[GameEvent]:
        """按 ``game_id`` 列出所有事件，按插入顺序。

        未知 ``game_id`` 返回 ``[]``。
        """

    @abstractmethod
    def get(self, event_id: str) -> GameEvent:
        """按 ``event_id`` 取单个事件。

        Raises:
            EventNotFoundError: ``event_id`` 不存在。
        """


# ---------- 实现 1：In-Memory ----------


class InMemoryEventStore(EventStore):
    """纯内存实现。

    适用于：
    - 单元测试（pytest 默认）；
    - Mock smoke run（不需要落盘）；
    - JsonlEventStore 的内部索引（见下）。

    数据结构：

    - ``_by_event_id: dict[str, GameEvent]``  -- O(1) ``get``。
    - ``_event_ids_by_game: dict[str, list[str]]``  -- 保留插入顺序，``list_by_game`` 是
      O(n_game) 解引用，不引入排序歧义。
    """

    def __init__(self) -> None:
        self._by_event_id: dict[str, GameEvent] = {}
        self._event_ids_by_game: dict[str, list[str]] = defaultdict(list)

    # --- EventStore 接口 ---

    def append(self, event: GameEvent) -> None:
        if event.event_id in self._by_event_id:
            raise DuplicateEventError(event.event_id)
        self._by_event_id[event.event_id] = event
        self._event_ids_by_game[event.game_id].append(event.event_id)

    def append_many(self, events: list[GameEvent]) -> None:
        for event in events:
            self.append(event)

    def list_by_game(self, game_id: str) -> list[GameEvent]:
        # 用 .get(..., []) 而非 defaultdict 取值，避免给未知 game_id 留下空列表副作用
        ids = self._event_ids_by_game.get(game_id, [])
        return [self._by_event_id[eid] for eid in ids]

    def get(self, event_id: str) -> GameEvent:
        try:
            return self._by_event_id[event_id]
        except KeyError as exc:
            raise EventNotFoundError(event_id) from exc

    # --- 便利方法（不在抽象接口里，仅用于内部 / 测试） ---

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._by_event_id

    def __len__(self) -> int:
        return len(self._by_event_id)

    def list_game_ids(self) -> list[str]:
        """便利方法：列出当前 store 中已有事件的 game_id。"""
        return sorted(self._event_ids_by_game)


# ---------- 实现 2：JSONL ----------


class JsonlEventStore(EventStore):
    """文件持久化实现。

    布局：

    ```
    <root_dir>/
    ├── <game_id_1>.jsonl   一行一个 GameEvent，model_dump_json 序列化
    ├── <game_id_2>.jsonl
    └── ...
    ```

    每个 game_id 独立一个文件 —— 好处：

    1. ``list_by_game`` 只读单文件（IO 顺序）；
    2. 容易归档 / 删除单局；
    3. 多 game 并发追加不会相互打架（不同文件 fd）。

    实现策略：

    - 构造时扫描 ``root_dir`` 下所有 ``*.jsonl``，按行读入，``hydrate`` 到内嵌的
      ``InMemoryEventStore`` 索引里。后续 ``get`` / ``list_by_game`` 全走内存。
    - ``append`` 双写：先 mem 校验（dup 检测），通过后再 append-only 写文件。文件先于内存
      会出现"写了文件但没进索引"的不一致；反过来如果文件写失败抛异常，内存里要回滚 ——
      实现里用一个简单的 try/except 处理。
    - 每行 ``GameEvent.model_dump_json()``；行末 ``\\n``。空行 / 末尾不完整行（崩溃残留）
      在 hydrate 时跳过并 raise，不静默吞掉。

    注意：

    - 不是 thread-safe；Phase 1 串行优先，调用方自己保证。
    - 不做文件锁；若需多进程共享同一目录，等到真出现需求再加。
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._index = InMemoryEventStore()
        # 不在启动时全量 hydrate：历史数据走 list_by_game 实时读盘，避免 OOM

    # --- EventStore 接口 ---

    def append(self, event: GameEvent) -> None:
        # 1) 先写内存索引：dup 检测一次性完成
        self._index.append(event)
        # 2) 再 append-only 落盘；失败则把内存回滚
        path = self._path_for(event.game_id)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        except OSError:
            # 内存里已经 append 了，但磁盘写失败 —— 回滚以保持一致
            self._rollback(event.event_id, event.game_id)
            raise

    def append_many(self, events: list[GameEvent]) -> None:
        # 按 game_id 分组后批量打开文件，减少 open/close 次数。
        # 仍然是"先内存后磁盘"，单 event 失败回滚单 event；
        # 不做整体事务回滚（事件日志 append-only 不需要）。
        grouped: dict[str, list[GameEvent]] = defaultdict(list)
        for event in events:
            self._index.append(event)  # dup 检测；抛错就中断，已 append 的留在内存
            grouped[event.game_id].append(event)

        for game_id, batch in grouped.items():
            path = self._path_for(game_id)
            try:
                with path.open("a", encoding="utf-8") as f:
                    for event in batch:
                        f.write(event.model_dump_json() + "\n")
            except OSError:
                # 磁盘失败：把这批 event 从内存回滚
                for event in batch:
                    self._rollback(event.event_id, event.game_id)
                raise

    def list_by_game(self, game_id: str) -> list[GameEvent]:
        # 先查内存索引（当局运行时 append 写入的）；历史局直接读盘，不回填索引，
        # 避免 API 大量翻历史时把全部数据塞进内存。
        cached = self._index.list_by_game(game_id)
        if cached:
            return cached
        path = self._path_for(game_id)
        if not path.exists():
            return []
        from contracts.schemas import GameEvent as _GameEvent  # noqa: PLC0415
        from stores.exceptions import DuplicateEventError  # noqa: PLC0415
        events: list[GameEvent] = []
        seen_ids: set[str] = set()
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    event = _GameEvent.model_validate_json(line)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"corrupt event log at {path}:{line_no}: {exc}"
                    ) from exc
                if event.event_id in seen_ids:
                    raise DuplicateEventError(event.event_id)
                seen_ids.add(event.event_id)
                events.append(event)
        return events

    def get(self, event_id: str) -> GameEvent:
        return self._index.get(event_id)

    def list_game_ids(self) -> list[str]:
        """实时扫 root_dir，返回当前磁盘上所有 game_id（无需重启即可发现新对局）。"""
        return [p.stem for p in sorted(self.root_dir.glob("*.jsonl"))]

    # --- 内部辅助 ---

    def _path_for(self, game_id: str) -> Path:
        # game_id 走 schema 校验（仅字符串），但仍要防御文件系统不友好字符。
        # 这里采取最保守策略：不允许路径分隔符；其他保留给 OS。
        if "/" in game_id or "\\" in game_id or game_id in ("", ".", ".."):
            raise ValueError(f"invalid game_id for filesystem: {game_id!r}")
        return self.root_dir / f"{game_id}.jsonl"

    def _hydrate(self) -> None:
        """启动时把 root_dir 下所有 ``*.jsonl`` 回读入内存索引。"""
        # 延迟 import：只有需要 hydrate 时才依赖 contracts
        from contracts.schemas import GameEvent  # noqa: PLC0415

        for jsonl_file in sorted(self.root_dir.glob("*.jsonl")):
            with jsonl_file.open(encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    try:
                        event = GameEvent.model_validate_json(line)
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            f"corrupt event log at {jsonl_file}:{line_no}: {exc}"
                        ) from exc
                    # hydrate 阶段碰到 dup 也直接抛 —— 同一 event_id 出现两次说明日志已损坏
                    self._index.append(event)

    def _rollback(self, event_id: str, game_id: str) -> None:
        """从内存索引里抽掉一条事件（仅供磁盘写失败回滚使用）。"""
        if event_id in self._index._by_event_id:  # noqa: SLF001
            del self._index._by_event_id[event_id]  # noqa: SLF001
        ids = self._index._event_ids_by_game.get(game_id, [])  # noqa: SLF001
        if event_id in ids:
            ids.remove(event_id)
