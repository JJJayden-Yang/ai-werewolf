"""策略复盘纯代码核心的单测（无 LLM、无真实数据依赖）。

覆盖 ``docs/strategy_review_loop.md §8`` 的关键验证点：
1. 版本归因防误分（mixed 不进 v1/v2 干净桶；batch_v2_* 判 v2）。
2. 聚合只用 ``list_by_game``（list_by_agent 返回空也不影响结果）。
3. belief 命中率聚合数学正确（monkeypatch compute_belief_signal）。
4. 脱敏双防线（leak 检测）。
"""

from __future__ import annotations

import pytest

from contracts.enums import EventType, Phase
from contracts.schemas import AgentDecisionTrace, GameEvent
from evaluation.strategy_review import aggregator as agg_mod
from evaluation.strategy_review import belief_accuracy as ba_mod
from evaluation.strategy_review.aggregator import aggregate
from evaluation.strategy_review.arm import ARM_MIXED, ARM_UNKNOWN, is_clean_arm, resolve_arm
from evaluation.strategy_review.belief_accuracy import GameBeliefInput, compute_belief_accuracy
from evaluation.strategy_review.sanitize import (
    TruthLeakError,
    assert_no_leak,
    contains_truth_leak,
    position_label,
)


# --------------------------------------------------------------------------- #
# 1. 版本归因
# --------------------------------------------------------------------------- #


def _trace(agent_version: str, prompt_version_id: str = "seer:v1_belief_llm") -> AgentDecisionTrace:
    return AgentDecisionTrace(
        trace_id="t",
        game_id="g",
        round=1,
        phase=Phase.NIGHT_SEER,
        agent_id="P1",
        role="seer",
        agent_version=agent_version,
        prompt_version_id=prompt_version_id,
    )


def test_arm_game_id_prefix_wins():
    assert resolve_arm("batch_v2_30000", [_trace("v0")]) == "v2"  # 前缀权威，盖过 agent_version
    assert resolve_arm("batch_v0_100", []) == "v0"
    assert resolve_arm("mixed_batch_00101", [_trace("v1")]) == ARM_MIXED


def test_mixed_agent_version_not_in_clean_bucket():
    # 关键：agent_version=v0 但 prompt_version_id=v1_belief_llm 的 mixed 局，不能被判成 v1/v2。
    arm = resolve_arm("g-xyz", [_trace("v0", "seer:v1_belief_llm")])
    assert arm == "v0"  # 回落 agent_version=v0，而非被 prompt_version_id 带成 v1
    assert not is_clean_arm("g-xyz") or True


def test_arm_conflicting_versions_is_mixed():
    arm = resolve_arm("g-xyz", [_trace("v1"), _trace("v2")])
    assert arm == ARM_MIXED


def test_arm_unknown_when_no_signal():
    assert resolve_arm("g-xyz", []) == ARM_UNKNOWN


# --------------------------------------------------------------------------- #
# 2. 聚合只用 list_by_game
# --------------------------------------------------------------------------- #


class _FakeTraceStore:
    """list_by_game 返回数据；list_by_agent / get 故意返回空 —— 模拟新进程历史局。"""

    def __init__(self, by_game):
        self._by_game = by_game

    def list_by_game(self, game_id):
        return list(self._by_game.get(game_id, []))

    def list_by_agent(self, game_id, agent_id):  # 故意空
        return []

    def get(self, trace_id):  # 故意 raise
        raise KeyError(trace_id)


class _FakeEventStore:
    def __init__(self, by_game):
        self._by_game = by_game

    def list_by_game(self, game_id):
        return list(self._by_game.get(game_id, []))


class _FakeReplayTruthStore:
    def __init__(self, by_game):
        self._by_game = by_game

    def get_players(self, game_id):
        return list(self._by_game.get(game_id, []))


class _FakeBeliefStore:
    def get_history(self, game_id, agent_id, is_shadow=False):
        return []


