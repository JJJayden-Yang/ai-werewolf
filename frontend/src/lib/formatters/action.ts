import type { ActionType } from "@/lib/types/contracts";

const ACTION_LABELS: Record<ActionType, string> = {
  speak: "Speak",
  vote: "Vote",
  night_kill_nominate: "Night kill nominate",
  check: "Check",
  save: "Save",
  poison: "Poison",
  hunter_shoot: "Hunter shoot",
  skip: "Skip"
};

export function formatAction(action?: ActionType | string | null): string {
  if (!action) return "Unknown action";
  return ACTION_LABELS[action as ActionType] ?? action;
}
