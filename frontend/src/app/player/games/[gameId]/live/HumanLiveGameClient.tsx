"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { GameBoard } from "@/components/GameBoard";
import { gameApi } from "@/lib/api";
import { formatApiError } from "@/lib/api/errors";
import type { PlayerPrivateEvent, PlayerVisiblePlayer } from "@/lib/api/gameApi";
import { formatAction } from "@/lib/formatters/action";
import { formatPhase } from "@/lib/formatters/phase";
import { formatRole } from "@/lib/formatters/role";
import { getPlayerReplayEvents, getReplayWinner } from "@/lib/replay";
import type { PlayerSeatView } from "@/lib/replay";
import type { ActionType, GameEvent, Role } from "@/lib/types/contracts";

type Props = {
  gameId: string;
  playerId: string;
};

type PendingContext = {
  game_id: string;
  agent_id: string;
  role: Role;
  round: number;
  phase: string;
  allowed_actions?: ActionType[];
  visible_players?: PlayerVisiblePlayer[];
  private_events?: PlayerPrivateEvent[];
  tie_candidates?: string[];
};

const TARGET_ACTIONS = new Set<ActionType>([
  "check",
  "vote",
  "night_kill_nominate",
  "save",
  "poison",
  "hunter_shoot"
]);

export function HumanLiveGameClient({ gameId, playerId }: Props) {
  const [safeEvents, setSafeEvents] = useState<GameEvent[]>([]);
  const [visiblePlayers, setVisiblePlayers] = useState<PlayerVisiblePlayer[]>([]);
  const [privateEvents, setPrivateEvents] = useState<PlayerPrivateEvent[]>([]);
  const [ownRole, setOwnRole] = useState<Role | null>(null);
  const [pending, setPending] = useState<PendingContext | null>(null);
  const [message, setMessage] = useState("我会继续观察大家的发言和投票。");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // 真相身份只在对局结束后拉取并公示，避免对局中泄漏（信息隔离红线）
  const [roleMap, setRoleMap] = useState<Record<string, string> | null>(null);
  const cursorRef = useRef(0);

  useEffect(() => {
    let ignore = false;
    async function poll() {
      try {
        const [eventResponse, pendingResponse] = await Promise.all([
          gameApi.getPlayerEvents(gameId, playerId, cursorRef.current),
          gameApi.getPending(gameId, playerId)
        ]);
        if (ignore) return;
        cursorRef.current = eventResponse.next_cursor;
        if (eventResponse.events.length > 0) {
          setSafeEvents((current) => [...current, ...eventResponse.events]);
        }
        setVisiblePlayers(eventResponse.visible_players);
        setPrivateEvents((current) => mergePrivateEvents(current, eventResponse.private_events));
        setOwnRole(eventResponse.role);
        setPending(pendingResponse.pending ? (pendingResponse.context as PendingContext) : null);
        setError(null);
      } catch (caught) {
        if (!ignore) setError(formatApiError(caught, "读取对局状态失败"));
      }
    }
    void poll();
    const timer = window.setInterval(poll, 1200);
    return () => {
      ignore = true;
      window.clearInterval(timer);
    };
  }, [gameId, playerId]);

  const playerEvents = useMemo(() => getPlayerReplayEvents(safeEvents), [safeEvents]);
  const winner = useMemo(() => getReplayWinner(safeEvents), [safeEvents]);
  const isFinished = Boolean(winner) || safeEvents.some((event) => event.event_type === "game_over");
  useEffect(() => {
    if (!isFinished || roleMap) return;
    let ignore = false;
    gameApi
      .getStatus(gameId)
      .then((status) => {
        if (!ignore && status.role_map) setRoleMap(status.role_map);
      })
      .catch(() => undefined);
    return () => {
      ignore = true;
    };
  }, [isFinished, roleMap, gameId]);
  const ownStatus = useMemo(
    () => visiblePlayers.find((player) => player.player_id === playerId)?.status ?? "alive",
    [visiblePlayers, playerId]
  );
  // 已死但仍有待办动作（如被放逐的猎人开枪）时，优先展示动作而非死亡通知
  const isOut = !isFinished && ownStatus === "dead" && !pending;
  const deathRound = useMemo(() => {
    const ownDeath = [...playerEvents]
      .reverse()
      .find((event) => event.tone === "death" && event.target === playerId);
    if (ownDeath) return ownDeath.round;
    const rounds = playerEvents.map((event) => event.round);
    return rounds.length > 0 ? Math.max(...rounds) : undefined;
  }, [playerEvents, playerId]);
  const allowedActions = pending?.allowed_actions ?? [];
  const dockMode = isFinished
    ? "finished"
    : allowedActions.includes("speak")
      ? "speak"
      : allowedActions.some((action) => TARGET_ACTIONS.has(action))
        ? "target"
        : pending
          ? "act"
          : "idle";
  const intelEvents = useMemo(
    () => mergePrivateEvents(privateEvents, pending?.private_events ?? []),
    [privateEvents, pending]
  );
  const targetActions = useMemo(
    () => allowedActions.filter((action) => TARGET_ACTIONS.has(action)),
    [allowedActions]
  );
  const targetActionsKey = targetActions.join("|");
  const [armedAction, setArmedAction] = useState<ActionType | null>(null);
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null);
  useEffect(() => {
    setArmedAction(targetActions.length === 1 ? targetActions[0] : null);
    setSelectedTarget(null);
    // 仅当本阶段可选目标动作的集合变化时重置（targetActionsKey 表征该集合）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetActionsKey]);
  const players = useMemo<PlayerSeatView[]>(() => {
    const source =
      visiblePlayers.length > 0
        ? visiblePlayers
        : Array.from({ length: 9 }, (_, index) => ({
            player_id: `P${index + 1}`,
            status: "alive" as const,
            public_claim: null
          }));
    return source.map((player) => {
      const role = roleMap ? ((roleMap[player.player_id] as Role | undefined) ?? null) : null;
      return {
        playerId: player.player_id,
        role,
        // 阵营由真实身份推导：狼人 → werewolf，其余 → villager
        camp: role ? (role === "werewolf" ? "werewolf" : "villager") : null
      };
    });
  }, [visiblePlayers, roleMap]);

  const availableTargets = useMemo(() => {
    const source = pending?.visible_players ?? visiblePlayers;
    return source.filter((player) => player.player_id !== playerId && player.status === "alive");
  }, [pending, playerId, visiblePlayers]);

  async function submit(actionType: ActionType, target?: string) {
    setIsSubmitting(true);
    setError(null);
    try {
      await gameApi.submitAction(gameId, {
        player_id: playerId,
        action_type: actionType,
        target,
        public_message: actionType === "speak" ? message : undefined
      });
      setPending(null);
    } catch (caught) {
      setError(formatApiError(caught, "提交动作失败"));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="screen player-live-board">
      <GameBoard
        allowViewModeSwitch={false}
        events={playerEvents}
        gameId={gameId}
        mode="player"
        onSeatSelect={armedAction ? (target) => setSelectedTarget(target) : undefined}
        onViewModeChange={() => undefined}
        phaseLabel={
          isFinished
            ? roleMap
              ? "对局结束 · 身份公示"
              : "对局结束"
            : pending
              ? `${playerId} · ${formatRole(pending.role)}`
              : "玩家视角"
        }
        players={players}
        seal="玩家"
        selectableSeats={armedAction ? availableTargets.map((player) => player.player_id) : undefined}
        selectedSeat={selectedTarget}
        title="真人对局"
        viewMode={isFinished && roleMap ? "god" : "normal"}
        winner={winner}
      />

      <aside className={`player-action-dock mode-${isOut ? "out" : dockMode}`}>
        <div className="dock-identity-stack">
          {intelEvents.length > 0 ? (
            <aside className="player-intel-overlay">
              <strong>你的情报</strong>
              {intelEvents.slice(-5).map((event, index) => (
                <span key={`${event.event_type}-${event.round ?? "x"}-${event.target ?? "none"}-${index}`}>
                  {formatPrivateEvent(event)}
                </span>
              ))}
            </aside>
          ) : null}
          <div className="dock-identity">
            <span>你的视角</span>
            <strong>{playerId} · {formatRole(pending?.role ?? ownRole)}</strong>
          </div>
        </div>

        <div className="dock-status">
          {isFinished ? (
            <em className="finished">对局结束</em>
          ) : isOut ? (
            <em className="dead-badge">👻 你已出局</em>
          ) : pending ? (
            <em>
              轮到你 · 第 {pending.round} 轮 {formatPhase(pending.phase)}
            </em>
          ) : (
            <em className="waiting">等待中</em>
          )}
        </div>

        <div className="dock-action">
          {error ? <p className="form-error">{error}</p> : null}
          {isFinished ? (
            <p className="player-action-status">
              结算：{winner ? `${formatWinner(winner)}胜利` : "对局已经结束"}。
            </p>
          ) : isOut ? (
            <div className="dock-dead">
              <span className="dock-dead-info">
                <strong>死亡</strong>
                {deathRound ? ` · 第 ${deathRound} 回合` : ""}
              </span>
              <span className="dock-spectating">👀 观战中</span>
            </div>
          ) : dockMode === "speak" ? (
            <div className="dock-action-row action-speak">
              <textarea
                className="text-input dock-composer"
                onChange={(event) => setMessage(event.target.value)}
                placeholder="输入你的发言…"
                value={message}
              />
              <button
                className="play-button"
                disabled={isSubmitting}
                onClick={() => submit("speak")}
                type="button"
              >
                提交发言
              </button>
            </div>
          ) : targetActions.length > 0 ? (
            <div className="dock-action-row action-target">
              {targetActions.length > 1 ? (
                <div className="dock-action-chips">
                  {targetActions.map((action) => (
                    <button
                      className={armedAction === action ? "chip active" : "chip"}
                      disabled={isSubmitting}
                      key={action}
                      onClick={() => setArmedAction(action)}
                      type="button"
                    >
                      {formatAction(action)}
                    </button>
                  ))}
                </div>
              ) : (
                <strong className="dock-action-label">{formatAction(targetActions[0])}</strong>
              )}
              {allowedActions
                .filter((action) => !TARGET_ACTIONS.has(action))
                .map((action) => (
                  <button
                    className="secondary-button"
                    disabled={isSubmitting}
                    key={action}
                    onClick={() => submit(action)}
                    type="button"
                  >
                    {formatAction(action)}
                  </button>
                ))}
              <span className="dock-hint">
                {!armedAction
                  ? "先选择上方动作，再点击座位"
                  : selectedTarget
                    ? `已选 ${selectedTarget}`
                    : "点击座位选择目标"}
              </span>
              <button
                className="play-button dock-confirm"
                disabled={isSubmitting || !armedAction || !selectedTarget}
                onClick={() => armedAction && selectedTarget && submit(armedAction, selectedTarget)}
                type="button"
              >
                确认
              </button>
            </div>
          ) : pending ? (
            <div className="dock-action-row">
              {allowedActions.map((action) => (
                <button
                  className="play-button"
                  disabled={isSubmitting}
                  key={action}
                  onClick={() => submit(action)}
                  type="button"
                >
                  {formatAction(action)}
                </button>
              ))}
            </div>
          ) : (
            <p className="player-action-status">AI 玩家正在行动，轮到你时这里会出现操作。</p>
          )}
        </div>
      </aside>
    </section>
  );
}

function formatWinner(winner: string): string {
  if (winner === "villagers" || winner === "villager") return "好人阵营";
  if (winner === "werewolves" || winner === "werewolf") return "狼人阵营";
  return winner;
}

function mergePrivateEvents(
  current: PlayerPrivateEvent[],
  incoming: PlayerPrivateEvent[]
): PlayerPrivateEvent[] {
  const seen = new Set(current.map(privateEventKey));
  const merged = [...current];
  for (const event of incoming) {
    const key = privateEventKey(event);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(event);
  }
  return merged;
}

function privateEventKey(event: PlayerPrivateEvent): string {
  return [event.event_type, event.round ?? "", event.target ?? "", event.result ?? "", event.teammates?.join(",") ?? ""].join(":");
}

function formatPrivateEvent(event: PlayerPrivateEvent): string {
  if (event.event_type === "seer_check_result") {
    return event.target ? `查验 ${event.target}：${event.result ?? "未知"}` : `查验结果：${event.result ?? "未知"}`;
  }
  if (event.event_type === "witch_kill_target_info") {
    return event.target ? `今晚刀口：${event.target}` : "今晚没有明确刀口";
  }
  if (event.event_type === "wolf_nomination" && event.teammates?.length) {
    return `狼队友：${event.teammates.join("、")}`;
  }
  return event.target ? `${event.event_type} · ${event.target}` : event.event_type;
}
