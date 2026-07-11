"""TraceStore 测试（C / S1）。

覆盖：

- 抽象层 TraceStore 不可直接实例化
- InMemory / JSONL 两套后端共享同一接口契约（参数化）
- 接口语义：插入序、game/agent 隔离、唯一性、未知 id 行为
- JSONL 专属：持久化往返、按 game_id 分文件、hydrate、损坏日志硬失败、回滚
- AgentTuningTraceStore S10 占位：NotImplementedError
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from contracts.enums import Phase, Role
from contracts.schemas import AgentDecisionTrace, AgentTuningTrace
from stores.exceptions import (
    DuplicateTraceError,
    StoreError,
    TraceNotFoundError,
)
from stores.trace_store import (
    AgentTuningTraceStore,
    InMemoryTraceStore,
    JsonlTraceStore,
    TraceStore,
)


# ---------- 测试工具 ----------


def make_trace(
    trace_id: str,
    game_id: str = "g1",
    agent_id: str = "P1",
    round_: int = 1,
    phase: Phase = Phase.NIGHT_SEER,
    role: Role = Role.SEER,
    agent_version: str = "v0",
) -> AgentDecisionTrace:
    """构造一条最小合法 AgentDecisionTrace 用于测试。"""
    return AgentDecisionTrace(
        trace_id=trace_id,
        game_id=game_id,
        round=round_,
        phase=phase,
        agent_id=agent_id,
        role=role,
        agent_version=agent_version,
        input_summary={"recent": "snippet"},
        decision_output={"action_type": "check", "target": "P2"},
        decision_quality_flags={"fallback_used": False},
    )


StoreFactory = Callable[[], TraceStore]


def _mem_factory() -> TraceStore:
    return InMemoryTraceStore()


@pytest.fixture
def jsonl_factory(tmp_path: Path) -> StoreFactory:
    def factory() -> TraceStore:
        return JsonlTraceStore(tmp_path / "traces")

    return factory


# ---------- ABC 行为 ----------


class TestTraceStoreInterface:
    def test_trace_store_is_abstract(self):
        """TraceStore 本身不能直接实例化 —— 强制走子类。"""
        with pytest.raises(TypeError):
            TraceStore()  # type: ignore[abstract]

    def test_subclasses_must_implement_all_methods(self):
        """缺方法的子类同样不能实例化。"""

        class IncompleteStore(TraceStore):  # type: ignore[misc]
            def append(self, trace): ...  # 故意只实现一个

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
class TestTraceStoreContract:
    def test_append_and_get_single(self, both_stores):
        trace = make_trace("t1")
        both_stores.append(trace)

        fetched = both_stores.get("t1")
        assert fetched.trace_id == "t1"
        assert fetched.agent_id == "P1"
        assert fetched.role == Role.SEER

    def test_append_preserves_insertion_order_in_list_by_game(self, both_stores):
        t1 = make_trace("t1")
        t2 = make_trace("t2")
        t3 = make_trace("t3")
        both_stores.append(t1)
        both_stores.append(t2)
        both_stores.append(t3)

        traces = both_stores.list_by_game("g1")
        assert [t.trace_id for t in traces] == ["t1", "t2", "t3"]

    def test_list_by_game_isolates_games(self, both_stores):
        both_stores.append(make_trace("ta", game_id="ga"))
        both_stores.append(make_trace("tb", game_id="gb"))
        both_stores.append(make_trace("ta2", game_id="ga"))

        traces_a = both_stores.list_by_game("ga")
        traces_b = both_stores.list_by_game("gb")

        assert [t.trace_id for t in traces_a] == ["ta", "ta2"]
        assert [t.trace_id for t in traces_b] == ["tb"]

    def test_list_by_game_unknown_returns_empty(self, both_stores):
        assert both_stores.list_by_game("nonexistent_game") == []

    def test_list_by_agent_filters_by_game_and_agent(self, both_stores):
        both_stores.append(make_trace("t1", game_id="g1", agent_id="P1"))
        both_stores.append(make_trace("t2", game_id="g1", agent_id="P2"))
        both_stores.append(make_trace("t3", game_id="g1", agent_id="P1"))
        both_stores.append(make_trace("t4", game_id="g2", agent_id="P1"))

        p1_in_g1 = both_stores.list_by_agent("g1", "P1")
        p2_in_g1 = both_stores.list_by_agent("g1", "P2")
        p1_in_g2 = both_stores.list_by_agent("g2", "P1")

        assert [t.trace_id for t in p1_in_g1] == ["t1", "t3"]
        assert [t.trace_id for t in p2_in_g1] == ["t2"]
        assert [t.trace_id for t in p1_in_g2] == ["t4"]

    def test_list_by_agent_unknown_returns_empty(self, both_stores):
        both_stores.append(make_trace("t1", game_id="g1", agent_id="P1"))
        assert both_stores.list_by_agent("g1", "P99") == []
        assert both_stores.list_by_agent("g99", "P1") == []

    def test_duplicate_trace_id_raises(self, both_stores):
        both_stores.append(make_trace("t1"))
        with pytest.raises(DuplicateTraceError) as exc_info:
            both_stores.append(make_trace("t1"))
        assert exc_info.value.trace_id == "t1"

    def test_duplicate_error_is_store_error(self, both_stores):
        both_stores.append(make_trace("t1"))
        with pytest.raises(StoreError):
            both_stores.append(make_trace("t1"))

    def test_get_unknown_raises(self, both_stores):
        with pytest.raises(TraceNotFoundError) as exc_info:
            both_stores.get("missing_trace")
        assert exc_info.value.trace_id == "missing_trace"

    def test_not_found_error_is_store_error(self, both_stores):
        with pytest.raises(StoreError):
            both_stores.get("missing")

    def test_append_many_writes_all(self, both_stores):
        traces = [make_trace(f"t{i}") for i in range(5)]
        both_stores.append_many(traces)

        assert len(both_stores.list_by_game("g1")) == 5
        for i in range(5):
            assert both_stores.get(f"t{i}").trace_id == f"t{i}"

    def test_append_many_stops_on_duplicate(self, both_stores):
        both_stores.append(make_trace("t1"))
        traces = [make_trace("t2"), make_trace("t1"), make_trace("t3")]

        with pytest.raises(DuplicateTraceError):
            both_stores.append_many(traces)

        # t2 已写入；t3 未达
        assert "t2" in {t.trace_id for t in both_stores.list_by_game("g1")}
        with pytest.raises(TraceNotFoundError):
            both_stores.get("t3")

    def test_multi_game_multi_agent_isolation(self, both_stores):
        for game_id in ("g1", "g2"):
            for agent_id in ("P1", "P2"):
                tid = f"{game_id}_{agent_id}"
                both_stores.append(
                    make_trace(tid, game_id=game_id, agent_id=agent_id)
                )

        assert {t.trace_id for t in both_stores.list_by_game("g1")} == {"g1_P1", "g1_P2"}
        assert {t.trace_id for t in both_stores.list_by_game("g2")} == {"g2_P1", "g2_P2"}
        assert [t.trace_id for t in both_stores.list_by_agent("g1", "P1")] == ["g1_P1"]


# ---------- InMemory 专属 ----------


class TestInMemoryTraceStoreSpecific:
    def test_contains_returns_membership(self):
        store = InMemoryTraceStore()
        store.append(make_trace("t1"))
        assert "t1" in store
        assert "t99" not in store

    def test_len_counts_traces(self):
        store = InMemoryTraceStore()
        assert len(store) == 0
        store.append(make_trace("t1"))
        store.append(make_trace("t2"))
        assert len(store) == 2


# ---------- JSONL 专属 ----------


class TestJsonlTraceStore:
    def test_persists_to_one_file_per_game(self, tmp_path: Path):
        root = tmp_path / "traces"
        store = JsonlTraceStore(root)
        store.append(make_trace("t1", game_id="ga"))
        store.append(make_trace("t2", game_id="ga"))
        store.append(make_trace("t3", game_id="gb"))

        files = sorted(p.name for p in root.glob("*.jsonl"))
        assert files == ["ga.jsonl", "gb.jsonl"]

        ga_lines = (root / "ga.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(ga_lines) == 2
        # 每行必须是合法 JSON
        for line in ga_lines:
            json.loads(line)

    def test_list_by_game_works_after_reload(self, tmp_path: Path):
        root = tmp_path / "traces"
        s1 = JsonlTraceStore(root)
        s1.append(make_trace("t1", game_id="g1"))
        s1.append(make_trace("t2", game_id="g1"))
        s1.append(make_trace("t3", game_id="g2"))

        s2 = JsonlTraceStore(root)
        assert {t.trace_id for t in s2.list_by_game("g1")} == {"t1", "t2"}
        assert {t.trace_id for t in s2.list_by_game("g2")} == {"t3"}

    def test_hydrate_preserves_insertion_order(self, tmp_path: Path):
        root = tmp_path / "traces"
        s1 = JsonlTraceStore(root)
        for i in range(5):
            s1.append(make_trace(f"t{i}", game_id="g"))

        s2 = JsonlTraceStore(root)
        ids = [t.trace_id for t in s2.list_by_game("g")]
        assert ids == [f"t{i}" for i in range(5)]

    def test_hydrate_skips_blank_lines(self, tmp_path: Path):
        root = tmp_path / "traces"
        root.mkdir()
        path = root / "g.jsonl"
        s1 = JsonlTraceStore(root)
        s1.append(make_trace("t1", game_id="g"))
        # 手动加空行
        with path.open("a", encoding="utf-8") as f:
            f.write("\n\n")
        s1.append(make_trace("t2", game_id="g"))

        s2 = JsonlTraceStore(root)
        assert {t.trace_id for t in s2.list_by_game("g")} == {"t1", "t2"}

    def test_corrupt_log_raises_on_read(self, tmp_path: Path):
        root = tmp_path / "traces"
        root.mkdir()
        (root / "bad.jsonl").write_text("this is not json\n", encoding="utf-8")

        store = JsonlTraceStore(root)
        with pytest.raises(ValueError, match="corrupt trace log"):
            store.list_by_game("bad")

    def test_duplicate_trace_id_raises_on_read(self, tmp_path: Path):
        root = tmp_path / "traces"
        root.mkdir()
        trace = make_trace("dup", game_id="g")
        line = trace.model_dump_json() + "\n"
        (root / "g.jsonl").write_text(line + line, encoding="utf-8")

        store = JsonlTraceStore(root)
        with pytest.raises(DuplicateTraceError):
            store.list_by_game("g")

    def test_append_rollback_on_disk_failure(self, tmp_path: Path):
        root = tmp_path / "traces"
        store = JsonlTraceStore(root)
        trace = make_trace("t1", game_id="g")

        # 模拟 open().write() 失败
        def boom(*args, **kwargs):
            raise OSError("disk full")

        with patch.object(Path, "open", boom):
            with pytest.raises(OSError, match="disk full"):
                store.append(trace)

        # 内存索引也回滚了，可以重新 append
        store.append(trace)
        assert store.get("t1").trace_id == "t1"

    def test_append_many_rollback_on_disk_failure(self, tmp_path: Path):
        root = tmp_path / "traces"
        store = JsonlTraceStore(root)  # hydrate 已完成，后续 patch 不影响它
        traces = [make_trace(f"t{i}", game_id="g") for i in range(3)]

        def always_fail(*args, **kwargs):
            raise OSError("disk full")

        with patch.object(Path, "open", always_fail):
            with pytest.raises(OSError):
                store.append_many(traces)

        # 三条都不该进索引（append_many 在磁盘失败时整批回滚）
        for i in range(3):
            with pytest.raises(TraceNotFoundError):
                store.get(f"t{i}")

    def test_invalid_game_id_raises(self, tmp_path: Path):
        """append 接收非法 game_id 应抛 ValueError；逐次使用不同 trace_id
        以隔离每条记录的内存索引状态（与 EventStore 同款语义）。"""
        root = tmp_path / "traces"
        store = JsonlTraceStore(root)

        for i, bad in enumerate(("a/b", "a\\b", "", ".", "..")):
            with pytest.raises(ValueError, match="invalid game_id"):
                store.append(make_trace(f"unique_{i}", game_id=bad))


# ---------- AgentTuningTraceStore S10 占位 ----------


class TestAgentTuningTraceStorePlaceholder:
    def test_save_raises_not_implemented(self):
        store = AgentTuningTraceStore()
        trace = AgentTuningTrace(
            tuning_trace_id="tune_1",
            role=Role.SEER,
            from_prompt_version="v0",
            to_prompt_version="v1",
        )
        with pytest.raises(NotImplementedError):
            store.save(trace)

    def test_list_by_role_raises_not_implemented(self):
        store = AgentTuningTraceStore()
        with pytest.raises(NotImplementedError):
            store.list_by_role("seer")
