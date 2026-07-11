"""Tests for audit_service."""

from unittest.mock import MagicMock

from api.audit_service import get_audit_run, list_audit_runs
from contracts import EventType
from contracts.schemas import GameEvent


def test_list_audit_runs_empty_event_store():
    """没有对局时返回空列表。"""
    mock_event_store = MagicMock()
    mock_event_store.list_game_ids.return_value = []

    result = list_audit_runs(mock_event_store)

    assert result == []


def test_list_audit_runs_single_game():
    """单个对局的摘要生成。"""
    game_id = "g-test-001"
    player_count = 9

    event1 = MagicMock()
    event1.event_type = EventType.ROLE_ASSIGNED
    event1.created_at = "2026-06-04T10:00:00Z"
    event1.payload = {"player_count": player_count}
    event1.round = 1

    event2 = MagicMock()
    event2.event_type = EventType.GAME_OVER
    event2.payload = {"winner": "werewolves"}
    event2.round = 3

    mock_event_store = MagicMock()
    mock_event_store.list_game_ids.return_value = [game_id]
    mock_event_store.list_by_game.return_value = [event1, event2]

    mock_trace_store = MagicMock()
    mock_trace_store.list_by_game.return_value = []

    result = list_audit_runs(
        mock_event_store,
        trace_store=mock_trace_store,
        data_dir="./data",
    )

    assert len(result) == 1
    run = result[0]
    assert run["gameId"] == game_id
    assert run["playerCount"] == player_count
    assert run["winner"] == "werewolves"
    assert run["rounds"] == 3
    assert run["eventCount"] == 2
    assert run["traceCount"] == 0
    assert run["hasAuditPage"] is False
    assert "events" in run["eventPath"]
    assert run["tracePath"] is None


def test_list_audit_runs_with_traces():
    """包含 trace 的对局。"""
    game_id = "g-test-002"

    event1 = MagicMock()
    event1.event_type = EventType.ROLE_ASSIGNED
    event1.created_at = "2026-06-04T11:00:00Z"
    event1.payload = {"player_count": 6}
    event1.round = 1

    mock_event_store = MagicMock()
    mock_event_store.list_game_ids.return_value = [game_id]
    mock_event_store.list_by_game.return_value = [event1]

    # 模拟有 3 条 trace
    mock_traces = [MagicMock(), MagicMock(), MagicMock()]
    mock_trace_store = MagicMock()
    mock_trace_store.list_by_game.return_value = mock_traces

    result = list_audit_runs(
        mock_event_store,
        trace_store=mock_trace_store,
        data_dir="./data",
    )

    run = result[0]
    assert run["traceCount"] == 3
    assert run["hasAuditPage"] is True
    assert run["tracePath"] is not None
    assert "traces" in run["tracePath"]


def test_list_audit_runs_ordered_by_creation_time():
    """按创建时间逆序排列。"""
    events_map = {
        "g-old": [MagicMock(event_type=EventType.ROLE_ASSIGNED,
                           created_at="2026-06-01T10:00:00Z",
                           payload={"player_count": 6},
                           round=1)],
        "g-new": [MagicMock(event_type=EventType.ROLE_ASSIGNED,
                           created_at="2026-06-04T10:00:00Z",
                           payload={"player_count": 9},
                           round=1)],
    }

    mock_event_store = MagicMock()
    mock_event_store.list_game_ids.return_value = ["g-old", "g-new"]

    def side_effect(game_id):
        return events_map.get(game_id, [])

    mock_event_store.list_by_game.side_effect = side_effect

    mock_trace_store = MagicMock()
    mock_trace_store.list_by_game.return_value = []

    result = list_audit_runs(
        mock_event_store,
        trace_store=mock_trace_store,
    )

    # 应该是 g-new 在前（较新）
    assert result[0]["gameId"] == "g-new"
    assert result[1]["gameId"] == "g-old"


def test_get_audit_run_returns_full_audit_data():
    """获取单个对局的完整审计数据。"""
    game_id = "g-audit-test-001"

    event1 = MagicMock()
    event1.event_id = "evt-1"
    event1.game_id = game_id
    event1.round = 1
    event1.phase = "ROLE_ASSIGNMENT"
    event1.event_type = EventType.ROLE_ASSIGNED
    event1.actor = None
    event1.target = None
    event1.visibility = "public"
    event1.payload = {"player_count": 9}
    event1.created_at = "2026-06-04T10:00:00Z"

    event2 = MagicMock()
    event2.event_id = "evt-2"
    event2.game_id = game_id
    event2.round = 3
    event2.phase = "GAME_OVER"
    event2.event_type = EventType.GAME_OVER
    event2.actor = None
    event2.target = None
    event2.visibility = "public"
    event2.payload = {"winner": "villagers"}
    event2.created_at = "2026-06-04T11:00:00Z"

    mock_event_store = MagicMock()
    mock_event_store.list_by_game.return_value = [event1, event2]

    mock_trace_store = MagicMock()
    mock_trace_store.list_by_game.return_value = []

    result = get_audit_run(game_id, mock_event_store, trace_store=mock_trace_store)

    assert result is not None
    assert "summary" in result
    assert "events" in result
    assert "traces" in result
    assert "phaseOrder" in result
    assert "phaseCounts" in result

    assert result["summary"]["game_id"] == game_id
    assert result["summary"]["winner"] == "villagers"
    assert result["summary"]["rounds"] == 3
    assert result["summary"]["player_count"] == 9
    assert len(result["events"]) == 2
    assert len(result["traces"]) == 0


def test_get_audit_run_not_found():
    """对局不存在时返回 None。"""
    mock_event_store = MagicMock()
    mock_event_store.list_by_game.return_value = []

    result = get_audit_run("g-missing", mock_event_store)

    assert result is None
