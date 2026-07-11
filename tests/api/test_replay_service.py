"""ReplayData 装配纯函数测试（C / S2）。

覆盖：

- 单独 EventStore（无 SessionProvider）能装出 game_id/events/timeline，
  players 留空。
- 单独 SessionProvider（无 events）能装出 players。
- 两者齐全的完整 happy path。
- timeline 字段顺序、字段集合。
- v1 / S10 字段保持 schema 默认空值。
- 未知 game_id（events 空 + session 也拿不到）抛 ReplayNotFoundError。
- SessionProvider 抛异常时优雅降级（players 留空，不传播给端点）。
"""

from __future__ import annotations

import pytest

from contracts import EventType, Phase, Role, Visibility
from contracts.schemas import ReplayData
from stores.event_store import InMemoryEventStore
from stores.replay_truth_store import InMemoryReplayTruthStore
from stores.trace_store import InMemoryTraceStore
from tests.context.conftest import FakeSessionProvider, make_6p_session, make_event

from api.replay_service import ReplayNotFoundError, assemble_replay, list_replay_summaries


# ---- happy path ----


def test_assemble_replay_returns_replay_data_instance():
    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001"))
    provider = FakeSessionProvider(make_6p_session(game_id="g001"))

    replay = assemble_replay("g001", event_store=store, session_provider=provider)

    assert isinstance(replay, ReplayData)
    assert replay.game_id == "g001"


def test_assemble_replay_populates_players_from_session():
    provider = FakeSessionProvider(make_6p_session(game_id="g001"))
    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001"))

    replay = assemble_replay("g001", event_store=store, session_provider=provider)

    pids = {p["player_id"] for p in replay.players}
    assert pids == {"P1", "P2", "P3", "P4", "P5", "P6"}
    # 字段集合（不依赖具体值）
    for player in replay.players:
        assert {"player_id", "role", "camp", "status", "vote_weight"} <= player.keys()


def test_assemble_replay_player_role_value_is_string():
    """role 字段应该是字符串而非枚举对象（dict 输出，且 JSON 友好）。"""
    provider = FakeSessionProvider(make_6p_session(game_id="g001"))
    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001"))

    replay = assemble_replay("g001", event_store=store, session_provider=provider)

    roles = {p["role"] for p in replay.players}
    assert Role.WEREWOLF.value in roles
    assert Role.SEER.value in roles
    # 不是枚举对象
    for player in replay.players:
        assert isinstance(player["role"], str)


def test_assemble_replay_events_match_event_store():
    store = InMemoryEventStore()
    e1 = make_event(EventType.SPEECH, game_id="g001", actor="P1")
    e2 = make_event(EventType.VOTE_CAST, game_id="g001", actor="P2", target="P3")
    e3 = make_event(EventType.EXILE, game_id="g001", target="P3")
    for e in (e1, e2, e3):
        store.append(e)

    replay = assemble_replay("g001", event_store=store)

    assert [e.event_id for e in replay.events] == [e1.event_id, e2.event_id, e3.event_id]


def test_assemble_replay_derives_players_from_post_game_events_without_session():
    store = InMemoryEventStore()
    events = [
        make_event(
            EventType.ROLE_ASSIGNED,
            game_id="g_derived",
            payload={
                "player_count": 6,
                "role_counts": {"werewolf": 2, "seer": 1, "witch": 1, "hunter": 0, "villager": 2},
            },
        ),
        make_event(
            EventType.WOLF_NOMINATION,
            game_id="g_derived",
            phase=Phase.NIGHT_WEREWOLF,
            actor="P3",
            target="P1",
            payload={"teammates": ["P3", "P4"]},
        ),
        make_event(
            EventType.WOLF_NOMINATION,
            game_id="g_derived",
            phase=Phase.NIGHT_WEREWOLF,
            actor="P4",
            target="P1",
        ),
        make_event(
            EventType.SEER_CHECK_RESULT,
            game_id="g_derived",
            phase=Phase.NIGHT_SEER,
            actor="P2",
            target="P3",
        ),
        make_event(
            EventType.AGENT_ACTION,
            game_id="g_derived",
            phase=Phase.NIGHT_WITCH,
            actor="P6",
            payload={"action_type": "skip"},
        ),
    ]
    for event in events:
        store.append(event)

    replay = assemble_replay("g_derived", event_store=store)

    roles = {player["player_id"]: player["role"] for player in replay.players}
    assert roles == {
        "P1": "villager",
        "P2": "seer",
        "P3": "werewolf",
        "P4": "werewolf",
        "P5": "villager",
        "P6": "witch",
    }
    assert {player["camp"] for player in replay.players if player["role"] == "werewolf"} == {"werewolf"}


