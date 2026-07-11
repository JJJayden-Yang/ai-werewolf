"""A5：6 人 Debug MVP 完整 smoke + 100 局稳定性回归。"""

import asyncio
import json
import random
from pathlib import Path

from contracts import (
    ActionType,
    AgentContext,
    EventType,
    GameConfig,
    Phase,
    PlayerStatus,
    Role,
    VisiblePlayer,
)
from game_core import GameEngine, GameSessionManager
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class SmokeContextAssembler:
    def __init__(self, engine: GameEngine) -> None:
        self._engine = engine

    def build_context(self, game_id: str, agent_id: str, phase: Phase) -> AgentContext:
        session = self._engine.sessions.get_game(game_id)
        player = session.truth_state.players[agent_id]
        targets = [
            pid
            for pid, p in session.truth_state.players.items()
            if p.status == PlayerStatus.ALIVE
            and pid != agent_id
            and not (phase == Phase.NIGHT_WEREWOLF and p.role == Role.WEREWOLF)
        ]
        if phase == Phase.DAY_TIE_REVOTE:
            targets = [
                pid
                for pid in session.truth_state.round_state.tie_candidates
                if pid != agent_id
                and session.truth_state.players[pid].status == PlayerStatus.ALIVE
            ]

        return AgentContext(
            game_id=game_id,
            agent_id=agent_id,
            role=player.role,
            round=session.round,
            phase=phase,
            tie_candidates=session.truth_state.round_state.tie_candidates,
            visible_players=[
                VisiblePlayer(player_id=pid, status=p.status, public_claim=p.public_claim)
                for pid, p in session.truth_state.players.items()
            ],
            rule_hints={"fallback_targets": targets},
        )


class LegalSmokeMockAgent:
    async def act(self, context: dict) -> dict:
        phase = Phase(context["phase"])
        targets = context.get("rule_hints", {}).get("fallback_targets") or []
        action_type = ActionType.SPEAK
        target = None
        public_message = None

        if phase == Phase.NIGHT_WEREWOLF:
            action_type = ActionType.NIGHT_KILL_NOMINATE
            target = targets[0] if targets else None
        elif phase == Phase.NIGHT_SEER:
            action_type = ActionType.CHECK
            target = targets[0] if targets else None
        elif phase == Phase.NIGHT_WITCH:
            action_type = ActionType.SKIP
        elif phase in (Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION):
            action_type = ActionType.SPEAK
            public_message = "I am sharing a cautious public statement."
        elif phase in (Phase.DAY_VOTE, Phase.DAY_TIE_REVOTE):
            action_type = ActionType.VOTE
            target = targets[0] if targets else None
        elif phase == Phase.HUNTER_SHOOT:
            action_type = ActionType.HUNTER_SHOOT
        elif phase == Phase.EXILE_LAST_WORDS:
            action_type = ActionType.SPEAK
            public_message = "These are my last words."

        return {
            "game_id": context["game_id"],
            "agent_id": context["agent_id"],
            "role": context["role"],
            "phase": context["phase"],
            "action_type": action_type,
            "target": target,
            "public_message": public_message,
        }


class SmokeEventSink:
    def __init__(self) -> None:
        self.events = []

    def append_many(self, events) -> None:
        self.events.extend(events)


def _build_game(seed: int, game_id: str):
    config_data = json.loads((FIXTURES / "game_config_6p_debug.json").read_text(encoding="utf-8"))
    config_data["game_id"] = game_id
    config = GameConfig.model_validate(config_data)

    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(config)
    sink = SmokeEventSink()
    supervisor = Supervisor(engine, SmokeContextAssembler(engine), LegalSmokeMockAgent(), sink)
    return config, engine, supervisor, sink


def _run_game(seed: int, game_id: str):
    config, engine, supervisor, sink = _build_game(seed, game_id)
    asyncio.run(supervisor.run_game(game_id))
    session = engine.sessions.get_game(game_id)
    return config, session, sink.events


def test_6p_debug_mvp_runs_to_game_over_with_replay_serializable_events():
    config, session, events = _run_game(seed=0, game_id="smoke_6p_single")

    assert session.current_phase == Phase.GAME_OVER
    assert session.round <= config.max_rounds
    assert events
    assert any(event.event_type == EventType.NIGHT_KILL_ANNOUNCED for event in events)
    assert any(event.event_type == EventType.DAY_ANNOUNCEMENT for event in events)
    assert any(event.event_type == EventType.VOTE_CAST for event in events)
    assert any(event.event_type == EventType.GAME_OVER for event in events)

    replay_payload = [event.model_dump(mode="json") for event in events]
    json.dumps(replay_payload)


def test_mock_agent_100_games_smoke_no_stuck_no_illegal_or_fallback():
    stats = {
        "game_completed_count": 0,
        "phase_stuck_count": 0,
        "illegal_action_count": 0,
        "fallback_count": 0,
        "rounds": [],
        "winner_distribution": {},
    }

    for seed in range(100):
        _config, session, events = _run_game(seed=seed, game_id=f"smoke_6p_{seed:03d}")
        if session.current_phase == Phase.GAME_OVER:
            stats["game_completed_count"] += 1
        else:
            stats["phase_stuck_count"] += 1

        stats["illegal_action_count"] += sum(
            1 for event in events if event.event_type == EventType.RULE_VALIDATION
        )
        stats["fallback_count"] += sum(
            1 for event in events if event.event_type == EventType.FALLBACK_USED
        )
        stats["rounds"].append(session.round)
        winner = next(
            event.payload.get("winner")
            for event in reversed(events)
            if event.event_type == EventType.GAME_OVER
        )
        stats["winner_distribution"][winner] = stats["winner_distribution"].get(winner, 0) + 1

    assert stats["game_completed_count"] == 100
    assert stats["phase_stuck_count"] == 0
    assert stats["illegal_action_count"] == 0
    assert stats["fallback_count"] == 0
    assert all(round_ <= 8 for round_ in stats["rounds"])
    assert sum(stats["winner_distribution"].values()) == 100
