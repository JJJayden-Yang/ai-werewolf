"""A1.5：Supervisor 最小薄切片——用 fake context / fake agent 跑通一条数据流。

证明数据流过所有边界：config → create_game → run_phase → build_context(fake)
→ agent.act(fake) → apply_action → emit → event_sink。不接 LLM / 真实策略。
"""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import (
    ActionType,
    AgentContext,
    EventType,
    GameConfig,
    Phase,
    PlayerStatus,
    Role,
    VisiblePlayer,
)
from game_core import GameEngine
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


# --- 测试替身（站位 C 的 ContextAssembler / B 的 Agent / C 的 EventStore）---

class FakeContextAssembler:
    def __init__(self, engine: GameEngine) -> None:
        self._engine = engine

    def build_context(self, game_id: str, agent_id: str, phase: Phase) -> AgentContext:
        session = self._engine.sessions.get_game(game_id)
        player = session.truth_state.players[agent_id]
        legal_targets = [
            pid
            for pid, p in session.truth_state.players.items()
            if p.status == PlayerStatus.ALIVE
            and pid != agent_id
            and not (phase == Phase.NIGHT_WEREWOLF and p.role == Role.WEREWOLF)
        ]
        return AgentContext(
            game_id=game_id,
            agent_id=agent_id,
            role=player.role,
            round=session.round,
            phase=phase,
            visible_players=[
                VisiblePlayer(player_id=pid, status=p.status, public_claim=p.public_claim)
                for pid, p in session.truth_state.players.items()
            ],
            allowed_actions=[_PHASE_ACTION.get(phase, ActionType.SPEAK)],
            rule_hints={"fallback_targets": legal_targets},
        )


_PHASE_ACTION = {
    Phase.NIGHT_WEREWOLF: ActionType.NIGHT_KILL_NOMINATE,
    Phase.NIGHT_SEER: ActionType.CHECK,
    Phase.NIGHT_WITCH: ActionType.SKIP,
    Phase.DAY_VOTE: ActionType.VOTE,
}


class FakeAgent:
    def __init__(self) -> None:
        self.received_contexts: list[dict] = []

    async def act(self, context: dict) -> dict:
        self.received_contexts.append(context)
        return {
            "game_id": context["game_id"],
            "agent_id": context["agent_id"],
            "role": context["role"],
            "phase": context["phase"],
            "action_type": _PHASE_ACTION.get(Phase(context["phase"]), ActionType.SPEAK),
            "target": (context["rule_hints"]["fallback_targets"] or [None])[0]
            if Phase(context["phase"])
            in (Phase.NIGHT_WEREWOLF, Phase.NIGHT_SEER, Phase.DAY_VOTE)
            else None,
        }


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list = []
        self.groups: list[list] = []

    def append_many(self, events) -> None:
        self.groups.append(list(events))
        self.events.extend(events)


def _build(config_name: str):
    engine = GameEngine()
    config = GameConfig.model_validate(
        json.loads((FIXTURES / config_name).read_text(encoding="utf-8"))
    )
    engine.sessions.create_game(config)
    agent = FakeAgent()
    sink = FakeEventSink()
    supervisor = Supervisor(engine, FakeContextAssembler(engine), agent, sink)
    return config, supervisor, agent, sink


def test_run_phase_flows_to_events_and_sink_6p():
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")
    events = asyncio.run(supervisor.run_phase(config.game_id))

    # 初始 phase = NIGHT_WEREWOLF → 2 个狼提名 + 1 个最终刀口公告
    assert [ev.event_type for ev in events].count(EventType.WOLF_NOMINATION) == 2
    assert events[-1].event_type == EventType.NIGHT_KILL_ANNOUNCED
    for ev in events:
        assert ev.game_id == config.game_id
        assert ev.phase == Phase.NIGHT_WEREWOLF
        assert ev.round == 1
    assert sink.events == events  # 数据确实流到了 event sink


def test_run_phase_9p_werewolf_count():
    config, supervisor, _agent, _sink = _build("game_config_9p_mvp.json")
    events = asyncio.run(supervisor.run_phase(config.game_id))
    assert [ev.event_type for ev in events].count(EventType.WOLF_NOMINATION) == 3
    assert events[-1].event_type == EventType.NIGHT_KILL_ANNOUNCED


def test_day_vote_appends_each_vote_before_final_tally():
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")
    session = supervisor._engine.sessions.get_game(config.game_id)
    session.truth_state.phase = Phase.DAY_VOTE
    actors = supervisor._engine.get_required_actors(config.game_id, Phase.DAY_VOTE)

    events = asyncio.run(supervisor.run_phase(config.game_id))

    non_empty_groups = [group for group in sink.groups if group]
    assert len(non_empty_groups) == len(actors)
    assert all(
        group[-1].event_type == EventType.VOTE_CAST
        for group in non_empty_groups
    )
    assert [event.event_type for event in events].count(EventType.VOTE_CAST) == len(actors)


