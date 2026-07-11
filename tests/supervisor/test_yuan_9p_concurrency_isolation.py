"""A4/S5（Yuan）：9 人并发隔离把关 —— 给 C 的并发 runner 定模型并实证无串局。

并发模型（A 的约定）：**每局一个独立 `GameEngine` 实例 + 独立 `ContextAssembler`**，
避免共享引擎内部 dict（`sessions` / `events._seq_by_game`）被并发访问。EventStore 可
每局独立，也可共享（`event_id = {game_id}_evt_{seq}` 按局前缀，天然不撞）。

实证思路：每局确定性（seed + RoleStrategyMockAgent + 确定性结算）。若 10 局**并发**跑出
的逐局逻辑事件流与**串行**完全一致，则证明并发未引入任何串局/竞态。
"""

import asyncio
import json
import random
from pathlib import Path

from agent_policy import RoleStrategyMockAgent
from contracts import EventType, GameConfig, Phase, Role
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"

_SEEDS = list(range(10))  # 10 局并发


def _config(game_id: str) -> GameConfig:
    data = json.loads((FIXTURES / "game_config_9p_mvp.json").read_text(encoding="utf-8"))
    data["game_id"] = game_id
    return GameConfig.model_validate(data)


def _build_runner(seed: int, game_id: str, store: InMemoryEventStore | None = None):
    """每局独立 GameEngine + ContextAssembler；store 可注入（共享）或每局新建。"""
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(_config(game_id))
    # 显式 None 判断：空 InMemoryEventStore 的 __len__==0 是 falsy，不能用 `store or ...`
    own_store = store if store is not None else InMemoryEventStore()
    assembler = ContextAssembler(session_provider=engine, event_store=own_store)
    supervisor = Supervisor(engine, assembler, RoleStrategyMockAgent(), own_store)
    return engine, own_store, supervisor


def _logical(events) -> list[tuple]:
    """逐事件逻辑投影（忽略 created_at 这种 wall-clock 噪声）。"""
    return [(str(e.event_type), e.round, e.phase.value if hasattr(e.phase, "value") else e.phase,
             e.actor, e.target) for e in events]


def _winner(events):
    return next(
        (e.payload.get("winner") for e in reversed(events) if e.event_type == EventType.GAME_OVER),
        None,
    )


def _run_serial() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for seed in _SEEDS:
        gid = f"yuan_conc_serial_{seed:02d}"
        engine, store, sup = _build_runner(seed, gid)
        asyncio.run(sup.run_game(gid))
        evs = store.list_by_game(gid)
        out[seed] = {"logical": _logical(evs), "winner": _winner(evs), "n": len(evs)}
    return out


async def _run_concurrent(store: InMemoryEventStore | None) -> dict[int, tuple[str, GameEngine, InMemoryEventStore]]:
    runners = {}
    for seed in _SEEDS:
        gid = f"yuan_conc_par_{seed:02d}"
        engine, st, sup = _build_runner(seed, gid, store=store)
        runners[seed] = (gid, engine, st, sup)
    # 10 局并发：在 await agent.act 处交错
    await asyncio.gather(*(sup.run_game(gid) for gid, _e, _s, sup in runners.values()))
    return {seed: (gid, engine, st) for seed, (gid, engine, st, _sup) in runners.items()}


def test_concurrent_10_games_match_serial_no_cross_contamination():
    """每局独立 engine+store：并发结果逐局与串行逐字段一致 → 零串局。"""
    serial = _run_serial()
    concurrent = asyncio.run(_run_concurrent(store=None))

    for seed in _SEEDS:
        gid, engine, store = concurrent[seed]
        evs = store.list_by_game(gid)
        # 1) 逻辑事件流与串行完全一致
        assert _logical(evs) == serial[seed]["logical"], f"seed={seed} 并发与串行逻辑流不一致（疑似串局）"
        assert _winner(evs) == serial[seed]["winner"]
        assert len(evs) == serial[seed]["n"]
        # 2) 该局 store 只含本局事件（event_id/payload 不混入别局）
        assert all(e.game_id == gid for e in evs)
        assert all(e.event_id.startswith(f"{gid}_evt_") for e in evs)
        # 3) TruthState 玩家属于本局、跑到终局
        assert engine.get_session(gid).current_phase == Phase.GAME_OVER


def test_concurrent_shared_eventstore_no_id_collision_and_isolated():
    """10 局并发共享同一个 EventStore：event_id 按局前缀不撞，list_by_game 按局隔离。"""
    serial = _run_serial()
    shared = InMemoryEventStore()
    concurrent = asyncio.run(_run_concurrent(store=shared))  # 不抛 DuplicateEventError 即证明 id 不撞

    seen_ids = set()
    for seed in _SEEDS:
        gid, _engine, _st = concurrent[seed]
        evs = shared.list_by_game(gid)  # 共享 store 按 game_id 取回本局
        assert all(e.game_id == gid for e in evs)
        assert len(evs) == serial[seed]["n"]  # 与串行同样的事件数（无丢、无串）
        ids = {e.event_id for e in evs}
        assert ids.isdisjoint(seen_ids), f"seed={seed} 与其它局 event_id 交叉"
        seen_ids |= ids
