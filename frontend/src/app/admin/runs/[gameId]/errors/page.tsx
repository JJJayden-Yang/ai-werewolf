import { PendingAuditPage } from "../PendingAuditPage";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function AdminRunErrorsPage({ params }: PageProps) {
  const { gameId } = await params;
  return (
    <PendingAuditPage
      apiName="auditApi.getBatchReport(runId) / future run errors API"
      gameId={gameId}
      title="Errors"
    />
  );
}
