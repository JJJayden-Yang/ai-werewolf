"""P3（Yuan）：真实 B/C 组件经 Supervisor 跑通 6 人最小 MVP。

与 test_6p_smoke.py 的区别：那里用 A 自造的假件（SmokeContextAssembler / 内联 mock /
list sink）。这里换成**真实**组件，验证契约接缝真的对得上：

- 真实 B：`agent_policy.LegalRandomMockAgent`（读真实 AgentContext，出真实 AgentAction）；
- 真实 C 存储：`stores.InMemoryEventStore`（带 event_id 去重，作为 EventSink）；
- 真实 A↔C 接口：装配器走 `SessionProvider.get_session` + `RuleValidator.allowed_actions`；
- 真实 A：GameEngine + Supervisor。

C 的真实 `context.ContextAssembler` 目前仍是 NotImplementedError 桩，所以这里的装配器在
真实 A↔C 契约接缝上搭一个等价实现——既跑通数据，也作为 C 落地时的接线样板。
"""

import asyncio
import json
import random
from pathlib import Path

from agent_policy import LegalRandomMockAgent
from contracts import (
    AgentContext,
    EventType,
    GameConfig,
    Phase,
    PrivateEvent,
    Role,
    Visibility,
    VisiblePlayer,
)
from game_core import GameEngine, GameSessionManager, RuleValidator, SessionProvider
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class RealInterfaceContextAssembler:
    """用 A 的真实对外接口装配 AgentContext，喂给真实 B MockAgent。

    遵守可见性：`VisiblePlayer` 不含 role；狼队友通过 private_events 注入（沿用
    fixtures 约定：ROLE_ASSIGNED + teammates + PRIVATE_TO_WOLVES）。allowed_actions
    取自 A 的单一真相源 `RuleValidator.allowed_actions`，tie_candidates 直接读 round_state。
    """

    def __init__(self, sessions: SessionProvider) -> None:
        self._sessions = sessions

    def build_context(self, game_id: str, agent_id: str, phase: Phase) -> AgentContext:
        session = self._sessions.get_session(game_id)
        ts = session.truth_state
        me = ts.players[agent_id]

        private: list[PrivateEvent] = []
        if me.role == Role.WEREWOLF:
            teammates = [pid for pid, p in ts.players.items() if p.role == Role.WEREWOLF]
            private.append(
                PrivateEvent(
                    event_type=EventType.ROLE_ASSIGNED,
                    teammates=teammates,
                    visibility=Visibility.PRIVATE_TO_WOLVES,
                )
            )

        return AgentContext(
            game_id=game_id,
            agent_id=agent_id,
            role=me.role,
            round=session.round,
            phase=phase,
            visible_players=[
                VisiblePlayer(player_id=pid, status=p.status, public_claim=p.public_claim)
                for pid, p in ts.players.items()
            ],
            private_events=private,
            tie_candidates=ts.round_state.tie_candidates,
            allowed_actions=list(RuleValidator.allowed_actions(phase)),
        )


class RecordingAgent:
    """包一层真实 MockAgent，记录它实际收到的 context（用于信息隔离断言）。"""

    def __init__(self, inner: LegalRandomMockAgent) -> None:
        self._inner = inner
        self.contexts: list[dict] = []

    async def act(self, context: dict) -> dict:
        self.contexts.append(context)
        return await self._inner.act(context)


def _run_real_bc_game(seed: int, game_id: str):
    config_data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)

    store = InMemoryEventStore()  # 真实 C 存储，作为 EventSink（带 event_id 去重）
    agent = RecordingAgent(LegalRandomMockAgent())  # 真实 B
    supervisor = Supervisor(engine, RealInterfaceContextAssembler(engine), agent, store)

    asyncio.run(supervisor.run_game(game_id))
    return config, engine, store, agent


def test_real_bc_6p_runs_to_game_over_through_real_event_store():
    config, engine, store, agent = _run_real_bc_game(seed=0, game_id="yuan_real_bc_6p")

    # 1) 真实 Supervisor 跑到终局
    assert engine.get_session(config.game_id).current_phase == Phase.GAME_OVER

    # 2) 事件确实落进真实 InMemoryEventStore（append 不抛 DuplicateEventError 已隐含证明
    #    event_id 跨批唯一），含开局/阶段/终局锚点
    events = store.list_by_game(config.game_id)
    assert events
    types = {e.event_type for e in events}
    assert {EventType.ROLE_ASSIGNED, EventType.PHASE_STARTED, EventType.GAME_OVER} <= types

    # 3) 真实 MockAgent 的动作全部通过真实 RuleValidator：无非法、无 fallback
    assert not any(e.event_type == EventType.RULE_VALIDATION for e in events)
    assert not any(e.event_type == EventType.FALLBACK_USED for e in events)

    # 4) event_id 经真实去重 store：唯一且按局前缀
    ids = [e.event_id for e in events]
    assert len(ids) == len(set(ids))
    assert all(eid.startswith(f"{config.game_id}_evt_") for eid in ids)

    # 5) 信息隔离：真实 agent 收到的 context（JSON 边界后）不含真相键
    assert agent.contexts
    for ctx in agent.contexts:
        blob = json.dumps(ctx)
        for forbidden in ("truth_state", "role_map", "hidden_roles"):
            assert forbidden not in blob

    # 6) replay 载荷可序列化
    json.dumps([e.model_dump(mode="json") for e in events])
