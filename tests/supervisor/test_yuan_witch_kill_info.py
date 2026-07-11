"""第0步（Yuan）：女巫刀口下发的引擎能力 + 私有可见性（W1 同型）。

修复点：
- `GameEngine.emit_witch_kill_info` 生成 `PRIVATE_TO_WITCH` 的 `WITCH_KILL_TARGET_INFO`，
  让女巫在**真实 ContextAssembler** 装配的 context 里看得到当晚刀口。
- `witch_save / witch_poison` 改为 `PRIVATE_TO_WITCH`（之前默认 public，落 C 私有过滤后谁都收不到）。

注意：本测试只验证 A 的引擎能力 + 可见性 + 单夜端到端。**完整接进 run_game 仍被 B/C 阻塞**
（女巫策略需用当前轮刀口、尊重解药；C 需按当前轮过滤 private_events 或给 PrivateEvent 加 round），
故此处不跑整局 run_game（多夜会因 B/C 未修而触发 fallback）。
"""

import json
import random
from pathlib import Path

from agent_policy.roles.witch import WitchStrategy
from contracts import (
    ActionType,
    AgentAction,
    EventType,
    GameConfig,
    Phase,
    PlayerStatus,
    Role,
    Visibility,
)
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.event_store import InMemoryEventStore

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _make_6p_engine(seed: int, game_id: str) -> GameEngine:
    config_data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)
    return engine


def _role_id(engine: GameEngine, game_id: str, role: Role) -> str:
    players = engine.get_session(game_id).truth_state.players
    return next(pid for pid, p in players.items() if p.role == role)


def test_witch_kill_info_reaches_witch_via_real_context_not_others():
    gid = "yuan_witch_info_seam"
    engine = _make_6p_engine(seed=0, game_id=gid)
    ts = engine.get_session(gid).truth_state
    witch_id = _role_id(engine, gid, Role.WITCH)
    villager_id = _role_id(engine, gid, Role.VILLAGER)

    # 模拟狼夜已定刀口（正常由 resolve_wolf_nomination 设置）
    kill_target = next(pid for pid, p in ts.players.items() if p.role == Role.VILLAGER)
    ts.night_state.kill_target = kill_target

    store = InMemoryEventStore()
    store.append_many(engine.emit_witch_kill_info(gid))  # Supervisor 在 build_context 前 append
    assembler = ContextAssembler(session_provider=engine, event_store=store)

    witch_ctx = assembler.build_context(gid, witch_id, Phase.NIGHT_WITCH)
    knife_events = [
        e for e in witch_ctx.private_events if e.event_type == EventType.WITCH_KILL_TARGET_INFO
    ]
    assert len(knife_events) == 1
    assert knife_events[0].target == kill_target

    # 非女巫拿不到（不泄漏）
    villager_ctx = assembler.build_context(gid, villager_id, Phase.NIGHT_WITCH)
    assert not any(
        e.event_type == EventType.WITCH_KILL_TARGET_INFO for e in villager_ctx.private_events
    )


def test_witch_kill_info_noop_when_no_target_or_no_alive_witch():
    gid = "yuan_witch_info_noop"
    engine = _make_6p_engine(seed=0, game_id=gid)
    ts = engine.get_session(gid).truth_state

    # 无刀口 → 不发
    ts.night_state.kill_target = None
    assert engine.emit_witch_kill_info(gid) == []

    # 有刀口但女巫已死 → 不发
    ts.night_state.kill_target = _role_id(engine, gid, Role.VILLAGER)
    ts.players[_role_id(engine, gid, Role.WITCH)].status = PlayerStatus.DEAD
    assert engine.emit_witch_kill_info(gid) == []


def test_witch_kill_info_event_is_private_to_witch():
    gid = "yuan_witch_info_visibility"
    engine = _make_6p_engine(seed=0, game_id=gid)
    ts = engine.get_session(gid).truth_state
    ts.night_state.kill_target = _role_id(engine, gid, Role.VILLAGER)

    events = engine.emit_witch_kill_info(gid)
    assert len(events) == 1
    assert events[0].visibility == Visibility.PRIVATE_TO_WITCH


def test_witch_save_and_poison_emitted_private_to_witch():
    """女巫救/毒事件现在带 PRIVATE_TO_WITCH（之前默认 public）。"""
    gid = "yuan_witch_save_vis"
    engine = _make_6p_engine(seed=0, game_id=gid)
    ts = engine.get_session(gid).truth_state
    witch_id = _role_id(engine, gid, Role.WITCH)
    kill_target = _role_id(engine, gid, Role.VILLAGER)

    ts.phase = Phase.NIGHT_WITCH
    ts.night_state.kill_target = kill_target

    save = AgentAction(
        game_id=gid,
        agent_id=witch_id,
        role=Role.WITCH,
        phase=Phase.NIGHT_WITCH,
        action_type=ActionType.SAVE,
        target=kill_target,
    )
    events = engine.apply_actions(gid, [save])
    save_events = [e for e in events if e.event_type == EventType.WITCH_SAVE]
    assert len(save_events) == 1
    assert save_events[0].visibility == Visibility.PRIVATE_TO_WITCH

    # poison 分支用全新一局（同夜救+毒默认禁止，且解药已用过）
    engine2 = _make_6p_engine(seed=1, game_id="yuan_witch_poison_vis")
    ts2 = engine2.get_session("yuan_witch_poison_vis").truth_state
    witch2 = _role_id(engine2, "yuan_witch_poison_vis", Role.WITCH)
    poison_target = _role_id(engine2, "yuan_witch_poison_vis", Role.VILLAGER)
    ts2.phase = Phase.NIGHT_WITCH
    poison = AgentAction(
        game_id="yuan_witch_poison_vis",
        agent_id=witch2,
        role=Role.WITCH,
        phase=Phase.NIGHT_WITCH,
        action_type=ActionType.POISON,
        target=poison_target,
    )
    poison_events = engine2.apply_actions("yuan_witch_poison_vis", [poison])
    poison_only = [e for e in poison_events if e.event_type == EventType.WITCH_POISON]
    assert len(poison_only) == 1
    assert poison_only[0].visibility == Visibility.PRIVATE_TO_WITCH


def test_witch_strategy_saves_when_assembled_context_has_knife_single_night():
    """单夜端到端：引擎发刀口 → 真实 assembler 装配 → 女巫策略据此 SAVE 当晚刀口。"""
    gid = "yuan_witch_e2e_night1"
    engine = _make_6p_engine(seed=0, game_id=gid)
    ts = engine.get_session(gid).truth_state
    witch_id = _role_id(engine, gid, Role.WITCH)
    kill_target = _role_id(engine, gid, Role.VILLAGER)
    ts.night_state.kill_target = kill_target

    store = InMemoryEventStore()
    store.append_many(engine.emit_witch_kill_info(gid))
    assembler = ContextAssembler(session_provider=engine, event_store=store)
    ctx = assembler.build_context(gid, witch_id, Phase.NIGHT_WITCH)

    action = WitchStrategy().decide(ctx)
    assert action.action_type == ActionType.SAVE
    assert action.target == kill_target