def test_assemble_replay_prefers_persisted_truth_players_over_event_derivation():
    store = InMemoryEventStore()
    store.append(
        make_event(
            EventType.ROLE_ASSIGNED,
            game_id="g_truth",
            payload={
                "player_count": 6,
                "role_counts": {"werewolf": 2, "seer": 1, "witch": 1, "hunter": 0, "villager": 2},
            },
        )
    )
    truth_store = InMemoryReplayTruthStore()
    truth_store.save_players(
        "g_truth",
        [
            {
                "player_id": "P1",
                "role": "seer",
                "camp": "villager",
                "status": "alive",
                "public_claim": None,
                "vote_weight": 1.0,
            }
        ],
    )

    replay = assemble_replay("g_truth", event_store=store, replay_truth_store=truth_store)

    assert replay.players == truth_store.get_players("g_truth")


def test_assemble_replay_does_not_guess_ambiguous_passive_roles():
    store = InMemoryEventStore()
    events = [
        make_event(
            EventType.ROLE_ASSIGNED,
            game_id="g_ambiguous_hunter",
            payload={
                "player_count": 9,
                "role_counts": {"werewolf": 3, "seer": 1, "witch": 1, "hunter": 1, "villager": 3},
            },
        ),
        make_event(
            EventType.WOLF_NOMINATION,
            game_id="g_ambiguous_hunter",
            phase=Phase.NIGHT_WEREWOLF,
            actor="P1",
            target="P7",
            payload={"teammates": ["P1", "P2", "P3"]},
        ),
        make_event(
            EventType.SEER_CHECK_RESULT,
            game_id="g_ambiguous_hunter",
            phase=Phase.NIGHT_SEER,
            actor="P4",
            target="P1",
        ),
        make_event(
            EventType.AGENT_ACTION,
            game_id="g_ambiguous_hunter",
            phase=Phase.NIGHT_WITCH,
            actor="P5",
            payload={"action_type": "skip"},
        ),
    ]
    for event in events:
        store.append(event)

    replay = assemble_replay("g_ambiguous_hunter", event_store=store)

    assert replay.players == []


# ---- timeline ----


def test_timeline_preserves_event_order():
    store = InMemoryEventStore()
    events = [
        make_event(EventType.SPEECH, game_id="g001", round_num=1),
        make_event(EventType.VOTE_CAST, game_id="g001", round_num=1, actor="P1", target="P2"),
        make_event(EventType.EXILE, game_id="g001", round_num=1, target="P2"),
        make_event(EventType.NIGHT_KILL_ANNOUNCED, game_id="g001", round_num=2),
    ]
    for e in events:
        store.append(e)

    replay = assemble_replay("g001", event_store=store)

    assert [t["event_id"] for t in replay.timeline] == [e.event_id for e in events]
    assert [t["round"] for t in replay.timeline] == [1, 1, 1, 2]


def test_timeline_entry_has_expected_fields():
    store = InMemoryEventStore()
    e = make_event(
        EventType.VOTE_CAST,
        game_id="g001",
        round_num=2,
        phase=Phase.DAY_VOTE,
        actor="P1",
        target="P2",
    )
    store.append(e)

    replay = assemble_replay("g001", event_store=store)

    entry = replay.timeline[0]
    assert entry["event_id"] == e.event_id
    assert entry["round"] == 2
    assert entry["phase"] == Phase.DAY_VOTE.value
    assert entry["event_type"] == EventType.VOTE_CAST.value
    assert entry["actor"] == "P1"
    assert entry["target"] == "P2"
    assert entry["visibility"] == Visibility.PUBLIC.value


