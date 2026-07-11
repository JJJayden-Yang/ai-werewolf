import { LiveSpectatorRoom } from "./LiveSpectatorRoom";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function LiveSpectatorPage({ params }: PageProps) {
  const { gameId } = await params;
  return <LiveSpectatorRoom gameId={gameId} />;
}
