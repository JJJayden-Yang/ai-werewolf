import type { GameEvent, TimelineGroup } from "@/lib/types/contracts";

export function groupEventsByRoundPhase(events: GameEvent[]): TimelineGroup[] {
  const groups = new Map<string, TimelineGroup>();

  for (const event of events) {
    const key = `${event.round}:${event.phase}`;
    const existing = groups.get(key);

    if (existing) {
      existing.events.push(event);
      continue;
    }

    groups.set(key, {
      key,
      round: event.round,
      phase: event.phase,
      events: [event]
    });
  }

  return Array.from(groups.values()).sort((left, right) => {
    if (left.round !== right.round) return left.round - right.round;
    return firstEventIndex(events, left) - firstEventIndex(events, right);
  });
}

function firstEventIndex(events: GameEvent[], group: TimelineGroup): number {
  const first = group.events[0];
  if (!first) return Number.MAX_SAFE_INTEGER;
  return events.findIndex((event) => event.event_id === first.event_id);
}
