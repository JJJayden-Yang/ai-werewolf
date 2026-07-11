import type { GameEvent, ReplayData, ReplaySummary } from "@/lib/types/contracts";

export const DEMO_GAME_ID = "demo-jiangnan-9p";

export const demoReplaySummaries: ReplaySummary[] = [];

export function getDemoReplayData(gameId: string): ReplayData | null {
  if (gameId === "demo-mock-6p") return createDemoReplay("demo-mock-6p", 6);
  if (gameId === DEMO_GAME_ID) return createDemoReplay(DEMO_GAME_ID, 9);
  return null;
}

function createDemoReplay(gameId: string, playerCount: 6 | 9): ReplayData {
  const players = [
    typedPlayer("P1", "villager", "villager", "alive"),
    typedPlayer("P2", "seer", "villager", "alive"),
    typedPlayer("P3", "werewolf", "werewolf", "alive"),
    typedPlayer("P4", "witch", "villager", "alive"),
    typedPlayer("P5", "werewolf", "werewolf", "dead"),
    typedPlayer("P6", "villager", "villager", "alive"),
    typedPlayer("P7", "hunter", "villager", "dead"),
    typedPlayer("P8", "werewolf", "werewolf", "alive"),
    typedPlayer("P9", "villager", "villager", "alive")
  ].slice(0, playerCount);

  const events: GameEvent[] = [
    event(gameId, "e1", 1, "NIGHT_WEREWOLF", "day_announcement", null, null, {
      deaths: []
    }),
    event(gameId, "e2", 2, "DAY_DISCUSSION", "speech", "P3", null, {
      public_message: "我认为五号发言有矛盾，今天可以重点听他解释。"
    }),
    event(gameId, "e3", 2, "DAY_DISCUSSION", "speech", "P5", null, {
      public_message: "我没有问题，我是平民，昨天只是发言保守。",
      role_claim: "villager"
    }),
    event(gameId, "e4", 2, "DAY_DISCUSSION", "speech", "P2", null, {
      public_message: "我昨晚查验五号，结果偏狼，建议今天出五号。",
      role_claim: "seer"
    }),
    event(gameId, "e5", 2, "DAY_VOTE", "vote_cast", "P1", "P5", {}),
    event(gameId, "e6", 2, "DAY_VOTE", "vote_cast", "P2", "P5", {}),
    event(gameId, "e7", 2, "DAY_VOTE", "vote_cast", "P3", "P5", {}),
    event(gameId, "e8", 2, "DAY_VOTE", "vote_cast", "P4", "P3", {}),
    event(gameId, "e9", 2, "DAY_VOTE", "vote_cast", "P5", "P3", {}),
    event(gameId, "e10", 2, "EXILE_RESOLUTION", "exile", null, "P5", {}),
    event(gameId, "e11", 2, "EXILE_RESOLUTION", "death_confirmed", null, "P5", {
      death_cause: "exile"
    }),
    event(gameId, "e12", 2, "NIGHT_WEREWOLF", "death_confirmed", null, "P7", {
      death_cause: "night_kill"
    }),
    event(gameId, "e13", 2, "HUNTER_SHOOT", "hunter_shot", "P7", "P3", {}),
    event(gameId, "e14", 2, "GAME_OVER", "game_over", null, null, {
      winner: "villagers",
      reason: "所有狼人被放逐或击杀，好人阵营胜利。"
    })
  ];

  return {
    game_id: gameId,
    players: [],
    timeline: [],
    events,
    belief_curves: [],
    deviation_points: [],
    bad_cases: [],
    evaluation_summary: {},
    typed_players: players,
    typed_belief_curves: []
  };
}

function typedPlayer(
  player_id: string,
  role: ReplayData["typed_players"][number]["role"],
  camp: ReplayData["typed_players"][number]["camp"],
  final_status: ReplayData["typed_players"][number]["final_status"]
): ReplayData["typed_players"][number] {
  return {
    player_id,
    role,
    camp,
    final_status,
    survived: final_status === "alive",
    death_round: final_status === "dead" ? 2 : null,
    death_phase: final_status === "dead" ? "EXILE_RESOLUTION" : null,
    death_cause: final_status === "dead" ? "exile" : null,
    death_source_event_id: null,
    killer_agent_id: null,
    killer_camp: null
  };
}

function event(
  game_id: string,
  event_id: string,
  round: number,
  phase: GameEvent["phase"],
  event_type: GameEvent["event_type"],
  actor: string | null,
  target: string | null,
  payload: GameEvent["payload"]
): GameEvent {
  return {
    event_id,
    game_id,
    round,
    phase,
    event_type,
    actor,
    target,
    visibility: "public",
    payload,
    created_at: "2026-05-28T10:00:00Z"
  };
}
