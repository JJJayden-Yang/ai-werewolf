"""FastAPI `GET /replay/{game_id}` 端到端测试（C / S2）。

用 FastAPI TestClient 走完整 HTTP 路径：
- 依赖注入通过 ``app.dependency_overrides`` 替换为测试 fixture。
- 验证 200 happy path / 404 not found。
- 验证 /health。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts import EventType
from stores.event_store import InMemoryEventStore
from tests.context.conftest import FakeSessionProvider, make_6p_session, make_event

from api.main import app, get_event_store, get_session_provider


@pytest.fixture
def store_with_one_game():
    store = InMemoryEventStore()
    store.append(make_event(EventType.SPEECH, game_id="g001", actor="P1"))
    store.append(make_event(EventType.VOTE_CAST, game_id="g001", actor="P1", target="P2"))
    return store


@pytest.fixture
def provider_for_g001():
    return FakeSessionProvider(make_6p_session(game_id="g001"))


@pytest.fixture
def client(store_with_one_game, provider_for_g001):
    """带 fixture 注入的 TestClient；每个测试结束自动清理 overrides。"""
    app.dependency_overrides[get_event_store] = lambda: store_with_one_game
    app.dependency_overrides[get_session_provider] = lambda: provider_for_g001
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---- /health ----


def test_health_returns_ok():
    with TestClient(app) as c:
        response = c.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "time" in body


# ---- /replay happy path ----


def test_get_replay_returns_200_and_full_payload(client):
    response = client.get("/replay/g001")

    assert response.status_code == 200
    body = response.json()
    assert body["game_id"] == "g001"
    assert len(body["players"]) == 6
    assert len(body["events"]) == 2
    assert len(body["timeline"]) == 2
    # v1 / S10 字段：保持空
    assert body["belief_curves"] == []
    assert body["deviation_points"] == []
    assert body["bad_cases"] == []
    assert body["evaluation_summary"] == {}


def test_get_replay_timeline_entries_have_expected_fields(client):
    response = client.get("/replay/g001")

    assert response.status_code == 200
    entries = response.json()["timeline"]
    for entry in entries:
        assert "event_id" in entry
        assert "round" in entry
        assert "phase" in entry
        assert "event_type" in entry
        assert "visibility" in entry


def test_get_replay_players_have_role_value(client):
    response = client.get("/replay/g001")

    assert response.status_code == 200
    players = response.json()["players"]
    roles = {p["role"] for p in players}
    assert "werewolf" in roles
    assert "seer" in roles
    assert "villager" in roles


# ---- /replay not found ----


def test_get_replay_unknown_game_returns_404():
    """无任何注入 fixture 时，默认 InMemory 是空 → 任何 game_id 都 404。"""
    # 不用 client fixture，避免它注入 g001 的数据
    app.dependency_overrides.clear()
    try:
        with TestClient(app) as c:
            response = c.get("/replay/g_missing")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "replay not found" in response.json()["detail"].lower()
