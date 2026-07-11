"""Tests for the /api/audit/runs endpoint."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.main import app, get_event_store, get_trace_store
from contracts import EventType


def test_audit_runs_endpoint_empty_store():
    """测试空 event_store 时返回空列表。"""
    mock_event_store = MagicMock()
    mock_trace_store = MagicMock()

    mock_event_store.list_game_ids.return_value = []

    app.dependency_overrides[get_event_store] = lambda: mock_event_store
    app.dependency_overrides[get_trace_store] = lambda: mock_trace_store

    try:
        with TestClient(app) as client:
            response = client.get("/api/audit/runs")

            assert response.status_code == 200
            data = response.json()
            assert "audit_runs" in data
            assert data["audit_runs"] == []
    finally:
        app.dependency_overrides.clear()


def test_audit_runs_endpoint_returns_audit_runs():
    """测试 /api/audit/runs endpoint 返回审计对局列表。"""
    mock_event_store = MagicMock()
    mock_trace_store = MagicMock()

    game_id = "g-test-001"

    event1 = MagicMock()
    event1.event_type = EventType.ROLE_ASSIGNED
    event1.created_at = "2026-06-04T10:00:00Z"
    event1.payload = {"player_count": 9}
    event1.round = 1

    mock_event_store.list_game_ids.return_value = [game_id]
    mock_event_store.list_by_game.return_value = [event1]
    mock_trace_store.list_by_game.return_value = []

    app.dependency_overrides[get_event_store] = lambda: mock_event_store
    app.dependency_overrides[get_trace_store] = lambda: mock_trace_store

    try:
        with TestClient(app) as client:
            response = client.get("/api/audit/runs")

            assert response.status_code == 200
            data = response.json()
            assert "audit_runs" in data
            assert isinstance(data["audit_runs"], list)
            assert len(data["audit_runs"]) == 1
            assert data["audit_runs"][0]["gameId"] == game_id
    finally:
        app.dependency_overrides.clear()


def test_audit_run_detail_endpoint_returns_full_data():
    """测试 /api/audit/runs/{game_id} endpoint 返回完整审计数据。"""
    mock_event_store = MagicMock()
    mock_trace_store = MagicMock()

    game_id = "g-detail-test-001"

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

    mock_event_store.list_by_game.return_value = [event1]
    mock_trace_store.list_by_game.return_value = []

    app.dependency_overrides[get_event_store] = lambda: mock_event_store
    app.dependency_overrides[get_trace_store] = lambda: mock_trace_store

    try:
        with TestClient(app) as client:
            response = client.get(f"/api/audit/runs/{game_id}")

            assert response.status_code == 200
            data = response.json()
            assert "audit" in data
            audit = data["audit"]
            assert "summary" in audit
            assert "events" in audit
            assert "traces" in audit
            assert "phaseOrder" in audit
            assert "phaseCounts" in audit
            assert audit["summary"]["game_id"] == game_id
            assert len(audit["events"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_audit_run_detail_endpoint_not_found():
    """测试 /api/audit/runs/{game_id} endpoint 对不存在的对局返回 404。"""
    mock_event_store = MagicMock()
    mock_trace_store = MagicMock()

    mock_event_store.list_by_game.return_value = []

    app.dependency_overrides[get_event_store] = lambda: mock_event_store
    app.dependency_overrides[get_trace_store] = lambda: mock_trace_store

    try:
        with TestClient(app) as client:
            response = client.get("/api/audit/runs/g-missing")

            assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
