import { auditApi } from "@/lib/api";

import { RunAuditNav } from "../RunAuditNav";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function AdminRunRawPage({ params }: PageProps) {
  const { gameId } = await params;
  const replay = await auditApi.getRawReplay(gameId);

  return (
    <section className="screen">
      <header className="audit-header">
        <div>
          <p className="eyebrow">Raw ReplayData</p>
          <h1 className="page-title">{gameId}</h1>
        </div>
      </header>
      <RunAuditNav gameId={gameId} />

      <section className="audit-panel">
        <pre className="raw-json">{JSON.stringify(replay, null, 2)}</pre>
      </section>
    </section>
  );
}
