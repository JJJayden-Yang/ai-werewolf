import { PendingAuditPage } from "../PendingAuditPage";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function AdminRunDecisionsPage({ params }: PageProps) {
  const { gameId } = await params;
  return (
    <PendingAuditPage
      apiName="auditApi.getDecisionTraces(gameId)"
      gameId={gameId}
      title="Decision Traces"
    />
  );
}
