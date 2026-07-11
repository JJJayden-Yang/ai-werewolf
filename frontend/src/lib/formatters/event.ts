import type { EventType, GameEvent } from "@/lib/types/contracts";

import { formatPhase } from "./phase";

const EVENT_LABELS: Record<EventType, string> = {
  phase_started: "Phase started",
  role_assigned: "Role assigned",
  wolf_nomination: "Wolf nomination",
  night_kill_announced: "Night kill announced",
  seer_check_result: "Seer check result",
  witch_kill_target_info: "Witch kill target info",
  witch_save: "Witch save",
  witch_poison: "Witch poison",
  day_announcement: "Day announcement",
  speech: "Speech",
  vote_cast: "Vote cast",
  tie_detected: "Tie detected",
  no_exile_due_to_second_tie: "No exile after second tie",
  exile: "Exile",
  last_words: "Last words",
  hunter_shot: "Hunter shot",
  death_confirmed: "Death confirmed",
  win_check: "Win check",
  game_over: "Game over",
  agent_action: "Agent action",
  llm_call: "LLM call",
  rule_validation: "Rule validation",
  fallback_used: "Fallback used",
  belief_snapshot: "Belief snapshot",
  belief_deviation: "Belief deviation",
  action_canonicalized: "Action canonicalized",
  action_guard_triggered: "Action guard triggered",
  context_assembled: "Context assembled",
  prompt_version_used: "Prompt version used",
  agent_decision_trace: "Agent decision trace",
  fact_stream_summary: "Fact stream summary"
};

export function formatEventType(eventType?: EventType | string | null): string {
  if (!eventType) return "Unknown event";
  return EVENT_LABELS[eventType as EventType] ?? eventType;
}

export function formatEventSummary(event: GameEvent): string {
  const actor = event.actor ? `${event.actor} ` : "";
  const target = event.target ? `-> ${event.target}` : "";
  return `${formatEventType(event.event_type)}: ${actor}${target}`.trim();
}

export function formatEventLocation(event: GameEvent): string {
  return `R${event.round} / ${formatPhase(event.phase)}`;
}
