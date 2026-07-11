import type {
  BeliefCurvePoint,
  LegacyReplayPlayer,
  ReplayData,
  ReplayPlayer,
  ReplayViewModel
} from "@/lib/types/contracts";

import { groupEventsByRoundPhase } from "./groupTimeline";

export function projectReplay(data: ReplayData): ReplayViewModel {
  return {
    gameId: data.game_id,
    players: selectReplayPlayers(data),
    timeline: groupEventsByRoundPhase(data.events),
    events: data.events,
    beliefCurves: selectBeliefCurves(data),
    evaluationSummary: data.evaluation_summary,
    raw: data
  };
}

export function selectReplayPlayers(data: ReplayData): ReplayPlayer[] {
  if (data.typed_players.length > 0) return data.typed_players;
  return data.players.flatMap(projectLegacyPlayer);
}

export function selectBeliefCurves(data: ReplayData): BeliefCurvePoint[] {
  if (data.typed_belief_curves.length > 0) return data.typed_belief_curves;
  return data.belief_curves.flatMap((point) =>
    isBeliefCurvePoint(point) ? [point] : []
  );
}

function projectLegacyPlayer(player: LegacyReplayPlayer): ReplayPlayer[] {
  const playerId = player.player_id ?? player.id;
  if (!playerId) return [];
  const finalStatus =
    player.final_status === "dead" || player.status === "dead" ? "dead" : "alive";

  return [
    {
      player_id: playerId,
      role: isRole(player.role) ? player.role : "villager",
      camp: isCamp(player.camp) ? player.camp : null,
      final_status: finalStatus,
      survived: player.survived ?? finalStatus !== "dead",
      death_round: null,
      death_phase: null,
      death_cause: null,
      death_source_event_id: null,
      killer_agent_id: null,
      killer_camp: null
    }
  ];
}

function isBeliefCurvePoint(value: unknown): value is BeliefCurvePoint {
  if (!value || typeof value !== "object") return false;
  const point = value as Partial<BeliefCurvePoint>;
  return (
    typeof point.round === "number" &&
    typeof point.agent_id === "string" &&
    typeof point.target_player_id === "string" &&
    typeof point.werewolf_prob === "number"
  );
}

function isRole(value: unknown): value is ReplayPlayer["role"] {
  return (
    value === "werewolf" ||
    value === "seer" ||
    value === "witch" ||
    value === "hunter" ||
    value === "villager"
  );
}

function isCamp(value: unknown): value is NonNullable<ReplayPlayer["camp"]> {
  return value === "werewolf" || value === "villager";
}
