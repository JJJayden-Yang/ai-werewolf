import { replayApi } from "@/lib/api";
import {
  getLivePlayerSeatViews,
  getPlayerReplayEvents,
  getPlayerSeatViews,
  getReplayWinner
} from "@/lib/replay";

import { ReplayRoom } from "./ReplayRoom";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

export default async function PlayerReplayPage({ params }: PageProps) {
  const { gameId } = await params;
  const replay = await replayApi.getReplay(gameId);
  const isGameOver = replay.events.some((event) => event.event_type === "game_over");
  const playerEvents = getPlayerReplayEvents(replay.events);
  const playerSeats =
    replay.players.length > 0
      ? getPlayerSeatViews(replay.players)
      : getLivePlayerSeatViews(replay.events, inferPlayerCount(replay.events));
  const winner = getReplayWinner(replay.events);

  return (
    <section className="screen">
      {!isGameOver ? (
        <section className="notice-panel">
          该对局尚未结束，玩家层回放将在结算后开放。
        </section>
      ) : (
        <ReplayRoom
          events={playerEvents}
          gameId={replay.gameId}
          players={playerSeats}
          winner={winner}
        />
      )}
    </section>
  );
}

function inferPlayerCount(events: { event_type: string; payload: Record<string, unknown> }[]): 6 | 9 {
  const roleAssigned = events.find((event) => event.event_type === "role_assigned");
  const playerCount = roleAssigned?.payload.player_count;
  return playerCount === 6 ? 6 : 9;
}
