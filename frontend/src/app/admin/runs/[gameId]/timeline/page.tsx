import { replayApi } from "@/lib/api";
import {
  formatCamp,
  formatEventLocation,
  formatEventSummary,
  formatEventType,
  formatPhase,
  formatPlayerStatus,
  formatRole
} from "@/lib/formatters";

import { RunAuditNav } from "../RunAuditNav";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function AdminRunTimelinePage({ params }: PageProps) {
  const { gameId } = await params;
  const replay = await replayApi.getReplay(gameId);
  const gameOver = replay.events.find((event) => event.event_type === "game_over");
  const winner = gameOver?.payload.winner;

  return (
    <section className="screen">
      <header className="audit-header">
        <div>
          <p className="eyebrow">Audit Timeline</p>
          <h1 className="page-title">{replay.gameId}</h1>
        </div>
        <div className="room-stats">
          <span>{replay.players.length} players</span>
          <span>{replay.events.length} events</span>
          <span>{typeof winner === "string" ? `${winner} win` : "winner unknown"}</span>
        </div>
      </header>
      <RunAuditNav gameId={replay.gameId} />

      <div className="audit-grid">
        <aside className="audit-panel">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Players</h2>
          <div style={{ display: "grid", gap: 10 }}>
            {replay.players.map((player) => (
              <div key={player.player_id}>
                <strong>{player.player_id}</strong>
                <div style={{ color: "var(--muted)" }}>
                  {formatRole(player.role)} / {formatCamp(player.camp)} /{" "}
                  {formatPlayerStatus(player.final_status)}
                </div>
              </div>
            ))}
          </div>
        </aside>

        <section className="audit-panel">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Timeline</h2>
          <div style={{ display: "grid", gap: 14 }}>
            {replay.timeline.map((group) => (
              <article className="audit-timeline-group" key={group.key}>
                <strong>
                  R{group.round} / {formatPhase(group.phase)}
                </strong>
                <ul>
                  {group.events.map((event) => (
                    <li key={event.event_id}>
                      <span>{formatEventLocation(event)}</span>
                      <strong>{formatEventType(event.event_type)}</strong>
                      <span>{formatEventSummary(event)}</span>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </section>

        <aside className="audit-panel">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Event Counts</h2>
          <div className="audit-kv-list">
            {Object.entries(countEvents(replay.events)).map(([type, count]) => (
              <div key={type}>
                <span>{type}</span>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </section>
  );
}

function countEvents(events: { event_type: string }[]) {
  return events.reduce<Record<string, number>>((acc, event) => {
    acc[event.event_type] = (acc[event.event_type] ?? 0) + 1;
    return acc;
  }, {});
}