# ---- 边界与异常 ----


def test_assemble_replay_without_session_provider_leaves_players_empty():
    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001"))

    replay = assemble_replay("g001", event_store=store)

    assert replay.players == []
    assert len(replay.events) == 1


def test_assemble_replay_without_events_uses_session_for_players():
    """events 空但 session_provider 拿到 session：仍然合法（赛前/赛中状态）。"""
    store = InMemoryEventStore()
    provider = FakeSessionProvider(make_6p_session(game_id="g001"))

    replay = assemble_replay("g001", event_store=store, session_provider=provider)

    assert replay.events == []
    assert len(replay.players) == 6


def test_assemble_replay_v1_and_s10_fields_default_empty():
    """A 已同意：现阶段 belief_curves/deviation_points/bad_cases/evaluation_summary
    留 schema 默认空值；v1/S10 才填。"""
    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001"))

    replay = assemble_replay("g001", event_store=store)

    assert replay.belief_curves == []
    assert replay.deviation_points == []
    assert replay.bad_cases == []
    assert replay.evaluation_summary == {}


def test_assemble_replay_unknown_game_raises_not_found():
    store = InMemoryEventStore()
    # 没有 session_provider 也没有 events
    with pytest.raises(ReplayNotFoundError) as exc_info:
        assemble_replay("g_missing", event_store=store)
    assert exc_info.value.game_id == "g_missing"


def test_assemble_replay_unknown_game_with_provider_raises_not_found():
    """provider 也拿不到对应 session：仍然算 not found。"""
    store = InMemoryEventStore()
    provider = FakeSessionProvider(make_6p_session(game_id="g_other"))

    with pytest.raises(ReplayNotFoundError):
        assemble_replay("g_missing", event_store=store, session_provider=provider)


def test_assemble_replay_swallows_session_provider_exceptions():
    """SessionProvider 抛非 not-found 异常时，players 优雅降级为空 —— 至少 events
    要能正常输出，不让 provider 故障打穿端点。"""

    class FailingProvider:
        def get_session(self, game_id: str):  # noqa: ARG002
            raise RuntimeError("backend down")

    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001"))

    replay = assemble_replay("g001", event_store=store, session_provider=FailingProvider())

    assert replay.players == []
    assert len(replay.events) == 1


def test_replay_model_validate_json_roundtrip():
    """整个 ReplayData 必须能 JSON 序列化往返 —— 给 FastAPI response_model 用。"""
    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001"))
    provider = FakeSessionProvider(make_6p_session(game_id="g001"))

    replay = assemble_replay("g001", event_store=store, session_provider=provider)

    payload = replay.model_dump_json()
    rebuilt = ReplayData.model_validate_json(payload)
    assert rebuilt.game_id == replay.game_id
    assert len(rebuilt.players) == len(replay.players)
    assert len(rebuilt.events) == len(replay.events)


# ---- ReplaySummary ----


def test_list_replay_summaries_derives_core_key_event_tags_in_stable_order():
    store = InMemoryEventStore()
    events = [
        make_event(EventType.ROLE_ASSIGNED, game_id="g_tags", payload={"player_count": 9}),
        make_event(EventType.WITCH_POISON, game_id="g_tags"),
        make_event(EventType.HUNTER_SHOT, game_id="g_tags", target="P2"),
        make_event(EventType.TIE_DETECTED, game_id="g_tags"),
        make_event(EventType.NO_EXILE_DUE_TO_SECOND_TIE, game_id="g_tags"),
        make_event(EventType.WITCH_SAVE, game_id="g_tags"),
    ]
    for event in events:
        store.append(event)

    summaries = list_replay_summaries(store)

    assert summaries[0]["tags"] == ["平票", "二次平票", "猎人开枪", "女巫救人", "女巫毒人"]


