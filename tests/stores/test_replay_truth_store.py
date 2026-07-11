from __future__ import annotations

from stores.replay_truth_store import InMemoryReplayTruthStore, JsonReplayTruthStore


def test_in_memory_replay_truth_store_roundtrips_players():
    store = InMemoryReplayTruthStore()
    players = [
        {
            "player_id": "P1",
            "role": "werewolf",
            "camp": "werewolf",
            "status": "alive",
            "public_claim": None,
            "vote_weight": 1.0,
        }
    ]

    store.save_players("g_truth", players)

    assert store.get_players("g_truth") == players


def test_json_replay_truth_store_persists_players_across_instances(tmp_path):
    players = [
        {
            "player_id": "P2",
            "role": "seer",
            "camp": "villager",
            "status": "dead",
            "public_claim": None,
            "vote_weight": 1.0,
        }
    ]
    store = JsonReplayTruthStore(tmp_path / "replay_truth")

    store.save_players("g_truth", players)

    reloaded = JsonReplayTruthStore(tmp_path / "replay_truth")
    assert reloaded.get_players("g_truth") == players
    assert (tmp_path / "replay_truth" / "g_truth.json").exists()


def test_replay_truth_store_returns_empty_for_unknown_game(tmp_path):
    store = JsonReplayTruthStore(tmp_path / "replay_truth")

    assert store.get_players("missing") == []