def test_day_vote_is_simultaneous_no_voter_sees_prior_votes():
    """投票同时性：后投者的 build_context 不得看到先投者已投的票。

    回归：旧实现每张 vote_cast 投完即 append 进 sink，下一个 actor build_context
    时就能从 current_round_events 读到（VOTE_CAST ∈ PUBLIC_EVENT_TYPES），等于顺序投票。
    本测试在每个 actor build_context 时刻快照 sink 里已有的 VOTE_CAST 数量，必须恒为 0。
    """
    config, _supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    engine = _supervisor._engine
    session = engine.sessions.get_game(config.game_id)
    session.truth_state.phase = Phase.DAY_VOTE

    sink = _sink
    seen_votes_at_build: list[int] = []

    base_assembler = FakeContextAssembler(engine)

    class RecordingAssembler:
        def build_context(self, game_id, agent_id, phase):
            if phase == Phase.DAY_VOTE:
                seen_votes_at_build.append(
                    sum(1 for e in sink.events if e.event_type == EventType.VOTE_CAST)
                )
            return base_assembler.build_context(game_id, agent_id, phase)

    _supervisor._context = RecordingAssembler()
    actors = engine.get_required_actors(config.game_id, Phase.DAY_VOTE)

    events = asyncio.run(_supervisor.run_phase(config.game_id))

    assert len(seen_votes_at_build) == len(actors)
    assert all(count == 0 for count in seen_votes_at_build)  # 没人在决策时看到任何已投票
    # 收齐后仍如常唱票：vote_cast 最终都进了 sink
    assert [e.event_type for e in events].count(EventType.VOTE_CAST) == len(actors)
    assert sink.events == events


def test_agent_receives_pure_json_without_truth_state():
    config, supervisor, agent, _sink = _build("game_config_6p_debug.json")
    asyncio.run(supervisor.run_phase(config.game_id))

    assert len(agent.received_contexts) == 2
    for ctx in agent.received_contexts:
        assert isinstance(ctx, dict)
        assert "truth_state" not in ctx and "role_map" not in ctx
        assert {"game_id", "agent_id", "role", "phase"} <= ctx.keys()


def test_validate_or_fallback_rejects_mismatched_game_id():
    """P2-1：action.game_id 必须与当前 game 一致，否则拒绝（防数据错配被隐藏）。"""
    supervisor = Supervisor(engine=None, context_assembler=None, agent_runtime=None, event_sink=None)
    raw = {
        "game_id": "WRONG",
        "agent_id": "P1",
        "role": "werewolf",
        "phase": "NIGHT_WEREWOLF",
        "action_type": "speak",
        "target": None,
    }
    with pytest.raises(ValueError):
        supervisor.validate_or_fallback("debug_6p_001", raw)

    raw["game_id"] = "debug_6p_001"
    action = supervisor.validate_or_fallback("debug_6p_001", raw)
    assert action.game_id == "debug_6p_001"


def test_validate_or_fallback_replaces_invalid_action_with_context_fallback():
    config, supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    session = supervisor._engine.sessions.get_game(config.game_id)
    wolf = next(pid for pid, p in session.truth_state.players.items() if p.role == Role.WEREWOLF)
    context = supervisor._context.build_context(config.game_id, wolf, Phase.NIGHT_WEREWOLF)

    raw = {
        "game_id": config.game_id,
        "agent_id": wolf,
        "role": "werewolf",
        "phase": "NIGHT_WEREWOLF",
        "action_type": "speak",  # 夜狼阶段非法
        "target": None,
    }

    action = supervisor.validate_or_fallback(config.game_id, raw, context)

    assert action.action_type == ActionType.NIGHT_KILL_NOMINATE
    assert action.metadata["fallback_used"] is True
    assert action.target in context.rule_hints["fallback_targets"]
    assert supervisor._engine.rules.validate(session, action).is_valid is True


