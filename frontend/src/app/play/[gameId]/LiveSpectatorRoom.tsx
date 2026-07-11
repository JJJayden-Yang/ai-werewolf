"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { GameBoard } from "@/components/GameBoard";
import { gameApi } from "@/lib/api";
import type { LiveGameStatus } from "@/lib/api/gameApi";
import {
  getLivePlayerSeatViews,
  getPlayerReplayEvents,
  getReplayWinner
} from "@/lib/replay";
import type { GameEvent } from "@/lib/types/contracts";

type LiveSpectatorRoomProps = {
  gameId: string;
};

type ViewMode = "normal" | "god";

export function LiveSpectatorRoom({ gameId }: LiveSpectatorRoomProps) {
  const [viewMode, setViewMode] = useState<ViewMode>("normal");
  const [rawEvents, setRawEvents] = useState<GameEvent[]>([]);
  const [status, setStatus] = useState<LiveGameStatus>("pending");
  const [winner, setWinner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playerCount, setPlayerCount] = useState<6 | 9>(9);
  const [roleMap, setRoleMap] = useState<Record<string, string> | null>(null);
  const cursorRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    gameApi
      .listGames()
      .then((response) => {
        if (cancelled) return;
        const current = response.games.find((game) => game.game_id === gameId);
        if (current) setPlayerCount(current.player_count);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let interval: number | undefined;

    async function poll() {
      if (inFlight) return;
      inFlight = true;
      try {
        const currentStatus = await gameApi.getStatus(gameId);
        if (cancelled) return;
        setStatus(currentStatus.status);
        setWinner(currentStatus.winner);
        setError(currentStatus.error);
        if (currentStatus.role_map) setRoleMap(currentStatus.role_map);

        if (currentStatus.status === "error") {
          if (interval) window.clearInterval(interval);
          return;
        }

        if (currentStatus.status === "running" || currentStatus.status === "finished") {
          const response = await gameApi.getEvents(gameId, cursorRef.current);
          if (cancelled) return;
          if (response.events.length > 0) {
            setRawEvents((events) => [...events, ...response.events]);
          }
          cursorRef.current = response.next_cursor;
          if (response.status === "finished") {
            setStatus("finished");
            if (interval) window.clearInterval(interval);
          }
        }
      } catch (caught) {
        if (!cancelled) {
          setStatus("error");
          setError(caught instanceof Error ? caught.message : "轮询事件失败");
          if (interval) window.clearInterval(interval);
        }
      } finally {
        inFlight = false;
      }
    }

    void poll();
    interval = window.setInterval(() => void poll(), 1500);

    return () => {
      cancelled = true;
      if (interval) window.clearInterval(interval);
    };
  }, [gameId]);

  const playerEvents = useMemo(
    () => getPlayerReplayEvents(rawEvents, { includeNightActions: viewMode === "god" }),
    [rawEvents, viewMode]
  );
  const players = useMemo(
    () => getLivePlayerSeatViews(rawEvents, playerCount, roleMap),
    [playerCount, rawEvents, roleMap]
  );
  const finalWinner = winner ?? getReplayWinner(rawEvents);

  if (status === "pending" && rawEvents.length === 0) {
    return (
      <section className="screen">
        <section className="notice-panel live-state-panel">
          <p className="eyebrow">Live Spectator</p>
          <h1>对局启动中…</h1>
          <span>正在等待后端创建事件流。</span>
        </section>
      </section>
    );
  }

  if (status === "error") {
    return (
      <section className="screen">
        <section className="notice-panel live-state-panel">
          <p className="eyebrow">Live Spectator</p>
          <h1>对局异常</h1>
          <span>{error ?? "后端返回错误状态。"}</span>
          <Link className="menu-button primary" href="/play">
            <span>重新开始</span>
            <span>Open</span>
          </Link>
        </section>
      </section>
    );
  }

  return (
    <section className="screen">
      <GameBoard
        events={playerEvents}
        gameId={gameId}
        mode="live"
        onViewModeChange={setViewMode}
        phaseLabel={status === "finished" ? "最终阶段" : "实时阶段"}
        players={players}
        title={status === "finished" ? "观战结算" : "实时观战"}
        viewMode={viewMode}
        winner={finalWinner}
      />
    </section>
  );
}
