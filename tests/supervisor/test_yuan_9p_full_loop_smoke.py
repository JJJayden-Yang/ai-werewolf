"""P4（Yuan）：9 人标准局完整 run_game smoke，真正走 hunter + 平票二次投票分支。

tests/game_core/ 里 hunter / tie-revote 是单元测试；这里让它们经过**完整 run_game 循环**：
- 9 人 config 含 hunter（6 人没有），猎人返回阶段（DAY_DISCUSSION / EXILE_LAST_WORDS）
  这条路径过去只被单测覆盖，没经过整局循环；
- 确定性"选第一个合法目标"不会产生平票，所以用**带种子的随机合法** mock：
  随机投票 → 出现平票；随机夜刀/放逐 → 猎人有时会死并开枪。
- 跑 100 局，断言 hunter / 平票分支至少各被走到一次，且全程无卡死 / 无非法 / 无 fallback。
"""

import asyncio
import json
import random
from pathlib import Path

from contracts import (
    ActionType,
    AgentContext,
    DeathCause,
    EventType,
    GameConfig,
    Phase,
    PlayerStatus,
    Role,
    VisiblePlayer,
)
from game_core import GameEngine, GameSessionManager, RuleValidator
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class NinePlayerSmokeContextAssembler:
    """9 人 smoke 装配器：用 A 真实接口（get_session + allowed_actions）拼 context，
    并按阶段给出合法目标（夜狼排除狼队友、二次投票限定 tie_candidates）。"""

    def __init__(self, engine: GameEngine) -> None:
        self._engine = engine

    def build_context(self, game_id: str, agent_id: str, phase: Phase) -> AgentContext:
        session = self._engine.get_session(game_id)
        ts = session.truth_state
        me = ts.players[agent_id]

        if phase == Phase.DAY_TIE_REVOTE:
            targets = [
                pid
                for pid in ts.round_state.tie_candidates
                if pid != agent_id and ts.players[pid].status == PlayerStatus.ALIVE
            ]
        else:
            targets = [
                pid
                for pid, p in ts.players.items()
                if p.status == PlayerStatus.ALIVE
                and pid != agent_id
                and not (phase == Phase.NIGHT_WEREWOLF and p.role == Role.WEREWOLF)
            ]

        return AgentContext(
            game_id=game_id,
            agent_id=agent_id,
            role=me.role,
            round=session.round,
            phase=phase,
            tie_candidates=ts.round_state.tie_candidates,
            visible_players=[
                VisiblePlayer(player_id=pid, status=p.status, public_claim=p.public_claim)
                for pid, p in ts.players.items()
            ],
            allowed_actions=list(RuleValidator.allowed_actions(phase)),
            rule_hints={"legal_targets": targets},
        )