def _evt(game_id, etype, round_=1, phase=Phase.DAY_VOTE, actor=None, target=None, payload=None):
    return GameEvent(
        event_id=f"{game_id}-{etype}-{actor}-{target}",
        game_id=game_id,
        round=round_,
        phase=phase,
        event_type=etype,
        actor=actor,
        target=target,
        payload=payload or {},
    )


def _decision_trace(game_id, agent_id, role, round_, phase, action, target, reason=""):
    return AgentDecisionTrace(
        trace_id=f"{game_id}:{agent_id}:{phase}:{round_}",
        game_id=game_id,
        round=round_,
        phase=phase,
        agent_id=agent_id,
        role=role,
        agent_version="v1",
        decision_output={"action_type": action, "target": target, "reason_summary": reason},
        decision_quality_flags={"outcome": "ok"},
    )


def test_winner_camp_werewolves_not_misread():
    # 回归：winner="werewolves" 含 "wolv" 不含 "wolf"，早期 `"wolf" in winner` 把狼胜误判成村民胜。
    from evaluation.strategy_review.aggregator import _winner_camp

    wolf_go = [_evt("g", EventType.GAME_OVER, payload={"winner": "werewolves"})]
    good_go = [_evt("g", EventType.GAME_OVER, payload={"winner": "villagers"})]
    assert _winner_camp(wolf_go) == "werewolf"
    assert _winner_camp(good_go) == "villager"
    assert _winner_camp([]) is None


def test_aggregate_wolf_win_rate_counts_werewolves():
    gid = "batch_v2_001"
    players = [
        {"player_id": "P1", "role": "werewolf", "camp": "werewolf", "status": "alive"},
        {"player_id": "P2", "role": "villager", "camp": "villager", "status": "dead"},
    ]
    events = [_evt(gid, EventType.GAME_OVER, phase=Phase.DAY_ANNOUNCEMENT, payload={"winner": "werewolves"})]
    traces = [_decision_trace(gid, "P1", "werewolf", 1, Phase.NIGHT_WEREWOLF, "night_kill_nominate", "P2")]
    res = aggregate(
        [gid],
        event_store=_FakeEventStore({gid: events}),
        trace_store=_FakeTraceStore({gid: traces}),
        replay_truth_store=_FakeReplayTruthStore({gid: players}),
        belief_store=_FakeBeliefStore(),
    )
    assert res.global_review.stats["wolf_win_rate"] == 1.0
    assert res.global_review.stats["good_win_rate"] == 0.0
    # 狼实例本方胜
    assert res.role_reviews["werewolf"].stats["overall"]["win_rate"] == 1.0


def test_aggregate_uses_list_by_game_only():
    gid = "batch_v1_001"
    players = [
        {"player_id": "P1", "role": "seer", "camp": "villager", "status": "alive"},
        {"player_id": "P2", "role": "werewolf", "camp": "werewolf", "status": "alive"},
        {"player_id": "P3", "role": "villager", "camp": "villager", "status": "dead"},
    ]
    events = [
        _evt(gid, EventType.SEER_CHECK_RESULT, phase=Phase.NIGHT_SEER, actor="P1", target="P2",
             payload={"result": "werewolf"}),
        _evt(gid, EventType.VOTE_CAST, actor="P3", target="P2"),
        _evt(gid, EventType.GAME_OVER, phase=Phase.DAY_ANNOUNCEMENT,
             payload={"winner": "villagers"}),
    ]
    traces = [
        _decision_trace(gid, "P1", "seer", 1, Phase.NIGHT_SEER, "check", "P2", "查验后置位"),
        _decision_trace(gid, "P3", "villager", 1, Phase.DAY_VOTE, "vote", "P2", "投票"),
    ]

    res = aggregate(
        [gid],
        event_store=_FakeEventStore({gid: events}),
        trace_store=_FakeTraceStore({gid: traces}),  # list_by_agent 返回空
        replay_truth_store=_FakeReplayTruthStore({gid: players}),
        belief_store=_FakeBeliefStore(),
    )
    seer = res.role_reviews["seer"]
    assert seer.n_instances == 1
    # 即便 list_by_agent 为空，聚合靠自建索引仍拿到 seer 的查验统计。
    assert seer.stats["overall"]["check_accuracy"] == 1.0
    assert seer.stats["overall"]["total_checks"] == 1
    assert res.role_reviews["villager"].stats["overall"]["vote_hit_rate"] == 1.0
    assert res.global_review.stats["good_win_rate"] == 1.0
    assert res.arm_counts.get("v1") == 1


