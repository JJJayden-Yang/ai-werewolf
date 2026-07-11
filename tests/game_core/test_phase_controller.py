"""A2：PhaseController 状态机——线性流程、平票分支、死亡跳过、终局收敛。"""

import json
import random
from pathlib import Path

from contracts import (
    ActionType,
    AgentAction,
    EventType,
    GameConfig,
    GameEvent,
    Phase,
    PlayerStatus,
    Role,
)
from game_core import GameSessionManager, PhaseController
from game_core.action_resolver import ActionResolver
from game_core.event_emitter import EventEmitter

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
PC = PhaseController()


def _session(config_name: str = "game_config_6p_debug.json", seed: int = 0):
    cfg = GameConfig.model_validate(json.loads((FIXTURES / config_name).read_text(encoding="utf-8")))
    return GameSessionManager(rng=random.Random(seed)).create_game(cfg)


def _at(session, phase: Phase) -> Phase:
    session.truth_state.phase = phase
    return PC.next_phase(session, [])


def test_night_sequence():
    s = _session()
    assert _at(s, Phase.NIGHT_WEREWOLF) == Phase.NIGHT_SEER
    assert _at(s, Phase.NIGHT_SEER) == Phase.NIGHT_WITCH
    assert _at(s, Phase.NIGHT_WITCH) == Phase.DAY_ANNOUNCEMENT


def test_day_sequence_no_tie_no_hunter():
    s = _session()
    assert _at(s, Phase.DAY_ANNOUNCEMENT) == Phase.DAY_DISCUSSION
    assert _at(s, Phase.DAY_DISCUSSION) == Phase.DAY_VOTE
    assert _at(s, Phase.DAY_VOTE) == Phase.EXILE_RESOLUTION
    assert _at(s, Phase.EXILE_RESOLUTION) == Phase.EXILE_LAST_WORDS
    assert _at(s, Phase.EXILE_LAST_WORDS) == Phase.WIN_CHECK


def test_win_check_continues_then_ends_at_max_rounds():
    s = _session()  # round=1, max_rounds=8
    assert _at(s, Phase.WIN_CHECK) == Phase.NIGHT_WEREWOLF
    s.truth_state.round = s.config.max_rounds
    assert _at(s, Phase.WIN_CHECK) == Phase.GAME_OVER


def test_tie_branch_triggered_by_tie_detected_event():
    s = _session()
    s.truth_state.phase = Phase.DAY_VOTE
    tie = GameEvent(
        event_id="e1", game_id=s.game_id, round=1, phase=Phase.DAY_VOTE, event_type="tie_detected"
    )
    assert PC.next_phase(s, [tie]) == Phase.DAY_TIE_DISCUSSION
    # 二次投票仍平票 → 无人出局
    s.truth_state.phase = Phase.DAY_TIE_REVOTE
    second_tie = GameEvent(
        event_id="e2",
        game_id=s.game_id,
        round=1,
        phase=Phase.DAY_TIE_REVOTE,
        event_type="no_exile_due_to_second_tie",
    )
    assert PC.next_phase(s, [second_tie]) == Phase.NO_EXILE_RESOLUTION
    # 二次投票分出最高票（无 tie 事件）→ 放逐
    s.truth_state.phase = Phase.DAY_TIE_REVOTE
    assert PC.next_phase(s, []) == Phase.EXILE_RESOLUTION


def test_no_exile_goes_to_win_check():
    s = _session()
    assert _at(s, Phase.NO_EXILE_RESOLUTION) == Phase.WIN_CHECK


def test_dead_seer_skips_night_seer():
    s = _session()
    for p in s.truth_state.players.values():
        if p.role == Role.SEER:
            p.status = PlayerStatus.DEAD
    assert PC.get_required_actors(s, Phase.NIGHT_SEER) == []
    assert PC.should_skip_phase(s, Phase.NIGHT_SEER) is True


def test_should_skip_distinguishes_actor_and_system_phases():
    s = _session()
    assert PC.should_skip_phase(s, Phase.NIGHT_WEREWOLF) is False  # 有存活狼
    assert PC.should_skip_phase(s, Phase.DAY_ANNOUNCEMENT) is True  # 纯结算阶段无 agent


def test_exile_last_words_actor_follows_last_exiled_player():
    s = _session()  # 6p fixture: last_words_enabled = True
    assert PC.get_required_actors(s, Phase.EXILE_LAST_WORDS) == []  # A4 未设置 → 跳过

    pid = next(iter(s.truth_state.players))
    s.truth_state.round_state.last_exiled_player = pid
    assert PC.get_required_actors(s, Phase.EXILE_LAST_WORDS) == [pid]

    s.truth_state.round_state.last_words_done = True
    assert PC.get_required_actors(s, Phase.EXILE_LAST_WORDS) == []  # 已发表遗言 → 不再触发


def test_last_words_available_every_round_not_just_first_exile():
    """端到端（resolver + phase_controller）：每一轮被放逐者都重新获得遗言机会。

    回归：旧实现中 last_words_done 一旦置 True 永不复位，第二轮起 EXILE_LAST_WORDS 被
    `not last_words_done` 守卫永久跳过——只有第一轮出局者能发遗言。
    """
    s = _session()  # 6p fixture: last_words_enabled = True
    resolver = ActionResolver(EventEmitter())
    pids = list(s.truth_state.players)
    victim1, victim2 = pids[0], pids[1]

    def _last_words(pid: str) -> AgentAction:
        return AgentAction(
            game_id=s.game_id,
            agent_id=pid,
            role=s.truth_state.players[pid].role,
            phase=Phase.EXILE_LAST_WORDS,
            action_type=ActionType.SPEAK,
            public_message="my last words",
        )

    # —— 第一轮：放逐 victim1，发表遗言 ——
    s.truth_state.phase = Phase.EXILE_RESOLUTION
    s.truth_state.round_state.last_exiled_player = victim1
    resolver.resolve_exile(s)
    s.truth_state.phase = Phase.EXILE_LAST_WORDS
    assert PC.get_required_actors(s, Phase.EXILE_LAST_WORDS) == [victim1]
    resolver.resolve_last_words(s, _last_words(victim1))
    assert PC.get_required_actors(s, Phase.EXILE_LAST_WORDS) == []  # 本轮已发表，不重复

    # —— 第二轮：放逐 victim2，必须重新获得遗言机会（核心修复点）——
    s.truth_state.phase = Phase.EXILE_RESOLUTION
    s.truth_state.round_state.last_exiled_player = victim2
    resolver.resolve_exile(s)
    s.truth_state.phase = Phase.EXILE_LAST_WORDS
    assert PC.get_required_actors(s, Phase.EXILE_LAST_WORDS) == [victim2]
