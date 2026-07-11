"""W1（Yuan）：狼队友名单经**真实** C ContextAssembler 抵达狼 Agent。

背景：狼"不刀/不投队友"依赖 `AgentContext.private_events[*].teammates`。在此修复前，
全库没有任何代码 emit 带 teammates 的事件——只有 fixture 和等价装配器手工注入，
绕过了 C 的可见性白名单。一旦换成 C 的真实 `context.ContextAssembler`，狼会失去队友
信息，`select_wolf_kill_target` 退化成"第一个存活非自己"，可能选中队友 →
RuleValidator 拦截（wolf_cannot_kill_teammate）→ rule_validation + fallback 噪声。

A 的修复：开局经 `GameEngine.emit_wolf_teammates` 播一条 WOLF_NOMINATION +
PRIVATE_TO_WOLVES + payload.teammates。本测试用**真实** ContextAssembler + 真实
EventStore 验证这条接缝端到端打通，且不向非狼泄漏。
"""

import asyncio
import json
import random
from pathlib import Path

from agent_policy import RoleStrategyMockAgent
from agent_policy.target_selectors import (
    select_wolf_kill_target,
    wolf_teammates_from_private_events,
)
from contracts import EventType, GameConfig, Phase, Role
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _make_6p_engine(seed: int, game_id: str) -> tuple[GameEngine, GameConfig]:
    config_data = json.loads(
        (FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8")
    )
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)
    return engine, config


def _wolves(engine: GameEngine, game_id: str) -> set[str]:
    players = engine.get_session(game_id).truth_state.players
    return {pid for pid, p in players.items() if p.role == Role.WEREWOLF}


def test_real_context_assembler_delivers_teammates_to_wolves_only():
    """开局发的 teammates 事件，经真实 ContextAssembler 后只有狼能看到。"""
    gid = "yuan_wolf_teammates_seam"
    engine, _ = _make_6p_engine(seed=0, game_id=gid)
    wolves = _wolves(engine, gid)
    assert len(wolves) >= 2  # 6 人 Debug 局是 2 狼

    # 真实 EventStore：开局锚点 + 狼队友名单都落进去
    store = InMemoryEventStore()
    store.append(engine.emit_role_assigned(gid))
    store.append_many(engine.emit_wolf_teammates(gid))

    # 真实 C ContextAssembler（session_provider=engine 满足 get_session 协议）
    assembler = ContextAssembler(session_provider=engine, event_store=store)

    # 狼：能看到完整队友名单，且夜刀目标绝不是队友
    for wolf_id in wolves:
        ctx = assembler.build_context(gid, wolf_id, Phase.NIGHT_WEREWOLF)
        assert wolf_teammates_from_private_events(ctx) == wolves
        target = select_wolf_kill_target(ctx)
        assert target is not None
        assert target not in wolves  # 永不刀队友

    # 非狼：拿不到任何 teammates（不泄漏）
    non_wolf = next(
        pid
        for pid, p in engine.get_session(gid).truth_state.players.items()
        if p.role != Role.WEREWOLF
    )
    ctx_villager = assembler.build_context(gid, non_wolf, Phase.NIGHT_WEREWOLF)
    assert wolf_teammates_from_private_events(ctx_villager) == set()


def test_wolf_teammates_persist_across_rounds():
    """开局发一次即可：ContextAssembler 读全量历史、窗口策略不裁 private_events。"""
    gid = "yuan_wolf_teammates_persist"
    engine, _ = _make_6p_engine(seed=1, game_id=gid)
    wolves = _wolves(engine, gid)

    store = InMemoryEventStore()
    store.append(engine.emit_role_assigned(gid))
    store.append_many(engine.emit_wolf_teammates(gid))
    assembler = ContextAssembler(session_provider=engine, event_store=store)

    wolf_id = next(iter(wolves))
    # 模拟推进到第 3 轮：teammates 仍可见（只发了一次，不依赖每夜重发）
    engine.get_session(gid).truth_state.round = 3
    ctx = assembler.build_context(gid, wolf_id, Phase.NIGHT_WEREWOLF)
    assert wolf_teammates_from_private_events(ctx) == wolves


class _RecordingAgent:
    """包真实 RoleStrategyMockAgent，记录每次 (context, action)。"""

    def __init__(self, inner: RoleStrategyMockAgent) -> None:
        self._inner = inner
        self.records: list[tuple[dict, dict]] = []

    async def act(self, context: dict) -> dict:
        action = await self._inner.act(context)
        self.records.append((context, action))
        return action


def _run_full_game_with_real_context(seed: int, game_id: str):
    engine, config = _make_6p_engine(seed, game_id)
    store = InMemoryEventStore()  # 同一个 store 既当 EventSink 又当 ContextAssembler 数据源
    assembler = ContextAssembler(session_provider=engine, event_store=store)
    agent = _RecordingAgent(RoleStrategyMockAgent())  # 真实 B 角色策略（含 WerewolfStrategy）
    supervisor = Supervisor(engine, assembler, agent, store)
    asyncio.run(supervisor.run_game(game_id))
    return engine, store, agent


def test_full_game_real_context_wolf_never_targets_teammate_no_fallback():
    """真实 ContextAssembler + WerewolfStrategy 跑整局：狼夜刀从不指向狼，且零规则噪声。

    无此修复时，狼夜里会瞎选到队友 → RuleValidator 拦截 → rule_validation + fallback。
    多个 seed 一起跑，提高"无修复时至少有一局会撞队友"的覆盖概率。
    """
    for seed in range(8):
        gid = f"yuan_wolf_full_{seed}"
        engine, store, agent = _run_full_game_with_real_context(seed, gid)

        assert engine.get_session(gid).current_phase == Phase.GAME_OVER

        wolves = _wolves(engine, gid)  # 初始狼集合（roster 在开局固定）
        wolf_night_actions = [
            action
            for context, action in agent.records
            if context["phase"] == Phase.NIGHT_WEREWOLF.value
            and context["role"] == Role.WEREWOLF.value
        ]
        assert wolf_night_actions  # 至少跑过一夜狼刀
        for action in wolf_night_actions:
            if action["action_type"] == "night_kill_nominate" and action["target"]:
                assert action["target"] not in wolves

        events = store.list_by_game(gid)
        assert not any(e.event_type == EventType.RULE_VALIDATION for e in events)
        assert not any(e.event_type == EventType.FALLBACK_USED for e in events)
