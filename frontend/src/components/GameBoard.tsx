"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { formatCamp, formatPhase, formatRole } from "@/lib/formatters";
import { getSeatStates } from "@/lib/replay";
import type { PlayerReplayEvent, PlayerSeatView } from "@/lib/replay";

type ViewMode = "normal" | "god";
type LogTab = "event" | "speech" | "vote" | "system";

type GameBoardProps = {
  players: PlayerSeatView[];
  events: PlayerReplayEvent[];
  viewMode: ViewMode;
  onViewModeChange: (viewMode: ViewMode) => void;
  gameId?: string;
  winner?: string | null;
  title?: string;
  seal?: string;
  phaseLabel?: string;
  mode?: "replay" | "live" | "player";
  allowViewModeSwitch?: boolean;
  selectableSeats?: string[];
  selectedSeat?: string | null;
  onSeatSelect?: (playerId: string) => void;
};

const LOG_TABS: { id: LogTab; label: string }[] = [
  { id: "event", label: "事件记录" },
  { id: "speech", label: "发言记录" },
  { id: "vote", label: "投票记录" },
  { id: "system", label: "系统消息" }
];

const TAB_TONES: Record<LogTab, PlayerReplayEvent["tone"][] | null> = {
  event: null,
  speech: ["speech"],
  vote: ["vote"],
  system: ["system", "death", "result"]
};

