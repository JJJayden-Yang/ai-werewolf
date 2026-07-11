"""实时观战对局 API 测试。

这些端点给前端 MVP 轮询使用：POST /games 启动一局，GET /games/{id}/events
按游标拉取全量 GameEvent。测试用 mock agent 跑完整局，不调用真实 LLM。
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from api.game_service import _event_to_player_visible_event
from api.main import app, get_event_store, get_replay_truth_store, get_trace_store
from contracts import EventType, GameEvent, Phase, Visibility
from stores.event_store import JsonlEventStore
from stores.replay_truth_store import JsonReplayTruthStore
from stores.trace_store import JsonlTraceStore


def _wait_for_finished(client: TestClient, game_id: str, *, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    last_body: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/games/{game_id}/status")
        assert response.status_code == 200
        last_body = response.json()
        if last_body["status"] in {"finished", "error"}:
            return last_body
        time.sleep(0.05)
    raise AssertionError(f"game did not finish before timeout; last status={last_body!r}")


def _wait_for_pending(
    client: TestClient,
    game_id: str,
    player_id: str,
    *,
    timeout: float = 4.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last_body: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/games/{game_id}/pending", params={"player_id": player_id})
        assert response.status_code == 200
        last_body = response.json()
        if last_body["pending"]:
            return last_body
        time.sleep(0.05)
    raise AssertionError(f"game did not reach pending before timeout; last pending={last_body!r}")


def test_post_games_starts_game_and_returns_running_status():
    with TestClient(app) as client:
        response = client.post(
            "/games",
            json={"player_count": 6, "arm": "v0", "seed": 0, "temperature": 0.6, "mode": "mock"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["game_id"].startswith("g-")
        assert body["status"] == "running"


def test_events_polling_returns_cursor_and_event_stream_until_finished():
    with TestClient(app) as client:
        start = client.post(
            "/games",
            json={"player_count": 6, "arm": "v0", "seed": 1, "temperature": 0.6, "mode": "mock"},
        )
        game_id = start.json()["game_id"]

        status = _wait_for_finished(client, game_id)
        assert status["status"] == "finished"
        assert status["winner"] in {"werewolf", "villager"}

        first = client.get(f"/games/{game_id}/events", params={"since": 0})
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["status"] == "finished"
        assert first_body["next_cursor"] == len(first_body["events"])
        assert first_body["next_cursor"] > 0
        assert first_body["events"][0]["event_type"] in {"role_assigned", "phase_started"}

        second = client.get(
            f"/games/{game_id}/events",
            params={"since": first_body["next_cursor"]},
        )
        assert second.status_code == 200
        assert second.json() == {
            "events": [],
            "next_cursor": first_body["next_cursor"],
            "status": "finished",
        }


def test_post_games_persists_events_to_jsonl_and_replay_reads_them(tmp_path):
    event_store = JsonlEventStore(tmp_path / "events")
    trace_store = JsonlTraceStore(tmp_path / "traces")
    replay_truth_store = JsonReplayTruthStore(tmp_path / "replay_truth")
    app.dependency_overrides[get_event_store] = lambda: event_store
    app.dependency_overrides[get_trace_store] = lambda: trace_store
    app.dependency_overrides[get_replay_truth_store] = lambda: replay_truth_store
    try:
        with TestClient(app) as client:
            start = client.post(
                "/games",
                json={
                    "player_count": 6,
                    "arm": "v0",
                    "seed": 11,
                    "temperature": 0.6,
                    "mode": "mock",
                },
            )
            assert start.status_code == 200
            game_id = start.json()["game_id"]
            _wait_for_finished(client, game_id)

            jsonl_path = tmp_path / "events" / f"{game_id}.jsonl"
            assert jsonl_path.exists()
            assert jsonl_path.read_text(encoding="utf-8").strip()
            truth_path = tmp_path / "replay_truth" / f"{game_id}.json"
            assert truth_path.exists()
            assert truth_path.read_text(encoding="utf-8").strip()

            replay = client.get(f"/replay/{game_id}")
            assert replay.status_code == 200
            body = replay.json()
            assert body["game_id"] == game_id
            assert body["events"]
            assert len(body["players"]) == 6
            assert {player["role"] for player in body["players"]} >= {"werewolf", "seer", "witch"}
            dead_targets = {
                event["target"]
                for event in body["events"]
                if event["event_type"] == "death_confirmed" and event["target"]
            }
            player_status = {player["player_id"]: player["status"] for player in body["players"]}
            assert all(player_status[target] == "dead" for target in dead_targets)
            assert body["events"][0]["event_type"] in {"role_assigned", "phase_started"}

            listing = client.get("/replays")
            assert listing.status_code == 200
            rows = listing.json()["replays"]
            row = next(item for item in rows if item["gameId"] == game_id)
            assert row["status"] == "completed"
            assert row["playerCount"] == 6
            assert row["mode"] == "Mock"
            assert row["rounds"] >= 1
    finally:
        app.dependency_overrides.clear()


def test_get_games_lists_started_games_with_latest_phase():
    with TestClient(app) as client:
        start = client.post(
            "/games",
            json={"player_count": 6, "arm": "v0", "seed": 2, "temperature": 0.6, "mode": "mock"},
        )
        game_id = start.json()["game_id"]
        _wait_for_finished(client, game_id)

        response = client.get("/games")

        assert response.status_code == 200
        games = response.json()["games"]
        row = next(game for game in games if game["game_id"] == game_id)
        assert row["status"] == "finished"
        assert row["player_count"] == 6
        assert row["arm"] == "v0"
        assert row["current_round"] >= 1
        assert row["current_phase"]


def test_replay_for_started_game_includes_registry_role_map():
    with TestClient(app) as client:
        start = client.post(
            "/games",
            json={"player_count": 6, "arm": "v0", "seed": 3, "temperature": 0.6, "mode": "mock"},
        )
        game_id = start.json()["game_id"]
        _wait_for_finished(client, game_id)

        response = client.get(f"/replay/{game_id}")

        assert response.status_code == 200
        players = response.json()["players"]
        assert len(players) == 6
        assert {player["role"] for player in players} >= {"werewolf", "seer", "witch"}


def test_human_game_exposes_pending_context_and_accepts_action():
    with TestClient(app) as client:
        start = client.post(
            "/games",
            json={
                "player_count": 6,
                "arm": "v0",
                "seed": 4,
                "temperature": 0.6,
                "mode": "mock",
                "human_seat": "P1",
                "human_role": "seer",
            },
        )
        assert start.status_code == 200
        game_id = start.json()["game_id"]

        status = client.get(f"/games/{game_id}/status")
        assert status.status_code == 200
        assert status.json()["role_map"]["P1"] == "seer"

        blocked = client.get(f"/games/{game_id}/pending", params={"player_id": "P2"})
        assert blocked.status_code == 403

        pending = _wait_for_pending(client, game_id, "P1")
        context = pending["context"]
        assert context["agent_id"] == "P1"
        assert context["role"] == "seer"
        assert "truth_state" not in context
        assert "role_map" not in context

        target = next(player["player_id"] for player in context["visible_players"] if player["player_id"] != "P1")
        action = client.post(
            f"/games/{game_id}/action",
            json={"player_id": "P1", "action_type": "check", "target": target},
        )
        assert action.status_code == 200
        assert action.json() == {"accepted": True}


def test_human_player_events_hide_god_view_night_information():
    with TestClient(app) as client:
        start = client.post(
            "/games",
            json={
                "player_count": 9,
                "arm": "v0",
                "seed": 5,
                "temperature": 0.6,
                "mode": "mock",
                "human_seat": "P2",
                "human_role": "seer",
            },
        )
        assert start.status_code == 200
        game_id = start.json()["game_id"]

        blocked = client.get(f"/games/{game_id}/player-events", params={"player_id": "P3"})
        assert blocked.status_code == 403

        _wait_for_pending(client, game_id, "P2")
        response = client.get(f"/games/{game_id}/player-events", params={"player_id": "P2", "since": 0})

        assert response.status_code == 200
        body = response.json()
        assert body["player_id"] == "P2"
        assert body["role"] == "seer"
        assert "role_map" not in body
        assert "truth_state" not in body
        assert all("role" not in player for player in body["visible_players"])
        assert all(event["visibility"] == "public" for event in body["events"])
        assert {event["event_type"] for event in body["events"]}.isdisjoint(
            {"role_assigned", "wolf_nomination", "night_kill_announced"}
        )


def test_player_visible_game_over_keeps_safe_winner_payload():
    event = GameEvent(
        event_id="evt_game_over",
        game_id="g-test",
        round=4,
        phase=Phase.GAME_OVER,
        event_type=EventType.GAME_OVER,
        visibility=Visibility.PUBLIC,
        payload={
            "winner": "villagers",
            "reason": "all_werewolves_dead",
            "role_map": {"P1": "werewolf"},
        },
    )

    body = _event_to_player_visible_event("g-test", event, {event.event_id: object()})

    assert body is not None
    assert body["event_type"] == "game_over"
    assert body["payload"] == {
        "winner": "villagers",
        "reason": "all_werewolves_dead",
    }


def test_player_visible_day_announcement_keeps_deaths():
    """回归：人类玩家端的天亮公告必须带 deaths，否则前端永远显示「平安夜」。"""
    event = GameEvent(
        event_id="evt_day_announce",
        game_id="g-test",
        round=2,
        phase=Phase.DAY_ANNOUNCEMENT,
        event_type=EventType.DAY_ANNOUNCEMENT,
        visibility=Visibility.PUBLIC,
        payload={"deaths": [{"player_id": "P1", "death_cause": "night_kill"}]},
    )

    body = _event_to_player_visible_event("g-test", event, {event.event_id: object()})

    assert body is not None
    assert body["event_type"] == "day_announcement"
    assert body["payload"]["deaths"] == [
        {"player_id": "P1", "death_cause": "night_kill"}
    ]


def test_player_visible_death_confirmed_keeps_target_and_cause():
    event = GameEvent(
        event_id="evt_death",
        game_id="g-test",
        round=2,
        phase=Phase.DAY_ANNOUNCEMENT,
        event_type=EventType.DEATH_CONFIRMED,
        visibility=Visibility.PUBLIC,
        target="P1",
        payload={"target": "P1", "death_cause": "night_kill"},
    )

    body = _event_to_player_visible_event("g-test", event, {event.event_id: object()})

    assert body is not None
    assert body["event_type"] == "death_confirmed"
    assert body["target"] == "P1"
    assert body["payload"]["death_cause"] == "night_kill"


def test_unknown_game_returns_404_for_status_and_events():
    with TestClient(app) as client:
        status = client.get("/games/g_missing/status")
        events = client.get("/games/g_missing/events", params={"since": 0})

    assert status.status_code == 404
    assert events.status_code == 404