def test_list_replay_summaries_does_not_tag_hunter_pass_as_shot():
    store = InMemoryEventStore()
    store.append(make_event(EventType.ROLE_ASSIGNED, game_id="g_pass", payload={"player_count": 9}))
    store.append(
        make_event(
            EventType.HUNTER_SHOT,
            game_id="g_pass",
            target=None,
            payload={"pass": True},
        )
    )

    summaries = list_replay_summaries(store)

    assert summaries[0]["tags"] == []


def test_list_replay_summaries_defaults_mode_to_mock_without_traces():
    store = InMemoryEventStore()
    store.append(make_event(EventType.ROLE_ASSIGNED, game_id="g_mock", payload={"player_count": 6}))

    summaries = list_replay_summaries(store)

    assert summaries[0]["mode"] == "Mock"


def test_list_replay_summaries_derives_llm_mode_from_trace_store():
    from contracts.schemas import AgentDecisionTrace

    event_store = InMemoryEventStore()
    trace_store = InMemoryTraceStore()
    for game_id, agent_version in (("g_v0", "v0"), ("g_v1", "v1"), ("g_v2", "v2")):
        event_store.append(
            make_event(EventType.ROLE_ASSIGNED, game_id=game_id, payload={"player_count": 6})
        )
        trace_store.append(
            AgentDecisionTrace(
                trace_id=f"trace_{game_id}",
                game_id=game_id,
                round=1,
                phase=Phase.DAY_DISCUSSION,
                agent_id="P1",
                role=Role.VILLAGER,
                agent_version=agent_version,
                input_summary={},
                decision_output={"action_type": "speak"},
                decision_quality_flags={},
            )
        )

    summaries = {
        summary["gameId"]: summary
        for summary in list_replay_summaries(event_store, trace_store=trace_store)
    }

    assert summaries["g_v0"]["mode"] == "LLM v0"
    assert summaries["g_v1"]["mode"] == "LLM v1"
    assert summaries["g_v2"]["mode"] == "LLM v2"


def test_list_replay_summaries_prefers_batch_arm_prefix_for_mode():
    from contracts.schemas import AgentDecisionTrace

    event_store = InMemoryEventStore()
    trace_store = InMemoryTraceStore()
    event_store.append(
        make_event(EventType.ROLE_ASSIGNED, game_id="batch_v2_30008", payload={"player_count": 9})
    )
    trace_store.append(
        AgentDecisionTrace(
            trace_id="trace_batch_v2",
            game_id="batch_v2_30008",
            round=1,
            phase=Phase.DAY_DISCUSSION,
            agent_id="P1",
            role=Role.VILLAGER,
            agent_version="v1",
            input_summary={},
            decision_output={"action_type": "speak"},
            decision_quality_flags={},
        )
    )

    summaries = list_replay_summaries(event_store, trace_store=trace_store)

    assert summaries[0]["mode"] == "LLM v2"


def test_list_replay_summaries_mode_override_wins_over_trace_store():
    from contracts.schemas import AgentDecisionTrace

    event_store = InMemoryEventStore()
    trace_store = InMemoryTraceStore()
    event_store.append(make_event(EventType.ROLE_ASSIGNED, game_id="g_override", payload={"player_count": 6}))
    trace_store.append(
        AgentDecisionTrace(
            trace_id="trace_override",
            game_id="g_override",
            round=1,
            phase=Phase.DAY_DISCUSSION,
            agent_id="P1",
            role=Role.VILLAGER,
            agent_version="v1",
            input_summary={},
            decision_output={"action_type": "speak"},
            decision_quality_flags={},
        )
    )

    summaries = list_replay_summaries(
        event_store,
        trace_store=trace_store,
        mode_overrides={"g_override": "Mock"},
    )

    assert summaries[0]["mode"] == "Mock"
