"""顺序发言（消息队列语义）回归守卫。

第一步改造：DAY_DISCUSSION / DAY_TIE_DISCUSSION 在 supervisor 里逐人结算 —— 每人发完立即
emit+append，后发言者的 build_context 能看到本轮已发言者的发言。本测试钉住这个语义：
- 第一个发言者看不到任何同轮 peer 发言；
- 最后一个发言者能看到本轮**全部**前序发言者（顺序可见 / 不是"同时发言"）。

对照：投票等批量阶段不在此约束内（仍批量收齐结算）。
"""

import asyncio
import json
import random
from pathlib import Path

from agent_runtime import LLMAgent  # noqa: F401  (确保 agent_runtime 可导入，不直接用)
from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater
from contracts import AgentContext, EventType, GameConfig, Phase, PlayerStatus
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.belief_state_store import InMemoryBeliefStateStore
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class _RecordingAgent:
    """记录每个 actor 在 build_context 时，本轮 current_round_events 里看到的发言者集合。"""

    def __init__(self) -> None:
        self.seen_speakers: dict[str, set[str]] = {}

    async def act(self, context: dict) -> dict:
        ctx = AgentContext.model_validate(context)
        self.seen_speakers[ctx.agent_id] = {
            ev.actor
            for ev in ctx.current_round_events
            if ev.event_type == EventType.SPEECH and ev.actor
        }
        return {
            "game_id": ctx.game_id,
            "agent_id": ctx.agent_id,
            "role": ctx.role.value,
            "phase": ctx.phase.value,
            "action_type": "speak",
            "public_message": f"hi from {ctx.agent_id}",
        }


def _discussion_setup(seed: int, game_id: str):
    data = json.loads((FIXTURES / "game_config_9p_mvp.json").read_text(encoding="utf-8"))
    data["game_id"] = game_id
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(GameConfig.model_validate(data))
    # 直接把局面摆到白天讨论阶段（round 默认 1，全员存活）。
    engine.get_session(game_id).truth_state.phase = Phase.DAY_DISCUSSION
    store = InMemoryEventStore()
    agent = _RecordingAgent()
    sup = Supervisor(engine, ContextAssembler(session_provider=engine, event_store=store), agent, store)
    return engine, sup, agent


def test_sequential_speech_later_speaker_sees_all_earlier_same_round():
    engine, sup, agent = _discussion_setup(0, "seq_speech")
    actors = engine.get_required_actors("seq_speech", Phase.DAY_DISCUSSION)
    assert len(actors) >= 3, "9 人局白天讨论应有 ≥3 个发言者"

    asyncio.run(sup.run_phase("seq_speech"))

    # 第一个发言者：本轮看不到任何 peer 发言
    assert agent.seen_speakers[actors[0]] == set()
    # 最后一个发言者：本轮能看到全部前序发言者（消息队列语义）
    assert set(actors[:-1]).issubset(agent.seen_speakers[actors[-1]])
    # 中间某人：能看到他之前的人、看不到他之后的人
    mid = len(actors) // 2
    assert set(actors[:mid]).issubset(agent.seen_speakers[actors[mid]])
    assert agent.seen_speakers[actors[mid]].isdisjoint(set(actors[mid + 1 :]))


def test_sequential_speech_visibility_is_strictly_incremental():
    """每个后位发言者看到的前序集合，是前一位看到的超集（严格递增）。"""
    engine, sup, agent = _discussion_setup(1, "seq_speech_inc")
    actors = engine.get_required_actors("seq_speech_inc", Phase.DAY_DISCUSSION)

    asyncio.run(sup.run_phase("seq_speech_inc"))

    for i in range(1, len(actors)):
        prev_seen = agent.seen_speakers[actors[i - 1]]
        cur_seen = agent.seen_speakers[actors[i]]
        assert prev_seen.issubset(cur_seen)
        # 第 i 位比第 i-1 位至少多看到第 i-1 位本人的发言
        assert actors[i - 1] in cur_seen


class _BeliefRecordingAgent:
    """第一位发查杀；后续记录 build_context 时是否已吃到该发言更新的 belief。"""

    def __init__(self, target: str) -> None:
        self._target = target
        self.contexts: dict[str, AgentContext] = {}
        self._turn = 0

    async def act(self, context: dict) -> dict:
        ctx = AgentContext.model_validate(context)
        self.contexts[ctx.agent_id] = ctx
        self._turn += 1
        action = {
            "game_id": ctx.game_id,
            "agent_id": ctx.agent_id,
            "role": ctx.role.value,
            "phase": ctx.phase.value,
            "action_type": "speak",
            "public_message": f"hi from {ctx.agent_id}",
        }
        if self._turn == 1:
            action["public_message"] = f"我查验 {self._target} 是狼人。"
            action["role_claim"] = "seer"
            action["claim_result"] = {
                "target": self._target,
                "claimed_alignment": "werewolf",
            }
        return action


def test_sequential_speech_updates_belief_before_next_speaker_context():
    engine, _sup, _agent = _discussion_setup(2, "seq_speech_belief")
    actors = engine.get_required_actors("seq_speech_belief", Phase.DAY_DISCUSSION)
    target = next(pid for pid in engine.get_session("seq_speech_belief").truth_state.players if pid not in actors[:2])
    store = InMemoryEventStore()
    belief_store = InMemoryBeliefStateStore()
    agent = _BeliefRecordingAgent(target=target)
    sup = Supervisor(
        engine,
        ContextAssembler(
            session_provider=engine,
            event_store=store,
            belief_store=belief_store,
        ),
        agent,
        store,
        belief_updater=RuleBasedRealtimeBeliefUpdater(
            event_store=store,
            belief_store=belief_store,
            session_provider=engine,
        ),
    )

    asyncio.run(sup.run_phase("seq_speech_belief"))

    second_ctx = agent.contexts[actors[1]]
    assert any(
        item["player_id"] == target and item["werewolf_prob"] > 0.2
        for item in second_ctx.belief_top_suspects
    )
