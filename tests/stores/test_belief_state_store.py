"""BeliefStateStore 测试（C6）。

覆盖：

- 抽象层  BeliefStateStore 不可直接实例化（强制 ABC 行为）
- InMemory / JSONL 两套后端共享同一接口契约（参数化）
- 接口语义：save→get、history 写入序、shadow 与 real lane 隔离、未知 lane 行为
- JSONL 专属：两级目录布局、persistence 往返、enum 保留、hydrate 错误处理、回滚

测试命名包含 spec 里点名的 `test_belief_state_store_get_save_get_history`
（``finalPlan/Interface_v2_1.md`` §11 Day-1 Demo 验收点）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from contracts.enums import Phase
from contracts.schemas import BeliefState, RoleBelief
from stores.belief_state_store import (
    BeliefStateStore,
    InMemoryBeliefStateStore,
    JsonlBeliefStateStore,
)
from stores.exceptions import BeliefStateNotFoundError, StoreError


# ---------- 测试工具 ----------


def make_belief(
    game_id: str = "g1",
    agent_id: str = "P1",
    round_: int | None = 1,
    phase: Phase | None = Phase.DAY_DISCUSSION,
    is_shadow: bool = False,
    *,
    wolf_prob: float = 0.0,
    seer_prob: float = 0.0,
    last_updated_event_id: str | None = None,
) -> BeliefState:
    """构造一个最小合法 BeliefState 用于测试。

    beliefs 只放一条目标玩家的 RoleBelief，方便断言；
    实际系统里 beliefs 会包含所有"其他玩家"的概率分布，不在 store 关心范围内。
    """
    return BeliefState(
        game_id=game_id,
        agent_id=agent_id,
        round=round_,
        phase=phase,
        is_shadow=is_shadow,
        beliefs={
            "P2": RoleBelief(werewolf=wolf_prob, seer=seer_prob),
        },
        last_updated_event_id=last_updated_event_id,
    )


StoreFactory = Callable[[], BeliefStateStore]


def _mem_factory() -> BeliefStateStore:
    return InMemoryBeliefStateStore()


@pytest.fixture
def jsonl_factory(tmp_path: Path) -> StoreFactory:
    """每个测试一个全新空目录的 JsonlBeliefStateStore。"""
    def factory() -> BeliefStateStore:
        return JsonlBeliefStateStore(tmp_path / "beliefs")
    return factory


# ---------- ABC 行为 ----------


class TestBeliefStateStoreInterface:
    def test_belief_state_store_is_abstract(self):
        """BeliefStateStore 本身不能直接实例化 —— 强制走子类。"""
        with pytest.raises(TypeError):
            BeliefStateStore()  # type: ignore[abstract]

    def test_subclasses_must_implement_all_methods(self):
        """缺方法的子类同样不能实例化。"""

        class IncompleteStore(BeliefStateStore):  # type: ignore[misc]
            def save(self, belief_state): ...  # 故意只实现一个

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
class TestBeliefStateStoreContract:
    """两个后端必须满足的接口契约。"""

    def test_belief_state_store_get_save_get_history(self, both_stores):
        """规划里点名的最小测试：save → get / get_history 工作。"""
        store = both_stores
        bs = make_belief(wolf_prob=0.7)
        store.save(bs)

        got = store.get("g1", "P1")
        assert got.game_id == "g1"
        assert got.agent_id == "P1"
        assert got.beliefs["P2"].werewolf == pytest.approx(0.7)

        hist = store.get_history("g1", "P1")
        assert len(hist) == 1
        assert hist[0].beliefs["P2"].werewolf == pytest.approx(0.7)

    def test_get_unknown_raises_belief_state_not_found(self, both_stores):
        store = both_stores
        with pytest.raises(BeliefStateNotFoundError) as exc:
            store.get("g1", "P1")
        assert exc.value.game_id == "g1"
        assert exc.value.agent_id == "P1"
        assert exc.value.is_shadow is False

    def test_belief_state_not_found_is_subclass_of_store_error(self, both_stores):
        store = both_stores
        with pytest.raises(StoreError):
            store.get("g1", "P1")

    def test_get_history_unknown_returns_empty(self, both_stores):
        """未知 (game_id, agent_id) 的历史返回 []（"还没存过"是合法状态）。"""
        store = both_stores
        assert store.get_history("never-seen", "never") == []

    def test_history_preserves_insertion_order(self, both_stores):
        """每次 save 都进 history，按写入顺序排列；不依赖任何字段排序。"""
        store = both_stores
        for r in range(1, 6):
            store.save(make_belief(round_=r, wolf_prob=r * 0.1))

        hist = store.get_history("g1", "P1")
        assert [bs.round for bs in hist] == [1, 2, 3, 4, 5]
        assert [bs.beliefs["P2"].werewolf for bs in hist] == pytest.approx(
            [0.1, 0.2, 0.3, 0.4, 0.5]
        )

    def test_get_returns_latest_save(self, both_stores):
        """get 取该 lane 最新一次 save 的 BeliefState。"""
        store = both_stores
        store.save(make_belief(round_=1, wolf_prob=0.1))
        store.save(make_belief(round_=2, wolf_prob=0.5))
        store.save(make_belief(round_=3, wolf_prob=0.9))

        latest = store.get("g1", "P1")
        assert latest.round == 3
        assert latest.beliefs["P2"].werewolf == pytest.approx(0.9)

    def test_isolates_across_games(self, both_stores):
        """game_id 隔离：a 局的 belief 不出现在 b 局的查询里。"""
        store = both_stores
        store.save(make_belief(game_id="ga", wolf_prob=0.3))
        store.save(make_belief(game_id="gb", wolf_prob=0.8))

        assert store.get("ga", "P1").beliefs["P2"].werewolf == pytest.approx(0.3)
        assert store.get("gb", "P1").beliefs["P2"].werewolf == pytest.approx(0.8)
        assert len(store.get_history("ga", "P1")) == 1
        assert len(store.get_history("gb", "P1")) == 1

    def test_isolates_across_agents(self, both_stores):
        """agent_id 隔离：P1 的 belief 不影响 P2 的查询。"""
        store = both_stores
        store.save(make_belief(agent_id="P1", wolf_prob=0.2))
        store.save(make_belief(agent_id="P2", wolf_prob=0.6))

        assert store.get("g1", "P1").beliefs["P2"].werewolf == pytest.approx(0.2)
        assert store.get("g1", "P2").beliefs["P2"].werewolf == pytest.approx(0.6)

    def test_shadow_and_real_lanes_isolated(self, both_stores):
        """shadow 与 real 是独立 lane；save shadow 不影响 real，反之亦然。"""
        store = both_stores
        store.save(make_belief(is_shadow=False, wolf_prob=0.1))
        store.save(make_belief(is_shadow=True, wolf_prob=0.9))

        real = store.get("g1", "P1", is_shadow=False)
        shadow = store.get("g1", "P1", is_shadow=True)

        assert real.is_shadow is False
        assert real.beliefs["P2"].werewolf == pytest.approx(0.1)
        assert shadow.is_shadow is True
        assert shadow.beliefs["P2"].werewolf == pytest.approx(0.9)

    def test_get_default_lane_is_real(self, both_stores):
        """get 不传 is_shadow 默认拿 real lane（严格符合 spec 签名）。"""
        store = both_stores
        store.save(make_belief(is_shadow=False, wolf_prob=0.2))
        store.save(make_belief(is_shadow=True, wolf_prob=0.8))

        # 不传 is_shadow → 拿 real
        got = store.get("g1", "P1")
        assert got.is_shadow is False
        assert got.beliefs["P2"].werewolf == pytest.approx(0.2)

    def test_get_history_lane_isolated(self, both_stores):
        """get_history 同样按 lane 分；shadow history 里没有 real 条目。"""
        store = both_stores
        store.save(make_belief(round_=1, is_shadow=False))
        store.save(make_belief(round_=1, is_shadow=True))
        store.save(make_belief(round_=2, is_shadow=False))

        assert [bs.round for bs in store.get_history("g1", "P1")] == [1, 2]
        assert [bs.round for bs in store.get_history(
            "g1", "P1", is_shadow=True
        )] == [1]

    def test_get_missing_shadow_raises_with_lane_info(self, both_stores):
        """只 save 了 real，get shadow 也要抛错且 lane 标记正确。"""
        store = both_stores
        store.save(make_belief(is_shadow=False))

        with pytest.raises(BeliefStateNotFoundError) as exc:
            store.get("g1", "P1", is_shadow=True)
        assert exc.value.is_shadow is True

    def test_save_routes_by_belief_state_is_shadow(self, both_stores):
        """save 不接 is_shadow 参数 —— 路由完全靠 belief_state.is_shadow 字段。"""
        store = both_stores
        store.save(make_belief(is_shadow=True))

        # real lane 应该是空的
        with pytest.raises(BeliefStateNotFoundError):
            store.get("g1", "P1", is_shadow=False)
        # shadow lane 拿到
        assert store.get("g1", "P1", is_shadow=True) is not None


# ---------- InMemory 专属 ----------


class TestInMemoryBeliefStateStore:
    def test_len_counts_all_saves_across_lanes(self):
        """便利方法 __len__：累计所有 lane 的 save 次数。"""
        store = InMemoryBeliefStateStore()
        assert len(store) == 0

        store.save(make_belief(round_=1))
        store.save(make_belief(round_=2))
        store.save(make_belief(is_shadow=True))
        assert len(store) == 3

    def test_get_history_returns_copy_not_internal_list(self):
        """外部 mutate 返回的 list 不能影响 store 内部状态。"""
        store = InMemoryBeliefStateStore()
        store.save(make_belief(round_=1))
        store.save(make_belief(round_=2))

        hist = store.get_history("g1", "P1")
        hist.clear()  # 外部清空

        # store 内部历史不受影响
        assert len(store.get_history("g1", "P1")) == 2


# ---------- JSONL 专属 ----------


class TestJsonlBeliefStateStore:
    def test_two_level_dir_layout(self, tmp_path):
        """每个 (game_id, agent_id, lane) 落到 <root>/<game>/<agent>/<lane>.jsonl。"""
        root = tmp_path / "beliefs"
        store = JsonlBeliefStateStore(root)
        store.save(make_belief(game_id="ga", agent_id="P1", is_shadow=False))
        store.save(make_belief(game_id="ga", agent_id="P1", is_shadow=True))
        store.save(make_belief(game_id="ga", agent_id="P2", is_shadow=False))
        store.save(make_belief(game_id="gb", agent_id="P1", is_shadow=False))

        assert (root / "ga" / "P1" / "real.jsonl").exists()
        assert (root / "ga" / "P1" / "shadow.jsonl").exists()
        assert (root / "ga" / "P2" / "real.jsonl").exists()
        assert (root / "gb" / "P1" / "real.jsonl").exists()
        # 没写过的 lane 不会出现
        assert not (root / "ga" / "P2" / "shadow.jsonl").exists()
        assert not (root / "gb" / "P1" / "shadow.jsonl").exists()

    def test_root_dir_created_if_missing(self, tmp_path):
        """构造时自动 mkdir -p。"""
        root = tmp_path / "deep" / "nested" / "beliefs"
        assert not root.exists()
        JsonlBeliefStateStore(root)
        assert root.exists() and root.is_dir()

    def test_persistence_across_instances(self, tmp_path):
        """关掉重开还能读回 —— JSONL 后端存在的意义。"""
        root = tmp_path / "beliefs"
        s1 = JsonlBeliefStateStore(root)
        s1.save(make_belief(round_=1, wolf_prob=0.1, last_updated_event_id="e1"))
        s1.save(make_belief(round_=2, wolf_prob=0.5, last_updated_event_id="e2"))
        s1.save(make_belief(round_=1, is_shadow=True, wolf_prob=0.9))

        del s1
        s2 = JsonlBeliefStateStore(root)

        real_hist = s2.get_history("g1", "P1")
        assert [bs.round for bs in real_hist] == [1, 2]
        assert real_hist[0].last_updated_event_id == "e1"
        assert real_hist[1].beliefs["P2"].werewolf == pytest.approx(0.5)

        shadow_hist = s2.get_history("g1", "P1", is_shadow=True)
        assert len(shadow_hist) == 1
        assert shadow_hist[0].beliefs["P2"].werewolf == pytest.approx(0.9)

    def test_persistence_preserves_enum_fields(self, tmp_path):
        """序列化往返不丢 enum 类型（Phase）。"""
        root = tmp_path / "beliefs"
        s1 = JsonlBeliefStateStore(root)
        s1.save(make_belief(phase=Phase.NIGHT_WEREWOLF))
        del s1

        s2 = JsonlBeliefStateStore(root)
        got = s2.get("g1", "P1")
        assert got.phase is Phase.NIGHT_WEREWOLF

    def test_persistence_get_works_after_reload(self, tmp_path):
        """重启后 get(...) 也能查得到最新一条。"""
        root = tmp_path / "beliefs"
        s1 = JsonlBeliefStateStore(root)
        s1.save(make_belief(round_=1))
        s1.save(make_belief(round_=2))
        del s1

        s2 = JsonlBeliefStateStore(root)
        assert s2.get("g1", "P1").round == 2

    def test_skips_blank_lines_during_hydrate(self, tmp_path):
        """容忍 .jsonl 文件里的空行（手动编辑后常见）。"""
        root = tmp_path / "beliefs"
        s1 = JsonlBeliefStateStore(root)
        s1.save(make_belief(round_=1))
        del s1

        jsonl = root / "g1" / "P1" / "real.jsonl"
        with jsonl.open("a", encoding="utf-8") as f:
            f.write("\n\n")

        s2 = JsonlBeliefStateStore(root)
        assert len(s2.get_history("g1", "P1")) == 1

    def test_corrupt_line_raises_on_read(self, tmp_path):
        """损坏的 JSON 行不能静默吞掉 —— get_history 读盘时立即抛 ValueError。"""
        root = tmp_path / "beliefs"
        (root / "g1" / "P1").mkdir(parents=True)
        with (root / "g1" / "P1" / "real.jsonl").open("w", encoding="utf-8") as f:
            f.write("not a json line\n")

        store = JsonlBeliefStateStore(root)
        with pytest.raises(ValueError, match="corrupt belief log"):
            store.get_history("g1", "P1")

    def test_hydrate_loads_deterministically(self, tmp_path):
        """多 game / 多 agent / 多 lane 的 hydrate 按 sorted() 顺序进，结果稳定。"""
        root = tmp_path / "beliefs"
        s1 = JsonlBeliefStateStore(root)
        s1.save(make_belief(game_id="gb", agent_id="P1"))
        s1.save(make_belief(game_id="ga", agent_id="P2"))
        s1.save(make_belief(game_id="ga", agent_id="P1"))
        del s1

        reloaded = JsonlBeliefStateStore(root)
        assert reloaded.get("ga", "P1") is not None
        assert reloaded.get("ga", "P2") is not None
        assert reloaded.get("gb", "P1") is not None

    def test_hydrate_ignores_unknown_jsonl_files_in_agent_dir(self, tmp_path):
        """非 ``real.jsonl`` / ``shadow.jsonl`` 的 .jsonl 文件被忽略，不参与 hydrate。

        允许调试时往子目录里塞临时数据；不在我们写入的 lane 文件名里就当不存在。
        """
        root = tmp_path / "beliefs"
        s1 = JsonlBeliefStateStore(root)
        s1.save(make_belief(round_=1))
        del s1

        # 在 agent dir 塞一个随便命名的 jsonl —— 应该被 hydrate 忽略
        extra = root / "g1" / "P1" / "scratch.jsonl"
        with extra.open("w", encoding="utf-8") as f:
            f.write("this is not valid belief json\n")

        s2 = JsonlBeliefStateStore(root)
        assert len(s2.get_history("g1", "P1")) == 1

    @pytest.mark.parametrize("bad_id", ["", ".", "..", "a/b", "a\\b"])
    def test_rejects_unsafe_game_id_for_filename(self, tmp_path, bad_id):
        """不允许把路径分隔符 / 特殊段当 game_id（文件系统注入防御）。"""
        store = JsonlBeliefStateStore(tmp_path / "beliefs")
        with pytest.raises(ValueError, match="invalid game_id"):
            store.save(make_belief(game_id=bad_id))

    @pytest.mark.parametrize("bad_id", ["", ".", "..", "a/b", "a\\b"])
    def test_rejects_unsafe_agent_id_for_filename(self, tmp_path, bad_id):
        """agent_id 同样要做文件系统安全校验。"""
        store = JsonlBeliefStateStore(tmp_path / "beliefs")
        with pytest.raises(ValueError, match="invalid agent_id"):
            store.save(make_belief(agent_id=bad_id))

    def test_invalid_id_does_not_pollute_memory(self, tmp_path):
        """非法 id 必须在写内存索引之前就拦下，避免脏数据残留。"""
        store = JsonlBeliefStateStore(tmp_path / "beliefs")
        store.save(make_belief(game_id="g1", agent_id="P1"))  # 一个合法 save
        with pytest.raises(ValueError):
            store.save(make_belief(game_id="bad/id", agent_id="P1"))

        # 唯一一条历史应该还是合法那条；非法 save 没留下尸体
        assert len(store.get_history("g1", "P1")) == 1
        assert store.get_history("bad/id", "P1") == []

    def test_save_failure_rolls_back_memory(self, tmp_path, monkeypatch):
        """模拟磁盘写失败：内存索引必须回滚，store 状态保持一致。"""
        root = tmp_path / "beliefs"
        store = JsonlBeliefStateStore(root)

        # 让对 real.jsonl 的追加写失败
        real_open = Path.open

        def fake_open(self, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "")
            if self.name == "real.jsonl" and "a" in mode:
                raise OSError("disk full simulation")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)

        with pytest.raises(OSError, match="disk full"):
            store.save(make_belief())

        # 关键：内存里不该残留这条 belief（回滚成功）
        with pytest.raises(BeliefStateNotFoundError):
            store.get("g1", "P1")
        assert store.get_history("g1", "P1") == []

    def test_get_history_reads_disk_on_every_call(self, tmp_path):
        """回归：get_history 实时读盘，不依赖构造时的 _index 快照。

        bug 场景：backend 已启动（JsonlBeliefStateStore 已构造），批跑进程随后
        把 BeliefState 写到同一目录。修复前 get_history 只查 _index（启动时快照），
        新对局对审计页不可见。修复后每次调用实时读盘。
        """
        root = tmp_path / "beliefs"

        # 1) backend 启动时构造第一个实例（_index 为空，dir 是空目录）
        store_backend = JsonlBeliefStateStore(root)

        # 2) 模拟"另一个进程"用第二个实例写一条 BeliefState 到同一目录
        store_batch = JsonlBeliefStateStore(root)
        bs = make_belief(game_id="g_new", agent_id="P1", round_=3, wolf_prob=0.42)
        store_batch.save(bs)

        # 3) 第一个实例的 _index 里没有 g_new（它是启动后才写入的）
        #    如果 get_history 只查 _index，会返回 []——这就是 bug。
        #    修复后应实时读盘，返回那条 BeliefState。
        history = store_backend.get_history("g_new", "P1")
        assert len(history) == 1, (
            "get_history must read disk on every call, not only the startup _index"
        )
        assert history[0].round == 3
        assert history[0].beliefs["P2"].werewolf == pytest.approx(0.42)

    def test_get_history_unknown_id_returns_empty_after_fix(self, tmp_path):
        """修复后：合法但从未写过的 game_id 依然返回 []。"""
        store = JsonlBeliefStateStore(tmp_path / "beliefs")
        assert store.get_history("nonexistent-game", "P1") == []

    def test_save_failure_then_recover_works(self, tmp_path, monkeypatch):
        """回滚之后，重新一次正常 save 应该成功，store 状态健康。"""
        root = tmp_path / "beliefs"
        store = JsonlBeliefStateStore(root)

        real_open = Path.open
        fail_once = {"done": False}

        def flaky_open(self, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "")
            if (
                self.name == "real.jsonl"
                and "a" in mode
                and not fail_once["done"]
            ):
                fail_once["done"] = True
                raise OSError("transient")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", flaky_open)

        with pytest.raises(OSError):
            store.save(make_belief(round_=1))

        # 第二次 save 应该成功
        store.save(make_belief(round_=2))
        assert store.get("g1", "P1").round == 2
        assert len(store.get_history("g1", "P1")) == 1
