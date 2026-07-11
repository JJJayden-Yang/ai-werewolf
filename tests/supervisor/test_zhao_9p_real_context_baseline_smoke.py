"""C（Zhao）：9 人 **真实 ContextAssembler** + 真实角色策略的 100 局 baseline 压测前回归。

照搬 A 写的 `test_yuan_6p_real_context_baseline_smoke.py` 模式，扩到 9 人：
- 3 狼 / 1 预言家 / 1 女巫 / 1 猎人 / 3 平民
- hunter_enabled=true（B 的 HunterStrategy 默认 pass，先稳定跑通）
- witch_knows_kill_target=true，但 A 还没 emit_witch_kill_info 接进 run_game，
  故女巫 NIGHT_WITCH 时 private_events 里没有 WITCH_KILL_TARGET_INFO，会按
  WitchStrategy 兜底（SKIP/POISON 视具体逻辑）—— 不破。

验收（A 5/25 18:55 群里"今天争取弄完上服务器进行压测和并发测试"）：
100 局全到 GAME_OVER、phase_stuck=0、零 rule_validation、零 fallback、
信息隔离不破；狼夜刀/投票/二次投票**从不指向狼**；预言家 9 人 D1 默认 hold
不主动跳明（避免 D2 没了视角）。

同时给出 9 人 baseline 的关键指标，方便给 A/B 群里反馈：
- winner 分布（狼 vs 好人胜率）
- avg round / avg events
- 预言家 D1 hold 率（衡量 9 人收紧策略实际生效）
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

FIXTURES_C = Path(__file__).resolve().parents[1] / "fixtures"

_FORBIDDEN_CONTEXT_KEYS = ("truth_state", "role_map", "hidden_roles")
_WOLF_TARGETING_ACTIONS = {"night_kill_nominate", "vote"}


class _RecordingAgent:
    """包真实 RoleStrategyMockAgent：记录精简动作 + 在线做信息隔离检查 +
    收集预言家 D1 是否 hold（9 人收紧策略的关键指标）。
    """

    def __init__(self, inner: RoleStrategyMockAgent) -> None:
        self._inner = inner
        self.actions: list[tuple[str, str, str, str | None, str | None, int]] = []
        # (role, phase, action_type, target, selected_by, round)
        self.isolation_ok = True

    async def act(self, context: dict) -> dict:
        if any(key in json.dumps(context) for key in _FORBIDDEN_CONTEXT_KEYS):
            self.isolation_ok = False
        action = await self._inner.act(context)
        meta = action.get("metadata") or {}
        self.actions.append(
            (
                context["role"],
                context["phase"],
                action["action_type"],
                action.get("target"),
                meta.get("selected_by"),
                context["round"],
            )
        )
        return action


def _run_real_context_game(seed: int, game_id: str):
    config_data = json.loads(
        (FIXTURES_C / "game_config_9p_debug.json").read_text(encoding="utf-8")
    )
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


def test_single_9p_real_context_game_observable_event_chain():
    config, engine, store, _agent = _run_real_context_game(
        seed=0, game_id="zhao_rc_9p_single"
    )
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
        assert required in types, f"missing {required}"

    # 开局狼队友 setup 事件：3 狼局，WOLF_NOMINATION + actor=None + 3 个 teammates
    setup = [
        e
        for e in events
        if e.event_type == EventType.WOLF_NOMINATION
        and e.actor is None
        and e.payload.get("teammates")
    ]
    assert len(setup) == 1
    assert set(setup[0].payload["teammates"]) == _truth_wolves(engine, config.game_id)
    assert len(setup[0].payload["teammates"]) == 3

    json.dumps([e.model_dump(mode="json") for e in events])  # replay 可序列化


def test_100_9p_real_context_games_stable_no_fallback_wolf_never_hits_teammate():
    stats = {
        "completed": 0,
        "phase_stuck": 0,
        "rule_validation": 0,
        "fallback": 0,
        "wolf_targeting_actions": 0,
        "wolf_hit_teammate": 0,
        "winner_distribution": {},
        "seer_d1_hold_count": 0,
        "seer_d1_speech_count": 0,
    }

    for seed in range(100):
        gid = f"zhao_rc_9p_{seed:03d}"
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
        stats["fallback"] += sum(
            1 for e in events if e.event_type == EventType.FALLBACK_USED
        )
        assert agent.isolation_ok, f"信息隔离破坏 @ seed={seed}"

        for role, phase, action_type, target, selected_by, round_num in agent.actions:
            if (
                role == Role.WEREWOLF.value
                and action_type in _WOLF_TARGETING_ACTIONS
                and target
            ):
                stats["wolf_targeting_actions"] += 1
                if target in wolves:
                    stats["wolf_hit_teammate"] += 1
            # 预言家 D1 SPEAK 是否 hold（9 人收紧策略）
            if (
                role == Role.SEER.value
                and action_type == "speak"
                and round_num == 1
                and phase == "day_discussion"
            ):
                stats["seer_d1_speech_count"] += 1
                if selected_by == "seer_9p_d1_hold":
                    stats["seer_d1_hold_count"] += 1

        winner = next(
            (
                e.payload.get("winner")
                for e in reversed(events)
                if e.event_type == EventType.GAME_OVER
            ),
            None,
        )
        stats["winner_distribution"][winner] = (
            stats["winner_distribution"].get(winner, 0) + 1
        )

    assert stats["completed"] == 100, f"phase_stuck={stats['phase_stuck']}"
    assert stats["phase_stuck"] == 0
    assert stats["rule_validation"] == 0
    assert stats["fallback"] == 0
    assert stats["wolf_targeting_actions"] > 0  # 确实跑过狼的刀/投
    assert stats["wolf_hit_teammate"] == 0  # 狼从不指向队友
    assert sum(stats["winner_distribution"].values()) == 100
