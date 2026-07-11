import { PendingBackendPanel } from "@/components/ui/PendingBackendPanel";

import { RunAuditNav } from "./RunAuditNav";

type PendingAuditPageProps = {
  apiName: string;
  gameId: string;
  title: string;
};

export function PendingAuditPage({ apiName, gameId, title }: PendingAuditPageProps) {
  return (
    <section className="screen">
      <header className="audit-header">
        <div>
          <p className="eyebrow">Audit</p>
          <h1 className="page-title">{gameId}</h1>
        </div>
      </header>
      <RunAuditNav gameId={gameId} />
      <PendingBackendPanel apiName={apiName} title={title}>
        This tab is wired as a route boundary only. It should stay empty until
        the corresponding HTTP API exists.
      </PendingBackendPanel>
    </section>
  );
}
