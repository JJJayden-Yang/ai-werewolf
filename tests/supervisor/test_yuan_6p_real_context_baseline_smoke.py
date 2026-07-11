"""W3（Yuan）：6 人 **真实 ContextAssembler** + 真实角色策略的 100 局 baseline 压测前回归。

与既有测试的区别：
- `test_6p_smoke.py`：100 局，但用假 SmokeContextAssembler + 通用 LegalSmokeMockAgent。
- `test_agent_policy_integration.py`：真实 RoleStrategyMockAgent，但假 context、单局。
- 本测试：**真实** `context.ContextAssembler`（同一个 EventStore 既当写端 sink 又当读端）
  + 真实 `RoleStrategyMockAgent`（含 W2 补强后的 WerewolfStrategy），跑 100 局。

验收（B 交接 §9 + A 的 S2 baseline）：100 局全到 GAME_OVER、phase_stuck=0、
零 rule_validation、零 fallback、信息隔离不破；狼夜刀/投票/二次投票**从不指向狼**。
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

_FORBIDDEN_CONTEXT_KEYS = ("truth_state", "role_map", "hidden_roles")
_WOLF_TARGETING_ACTIONS = {"night_kill_nominate", "vote"}


class _RecordingAgent:
    """包真实 RoleStrategyMockAgent：记录精简动作 + 在线做信息隔离检查。"""

    def __init__(self, inner: RoleStrategyMockAgent) -> None:
        self._inner = inner
        self.actions: list[tuple[str, str, str, str | None]] = []  # role, phase, action_type, target
        self.isolation_ok = True

    async def act(self, context: dict) -> dict:
        if any(key in json.dumps(context) for key in _FORBIDDEN_CONTEXT_KEYS):
            self.isolation_ok = False
        action = await self._inner.act(context)
        self.actions.append(
            (context["role"], context["phase"], action["action_type"], action.get("target"))
        )
        return action


def _run_real_context_game(seed: int, game_id: str):
    config_data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)

    store = InMemoryEventStore()  # 同一实例：写端(sink) == 读端(ContextAssembler)
    assembler = ContextAssembler(session_provider=engine, event_store=store)
    agent = _RecordingAgent(RoleStrategyMockAgent())
    supervisor = Supervisor(engine, assembler, agent, store)

    asyncio.run(supervisor.run_game(game_id))
    return config, engine, store, agent


def _truth_wolves(engine: GameEngine, game_id: str) -> set[str]:
    players = engine.get_session(game_id).truth_state.players
    return {pid for pid, p in players.items() if p.role == Role.WEREWOLF}


def test_single_real_context_game_observable_event_chain():
    config, engine, store, _agent = _run_real_context_game(seed=0, game_id="yuan_rc_6p_single")
    events = store.list_by_game(config.game_id)

    assert engine.get_session(config.game_id).current_phase == Phase.GAME_OVER
    types = {e.event_type for e in events}
    for required in (
        EventType.ROLE_ASSIGNED,
        EventType.PHASE_STARTED,
        EventType.NIGHT_KILL_ANNOUNCED,
        EventType.DAY_ANNOUNCEMENT,
        EventType.VOTE_CAST,
        EventType.GAME_OVER,
    ):
        assert required in types

    # 开局狼队友 setup 事件：WOLF_NOMINATION + actor=None + payload.teammates 非空
    setup = [
        e
        for e in events
        if e.event_type == EventType.WOLF_NOMINATION
        and e.actor is None
        and e.payload.get("teammates")
    ]
    assert len(setup) == 1
    assert set(setup[0].payload["teammates"]) == _truth_wolves(engine, config.game_id)

    json.dumps([e.model_dump(mode="json") for e in events])  # replay 可序列化


def test_100_real_context_games_stable_no_fallback_wolf_never_hits_teammate():
    stats = {
        "completed": 0,
        "phase_stuck": 0,
        "rule_validation": 0,
        "fallback": 0,
        "wolf_targeting_actions": 0,
        "wolf_hit_teammate": 0,
        "winner_distribution": {},
    }

    for seed in range(100):
        gid = f"yuan_rc_6p_{seed:03d}"
        config, engine, store, agent = _run_real_context_game(seed, gid)
        events = store.list_by_game(gid)
        wolves = _truth_wolves(engine, gid)

        if engine.get_session(gid).current_phase == Phase.GAME_OVER:
            stats["completed"] += 1
        else:
            stats["phase_stuck"] += 1

        stats["rule_validation"] += sum(
            1 for e in events if e.event_type == EventType.RULE_VALIDATION
        )
        stats["fallback"] += sum(1 for e in events if e.event_type == EventType.FALLBACK_USED)
        assert agent.isolation_ok, f"信息隔离破坏 @ seed={seed}"

        for role, _phase, action_type, target in agent.actions:
            if role == Role.WEREWOLF.value and action_type in _WOLF_TARGETING_ACTIONS and target:
                stats["wolf_targeting_actions"] += 1
                if target in wolves:
                    stats["wolf_hit_teammate"] += 1

        winner = next(
            (e.payload.get("winner") for e in reversed(events) if e.event_type == EventType.GAME_OVER),
            None,
        )
        stats["winner_distribution"][winner] = stats["winner_distribution"].get(winner, 0) + 1

    assert stats["completed"] == 100
    assert stats["phase_stuck"] == 0
    assert stats["rule_validation"] == 0
    assert stats["fallback"] == 0
    assert stats["wolf_targeting_actions"] > 0  # 确实跑过狼的刀/投
    assert stats["wolf_hit_teammate"] == 0  # 狼从不指向队友
    assert sum(stats["winner_distribution"].values()) == 100
