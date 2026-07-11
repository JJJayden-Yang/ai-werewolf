"""EventStore 测试（C5）。

覆盖：

- 抽象层  EventStore 不可直接实例化（强制 ABC 行为）
- InMemory / JSONL 两套后端共享同一接口契约（参数化）
- 接口语义：插入序、隔离、唯一性、未知 id 行为
- JSONL 专属：持久化往返、按 game_id 分文件、hydrate、损坏日志硬失败、回滚

测试命名包含规划里点名的 `test_event_store_append_and_list`（第一阶段分工_v2.1 §6.3）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from contracts.enums import EventType, Phase, Visibility
from contracts.schemas import GameEvent
from stores.event_store import EventStore, InMemoryEventStore, JsonlEventStore
from stores.exceptions import (
    DuplicateEventError,
    EventNotFoundError,
    StoreError,
)


# ---------- 测试工具 ----------


def make_event(
    event_id: str,
    game_id: str = "g1",
    round_: int = 1,
    phase: Phase = Phase.NIGHT_WEREWOLF,
    event_type: EventType = EventType.WOLF_NOMINATION,
    actor: str | None = None,
    target: str | None = None,
) -> GameEvent:
    """构造一个最小合法 GameEvent 用于测试。"""
    return GameEvent(
        event_id=event_id,
        game_id=game_id,
        round=round_,
        phase=phase,
        event_type=event_type,
        actor=actor,
        target=target,
        visibility=Visibility.PUBLIC,
    )


StoreFactory = Callable[[], EventStore]


def _mem_factory() -> EventStore:
    return InMemoryEventStore()


@pytest.fixture
def jsonl_factory(tmp_path: Path) -> StoreFactory:
    """每个测试一个全新空目录的 JsonlEventStore。"""
    def factory() -> EventStore:
        return JsonlEventStore(tmp_path / "events")
    return factory


# ---------- ABC 行为 ----------


class TestEventStoreInterface:
    def test_event_store_is_abstract(self):
        """EventStore 本身不能直接实例化 —— 强制走子类。"""
        with pytest.raises(TypeError):
            EventStore()  # type: ignore[abstract]

    def test_subclasses_must_implement_all_methods(self):
        """缺方法的子类同样不能实例化。"""

        class IncompleteStore(EventStore):  # type: ignore[misc]
            def append(self, event): ...  # 故意只实现一个

        with pytest.raises(TypeError):
            IncompleteStore()  # type: ignore[abstract]


# ---------- 跨实现的共享语义测试（参数化） ----------


@pytest.fixture
def both_stores(request, jsonl_factory):
    """跑两遍：一遍 InMemory，一遍 JSONL（同一份测试逻辑）。"""
    if request.param == "memory":
        return _mem_factory()
    return jsonl_factory()


@pytest.mark.parametrize("both_stores", ["memory", "jsonl"], indirect=True)
class TestEventStoreContract:
    """两个后端必须满足的接口契约。"""

    def test_event_store_append_and_list(self, both_stores):
        """规划里点名的最小测试：append + list_by_game 工作。"""
        store = both_stores
        e1 = make_event("e1", "g1")
        e2 = make_event("e2", "g1")
        store.append(e1)
        store.append(e2)

        events = store.list_by_game("g1")
        assert [e.event_id for e in events] == ["e1", "e2"]

    def test_list_preserves_insertion_order(self, both_stores):
        """list_by_game 严格保留插入顺序，不依赖 created_at。"""
        store = both_stores
        ids = [f"e{i}" for i in range(10)]
        for eid in ids:
            store.append(make_event(eid, "g1"))
        assert [e.event_id for e in store.list_by_game("g1")] == ids

    def test_list_unknown_game_returns_empty(self, both_stores):
        """未知 game_id 返回 []（"还没事件" 是合法状态，不报错）。"""
        store = both_stores
        assert store.list_by_game("never-seen") == []

    def test_get_by_event_id(self, both_stores):
        """get 能按 event_id 拿到原 event。"""
        store = both_stores
        e = make_event("e1", "g1", actor="P1", target="P2")
        store.append(e)
        got = store.get("e1")
        assert got.event_id == "e1"
        assert got.actor == "P1"
        assert got.target == "P2"

    def test_get_missing_raises_event_not_found(self, both_stores):
        store = both_stores
        with pytest.raises(EventNotFoundError) as exc:
            store.get("nope")
        assert exc.value.event_id == "nope"

    def test_event_not_found_is_subclass_of_store_error(self, both_stores):
        store = both_stores
        with pytest.raises(StoreError):
            store.get("nope")

    def test_isolates_events_across_games(self, both_stores):
        """game_id 严格隔离：a 局的事件不出现在 b 局的列表。"""
        store = both_stores
        store.append(make_event("a1", "ga"))
        store.append(make_event("b1", "gb"))
        store.append(make_event("a2", "ga"))

        assert [e.event_id for e in store.list_by_game("ga")] == ["a1", "a2"]
        assert [e.event_id for e in store.list_by_game("gb")] == ["b1"]

    def test_append_duplicate_event_id_raises(self, both_stores):
        """同 event_id 不能 append 两次 —— 上游 EventEmitter 有 bug 必须暴露。"""
        store = both_stores
        store.append(make_event("e1", "g1"))
        with pytest.raises(DuplicateEventError) as exc:
            store.append(make_event("e1", "g1"))
        assert exc.value.event_id == "e1"

    def test_duplicate_across_different_games_still_rejected(self, both_stores):
        """event_id 全局唯一，不允许同 id 出现在两个 game 里。"""
        store = both_stores
        store.append(make_event("e1", "ga"))
        with pytest.raises(DuplicateEventError):
            store.append(make_event("e1", "gb"))

    def test_append_many_batches_correctly(self, both_stores):
        store = both_stores
        store.append_many([
            make_event("e1", "g1"),
            make_event("e2", "g1"),
            make_event("e3", "g2"),
        ])
        assert [e.event_id for e in store.list_by_game("g1")] == ["e1", "e2"]
        assert [e.event_id for e in store.list_by_game("g2")] == ["e3"]

    def test_append_many_with_duplicate_aborts_at_duplicate(self, both_stores):
        """append_many 是逐条 append；遇到 dup 中断，前面已成功的保留（append-only 语义）。"""
        store = both_stores
        store.append(make_event("e1", "g1"))
        with pytest.raises(DuplicateEventError):
            store.append_many([
                make_event("e2", "g1"),
                make_event("e1", "g1"),  # dup
                make_event("e3", "g1"),  # 不会被写入
            ])
        # e2 已经写进去了；e3 没写
        ids = [e.event_id for e in store.list_by_game("g1")]
        assert "e2" in ids
        assert "e3" not in ids


# ---------- InMemory 专属 ----------


class TestInMemoryEventStore:
    def test_contains_and_len(self):
        """便利方法：__contains__ / __len__（仅测试用）。"""
        store = InMemoryEventStore()
        assert len(store) == 0
        assert "e1" not in store

        store.append(make_event("e1", "g1"))
        assert len(store) == 1
        assert "e1" in store
        assert "nope" not in store


# ---------- JSONL 专属 ----------


class TestJsonlEventStore:
    def test_one_file_per_game(self, tmp_path):
        """每个 game_id 在 root_dir 下独占一个 .jsonl 文件。"""
        root = tmp_path / "events"
        store = JsonlEventStore(root)
        store.append(make_event("a1", "ga"))
        store.append(make_event("b1", "gb"))
        store.append(make_event("a2", "ga"))

        files = sorted(p.name for p in root.glob("*.jsonl"))
        assert files == ["ga.jsonl", "gb.jsonl"]

    def test_root_dir_created_if_missing(self, tmp_path):
        """构造时自动 mkdir -p。"""
        root = tmp_path / "deep" / "nested" / "events"
        assert not root.exists()
        JsonlEventStore(root)
        assert root.exists() and root.is_dir()

    def test_persistence_across_instances(self, tmp_path):
        """关掉重开还能读回 —— 这是 JSONL 后端存在的意义。"""
        root = tmp_path / "events"
        s1 = JsonlEventStore(root)
        s1.append(make_event("e1", "g1", actor="P1"))
        s1.append(make_event("e2", "g1", actor="P2"))

        # 模拟进程重启
        del s1
        s2 = JsonlEventStore(root)
        events = s2.list_by_game("g1")
        assert [e.event_id for e in events] == ["e1", "e2"]
        assert [e.actor for e in events] == ["P1", "P2"]

    def test_persistence_preserves_enum_fields(self, tmp_path):
        """序列化往返不丢 enum 类型（Phase / EventType / Visibility）。"""
        root = tmp_path / "events"
        s1 = JsonlEventStore(root)
        s1.append(make_event(
            "e1", "g1",
            phase=Phase.DAY_VOTE,
            event_type=EventType.VOTE_CAST,
        ))
        del s1

        s2 = JsonlEventStore(root)
        events = s2.list_by_game("g1")
        assert len(events) == 1
        got = events[0]
        assert got.phase is Phase.DAY_VOTE
        assert got.event_type is EventType.VOTE_CAST
        assert got.visibility is Visibility.PUBLIC

    def test_list_by_game_works_after_reload(self, tmp_path):
        """重启后 list_by_game 从磁盘实时读取，无需 hydrate。"""
        root = tmp_path / "events"
        s1 = JsonlEventStore(root)
        s1.append(make_event("e1", "g1"))
        del s1

        s2 = JsonlEventStore(root)
        events = s2.list_by_game("g1")
        assert len(events) == 1
        assert events[0].event_id == "e1"

    def test_skips_blank_lines_during_hydrate(self, tmp_path):
        """容忍 JSONL 文件里的空行（手动编辑/拼接后常见）。"""
        root = tmp_path / "events"
        root.mkdir()
        s1 = JsonlEventStore(root)
        s1.append(make_event("e1", "g1"))
        del s1

        # 在 jsonl 文件末尾追加空行
        jsonl = root / "g1.jsonl"
        with jsonl.open("a", encoding="utf-8") as f:
            f.write("\n\n")

        s2 = JsonlEventStore(root)
        assert len(s2.list_by_game("g1")) == 1

    def test_corrupt_line_raises_on_read(self, tmp_path):
        """损坏的行不能静默吞掉 —— list_by_game 读盘时立即抛 ValueError。"""
        root = tmp_path / "events"
        root.mkdir()
        jsonl = root / "g1.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            f.write("not a json line\n")

        store = JsonlEventStore(root)
        with pytest.raises(ValueError, match="corrupt event log"):
            store.list_by_game("g1")

    def test_duplicate_event_id_in_file_raises_on_read(self, tmp_path):
        """同一个文件里出现重复 event_id，list_by_game 时立即抛错。"""
        root = tmp_path / "events"
        root.mkdir()
        good = make_event("e1", "g1")
        jsonl = root / "g1.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            f.write(good.model_dump_json() + "\n")
            f.write(good.model_dump_json() + "\n")  # 同 event_id 重复

        store = JsonlEventStore(root)
        with pytest.raises(DuplicateEventError):
            store.list_by_game("g1")

    def test_hydrate_loads_files_deterministically(self, tmp_path):
        """多文件 hydrate 按文件名排序进 —— 不同测试运行结果稳定。"""
        root = tmp_path / "events"
        root.mkdir()
        ga = JsonlEventStore(root)
        ga.append(make_event("a1", "ga"))
        ga.append(make_event("b1", "gb"))
        del ga

        reloaded = JsonlEventStore(root)
        # 各 game 内部按插入序
        assert [e.event_id for e in reloaded.list_by_game("ga")] == ["a1"]
        assert [e.event_id for e in reloaded.list_by_game("gb")] == ["b1"]

    @pytest.mark.parametrize("bad_id", ["", ".", "..", "a/b", "a\\b"])
    def test_rejects_unsafe_game_id_for_filename(self, tmp_path, bad_id):
        """不允许把路径分隔符 / 特殊段当 game_id（文件系统注入防御）。"""
        store = JsonlEventStore(tmp_path / "events")
        with pytest.raises(ValueError, match="invalid game_id"):
            store.append(make_event("e1", bad_id))

    def test_append_failure_rolls_back_memory(self, tmp_path, monkeypatch):
        """模拟磁盘写失败：内存索引必须回滚，store 状态保持一致。"""
        root = tmp_path / "events"
        store = JsonlEventStore(root)

        # 让下一次 open() 失败
        real_open = Path.open

        def fake_open(self, *args, **kwargs):
            if self.name.endswith(".jsonl") and "a" in (args[0] if args else kwargs.get("mode", "")):
                raise OSError("disk full simulation")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)

        with pytest.raises(OSError, match="disk full"):
            store.append(make_event("e1", "g1"))

        # 关键：内存里不该残留 e1（回滚成功）
        with pytest.raises(EventNotFoundError):
            store.get("e1")
        assert store.list_by_game("g1") == []
