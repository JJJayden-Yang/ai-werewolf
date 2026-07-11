"""A2（Yuan）：狼人 9 人 baseline —— 真实 context 下 3 狼收敛同一目标、互不刀。

确认现有 W1-W4 狼策略天然处理 3 狼（不重写、不加 docx 高级策略）：
- 3 狼经真实 ContextAssembler 各自决策，收敛到同一刀口；
- 任何狼都不提名狼队友；
- 有公开跳预言家时 3 狼都刀预言家；无则都取第一个存活非狼；
- 收敛后经 Engine resolve_wolf_nomination 落成单一 kill_target，无 RULE_VALIDATION。
"""

import json
import random
from pathlib import Path

from agent_policy.roles.werewolf import WerewolfStrategy
from contracts import EventType, GameConfig, Phase, Role
from context.context_assembler import ContextAssembler
from game_core import GameEngine, GameSessionManager
from stores.event_store import InMemoryEventStore

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def _make_9p(seed: int, game_id: str) -> GameEngine:
    config_data = json.loads((FIXTURES / "game_config_9p_mvp.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)
    return engine


def _wolves(engine: GameEngine, gid: str) -> list[str]:
    players = engine.get_session(gid).truth_state.players
    return sorted(pid for pid, p in players.items() if p.role == Role.WEREWOLF)


def _assembler_with_setup(engine: GameEngine, gid: str) -> ContextAssembler:
    store = InMemoryEventStore()
    store.append(engine.emit_role_assigned(gid))
    store.append_many(engine.emit_wolf_teammates(gid))  # 狼队友名单（W1）
    return ContextAssembler(session_provider=engine, event_store=store)


def _wolf_nominations(engine, gid, assembler) -> dict[str, str]:
    """每只狼经真实 context 决策，返回 {wolf_id: nominated_target}。"""
    out: dict[str, str] = {}
    for wolf_id in _wolves(engine, gid):
        ctx = assembler.build_context(gid, wolf_id, Phase.NIGHT_WEREWOLF)
        action = WerewolfStrategy().decide(ctx)
        assert action.target is not None, f"狼 {wolf_id} 夜刀产出空目标"
        out[wolf_id] = action.target
    return out


def test_three_wolves_converge_first_night_and_never_target_teammate():
    gid = "yuan_wolf_9p_n1"
    engine = _make_9p(seed=0, game_id=gid)
    wolves = set(_wolves(engine, gid))
    assert len(wolves) == 3  # 9 人 = 3 狼

    noms = _wolf_nominations(engine, gid, _assembler_with_setup(engine, gid))

    # 收敛：3 狼提名同一目标
    assert len(set(noms.values())) == 1, f"3 狼未收敛: {noms}"
    target = next(iter(noms.values()))
    # 互不刀：目标非狼
    assert target not in wolves


def test_three_wolves_all_target_public_claimed_seer():
    gid = "yuan_wolf_9p_seer"
    engine = _make_9p(seed=0, game_id=gid)
    wolves = set(_wolves(engine, gid))
    ts = engine.get_session(gid).truth_state

    # 模拟某真预言家白天跳了身份（public_claim 在白天发言时被置）
    seer_id = next(pid for pid, p in ts.players.items() if p.role == Role.SEER)
    ts.players[seer_id].public_claim = Role.SEER.value

    noms = _wolf_nominations(engine, gid, _assembler_with_setup(engine, gid))

    assert set(noms.values()) == {seer_id}, f"3 狼未集中刀公开预言家: {noms}"
    assert seer_id not in wolves


def test_converged_nominations_resolve_to_single_kill_no_rule_validation():
    """收敛的 3 份提名经 Engine 结算 → 单一 kill_target，无非法。"""
    gid = "yuan_wolf_9p_resolve"
    engine = _make_9p(seed=0, game_id=gid)
    assembler = _assembler_with_setup(engine, gid)
    noms = _wolf_nominations(engine, gid, assembler)

    # 构造 3 狼的 AgentAction 喂给引擎（当前 phase 已是 NIGHT_WEREWOLF）
    from contracts import ActionType, AgentAction

    actions = [
        AgentAction(
            game_id=gid,
            agent_id=wolf_id,
            role=Role.WEREWOLF,
            phase=Phase.NIGHT_WEREWOLF,
            action_type=ActionType.NIGHT_KILL_NOMINATE,
            target=target,
        )
        for wolf_id, target in noms.items()
    ]
    events = engine.apply_actions(gid, actions)

    assert not any(e.event_type == EventType.RULE_VALIDATION for e in events)
    assert engine.get_session(gid).truth_state.night_state.kill_target == next(iter(noms.values()))
