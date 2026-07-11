"use client";

import { useState } from "react";

import { GameBoard } from "@/components/GameBoard";
import type { PlayerReplayEvent, PlayerSeatView } from "@/lib/replay";

type ReplayRoomProps = {
  gameId: string;
  players: PlayerSeatView[];
  events: PlayerReplayEvent[];
  winner: string | null;
};

type ViewMode = "normal" | "god";

export function ReplayRoom({ events, gameId, players, winner }: ReplayRoomProps) {
  const [viewMode, setViewMode] = useState<ViewMode>("normal");

  return (
    <GameBoard
      events={events}
      gameId={gameId}
      onViewModeChange={setViewMode}
      players={players}
      viewMode={viewMode}
      winner={winner}
    />
  );
}
