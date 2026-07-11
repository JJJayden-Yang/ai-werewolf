import { HumanLiveGameClient } from "./HumanLiveGameClient";

type PageProps = {
  params: Promise<{ gameId: string }>;
  searchParams: Promise<{ player_id?: string }>;
};

export default async function PlayerLiveGamePage({ params, searchParams }: PageProps) {
  const { gameId } = await params;
  const { player_id: playerId = "P1" } = await searchParams;

  return <HumanLiveGameClient gameId={gameId} playerId={playerId} />;
}
