import { PendingAuditPage } from "../PendingAuditPage";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function AdminRunContextPage({ params }: PageProps) {
  const { gameId } = await params;
  return (
    <PendingAuditPage
      apiName="auditApi.getContextSnapshots(gameId)"
      gameId={gameId}
      title="Context Snapshots"
    />
  );
}