def test_validate_or_fallback_never_raises_when_context_fallback_also_invalid():
    """P5：连 context 兜底都非法时，退到真相态安全兜底，绝不抛异常。

    模拟真实/LLM agent 输出脏数据 + C 给的 context 也"饿死"（无可选目标）的情况：
    旧实现会 raise ValueError 让整局崩；新实现收敛到一个保证合法的动作。
    """
    config, supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    session = supervisor._engine.sessions.get_game(config.game_id)
    session.truth_state.phase = Phase.DAY_VOTE  # 切到需要 target 的阶段
    agent_id = "P1"

    # 故意"饿死"的 context：可见玩家只有自己 → _fallback_from_context 找不到合法目标
    starved = AgentContext(
        game_id=config.game_id,
        agent_id=agent_id,
        role=session.truth_state.players[agent_id].role,
        round=session.round,
        phase=Phase.DAY_VOTE,
        visible_players=[VisiblePlayer(player_id=agent_id, status=PlayerStatus.ALIVE)],
        allowed_actions=[ActionType.VOTE],
        rule_hints={},
    )
    raw = {  # DAY_VOTE 阶段 speak 非法
        "game_id": config.game_id,
        "agent_id": agent_id,
        "role": starved.role.value,
        "phase": "DAY_VOTE",
        "action_type": "speak",
        "target": None,
    }

    action = supervisor.validate_or_fallback(config.game_id, raw, starved)  # 不应抛

    # 收敛到真相态里的合法动作
    assert supervisor._engine.rules.validate(session, action).is_valid
    assert action.action_type == ActionType.VOTE
    assert action.target is not None
    assert action.metadata.get("safe_fallback") is True
    # 原非法动作 + 降级兜底仍可观测
    types = [e.event_type for e in supervisor._pending_observation_events]
    assert EventType.RULE_VALIDATION in types
    assert EventType.FALLBACK_USED in types
    fb = next(
        e for e in supervisor._pending_observation_events if e.event_type == EventType.FALLBACK_USED
    )
    assert fb.payload["degraded"] is True
    assert fb.payload["fallback_failed"] is False  # 安全兜底复验合法


def test_validate_or_fallback_recovers_from_schema_invalid_raw():
    """P1：连 schema 都不合法的 raw（缺字段 / 非法 enum / action_type 乱填）也不能让整局崩。

    旧实现 AgentAction.model_validate 直接抛 ValidationError；现在用权威 actor_id 退到
    真相态安全兜底，并记成可观测事件。
    """
    config, supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    session = supervisor._engine.sessions.get_game(config.game_id)
    wolf = next(pid for pid, p in session.truth_state.players.items() if p.role == Role.WEREWOLF)
    context = supervisor._context.build_context(config.game_id, wolf, Phase.NIGHT_WEREWOLF)

    raw = {"action_type": "foo", "garbage": True}  # 缺必填字段 + 非法 action_type

    action = supervisor.validate_or_fallback(config.game_id, raw, context, actor_id=wolf)  # 不应抛

    assert supervisor._engine.rules.validate(session, action).is_valid
    assert action.agent_id == wolf
    assert action.action_type == ActionType.NIGHT_KILL_NOMINATE
    assert action.metadata.get("safe_fallback") is True
    fb = next(
        e for e in supervisor._pending_observation_events if e.event_type == EventType.FALLBACK_USED
    )
    assert fb.payload["original_error"] == "schema_invalid"
    assert fb.payload["degraded"] is True
    assert fb.payload["fallback_failed"] is False


def test_validate_or_fallback_schema_invalid_with_wrong_game_id_hard_raises():
    """schema-invalid 但携带错误 game_id：路由错误优先，必须硬抛，不能恢复成本局安全动作。"""
    config, supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    session = supervisor._engine.sessions.get_game(config.game_id)
    wolf = next(pid for pid, p in session.truth_state.players.items() if p.role == Role.WEREWOLF)
    context = supervisor._context.build_context(config.game_id, wolf, Phase.NIGHT_WEREWOLF)

    raw = {"game_id": "OTHER_GAME", "action_type": "foo"}  # 既 schema-invalid 又错局
    with pytest.raises(ValueError):
        supervisor.validate_or_fallback(config.game_id, raw, context, actor_id=wolf)


def test_validate_or_fallback_schema_invalid_reraises_when_no_recoverable_actor():
    """无法兜底（无 engine/合法 actor）时，schema-invalid 仍照常抛——这是集成错误而非脏数据。"""
    config, supervisor, _agent, _sink = _build("game_config_6p_debug.json")
    raw = {"action_type": "foo"}  # schema-invalid
    with pytest.raises(ValidationError):
        supervisor.validate_or_fallback(config.game_id, raw)  # 无 actor_id / 无 context