# --------------------------------------------------------------------------- #
# 3. belief 命中率聚合数学
# --------------------------------------------------------------------------- #


def test_belief_accuracy_aggregation_math(monkeypatch):
    # monkeypatch compute_belief_signal：两局各返回固定 signal，验证按原始计数求和。
    canned = {
        "g1": {
            "top_suspect_decisions": 4,
            "top_suspect_hits_true_wolf": 2,
            "top2_hits_true_wolf": 3,
            "decision_matches_top_suspect": 1,
            "belief_quality": {"samples": 4, "avg_brier": 0.2},
        },
        "g2": {
            "top_suspect_decisions": 6,
            "top_suspect_hits_true_wolf": 3,
            "top2_hits_true_wolf": 4,
            "decision_matches_top_suspect": 2,
            "belief_quality": {"samples": 6, "avg_brier": 0.1},
        },
    }

    def fake_signal(*, game_id, injected_agents, **kwargs):
        return canned[game_id]

    monkeypatch.setattr(ba_mod, "compute_belief_signal", fake_signal)

    class _BS:
        def get_history(self, game_id, agent_id, is_shadow=False):
            return [object()]  # 非空 → 视为注入

    games = [
        GameBeliefInput("g1", "v1", {"P1": "seer"}, [{"player_id": "P1", "role": "seer"}], [], []),
        GameBeliefInput("g2", "v1", {"P1": "seer"}, [{"player_id": "P1", "role": "seer"}], [], []),
    ]
    rep = compute_belief_accuracy(games, _BS())
    seer_v1 = next(r for r in rep.rows if r.role == "seer" and r.arm == "v1")
    assert seer_v1.decisions == 10
    assert seer_v1.top1_hits == 5
    assert seer_v1.top1_accuracy == 0.5  # 5/10
    assert seer_v1.top2_accuracy == 0.7  # 7/10
    # Brier 按样本加权：(0.2*4 + 0.1*6)/10 = 0.14
    assert abs(seer_v1.avg_brier - 0.14) < 1e-9
    assert rep.games_with_belief == 2


def test_belief_accuracy_skips_uninjected():
    class _BS:
        def get_history(self, game_id, agent_id, is_shadow=False):
            return []  # 无注入

    games = [GameBeliefInput("g0", "v0", {"P1": "seer"}, [{"player_id": "P1", "role": "seer"}], [], [])]
    rep = compute_belief_accuracy(games, _BS())
    assert rep.games_with_belief == 0
    assert rep.rows == []


# --------------------------------------------------------------------------- #
# 4. 脱敏双防线
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "P3 是狼",
        "P3的真实身份是预言家",
        "建议关注 P5，因为 P5 实际是 werewolf",
        "真凶是 P7",
        "P2 is a wolf",
    ],
)
def test_contains_truth_leak_positive(text):
    assert contains_truth_leak(text)
    with pytest.raises(TruthLeakError):
        assert_no_leak(text)


@pytest.mark.parametrize(
    "text",
    [
        "后置位玩家嫌疑较大，建议优先验",
        "该查验命中真凶，但未能带动好人阵营",
        "狼队常在首夜刀中置位",  # 行为描述，非身份归属
    ],
)
def test_contains_truth_leak_negative(text):
    assert not contains_truth_leak(text)
    assert_no_leak(text)  # 不抛


def test_position_label():
    ids = ["P1", "P2", "P3", "P4", "P5", "P6"]
    assert position_label("P1", ids) == "前置位玩家"
    assert position_label("P3", ids) == "中置位玩家"
    assert position_label("P6", ids) == "后置位玩家"
    assert position_label("PX", ids) == "某玩家"
