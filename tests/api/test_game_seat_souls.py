from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app


def test_llm_game_requires_complete_seat_souls():
    with TestClient(app) as client:
        response = client.post(
            "/games",
            json={
                "player_count": 6,
                "arm": "v0",
                "seed": 0,
                "temperature": 0.6,
                "mode": "llm",
                "seat_souls": {"P1": "cautious"},
            },
        )

    assert response.status_code == 400
    assert "seat_souls" in response.json()["detail"]


def test_llm_game_rejects_unknown_soul_before_credentials_check():
    with TestClient(app) as client:
        response = client.post(
            "/games",
            json={
                "player_count": 6,
                "arm": "v0",
                "seed": 0,
                "temperature": 0.6,
                "mode": "llm",
                "seat_souls": {f"P{i}": "missing_soul" for i in range(1, 7)},
            },
        )

    assert response.status_code == 400
    assert "unknown soul" in response.json()["detail"]


def test_mock_game_accepts_seat_souls_without_requiring_real_souls():
    with TestClient(app) as client:
        response = client.post(
            "/games",
            json={
                "player_count": 6,
                "arm": "v0",
                "seed": 0,
                "temperature": 0.6,
                "mode": "mock",
                "seat_souls": {f"P{i}": "not_used_by_mock" for i in range(1, 7)},
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