def test_run_phase_records_rule_validation_and_fallback_used_for_invalid_agent_action():
    config, supervisor, agent, sink = _build("game_config_6p_debug.json")
    original_act = agent.act

    async def invalid_act(context: dict) -> dict:
        await original_act(context)
        return {
            "game_id": context["game_id"],
            "agent_id": context["agent_id"],
            "role": context["role"],
            "phase": context["phase"],
            "action_type": "speak",
            "target": None,
        }

    agent.act = invalid_act

    events = asyncio.run(supervisor.run_phase(config.game_id))

    assert any(e.event_type == EventType.RULE_VALIDATION for e in events)
    assert any(e.event_type == EventType.FALLBACK_USED for e in events)
    assert sink.events == events


def test_run_game_terminates_at_game_over_6p():
    """A2：run_game 跑完整局并在 max_rounds 收敛到 GAME_OVER（不死循环）。"""
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")
    result = asyncio.run(supervisor.run_game(config.game_id))

    assert result is None  # 与 Interface §3 一致
    session = supervisor._engine.sessions.get_game(config.game_id)
    assert session.current_phase == Phase.GAME_OVER
    assert session.round <= config.max_rounds  # A4 后由真实胜负提前收敛
    assert any(e.event_type == EventType.GAME_OVER for e in sink.events)
    assert len(sink.events) > 0  # 整局确实产出了事件


def test_run_game_emits_role_assigned_anchor_without_role_mapping():
    """P2 方案A：开局发一条 role_assigned 锚点。

    - 它是整局第一条事件；
    - payload 只含公开设置信息（人数 + 角色数量分布），与 GameConfig 一致；
    - 绝不含 pid→role 真实映射（信息隔离红线）——payload 键被严格限定。
    """
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")
    asyncio.run(supervisor.run_game(config.game_id))

    role_events = [e for e in sink.events if e.event_type == EventType.ROLE_ASSIGNED]
    assert len(role_events) == 1
    ev = role_events[0]
    assert sink.events[0] is ev  # 开局第一条
    # 严格：payload 只有公开设定，绝不含 pid→role，也绝不含可反推身份的 seed
    assert set(ev.payload.keys()) == {"player_count", "role_counts"}
    assert ev.payload["player_count"] == config.player_count
    assert ev.payload["role_counts"] == config.roles.model_dump()

    blob = json.dumps(ev.model_dump(mode="json"))
    for forbidden in ("role_map", "hidden_roles", "truth_state"):
        assert forbidden not in blob


def test_run_game_on_finished_session_does_not_pollute_timeline():
    """P1 修复：对已结束的 session 再次 run_game，不得追加任何事件。

    否则会在终局 game_over 之后又冒出一条 role_assigned，审计时间线被污染。
    """
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")
    asyncio.run(supervisor.run_game(config.game_id))
    events_after_first = list(sink.events)

    asyncio.run(supervisor.run_game(config.game_id))  # 终局后再次调用

    assert sink.events == events_after_first  # 没有新增任何事件
    assert sum(1 for e in sink.events if e.event_type == EventType.ROLE_ASSIGNED) == 1
    assert sink.events[-1].event_type == EventType.GAME_OVER  # game_over 仍是时间线末尾


def test_run_game_emits_phase_started_timeline():
    """P2：每进入一个 phase 发一条 phase_started，作为 replay 的阶段时间轴锚点。

    - 第一条 phase_started 是 NIGHT_WEREWOLF / round 1（开局锚点）；
    - 终局 GAME_OVER 不发 phase_started（循环在它之前返回），由 game_over 事件收尾；
    - phase_started 是观测事件，actor 恒为 None。
    """
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")
    asyncio.run(supervisor.run_game(config.game_id))

    phase_started = [e for e in sink.events if e.event_type == EventType.PHASE_STARTED]
    assert phase_started, "整局应至少产出一条 phase_started"
    assert phase_started[0].phase == Phase.NIGHT_WEREWOLF
    assert phase_started[0].round == 1
    assert all(e.actor is None for e in phase_started)
    assert all(e.phase != Phase.GAME_OVER for e in phase_started)


def test_run_game_skips_dead_role_phase_and_continues():
    """P2-1：死亡角色阶段(NIGHT_SEER)被跳过后，run_game 仍能推进到 GAME_OVER。"""
    config, supervisor, _agent, sink = _build("game_config_6p_debug.json")
    session = supervisor._engine.sessions.get_game(config.game_id)
    for p in session.truth_state.players.values():
        if p.role == Role.SEER:
            p.status = PlayerStatus.DEAD

    asyncio.run(supervisor.run_game(config.game_id))

    assert session.current_phase == Phase.GAME_OVER  # 跳过死预言家阶段，整局未卡住
    seer_ids = {pid for pid, p in session.truth_state.players.items() if p.role == Role.SEER}
    actors_in_events = {e.actor for e in sink.events}
    assert not (seer_ids & actors_in_events)  # 死预言家从未作为 actor 行动