class RandomLegalSmokeAgent:
    """带种子的随机合法 mock：从 context 给的合法目标里随机选，制造平票/多样死亡。

    女巫一律 skip（保证夜刀落地、猎人有机会被刀），猎人有目标就开枪（确保 shoot 分支被走）。
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    async def act(self, context: dict) -> dict:
        phase = Phase(context["phase"])
        targets = context.get("rule_hints", {}).get("legal_targets") or []
        chosen = self._rng.choice(targets) if targets else None

        action = {
            "game_id": context["game_id"],
            "agent_id": context["agent_id"],
            "role": context["role"],
            "phase": context["phase"],
            "action_type": ActionType.SPEAK,
            "target": None,
            "public_message": None,
        }

        if phase == Phase.NIGHT_WEREWOLF:
            action["action_type"] = ActionType.NIGHT_KILL_NOMINATE
            action["target"] = chosen
        elif phase == Phase.NIGHT_SEER:
            action["action_type"] = ActionType.CHECK
            action["target"] = chosen
        elif phase == Phase.NIGHT_WITCH:
            action["action_type"] = ActionType.SKIP
        elif phase in (Phase.DAY_VOTE, Phase.DAY_TIE_REVOTE):
            action["action_type"] = ActionType.VOTE
            action["target"] = chosen
        elif phase == Phase.HUNTER_SHOOT:
            action["action_type"] = ActionType.HUNTER_SHOOT
            action["target"] = chosen  # 有目标就开枪，None 则 pass
        elif phase in (Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION):
            action["public_message"] = "我先听听大家的发言和投票。"
        elif phase == Phase.EXILE_LAST_WORDS:
            action["public_message"] = "这是我的遗言。"

        return action


class _ListSink:
    def __init__(self) -> None:
        self.events: list = []

    def append_many(self, events) -> None:
        self.events.extend(events)


def _run_9p_game(seed: int, game_id: str):
    config_data = json.loads((FIXTURES / "game_config_9p_mvp.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)
    sink = _ListSink()
    agent = RandomLegalSmokeAgent(random.Random(10_000 + seed))
    supervisor = Supervisor(engine, NinePlayerSmokeContextAssembler(engine), agent, sink)

    asyncio.run(supervisor.run_game(game_id))
    return config, engine.get_session(game_id), sink.events


def _hunter_return_path(events) -> tuple[str | None, Phase | None]:
    """从事件流还原猎人开枪路径。

    返回 (触发开枪的 death_cause, HUNTER_SHOOT 之后的返回 phase)。
    - death_cause 取自带 hunter_can_shoot=True 的 death_confirmed（一局至多一次）；
    - 返回 phase 取 phase_started 序列里 HUNTER_SHOOT 的下一个；若 HUNTER_SHOOT 是最后一个
      phase_started（开枪即终局，无返回），返回 None。
    无猎人开枪则 (None, None)。
    """
    trigger: str | None = None
    for e in events:
        if e.event_type == EventType.DEATH_CONFIRMED and e.payload.get("hunter_can_shoot") is True:
            trigger = e.payload.get("death_cause")
    phases = [e.phase for e in events if e.event_type == EventType.PHASE_STARTED]
    return_phase: Phase | None = None
    if Phase.HUNTER_SHOOT in phases:
        i = phases.index(Phase.HUNTER_SHOOT)
        return_phase = phases[i + 1] if i + 1 < len(phases) else None
    return trigger, return_phase


def _entered_tie_revote(events) -> bool:
    """是否真正进入了二次投票（而不仅仅出现 tie_detected）。"""
    return any(
        (e.event_type == EventType.PHASE_STARTED and e.phase == Phase.DAY_TIE_REVOTE)
        or (e.event_type == EventType.VOTE_CAST and e.phase == Phase.DAY_TIE_REVOTE)
        for e in events
    )


def test_9p_single_game_runs_to_game_over_replay_serializable():
    config, session, events = _run_9p_game(seed=0, game_id="yuan_9p_single")
    assert session.current_phase == Phase.GAME_OVER
    assert session.round <= config.max_rounds
    assert any(e.event_type == EventType.GAME_OVER for e in events)
    json.dumps([e.model_dump(mode="json") for e in events])  # replay 可序列化


def test_9p_100_games_smoke_walks_complete_hunter_and_tie_revote_paths():
    """100 局健壮性 + 硬断言两条猎人返回路径与二次投票被完整 run_game 消费。

    不只统计"出现过 HUNTER_SHOT/TIE_DETECTED"，而是证明 engine.py 设置的两个
    hunter_shoot_return_phase 真的经整局循环被消费、平票真的进了二次投票。
    """
    completed = stuck = illegal = fallback = 0
    nightkill_to_discussion = exile_to_last_words = tie_revote_entered = 0
    rounds: list[int] = []
    winners: dict = {}

    for seed in range(100):
        config, session, events = _run_9p_game(seed=seed, game_id=f"yuan_9p_{seed:03d}")
        if session.current_phase == Phase.GAME_OVER:
            completed += 1
        else:
            stuck += 1
        illegal += sum(1 for e in events if e.event_type == EventType.RULE_VALIDATION)
        fallback += sum(1 for e in events if e.event_type == EventType.FALLBACK_USED)
        rounds.append(session.round)

        trigger, return_phase = _hunter_return_path(events)
        if trigger == DeathCause.NIGHT_KILL.value and return_phase == Phase.DAY_DISCUSSION:
            nightkill_to_discussion += 1
        if trigger == DeathCause.EXILE.value and return_phase == Phase.EXILE_LAST_WORDS:
            exile_to_last_words += 1
        if _entered_tie_revote(events):
            tie_revote_entered += 1

        winner = next(
            (e.payload.get("winner") for e in reversed(events) if e.event_type == EventType.GAME_OVER),
            "NONE",
        )
        winners[winner] = winners.get(winner, 0) + 1

    # 健壮性
    assert completed == 100, f"有局未跑到 GAME_OVER: stuck={stuck}"
    assert stuck == 0
    assert illegal == 0, f"出现非法动作事件: {illegal}"
    assert fallback == 0, f"出现 fallback 事件: {fallback}"
    assert all(r <= config.max_rounds for r in rounds)
    assert sum(winners.values()) == 100

    # 完整路径硬断言（均经过 phase_started 序列验证返回阶段，而非仅出现触发事件）
    assert nightkill_to_discussion >= 1, (
        "夜刀死的猎人 HUNTER_SHOOT->DAY_DISCUSSION 完整返回路径从未被走到"
    )
    assert exile_to_last_words >= 1, (
        "放逐死的猎人 HUNTER_SHOOT->EXILE_LAST_WORDS 完整返回路径从未被走到"
    )
    assert tie_revote_entered >= 1, "平票后从未真正进入 DAY_TIE_REVOTE（仅 tie_detected 不算）"
