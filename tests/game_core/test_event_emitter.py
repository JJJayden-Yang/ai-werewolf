"""A0：EventEmitter 生成的 GameEvent 包含必要字段。"""

import json
from pathlib import Path

from contracts import GameConfig, TruthState
from contracts.enums import EventType, Visibility
from game_core import EventEmitter, GameSession

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _build_session() -> GameSession:
    cfg = GameConfig.model_validate(
        json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    )
    ts = TruthState.model_validate(
        json.loads((FIXTURES / "truth_state_6p_initial.json").read_text(encoding="utf-8"))
    )
    return GameSession(game_id=cfg.game_id, config=cfg, truth_state=ts)


def test_emit_produces_event_with_required_fields():
    emitter = EventEmitter()
    session = _build_session()
    event = emitter.emit(session, "phase_started", {"phase": "NIGHT_WEREWOLF"})

    assert event.event_id
    assert event.game_id == session.game_id
    assert event.round == session.round
    assert event.phase == session.current_phase
    assert event.event_type == EventType.PHASE_STARTED
    assert event.visibility == Visibility.PUBLIC
    assert event.created_at
    assert event.payload == {"phase": "NIGHT_WEREWOLF"}


def test_emit_lifts_actor_target_visibility_to_top_level():
    emitter = EventEmitter()
    session = _build_session()
    event = emitter.emit(
        session,
        "death_confirmed",
        {"actor": "P1", "target": "P3", "visibility": "public", "death_cause": "night_kill"},
    )
    assert event.actor == "P1"
    assert event.target == "P3"
    assert event.payload == {"death_cause": "night_kill"}


def test_event_ids_are_unique_and_incrementing():
    emitter = EventEmitter()
    session = _build_session()
    ids = [emitter.emit(session, "phase_started", {}).event_id for _ in range(3)]
    gid = session.game_id
    assert ids == [f"{gid}_evt_0001", f"{gid}_evt_0002", f"{gid}_evt_0003"]


def test_emit_uses_injected_clock_for_created_at():
    """created_at 时钟可注入：注入逻辑时钟让 replay 字节级可复现（wall-clock 每次都不同）。"""
    ticks = iter(["t1", "t2", "t3"])
    emitter = EventEmitter(clock=lambda: next(ticks))
    session = _build_session()
    e1 = emitter.emit(session, "phase_started", {})
    e2 = emitter.emit(session, "phase_started", {})
    assert e1.created_at == "t1"
    assert e2.created_at == "t2"


def test_event_ids_do_not_collide_across_games_in_shared_store():
    """两局事件进同一个去重 store 不能撞 id。

    复现真实失败：每局 new GameEngine() → EventEmitter 序号重置；若不带 game_id 前缀，
    第二局的 evt_0001 会撞第一局，append 抛 DuplicateEventError。
    """
    from stores.event_store import InMemoryEventStore

    session_a = _build_session()
    session_b = _build_session()
    session_b.game_id = "other_game"

    store = InMemoryEventStore()
    # 模拟两个独立引擎：各自 fresh emitter，序号都从 1 开始。
    for emitter, session in ((EventEmitter(), session_a), (EventEmitter(), session_b)):
        store.append_many([emitter.emit(session, "phase_started", {}) for _ in range(3)])

    assert len(store) == 6
    assert all(e.event_id.startswith(f"{session_a.game_id}_") for e in store.list_by_game(session_a.game_id))
    assert all(e.event_id.startswith("other_game_") for e in store.list_by_game("other_game"))
