import { PendingAuditPage } from "../PendingAuditPage";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function AdminRunBeliefPage({ params }: PageProps) {
  const { gameId } = await params;
  return (
    <PendingAuditPage
      apiName="auditApi.getBeliefCurves(gameId) / auditApi.getBeliefUpdates(gameId)"
      gameId={gameId}
      title="Belief Audit"
    />
  );
}
