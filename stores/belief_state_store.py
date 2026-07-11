"""BeliefStateStore —— Task C6。BeliefState 的持久化层。

调用方（见 ``finalPlan/Interface_v2_1.md`` §6.2）：

| 方法 | 使用方 |
|---|---|
| get          | ContextAssembler / RealtimeBeliefUpdater |
| save         | RealtimeBeliefUpdater |
| get_history  | Belief Curve / PostGameAnalyzer |

模块结构（与 ``stores.event_store`` 对齐）：

- ``BeliefStateStore``  对外接口（ABC）；任何后端都必须实现三个方法。
- ``InMemoryBeliefStateStore``  dict 实现，最快；测试 / Mock / 单元测试默认。
- ``JsonlBeliefStateStore``  按 ``<game_id>/<agent_id>/<lane>.jsonl`` 分文件；本地 debug /
  Belief Curve 回放用。
- 异常见 ``stores.exceptions``。

接口语义（与 ``Interface_v2_1.md`` §6.2 一致）：

```python
class BeliefStateStore:
    def get(self, game_id: str, agent_id: str) -> BeliefState
    def save(self, belief_state: BeliefState) -> None
    def get_history(self, game_id: str, agent_id: str) -> list[BeliefState]
```

红线 / 约定：

- **append-only history**：每次 ``save`` 都进 history；不会原地修改既往 BeliefState。
  ``get`` 返回该 lane 最新一次 save 的快照。
- **shadow / real 分 lane**：BeliefState 自带 ``is_shadow`` 字段。
  shadow 在 v0 系统后台维护，不注入 AgentContext，仅供 PostGameAnalyzer 做 deviation 对比；
  real 给 ContextAssembler。两者各有独立 history，互不污染。
- **接口扩展**：spec 的 ``get`` / ``get_history`` 签名只有 ``(game_id, agent_id)``；
  本类加了 keyword-only 的 ``is_shadow`` 默认 ``False`` —— 严格按 spec 调用的代码
  （ContextAssembler 的常规路径）行为不变；只有需要读 shadow lane 的 PostGameAnalyzer 显式传参。
- **clamp / normalize**：写入前已由 ``RealtimeBeliefUpdater``（B）做完，本类只负责存。
- **缺失 (game_id, agent_id, lane)** ``get`` 抛 ``BeliefStateNotFoundError``；
  ``get_history`` 返回 ``[]``（与 EventStore 的"未知 game_id 返回 []"对齐）。
- **不存 TruthState**：BeliefState 是 agent 的主观信念，不存任何真相态。
- **线程**：第一阶段串行优先（与 EventStore 同），not thread-safe。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from stores.exceptions import BeliefStateNotFoundError

if TYPE_CHECKING:
    from contracts.schemas import BeliefState


# 文件系统对外暴露的子目录文件名 —— 不允许 agent_id 取这两个保留名，
# 否则会与 lane 文件夹/文件层级混淆。JsonlBeliefStateStore 里会做检查。
_REAL_LANE_FILENAME = "real.jsonl"
_SHADOW_LANE_FILENAME = "shadow.jsonl"


def _lane_filename(is_shadow: bool) -> str:
    return _SHADOW_LANE_FILENAME if is_shadow else _REAL_LANE_FILENAME


# ---------- 接口 ----------


class BeliefStateStore(ABC):
    """BeliefStateStore 抽象接口。

    所有具体后端必须实现 ``get`` / ``save`` / ``get_history``。
    用 ABC 而非 ``NotImplementedError``：少一个方法就在子类实例化时报错，比运行到调用点才崩更早。
    """

    @abstractmethod
    def get(
        self,
        game_id: str,
        agent_id: str,
        *,
        is_shadow: bool = False,
    ) -> BeliefState:
        """取该 (game_id, agent_id, lane) 下最新一次 ``save`` 写入的 BeliefState。

        默认 ``is_shadow=False`` 即"真实 belief"（供 ContextAssembler）；
        ``is_shadow=True`` 取 shadow lane（供 PostGameAnalyzer）。

        Raises:
            BeliefStateNotFoundError: 该 lane 下还没人 save 过。
        """

    @abstractmethod
    def save(self, belief_state: BeliefState) -> None:
        """追加一次 BeliefState 快照到对应 lane 的 history。

        lane 由 ``belief_state.is_shadow`` 决定，调用方无需额外指定。
        ``save`` 不去重、不合并；同 (game_id, agent_id, lane) 多次写入按写入顺序累积。
        """

    @abstractmethod
    def get_history(
        self,
        game_id: str,
        agent_id: str,
        *,
        is_shadow: bool = False,
    ) -> list[BeliefState]:
        """按写入顺序返回该 lane 下的全部 BeliefState 快照。

        未知 (game_id, agent_id, lane) 返回 ``[]``。
        """


# ---------- 实现 1：In-Memory ----------


class InMemoryBeliefStateStore(BeliefStateStore):
    """纯内存实现。

    数据结构：

    - ``_history: dict[tuple[game_id, agent_id, is_shadow], list[BeliefState]]``
      —— 每个 lane 一段独立的有序 history；``get`` 取末位，``get_history`` 直接返回拷贝。

    返回拷贝（list(...)）防止外部 mutate 反过来污染内部状态；
    BeliefState 本身是 pydantic model，没有冻结但常规使用不会被改。
    """

    def __init__(self) -> None:
        self._history: dict[tuple[str, str, bool], list[BeliefState]] = defaultdict(list)

    # --- BeliefStateStore 接口 ---

    def get(
        self,
        game_id: str,
        agent_id: str,
        *,
        is_shadow: bool = False,
    ) -> BeliefState:
        history = self._history.get((game_id, agent_id, is_shadow))
        if not history:
            raise BeliefStateNotFoundError(game_id, agent_id, is_shadow=is_shadow)
        return history[-1]

    def save(self, belief_state: BeliefState) -> None:
        key = (belief_state.game_id, belief_state.agent_id, belief_state.is_shadow)
        self._history[key].append(belief_state)

    def get_history(
        self,
        game_id: str,
        agent_id: str,
        *,
        is_shadow: bool = False,
    ) -> list[BeliefState]:
        # 拷贝出去 —— 外部 append 不应影响内部 history
        return list(self._history.get((game_id, agent_id, is_shadow), []))

    # --- 便利方法（不在抽象接口里，仅用于测试 / 内部 fast-path） ---

    def __len__(self) -> int:
        """所有 lane 累计的 save 次数（不是 lane 数量）。"""
        return sum(len(h) for h in self._history.values())


# ---------- 实现 2：JSONL ----------


class JsonlBeliefStateStore(BeliefStateStore):
    """文件持久化实现。

    布局：

    ```
    <root_dir>/
    ├── <game_id_1>/
    │   ├── <agent_id_A>/
    │   │   ├── real.jsonl      一行一个 BeliefState（is_shadow=False），按写入序追加
    │   │   └── shadow.jsonl    is_shadow=True 单独走这条 lane
    │   └── <agent_id_B>/
    │       └── real.jsonl
    └── <game_id_2>/
        └── ...
    ```

    选择两级目录的理由：

    1. ``get_history`` 只读单文件（IO 顺序，append-only 不需要 seek）。
    2. game/agent/lane 三元组天然映射到路径，**绝不会出现 (g, "a__shadow", real)
       与 (g, "a", shadow) 同名碰撞**（如果用扁平 ``g__a__lane.jsonl`` 会撞）。
    3. 删某一 game 或某一 agent 只要 ``rm -rf`` 子目录，运维友好。

    实现策略（沿用 ``JsonlEventStore`` 的成熟模式）：

    - 构造时扫描 ``root_dir`` 下 ``<game_id>/<agent_id>/{real,shadow}.jsonl``，
      hydrate 到内嵌的 ``InMemoryBeliefStateStore`` 索引里；后续读全走内存。
    - ``save`` 双写：先内存索引、再 append-only 写文件；磁盘失败回滚内存。
    - 每行 ``BeliefState.model_dump_json()``；空行 / 损坏行在 hydrate 时**立即抛错**，
      不静默吞掉（防止悄无声息地丢 belief）。

    注意：

    - 不是 thread-safe；Phase 1 串行优先（同 EventStore）。
    - 不做文件锁；多进程共享同一目录的需求出现时再加。
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._index = InMemoryBeliefStateStore()
        # 不在启动时全量 hydrate：历史数据走 get_history 实时读盘，避免 OOM

    # --- BeliefStateStore 接口 ---

    def get(
        self,
        game_id: str,
        agent_id: str,
        *,
        is_shadow: bool = False,
    ) -> BeliefState:
        # 先查内存（当前对局运行时写入的）；没有则从磁盘取最新一条
        try:
            return self._index.get(game_id, agent_id, is_shadow=is_shadow)
        except Exception:
            pass
        history = self.get_history(game_id, agent_id, is_shadow=is_shadow)
        if not history:
            from stores.exceptions import BeliefStateNotFoundError  # noqa: PLC0415
            raise BeliefStateNotFoundError(game_id, agent_id, is_shadow=is_shadow)
        return history[-1]

    def save(self, belief_state: BeliefState) -> None:
        # 1) 先算路径（顺手校验 game_id / agent_id 文件系统安全）—— 非法 id 不会污染内存索引。
        path = self._path_for(
            belief_state.game_id,
            belief_state.agent_id,
            belief_state.is_shadow,
        )
        # 2) 再写内存索引
        self._index.save(belief_state)
        # 3) 最后 append-only 落盘；失败则回滚内存
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(belief_state.model_dump_json() + "\n")
        except OSError:
            self._rollback(belief_state)
            raise

    def get_history(
        self,
        game_id: str,
        agent_id: str,
        *,
        is_shadow: bool = False,
    ) -> list[BeliefState]:
        # get_history 只服务审计 / Belief Curve / PostGameAnalyzer，不在热路径上。
        # 批跑进程在后台直接写盘，backend 启动后构造的 _index 里没有这些对局。
        # 每次调用实时读盘（同 EventStore.list_game_ids / TraceStore.list_by_game
        # 的"实时读盘"修复），保证跨进程新增对局对审计页立刻可见。
        from contracts.schemas import BeliefState  # noqa: PLC0415

        try:
            path = self._path_for(game_id, agent_id, is_shadow)
        except ValueError:
            # 非法 / 文件系统不安全的 id —— 文档约定"未知 lane 返回 []"
            return []

        if not path.exists():
            return []

        result: list[BeliefState] = []
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    result.append(BeliefState.model_validate_json(line))
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"corrupt belief log at {path}:{line_no}: {exc}"
                    ) from exc
        return result

    def __len__(self) -> int:
        """所有 lane 累计的 save 次数（与 InMemoryBeliefStateStore 对齐）。"""
        return len(self._index)

    # --- 内部辅助 ---

    @staticmethod
    def _validate_id_for_path(value: str, *, label: str) -> None:
        # game_id / agent_id 走 schema 校验只确认是 str；落到文件系统再额外防一道。
        # 同 EventStore 的策略：不允许路径分隔符 / 保留段，其他给 OS 自己拒。
        if "/" in value or "\\" in value or value in ("", ".", ".."):
            raise ValueError(f"invalid {label} for filesystem: {value!r}")

    def _path_for(self, game_id: str, agent_id: str, is_shadow: bool) -> Path:
        self._validate_id_for_path(game_id, label="game_id")
        self._validate_id_for_path(agent_id, label="agent_id")
        return self.root_dir / game_id / agent_id / _lane_filename(is_shadow)

    def _hydrate(self) -> None:
        """启动时回读所有 ``<game_id>/<agent_id>/{real,shadow}.jsonl`` 到内存索引。"""
        # 延迟 import，只在 hydrate 时才依赖 contracts
        from contracts.schemas import BeliefState  # noqa: PLC0415

        # 按 game / agent / lane 排序，保证多机/多次启动顺序一致，便于排错。
        for game_dir in sorted(p for p in self.root_dir.iterdir() if p.is_dir()):
            for agent_dir in sorted(p for p in game_dir.iterdir() if p.is_dir()):
                for jsonl_file in sorted(agent_dir.glob("*.jsonl")):
                    if jsonl_file.name not in (_REAL_LANE_FILENAME, _SHADOW_LANE_FILENAME):
                        # 容忍但忽略 —— 不在我们写入的 lane 文件名里的 .jsonl 可能是手动放进去的
                        # 调试数据，不当作 belief lane 解读。
                        continue
                    with jsonl_file.open(encoding="utf-8") as f:
                        for line_no, line in enumerate(f, start=1):
                            if not line.strip():
                                continue
                            try:
                                bs = BeliefState.model_validate_json(line)
                            except (ValueError, json.JSONDecodeError) as exc:
                                raise ValueError(
                                    f"corrupt belief log at {jsonl_file}:{line_no}: {exc}"
                                ) from exc
                            self._index.save(bs)

    def _rollback(self, belief_state: BeliefState) -> None:
        """从内存索引里抽掉最后一条（仅供磁盘写失败回滚使用）。

        因为 ``save`` 末尾才落盘，磁盘失败时这条 BeliefState 一定是该 lane 的尾部，
        ``pop()`` 之即可。"""
        key = (belief_state.game_id, belief_state.agent_id, belief_state.is_shadow)
        history = self._index._history.get(key)  # noqa: SLF001
        if history:
            history.pop()
            if not history:
                # 删空 list 顺手把 key 也清掉，避免假"有历史"残留
                del self._index._history[key]  # noqa: SLF001