export function GameBoard({
  allowViewModeSwitch = true,
  events,
  gameId,
  mode = "replay",
  onSeatSelect,
  onViewModeChange,
  phaseLabel = "当前阶段",
  players,
  seal = mode === "player" ? "玩家" : mode === "live" ? "观战" : "復盤",
  selectableSeats,
  selectedSeat,
  title = mode === "player" ? "真人对局" : mode === "live" ? "实时观战" : "历史回放",
  viewMode,
  winner
}: GameBoardProps) {
  const [index, setIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [logTab, setLogTab] = useState<LogTab>("event");
  const [playbackSpeed, setPlaybackSpeed] = useState<1 | 2 | 3>(1);
  // 实时模式默认跟随最新；用户点上一步/下一步后暂停跟随，可「回到最新」恢复。
  const [followLive, setFollowLive] = useState(true);

  useEffect(() => {
    if ((mode === "live" || mode === "player") && followLive) {
      setIndex(Math.max(events.length - 1, 0));
    }
  }, [events.length, mode, followLive]);

  const current = events[index];
  const visibleEvents = useMemo(() => events.slice(0, index + 1), [events, index]);
  const seats = useMemo(
    () => getSeatStates(players, visibleEvents),
    [players, visibleEvents]
  );

  const feedEvents = useMemo(() => {
    const tones = TAB_TONES[logTab];
    return events
      .map((entry, eventIndex) => ({ entry, eventIndex }))
      .filter(({ entry }) => !tones || tones.includes(entry.tone));
  }, [events, logTab]);

  useEffect(() => {
    if (mode === "live" || mode === "player" || !isPlaying || index >= events.length - 1) return;
    const timer = window.setTimeout(
      () => setIndex((value) => value + 1),
      1400 / playbackSpeed
    );
    return () => window.clearTimeout(timer);
  }, [events.length, index, isPlaying, mode, playbackSpeed]);

  useEffect(() => {
    if (index >= events.length - 1) setIsPlaying(false);
  }, [events.length, index]);

  if (events.length === 0) {
    return (
      <section className="notice-panel">
        暂无可供玩家层展示的公开事件。
      </section>
    );
  }

  const dayNodes = buildDayNodes(events);
  const activeDayNode = current ? activeDayNodeIndex(dayNodes, current) : 0;
  const progress = activeDayNode / Math.max(dayNodes.length - 1, 1);
  const isPlayerMode = mode === "player";

  return (
    <section className="prototype-replay">
      <header className="replay-topbar">
        <div className="topbar-left">
          <span className="wolf-medal" aria-hidden="true">
            狼
          </span>
          <div className="brand-text">
            <h1>{title}</h1>
            <span className="brand-seal">{seal}</span>
          </div>
          <Link className="home-icon" href="/" aria-label="返回主菜单" title="返回主菜单">
            ⌂
          </Link>
        </div>

        <div className="topbar-center">
          <div className="match-info" aria-label="对局信息">
            {gameId ? <span>对局编号：{gameId}</span> : null}
            <span>{players.length} 人局</span>
            {mode === "live" ? <span className="live-dot">实时</span> : null}
          </div>
          <div className="phase-now">
            <strong>
              {current ? `第 ${current.round} 天 · ${formatPhase(current.phase)}` : title}
            </strong>
            <span>{phaseLabel}</span>
          </div>
        </div>

        {allowViewModeSwitch ? (
        <div className="topbar-right">
          <div className="view-switch" role="group" aria-label="观战视角">
            <button
              className={viewMode === "normal" ? "active" : ""}
              onClick={() => onViewModeChange("normal")}
              type="button"
            >
              普通视角
            </button>
            <button
              className={viewMode === "god" ? "active" : ""}
              onClick={() => onViewModeChange("god")}
              type="button"
            >
              上帝视角
            </button>
          </div>
        </div>
        ) : null}
      </header>

      <div className="replay-grid">
        <section className="table-map" aria-label="玩家圆桌">
          <div className="replay-stage">
            <div className="map-ink" aria-hidden="true" />
            <div className="round-table" aria-hidden="true" />

            <div className="avatar-ring">
              {seats.map((seat, seatIndex) => {
                const isFocused =
                  seat.playerId === current?.actor ||
                  seat.playerId === current?.target;
                const isSelectable = Boolean(
                  onSeatSelect && selectableSeats?.includes(seat.playerId)
                );
                const isSelected = isSelectable && seat.playerId === selectedSeat;
                return (
                  <article
                    className={[
                      "avatar-card",
                      seat.isAlive ? "" : "dead",
                      isFocused ? "focused" : "",
                      isSelectable ? "selectable" : "",
                      isSelected ? "selected" : ""
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    key={seat.playerId}
                    onClick={isSelectable ? () => onSeatSelect?.(seat.playerId) : undefined}
                    onKeyDown={
                      isSelectable
                        ? (event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              onSeatSelect?.(seat.playerId);
                            }
                          }
                        : undefined
                    }
                    role={isSelectable ? "button" : undefined}
                    style={seatPosition(seatIndex, seats.length)}
                    tabIndex={isSelectable ? 0 : undefined}
                  >
                    <span className="card-corner tl" aria-hidden="true" />
                    <span className="card-corner tr" aria-hidden="true" />
                    <span className="card-corner bl" aria-hidden="true" />
                    <span className="card-corner br" aria-hidden="true" />
                    <div
                      className={`portrait portrait-${seatIndex % 5}`}
                      style={
                        {
                          "--seat-photo": `url('/avatars/${seatOrdinal(seat.playerId)}.png')`
                        } as React.CSSProperties
                      }
                    >
                      <span className="portrait-no">{seatOrdinal(seat.playerId)}</span>
                    </div>
                    <div className="seat-copy">
                      <div className="seat-head">
                        <strong>{formatPlayerLabel(seat.playerId)}</strong>
                        <span className={seat.isAlive ? "state alive" : "state dead"}>
                          {seat.isAlive ? "存活" : "出局"}
                        </span>
                      </div>
                      <span className="seat-claim">
                        {viewMode === "god"
                          ? `${seat.role ? formatRole(seat.role) : "身份待同步"}${
                              seat.camp ? ` / ${formatCamp(seat.camp)}` : ""
                            }`
                          : seat.publicClaim
                            ? `自称${seat.publicClaim}`
                            : "未跳身份"}
                      </span>
                      <em
                        className={seat.voteTarget ? "seat-vote acting" : "seat-vote"}
                      >
                        {seat.voteTarget
                          ? `投 ${formatPlayerLabel(seat.voteTarget)}`
                          : "—"}
                      </em>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </section>

        <aside className={isPlayerMode ? "log-scroll player-event-list-only" : "log-scroll"}>
          <span className="scroll-cap top" aria-hidden="true" />
          <nav className="log-tabs" aria-label="记录分类">
            {LOG_TABS.map((tab) => (
              <button
                className={tab.id === logTab ? "active" : ""}
                key={tab.id}
                onClick={() => setLogTab(tab.id)}
                type="button"
              >
                {tab.label}
              </button>
            ))}
          </nav>

          {current && !isPlayerMode ? (
            <article className={`current-scroll-event tone-${current.tone}`}>
              <p>{formatPhase(current.phase)}</p>
              <h2>{current.title}</h2>
              <span>{current.body}</span>
            </article>
          ) : null}

          {!isPlayerMode ? (
          <div className="log-expand-button" aria-label="事件列表">
            <span>事件列表</span>
            <strong>{feedEvents.length} 条⌄</strong>
          </div>
          ) : null}

          <div className="scroll-feed">
            {feedEvents.length === 0 ? (
              <p className="scroll-empty">该分类暂无记录。</p>
            ) : (
              feedEvents.map(({ entry, eventIndex }) => (
                <button
                  className={[
                    "scroll-feed-item",
                    `tone-${entry.tone}`,
                    eventIndex === index ? "active" : "",
                    eventIndex > index ? "future" : ""
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  key={entry.key}
                  onClick={() => {
                    // 用户主动点某条事件 = 想停下来细看。实时模式下暂停跟随
                    // （否则新发言一来又把视图跳走，可点「回到最新」恢复）；
                    // 回放模式下暂停自动播放，停在这条上。
                    if (mode === "live" || mode === "player") setFollowLive(false);
                    else setIsPlaying(false);
                    setIndex(eventIndex);
                  }}
                  type="button"
                >
                  <span className="feed-day">
                    第 {entry.round} 天{entry.createdAt ? ` · ${formatEventTime(entry.createdAt)}` : ""}
                  </span>
                  <strong>{entry.title}</strong>
                  <small>{entry.body}</small>
                </button>
              ))
            )}
          </div>
          <span className="scroll-cap bottom" aria-hidden="true" />
        </aside>
      </div>

      {mode === "replay" ? (
        <footer className="replay-controls">
          <div className="control-row">
            <button
              className="play-button"
              onClick={() => setIsPlaying((value) => !value)}
              type="button"
            >
              {isPlaying ? "暂停" : "播放"}
            </button>
            <button
              className="wood-button"
              disabled={index === 0}
              onClick={() => setIndex((value) => Math.max(0, value - 1))}
              type="button"
            >
              上一条
            </button>
            <button
              className="wood-button"
              disabled={index >= events.length - 1}
              onClick={() => setIndex((value) => Math.min(events.length - 1, value + 1))}
              type="button"
            >
              下一条
            </button>
            <button
              className="wood-button"
              onClick={() => setPlaybackSpeed(nextPlaybackSpeed)}
              type="button"
            >
              速度 {playbackSpeed}x
            </button>
            <button
              className="wood-button"
              onClick={() => {
                setIndex(0);
                setIsPlaying(false);
              }}
              type="button"
            >
              重新播放
            </button>
          </div>

          <div
            className="phase-rail"
            aria-label="阶段进度"
            style={
              {
                "--node-count": dayNodes.length
              } as React.CSSProperties
            }
          >
            <span className="phase-rail-track" aria-hidden="true">
              <span
                className="phase-rail-fill"
                style={{ width: `${progress * 100}%` }}
              />
            </span>
            {dayNodes.map((node, nodeIndex) => {
              return (
                <span
                  className={[
                    "phase-node",
                    nodeIndex < activeDayNode ? "done" : "",
                    nodeIndex === activeDayNode ? "active" : ""
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  key={`${node.label}-${nodeIndex}`}
                >
                  <i>{node.icon}</i>
                  {node.label}
                </span>
              );
            })}
          </div>

          {winner ? <p className="winner-note">结算：{formatWinner(winner)}胜利</p> : null}
        </footer>
      ) : mode === "player" ? null : (
        <footer className="live-controls">
          <div className="control-row">
            <button
              className="wood-button"
              disabled={index === 0}
              onClick={() => {
                setFollowLive(false);
                setIndex((value) => Math.max(0, value - 1));
              }}
              type="button"
            >
              上一步
            </button>
            <button
              className="wood-button"
              disabled={index >= events.length - 1}
              onClick={() => {
                setFollowLive(false);
                setIndex((value) => Math.min(events.length - 1, value + 1));
              }}
              type="button"
            >
              下一步
            </button>
            <button
              className={followLive ? "wood-button active" : "wood-button"}
              onClick={() => {
                setFollowLive(true);
                setIndex(Math.max(events.length - 1, 0));
              }}
              type="button"
            >
              {followLive ? "跟随中" : "回到最新"}
            </button>
          </div>
          <span>
            已同步 {events.length} 条事件 · 当前第 {Math.min(index + 1, events.length)} 条
          </span>
          {winner ? <strong>结算：{formatWinner(winner)}胜利</strong> : null}
        </footer>
      )}
    </section>
  );
}

type DayNode = {
  key: string;
  label: string;
  icon: string;
  round?: number;
  isFinal?: boolean;
};

function buildDayNodes(events: PlayerReplayEvent[]): DayNode[] {
  const rounds = Array.from(new Set(events.map((event) => event.round))).sort(
    (left, right) => left - right
  );
  const nodes: DayNode[] = rounds.map((round) => ({
    key: `round-${round}`,
    label: `第 ${round} 天`,
    icon: String(round),
    round
  }));
  if (events.some((event) => event.eventType === "game_over")) {
    nodes.push({ key: "game-over", label: "对局结束", icon: "终", isFinal: true });
  }
  return nodes.length > 0 ? nodes : [{ key: "round-1", label: "第 1 天", icon: "1", round: 1 }];
}

function activeDayNodeIndex(nodes: DayNode[], event: PlayerReplayEvent): number {
  if (event.eventType === "game_over") {
    const finalIndex = nodes.findIndex((node) => node.isFinal);
    if (finalIndex >= 0) return finalIndex;
  }
  const dayIndex = nodes.findIndex((node) => node.round === event.round);
  return dayIndex >= 0 ? dayIndex : 0;
}

function nextPlaybackSpeed(speed: 1 | 2 | 3): 1 | 2 | 3 {
  if (speed === 1) return 2;
  if (speed === 2) return 3;
  return 1;
}

function seatPosition(index: number, total: number): React.CSSProperties {
  const angle = -Math.PI / 2 + (index / total) * Math.PI * 2;
  const rx = 38;
  const ry = 34;
  const left = 50 + rx * Math.cos(angle);
  const top = 50 + ry * Math.sin(angle);
  return {
    left: `${left}%`,
    top: `${top}%`,
    transform: "translate(-50%, -50%)"
  };
}

function seatOrdinal(playerId?: string | null): number {
  const numeric = playerId?.match(/^P(\d+)$/i)?.[1];
  return numeric ? Number(numeric) : 1;
}

function formatPlayerLabel(playerId?: string | null): string {
  if (!playerId) return "玩家";
  const numeric = playerId.match(/^P(\d+)$/i)?.[1];
  const labels: Record<string, string> = {
    "1": "一号",
    "2": "二号",
    "3": "三号",
    "4": "四号",
    "5": "五号",
    "6": "六号",
    "7": "七号",
    "8": "八号",
    "9": "九号"
  };
  return numeric ? labels[numeric] ?? `${numeric}号` : playerId;
}

function formatWinner(winner: string): string {
  if (winner === "villagers" || winner === "villager") return "好人阵营";
  if (winner === "werewolves" || winner === "werewolf") return "狼人阵营";
  return winner;
}

function formatEventTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
}
