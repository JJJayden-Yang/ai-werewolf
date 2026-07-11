"""#3（Yuan）：emit_witch_kill_info 接进 run_game —— 由 deliver_witch_kill_info 开关控制。

- 默认关：事件流无 WITCH_KILL_TARGET_INFO，mock baseline 行为完全不变（不破 0-fallback 断言）。
- 打开（v0 LLM 用）：进 NIGHT_WITCH 前下发当晚刀口（PRIVATE_TO_WITCH, actor=None），让女巫看得到。

注：自 RuleValidator 放开"女巫第一夜可自救"后,开关打开 + mock 女巫被刀的第一夜会合法自救(无 fallback)；
仅第二夜起刀口是自己才会撞 target_self / 无解药 fallback(属 B 的 mock 夜间动作卫生)。故本测试仍只验
"是否下发 + 可见性",不断言 0 fallback。
"""

import asyncio
import json
import random
from pathlib import Path

from agent_policy import RoleStrategyMockAgent
from contracts import EventType, GameConfig, Phase, Visibility
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _run(seed: int, game_id: str, *, deliver: bool):
    data = json.loads((FIXTURES / "game_config_9p_mvp.json").read_text(encoding="utf-8"))
    data["game_id"] = game_id
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(GameConfig.model_validate(data))
    store = InMemoryEventStore()
    sup = Supervisor(
        engine,
        ContextAssembler(session_provider=engine, event_store=store),
        RoleStrategyMockAgent(),
        store,
        deliver_witch_kill_info=deliver,
    )
    asyncio.run(sup.run_game(game_id))
    return engine, store.list_by_game(game_id)


def test_default_off_does_not_deliver_witch_kill_info():
    """默认关 → 事件流无 witch_kill_target_info，baseline 行为不变。"""
    engine, events = _run(0, "yuan_wki_off", deliver=False)
    assert engine.get_session("yuan_wki_off").current_phase == Phase.GAME_OVER
    assert not any(e.event_type == EventType.WITCH_KILL_TARGET_INFO for e in events)


def test_flag_on_delivers_witch_kill_info_at_night_witch_private_to_witch():
    """打开 → NIGHT_WITCH 有 witch_kill_target_info（PRIVATE_TO_WITCH, actor=None）。"""
    engine, events = _run(0, "yuan_wki_on", deliver=True)
    assert engine.get_session("yuan_wki_on").current_phase == Phase.GAME_OVER  # 仍跑完整局
    wki = [e for e in events if e.event_type == EventType.WITCH_KILL_TARGET_INFO]
    assert wki, "开关打开后第一夜应至少下发一条 witch_kill_target_info"
    for e in wki:
        assert e.phase == Phase.NIGHT_WITCH  # 只在女巫阶段发，不在 NIGHT_SEER
        assert e.visibility == Visibility.PRIVATE_TO_WITCH
        assert e.actor is None
        assert e.target is not None  # 当晚刀口
