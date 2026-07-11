import { auditApi } from "@/lib/api";
import { formatEventType, formatPhase } from "@/lib/formatters";

import { RunAuditNav } from "../RunAuditNav";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function AdminRunEventsPage({ params }: PageProps) {
  const { gameId } = await params;
  const events = await auditApi.getEvents(gameId);

  return (
    <section className="screen">
      <header className="audit-header">
        <div>
          <p className="eyebrow">Audit Events</p>
          <h1 className="page-title">{gameId}</h1>
        </div>
        <div className="room-stats">
          <span>{events.length} events</span>
        </div>
      </header>
      <RunAuditNav gameId={gameId} />

      <section className="audit-panel">
        <div className="event-table">
          <div className="event-table-head">
            <span>ID</span>
            <span>Round</span>
            <span>Phase</span>
            <span>Type</span>
            <span>Actor</span>
            <span>Target</span>
            <span>Visibility</span>
            <span>Payload</span>
          </div>
          {events.map((event) => (
            <div className="event-table-row" key={event.event_id}>
              <span>{event.event_id}</span>
              <span>{event.round}</span>
              <span>{formatPhase(event.phase)}</span>
              <span>{formatEventType(event.event_type)}</span>
              <span>{event.actor ?? "-"}</span>
              <span>{event.target ?? "-"}</span>
              <span>{event.visibility}</span>
              <code>{JSON.stringify(event.payload)}</code>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}
