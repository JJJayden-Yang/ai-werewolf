export type Role = "werewolf" | "seer" | "witch" | "hunter" | "villager";

export type Camp = "werewolf" | "villager";

export type PlayerStatus = "alive" | "dead";

export type Phase =
  | "INIT"
  | "ROLE_ASSIGNMENT"
  | "NIGHT_WEREWOLF"
  | "NIGHT_SEER"
  | "NIGHT_WITCH"
  | "DAY_ANNOUNCEMENT"
  | "HUNTER_SHOOT"
  | "DAY_DISCUSSION"
  | "DAY_VOTE"
  | "DAY_TIE_DISCUSSION"
  | "DAY_TIE_REVOTE"
  | "EXILE_RESOLUTION"
  | "NO_EXILE_RESOLUTION"
  | "EXILE_LAST_WORDS"
  | "WIN_CHECK"
  | "GAME_OVER";

export type EventType =
  | "phase_started"
  | "role_assigned"
  | "wolf_nomination"
  | "night_kill_announced"
  | "seer_check_result"
  | "witch_kill_target_info"
  | "witch_save"
  | "witch_poison"
  | "day_announcement"
  | "speech"
  | "vote_cast"
  | "tie_detected"
  | "no_exile_due_to_second_tie"
  | "exile"
  | "last_words"
  | "hunter_shot"
  | "death_confirmed"
  | "win_check"
  | "game_over"
  | "agent_action"
  | "llm_call"
  | "rule_validation"
  | "fallback_used"
  | "belief_snapshot"
  | "belief_deviation"
  | "action_canonicalized"
  | "action_guard_triggered"
  | "context_assembled"
  | "prompt_version_used"
  | "agent_decision_trace"
  | "fact_stream_summary";

export type ActionType =
  | "speak"
  | "vote"
  | "night_kill_nominate"
  | "check"
  | "save"
  | "poison"
  | "hunter_shoot"
  | "skip";

export type Visibility =
  | "public"
  | "private_to_seer"
  | "private_to_witch"
  | "private_to_wolves";

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export interface GameEvent {
  event_id: string;
  game_id: string;
  round: number;
  phase: Phase;
  event_type: EventType;
  actor?: string | null;
  target?: string | null;
  visibility: Visibility;
  payload: Record<string, JsonValue>;
  created_at?: string | null;
}

export interface ReplayPlayer {
  player_id: string;
  role: Role;
  camp?: Camp | null;
  final_status: PlayerStatus;
  survived: boolean;
  death_round?: number | null;
  death_phase?: Phase | null;
  death_cause?: string | null;
  death_source_event_id?: string | null;
  killer_agent_id?: string | null;
  killer_camp?: Camp | null;
}

export interface LegacyReplayPlayer {
  player_id?: string;
  id?: string;
  role?: Role | string | null;
  camp?: Camp | string | null;
  status?: PlayerStatus | string | null;
  final_status?: PlayerStatus | string | null;
  survived?: boolean;
  [key: string]: JsonValue | undefined;
}

export interface BeliefCurvePoint {
  round: number;
  phase?: Phase | null;
  agent_id: string;
  target_player_id: string;
  werewolf_prob: number;
  derived_by?: string | null;
}

export interface ReplayData {
  game_id: string;
  players: LegacyReplayPlayer[];
  timeline: JsonValue[];
  events: GameEvent[];
  belief_curves: JsonValue[];
  deviation_points: JsonValue[];
  bad_cases: JsonValue[];
  evaluation_summary: Record<string, JsonValue>;
  typed_players: ReplayPlayer[];
  typed_belief_curves: BeliefCurvePoint[];
}

export interface ReplaySummary {
  gameId: string;
  createdAt?: string | null;
  playerCount?: number | null;
  mode?: string | null;
  status?: string | null;
  winner?: string | null;
  rounds?: number | null;
  durationSec?: number | null;
  tags: string[];
}

export interface ReplayViewModel {
  gameId: string;
  players: ReplayPlayer[];
  timeline: TimelineGroup[];
  events: GameEvent[];
  beliefCurves: BeliefCurvePoint[];
  evaluationSummary: Record<string, JsonValue>;
  raw: ReplayData;
}

export interface TimelineGroup {
  key: string;
  round: number;
  phase: Phase;
  events: GameEvent[];
}
